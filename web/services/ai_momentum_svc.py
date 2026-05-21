"""AI 篮子短期动能 + 资金流服务

输出：
  1. 个股动能 + 资金流复合分（0-10），按复合分降序排序
  2. 子组热力（每个子组的中位动能、领涨/领跌、资金流方向）
  3. 篮子层面 A/D 线 + 金额加权 OBV 序列（前端画图）
  4. Top-N 推荐持仓（默认 Top-4）

复合分构成（z-score 归一化后加权）：
  - mom_5d_vs_spy   0.35   主信号：5 日相对大盘动能
  - mom_3d_vs_spy   0.20   3 日确认（避免 5 日动能已衰）
  - rs_vs_group_5d  0.20   子组内龙头识别
  - vol_ratio       0.15   3 日均量 / 20 日均量（放量加成）
  - flow_score      0.10   OBV 斜率 + up/down 量比

  + 加速 bonus 0.5：mom_3d 折算到 5d 的速率 > mom_5d 平均速率

主时间窗 5 日，3 日/10 日同时返回供前端切换。
"""
from __future__ import annotations
import os
import sys
import json
import math
import logging
import threading
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from core import data_store as _data_store  # noqa: F401

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_AI_UNIVERSE_FILE   = ROOT / 'data' / 'ai_universe.json'
_MOMENTUM_CACHE     = ROOT / 'data' / '.ai_momentum_cache.json'
_CACHE_TTL_MINUTES  = 30  # 动能数据时效性高，30 分钟缓存

_scan_running: dict[str, bool] = {}
_scan_lock = threading.Lock()


def _clean_floats(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_floats(v) for v in obj]
    return obj


def _load_universe() -> dict:
    with open(_AI_UNIVERSE_FILE, encoding='utf-8') as f:
        return json.load(f)


# ── 个股层面指标 ────────────────────────────────────────────

def _pct_change(series: pd.Series, n: int) -> float | None:
    """n 个交易日收益率（最后一日 vs n 日前）"""
    if len(series) <= n:
        return None
    cur = series.iloc[-1]
    prev = series.iloc[-1 - n]
    if prev is None or prev == 0 or pd.isna(prev):
        return None
    return float((cur - prev) / prev)


def _vol_ratio(df: pd.DataFrame, short: int = 3, long: int = 20) -> float | None:
    if len(df) < long + 1:
        return None
    vol = df['volume']
    short_avg = vol.iloc[-short:].mean()
    long_avg = vol.iloc[-long:].mean()
    if long_avg <= 0 or pd.isna(long_avg):
        return None
    return float(short_avg / long_avg)


def _flow_metrics(df: pd.DataFrame) -> dict[str, float | None]:
    """资金流指标：OBV 5日斜率 + 上涨日量/下跌日量比

    obv_slope：把最近 5 日 OBV 变化除以 5 日平均成交量，得到标准化"资金净流入强度"
    up_vol_ratio：5 日内上涨日总量 / 下跌日总量（>1 资金主动买盘多）
    """
    if len(df) < 25:
        return {'obv_slope': None, 'up_vol_ratio': None}

    close = df['close']
    vol = df['volume']
    diff = close.diff()
    sign = np.sign(diff).fillna(0)
    obv = (sign * vol).cumsum()

    # 标准化 OBV 5 日变化
    obv_recent = obv.iloc[-6:]  # 取 5 个 diff
    obv_delta = obv_recent.iloc[-1] - obv_recent.iloc[0]
    avg_vol_20 = vol.iloc[-20:].mean()
    obv_slope = float(obv_delta / (avg_vol_20 * 5)) if avg_vol_20 > 0 else None

    # up/down volume ratio
    last5 = df.iloc[-5:]
    d5 = last5['close'].diff().fillna(0)
    up_vol = last5.loc[d5 > 0, 'volume'].sum()
    down_vol = last5.loc[d5 < 0, 'volume'].sum()
    up_vol_ratio = float((up_vol + 1) / (down_vol + 1))

    return {'obv_slope': obv_slope, 'up_vol_ratio': up_vol_ratio}


