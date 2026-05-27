"""EMA21 网格补仓策略（单股）。

逻辑：
  1. 底仓入场：首次出现 RSMomentum 买入信号（signal==1）
     - 仓位：initial_cash × base_pct
  2. 补仓：close 触及 EMA21 ±touch_tol，且 EMA21 > EMA50（趋势确认）
     - 单次补仓 = 底仓股数 × add_size_mult
     - 总补仓次数上限 max_adds
  3. 卖出补仓：close - EMA21 > sell_atr_mult × ATR14
     - FIFO 卖掉最早的一个补仓批次
  4. 整体止损：close < EMA{stop_ema_period} → 全部清仓（含底仓）
  5. 成交：T 日触发，T+1 开盘价成交（与 RSMomentum 回测一致，无前瞻偏差）

返回：summary / equity_curve / trades / benchmarks
"""
from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

_logger = logging.getLogger(__name__)


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity / peak - 1.0).min()
    return float(dd) if pd.notna(dd) else 0.0


def _sharpe(returns: pd.Series, freq: int = 252) -> float:
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(np.sqrt(freq) * returns.mean() / returns.std())


def _compute_indicators(df: pd.DataFrame, ema_fast: int, ema_slow: int, atr_period: int) -> pd.DataFrame:
    df = df.copy()
    df[f'ema{ema_fast}'] = _ema(df['close'], ema_fast)
    df[f'ema{ema_slow}'] = _ema(df['close'], ema_slow)
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low']  - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df[f'atr{atr_period}'] = tr.rolling(atr_period).mean()
    return df


def _rs_momentum_signals(df: pd.DataFrame, spy_close: pd.Series) -> pd.Series:
    """复用 RSMomentum 的信号生成，仅返回 signal 列。"""
    from strategies.rs_momentum import RSMomentum
    strat = RSMomentum()
    strat.set_spy(spy_close)
    sig_df = strat.generate_signals(df[['open', 'high', 'low', 'close', 'volume']])
    return sig_df['signal'].reindex(df.index).fillna(0).astype(int)


# ── 单股回测主函数 ──────────────────────────────────────────

