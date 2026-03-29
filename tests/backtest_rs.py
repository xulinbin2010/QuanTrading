"""
RS 动量策略回测 —— 模拟完整交易过程。

用法：
  python -m tests.backtest_rs --period 3mo          # 最近3个月
  python -m tests.backtest_rs --period 6mo --top 20 # 最近6个月，最多持20只
"""
import argparse
import warnings
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from strategies.rs_momentum import RSMomentum
from core.universe import get_tickers, get_stock_info
from core.data_store import DataStore
from core.fmt import lj, rj
import config

warnings.filterwarnings('ignore')

# 风控参数（统一定义在 config.py）
MAX_POSITIONS           = config.MAX_POSITIONS
POSITION_PCT            = config.POSITION_PCT
CASH_RESERVE_PCT        = config.CASH_RESERVE_PCT
STOP_LOSS_PCT           = config.STOP_LOSS_PCT
INITIAL_CASH            = config.INITIAL_CASH
MAX_PER_SECTOR          = config.MAX_PER_SECTOR
VOL_SHRINK_RATIO        = config.VOL_SHRINK_RATIO
TRAIL_STOP_ACTIVATE_PCT = config.TRAIL_STOP_ACTIVATE_PCT
TRAIL_STOP_PCT          = config.TRAIL_STOP_PCT
SPY_BRAKE_PERIOD        = config.SPY_BRAKE_PERIOD
SPY_BRAKE_PCT           = config.SPY_BRAKE_PCT


# ── IBKR 阶梯手续费模型 ───────────────────────────────────────
def calc_commission(shares: int, price: float, is_sell: bool = False) -> float:
    """
    IBKR 阶梯定价（Tiered，美股）：
      - 每股 $0.0035，最低 $0.35 / 笔，上限 1% 成交额
    卖出额外收费：
      - SEC 交易费：成交额 × $0.0000278（证监会，卖方）
      - FINRA TAF：每股 $0.000166，上限 $8.30 / 笔
    """
    trade_value = shares * price
    commission  = max(0.35, shares * 0.0035)
    commission  = min(commission, trade_value * 0.01)   # 上限 1%
    if is_sell:
        commission += trade_value * 0.0000278            # SEC fee
        commission += min(shares * 0.000166, 8.30)      # FINRA TAF
    return round(commission, 4)


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('--period',   default='3mo', help='回测周期: 1mo/3mo/6mo/1y')
    parser.add_argument('--top',      type=int, default=10)
    parser.add_argument('--start',    default=None, help='回测起始日期 YYYY-MM-DD')
    parser.add_argument('--end',      default=None, help='回测结束日期 YYYY-MM-DD')
    parser.add_argument('--universe', default='sp500', help='股票池: sp500 / nasdaq100 / russell2000')
    parser.add_argument('--daily',    action='store_true', help='打印每日持仓明细（默认关闭）')
    parser.add_argument('--min-cap',  type=float, default=config.MIN_CAP_B,
                        dest='min_cap_b', help=f'最小市值（十亿USD），默认 {config.MIN_CAP_B}B')
    parser.add_argument('--max-cap',  type=float, default=config.MAX_CAP_B,
                        dest='max_cap_b', help=f'最大市值（十亿USD），默认 {config.MAX_CAP_B}B')
    parser.add_argument('--deny-industry', nargs='+', default=config.DENY_INDUSTRIES,
                        dest='deny_industries', help='拒绝行业关键词（模糊匹配）')
    return parser.parse_args()


