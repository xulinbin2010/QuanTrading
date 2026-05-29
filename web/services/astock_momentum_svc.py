"""A 股板块强度 + 个股动能扫描（akshare 数据源，沪深300 基准）。

与美股 ai_momentum_svc.py 并行：
- 复用其纯价格量算法（_pct_change/_vol_ratio/_flow_metrics/_flow_score_0_10/_zscore_to_0_10）
- 基准 SPY → 沪深300（HS300）
- 双板块模式 mode='sw'（申万一级行业全市场轮动）| 'theme'（自定义主题板块）

输出结构与美股动能 svc 完全一致（rows/groups/basket/top4），前端复用同款渲染。
"""
from __future__ import annotations

import math
import json
import logging
import threading
from datetime import datetime, timedelta, date
from pathlib import Path

import numpy as np
import pandas as pd

# 复用美股动能 svc 的纯算法函数（市场无关）
from web.services.ai_momentum_svc import (
    _pct_change, _vol_ratio, _flow_metrics, _flow_score_0_10,
    _zscore_to_0_10, _clean_floats,
)
from core.astock_data_store import AStockDataStore
from core import astock_universe as _au

_logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
_CACHE_TTL_MINUTES = 30
_BENCHMARK = 'HS300'   # 沪深300 作为大盘基准

# 每种 mode 独立缓存文件
def _cache_path(mode: str) -> Path:
    return ROOT / 'data' / f'.astock_momentum_{mode}_cache.json'

_scan_running: dict[str, bool] = {}
_scan_lock = threading.Lock()


# ── 板块定义：sw（申万行业）/ theme（主题）──────────────────

def _build_groups(mode: str) -> tuple[dict, dict, dict]:
    """返回 (groups_cfg, sym_to_group, code_names)。

    groups_cfg: { group_key: {label, color, symbols[]} }
    sym_to_group: { code: group_key }
    code_names: { code: 名称 }
    """
    groups_cfg: dict = {}
    sym_to_group: dict = {}
    code_names: dict = {}

    if mode == 'sw':
        sw = _au.get_sw_l1_industries(top_n=40)
        palette = ['#f97316', '#a855f7', '#22c55e', '#ef4444', '#3b82f6',
                   '#eab308', '#06b6d4', '#ec4899', '#14b8a6', '#8b5cf6']
        for i, (ind_name, info) in enumerate(sw.items()):
            gk = info['code']
            groups_cfg[gk] = {
                'label': ind_name,
                'color': palette[i % len(palette)],
                'symbols': info['symbols'],
            }
            code_names.update(info.get('names', {}))
            for code in info['symbols']:
                if code not in sym_to_group:
                    sym_to_group[code] = gk
    else:  # theme
        themes = _au.load_themes().get('groups', {})
        for gk, gv in themes.items():
            groups_cfg[gk] = {
                'label': gv['label'],
                'color': gv.get('color', '#94a3b8'),
                'symbols': [str(s).zfill(6) for s in gv.get('symbols', [])],
            }
            for code in groups_cfg[gk]['symbols']:
                if code not in sym_to_group:
                    sym_to_group[code] = gk
        # 主题模式补名称
        all_codes = list(sym_to_group.keys())
        code_names = _au.get_astock_names(all_codes)

    return groups_cfg, sym_to_group, code_names


# ── 缓存 ──────────────────────────────────────────────────

def _read_cache(mode: str) -> dict | None:
    p = _cache_path(mode)
    if not p.exists():
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(mode: str, data: dict) -> None:
    p = _cache_path(mode)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ── 主入口 ──────────────────────────────────────────────────

def scan_momentum(mode: str = 'sw', force: bool = False) -> dict:
    """A 股动能扫描。mode='sw'（申万行业）|'theme'（主题板块）。"""
    if mode not in ('sw', 'theme'):
        mode = 'sw'

    cached = _read_cache(mode)
    if not force and cached is not None:
        try:
            age = datetime.now() - datetime.fromisoformat(cached['last_updated'])
            if age < timedelta(minutes=_CACHE_TTL_MINUTES):
                return cached
        except Exception:
            pass

    key = f'astock_{mode}'
    thread_to_wait = None
    with _scan_lock:
        if not _scan_running.get(key, False):
            _scan_running[key] = True
            t = threading.Thread(target=_run_scan_bg, args=(mode,), daemon=True)
            t.start()
            if cached is None or force:
                thread_to_wait = t

    if thread_to_wait is not None:
        thread_to_wait.join()
        cached = _read_cache(mode)

    if cached is not None:
        return {**cached, 'scanning': _scan_running.get(key, False)}
    return {'rows': [], 'groups': [], 'basket': {}, 'top4': [], 'mode': mode, 'scanning': True}


