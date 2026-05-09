"""
5日动量策略回测（AI产业链专属）

用法：
  python -m tests.backtest_momentum5d --start 2024-01-01 --end 2024-12-31
  python -m tests.backtest_momentum5d --start 2023-01-01
  python -m tests.backtest_momentum5d --period 6mo
  python -m tests.backtest_momentum5d --start 2025-01-01 --max-pos 3 --pos-pct 0.25 --daily

策略规格：
  - 股票池  : data/ai_universe.json，剔除软件行业
  - 入场    : 5日RS > 0，按 RS 降序排名，最多 MAX_POS 只
  - 出场    : 5日RS ≤ 0（动量消失）→ 次日 OPG 卖
  - 硬止损  : 入场价 × (1 + HARD_STOP)，默认 -8%
  - 执行    : T+1 OPG（与旧策略一致，含 1% 滑点保护）
  - 手续费  : IBKR 阶梯模型
"""
import argparse
import re
import warnings
from datetime import timedelta

import numpy as np
import pandas as pd

from core.data_store import DataStore
from core.universe import get_ai_tickers, get_stock_info
from strategies.momentum5d import Momentum5D

warnings.filterwarnings('ignore')

# ── 默认参数 ──────────────────────────────────────────────────
MAX_POSITIONS = 4
POSITION_PCT  = 0.22    # 每仓 22%
HARD_STOP     = -0.08   # 硬止损 -8%
INITIAL_CASH  = 60_000
MAX_ENTRY_SLIPPAGE = 0.01   # 开盘跳价超过昨收 1% 则放弃


# ── IBKR 手续费（与 backtest_rs.py 相同）─────────────────────
def calc_commission(shares: int, price: float, is_sell: bool = False) -> float:
    trade_value = shares * price
    commission  = max(0.35, shares * 0.0035)
    commission  = min(commission, trade_value * 0.01)
    if is_sell:
        commission += trade_value * 0.0000278
        commission += min(shares * 0.000166, 8.30)
    return round(commission, 4)


# ── 行业名标准化（兼容 em dash / 连字符 / 空格差异）────────────
def _norm_ind(s: str) -> str:
    s = re.sub(r'[—–‒\-_/]', ' ', s.lower())
    return re.sub(r'\s+', ' ', s).strip()


# ── 加载 AI 股票池（剔除软件行业）────────────────────────────
def load_universe(deny_software: bool = True) -> list[str]:
    symbols = get_ai_tickers()
    if not deny_software:
        return symbols
    info_map = get_stock_info(symbols)
    filtered = []
    removed  = []
    for s in symbols:
        ind = _norm_ind(info_map.get(s, {}).get('industry') or '')
        # 只排除纯软件应用（application），保留基础设施类（infrastructure/networking等）
        if 'software application' in ind or 'application software' in ind:
            removed.append(s)
        else:
            filtered.append(s)
    if removed:
        print(f"  [软件过滤] 移除 {len(removed)} 只(纯应用软件): {sorted(removed)}")
    return filtered


