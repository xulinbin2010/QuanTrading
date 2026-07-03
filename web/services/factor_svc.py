"""因子看板服务层：封装 DataStore + 因子计算 + 基本面数据"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import time
import math
import threading
import pandas as pd
from datetime import date, timedelta
import config

# 主线程预加载：ib_insync 在后台线程初始化 asyncio.event_loop 会失败（Py3.12）
# 这些模块通过 core/__init__.py 链式 import IBConnection → ib_insync
from core import data_store as _data_store           # noqa: F401
from core import universe as _universe               # noqa: F401
from core import earnings as _earnings               # noqa: F401
from strategies import rs_momentum as _rs_momentum   # noqa: F401


def _clean_floats(obj):
    """递归将 nan/inf/-inf 替换为 None，确保结果可 JSON 序列化。
    yfinance 会返回 float('nan') 而非 None，直接序列化会报 ValueError。
    """
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_floats(v) for v in obj]
    return obj


# ── 缓存（factor scan 结果，TTL 1 小时）─────────────────────
# 双层：内存缓存(同进程快速命中) + 文件缓存(跨进程共享，供定时任务预热)。
# 定时任务在子进程跑 scan，写入文件缓存；web server 进程读文件即可秒出，
# 不必等用户点开页面时现场跑。
import json
from pathlib import Path

_scan_cache: dict = {}         # key: universe, value: {ts, data}
_scan_running: dict[str, bool] = {}   # 正在后台扫描的 universe
_scan_lock = threading.Lock()
CACHE_TTL = 3600               # 秒
_CACHE_DIR = Path(__file__).resolve().parents[2] / 'data'


def _cache_file(universe: str) -> Path:
    return _CACHE_DIR / f'.factor_scan_{universe}.json'


def _read_file_cache(universe: str) -> dict | None:
    """读文件缓存 {ts, data}，新鲜则回灌内存。无/损坏/过期 → None。"""
    fp = _cache_file(universe)
    if not fp.exists():
        return None
    try:
        with open(fp, encoding='utf-8') as f:
            entry = json.load(f)
    except Exception:
        return None
    if not isinstance(entry, dict) or (time.time() - entry.get('ts', 0)) >= CACHE_TTL:
        return None
    _scan_cache[universe] = entry   # 回灌内存，下次走快速路径
    return entry


def _write_file_cache(universe: str, entry: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_cache_file(universe), 'w', encoding='utf-8') as f:
            json.dump(entry, f, ensure_ascii=False)
    except Exception:
        pass


def _cache_valid(universe: str) -> bool:
    entry = _scan_cache.get(universe)
    if entry is not None and (time.time() - entry['ts']) < CACHE_TTL:
        return True
    # 内存未命中/过期 → 看文件缓存（可能是定时任务刚预热的）
    return _read_file_cache(universe) is not None


def invalidate_cache(universe: str = None):
    if universe:
        _scan_cache.pop(universe, None)
        _cache_file(universe).unlink(missing_ok=True)
    else:
        _scan_cache.clear()
        for fp in _CACHE_DIR.glob('.factor_scan_*.json'):
            fp.unlink(missing_ok=True)


# ── 注册表 API ─────────────────────────────────────────────

def get_factor_registry() -> list[dict]:
    """返回所有已注册因子的元数据 + 当前启用状态 + 当前 DB 参数覆盖值"""
    from strategies.factors.registry import get_registry
    import config

    registry = get_registry()
    result = []
    for key, meta in registry.items():
        # 从 config 读取启用状态（默认取注册表 default_enabled）
        cfg_key = f'FACTOR_{key}_ENABLED'
        enabled = _parse_bool(config.get(cfg_key), meta.default_enabled)

        # 当前 DB 中的参数覆盖（无覆盖时 = 注册表默认值）
        db_params = get_factor_params_from_db(key) if meta.params else {}

        params_info = {
            pname: {
                'default': pdefault,
                'current': db_params.get(pname, pdefault),
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


def _parse_bool(val, default=False) -> bool:
    """正确解析 DB 中存储的布尔字符串（避免 bool('False') == True 的陷阱）"""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() in ('true', '1', 'yes')


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
        import sqlite3
        conn = sqlite3.connect(config.DB_PATH, timeout=2)
        cur = conn.cursor()
        prefix = f'FACTOR_{key}_PARAM_'
        cur.execute("SELECT key, value FROM config_store WHERE key LIKE ?", (prefix + '%',))
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
      price_map 包含股票 + SPY（rs_score 基准）。
    """
    from core.data_store import DataStore
    from core.universe import get_tickers, get_stock_info

    tickers = get_tickers(universe)
    end_date = date.today().strftime('%Y-%m-%d')
    start_date = (date.today() - timedelta(days=300)).strftime('%Y-%m-%d')

    store = DataStore()
    price_map = store.get(tickers + ['SPY'], start=start_date, end=end_date,
                          min_rows=60, auto_update=True)

    spy_df = price_map.get('SPY')
    spy_close = spy_df['close'] if spy_df is not None else None
    stock_info = get_stock_info(tickers)
    return tickers, price_map, spy_close, stock_info