def _run_scan_bg(mode: str) -> None:
    try:
        _do_scan(mode)
    except Exception as e:
        _logger.error(f'[AStockMomentum/{mode}] 扫描失败：{e}', exc_info=True)
    finally:
        with _scan_lock:
            _scan_running.pop(f'astock_{mode}', None)


def _trend_quality(close: pd.Series, ema7_s: pd.Series, n: int = 20) -> dict:
    """趋势质量分（0-10）：站上EMA7占比(持续性) + log价格回归R²(平滑·仅上升) × 暴涨惩罚。

    暴涨不硬删除，而是扣分：单日涨幅 >9.5%（涨停级异动）或近 n 日累计 >50% 时按超出幅度降权。
    """
    c = close.tail(n)
    if len(c) < 8:
        return {'trend_score': None, 'ema7_hold': None, 'trend_r2': None}
    e = ema7_s.reindex(c.index)
    hold = float((c.values >= e.values).mean())          # 收盘站上 EMA7 的天数占比
    y = np.log(c.values)
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    yhat = slope * x + intercept
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = (1 - float(((y - yhat) ** 2).sum()) / ss_tot) if ss_tot > 0 else 0.0
    r2_eff = r2 if slope > 0 else 0.0                    # 必须是上升趋势才算平滑度
    daily = c.pct_change().dropna()
    max_1d = float(daily.max()) if len(daily) else 0.0
    ret_n = float(c.iloc[-1] / c.iloc[0] - 1)
    pen = 1.0
    if max_1d > 0.095:
        pen *= max(0.4, 1 - (max_1d - 0.095) * 5)
    if ret_n > 0.5:
        pen *= max(0.3, 1 - (ret_n - 0.5))
    score = (0.5 * hold + 0.5 * r2_eff) * 10 * pen
    return {'trend_score': round(score, 2), 'ema7_hold': round(hold, 2), 'trend_r2': round(r2_eff, 2)}