def run_backtest(
    period: str = '3mo',
    top: int = 10,
    start: str = None,
    end: str = None,
    universe: str = 'sp500',
    daily: bool = False,
    min_cap_b: float = None,
    max_cap_b: float = None,
    deny_industries: list = None,
    factors: list = None,
    factor_params: dict = None,
) -> dict:
    """
    纯计算函数，返回回测结果 dict（不打印任何内容）。

    返回格式：
    {
        'params': {...},
        'summary': {...},
        'equity_curve': [{'date', 'equity', 'spy_equity'}, ...],
        'trades': [{'symbol', 'entry_date', 'exit_date', ...}, ...],
        'open_positions': [...],
        'daily_holdings': [...],
    }
    """
    if min_cap_b is None:
        min_cap_b = config.MIN_CAP_B
    if max_cap_b is None:
        max_cap_b = config.MAX_CAP_B
    if deny_industries is None:
        deny_industries = config.DENY_INDUSTRIES

    # ── 确定日期范围 ──────────────────────────────────────
    tickers = get_tickers(universe)

    if start:
        bt_start = pd.Timestamp(start)
        bt_end   = pd.Timestamp(end) if end else pd.Timestamp.today()
    else:
        period_days = {'1mo': 21, '3mo': 63, '6mo': 126, '1y': 252}
        bt_days  = period_days.get(period, 63)
        bt_end   = pd.Timestamp.today()
        bt_start = bt_end - timedelta(days=int(bt_days * 1.5))

    dl_start = (bt_start - timedelta(days=140)).strftime('%Y-%m-%d')
    dl_end   = bt_end.strftime('%Y-%m-%d')

    # ── 加载数据 ──────────────────────────────────────────
    all_syms = list(set(tickers + ['SPY']))
    store    = DataStore()
    all_data = store.get(all_syms, start=dl_start, end=dl_end, min_rows=40)

    spy_df = all_data.get('SPY')
    if spy_df is None:
        raise RuntimeError("SPY 数据获取失败")
    spy_close = spy_df['close']
    all_dates = spy_df.index

    if start:
        bt_start_idx = all_dates.searchsorted(bt_start)
        bt_start_idx = max(63, bt_start_idx)
    else:
        bt_start_idx = max(63, len(all_dates) - bt_days)
    dates = all_dates

    # ── 准备股票数据 + 信号 ───────────────────────────────
    stock_data = {sym: df for sym, df in all_data.items() if sym != 'SPY'}
    if factors:
        from strategies.dynamic_factor import DynamicFactorStrategy
        strategy = DynamicFactorStrategy(factors, factor_params)
    else:
        strategy = RSMomentum(vol_shrink_ratio=VOL_SHRINK_RATIO)
    strategy.set_spy(spy_close)
    signals = {}
    for sym, df in stock_data.items():
        try:
            signals[sym] = strategy.generate_signals(df)
        except Exception:
            pass

    # ── 市值 / 行业过滤 ────────────────────────────────────
    deny_set  = {d.lower() for d in (deny_industries or [])}
    _allowed_cache: dict[str, bool] = {}

    def _is_allowed(sym: str) -> bool:
        if sym not in _allowed_cache:
            if not deny_set and min_cap_b is None and max_cap_b is None:
                _allowed_cache[sym] = True
            else:
                info = get_stock_info([sym]).get(sym, {})
                cap  = info.get('market_cap_b')
                ind  = (info.get('industry') or '').lower()
                ok   = True
                if cap is not None:
                    if min_cap_b is not None and cap < min_cap_b:
                        ok = False
                    if max_cap_b is not None and cap > max_cap_b:
                        ok = False
                if ok and deny_set and any(d in ind for d in deny_set):
                    ok = False
                _allowed_cache[sym] = ok
        return _allowed_cache[sym]

    # ── 行业分类 ────────────────────────────────────────────
    _sector_batch = get_stock_info(list(stock_data.keys()))
    _sector_map: dict[str, str] = {
        s: (_sector_batch.get(s, {}).get('sector') or 'Unknown')
        for s in stock_data
    }

    # ── SPY 熔断 ────────────────────────────────────────────
    spy_rolling_ret = spy_close.pct_change(periods=SPY_BRAKE_PERIOD)

    # ── 逐日模拟 ────────────────────────────────────────────
    cash           = INITIAL_CASH
    positions      = {}
    trades         = []
    equity_history = []
    daily_holdings = []
    total_commission = 0.0
    pending_sells  = {}
    pending_buys   = []
    spy_brake_days = 0

    for i, date in enumerate(dates):
        if i < bt_start_idx:
            equity_history.append({'date': str(date.date()), 'equity': cash,
                                   'spy_equity': INITIAL_CASH})
            continue

        # T+1 卖单执行
        for sym, sell_reason in list(pending_sells.items()):
            if sym not in positions:
                continue
            if sym not in stock_data or date not in stock_data[sym].index:
                continue
            pos   = positions.pop(sym)
            price = stock_data[sym].loc[date, 'open']
            ret   = (price - pos['entry_price']) / pos['entry_price']
            comm  = calc_commission(pos['qty'], price, is_sell=True)
            proceeds = pos['qty'] * price - comm
            pnl   = proceeds - pos['qty'] * pos['entry_price'] - pos['commission']
            cash += proceeds
            total_commission += comm
            trades.append({
                'symbol':      sym,
                'entry_date':  str(pos['entry_date'].date()),
                'exit_date':   str(date.date()),
                'entry_price': round(float(pos['entry_price']), 4),
                'exit_price':  round(float(price), 4),
                'qty':         int(pos['qty']),
                'pnl':         round(float(pnl), 2),
                'return':      round(float(ret), 6),
                'days_held':   (date - pos['entry_date']).days,
                'exit_reason': sell_reason,
                'commission':  round(float(comm), 4),
            })
        pending_sells.clear()

        # T+1 买单执行
        if pending_buys:
            slots = MAX_POSITIONS - len(positions)
            if slots > 0:
                net_liq = cash + sum(
                    p['qty'] * (stock_data[s].loc[date, 'open']
                                if s in stock_data and date in stock_data[s].index
                                else p['entry_price'])
                    for s, p in positions.items()
                )
                min_cash = net_liq * CASH_RESERVE_PCT
                executed = 0
                for sym, _ in pending_buys:
                    if executed >= slots:
                        break
                    if sym in positions or sym not in stock_data or date not in stock_data[sym].index:
                        continue
                    price = stock_data[sym].loc[date, 'open']
                    qty   = int(net_liq * POSITION_PCT / price)
                    comm  = calc_commission(qty, price, is_sell=False)
                    cost  = qty * price + comm
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

        # T 日信号检查
        for sym in list(positions.keys()):
            if sym not in stock_data or date not in stock_data[sym].index:
                continue
            pos   = positions[sym]
            price = stock_data[sym].loc[date, 'close']
            ret   = (price - pos['entry_price']) / pos['entry_price']
            pos['peak_price'] = max(pos['peak_price'], price)
            if ret <= STOP_LOSS_PCT:
                pending_sells[sym] = '止损'
            else:
                peak_ret  = (pos['peak_price'] - pos['entry_price']) / pos['entry_price']
                trail_ret = (price - pos['peak_price']) / pos['peak_price']
                if peak_ret >= TRAIL_STOP_ACTIVATE_PCT and trail_ret <= TRAIL_STOP_PCT:
                    pending_sells[sym] = '移动止损'
                elif sym in signals and date in signals[sym].index:
                    if signals[sym].loc[date, 'signal'] == -1:
                        pending_sells[sym] = '量价背离'

        # SPY 熔断
        spy_ret_20d = spy_rolling_ret.get(date)
        spy_brake   = spy_ret_20d is not None and spy_ret_20d <= SPY_BRAKE_PCT
        if spy_brake:
            spy_brake_days += 1

        # 买入信号
        free_slots = MAX_POSITIONS - len(positions) + len(pending_sells)
        if free_slots > 0 and not spy_brake:
            new_buys = []
            for sym, sig_df in signals.items():
                if sym in positions or sym in pending_sells or date not in sig_df.index:
                    continue
                row = sig_df.loc[date]
                if row['signal'] == 1 and _is_allowed(sym):
                    new_buys.append((sym, row['rs_score']))
            new_buys.sort(key=lambda x: x[1], reverse=True)
            kept_sectors: dict[str, int] = {}
            for s in positions:
                if s not in pending_sells:
                    sec = _sector_map.get(s, 'Unknown')
                    kept_sectors[sec] = kept_sectors.get(sec, 0) + 1
            pending_sector_counts = dict(kept_sectors)
            filtered_buys = []
            for sym, rs in new_buys:
                if len(filtered_buys) >= free_slots:
                    break
                sec = _sector_map.get(sym, 'Unknown')
                if pending_sector_counts.get(sec, 0) >= MAX_PER_SECTOR:
                    continue
                filtered_buys.append((sym, rs))
                pending_sector_counts[sec] = pending_sector_counts.get(sec, 0) + 1
            pending_buys = filtered_buys

        # 当日净值
        port_value = cash
        for sym, pos in positions.items():
            if date in stock_data[sym].index:
                port_value += pos['qty'] * stock_data[sym].loc[date, 'close']

        # SPY 基准净值（同期从 bt_start_idx 起点算）
        spy_start_price = float(spy_close.iloc[bt_start_idx])
        spy_equity = INITIAL_CASH * float(spy_close.get(date, spy_start_price)) / spy_start_price

        equity_history.append({
            'date':      str(date.date()),
            'equity':    round(float(port_value), 2),
            'spy_equity': round(float(spy_equity), 2),
        })

        # 每日持仓快照
        holding_snap = []
        for sym, pos in positions.items():
            cur_p = stock_data[sym].loc[date, 'close'] if date in stock_data[sym].index else pos['entry_price']
            ret = (cur_p - pos['entry_price']) / pos['entry_price']
            holding_snap.append({
                'symbol': sym, 'qty': pos['qty'],
                'entry_price': round(float(pos['entry_price']), 4),
                'cur_price': round(float(cur_p), 4),
                'return': round(float(ret), 6),
                'market_value': round(float(pos['qty'] * cur_p), 2),
            })
        holding_snap.sort(key=lambda x: x['return'], reverse=True)
        daily_holdings.append({
            'date': str(date.date()),
            'equity': round(float(port_value), 2),
            'cash': round(float(cash), 2),
            'holdings': holding_snap,
        })

    # ── 未平仓持仓 ─────────────────────────────────────────
    last_date = dates[-1]
    open_positions = []
    for sym, pos in positions.items():
        cur_price = (stock_data[sym].loc[last_date, 'close']
                     if last_date in stock_data[sym].index else pos['entry_price'])
        pnl = (cur_price - pos['entry_price']) * pos['qty']
        ret = (cur_price - pos['entry_price']) / pos['entry_price']
        open_positions.append({
            'symbol':      sym,
            'entry_date':  str(pos['entry_date'].date()),
            'entry_price': round(float(pos['entry_price']), 4),
            'cur_price':   round(float(cur_price), 4),
            'qty':         int(pos['qty']),
            'pnl':         round(float(pnl), 2),
            'return':      round(float(ret), 6),
            'days_held':   (last_date - pos['entry_date']).days,
        })

    # ── 统计指标 ────────────────────────────────────────────
    # 只取回测区间内的净值序列
    eq_slice = [e for e in equity_history if e['equity'] != INITIAL_CASH or
                equity_history.index(e) >= bt_start_idx]
    equity_df = pd.DataFrame(equity_history).set_index('date')
    final     = float(equity_df['equity'].iloc[-1])
    total_ret = (final - INITIAL_CASH) / INITIAL_CASH
    days      = (dates[-1] - dates[bt_start_idx]).days
    ann_ret   = (1 + total_ret) ** (365 / max(days, 1)) - 1
    spy_ret   = (float(spy_close.iloc[-1]) / float(spy_close.iloc[bt_start_idx])) - 1

    eq_returns = equity_df['equity'].pct_change().dropna()
    sharpe = (float(eq_returns.mean() / eq_returns.std() * np.sqrt(252))
              if eq_returns.std() > 0 else 0.0)
    roll_max = equity_df['equity'].cummax()
    max_dd   = float(((equity_df['equity'] - roll_max) / roll_max).min())

    wins     = [t for t in trades if t['pnl'] > 0]
    win_rate = len(wins) / len(trades) if trades else 0.0

    summary = {
        'initial_cash':     INITIAL_CASH,
        'final_equity':     round(final, 2),
        'total_return':     round(total_ret, 6),
        'annual_return':    round(ann_ret, 6),
        'spy_return':       round(spy_ret, 6),
        'excess_return':    round(total_ret - spy_ret, 6),
        'max_drawdown':     round(max_dd, 6),
        'sharpe':           round(sharpe, 4),
        'total_trades':     len(trades),
        'win_rate':         round(win_rate, 4),
        'total_commission': round(total_commission, 2),
        'spy_brake_days':   spy_brake_days,
        'universe':         universe,
        'bt_start':         str(dates[bt_start_idx].date()),
        'bt_end':           str(last_date.date()),
        'days':             days,
    }

    return {
        'params': {
            'period': period, 'start': start, 'end': end,
            'universe': universe, 'top': top,
            'min_cap_b': min_cap_b, 'max_cap_b': max_cap_b,
            'deny_industries': deny_industries,
        },
        'summary':        summary,
        'equity_curve':   equity_history,
        'trades':         trades,
        'open_positions': open_positions,
        'daily_holdings': daily_holdings if daily else [],
    }


