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


# ── 趋势质量分（市场无关纯算法） ───────────────────────────

def _freshness(high: pd.Series, low: pd.Series, close: pd.Series,
               m: int = 10, w: int = 20) -> float:
    """新高新鲜度 0-1：近 m 日内盘中创 w 日新高的天数占比，按当日收盘兑现度打折。

    冲高回落（盘中破新高但收在最低）只拿 0.4×，防长上影/出货日被当成趋势分加分项。
    收在最高 → 满分；介于两者按收盘在当日振幅中的位置线性插值。
    """
    if len(close) < w + 1:
        return 0.0
    roll_hi = high.shift(1).rolling(w).max()          # 截至前一日的 w 日最高
    push = (high.values[-m:] >= roll_hi.values[-m:]).astype(float)  # 盘中创新高=1
    rng = (high - low).clip(lower=1e-9)
    hold = ((close - low) / rng).clip(0, 1).values[-m:]            # 收盘在振幅中的位置
    valid = ~np.isnan(roll_hi.values[-m:])
    if not valid.any():
        return 0.0
    return float((push * (0.4 + 0.6 * hold))[valid].mean())


def _trend_quality(df: pd.DataFrame, n: int = 20) -> dict:
    """趋势质量分（0-10）：站上 EMA7 占比(持续性) + log价格回归 R²(平滑·仅上升)
    + 新高新鲜度(动能),再乘暴涨惩罚。

    三项各测一面：ema7_hold=趋势没坏；trend_r2=上升平滑；freshness=最近还在创新高。
    暴涨不硬删除而是扣分：单日涨幅 >9.5%（涨停级）或近 n 日累计 >50% 时按超出幅度降权。
    """
    close = df['close']
    c = close.tail(n)
    if len(c) < 8:
        return {'trend_score': None, 'ema7_hold': None, 'trend_r2': None, 'freshness': None}
    e = close.ewm(span=7, adjust=False).mean().reindex(c.index)
    hold = float((c.values >= e.values).mean())          # 收盘站上 EMA7 的天数占比
    y = np.log(c.values)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    yhat = slope * x + intercept
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = (1 - float(((y - yhat) ** 2).sum()) / ss_tot) if ss_tot > 0 else 0.0
    r2_eff = r2 if slope > 0 else 0.0                    # 必须是上升趋势才算平滑度
    fresh = _freshness(df['high'], df['low'], close, m=10, w=20) if 'high' in df.columns else 0.0
    daily = c.pct_change().dropna()
    max_1d = float(daily.max()) if len(daily) else 0.0
    ret_n = float(c.iloc[-1] / c.iloc[0] - 1)
    pen = 1.0
    if max_1d > 0.095:
        pen *= max(0.4, 1 - (max_1d - 0.095) * 5)
    if ret_n > 0.5:
        pen *= max(0.3, 1 - (ret_n - 0.5))
    score = (0.40 * hold + 0.35 * r2_eff + 0.25 * fresh) * 10 * pen
    return {'trend_score': round(score, 2), 'ema7_hold': round(hold, 2),
            'trend_r2': round(r2_eff, 2), 'freshness': round(fresh, 2)}


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
    # 400 天窗口:短线动能只需 ~40 天,但 RS 动量分(实盘口径)要 63 日 RS + 252 日 drawdown,
    # 故按更长历史加载(本地 parquet 读取,auto_update=False,多读行近乎零成本)。
    start_d = str(date.today() - timedelta(days=400))
    price_map = store.get(all_syms + ['SPY'], start=start_d, auto_update=False)

    spy_df = price_map.get('SPY')
    if spy_df is None or len(spy_df) < 15:
        raise RuntimeError('SPY 数据不可用，无法计算相对动能')

    spy_close = spy_df['close']
    spy_3d = _pct_change(spy_close, 3)
    spy_5d = _pct_change(spy_close, 5)
    spy_10d = _pct_change(spy_close, 10)

    # ── auto_trader 同口径 RS 动量分(entry_score)预备 ───────────────────────
    # 目标:动能轮动面板可按「实盘下单口径」对全员排序,与 auto_trader/因子看板一致。
    # rs_score(63日RS) / vol_ratio(当日量÷vol_ma20) / drawdown_from_high(252日) 三项
    # 完全复现 auto_trader.scan_signals,再走 core.pool_policy.entry_score_from_config。
    from strategies.factors.rs_score import compute_rs_score
    from strategies.factors.drawdown import compute_drawdown_filter
    from core.pool_policy import (
        load_ai_priority_set, load_ai_tracker_boost_map, entry_score_from_config,
    )
    import config
    try:
        from web.services.factor_svc import get_factor_params_from_db
        _rs_params = get_factor_params_from_db('rs_score')
    except Exception:
        _rs_params = {}
    _rs_period  = int(_rs_params.get('period', 63))
    _rs_weights = str(_rs_params.get('weights', '') or '')
    _ai_set     = load_ai_priority_set()
    _boost_map  = load_ai_tracker_boost_map()
    try:
        from core.insider import get_insider_buys
        _insider_map = get_insider_buys(days=config.INSIDER_DAYS,
                                        min_value_k=config.INSIDER_MIN_VALUE_K)
    except Exception:
        _insider_map = {}

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

        # ── auto_trader 同口径 RS 动量分 ──────────────────────────────
        # 与 scan_signals 完全一致:rs_score=63日RS、vol_ratio=当日量÷vol_ma20、
        # drawdown_from_high=252日回撤;再走 entry_score_from_config(权重从 DB)。
        rs_score_at = None
        entry_score_at = None
        try:
            _r = compute_rs_score(df.copy(), spy_close, _rs_period, weights=_rs_weights)
            rs_v = _r['rs_score'].iloc[-1]
            rs_score_at = float(rs_v) if pd.notna(rs_v) else None
        except Exception:
            rs_score_at = None
        if rs_score_at is not None:
            _vol = df['volume']
            vol_ma20 = _vol.iloc[-20:].mean() if len(_vol) >= 20 else None
            at_vol_ratio = float(_vol.iloc[-1] / vol_ma20) if (vol_ma20 and vol_ma20 > 0) else 1.0
            try:
                _dd = compute_drawdown_filter(df.copy())['drawdown_from_high'].iloc[-1]
                ddown = float(_dd) if pd.notna(_dd) else -0.15
            except Exception:
                ddown = -0.15
            _sig = {
                'rs_score':           rs_score_at,
                'vol_ratio':          at_vol_ratio,
                'drawdown_from_high': ddown,
                'insider_score':      _insider_map.get(sym, {}).get('score', 0),
                'ai_tracker_boost':   _boost_map.get(sym, 0.0),
            }
            entry_score_at = round(entry_score_from_config(_sig), 4)

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
            # RS 动量分(实盘口径):rs_score=63日RS,entry_score=排序分,rank_tier=0(AI优先池)/1
            'rs_score':     rs_score_at,
            'entry_score':  entry_score_at,
            'rank_tier':    0 if sym in _ai_set else 1,
            'ai_priority':  sym in _ai_set,
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

    def _grp_median(members, field):
        vals = [r[field] for r in members if r[field] is not None]
        return float(np.median(vals)) if vals else None

    def _grp_adv(members, field):
        return sum(1 for r in members if (r[field] or 0) > 0)

    for gk, gv in groups_cfg.items():
        members = [r for r in raw_rows if r['group'] == gk]
        if not members:
            continue
        mom5_vals = [r['mom_5d'] for r in members if r['mom_5d'] is not None]
        flow_vals_g = [r['flow_score'] for r in members]
        n = len(members)
        adv3, adv5, adv10 = _grp_adv(members, 'rs_3d'), _grp_adv(members, 'rs_5d'), _grp_adv(members, 'rs_10d')
        leaders = sorted(members, key=lambda x: x['composite'], reverse=True)[:3]
        median_flow = float(np.median(flow_vals_g)) if flow_vals_g else 5.0
        flow_signal = 'inflow' if median_flow > 6 else ('outflow' if median_flow < 4 else 'neutral')
        groups_summary.append({
            'key':            gk,
            'label':          gv['label'],
            'color':          gv.get('color', '#94a3b8'),
            'count':          n,
            'median_mom_5d':  float(np.median([r['mom_5d'] for r in members if r['mom_5d'] is not None])) if mom5_vals else None,
            # 各窗口板块中位超额（前端热力卡按 3/5/10 日切换取值）
            'median_rs_3d':   _grp_median(members, 'rs_3d'),
            'median_rs_5d':   _grp_median(members, 'rs_5d'),
            'median_rs_10d':  _grp_median(members, 'rs_10d'),
            'advance':        adv5,            # 兼容旧字段（=5 日口径）
            'decline':        n - adv5,
            'advance_3d':     adv3,  'decline_3d':  n - adv3,
            'advance_5d':     adv5,  'decline_5d':  n - adv5,
            'advance_10d':    adv10, 'decline_10d': n - adv10,
            'flow_score':     round(median_flow, 2),
            'flow_signal':    flow_signal,
            'leaders':        [{'symbol': r['symbol'], 'composite': r['composite']} for r in leaders],
        })
    groups_summary.sort(key=lambda x: (x['median_rs_5d'] or -1), reverse=True)

    # ── 篮子层面 A/D 线 + 金额加权 OBV ───────────────────────
    basket = _compute_basket_flow(price_map, all_syms)

    # ── Top-N ────────────────────────────────────────────
    # top4   = 短线动能口径(composite 降序,raw_rows 已按 composite 排好)
    # top4_rs= RS 动量分口径(rank_tier 升序→entry_score 降序,与 auto_trader 下单次序一致)
    top4 = [r['symbol'] for r in raw_rows[:4]]
    _rs_ranked = sorted(
        [r for r in raw_rows if r.get('entry_score') is not None],
        key=lambda r: (r['rank_tier'], -r['entry_score']),
    )
    top4_rs = [r['symbol'] for r in _rs_ranked[:4]]

    result = _clean_floats({
        'rows':          raw_rows,
        'groups':        groups_summary,
        'basket':        basket,
        'top4_rs':       top4_rs,
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


# ── 财报对比(AI 追踪器「财报对比」tab)──────────────────────────────────────

_EARNINGS_CACHE     = ROOT / 'data' / '.earnings_compare_cache.pkl'
_EARNINGS_TTL_HOURS = 24


def _load_earnings_cache() -> dict:
    import pickle
    if not _EARNINGS_CACHE.exists():
        return {}
    try:
        with open(_EARNINGS_CACHE, 'rb') as f:
            return pickle.load(f)
    except Exception:
        return {}


def _save_earnings_cache(data: dict) -> None:
    import pickle
    try:
        _EARNINGS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(_EARNINGS_CACHE, 'wb') as f:
            pickle.dump(data, f)
    except Exception:
        pass


def _fetch_quarters(sym: str, n: int = 5) -> list[dict]:
    """最近 n 季 {quarter, revenue_b, net_income_b, eps},来自 yfinance 季度利润表。
    yfinance 季度数据通常只给 4-5 季,缺失字段优雅置 None。"""
    import yfinance as yf
    try:
        q = yf.Ticker(sym).quarterly_income_stmt
    except Exception:
        return []
    if q is None or getattr(q, 'empty', True):
        return []

    def _row(labels):
        for lb in labels:
            if lb in q.index:
                return q.loc[lb]
        return None

    rev = _row(['Total Revenue', 'Operating Revenue', 'Revenue'])
    ni  = _row(['Net Income', 'Net Income Common Stockholders',
                'Net Income From Continuing Operation Net Minority Interest'])
    eps = _row(['Diluted EPS', 'Basic EPS'])
    # 列 = 财季日期,降序(最新在前)
    cols = list(q.columns)[:n]
    out = []
    for c in cols:
        def _g(series, scale=1.0):
            if series is None or c not in series.index:
                return None
            v = series.get(c)
            try:
                v = float(v)
            except (TypeError, ValueError):
                return None
            if v != v:  # NaN
                return None
            return round(v / scale, 2)
        out.append({
            'quarter':      str(c)[:7],          # 2025-10
            'revenue_b':    _g(rev, 1e9),
            'net_income_b': _g(ni, 1e9),
            'eps':          _g(eps, 1.0),
        })
    out.reverse()   # 改为时间升序,前端从左到右读
    return out


def get_earnings_compare(symbols: list[str], force: bool = False) -> dict:
    """最多 3 只 AI 标的的财报横向对比:快照(YoY 增速/估值/市值)+ 最近 5 季营收/净利/EPS。
    单股季度数据 24h 缓存(yfinance 慢且限流)。"""
    from datetime import datetime, timedelta
    from core.universe import get_stock_info

    syms = [s.strip().upper() for s in symbols if s and s.strip()][:3]
    if not syms:
        return {'companies': []}

    info_map = get_stock_info(syms)
    cache = _load_earnings_cache()
    now = datetime.now()
    dirty = False

    companies = []
    for s in syms:
        ent = cache.get(s)
        if force or not ent or (now - ent.get('_time', now - timedelta(days=999))) > timedelta(hours=_EARNINGS_TTL_HOURS):
            quarters = _fetch_quarters(s)
            cache[s] = {'_time': now, 'quarters': quarters}
            dirty = True
        else:
            quarters = ent.get('quarters', [])
        info = info_map.get(s, {})
        companies.append({
            'symbol':          s,
            'market_cap_b':    info.get('market_cap_b'),
            'revenue_growth':  info.get('revenue_growth'),    # 最新季 YoY 营收增速
            'earnings_growth': info.get('earnings_growth'),   # 最新季 YoY 盈利增速
            'pe_ratio':        info.get('pe_ratio'),
            'ps_ratio':        info.get('ps_ratio'),
            'gross_margins':   info.get('gross_margins'),
            'quarters':        quarters,
        })

    if dirty:
        _save_earnings_cache(cache)
    return _clean_floats({'companies': companies})


if __name__ == '__main__':
    import json as _json
    print(_json.dumps(get_earnings_compare(['MU', 'LITE', 'MRVL']), ensure_ascii=False, indent=2))
