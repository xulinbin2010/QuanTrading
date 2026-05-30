"""A 股动能轮动回测:每周一 rebalance,持有 composite 前 N 名,沪深300 基准。

复用 astock_momentum_svc._do_scan 的算法,但在历史任一日上重算(截断价格序列到该日)。
退出规则:仅"跌出前 N 卖出",无止损/止盈。
T+1 简化:用当周第一个交易日开盘价成交(rebalance 日 = 周一或周一假期顺延)。
"""
from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
_PE_CACHE_PATH = ROOT / 'data' / '.astock_pe_cache.json'
_PE_CACHE_TTL_HOURS = 24

from web.services.ai_momentum_svc import (
    _pct_change, _vol_ratio, _flow_metrics, _flow_score_0_10,
    _zscore_to_0_10, _clean_floats,
)
from core.astock_data_store import AStockDataStore
from core import astock_universe as _au

_logger = logging.getLogger(__name__)
_BENCHMARK = 'HS300'

# 任务存储(内存:简单可靠,服务重启即丢)
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()


def _compute_composite_for_date(
    price_map: dict[str, pd.DataFrame],
    bench_df: pd.DataFrame,
    eval_date: pd.Timestamp,
    sym_to_group: dict[str, str],
) -> list[dict]:
    """在 eval_date(含当日)截止的数据上算所有股票 composite 分,返回降序列表。"""
    bench_close = bench_df['close'].loc[:eval_date]
    if len(bench_close) < 15:
        return []
    b3 = _pct_change(bench_close, 3)
    b5 = _pct_change(bench_close, 5)
    b10 = _pct_change(bench_close, 10)

    raw_rows: list[dict] = []
    for sym, df in price_map.items():
        df_cut = df.loc[:eval_date]
        if len(df_cut) < 15:
            continue
        close = df_cut['close']
        mom_3d, mom_5d, mom_10d = _pct_change(close, 3), _pct_change(close, 5), _pct_change(close, 10)
        rs_3d = mom_3d - b3 if (mom_3d is not None and b3 is not None) else None
        rs_5d = mom_5d - b5 if (mom_5d is not None and b5 is not None) else None
        rs_10d = mom_10d - b10 if (mom_10d is not None and b10 is not None) else None
        vr = _vol_ratio(df_cut, short=3, long=20)
        flow = _flow_metrics(df_cut)
        flow_score = _flow_score_0_10(flow['obv_slope'], flow['up_vol_ratio'])
        accel = (mom_3d / 3.0 > mom_5d / 5.0) if (mom_3d is not None and mom_5d is not None) else False
        raw_rows.append({
            'symbol': sym, 'group': sym_to_group.get(sym),
            'mom_3d': mom_3d, 'mom_5d': mom_5d, 'mom_10d': mom_10d,
            'rs_3d': rs_3d, 'rs_5d': rs_5d, 'rs_10d': rs_10d,
            'vol_ratio': vr, 'flow_score': flow_score, 'accel': accel,
        })

    # 组内中位 → rs_vs_group_5d
    rs5_by_group: dict[str, list[float]] = {}
    for r in raw_rows:
        if r['rs_5d'] is not None:
            rs5_by_group.setdefault(r['group'], []).append(r['rs_5d'])
    group_median = {gk: float(np.median(vs)) for gk, vs in rs5_by_group.items() if vs}
    for r in raw_rows:
        med = group_median.get(r['group'])
        r['rs_vs_group_5d'] = (r['rs_5d'] - med) if (r['rs_5d'] is not None and med is not None) else None

    # z-score 复合
    z_mom5 = _zscore_to_0_10([r['rs_5d'] for r in raw_rows])
    z_mom3 = _zscore_to_0_10([r['rs_3d'] for r in raw_rows])
    z_rsgrp = _zscore_to_0_10([r['rs_vs_group_5d'] for r in raw_rows])
    z_vol = _zscore_to_0_10([r['vol_ratio'] for r in raw_rows])
    for i, r in enumerate(raw_rows):
        composite = (0.35 * z_mom5[i] + 0.20 * z_mom3[i] + 0.20 * z_rsgrp[i]
                     + 0.15 * z_vol[i] + 0.10 * r['flow_score'])
        if r['accel']:
            composite = min(10.0, composite + 0.5)
        r['composite'] = composite

    raw_rows.sort(key=lambda x: x['composite'], reverse=True)
    return raw_rows