def _do_scan(mode: str, refresh: bool = False) -> dict:
    groups_cfg, sym_to_group, code_names = _build_groups(mode)
    all_syms = list(sym_to_group.keys())
    _logger.info(f'[AStockMomentum/{mode}] 扫描 {len(all_syms)} 只...')

    store = AStockDataStore()
    if refresh:
        # 次日复核：重拉最近正式前复权日线，覆盖前一日快照补的原始价 bar
        try:
            m = store.refresh_recent(all_syms, days=15)
            store.refresh_index(_BENCHMARK)
            _logger.info(f'[AStockMomentum/{mode}] 次日复核重拉 {m} 只')
        except Exception as e:
            _logger.warning(f'[AStockMomentum/{mode}] 次日复核失败：{e}')
    else:
        # 当日补齐：sina 历史日K对当天有延迟，盘后先用实时快照把当天 bar 补进本地
        try:
            n = store.topup_today_from_spot(all_syms)
            store.topup_index_today(_BENCHMARK)
            if n:
                _logger.info(f'[AStockMomentum/{mode}] 实时快照补当日 {n} 只')
        except Exception as e:
            _logger.warning(f'[AStockMomentum/{mode}] 当日补齐失败：{e}')
    start_d = str(date.today() - timedelta(days=90))
    price_map = store.get(all_syms + [_BENCHMARK], start=start_d, auto_update=True)

    bench = price_map.get(_BENCHMARK)
    if bench is None or len(bench) < 15:
        raise RuntimeError('沪深300 基准数据不可用')
    bc = bench['close']
    b3, b5, b10 = _pct_change(bc, 3), _pct_change(bc, 5), _pct_change(bc, 10)

    raw_rows: list[dict] = []
    for sym in all_syms:
        df = price_map.get(sym)
        if df is None or len(df) < 15:
            continue
        close = df['close']
        mom_3d, mom_5d, mom_10d = _pct_change(close, 3), _pct_change(close, 5), _pct_change(close, 10)
        rs_3d = mom_3d - b3 if (mom_3d is not None and b3 is not None) else None
        rs_5d = mom_5d - b5 if (mom_5d is not None and b5 is not None) else None
        rs_10d = mom_10d - b10 if (mom_10d is not None and b10 is not None) else None
        vr = _vol_ratio(df, short=3, long=20)
        flow = _flow_metrics(df)
        flow_score = _flow_score_0_10(flow['obv_slope'], flow['up_vol_ratio'])
        accel = (mom_3d / 3.0 > mom_5d / 5.0) if (mom_3d is not None and mom_5d is not None) else False
        # 均线状态：EMA7（短期）/ EMA21（中期），现价跌破即视为走弱
        ema7_s = close.ewm(span=7, adjust=False).mean()
        ema7 = float(ema7_s.iloc[-1])
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        last_close = float(close.iloc[-1])
        above_ema7 = last_close >= ema7
        above_ema21 = last_close >= ema21
        ema_state = 'strong' if above_ema7 and above_ema21 else ('weak' if above_ema21 else 'broken')
        tq = _trend_quality(close, ema7_s, n=20)
        # 流通市值（亿元）= 收盘价 × 流通股本；无股本数据时为 None
        shares = df['shares'].iloc[-1] if 'shares' in df.columns else None
        market_cap = (last_close * float(shares) / 1e8) if (shares is not None and not pd.isna(shares)) else None
        gk = sym_to_group[sym]
        raw_rows.append({
            'symbol': sym, 'name': code_names.get(sym, sym),
            'group': gk, 'group_label': groups_cfg[gk]['label'],
            'group_color': groups_cfg[gk].get('color', '#94a3b8'),
            'close': last_close, 'market_cap': market_cap,
            'mom_3d': mom_3d, 'mom_5d': mom_5d, 'mom_10d': mom_10d,
            'rs_3d': rs_3d, 'rs_5d': rs_5d, 'rs_10d': rs_10d,
            'vol_ratio': vr, 'obv_slope': flow['obv_slope'],
            'up_vol_ratio': flow['up_vol_ratio'], 'flow_score': flow_score,
            'accel': accel,
            'ema7': ema7, 'ema21': ema21,
            'above_ema7': above_ema7, 'above_ema21': above_ema21, 'ema_state': ema_state,
            'trend_score': tq['trend_score'], 'ema7_hold': tq['ema7_hold'], 'trend_r2': tq['trend_r2'],
        })

    # 组内中位（rs_vs_group_5d）
    rs5_by_group: dict[str, list[float]] = {}
    for r in raw_rows:
        if r['rs_5d'] is not None:
            rs5_by_group.setdefault(r['group'], []).append(r['rs_5d'])
    group_median = {gk: float(np.median(vs)) for gk, vs in rs5_by_group.items() if vs}
    for r in raw_rows:
        med = group_median.get(r['group'])
        r['rs_vs_group_5d'] = (r['rs_5d'] - med) if (r['rs_5d'] is not None and med is not None) else None

    # z-score 复合分（与美股同权重）
    z_mom5 = _zscore_to_0_10([r['rs_5d'] for r in raw_rows])
    z_mom3 = _zscore_to_0_10([r['rs_3d'] for r in raw_rows])
    z_rsgrp = _zscore_to_0_10([r['rs_vs_group_5d'] for r in raw_rows])
    z_vol = _zscore_to_0_10([r['vol_ratio'] for r in raw_rows])
    for i, r in enumerate(raw_rows):
        composite = (0.35 * z_mom5[i] + 0.20 * z_mom3[i] + 0.20 * z_rsgrp[i]
                     + 0.15 * z_vol[i] + 0.10 * r['flow_score'])
        if r['accel']:
            composite = min(10.0, composite + 0.5)
        r['z_mom_5d'] = round(z_mom5[i], 2)
        r['z_mom_3d'] = round(z_mom3[i], 2)
        r['z_rs_group'] = round(z_rsgrp[i], 2)
        r['z_vol_ratio'] = round(z_vol[i], 2)
        r['composite'] = round(composite, 2)

    raw_rows.sort(key=lambda x: x['composite'], reverse=True)
    for i, r in enumerate(raw_rows):
        r['rank'] = i + 1

    # 子组聚合
    groups_summary: list[dict] = []
    for gk, gv in groups_cfg.items():
        members = [r for r in raw_rows if r['group'] == gk]
        if not members:
            continue
        rs5_vals = [r['rs_5d'] for r in members if r['rs_5d'] is not None]
        mom5_vals = [r['mom_5d'] for r in members if r['mom_5d'] is not None]
        flow_vals_g = [r['flow_score'] for r in members]
        advance = sum(1 for r in members if (r['rs_5d'] or 0) > 0)
        leaders = sorted(members, key=lambda x: x['composite'], reverse=True)[:3]
        median_flow = float(np.median(flow_vals_g)) if flow_vals_g else 5.0
        flow_signal = 'inflow' if median_flow > 6 else ('outflow' if median_flow < 4 else 'neutral')
        groups_summary.append({
            'key': gk, 'label': gv['label'], 'color': gv.get('color', '#94a3b8'),
            'count': len(members),
            'median_mom_5d': float(np.median(mom5_vals)) if mom5_vals else None,
            'median_rs_5d': float(np.median(rs5_vals)) if rs5_vals else None,
            'advance': advance, 'decline': len(members) - advance,
            'flow_score': round(median_flow, 2), 'flow_signal': flow_signal,
            'leaders': [{'symbol': r['symbol'], 'name': r.get('name', r['symbol']),
                         'composite': r['composite']} for r in leaders],
        })
    groups_summary.sort(key=lambda x: (x['median_rs_5d'] or -1), reverse=True)

    basket = _compute_basket_flow_cny(price_map, all_syms)
    top4 = [r['symbol'] for r in raw_rows[:4]]

    result = _clean_floats({
        'rows': raw_rows, 'groups': groups_summary, 'basket': basket,
        'top4': top4, 'mode': mode,
        'benchmark': {'mom_3d': b3, 'mom_5d': b5, 'mom_10d': b10},
        'total': len(raw_rows),
        'last_updated': datetime.now().isoformat(timespec='seconds'),
    })
    _write_cache(mode, result)
    return result