# ── 主回测函数 ────────────────────────────────────────────────
def run_backtest(
    period: str = '3mo',
    start: str  = None,
    end: str    = None,
    max_pos: int = MAX_POSITIONS,
    pos_pct: float = POSITION_PCT,
    hard_stop: float = HARD_STOP,
    daily: bool = False,
    deny_software: bool = True,
) -> dict:

    # ── 日期范围 ──────────────────────────────────────────────
    if start:
        bt_start = pd.Timestamp(start)
        bt_end   = pd.Timestamp(end) if end else pd.Timestamp.today()
    else:
        period_days = {'1mo': 21, '2mo': 42, '3mo': 63, '6mo': 126, '1y': 252, '2y': 504}
        bt_days  = period_days.get(period, 63)
        bt_end   = pd.Timestamp.today()
        bt_start = bt_end - timedelta(days=int(bt_days * 1.5))

    # 多加载 60 天数据供 5日RS 和20日均量预热
    dl_start = (bt_start - timedelta(days=60)).strftime('%Y-%m-%d')
    dl_end   = bt_end.strftime('%Y-%m-%d')

    # ── 加载股票池 + 数据 ──────────────────────────────────────
    print("加载 AI 股票池...")
    universe = load_universe(deny_software=deny_software)
    print(f"  有效标的: {len(universe)} 只")

    all_syms = list(set(universe + ['SPY']))
    store    = DataStore()
    print(f"加载价格数据（{dl_start} → {dl_end}）...")
    all_data = store.get(all_syms, start=dl_start, end=dl_end, min_rows=10)

    spy_df = all_data.get('SPY')
    if spy_df is None:
        raise RuntimeError("SPY 数据获取失败")

    # 统一所有 DataFrame 的 DatetimeIndex 单位为 'us'，避免 pandas 2.x 报错
    def _norm(df: pd.DataFrame) -> pd.DataFrame:
        if hasattr(df.index, 'as_unit'):
            df = df.copy()
            df.index = df.index.as_unit('us')
        return df

    spy_df   = _norm(spy_df)
    spy_close = spy_df['close']
    all_dates = spy_df.index

    # 确定回测起点索引（至少留 25 个交易日预热）
    bt_start_idx = int(all_dates.searchsorted(bt_start))
    bt_start_idx = max(25, bt_start_idx)
    dates = all_dates

    # ── 生成信号 ──────────────────────────────────────────────
    strategy = Momentum5D()
    strategy.set_spy(spy_close)

    stock_data = {s: _norm(df) for s, df in all_data.items() if s != 'SPY'}
    signals: dict[str, pd.DataFrame] = {}
    for sym, df in stock_data.items():
        if sym not in universe:
            continue
        try:
            signals[sym] = strategy.generate_signals(df)
        except Exception:
            pass
    print(f"  信号生成完成: {len(signals)} 只")

    # ── 逐日模拟 ──────────────────────────────────────────────
    cash          = float(INITIAL_CASH)
    positions: dict = {}
    trades: list    = []
    equity_history: list = []
    daily_holdings: list = []
    total_commission = 0.0
    pending_sells: dict  = {}
    pending_buys: list   = []

    for i, date in enumerate(dates):
        if i < bt_start_idx:
            equity_history.append({'date': str(date.date()),
                                   'equity': cash, 'spy_equity': float(INITIAL_CASH)})
            continue

        # ── T+1 卖单执行 ─────────────────────────────────────
        for sym, reason in list(pending_sells.items()):
            if sym not in positions:
                continue
            if sym not in stock_data or date not in stock_data[sym].index:
                continue
            pos   = positions.pop(sym)
            price = float(stock_data[sym].loc[date, 'open'])
            ret   = (price - pos['entry_price']) / pos['entry_price']
            comm  = calc_commission(pos['qty'], price, is_sell=True)
            cash += pos['qty'] * price - comm
            total_commission += comm
            trades.append({
                'symbol':      sym,
                'entry_date':  str(pos['entry_date'].date()),
                'exit_date':   str(date.date()),
                'entry_price': round(pos['entry_price'], 4),
                'exit_price':  round(price, 4),
                'qty':         pos['qty'],
                'pnl':         round(pos['qty'] * price - comm
                                     - pos['qty'] * pos['entry_price'] - pos['commission'], 2),
                'return':      round(ret, 6),
                'days_held':   (date - pos['entry_date']).days,
                'exit_reason': reason,
                'commission':  round(comm, 4),
            })
        pending_sells.clear()

        # ── T+1 买单执行 ─────────────────────────────────────
        if pending_buys:
            slots = max_pos - len(positions)
            net_liq = cash + sum(
                p['qty'] * (float(stock_data[s].loc[date, 'open'])
                            if s in stock_data and date in stock_data[s].index
                            else p['entry_price'])
                for s, p in positions.items()
            )
            min_cash = net_liq * max(0.0, 1.0 - max_pos * pos_pct)
            executed = 0
            for sym in pending_buys:
                if executed >= slots:
                    break
                if sym in positions or sym not in stock_data or date not in stock_data[sym].index:
                    continue
                price = float(stock_data[sym].loc[date, 'open'])
                # OPG 滑点保护：开盘跳价超 1% 放弃
                sym_df  = stock_data[sym]
                sym_idx = sym_df.index.get_loc(date)
                if sym_idx > 0:
                    prev_close = float(sym_df.iloc[sym_idx - 1]['close'])
                    if price > prev_close * (1 + MAX_ENTRY_SLIPPAGE):
                        continue
                qty  = int(net_liq * pos_pct / price)
                comm = calc_commission(qty, price)
                cost = qty * price + comm
                if qty <= 0 or cash - cost < min_cash:
                    continue
                cash -= cost
                total_commission += comm
                positions[sym] = {
                    'qty': qty, 'entry_price': price, 'entry_date': date,
                    'commission': comm, 'peak_price': price,
                }
                executed += 1
        pending_buys = []

        # ── T日出场检查 ────────────────────────────────────────
        for sym in list(positions.keys()):
            if sym not in stock_data or date not in stock_data[sym].index:
                continue
            pos   = positions[sym]
            price = float(stock_data[sym].loc[date, 'close'])
            ret   = (price - pos['entry_price']) / pos['entry_price']

            if ret <= hard_stop:
                pending_sells[sym] = f'硬止损({hard_stop:.0%})'
            elif sym in signals and date in signals[sym].index:
                if signals[sym].loc[date, 'signal'] == -1:
                    pending_sells[sym] = 'RS转负'

        # ── T日买入候选 ────────────────────────────────────────
        free_slots = max_pos - len(positions) + len(pending_sells)
        if free_slots > 0:
            candidates = []
            for sym, sig_df in signals.items():
                if sym in positions or sym in pending_sells:
                    continue
                if date not in sig_df.index:
                    continue
                row = sig_df.loc[date]
                if row['signal'] == 1:
                    candidates.append((sym, float(row['rs_5d'])))
            candidates.sort(key=lambda x: -x[1])
            pending_buys = [sym for sym, _ in candidates[:free_slots]]

        # ── 当日净值 ───────────────────────────────────────────
        port_value = cash
        for sym, pos in positions.items():
            if date in stock_data.get(sym, pd.DataFrame()).index:
                port_value += pos['qty'] * float(stock_data[sym].loc[date, 'close'])

        spy_start_price = float(spy_close.iloc[bt_start_idx])
        spy_equity = INITIAL_CASH * float(spy_close.get(date, spy_start_price)) / spy_start_price

        equity_history.append({
            'date':       str(date.date()),
            'equity':     round(port_value, 2),
            'spy_equity': round(spy_equity, 2),
        })

        holding_snap = []
        for sym, pos in positions.items():
            cur_p = (float(stock_data[sym].loc[date, 'close'])
                     if date in stock_data.get(sym, pd.DataFrame()).index
                     else pos['entry_price'])
            ret_p = (cur_p - pos['entry_price']) / pos['entry_price']
            holding_snap.append({
                'symbol':       sym,
                'qty':          pos['qty'],
                'entry_price':  round(pos['entry_price'], 4),
                'cur_price':    round(cur_p, 4),
                'return':       round(ret_p, 6),
                'market_value': round(pos['qty'] * cur_p, 2),
            })

        if daily:
            sig_str = ' | '.join(f"{h['symbol']}({h['return']*100:+.1f}%)"
                                  for h in holding_snap) or '空仓'
            print(f"  {date.date()}  净值=${port_value:,.0f}  持仓: {sig_str}")

        daily_holdings.append({
            'date':     str(date.date()),
            'equity':   round(port_value, 2),
            'cash':     round(cash, 2),
            'holdings': holding_snap,
        })

    # ── 未平仓持仓（与 backtest_rs 格式保持一致）────────────────
    last_date = dates[-1]
    open_positions = []
    for sym, pos in positions.items():
        cur_p = (float(stock_data[sym].loc[last_date, 'close'])
                 if last_date in stock_data.get(sym, pd.DataFrame()).index
                 else pos['entry_price'])
        open_positions.append({
            'symbol':      sym,
            'entry_date':  str(pos['entry_date'].date()),
            'entry_price': round(pos['entry_price'], 4),
            'cur_price':   round(cur_p, 4),
            'qty':         pos['qty'],
            'pnl':         round((cur_p - pos['entry_price']) * pos['qty'], 2),
            'return':      round((cur_p - pos['entry_price']) / pos['entry_price'], 6),
            'days_held':   (last_date - pos['entry_date']).days,
        })

    # ── 统计 ───────────────────────────────────────────────────
    equity_df = pd.DataFrame(equity_history).set_index('date')
    final     = float(equity_df['equity'].iloc[-1])
    total_ret = (final - INITIAL_CASH) / INITIAL_CASH
    days      = (dates[-1] - dates[bt_start_idx]).days
    ann_ret   = (1 + total_ret) ** (365 / max(days, 1)) - 1
    spy_ret   = (float(spy_close.iloc[-1]) / float(spy_close.iloc[bt_start_idx])) - 1
    excess    = total_ret - spy_ret

    eq_ret = equity_df['equity'].pct_change().dropna()
    sharpe = float(eq_ret.mean() / eq_ret.std() * np.sqrt(252)) if eq_ret.std() > 0 else 0.0
    roll_max = equity_df['equity'].cummax()
    max_dd   = float(((equity_df['equity'] - roll_max) / roll_max).min())

    wins      = [t for t in trades if t['pnl'] > 0]
    win_rate  = len(wins) / len(trades) if trades else 0.0
    avg_days  = (sum(t['days_held'] for t in trades) / len(trades)) if trades else 0.0
    months    = max(days / 30, 1)
    trades_pm = len(trades) / months

    summary = {
        'initial_cash':   INITIAL_CASH,
        'final_equity':   round(final, 2),
        'total_return':   round(total_ret, 6),
        'annual_return':  round(ann_ret, 6),
        'spy_return':     round(spy_ret, 6),
        'excess_return':  round(excess, 6),
        'max_drawdown':   round(max_dd, 6),
        'sharpe':         round(sharpe, 4),
        'total_trades':   len(trades),
        'win_rate':       round(win_rate, 4),
        'avg_hold_days':  round(avg_days, 1),
        'trades_per_month': round(trades_pm, 1),
        'total_commission': round(total_commission, 2),
        'bt_start':         str(dates[bt_start_idx].date()),
        'bt_end':           str(dates[-1].date()),
        'days':             days,
        'universe_size':    len(universe),
        'universe':         'ai（5日动量）',   # 供历史列表展示
        'strategy':         'momentum5d',
        # 与 backtest_rs summary 字段对齐（前端兼容）
        'spy_brake_days':   0,
        'vix_brake_days':   0,
        'breadth_cap_days': 0,
    }

    return {
        'params':          {'max_pos': max_pos, 'pos_pct': pos_pct, 'hard_stop': hard_stop},
        'summary':         summary,
        'equity_curve':    equity_history,
        'trades':          trades,
        'open_positions':  open_positions,
        'daily_holdings':  daily_holdings,
    }