def run_ema_pullback_backtest(
    symbol: str,
    start: str,
    end: str,
    initial_cash: float = 60_000.0,
    base_pct: float = 0.50,
    add_size_mult: float = 0.50,
    max_adds: int = 2,
    touch_tol: float = 0.01,
    sell_atr_mult: float = 2.5,
    stop_ema_period: int = 50,
    ema_fast: int = 21,
    commission_per_share: float = 0.005,
    entry_mode: str = 'rs_momentum',  # 'rs_momentum'（严苛）/ 'ema_relaxed'（宽松：close>EMA21 且 EMA21>EMA50）
    allow_margin: bool = False,
    max_leverage: float = 1.0,
    margin_rate: float = 0.06,
) -> dict:
    """单股 EMA21 补仓回测，同时跑 RSMomentum 纯策略对照 + Buy&Hold + SPY。

    entry_mode:
      - 'rs_momentum': 沿用 RSMomentum 5 条件（RS+突破+放量+不崩+趋势），单股一年通常只触发 1-3 次
      - 'ema_relaxed': close > EMA21 且 EMA21 > EMA50 即可建底仓，信号频次高 5-10 倍，
                       适合在已知是上升趋势股上做"踩 EMA21 网格"
    """
    from core.data_store import DataStore

    store = DataStore()

    # 多拉 90 天预热（计算 EMA50/ATR14/RS63）
    pre_start = (pd.to_datetime(start) - pd.Timedelta(days=180)).strftime('%Y-%m-%d')

    px_map = store.get([symbol, 'SPY'], start=pre_start, end=end, auto_update=True)
    if symbol not in px_map or px_map[symbol].empty:
        raise ValueError(f'无法获取 {symbol} 历史数据')
    if 'SPY' not in px_map or px_map['SPY'].empty:
        raise ValueError('无法获取 SPY 数据')

    df = px_map[symbol].copy()
    spy_close = px_map['SPY']['close']

    df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close', 'volume']].dropna()

    # 信号 + 指标（在完整窗口上计算，截断要等会儿做）
    df = _compute_indicators(df, ema_fast=ema_fast, ema_slow=stop_ema_period, atr_period=14)
    if entry_mode == 'ema_relaxed':
        # 宽松入场：close > EMA21 且 EMA21 > EMA50 → 进场（只在"未持仓 → 持仓"边沿触发一次）
        cond = (df['close'] > df[f'ema{ema_fast}']) & (df[f'ema{ema_fast}'] > df[f'ema{stop_ema_period}'])
        # 边沿检测：上一日不满足、当日满足
        edge = cond & ~cond.shift(1, fill_value=False)
        df['signal'] = 0
        df.loc[edge, 'signal'] = 1
    else:
        df['signal'] = _rs_momentum_signals(df, spy_close)

    # 截断到用户指定的回测区间
    bt = df[(df.index >= pd.to_datetime(start)) & (df.index <= pd.to_datetime(end))].copy()
    if bt.empty:
        raise ValueError(f'{symbol} 在 {start} ~ {end} 区间无数据')

    ema_fast_col = f'ema{ema_fast}'
    ema_slow_col = f'ema{stop_ema_period}'
    atr_col      = 'atr14'

    # ── 主回测：EMA Pullback ───────────────────────────────
    ema_state = _simulate_ema_pullback(
        bt, initial_cash, base_pct, add_size_mult, max_adds,
        touch_tol, sell_atr_mult,
        ema_fast_col, ema_slow_col, atr_col, commission_per_share,
        allow_margin=allow_margin, max_leverage=max_leverage, margin_rate=margin_rate,
    )
    # ── 对照：RSMomentum 纯策略（信号买入，跌破 EMA50 卖出，无补仓）─
    rs_state = _simulate_rs_only(
        bt, initial_cash, base_pct, ema_slow_col, commission_per_share,
    )
    # ── 对照：Buy & Hold 满仓（100% 持有，上限基准）────────
    bh_state = _simulate_buy_hold(bt, initial_cash, commission_per_share, budget_pct=1.0)
    # ── 对照：Buy & Hold 同底仓（用 base_pct × cash，与策略底仓暴露一致 → 公平比较）─
    bh_base_state = _simulate_buy_hold(bt, initial_cash, commission_per_share, budget_pct=base_pct)
    # ── 对照：SPY Buy & Hold ───────────────────────────────
    spy_slice = spy_close.loc[bt.index[0]:bt.index[-1]]
    spy_norm  = (spy_slice / spy_slice.iloc[0]) * initial_cash

    # ── 汇总 ──────────────────────────────────────────────
    dates = [d.strftime('%Y-%m-%d') for d in bt.index]
    equity_curve = []
    for i, d in enumerate(dates):
        equity_curve.append({
            'date':           d,
            'ema_equity':     float(ema_state['equity_series'].iloc[i]),
            'rs_equity':      float(rs_state['equity_series'].iloc[i]),
            'bh_equity':      float(bh_state['equity_series'].iloc[i]),
            'bh_base_equity': float(bh_base_state['equity_series'].iloc[i]),
            'spy_equity':     float(spy_norm.iloc[i]) if i < len(spy_norm) else None,
            'close':          float(bt['close'].iloc[i]),
            'ema_fast':       float(bt[ema_fast_col].iloc[i]) if pd.notna(bt[ema_fast_col].iloc[i]) else None,
            'ema_slow':       float(bt[ema_slow_col].iloc[i]) if pd.notna(bt[ema_slow_col].iloc[i]) else None,
        })

    def _summary(state: dict, label: str) -> dict:
        eq = state['equity_series']
        ret = eq.iloc[-1] / initial_cash - 1.0
        daily_ret = eq.pct_change().dropna()
        wins   = [t for t in state['trades'] if t.get('pnl') is not None and t['pnl'] >  0]
        losses = [t for t in state['trades'] if t.get('pnl') is not None and t['pnl'] <= 0]
        avg_win  = float(np.mean([t['pnl'] for t in wins]))   if wins   else 0.0
        avg_loss = float(np.mean([t['pnl'] for t in losses])) if losses else 0.0
        win_rate = len(wins) / (len(wins) + len(losses)) if (wins or losses) else 0.0
        years = max((bt.index[-1] - bt.index[0]).days / 365.25, 0.01)
        cagr = (eq.iloc[-1] / initial_cash) ** (1.0 / years) - 1.0
        return {
            'label':          label,
            'total_return':   float(ret),
            'cagr':           float(cagr),
            'sharpe':         _sharpe(daily_ret),
            'max_drawdown':   _max_drawdown(eq),
            'num_trades':     len([t for t in state['trades'] if t.get('action') == 'sell']),
            'win_rate':       float(win_rate),
            'avg_win':        avg_win,
            'avg_loss':       avg_loss,
            'profit_factor':  float(abs(avg_win / avg_loss)) if avg_loss else 0.0,
            'final_equity':   float(eq.iloc[-1]),
        }

    def _passive_summary(state: dict, label: str) -> dict:
        eq = state['equity_series']
        return {
            'label':         label,
            'total_return':  float(eq.iloc[-1] / initial_cash - 1.0),
            'cagr':          None,
            'sharpe':        _sharpe(eq.pct_change().dropna()),
            'max_drawdown':  _max_drawdown(eq),
            'num_trades':    1,
            'win_rate':      None,
            'avg_win':       None,
            'avg_loss':      None,
            'profit_factor': None,
            'final_equity':  float(eq.iloc[-1]),
        }

    ema_summary = _summary(ema_state, 'EMA21 补仓')
    ema_summary['interest_paid'] = float(ema_state.get('interest_paid', 0.0))
    summaries = {
        'ema_pullback': ema_summary,
        'rs_only':      _summary(rs_state, 'RSMomentum 纯策略'),
        'buy_hold':     _passive_summary(bh_state,      'B&H 满仓 (100%)'),
        'buy_hold_base': _passive_summary(bh_base_state, f'B&H 同底仓 ({int(base_pct*100)}%)'),
        'spy':          _passive_summary({'equity_series': spy_norm}, 'SPY Buy & Hold'),
    }

    total_trading_days = len(bt)
    bt_start_str = bt.index[0].strftime('%Y-%m-%d')
    bt_end_str   = bt.index[-1].strftime('%Y-%m-%d')
    raw_signal_triggers = int((bt['signal'] == 1).sum())
    signal_stats = {
        'total_trading_days':  total_trading_days,
        'entry_mode':          entry_mode,
        'signal_label':        'RSMomentum 5条件' if entry_mode == 'rs_momentum' else 'close>EMA21 且 EMA21>EMA50',
        'rs_signal_triggers':  raw_signal_triggers,
        'ema_pullback':        _calc_position_stats(ema_state['trades'], bt_start_str, bt_end_str),
        'rs_only':             _calc_position_stats(rs_state['trades'],  bt_start_str, bt_end_str),
    }

    return {
        'symbol':       symbol,
        'start':        bt.index[0].strftime('%Y-%m-%d'),
        'end':          bt.index[-1].strftime('%Y-%m-%d'),
        'initial_cash': initial_cash,
        'params': {
            'base_pct':       base_pct,
            'add_size_mult':  add_size_mult,
            'max_adds':       max_adds,
            'touch_tol':      touch_tol,
            'sell_atr_mult':  sell_atr_mult,
            'stop_ema_period': stop_ema_period,
            'ema_fast':       ema_fast,
            'entry_mode':     entry_mode,
            'allow_margin':   allow_margin,
            'max_leverage':   max_leverage,
            'margin_rate':    margin_rate,
        },
        'summaries':     summaries,
        'signal_stats':  signal_stats,
        'equity_curve':  equity_curve,
        'ema_trades':    ema_state['trades'],
        'rs_trades':     rs_state['trades'],
    }