def _compute_basket_flow_cny(price_map: dict, syms: list[str]) -> dict:
    """A/D 线 + 金额加权资金流（单位：亿元人民币，缩放 1e8）。"""
    n_days = 10
    closes, vols = [], []
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
    close_mat = close_mat.iloc[-(n_days + 1):]
    vol_mat = vol_mat.iloc[-(n_days + 1):]
    diff = close_mat.diff()
    advance = (diff > 0).sum(axis=1)
    decline = (diff < 0).sum(axis=1)
    ad_daily = (advance - decline).iloc[1:]
    ad_line = ad_daily.cumsum()
    money_flow_daily = (np.sign(diff) * close_mat * vol_mat).sum(axis=1).iloc[1:]
    money_flow_cum = money_flow_daily.cumsum()
    return {
        'dates': [d.strftime('%Y-%m-%d') for d in ad_line.index],
        'ad_daily': [int(v) for v in ad_daily.values],
        'ad_cumulative': [int(v) for v in ad_line.values],
        'money_flow_b': (money_flow_daily / 1e8).round(2).tolist(),
        'money_flow_cum_b': (money_flow_cum / 1e8).round(2).tolist(),
        'advance_today': int(advance.iloc[-1]) if len(advance) else 0,
        'decline_today': int(decline.iloc[-1]) if len(decline) else 0,
        'advance_5d': int(ad_daily.iloc[-5:].sum()) if len(ad_daily) >= 5 else int(ad_daily.sum()),
        'money_flow_5d_b': float(money_flow_daily.iloc[-5:].sum() / 1e8) if len(money_flow_daily) >= 5 else float(money_flow_daily.sum() / 1e8),
    }


# ── 个股 K 线详情（给前端 K 线弹窗用）────────────────────────