def _flow_score_0_10(obv_slope: float | None, up_vol_ratio: float | None) -> float:
    """资金流综合得分 0-10。"""
    s = 5.0
    if obv_slope is not None:
        # obv_slope 通常在 [-0.5, 0.5] 区间，扩展到 ±2.5
        s += max(-2.5, min(2.5, obv_slope * 5))
    if up_vol_ratio is not None:
        # up_vol_ratio = 1 中性；2 强势；0.5 弱势
        ratio_score = math.log(max(up_vol_ratio, 0.1)) / math.log(3)  # log_3 scale
        s += max(-2.5, min(2.5, ratio_score * 2.5))
    return max(0.0, min(10.0, s))


# ── z-score 归一化（0-10） ──────────────────────────────────

def _zscore_to_0_10(values: list[float | None]) -> list[float]:
    """把一组数标准化为 0-10。None → 5（中性）。"""
    arr = np.array([v for v in values if v is not None], dtype=float)
    if len(arr) < 2:
        return [5.0] * len(values)
    mean = float(arr.mean())
    std = float(arr.std()) or 1.0
    out = []
    for v in values:
        if v is None:
            out.append(5.0)
        else:
            z = (v - mean) / std
            score = 5.0 + z * 2.5  # [-2, 2] → [0, 10]
            out.append(max(0.0, min(10.0, score)))
    return out


# ── 主扫描 ────────────────────────────────────────────────