# ── 扫描全股票池因子 ───────────────────────────────────────

def scan_factors(universe: str = 'sp500', top: int = 50, force: bool = False) -> dict:
    """
    返回股票池内各股最新一天的因子数据（技术 + 基本面），按 rs_score 降序。

    缓存策略：
    - 缓存有效 → 立即返回
    - 缓存过期 → 立即返回旧缓存（含 scanning=True），后台启动刷新
    - 无缓存（首次）→ 同步等待完成
    """
    if not force and _cache_valid(universe):
        return _scan_cache[universe]['data']

    # 关键：t.join() 必须在锁外，否则与 _run_scan finally 里的 lock.acquire() 死锁
    thread_to_wait = None
    with _scan_lock:
        if not _scan_running.get(universe, False):
            _scan_running[universe] = True
            t = threading.Thread(
                target=_run_scan, args=(universe, top), daemon=True
            )
            t.start()
            if universe not in _scan_cache:
                thread_to_wait = t   # 首次运行，没有任何缓存可返回，需等待
            # 否则有旧缓存 → 后台刷新，直接返回 stale

    if thread_to_wait is not None:
        thread_to_wait.join()

    entry = _scan_cache.get(universe)
    if entry:
        data = entry['data']
        is_bg = _scan_running.get(universe, False)
        return {**data, 'scanning': is_bg}
    return {'rows': [], 'coverage': {}, 'total': 0, 'scanning': True}


def _run_scan(universe: str, top: int) -> None:
    """实际扫描逻辑，在后台线程中运行。"""
    try:
        entry = {'ts': time.time(), 'data': _do_scan(universe, top)}
        _scan_cache[universe] = entry
        _write_file_cache(universe, entry)   # 同步落盘，供其他进程/重启后复用
    finally:
        with _scan_lock:
            _scan_running.pop(universe, None)