def _calc_position_stats(trades: list[dict], bt_start: str, bt_end: str) -> dict:
    """从交易列表中算出建仓次数、平均持仓天数、空仓占比（基于日历日）。

    持仓段定义：第一次 base 买入 → 紧接着的 base/base_stop 卖出（含中间补仓和补仓卖出）。
    未平仓段以回测结束日作为退出日参与统计。
    """
    from datetime import datetime as _dt
    entries: list[tuple[str, str]] = []   # 已平仓段
    open_entry: str | None = None
    for t in trades:
        action = t.get('action')
        kind   = t.get('kind') or ''
        if action == 'buy' and kind == 'base':
            if open_entry is None:
                open_entry = t['date']
        elif action == 'sell' and kind.startswith('base'):
            if open_entry is not None:
                entries.append((open_entry, t['date']))
                open_entry = None
    if open_entry is not None:
        entries.append((open_entry, bt_end))  # 未平仓段补到回测末日

    held_days_list = []
    for e_in, e_out in entries:
        d_in  = _dt.strptime(e_in,  '%Y-%m-%d')
        d_out = _dt.strptime(e_out, '%Y-%m-%d')
        held_days_list.append(max(0, (d_out - d_in).days))
    avg_holding = float(np.mean(held_days_list)) if held_days_list else 0.0

    bt_span_days = max(1, (_dt.strptime(bt_end, '%Y-%m-%d') - _dt.strptime(bt_start, '%Y-%m-%d')).days)
    held_total   = sum(held_days_list)
    flat_pct     = max(0.0, 1.0 - held_total / bt_span_days)

    return {
        'entries':          len(entries),
        'avg_holding_days': round(avg_holding, 1),
        'flat_days_pct':    round(flat_pct, 3),
    }


# ── 三个模拟器：逐日状态机 ──────────────────────────────────