# ── 选股器(strategy 路由)─────────────────────────────────

def _select_momentum(scored_rows: list[dict], top_n: int, **_) -> list[str]:
    """纯动能:composite 前 N。"""
    return [r['symbol'] for r in scored_rows[:top_n]]


def _select_momentum_filtered(scored_rows: list[dict], top_n: int,
                              price_map: dict, eval_date: pd.Timestamp, **_) -> list[str]:
    """动能 + 趋势过滤:composite 前 N 且 收盘 ≥ EMA21。"""
    out: list[str] = []
    for r in scored_rows:
        if len(out) >= top_n:
            break
        sym = r['symbol']
        df = price_map.get(sym)
        if df is None:
            continue
        close = df['close'].loc[:eval_date]
        if len(close) < 21:
            continue
        ema21 = float(close.ewm(span=21, adjust=False).mean().iloc[-1])
        if float(close.iloc[-1]) >= ema21:
            out.append(sym)
    return out


def _select_sector_rotation(scored_rows: list[dict], top_n: int,
                            top_sectors: int = 2, **_) -> list[str]:
    """板块轮动:先按板块强度(组内 composite 中位)排序,取前 K 板块,每板块取龙头。"""
    by_group: dict[str, list[dict]] = {}
    for r in scored_rows:
        g = r.get('group')
        if g is None:
            continue
        by_group.setdefault(g, []).append(r)
    group_scores = {
        g: float(np.median([r['composite'] for r in rows if r.get('composite') is not None]))
        for g, rows in by_group.items()
        if any(r.get('composite') is not None for r in rows)
    }
    top_sec = sorted(group_scores.items(), key=lambda x: x[1], reverse=True)[:top_sectors]
    per_sec = math.ceil(top_n / max(top_sectors, 1))
    out: list[str] = []
    for g, _ in top_sec:
        sorted_in = sorted(by_group[g], key=lambda r: r.get('composite') or 0, reverse=True)
        for r in sorted_in[:per_sec]:
            if len(out) >= top_n:
                break
            out.append(r['symbol'])
        if len(out) >= top_n:
            break
    return out


def _select_quality_momentum(scored_rows: list[dict], top_n: int,
                             pe_map: dict[str, float] | None = None, **_) -> list[str]:
    """质量动能:composite × (1 + 0.5 × 归一化 EP),PE 越低权重越高。无 PE 数据则中性。"""
    pe_map = pe_map or {}
    eps = [1.0 / pe_map[r['symbol']] for r in scored_rows
           if r['symbol'] in pe_map and pe_map[r['symbol']] > 0]
    if eps:
        ep_min, ep_max = min(eps), max(eps)
    else:
        ep_min, ep_max = 0.0, 0.0

    for r in scored_rows:
        pe = pe_map.get(r['symbol'])
        if pe and pe > 0 and ep_max > ep_min:
            ep_norm = (1.0 / pe - ep_min) / (ep_max - ep_min)
            boost = 0.5 * ep_norm
        else:
            boost = 0.0
        r['quality_score'] = (r.get('composite') or 0) * (1 + boost)
    ranked = sorted(scored_rows, key=lambda r: r['quality_score'], reverse=True)
    return [r['symbol'] for r in ranked[:top_n]]


_STRATEGIES = {
    'momentum':           _select_momentum,
    'momentum_filtered':  _select_momentum_filtered,
    'sector_rotation':    _select_sector_rotation,
    'quality_momentum':   _select_quality_momentum,
}


# ── 全市场 PE 数据(quality_momentum 用)──────────────────

