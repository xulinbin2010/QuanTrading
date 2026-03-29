"""因子看板服务层：封装 DataStore + 因子计算 + 基本面数据"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import time
import pandas as pd
from datetime import date, timedelta

# ── 内存缓存（factor scan 结果，TTL 1 小时）────────────────
_scan_cache: dict = {}   # key: universe, value: {ts, data}
CACHE_TTL = 3600         # 秒


def _cache_valid(universe: str) -> bool:
    entry = _scan_cache.get(universe)
    return entry is not None and (time.time() - entry['ts']) < CACHE_TTL


def invalidate_cache(universe: str = None):
    if universe:
        _scan_cache.pop(universe, None)
    else:
        _scan_cache.clear()


# ── 注册表 API ─────────────────────────────────────────────

def get_factor_registry() -> list[dict]:
    """返回所有已注册因子的元数据 + 当前启用状态"""
    from strategies.factors.registry import get_registry
    import config

    registry = get_registry()
    result = []
    for key, meta in registry.items():
        # 从 config 读取启用状态（默认取注册表 default_enabled）
        cfg_key = f'FACTOR_{key}_ENABLED'
        enabled = config.get(cfg_key)
        if enabled is None:
            enabled = meta.default_enabled

        params_info = {
            pname: {
                'default': pdefault,
                'type':    ptype.__name__,
                'desc':    pdesc,
            }
            for pname, (pdefault, ptype, pdesc) in meta.params.items()
        }
        result.append({
            'key':          meta.key,
            'name':         meta.name,
            'category':     meta.category,
            'data_type':    meta.data_type,
            'signal_type':  meta.signal_type,
            'output_columns': meta.output_columns,
            'params':       params_info,
            'enabled':      enabled,
        })
    return result


def update_factor_config(key: str, enabled: bool | None = None, params: dict | None = None) -> bool:
    """更新因子开关或参数，写入 config_store"""
    import config
    ok = True
    if enabled is not None:
        cfg_key = f'FACTOR_{key}_ENABLED'
        if cfg_key in config._DEFAULTS:
            ok = config.set_param(cfg_key, enabled) and ok
    # 因子参数更新（暂存到独立 key，供 DynamicFactorStrategy 读取）
    if params:
        for pname, pval in params.items():
            cfg_key = f'FACTOR_{key}_PARAM_{pname}'
            # 动态写入，不需要预定义
            try:
                import pymysql
                conn = pymysql.connect(
                    host=config.DB_HOST, port=config.DB_PORT,
                    user=config.DB_USER, password=config.DB_PASSWORD,
                    database=config.DB_NAME, charset='utf8mb4',
                    autocommit=True, connect_timeout=3,
                )
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS config_store (
                        `key` VARCHAR(80) PRIMARY KEY, value VARCHAR(500) NOT NULL,
                        type VARCHAR(10), category VARCHAR(20), description VARCHAR(200),
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                cur.execute("""
                    INSERT INTO config_store (`key`, value, type, category, description)
                    VALUES (%s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = NOW()
                """, (cfg_key, str(pval), type(pval).__name__, '因子参数', f'{key}.{pname}'))
                conn.close()
            except Exception:
                ok = False
    return ok


def get_factor_params_from_db(key: str) -> dict:
    """从 DB 读取某因子的参数覆盖值"""
    import config
    from strategies.factors.registry import get_registry
    registry = get_registry()
    meta = registry.get(key)
    if not meta:
        return {}
    params = {pname: pdefault for pname, (pdefault, _, _) in meta.params.items()}
    try:
        import pymysql
        conn = pymysql.connect(
            host=config.DB_HOST, port=config.DB_PORT,
            user=config.DB_USER, password=config.DB_PASSWORD,
            database=config.DB_NAME, charset='utf8mb4',
            autocommit=True, connect_timeout=3,
        )
        cur = conn.cursor()
        prefix = f'FACTOR_{key}_PARAM_'
        cur.execute("SELECT `key`, value FROM config_store WHERE `key` LIKE %s", (prefix + '%',))
        for row_key, row_val in cur.fetchall():
            pname = row_key[len(prefix):]
            if pname in meta.params:
                _, ptype, _ = meta.params[pname]
                try:
                    params[pname] = ptype(row_val)
                except Exception:
                    pass
        conn.close()
    except Exception:
        pass
    return params


# ── 扫描全股票池因子 ───────────────────────────────────────

def scan_factors(universe: str = 'sp500', top: int = 50, force: bool = False) -> dict:
    """
    返回股票池内各股最新一天的因子数据（技术 + 基本面），按 rs_score 降序。
    同时返回覆盖率统计。

    返回格式：
    {
        'rows': [...],
        'coverage': {'rs_score': {'total': 500, 'valid': 498, 'pct': 99.6}, ...},
        'total': N,   # 扫描总数
    }
    """
    if not force and _cache_valid(universe):
        return _scan_cache[universe]['data']

    from core.data_store import DataStore
    from core.universe import get_tickers, get_stock_info
    from strategies.rs_momentum import RSMomentum
    from strategies.factors.fundamental import (
        compute_revenue_growth, compute_earnings_growth,
        compute_roe, compute_fcf_yield, compute_pe_ratio, compute_pb_ratio,
    )
    import config

    # 读取基本面因子启用状态
    fundamental_keys = [
        'revenue_growth', 'earnings_growth', 'roe',
        'debt_to_equity', 'fcf_yield', 'pe_ratio', 'pb_ratio',
    ]
    enabled_fundamental = {
        k: bool(config.get(f'FACTOR_{k}_ENABLED') or False)
        for k in fundamental_keys
    }

    tickers = get_tickers(universe)
    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(days=300)).strftime('%Y-%m-%d')

    store = DataStore()
    price_map = store.get(tickers + ['SPY'], start=start_date, end=end_date,
                          min_rows=60, auto_update=True)

    spy_df = price_map.get('SPY')
    spy_close = spy_df['close'] if spy_df is not None else None

    strategy = RSMomentum()
    if spy_close is not None:
        strategy.set_spy(spy_close)

    stock_info = get_stock_info(tickers)

    rows = []
    total_scanned = 0
    for sym in tickers:
        df = price_map.get(sym)
        if df is None or len(df) < 60:
            continue
        total_scanned += 1
        try:
            sig_df = strategy.generate_signals(df)
            last = sig_df.iloc[-1]
            info = stock_info.get(sym, {})

            row = {
                "symbol": sym,
                "close": round(float(last['close']), 2),
                "rs_score": round(float(last.get('rs_score', 0)), 4),
                "vol_ratio": round(float(last['volume'] / last['vol_ma20']), 2)
                             if last.get('vol_ma20') else 0,
                "breakout": bool(last.get('breakout', False)),
                "vol_surge": bool(last.get('vol_surge', False)),
                "uptrend": bool(last.get('uptrend', False)),
                "not_crashed": bool(last.get('not_crashed', True)),
                "signal": int(last.get('signal', 0)),
                "market_cap_b": info.get('market_cap_b'),
                "industry": info.get('industry'),
                "sector": info.get('sector'),
                # 基本面因子（始终附带，前端按启用状态显示）
                "revenue_growth": info.get('revenue_growth'),
                "earnings_growth": info.get('earnings_growth'),
                "roe": info.get('roe'),
                "debt_to_equity": info.get('debt_to_equity'),
                "fcf_yield": _compute_fcf_yield(info),
                "pe_ratio": info.get('pe_ratio'),
                "pb_ratio": info.get('pb_ratio'),
            }
            rows.append(row)
        except Exception:
            continue

    rows.sort(key=lambda x: x['rs_score'], reverse=True)
    top_rows = rows[:top] if top else rows

    # ── 覆盖率统计 ────────────────────────────────────────
    factor_cols = [
        'rs_score', 'vol_ratio', 'breakout', 'vol_surge', 'uptrend', 'not_crashed',
        'revenue_growth', 'earnings_growth', 'roe', 'debt_to_equity',
        'fcf_yield', 'pe_ratio', 'pb_ratio',
    ]
    coverage = {}
    n = len(rows)
    for col in factor_cols:
        valid = sum(1 for r in rows if r.get(col) is not None)
        coverage[col] = {
            'total': n,
            'valid': valid,
            'pct': round(valid / n * 100, 1) if n > 0 else 0,
        }

    data = {
        'rows': top_rows,
        'coverage': coverage,
        'total': n,
    }

    _scan_cache[universe] = {'ts': time.time(), 'data': data}
    return data


def _compute_fcf_yield(info: dict) -> float | None:
    fcf = info.get('free_cashflow')
    mc  = info.get('market_cap_b')
    if fcf is not None and mc is not None and mc > 0:
        return round(float(fcf) / (float(mc) * 1e9), 4)
    return None


# ── 单股因子时序 ───────────────────────────────────────────

def get_stock_factors(symbol: str, days: int = 120) -> dict:
    """返回单股 OHLCV + 全因子时序数据"""
    from core.data_store import DataStore
    from strategies.rs_momentum import RSMomentum
    from core.universe import get_stock_info

    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(days=days + 200)).strftime('%Y-%m-%d')

    store = DataStore()
    price_map = store.get([symbol, 'SPY'], start=start_date, end=end_date,
                          min_rows=60, auto_update=True)

    df = price_map.get(symbol)
    spy_df = price_map.get('SPY')
    if df is None or len(df) < 20:
        raise ValueError(f"股票 {symbol} 数据不足")

    strategy = RSMomentum()
    if spy_df is not None:
        strategy.set_spy(spy_df['close'])

    sig_df = strategy.generate_signals(df)
    sig_df = sig_df.tail(days)

    # 基本面快照
    info = get_stock_info([symbol]).get(symbol, {})
    fundamental = {
        'revenue_growth':  info.get('revenue_growth'),
        'earnings_growth': info.get('earnings_growth'),
        'roe':             info.get('roe'),
        'debt_to_equity':  info.get('debt_to_equity'),
        'fcf_yield':       _compute_fcf_yield(info),
        'pe_ratio':        info.get('pe_ratio'),
        'pb_ratio':        info.get('pb_ratio'),
    }

    def _fmt(v):
        if hasattr(v, 'item'):
            v = v.item()
        if isinstance(v, float) and (v != v):
            return None
        return v

    ohlcv = []
    factors_list = []
    for idx, row in sig_df.iterrows():
        d = str(idx.date()) if hasattr(idx, 'date') else str(idx)[:10]
        ohlcv.append({
            "date": d,
            "open": round(float(row['open']), 2),
            "high": round(float(row['high']), 2),
            "low": round(float(row['low']), 2),
            "close": round(float(row['close']), 2),
            "volume": int(row['volume']),
        })
        factors_list.append({
            "date": d,
            "rs_score": _fmt(row.get('rs_score')),
            "breakout": bool(row.get('breakout', False)),
            "vol_surge": bool(row.get('vol_surge', False)),
            "uptrend": bool(row.get('uptrend', False)),
            "not_crashed": bool(row.get('not_crashed', True)),
            "vol_ratio": round(float(row['volume'] / row['vol_ma20']), 2)
                         if row.get('vol_ma20') else None,
            "ma50": _fmt(row.get('ma50')),
            "ma200": _fmt(row.get('ma200')),
            "atr14": _fmt(row.get('atr14')),
            "signal": int(row.get('signal', 0)),
        })

    return {
        "symbol": symbol,
        "ohlcv": ohlcv,
        "factors": factors_list,
        "fundamental": fundamental,
    }