def get_astock_detail(code: str, days: int = 120) -> dict:
    """单只 A 股 K 线 + MA10/20 + RS（对沪深300）。结构对齐美股 /factors/stock。"""
    code = str(code).zfill(6)
    store = AStockDataStore()
    start_d = str(date.today() - timedelta(days=days + 120))
    pm = store.get([code, _BENCHMARK], start=start_d, auto_update=True)
    df = pm.get(code)
    if df is None or df.empty:
        return {'ohlcv': [], 'factors': [], 'fundamental': {}}
    df = df.tail(days + 30)
    # K 线图均线统一用 EMA7/EMA21（与美股一致）
    ma10 = df['close'].ewm(span=7, adjust=False).mean()
    ma20 = df['close'].ewm(span=21, adjust=False).mean()
    bench = pm.get(_BENCHMARK)
    # RS：个股累计收益 - 沪深300 累计收益（对齐日期）
    rs_series = None
    if bench is not None and not bench.empty:
        aligned = pd.concat([df['close'].rename('s'), bench['close'].rename('b')], axis=1).dropna()
        if len(aligned) > 20:
            s_ret = aligned['s'] / aligned['s'].iloc[0] - 1
            b_ret = aligned['b'] / aligned['b'].iloc[0] - 1
            rs_series = (s_ret - b_ret).reindex(df.index)

    ohlcv = [{'date': d.strftime('%Y-%m-%d'),
              'open': float(r['open']), 'high': float(r['high']),
              'low': float(r['low']), 'close': float(r['close']),
              'volume': float(r['volume'])} for d, r in df.iterrows()]
    factors = []
    for i, (d, _) in enumerate(df.iterrows()):
        factors.append({
            'date': d.strftime('%Y-%m-%d'),
            'ma_fast': float(ma10.iloc[i]) if not pd.isna(ma10.iloc[i]) else None,
            'ma_slow': float(ma20.iloc[i]) if not pd.isna(ma20.iloc[i]) else None,
            'rs_score': float(rs_series.iloc[i]) if (rs_series is not None and not pd.isna(rs_series.iloc[i])) else None,
        })

    # 个股信息条：名称 + 所属板块 + 申万行业 + 最新动能/均线状态（替代 A 股无效的突破/放量因子）
    from core import astock_universe as _au
    close = df['close']
    last_close = float(close.iloc[-1])
    ema7v = float(close.ewm(span=7, adjust=False).mean().iloc[-1])
    ema21v = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
    ema_state = 'strong' if (last_close >= ema7v and last_close >= ema21v) else ('weak' if last_close >= ema21v else 'broken')
    mom_5d, mom_20d = _pct_change(close, 5), _pct_change(close, 20)
    rs_5d = None
    if bench is not None and not bench.empty:
        b5 = _pct_change(bench['close'], 5)
        if mom_5d is not None and b5 is not None:
            rs_5d = mom_5d - b5
    _, group_label = _au.theme_group_of(code)
    shares = df['shares'].iloc[-1] if 'shares' in df.columns else None
    market_cap = (last_close * float(shares) / 1e8) if (shares is not None and not pd.isna(shares)) else None
    tq = _trend_quality(close, close.ewm(span=7, adjust=False).mean(), n=20)
    info = {
        'name': _au.get_astock_names([code]).get(code, code),
        'group_label': group_label,
        'sw_industry': _au.sw3_industry_of(code),
        'ema_state': ema_state,
        'mom_5d': mom_5d, 'mom_20d': mom_20d,
        'vol_ratio': _vol_ratio(df, short=3, long=20),
        'rs_5d': rs_5d, 'close': last_close, 'market_cap': market_cap,
        'trend_score': tq['trend_score'], 'ema7_hold': tq['ema7_hold'],
    }
    return _clean_floats({'ohlcv': ohlcv[-days:], 'factors': factors[-days:],
                          'fundamental': {}, 'info': info})


# ── 命令行入口：盘后增量更新 + 重建扫描缓存（供调度器调用）──────────
if __name__ == '__main__':
    import argparse
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s', datefmt='%H:%M:%S')
    parser = argparse.ArgumentParser(description='A 股盘后数据更新 + 扫描缓存重建')
    parser.add_argument('--mode', choices=['sw', 'theme', 'all'], default='all',
                        help='更新哪种模式的缓存（默认全部）')
    parser.add_argument('--refresh', action='store_true',
                        help='次日复核：重拉正式前复权日线覆盖前一日快照 bar（次日早任务用）')
    args = parser.parse_args()
    modes = ['theme', 'sw'] if args.mode == 'all' else [args.mode]
    tag = '次日复核' if args.refresh else '盘后更新'
    for _m in modes:
        _logger.info(f'[AStockUpdate/{tag}] 开始 {_m} ...')
        try:
            _r = _do_scan(_m, refresh=args.refresh)
            _logger.info(f'[AStockUpdate/{tag}] {_m} 完成：rows={len(_r.get("rows", []))} groups={len(_r.get("groups", []))}')
        except Exception as _e:
            _logger.error(f'[AStockUpdate/{tag}] {_m} 失败：{_e}', exc_info=True)