def _read_cache() -> dict | None:
    if not _MOMENTUM_CACHE.exists():
        return None
    try:
        with open(_MOMENTUM_CACHE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(data: dict) -> None:
    _MOMENTUM_CACHE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(_MOMENTUM_CACHE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def scan_momentum(force: bool = False) -> dict:
    """主入口：AI 篮子短期动能扫描。

    缓存策略：
    - 30 分钟内缓存有效 → 直接返回
    - 缓存过期或不存在 → 后台扫描；若有过期缓存，立即返回 stale + 后台刷新
    """
    cached = _read_cache()
    if not force and cached is not None:
        try:
            age = datetime.now() - datetime.fromisoformat(cached['last_updated'])
            if age < timedelta(minutes=_CACHE_TTL_MINUTES):
                return cached
        except Exception:
            pass

    key = 'ai_momentum'
    thread_to_wait = None
    with _scan_lock:
        if not _scan_running.get(key, False):
            _scan_running[key] = True
            t = threading.Thread(target=_run_scan_bg, daemon=True)
            t.start()
            if cached is None:
                thread_to_wait = t

    if thread_to_wait is not None:
        thread_to_wait.join()
        cached = _read_cache()

    if cached is not None:
        return {**cached, 'scanning': _scan_running.get(key, False)}
    return {'rows': [], 'groups': [], 'basket': {}, 'top4': [], 'scanning': True}


def _run_scan_bg() -> None:
    try:
        _do_scan()
    except Exception as e:
        _logger.error(f'[AIMomentum] 扫描失败：{e}', exc_info=True)
    finally:
        with _scan_lock:
            _scan_running.pop('ai_momentum', None)


def _do_scan() -> dict:
    """实际扫描：读 universe → 批量加载价格 → 计算指标 → 排序写盘"""
    from core.data_store import DataStore

    universe = _load_universe()
    groups_cfg = universe.get('groups', {})

    all_syms: list[str] = []
    sym_to_group: dict[str, str] = {}
    for gk, gv in groups_cfg.items():
        for s in gv.get('symbols', []):
            sym = s.upper()
            if sym not in sym_to_group:
                all_syms.append(sym)
                sym_to_group[sym] = gk

    _logger.info(f'[AIMomentum] 扫描 {len(all_syms)} 只...')

    store = DataStore()
    start_d = str(date.today() - timedelta(days=60))
    price_map = store.get(all_syms + ['SPY'], start=start_d, auto_update=False)

    spy_df = price_map.get('SPY')
    if spy_df is None or len(spy_df) < 15:
        raise RuntimeError('SPY 数据不可用，无法计算相对动能')

    spy_close = spy_df['close']
    spy_3d = _pct_change(spy_close, 3)
    spy_5d = _pct_change(spy_close, 5)
    spy_10d = _pct_change(spy_close, 10)

    # 第一遍：算原始指标
    raw_rows: list[dict] = []
    for sym in all_syms:
        df = price_map.get(sym)
        if df is None or len(df) < 15:
            continue

        close = df['close']
        mom_3d = _pct_change(close, 3)
        mom_5d = _pct_change(close, 5)
        mom_10d = _pct_change(close, 10)

        rs_3d = mom_3d - spy_3d if (mom_3d is not None and spy_3d is not None) else None
        rs_5d = mom_5d - spy_5d if (mom_5d is not None and spy_5d is not None) else None
        rs_10d = mom_10d - spy_10d if (mom_10d is not None and spy_10d is not None) else None

        vr = _vol_ratio(df, short=3, long=20)
        flow = _flow_metrics(df)
        flow_score = _flow_score_0_10(flow['obv_slope'], flow['up_vol_ratio'])

        # 加速判断：3 日收益日均速率 > 5 日日均速率
        accel = False
        if mom_3d is not None and mom_5d is not None:
            accel = (mom_3d / 3.0) > (mom_5d / 5.0)

        last_close = float(close.iloc[-1])

        raw_rows.append({
            'symbol':       sym,
            'group':        sym_to_group[sym],
            'group_label':  groups_cfg[sym_to_group[sym]]['label'],
            'group_color':  groups_cfg[sym_to_group[sym]].get('color', '#94a3b8'),
            'close':        last_close,
            'mom_3d':       mom_3d,
            'mom_5d':       mom_5d,
            'mom_10d':      mom_10d,
            'rs_3d':        rs_3d,
            'rs_5d':        rs_5d,
            'rs_10d':       rs_10d,
            'vol_ratio':    vr,
            'obv_slope':    flow['obv_slope'],
            'up_vol_ratio': flow['up_vol_ratio'],
            'flow_score':   flow_score,
            'accel':        accel,
        })

    # 第二遍：组内中位数（rs_vs_group_5d）
    rs_5d_by_group: dict[str, list[float]] = {}
    for r in raw_rows:
        if r['rs_5d'] is not None:
            rs_5d_by_group.setdefault(r['group'], []).append(r['rs_5d'])
    group_median: dict[str, float] = {
        gk: float(np.median(vs)) for gk, vs in rs_5d_by_group.items() if vs
    }
    for r in raw_rows:
        med = group_median.get(r['group'])
        r['rs_vs_group_5d'] = (r['rs_5d'] - med) if (r['rs_5d'] is not None and med is not None) else None

    # 第三遍：z-score 归一化
    z_mom5    = _zscore_to_0_10([r['rs_5d']           for r in raw_rows])
    z_mom3    = _zscore_to_0_10([r['rs_3d']           for r in raw_rows])
    z_rsgrp   = _zscore_to_0_10([r['rs_vs_group_5d']  for r in raw_rows])
    z_vol     = _zscore_to_0_10([r['vol_ratio']       for r in raw_rows])
    # flow_score 已是 0-10，无需再归一化
    flow_vals = [r['flow_score'] for r in raw_rows]

    for i, r in enumerate(raw_rows):
        composite = (
            0.35 * z_mom5[i]
            + 0.20 * z_mom3[i]
            + 0.20 * z_rsgrp[i]
            + 0.15 * z_vol[i]
            + 0.10 * flow_vals[i]
        )
        if r['accel']:
            composite = min(10.0, composite + 0.5)
        r['z_mom_5d']     = round(z_mom5[i], 2)
        r['z_mom_3d']     = round(z_mom3[i], 2)
        r['z_rs_group']   = round(z_rsgrp[i], 2)
        r['z_vol_ratio']  = round(z_vol[i], 2)
        r['composite']    = round(composite, 2)

    # 按复合分降序
    raw_rows.sort(key=lambda x: x['composite'], reverse=True)
    for i, r in enumerate(raw_rows):
        r['rank'] = i + 1

    # ── 子组聚合 ──────────────────────────────────────────
    groups_summary: list[dict] = []
    for gk, gv in groups_cfg.items():
        members = [r for r in raw_rows if r['group'] == gk]
        if not members:
            continue
        rs5_vals = [r['rs_5d'] for r in members if r['rs_5d'] is not None]
        mom5_vals = [r['mom_5d'] for r in members if r['mom_5d'] is not None]
        flow_vals_g = [r['flow_score'] for r in members]
        advance = sum(1 for r in members if (r['rs_5d'] or 0) > 0)
        decline = len(members) - advance
        leaders = sorted(members, key=lambda x: x['composite'], reverse=True)[:3]
        median_flow = float(np.median(flow_vals_g)) if flow_vals_g else 5.0
        flow_signal = 'inflow' if median_flow > 6 else ('outflow' if median_flow < 4 else 'neutral')
        groups_summary.append({
            'key':            gk,
            'label':          gv['label'],
            'color':          gv.get('color', '#94a3b8'),
            'count':          len(members),
            'median_mom_5d':  float(np.median(mom5_vals)) if mom5_vals else None,
            'median_rs_5d':   float(np.median(rs5_vals)) if rs5_vals else None,
            'advance':        advance,
            'decline':        decline,
            'flow_score':     round(median_flow, 2),
            'flow_signal':    flow_signal,
            'leaders':        [{'symbol': r['symbol'], 'composite': r['composite']} for r in leaders],
        })
    groups_summary.sort(key=lambda x: (x['median_rs_5d'] or -1), reverse=True)

    # ── 篮子层面 A/D 线 + 金额加权 OBV ───────────────────────
    basket = _compute_basket_flow(price_map, all_syms)

    # ── Top-N ────────────────────────────────────────────
    top4 = [r['symbol'] for r in raw_rows[:4]]

    result = _clean_floats({
        'rows':          raw_rows,
        'groups':        groups_summary,
        'basket':        basket,
        'top4':          top4,
        'spy':           {'mom_3d': spy_3d, 'mom_5d': spy_5d, 'mom_10d': spy_10d},
        'total':         len(raw_rows),
        'last_updated':  datetime.now().isoformat(timespec='seconds'),
    })

    _write_cache(result)
    return result


def _compute_basket_flow(price_map: dict, syms: list[str]) -> dict:
    """计算 AI 篮子整体的 A/D 线和金额加权 OBV（最近 10 个交易日序列）。

    A/D 线（advance/decline line）：每日"涨家数 - 跌家数"累计
    money_flow_5d：金额加权 OBV，每日 sum(sign(Δclose) × close × volume) 滚动 5 日累计
    """
    n_days = 10
    closes = []  # list of pd.Series, aligned by date
    vols = []
    for s in syms:
        df = price_map.get(s)
        if df is None or len(df) < n_days + 2:
            continue
        closes.append(df['close'].rename(s))
        vols.append(df['volume'].rename(s))
    if not closes:
        return {}

    close_mat = pd.concat(closes, axis=1).dropna(how='all')
    vol_mat = pd.concat(vols, axis=1).reindex(close_mat.index)
    # 取最近 n_days+1 行（要算 diff，所以多取一天）
    close_mat = close_mat.iloc[-(n_days + 1):]
    vol_mat = vol_mat.iloc[-(n_days + 1):]

    diff = close_mat.diff()
    advance = (diff > 0).sum(axis=1)
    decline = (diff < 0).sum(axis=1)
    ad_daily = (advance - decline).iloc[1:]  # 去掉首行 NaN
    ad_line = ad_daily.cumsum()

    # 金额加权资金流：sign(Δ) × close × volume
    money_flow_daily = (np.sign(diff) * close_mat * vol_mat).sum(axis=1).iloc[1:]
    money_flow_cum = money_flow_daily.cumsum()

    # 缩放成 "亿美元" 显示
    money_flow_daily_b = (money_flow_daily / 1e9).round(2)
    money_flow_cum_b = (money_flow_cum / 1e9).round(2)

    return {
        'dates':            [d.strftime('%Y-%m-%d') for d in ad_line.index],
        'ad_daily':         [int(v) for v in ad_daily.values],
        'ad_cumulative':    [int(v) for v in ad_line.values],
        'money_flow_b':     money_flow_daily_b.tolist(),
        'money_flow_cum_b': money_flow_cum_b.tolist(),
        'advance_today':   int(advance.iloc[-1]) if len(advance) else 0,
        'decline_today':   int(decline.iloc[-1]) if len(decline) else 0,
        'advance_5d':      int(ad_daily.iloc[-5:].sum()) if len(ad_daily) >= 5 else int(ad_daily.sum()),
        'money_flow_5d_b': float(money_flow_daily.iloc[-5:].sum() / 1e9) if len(money_flow_daily) >= 5 else float(money_flow_daily.sum() / 1e9),
    }
