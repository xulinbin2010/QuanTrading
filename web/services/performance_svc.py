"""
业绩复盘服务：读取 account_snapshots，每天取最后一条，对比 SPY。
不依赖订单重建，直接用 IB 真实净值。
"""
from __future__ import annotations
import sys
import os
import time
from datetime import date, timedelta
from datetime import datetime as dt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_TRADING_DAYS_PER_YEAR = 252
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # 5 分钟


def get_performance(days: int = 30) -> dict:
    cache_key = f'perf_{days}'
    if cache_key in _cache:
        ts, data = _cache[cache_key]
        if time.monotonic() - ts < _CACHE_TTL:
            return data
    result = _compute(days)
    _cache[cache_key] = (time.monotonic(), result)
    return result


def invalidate_cache():
    """新快照写入后清除缓存"""
    _cache.clear()


def _compute(days: int) -> dict:
    from web.services.portfolio_svc import get_db
    from core.data_store import DataStore

    db = get_db()
    # 拉取足够多的历史快照（升序）
    rows = db.get_account_history(limit=1000)
    if not rows:
        return {'nav': [], 'spy': [], 'metrics': {}, 'has_data': False}

    # rows 格式（Database.get_account_history 返回元组，升序）：
    # (snapshot_at, net_liquidation, total_cash, unrealized_pnl, realized_pnl, buying_power)

    # ── 每天只保留最后一条快照 ───────────────────────────────
    daily: dict[date, float] = {}
    for r in rows:
        raw_dt = r[0]   # snapshot_at
        if isinstance(raw_dt, str):
            raw_dt = dt.fromisoformat(raw_dt)
        d = raw_dt.date() if isinstance(raw_dt, dt) else raw_dt
        nav = float(r[1]) if r[1] is not None else 0.0   # net_liquidation
        if nav > 0:
            daily[d] = nav   # 升序遍历，后来的覆盖前面的 → 每天最后一条

    if not daily:
        return {'nav': [], 'spy': [], 'metrics': {}, 'has_data': False}

    # ── 过滤明显异常值（< 中位数 10%）────────────────────────
    median_val = sorted(daily.values())[len(daily) // 2]
    daily = {d: v for d, v in daily.items() if v >= median_val * 0.1}

    # ── 按 days 参数截取区间 ─────────────────────────────────
    today        = date.today()
    period_start = today - timedelta(days=days)
    # 不能早于有数据的第一天
    data_start   = min(daily.keys())
    period_start = max(period_start, data_start)

    period = {d: v for d, v in daily.items() if d >= period_start}
    if not period:
        return {'nav': [], 'spy': [], 'metrics': {}, 'has_data': False}

    sorted_days = sorted(period.items())   # [(date, nav), ...]

    # ── 归一到 100 ───────────────────────────────────────────
    base = sorted_days[0][1]
    nav_series = [
        {'date': d.strftime('%Y-%m-%d'), 'value': round(v / base * 100, 4)}
        for d, v in sorted_days
    ]

    # ── SPY 对比（与 nav_series 日期严格对齐，缺失日填 None）────
    start_str = sorted_days[0][0].strftime('%Y-%m-%d')
    end_str   = sorted_days[-1][0].strftime('%Y-%m-%d')

    spy_series: list[dict] = []
    try:
        store    = DataStore()
        spy_data = store.get(['SPY'], start=start_str, end=end_str,
                             auto_update=True, min_rows=1)
        spy_df   = spy_data.get('SPY')
        if spy_df is not None and not spy_df.empty:
            # 构建 date_str → close 的快速查找表
            spy_px: dict[str, float] = {}
            for idx_val in spy_df.index:
                d = idx_val.date() if hasattr(idx_val, 'date') else idx_val
                spy_px[d.strftime('%Y-%m-%d')] = float(spy_df.loc[idx_val, 'close'])

            # 以 nav 序列的第一个有 SPY 数据的日期为基准归一
            spy_start_px = next(
                (spy_px[item['date']] for item in nav_series if item['date'] in spy_px),
                None
            )
            if spy_start_px:
                for item in nav_series:
                    px = spy_px.get(item['date'])
                    spy_series.append({
                        'date':  item['date'],
                        'value': round(px / spy_start_px * 100, 4) if px else None,
                    })
    except Exception:
        pass

    # ── 计算指标 ─────────────────────────────────────────────
    values  = [x['value'] for x in nav_series]
    returns = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]

    total_return = (values[-1] - 100.0) / 100.0
    n            = len(values)
    annualized   = (1 + total_return) ** (_TRADING_DAYS_PER_YEAR / n) - 1 if n > 1 else 0.0

    ret_arr = np.array(returns) if returns else np.array([0.0])
    sharpe  = float(ret_arr.mean() / ret_arr.std() * np.sqrt(_TRADING_DAYS_PER_YEAR)) \
              if ret_arr.std() > 0 else 0.0

    peak   = 100.0
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (v - peak) / peak
        if dd < max_dd:
            max_dd = dd

    last_spy   = next((x['value'] for x in reversed(spy_series) if x['value'] is not None), None)
    spy_return = (last_spy - 100.0) if last_spy is not None else 0.0
    excess_spy = total_return * 100.0 - spy_return

    metrics = {
        'total_return': round(total_return * 100, 2),
        'annualized':   round(annualized * 100, 2),
        'sharpe':       round(sharpe, 2),
        'max_drawdown': round(max_dd * 100, 2),
        'excess_spy':   round(excess_spy, 2),
        'period_days':  n,
        'data_from':    sorted_days[0][0].strftime('%Y-%m-%d'),
    }

    return {'nav': nav_series, 'spy': spy_series, 'metrics': metrics, 'has_data': True}