def _load_pe_map() -> dict[str, float]:
    """全市场动态 PE 快照,24h 缓存。akshare stock_zh_a_spot_em 一次拉 5000+ 只。"""
    if _PE_CACHE_PATH.exists():
        try:
            with open(_PE_CACHE_PATH, encoding='utf-8') as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data['updated'])
            if datetime.now() - updated < timedelta(hours=_PE_CACHE_TTL_HOURS):
                return data['pe_map']
        except Exception:
            pass
    from core.astock_data_store import ak
    try:
        spot = ak.stock_zh_a_spot_em()
    except Exception as e:
        _logger.warning(f'[AStockBacktest] PE 快照拉取失败:{e}')
        return {}
    pe_col = '市盈率-动态' if '市盈率-动态' in spot.columns else (
        '市盈率' if '市盈率' in spot.columns else None
    )
    if pe_col is None:
        return {}
    pe_map: dict[str, float] = {}
    for _, row in spot.iterrows():
        code = str(row.get('代码', '')).zfill(6)
        pe = row.get(pe_col)
        if pe is not None and not pd.isna(pe) and pe > 0:
            pe_map[code] = float(pe)
    _PE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PE_CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.now().isoformat(), 'pe_map': pe_map}, f)
    _logger.info(f'[AStockBacktest] PE 快照已刷新:{len(pe_map)} 只')
    return pe_map