# ── 结果打印 ───────────────────────────────────────────────────
def print_report(result: dict):
    s = result['summary']
    p = result['params']
    trades = result['trades']

    def pct(v): return f"{v*100:+.2f}%" if v is not None else '-'
    def bar(v, width=20):
        filled = int(abs(v * 100) / 5)
        filled = min(filled, width)
        return ('█' * filled).ljust(width)

    print()
    print("═" * 56)
    print("  5日动量策略回测报告  (AI产业链，剔除软件)")
    print("═" * 56)
    print(f"  回测区间  : {s['bt_start']} → {s['bt_end']}  ({s['days']}天)")
    print(f"  股票池    : AI产业链 {s['universe_size']} 只（剔软件后）")
    print(f"  参数      : 最多{p['max_pos']}仓 × {p['pos_pct']:.0%}  硬止损{p['hard_stop']:.0%}")
    print("─" * 56)
    print(f"  总收益    : {pct(s['total_return'])}  {bar(s['total_return'])}")
    print(f"  年化收益  : {pct(s['annual_return'])}")
    print(f"  SPY 同期  : {pct(s['spy_return'])}")
    print(f"  超额收益  : {pct(s['excess_return'])}")
    print(f"  Sharpe    : {s['sharpe']:.2f}")
    print(f"  最大回撤  : {pct(s['max_drawdown'])}")
    print("─" * 56)
    print(f"  总交易次  : {s['total_trades']} 笔  ({s['trades_per_month']:.1f}笔/月)")
    print(f"  胜率      : {s['win_rate']*100:.1f}%")
    print(f"  平均持仓  : {s['avg_hold_days']:.1f} 天")
    print(f"  总手续费  : ${s['total_commission']:,.2f}")
    print("─" * 56)

    if trades:
        # 出场原因统计
        reasons: dict[str, int] = {}
        for t in trades:
            reasons[t['exit_reason']] = reasons.get(t['exit_reason'], 0) + 1
        print("  出场原因:")
        for reason, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
            print(f"    {reason:15s} {cnt:3d} 笔  ({cnt/len(trades)*100:.0f}%)")
        print()

        # 最近 10 笔交易
        print("  最近10笔交易:")
        print(f"  {'日期':10s} {'股票':6s} {'持天':>4s} {'收益':>8s} {'原因'}")
        print("  " + "-" * 42)
        for t in trades[-10:]:
            print(f"  {t['exit_date']:10s} {t['symbol']:6s} "
                  f"{t['days_held']:4d}天 {t['return']*100:+7.1f}%  {t['exit_reason']}")

    print("═" * 56)