def _do_scan(universe: str, top: int) -> dict:
    """
    同步扫描全股票池，返回结果 dict。

    返回格式：
    {
        'rows': [...],
        'coverage': {'rs_score': {'total': 500, 'valid': 498, 'pct': 99.6}, ...},
        'total': N,
    }
    """
    from strategies.rs_momentum import RSMomentum
    import config

    earnings_avoid_enabled = _parse_bool(config.get('FACTOR_earnings_avoid_ENABLED'))
    earnings_avoid_days    = int(config.get('EARNINGS_AVOID_DAYS') or 2)
    earnings_cache: dict   = {}

    tickers, price_map, spy_close, stock_info = _load_price_map(universe)

    if earnings_avoid_enabled:
        from core.earnings import prefetch_earnings
        earnings_cache = prefetch_earnings(tickers)

    rs_params = get_factor_params_from_db('rs_score')
    strategy = RSMomentum(
        rs_period=int(rs_params.get('period', 63)),
        rs_weights=str(rs_params.get('weights', '') or ''),
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
            last   = sig_df.iloc[-1]
            info   = stock_info.get(sym, {})

            if universe == 'ai':
                cap = info.get('market_cap_b')
                if cap is not None and (cap < 10.0 or cap > 500.0):
                    continue

            row = {
                "symbol":        sym,
                "close":         round(float(last['close']), 2),
                "rs_score":      round(float(last.get('rs_score', 0)), 4),
                "vol_ratio":     round(float(last['volume'] / last['vol_ma20']), 2)
                                 if last.get('vol_ma20') else 0,
                "breakout":      bool(last.get('breakout', False)),
                "vol_surge":     bool(last.get('vol_surge', False)),
                "uptrend":       bool(last.get('uptrend', False)),
                "not_crashed":   bool(last.get('not_crashed', True)),
                "signal":        int(last.get('signal', 0)),
                "market_cap_b":  info.get('market_cap_b'),
                "industry":      info.get('industry'),
                "sector":        info.get('sector'),
                "revenue_growth":  info.get('revenue_growth'),
                "earnings_growth": info.get('earnings_growth'),
                "roe":             info.get('roe'),
                "debt_to_equity":  info.get('debt_to_equity'),
                "fcf_yield":       _compute_fcf_yield(info),
                "pe_ratio":        info.get('pe_ratio'),
                "pb_ratio":        info.get('pb_ratio'),
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

    factor_cols = [
        'rs_score', 'vol_ratio', 'breakout', 'vol_surge', 'uptrend', 'not_crashed',
        'revenue_growth', 'earnings_growth', 'roe', 'debt_to_equity',
        'fcf_yield', 'pe_ratio', 'pb_ratio',
    ]
    n = len(rows)
    coverage = {
        col: {
            'total': n,
            'valid': sum(1 for r in rows if r.get(col) is not None),
            'pct':   round(sum(1 for r in rows if r.get(col) is not None) / n * 100, 1) if n else 0,
        }
        for col in factor_cols
    }

    return _clean_floats({'rows': top_rows, 'coverage': coverage, 'total': n})


def _compute_fcf_yield(info: dict) -> float | None:
    fcf = info.get('free_cashflow')
    mc  = info.get('market_cap_b')
    try:
        if fcf is not None and mc is not None and float(mc) > 0:
            v = float(fcf) / (float(mc) * 1e9)
            return round(v, 4) if math.isfinite(v) else None
    except (TypeError, ValueError):
        pass
    return None


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

def _compute_ytd(df) -> float | None:
    """年初至今涨跌幅 = 最新收盘 / 去年最后一个交易日收盘 - 1。

    无去年数据时退回今年首个交易日收盘为基准。df 需为 DatetimeIndex 的完整历史。"""
    if df is None or len(df) == 0:
        return None
    try:
        idx = df.index
        cur_year = idx[-1].year
        last_close = float(df['close'].iloc[-1])
        prev = df[idx.year < cur_year]
        if len(prev) > 0:
            base = float(prev['close'].iloc[-1])
        else:
            this_year = df[idx.year == cur_year]
            if len(this_year) < 2:
                return None
            base = float(this_year['close'].iloc[0])
        if base <= 0:
            return None
        return (last_close - base) / base
    except Exception:
        return None


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

    rs_params = get_factor_params_from_db('rs_score')
    strategy = RSMomentum(
        rs_period=int(rs_params.get('period', 63)),
        rs_weights=str(rs_params.get('weights', '') or ''),
        vol_shrink_ratio=float(config.get('VOL_SHRINK_RATIO') or 0.7),
    )
    if spy_df is not None:
        strategy.set_spy(spy_df['close'])

    sig_df = strategy.generate_signals(df)
    # K 线图均线统一用 EMA7/EMA21（在 tail 前算，保留预热数据）；
    # 复用 ma_fast/ma_slow 输出字段，值改为 EMA。策略内部的 MA10>MA20 趋势过滤不受影响。
    sig_df['ema7'] = sig_df['close'].ewm(span=7, adjust=False).mean()
    sig_df['ema21'] = sig_df['close'].ewm(span=21, adjust=False).mean()
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
        if v is None:
            return None
        if isinstance(v, float) and (v != v or v == float('inf') or v == float('-inf')):
            return None
        return v

    ohlcv = []
    factors_list = []
    for idx, row in sig_df.iterrows():
        d = str(idx.date()) if hasattr(idx, 'date') else str(idx)[:10]
        ohlcv.append({
            "date": d,
            "open": _fmt(round(float(row['open']), 2)),
            "high": _fmt(round(float(row['high']), 2)),
            "low":  _fmt(round(float(row['low']),  2)),
            "close": _fmt(round(float(row['close']), 2)),
            "volume": int(row['volume']),
        })
        vol_ma20 = row.get('vol_ma20')
        vol_ratio = None
        if pd.notna(vol_ma20) and float(vol_ma20) > 0:
            ratio = float(row['volume']) / float(vol_ma20)
            vol_ratio = _fmt(round(ratio, 2))
        factors_list.append({
            "date": d,
            "rs_score": _fmt(row.get('rs_score')),
            "breakout": bool(row.get('breakout', False)),
            "vol_surge": bool(row.get('vol_surge', False)),
            "uptrend": bool(row.get('uptrend', False)),
            "not_crashed": bool(row.get('not_crashed', True)),
            "vol_ratio": vol_ratio,
            "ma_fast": _fmt(row.get('ema7')),
            "ma_slow": _fmt(row.get('ema21')),
            "atr14": _fmt(row.get('atr14')),
            "signal": int(row.get('signal', 0)),
        })

    return {
        "symbol": symbol,
        "industry": info.get('industry'),
        "sector": info.get('sector'),
        "ytd": _fmt(_compute_ytd(df)),   # 年初至今涨跌幅（用完整 df,基准=去年最后收盘）
        "ohlcv": ohlcv,
        "factors": factors_list,
        "fundamental": fundamental,
    }


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


# ── 5x 候选筛选器（8 维打分，满分 19）────────────────────────

_FIVEBAGGER_CACHE    = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'fivebagger_cache.json')
_QUARTERLY_CACHE     = os.path.join(os.path.dirname(__file__), '..', '..', 'data', '.quarterly_cache.pkl')
_FIVEBAGGER_TTL_DAYS = 7


def _load_quarterly_cache() -> dict:
    import pickle
    from datetime import datetime, timedelta
    path = os.path.normpath(_QUARTERLY_CACHE)
    if os.path.exists(path):
        try:
            with open(path, 'rb') as f:
                stored = pickle.load(f)
            if datetime.now() - stored.get('_time', datetime.min) < timedelta(days=7):
                return stored.get('data', {})
        except Exception:
            pass
    return {}


def _save_quarterly_cache(data: dict):
    import pickle
    from datetime import datetime
    path = os.path.normpath(_QUARTERLY_CACHE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, 'wb') as f:
            pickle.dump({'_time': datetime.now(), 'data': data}, f)
    except Exception:
        pass


def _fetch_rev_accel(sym: str):
    """季报营收加速度：最新季 YoY 增长率 - 上季 YoY 增长率。正值=加速。"""
    import yfinance as yf
    try:
        qf = yf.Ticker(sym).quarterly_financials
        if qf is None or qf.empty:
            return None
        rev_row = None
        for label in ['Total Revenue', 'Revenue', 'Operating Revenue']:
            if label in qf.index:
                rev_row = qf.loc[label].sort_index(ascending=False).dropna()
                break
        if rev_row is None or len(rev_row) < 5:
            return None
        q0, q1 = rev_row.iloc[0], rev_row.iloc[1]
        q4 = rev_row.iloc[4] if len(rev_row) > 4 else None
        q5 = rev_row.iloc[5] if len(rev_row) > 5 else None
        if q4 and abs(q4) > 0 and q5 and abs(q5) > 0:
            return float((q0 - q4) / abs(q4) - (q1 - q5) / abs(q5))
    except Exception:
        pass
    return None


def _batch_rs_scores(symbols: list) -> dict:
    """批量计算 63 日 RS vs SPY，返回 {sym: float}。"""
    import yfinance as yf
    if not symbols:
        return {}
    try:
        df = yf.download(list(set(symbols + ['SPY'])), period='100d',
                         progress=False, auto_adjust=True)['Close']
        if df.empty:
            return {}
        ret = df.pct_change(63).iloc[-1]
        spy_val = ret.get('SPY', 0)
        spy = float(spy_val) if spy_val == spy_val else 0.0  # NaN guard
        result = {}
        for s in symbols:
            if s not in ret.index:
                continue
            v = ret.get(s)
            if v is None or v != v:  # None or NaN
                continue
            result[s] = float(v) - spy
        return result
    except Exception:
        return {}


def _score_candidate(info: dict, ins: dict, rev_accel, rs) -> tuple:
    """8 维打分，返回 (total_score, breakdown_dict)。"""
    score = 0
    bd = {}

    # 1. 规模（$0.2B–$5B）
    cap = info.get('market_cap_b')
    s = 2 if cap and 0.2 <= cap <= 2 else (1 if cap and cap <= 5 else 0)
    score += s; bd['size'] = s

    # 2. 营收增长 YoY
    rv = info.get('revenue_growth')
    s = 3 if rv and rv > 0.50 else (2 if rv and rv > 0.30 else (1 if rv and rv > 0.15 else 0))
    score += s; bd['rev_growth'] = s

    # 3. 营收加速（季报）
    s = 2 if rev_accel and rev_accel > 0.05 else (1 if rev_accel and rev_accel > 0 else 0)
    score += s; bd['rev_accel'] = s

    # 4. PS 估值
    ps = info.get('ps_ratio')
    s = 3 if ps and ps < 2 else (2 if ps and ps < 5 else (1 if ps and ps < 10 else 0))
    score += s; bd['ps_ratio'] = s

    # 5. 毛利率
    gm = info.get('gross_margins')
    s = 2 if gm and gm > 0.60 else (1 if gm and gm > 0.40 else 0)
    score += s; bd['gross_margin'] = s

    # 6. 内幕买入（90 天）
    s = min(ins.get('score', 0), 3) if ins else 0
    score += s; bd['insider'] = s

    # 7. 价格动量 RS vs SPY
    s = 2 if rs and rs > 0.05 else (1 if rs and rs > 0 else 0)
    score += s; bd['rs_momentum'] = s

    # 8. 财务健康（FCF + D/E）
    fcf_ok = (info.get('free_cashflow') or 0) > 0
    de_ok  = info.get('debt_to_equity') is not None and info['debt_to_equity'] < 1.0
    s = 2 if (fcf_ok and de_ok) else (1 if (fcf_ok or de_ok) else 0)
    score += s; bd['fin_health'] = s

    return score, bd


def screen_fivebagger(force: bool = False) -> dict:
    """
    Russell 2000 5x 候选筛选器，8 维打分（满分 19）。
    Phase 1（快速）：市值 $0.2–5B + 营收 YoY > 10%（yf.info 7 天缓存）
    Phase 2（深度）：季报营收加速 + 批量 RS（仅对 Phase 1 候选，通常 100–300 只）
    结果缓存 7 天，force=True 强制重算。
    """
    import json
    from datetime import datetime, timedelta

    cache_path = os.path.normpath(_FIVEBAGGER_CACHE)
    if not force and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding='utf-8') as f:
                cached = json.load(f)
            if datetime.now() - datetime.fromisoformat(cached['last_updated']) < timedelta(days=_FIVEBAGGER_TTL_DAYS):
                return cached
        except Exception:
            pass

    from core.universe import get_russell2000_tickers, get_stock_info
    from core.insider import get_insider_buys

    print("  [5x] 获取 Russell 2000 股票池...")
    tickers     = get_russell2000_tickers()
    total_scanned = len(tickers)

    print(f"  [5x] 批量查询 {total_scanned} 只基本信息（7 天缓存）...")
    info_map    = get_stock_info(tickers)
    insider_map = get_insider_buys(days=90, min_value_k=100)

    # Phase 1：快速预筛
    candidates = [
        s for s in tickers
        if (info_map.get(s, {}).get('market_cap_b') or 0) >= 0.2
        and (info_map.get(s, {}).get('market_cap_b') or 999) <= 5.0
        and (info_map.get(s, {}).get('revenue_growth') or -1) > 0.10
    ]
    print(f"  [5x] Phase 1 候选 {len(candidates)} 只，进入 Phase 2...")

    # Phase 2a：批量 RS（快，几秒）
    rs_map = _batch_rs_scores(candidates)

    # Phase 2b：季报营收加速（带 7 天缓存，只查新出现的 symbol）
    q_cache = _load_quarterly_cache()
    need_q  = [s for s in candidates if s not in q_cache]
    if need_q:
        print(f"  [5x] 查询季报加速：{len(need_q)} 只...")
        for i, sym in enumerate(need_q, 1):
            q_cache[sym] = _fetch_rev_accel(sym)
            if i % 25 == 0:
                print(f"    {i}/{len(need_q)}")
        _save_quarterly_cache(q_cache)

    # Phase 3：打分并排序
    rows = []
    for sym in candidates:
        info  = info_map.get(sym, {})
        ins   = insider_map.get(sym, {})
        accel = q_cache.get(sym)
        rs    = rs_map.get(sym)
        total, bd = _score_candidate(info, ins, accel, rs)
        rows.append({
            'symbol':            sym,
            'score':             total,
            'breakdown':         bd,
            'market_cap_b':      info.get('market_cap_b'),
            'revenue_growth':    info.get('revenue_growth'),
            'rev_accel':         round(accel, 3) if accel is not None else None,
            'ps_ratio':          info.get('ps_ratio'),
            'gross_margins':     info.get('gross_margins'),
            'insider_score':     ins.get('score', 0) if ins else 0,
            'insider_count':     ins.get('count', 0) if ins else 0,
            'insider_value':     ins.get('total_value', 0) if ins else 0,
            'last_insider_date': ins.get('last_date', '') if ins else '',
            'rs_score':          round(rs, 3) if rs is not None else None,
            'free_cashflow':     info.get('free_cashflow'),
            'debt_to_equity':    info.get('debt_to_equity'),
            'sector':            info.get('sector') or '',
            'industry':          info.get('industry') or '',
        })

    rows.sort(key=lambda x: x['score'], reverse=True)

    result = _clean_floats({
        'rows':          rows[:30],
        'last_updated':  datetime.now().isoformat(timespec='seconds'),
        'total_scanned': total_scanned,
        'total_passed':  len(rows),
    })

    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    return result