def _simulate_ema_pullback(
    bt: pd.DataFrame, cash: float, base_pct: float,
    add_size_mult: float, max_adds: int,
    touch_tol: float, sell_atr_mult: float,
    ema_fast_col: str, ema_slow_col: str, atr_col: str,
    commission_per_share: float,
    allow_margin: bool = False,
    max_leverage: float = 1.0,
    margin_rate: float = 0.06,
) -> dict:
    """EMA21 补仓策略 — T 日触发 / T+1 开盘成交。

    allow_margin: 允许 cash 为负（融资）
    max_leverage: 总持仓市值上限 = initial_cash × max_leverage
    margin_rate:  年化融资利率，按 252 交易日折算每日计息
    """
    n = len(bt)
    init_cash = cash
    base_shares = 0       # 底仓股数
    batches: list[dict] = []  # 每个批次 {shares, entry_price, kind}, kind='base'|'add'
    adds_done = 0
    pending: list[dict] = []  # 次日开盘待执行的指令 [{action, kind, batch_idx?}]
    equity_series = []
    trades: list[dict] = []
    interest_paid = 0.0
    daily_rate = margin_rate / 252.0

    closes = bt['close'].values
    opens  = bt['open'].values

    for i in range(n):
        # 0. 每日开始先按昨日 cash 余额计融资利息（cash<0 时）
        if allow_margin and cash < 0:
            interest = -cash * daily_rate
            cash -= interest
            interest_paid += interest

        # 1. 先执行昨日下的次日订单（用今日 open 成交）
        for ord_ in pending:
            px = opens[i]
            if ord_['action'] == 'buy':
                shares = ord_['shares']
                cost   = shares * px + shares * commission_per_share
                if allow_margin:
                    # 杠杆模式：总持仓市值上限约束
                    current_value = sum(b['shares'] * px for b in batches)
                    max_value     = init_cash * max_leverage
                    room          = max_value - current_value
                    if shares * px > room:
                        shares = max(0, int(room / px))
                        cost   = shares * px + shares * commission_per_share
                else:
                    # 无杠杆：现金约束
                    if cost > cash:
                        shares = int((cash - shares * commission_per_share) / px)
                        cost   = shares * px + shares * commission_per_share
                if shares <= 0:
                    continue
                cash -= cost
                batches.append({'shares': shares, 'entry_price': px, 'kind': ord_['kind']})
                if ord_['kind'] == 'base':
                    base_shares = shares
                else:
                    adds_done += 1
                trades.append({
                    'date':   bt.index[i].strftime('%Y-%m-%d'),
                    'action': 'buy',
                    'kind':   ord_['kind'],
                    'price':  float(px),
                    'shares': shares,
                    'pnl':    None,
                })
            elif ord_['action'] == 'sell':
                batch_idx = ord_['batch_idx']
                if batch_idx >= len(batches):
                    continue
                b = batches[batch_idx]
                proceeds = b['shares'] * px - b['shares'] * commission_per_share
                pnl = (px - b['entry_price']) * b['shares'] - 2 * b['shares'] * commission_per_share
                cash += proceeds
                trades.append({
                    'date':   bt.index[i].strftime('%Y-%m-%d'),
                    'action': 'sell',
                    'kind':   b['kind'],
                    'price':  float(px),
                    'shares': b['shares'],
                    'pnl':    float(pnl),
                })
                batches[batch_idx] = None  # 标记，待会儿清掉
            elif ord_['action'] == 'sell_all':
                for b in batches:
                    if b is None:
                        continue
                    proceeds = b['shares'] * px - b['shares'] * commission_per_share
                    pnl = (px - b['entry_price']) * b['shares'] - 2 * b['shares'] * commission_per_share
                    cash += proceeds
                    trades.append({
                        'date':   bt.index[i].strftime('%Y-%m-%d'),
                        'action': 'sell',
                        'kind':   b['kind'] + '_stop',
                        'price':  float(px),
                        'shares': b['shares'],
                        'pnl':    float(pnl),
                    })
                batches = []
                base_shares = 0
                adds_done = 0
        batches = [b for b in batches if b is not None]
        pending = []

        # 2. 计算今日权益（按 close 估值）
        held_value = sum(b['shares'] * closes[i] for b in batches)
        equity_series.append(cash + held_value)

        # 3. 生成次日订单
        c   = closes[i]
        ef  = bt[ema_fast_col].iloc[i]
        es  = bt[ema_slow_col].iloc[i]
        atr = bt[atr_col].iloc[i]
        sig = int(bt['signal'].iloc[i])

        if pd.isna(ef) or pd.isna(es) or pd.isna(atr):
            continue

        # 已持仓
        if batches:
            # 止损优先
            if c < es:
                pending.append({'action': 'sell_all'})
                continue
            # 偏离过度 → 卖最早的补仓批次
            if (c - ef) > sell_atr_mult * atr:
                for idx, b in enumerate(batches):
                    if b['kind'] == 'add':
                        pending.append({'action': 'sell', 'batch_idx': idx})
                        break
                continue
            # 补仓触发：触及 EMA21 ±touch_tol，且 EMA21 > EMA50
            if (adds_done < max_adds and
                    abs(c - ef) / ef <= touch_tol and
                    ef > es):
                add_shares = int(base_shares * add_size_mult)
                if add_shares > 0:
                    pending.append({'action': 'buy', 'kind': 'add', 'shares': add_shares})
        else:
            # 空仓：等首信号建底仓
            if sig == 1 and adds_done == 0:
                budget = init_cash * base_pct
                shares = int(budget / c)
                if shares > 0:
                    pending.append({'action': 'buy', 'kind': 'base', 'shares': shares})

    return {
        'equity_series': pd.Series(equity_series, index=bt.index),
        'trades':        trades,
        'interest_paid': float(interest_paid),
    }