# ── CLI 入口 ───────────────────────────────────────────────────
def _parse_args():
    parser = argparse.ArgumentParser(description='5日动量策略回测（AI产业链）')
    parser.add_argument('--period',   default='3mo',
                        help='回测周期: 1mo/2mo/3mo/6mo/1y/2y（与 --start 二选一）')
    parser.add_argument('--start',    default=None, help='起始日期 YYYY-MM-DD')
    parser.add_argument('--end',      default=None, help='结束日期 YYYY-MM-DD')
    parser.add_argument('--max-pos',  type=int,   default=MAX_POSITIONS, dest='max_pos')
    parser.add_argument('--pos-pct',  type=float, default=POSITION_PCT,  dest='pos_pct',
                        help='每仓比例，默认 0.22')
    parser.add_argument('--hard-stop', type=float, default=HARD_STOP, dest='hard_stop',
                        help='硬止损比例，默认 -0.08')
    parser.add_argument('--daily',    action='store_true', help='打印每日持仓')
    parser.add_argument('--no-software-filter', action='store_true', dest='no_sw',
                        help='不过滤软件行业（默认过滤）')
    return parser.parse_args()


if __name__ == '__main__':
    args = _parse_args()
    result = run_backtest(
        period      = args.period,
        start       = args.start,
        end         = args.end,
        max_pos     = args.max_pos,
        pos_pct     = args.pos_pct,
        hard_stop   = args.hard_stop,
        daily       = args.daily,
        deny_software = not args.no_sw,
    )
    print_report(result)