def print_report(result: dict, daily: bool = False):
    """将 run_backtest() 的返回值打印为 CLI 格式报告"""
    s  = result['summary']
    p  = result['params']
    trades        = result['trades']
    open_positions = result['open_positions']

    wins         = [t for t in trades if t['pnl'] > 0]
    stop_losses  = [t for t in trades if t.get('exit_reason') == '止损']
    trail_stops  = [t for t in trades if t.get('exit_reason') == '移动止损']
    vol_div_exits = [t for t in trades if t.get('exit_reason') == '量价背离']

    print(f"\n{'='*60}")
    print(f"  RS 动量策略回测报告  [{s['universe'].upper()}]")
    print(f"  {s['bt_start']} → {s['bt_end']}（{s['days']} 天）")
    print(f"  执行方式：T 日信号 → T+1 开盘价成交（OPG 模式）")
    print(f"{'='*60}")
    print(f"  初始资金        ${s['initial_cash']:>14,}")
    print(f"  最终净值        ${s['final_equity']:>14,.0f}")
    print(f"  总收益率        {s['total_return']:>14.1%}")
    print(f"  手续费合计      ${s['total_commission']:>14,.2f}")
    print(f"  年化收益        {s['annual_return']:>14.1%}")
    print(f"  SPY 同期收益    {s['spy_return']:>14.1%}  ← 基准")
    print(f"  超额收益        {s['excess_return']:>14.1%}  ← {'跑赢' if s['excess_return'] > 0 else '跑输'}大盘")
    print(f"  最大回撤        {s['max_drawdown']:>14.1%}")
    print(f"  Sharpe          {s['sharpe']:>14.2f}")
    brake_pct = s['spy_brake_days'] / max(s['days'], 1) * 100
    print(f"  SPY熔断天数     {s['spy_brake_days']:>11} 天  ({brake_pct:.0f}% 回测期)")
    print(f"  已平仓交易      {s['total_trades']:>14} 笔")
    print(f"  胜率            {s['win_rate']:>14.1%}")
    if trades:
        avg_win  = np.mean([t['pnl'] for t in wins]) if wins else 0
        avg_loss = np.mean([t['pnl'] for t in trades if t['pnl'] <= 0]) or 0
        print(f"  平均盈利        ${avg_win:>14,.0f}")
        print(f"  平均亏损        ${avg_loss:>14,.0f}")
        print(f"  硬止损触发      {len(stop_losses):>14} 笔")
        if stop_losses:
            print(f"  硬止损总亏损    ${sum(t['pnl'] for t in stop_losses):>14,.0f}")
        print(f"  移动止损触发    {len(trail_stops):>14} 笔")
        if trail_stops:
            ts_total = sum(t['pnl'] for t in trail_stops)
            ts_avg   = np.mean([t['return'] for t in trail_stops])
            print(f"  移动止损总盈亏  ${ts_total:>14,.0f}  (平均 {ts_avg:+.1%} 退出)")
        print(f"  量价背离触发    {len(vol_div_exits):>14} 笔")

    if trades:
        print(f"\n{'='*60}")
        print(f"  已平仓交易明细（共 {len(trades)} 笔）")
        print(f"{'='*60}")
        print(f"  {lj('股票',7)}{rj('买入日',12)}{rj('卖出日',12)}{rj('买入价',9)}{rj('卖出价',9)}{rj('收益率',8)}  {rj('盈亏',10)}  原因")
        print(f"  {'-'*80}")
        for t in sorted(trades, key=lambda x: x['exit_date']):
            print(f"  {t['symbol']:<7}"
                  f"{t['entry_date']:>12}"
                  f"{t['exit_date']:>12}"
                  f"{t['entry_price']:>9.2f}"
                  f"{t['exit_price']:>9.2f}"
                  f"{t['return']:>+8.1%}"
                  f"  ${t['pnl']:>+9,.0f}"
                  f"  {t.get('exit_reason', '')}")

    if open_positions:
        print(f"\n{'='*60}")
        print(f"  当前未平仓持仓（{len(open_positions)} 只）")
        print(f"{'='*60}")
        print(f"  {lj('股票',7)}{rj('买入日',12)}{rj('买入价',9)}{rj('现价',9)}{rj('收益率',8)}  {rj('浮盈',10)}{rj('持仓天数',10)}")
        print(f"  {'-'*67}")
        total_open_pnl = 0
        for p in sorted(open_positions, key=lambda x: x['return'], reverse=True):
            total_open_pnl += p['pnl']
            print(f"  {p['symbol']:<7}"
                  f"{p['entry_date']:>12}"
                  f"{p['entry_price']:>9.2f}"
                  f"{p['cur_price']:>9.2f}"
                  f"{p['return']:>+8.1%}"
                  f"  ${p['pnl']:>+9,.0f}"
                  f"{p['days_held']:>8}天")
        print(f"  {'-'*67}")
        print(f"  {'持仓浮盈合计':<20}{'':>18}  ${total_open_pnl:>+9,.0f}")

    if daily and result.get('daily_holdings'):
        _print_daily_holdings(result['daily_holdings'], last_n=50)