def _simulate_rs_only(
    bt: pd.DataFrame, cash: float, base_pct: float,
    ema_slow_col: str, commission_per_share: float,
) -> dict:
    """RSMomentum 纯策略：首信号建仓，跌破 EMA50 全平。仓位 = base_pct（与 EMA 策略底仓相同）。"""
    n = len(bt)
    init_cash = cash
    shares = 0
    entry_price = 0.0
    pending: list[dict] = []
    equity_series = []
    trades: list[dict] = []

    closes = bt['close'].values
    opens  = bt['open'].values

    for i in range(n):
        for ord_ in pending:
            px = opens[i]
            if ord_['action'] == 'buy':
                s = ord_['shares']
                cost = s * px + s * commission_per_share
                if cost > cash:
                    s = int((cash - s * commission_per_share) / px)
                    cost = s * px + s * commission_per_share
                if s <= 0:
                    continue
                cash -= cost
                shares = s
                entry_price = px
                trades.append({
                    'date': bt.index[i].strftime('%Y-%m-%d'),
                    'action': 'buy', 'kind': 'base',
                    'price': float(px), 'shares': s, 'pnl': None,
                })
            elif ord_['action'] == 'sell':
                proceeds = shares * px - shares * commission_per_share
                pnl = (px - entry_price) * shares - 2 * shares * commission_per_share
                cash += proceeds
                trades.append({
                    'date': bt.index[i].strftime('%Y-%m-%d'),
                    'action': 'sell', 'kind': 'base_stop',
                    'price': float(px), 'shares': shares, 'pnl': float(pnl),
                })
                shares = 0
                entry_price = 0.0
        pending = []

        equity_series.append(cash + shares * closes[i])

        c  = closes[i]
        es = bt[ema_slow_col].iloc[i]
        sig = int(bt['signal'].iloc[i])
        if pd.isna(es):
            continue

        if shares > 0:
            if c < es:
                pending.append({'action': 'sell'})
        else:
            if sig == 1:
                budget = init_cash * base_pct
                s = int(budget / c)
                if s > 0:
                    pending.append({'action': 'buy', 'shares': s})

    return {
        'equity_series': pd.Series(equity_series, index=bt.index),
        'trades':        trades,
    }


def _simulate_buy_hold(bt: pd.DataFrame, cash: float, commission_per_share: float,
                       budget_pct: float = 1.0) -> dict:
    """从第一天开盘买入持有到最后。

    budget_pct=1.0 → 满仓 B&H（无脑持有上限）
    budget_pct<1.0 → 仅用 budget_pct × cash 建仓，剩下闲置 — 用于和策略同底仓暴露做公平比较
    """
    budget = cash * budget_pct
    px0 = bt['open'].iloc[0]
    shares = int((budget - commission_per_share * 1) / px0)
    if shares <= 0:
        equity_series = pd.Series([cash] * len(bt), index=bt.index)
        return {'equity_series': equity_series, 'trades': []}
    spent  = shares * px0 + shares * commission_per_share
    rest   = cash - spent
    equity_series = rest + shares * bt['close']
    return {
        'equity_series': equity_series,
        'trades': [{
            'date':   bt.index[0].strftime('%Y-%m-%d'),
            'action': 'buy', 'kind': 'base',
            'price':  float(px0), 'shares': shares, 'pnl': None,
        }],
    }