def run_backtest(
    start_date: str,
    end_date: str,
    initial_cash: float = 100_000,
    top_n: int = 4,
    groups: list[str] | None = None,
    strategy: str = 'momentum',
) -> dict:
    """主入口。每周第一个交易日 rebalance,持有 strategy 选出的前 top_n 等分。"""
    if strategy not in _STRATEGIES:
        raise ValueError(f'未知策略 {strategy},可选:{list(_STRATEGIES.keys())}')
    themes_cfg = _au.load_themes().get('groups', {})
    if groups:
        themes_cfg = {k: v for k, v in themes_cfg.items() if k in groups}
    sym_to_group: dict[str, str] = {}
    for gk, gv in themes_cfg.items():
        for code in gv.get('symbols', []):
            sym_to_group[str(code).zfill(6)] = gk
    all_syms = list(sym_to_group.keys())
    if not all_syms:
        raise ValueError('股票池为空')

    # 数据加载:留 150 天前置窗口给 RS 计算。
    # auto_update=False:依赖本地缓存(sina 串行拉 186 只要 60-90s,体验差;
    # 假设用户已在「A 股追踪」theme 模式预热过,缓存命中率应近 100%)
    data_start = (pd.Timestamp(start_date) - pd.Timedelta(days=150)).strftime('%Y-%m-%d')
    store = AStockDataStore()
    price_map = store.get(all_syms + [_BENCHMARK], start=data_start, end=end_date, auto_update=False)
    bench_df = price_map.pop(_BENCHMARK, None)
    if bench_df is None or bench_df.empty:
        raise RuntimeError('沪深300 基准数据缺失。请先在「A 股追踪」页面用 theme 模式跑一次扫描预热数据。')
    missing = [s for s in all_syms if s not in price_map]
    if len(missing) > len(all_syms) * 0.1:   # 缺失超 10% 提示预热
        raise RuntimeError(
            f'本地缓存缺失 {len(missing)}/{len(all_syms)} 只股票数据(超 10%)。'
            f'请先在「A 股追踪」页面用 theme 模式跑一次扫描预热,然后再回测。'
        )

    # 回测窗口内的所有交易日(以 HS300 为准)
    bench_window = bench_df.loc[start_date:end_date]
    all_dates: list[pd.Timestamp] = list(bench_window.index)
    if len(all_dates) < 5:
        raise ValueError(f'{start_date} ~ {end_date} 交易日不足({len(all_dates)} 天)')

    # 每周第一个交易日 = rebalance 日(周一,或周一假期顺延)
    rebalance_dates: list[pd.Timestamp] = []
    seen_weeks: set = set()
    for d in all_dates:
        wk = (d.year, d.isocalendar()[1])
        if wk not in seen_weeks:
            seen_weeks.add(wk)
            rebalance_dates.append(d)
    rebal_set = set(rebalance_dates)
    selector = _STRATEGIES[strategy]
    # quality_momentum 用 PE 加权,提前加载
    pe_map = _load_pe_map() if strategy == 'quality_momentum' else {}
    _logger.info(f'[AStockBacktest] strategy={strategy} | {len(all_dates)} 交易日 / {len(rebalance_dates)} rebalance 日'
                 + (f' | PE map {len(pe_map)} 只' if pe_map else ''))

    cash = float(initial_cash)
    # positions[sym] = {'qty', 'entry_price', 'entry_date'(Timestamp)}
    positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_curve: list[dict] = []
    bench_initial = float(bench_df.loc[all_dates[0], 'close'])

    for i, d in enumerate(all_dates):
        # rebalance:用前一交易日数据评分(避免前瞻),当日开盘价成交
        if d in rebal_set and i > 0:
            eval_date = all_dates[i - 1]
            scores = _compute_composite_for_date(price_map, bench_df, eval_date, sym_to_group)
            # 策略选股 → 再过滤"当日可成交"(开盘价>0)
            picked = selector(scores, top_n, price_map=price_map, eval_date=eval_date, pe_map=pe_map)
            target_syms: list[str] = []
            for sym in picked:
                df = price_map.get(sym)
                if df is not None and d in df.index and df.loc[d, 'open'] > 0:
                    target_syms.append(sym)
                if len(target_syms) >= top_n:
                    break

            # 卖出不在 target 的(开盘价)+ 算单笔盈亏
            for sym in list(positions.keys()):
                if sym not in target_syms:
                    df = price_map.get(sym)
                    if df is None or d not in df.index:
                        continue   # 停牌:留待之后再卖,避免亏空
                    sell_price = float(df.loc[d, 'open'])
                    pos = positions.pop(sym)
                    qty = pos['qty']
                    entry_price = pos['entry_price']
                    proceeds = qty * sell_price
                    cash += proceeds
                    profit = (sell_price - entry_price) * qty
                    profit_pct = (sell_price - entry_price) / entry_price if entry_price > 0 else 0
                    hold_days = (d - pos['entry_date']).days
                    trades.append({
                        'date': d.strftime('%Y-%m-%d'), 'action': 'SELL',
                        'symbol': sym, 'qty': qty, 'price': sell_price, 'amount': proceeds,
                        'entry_price': round(entry_price, 2),
                        'profit': round(profit, 2),
                        'profit_pct': round(profit_pct, 4),
                        'hold_days': hold_days,
                    })

            # 买入新进 target 的(总资产 / top_n 等分预算)
            new_buys = [s for s in target_syms if s not in positions]
            if new_buys:
                held_value = sum(
                    positions[s]['qty'] * float(price_map[s].loc[d, 'open'])
                    for s in positions if d in price_map[s].index
                )
                total_assets = cash + held_value
                target_per_pos = total_assets / top_n
                for sym in new_buys:
                    df = price_map.get(sym)
                    if df is None or d not in df.index:
                        continue
                    buy_price = float(df.loc[d, 'open'])
                    if buy_price <= 0:
                        continue
                    # A 股按手交易,1 手 = 100 股;预算不够 1 手则 qty=0 自然跳过
                    qty = int(min(target_per_pos, cash) / buy_price) // 100 * 100
                    if qty <= 0:
                        continue
                    cost = qty * buy_price
                    cash -= cost
                    positions[sym] = {'qty': qty, 'entry_price': buy_price, 'entry_date': d}
                    trades.append({
                        'date': d.strftime('%Y-%m-%d'), 'action': 'BUY',
                        'symbol': sym, 'qty': qty, 'price': buy_price, 'amount': cost,
                    })

        # 当日净值(收盘价)
        port_value = cash
        for sym, pos in positions.items():
            df = price_map.get(sym)
            if df is not None and d in df.index:
                port_value += pos['qty'] * float(df.loc[d, 'close'])
        bench_value = initial_cash * float(bench_df.loc[d, 'close']) / bench_initial
        equity_curve.append({
            'date': d.strftime('%Y-%m-%d'),
            'portfolio': round(port_value, 2),
            'benchmark': round(bench_value, 2),
        })

    # 指标
    port_series = pd.Series([row['portfolio'] for row in equity_curve])
    bench_series = pd.Series([row['benchmark'] for row in equity_curve])
    returns = port_series.pct_change().dropna()

    total_return = port_series.iloc[-1] / initial_cash - 1
    bench_return = bench_series.iloc[-1] / initial_cash - 1
    days_held = max((all_dates[-1] - all_dates[0]).days, 1)
    annualized = (1 + total_return) ** (365 / days_held) - 1
    sharpe = ((returns.mean() - 0.03 / 252) / returns.std() * np.sqrt(252)) if returns.std() > 0 else 0.0
    drawdown = (port_series - port_series.cummax()) / port_series.cummax()
    max_dd = float(drawdown.min())
    avg_assets = float(port_series.mean())
    total_buys = sum(t['amount'] for t in trades if t['action'] == 'BUY')
    turnover_annual = (total_buys / avg_assets * (252 / max(days_held, 1))) if avg_assets > 0 else 0.0

    # 名称回填(交易明细前端展示用)
    names = _au.get_astock_names(list({t['symbol'] for t in trades}))
    for t in trades:
        t['name'] = names.get(t['symbol'], t['symbol'])

    # 已实现盈亏统计(SELL 笔)
    sells = [t for t in trades if t['action'] == 'SELL']
    realized_pnl = sum(t.get('profit', 0) for t in sells)
    n_wins = sum(1 for t in sells if t.get('profit', 0) > 0)
    n_losses = sum(1 for t in sells if t.get('profit', 0) < 0)
    win_rate = n_wins / len(sells) if sells else 0.0
    avg_win = (sum(t['profit'] for t in sells if t.get('profit', 0) > 0) / max(n_wins, 1)) if n_wins else 0
    avg_loss = (sum(t['profit'] for t in sells if t.get('profit', 0) < 0) / max(n_losses, 1)) if n_losses else 0

    return _clean_floats({
        'start_date': start_date, 'end_date': end_date,
        'initial_cash': float(initial_cash), 'top_n': top_n,
        'strategy': strategy,
        'universe_size': len(all_syms), 'groups_used': sorted(themes_cfg.keys()),
        'final_value': float(port_series.iloc[-1]),
        'total_return': float(total_return),
        'annualized_return': float(annualized),
        'benchmark_return': float(bench_return),
        'excess_return': float(total_return - bench_return),
        'sharpe': float(sharpe),
        'max_drawdown': max_dd,
        'turnover_annual': float(turnover_annual),
        'n_rebalances': len(rebalance_dates),
        'n_trades': len(trades),
        'realized_pnl': float(realized_pnl),
        'win_rate': float(win_rate),
        'n_wins': n_wins,
        'n_losses': n_losses,
        'avg_win': float(avg_win),
        'avg_loss': float(avg_loss),
        'equity_curve': equity_curve,
        'trades': trades,
    })


# ── 异步任务管理 ────────────────────────────────────────────

def submit_backtest(params: dict) -> str:
    """提交后台回测,立即返回 task_id。"""
    import uuid
    task_id = uuid.uuid4().hex[:12]
    with _tasks_lock:
        _tasks[task_id] = {'status': 'running', 'created': datetime.now().isoformat(timespec='seconds')}

    def _run():
        try:
            result = run_backtest(**params)
            with _tasks_lock:
                _tasks[task_id] = {**_tasks[task_id], 'status': 'completed', 'result': result}
        except Exception as e:
            _logger.error(f'[AStockBacktest/{task_id}] 失败: {e}', exc_info=True)
            with _tasks_lock:
                _tasks[task_id] = {**_tasks[task_id], 'status': 'failed', 'error': str(e)}

    threading.Thread(target=_run, daemon=True).start()
    return task_id


def get_task(task_id: str) -> dict | None:
    with _tasks_lock:
        return _tasks.get(task_id)