def _print_daily_holdings(daily_holdings, last_n=50):
    """打印最近 N 个交易日的每日持仓明细"""
    recent = daily_holdings[-last_n:]
    if not recent:
        return

    print(f"\n{'='*90}")
    print(f"  每日持仓明细（最近 {len(recent)} 个交易日）")
    print(f"{'='*90}")

    for day in recent:
        date_str = day['date']
        n_hold = len(day['holdings'])
        invested = sum(h['market_value'] for h in day['holdings'])
        cash_pct = day['cash'] / day['equity'] * 100 if day['equity'] > 0 else 0

        print(f"\n  {date_str}  净值 ${day['equity']:>12,.0f}  "
              f"现金 ${day['cash']:>10,.0f}({cash_pct:.0f}%)  "
              f"持仓 {n_hold} 只  投资 ${invested:>10,.0f}")

        if day['holdings']:
            print(f"    {lj('股票',7)}{rj('数量',6)}{rj('买入价',9)}{rj('现价',9)}{rj('收益率',8)}  {rj('市值',10)}")
            print(f"    {'-'*51}")
            for h in day['holdings']:
                print(f"    {h['symbol']:<7}{h['qty']:>6}"
                      f"{h['entry_price']:>9.2f}{h['cur_price']:>9.2f}"
                      f"{h['return']:>+8.1%}  ${h['market_value']:>9,.0f}")
        else:
            print(f"    （空仓）")


def run():
    """CLI 入口：解析参数 → run_backtest() → 打印报告"""
    args = _parse_args()
    print(f"获取股票池（{args.universe}）...")
    result = run_backtest(
        period=args.period,
        top=args.top,
        start=args.start,
        end=args.end,
        universe=args.universe,
        daily=args.daily,
        min_cap_b=args.min_cap_b,
        max_cap_b=args.max_cap_b,
        deny_industries=args.deny_industries,
    )
    print_report(result, daily=args.daily)


if __name__ == '__main__':
    run()