# ── 叙事错位观察名单 ─────────────────────────────────────────

_NARRATIVE_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'narrative_watchlist.json')


def list_narrative_watchlist() -> list[dict]:
    import json
    path = os.path.normpath(_NARRATIVE_PATH)
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def _save_narrative_watchlist(entries: list[dict]):
    import json
    path = os.path.normpath(_NARRATIVE_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)


def upsert_narrative_entry(symbol: str, old_category: str, new_narrative: str,
                            thesis_notes: str, target_price=None) -> dict:
    from datetime import datetime
    entries = list_narrative_watchlist()
    sym = symbol.upper().strip()
    now = datetime.now().isoformat(timespec='seconds')

    for entry in entries:
        if entry['symbol'] == sym:
            entry.update({
                'old_category':  old_category,
                'new_narrative': new_narrative,
                'thesis_notes':  thesis_notes,
                'target_price':  target_price,
                'updated_at':    now,
            })
            _save_narrative_watchlist(entries)
            return entry

    entry = {
        'id':            max((e['id'] for e in entries), default=0) + 1,
        'symbol':        sym,
        'old_category':  old_category,
        'new_narrative': new_narrative,
        'thesis_notes':  thesis_notes,
        'target_price':  target_price,
        'added_at':      now,
        'updated_at':    now,
    }
    entries.append(entry)
    _save_narrative_watchlist(entries)
    return entry


def delete_narrative_entry(entry_id: int) -> bool:
    entries = list_narrative_watchlist()
    new_entries = [e for e in entries if e['id'] != entry_id]
    if len(new_entries) == len(entries):
        return False
    _save_narrative_watchlist(new_entries)
    return True


# CLI 入口（供 scheduler 定时预热市场扫描缓存）：
#   python -m web.services.factor_svc --universe ai --top 50
if __name__ == '__main__':
    import argparse
    import logging

    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    _p = argparse.ArgumentParser(description='预热市场扫描因子缓存')
    _p.add_argument('--universe', default='ai', help='股票池(默认 ai)')
    _p.add_argument('--top', type=int, default=50, help='输出前 N(默认 50)')
    _args = _p.parse_args()

    _entry = {'ts': time.time(), 'data': _do_scan(_args.universe, _args.top)}
    _scan_cache[_args.universe] = _entry
    _write_file_cache(_args.universe, _entry)
    print(f"[factor_svc] 预热完成 universe={_args.universe} "
          f"rows={len(_entry['data'].get('rows', []))} total={_entry['data'].get('total')}")
