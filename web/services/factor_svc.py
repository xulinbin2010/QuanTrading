"""因子看板服务层：封装 DataStore + 因子计算 + 基本面数据"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import time
import pandas as pd
from datetime import date, timedelta
import config

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
            'enabled':       enabled,
            'is_dependency': meta.is_dependency,
            'display_only':  meta.display_only,
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


# ── 公共数据加载 ───────────────────────────────────────────

def _load_price_map(universe: str):
    """
    加载股票池价格数据。

    返回：(tickers, price_map, spy_close, stock_info)
      price_map 包含股票 + SPY + 11 个行业 ETF（供 sector_rs 因子使用）。
    """
    from core.data_store import DataStore
    from core.universe import get_tickers, get_stock_info
    from strategies.factors.sector_rs import ALL_SECTOR_ETFS

    tickers = get_tickers(universe)
    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(days=300)).strftime('%Y-%m-%d')

    store = DataStore()
    # 一并加载 SPY 和所有行业 ETF
    extra = ['SPY'] + ALL_SECTOR_ETFS
    price_map = store.get(tickers + extra, start=start_date, end=end_date,
                          min_rows=60, auto_update=True)

    spy_df = price_map.get('SPY')
    spy_close = spy_df['close'] if spy_df is not None else None
    stock_info = get_stock_info(tickers)
    return tickers, price_map, spy_close, stock_info


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

    from strategies.rs_momentum import RSMomentum
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

    # 财报回避因子是否启用
    earnings_avoid_enabled = bool(config.get('FACTOR_earnings_avoid_ENABLED') or False)
    earnings_avoid_days    = int(config.get('EARNINGS_AVOID_DAYS') or 2)
    earnings_cache: dict   = {}
    if earnings_avoid_enabled:
        from core.earnings import prefetch_earnings

    tickers, price_map, spy_close, stock_info = _load_price_map(universe)

    # 预取财报日期（批量一次，避免逐只联网）
    if earnings_avoid_enabled:
        earnings_cache = prefetch_earnings(tickers)

    sector_rs_enabled = bool(config.get('FACTOR_sector_rs_ENABLED') or False)
    from strategies.factors.sector_rs import SECTOR_ETFS, compute_sector_rs

    strategy = RSMomentum(
        vol_shrink_ratio=float(config.get('VOL_SHRINK_RATIO') or 0.7),
    )
    if spy_close is not None:
        strategy.set_spy(spy_close)

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
                # 行业相对强度（仅当 sector_rs 启用时有意义）
                **_compute_sector_rs_vals(sym, info, price_map, spy_close, sector_rs_enabled),
                # 财报回避（display_only，仅当 earnings_avoid 启用时有意义）
                "earnings_safe": (
                    _earnings_safe(sym, earnings_cache, earnings_avoid_days)
                    if earnings_avoid_enabled else None
                ),
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


# ── 自定义因子组合信号预览 ─────────────────────────────────

def preview_signals(universe: str, factors: list[str], top: int = 100) -> dict:
    """
    用 DynamicFactorStrategy(factors) 扫描股票池，返回各股当日信号。
    不缓存（预览是临时实验），不影响生产 RSMomentum 缓存。

    返回格式：
    {
        'rows': [{'symbol', 'close', 'signal', 'rs_score', ...}, ...],
        'factors': [...],   # 实际使用的因子列表
        'buy_count': N,
        'sell_count': N,
        'total': N,
    }
    """
    from strategies.dynamic_factor import DynamicFactorStrategy
    from strategies.factors.sector_rs import SECTOR_ETFS

    tickers, price_map, spy_close, stock_info = _load_price_map(universe)
    sector_rs_enabled = 'sector_rs' in factors

    strategy = DynamicFactorStrategy(factors)
    if spy_close is not None:
        strategy.set_spy(spy_close)

    rows = []
    for sym in tickers:
        df = price_map.get(sym)
        if df is None or len(df) < 60:
            continue
        try:
            # 为 sector_rs 因子传入对应行业 ETF 数据
            if sector_rs_enabled:
                info = stock_info.get(sym, {})
                etf_sym = SECTOR_ETFS.get(info.get('sector', ''))
                etf_df  = price_map.get(etf_sym) if etf_sym else None
                strategy.set_sector_etf(
                    info.get('sector'),
                    etf_df['close'] if etf_df is not None else None,
                )
            sig_df = strategy.generate_signals(df)
            last = sig_df.iloc[-1]

            # 动态提取所有因子列（只取数值/布尔，跳过 OHLCV）
            skip = {'open', 'high', 'low', 'close', 'volume', 'signal',
                    'vol_ma20', 'ma_fast', 'ma_slow', 'atr14', 'prev_high'}
            factor_vals: dict = {}
            for col in sig_df.columns:
                if col in skip:
                    continue
                v = last.get(col)
                if v is None:
                    continue
                try:
                    fv = v.item() if hasattr(v, 'item') else v
                    if isinstance(fv, float) and fv != fv:   # NaN
                        fv = None
                    factor_vals[col] = fv
                except Exception:
                    pass

            rows.append({
                'symbol':   sym,
                'close':    round(float(last['close']), 2),
                'signal':   int(last.get('signal', 0)),
                **factor_vals,
            })
        except Exception:
            continue

    # 排序：买入优先，其次按 rs_score
    rows.sort(key=lambda r: (-(r.get('signal', 0)), -float(r.get('rs_score') or 0)))
    top_rows = rows[:top] if top else rows

    buy_count  = sum(1 for r in rows if r['signal'] == 1)
    sell_count = sum(1 for r in rows if r['signal'] == -1)

    return {
        'rows':       top_rows,
        'factors':    factors,
        'buy_count':  buy_count,
        'sell_count': sell_count,
        'total':      len(rows),
    }


def _compute_fcf_yield(info: dict) -> float | None:
    fcf = info.get('free_cashflow')
    mc  = info.get('market_cap_b')
    if fcf is not None and mc is not None and mc > 0:
        return round(float(fcf) / (float(mc) * 1e9), 4)
    return None


def _compute_sector_rs_vals(
    sym: str,
    info: dict,
    price_map: dict,
    spy_close,
    enabled: bool,
) -> dict:
    """计算单股的行业相对强度值，供 scan_factors 行数据使用。"""
    if not enabled or spy_close is None:
        return {"sector_rs": None, "stock_vs_sector": None}

    from strategies.factors.sector_rs import SECTOR_ETFS, compute_sector_rs

    df = price_map.get(sym)
    if df is None:
        return {"sector_rs": None, "stock_vs_sector": None}

    etf_sym = SECTOR_ETFS.get(info.get('sector', ''))
    etf_df  = price_map.get(etf_sym) if etf_sym else None
    sector_etf_close = etf_df['close'] if etf_df is not None else None

    try:
        result_df = compute_sector_rs(df.copy(), sector_etf_close, spy_close)
        last = result_df.iloc[-1]
        sr   = last.get('sector_rs')
        svs  = last.get('stock_vs_sector')
        return {
            "sector_rs":       round(float(sr),  4) if sr  is not None and sr  == sr  else None,
            "stock_vs_sector": round(float(svs), 4) if svs is not None and svs == svs else None,
        }
    except Exception:
        return {"sector_rs": None, "stock_vs_sector": None}


def _earnings_safe(
    symbol: str,
    earnings_cache: dict,
    within_days: int,
) -> bool | None:
    """根据预取的财报缓存判断当前是否临近财报。True=安全，False=临近财报。"""
    from datetime import date, timedelta
    ed = earnings_cache.get(symbol)
    if ed is None:
        return None  # 无数据，不确定
    today  = date.today()
    cutoff = today + timedelta(days=within_days)
    return not (today <= ed <= cutoff)


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

    strategy = RSMomentum(
        vol_shrink_ratio=float(config.get('VOL_SHRINK_RATIO') or 0.7),
    )
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
            "ma_fast": _fmt(row.get('ma_fast')),
            "ma_slow": _fmt(row.get('ma_slow')),
            "atr14": _fmt(row.get('atr14')),
            "signal": int(row.get('signal', 0)),
        })

    return {
        "symbol": symbol,
        "ohlcv": ohlcv,
        "factors": factors_list,
        "fundamental": fundamental,
    }


# ── 移动止损检查 ───────────────────────────────────────────

def check_trail_stops(positions: list[dict]) -> list[dict]:
    """
    对持仓列表检查移动止损状态。
    positions: [{'symbol': 'NVDA', 'avg_cost': 850.0}, ...]
    返回每只股票的止损分析结果。
    """
    import config
    from core.data_store import DataStore
    from datetime import date, timedelta

    ACTIVATE = config.TRAIL_STOP_ACTIVATE_PCT
    TRAIL    = config.TRAIL_STOP_PCT

    symbols  = [p['symbol'] for p in positions]
    start    = (date.today() - timedelta(days=400)).strftime('%Y-%m-%d')
    end      = date.today().strftime('%Y-%m-%d')
    store    = DataStore()
    all_data = store.get(symbols, start=start, end=end, auto_update=False)

    results = []
    for pos in positions:
        sym      = pos['symbol']
        avg_cost = float(pos['avg_cost'])
        df       = all_data.get(sym)

        if df is None or df.empty:
            results.append({'symbol': sym, 'avg_cost': avg_cost,
                            'status': 'no_data', 'trigger': False})
            continue

        cur_price = float(df['close'].iloc[-1])
        # 只取入场价以上的峰值，避免买前历史高点干扰
        peak      = float(df['close'][df['close'] >= avg_cost].max()) if (df['close'] >= avg_cost).any() else avg_cost
        ret       = (cur_price - avg_cost) / avg_cost
        peak_ret  = (peak - avg_cost) / avg_cost
        trail_ret = (cur_price - peak) / peak
        trigger   = peak_ret >= ACTIVATE and trail_ret <= TRAIL

        results.append({
            'symbol':    sym,
            'avg_cost':  round(avg_cost, 2),
            'cur_price': round(cur_price, 2),
            'peak':      round(peak, 2),
            'ret':       round(ret, 4),
            'peak_ret':  round(peak_ret, 4),
            'trail_ret': round(trail_ret, 4),
            'activate':  ACTIVATE,
            'trail':     TRAIL,
            'trigger':   trigger,
            'status':    'triggered' if trigger else (
                         'watching' if peak_ret >= ACTIVATE else 'not_activated'),
        })

    return results


# ── 内部人买入 ─────────────────────────────────────────────

def get_insider_data(days: int = None, min_value_k: int = None) -> list[dict]:
    """
    返回近期内部人净买入记录，按买入金额降序排列。
    使用 core/insider.py 的 20 小时缓存，不重复请求。
    """
    from core.insider import get_insider_buys
    from core.universe import get_tickers
    _days        = days        or config.INSIDER_DAYS
    _min_value_k = min_value_k or config.INSIDER_MIN_VALUE_K

    universe_set = set(get_tickers('sp500+ndx'))
    raw = get_insider_buys(days=_days, min_value_k=_min_value_k)
    rows = [
        {
            'symbol':      sym,
            'score':       info['score'],
            'count':       info['count'],
            'total_value': info['total_value'],
            'last_date':   info.get('last_date', ''),
            'in_universe': sym in universe_set,
        }
        for sym, info in raw.items()
    ]
    rows.sort(key=lambda x: x['total_value'], reverse=True)
    return rows
