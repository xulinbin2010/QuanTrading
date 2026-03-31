"""
RS 动量选股扫描器（独立运行，无需 IB Gateway）

每天收盘后运行，输出：
  - 买入候选：RS 最强 + 量价突破 + 趋势向上，按 RS 得分排名
  - 持仓报警：量价背离（顶部信号）
  - RS 强度排名 TOP N
  - 内部人买入：近 N 天有高管/董事主动买入的股票（按金额排序）

用法：
  python sp500_scanner.py                             # 扫描 S&P 500，显示前15名
  python sp500_scanner.py --top 20                    # 显示前20名买入信号
  python sp500_scanner.py --held NVDA AMD             # 监控指定持仓的卖出报警
  python sp500_scanner.py --universe nasdaq100        # 扫描纳斯达克100
  python sp500_scanner.py --universe russell2000      # 扫描罗素2000
  python sp500_scanner.py --extra TSLA AAPL           # 追加自选股
  python sp500_scanner.py --insider-days 60           # 查看近60天内部人买入
  python sp500_scanner.py --universe nasdaq100 --top 10 --held NVDA
"""
import argparse
import warnings
from datetime import date, timedelta
import pandas as pd
from strategies.rs_momentum import RSMomentum
from core.universe import get_tickers
from core.data_store import DataStore
from core.insider import get_insider_buys, score_to_stars
from core.fmt import lj, rj
import config

warnings.filterwarnings('ignore')

# period 字符串 → 下载天数映射（含预热裕量）
_PERIOD_DAYS = {'1mo': 60, '3mo': 120, '6mo': 210, '1y': 400, '2y': 760}


def run_scanner(
    held_positions: list[str] = None,
    avg_costs: dict[str, float] = None,   # {symbol: avg_cost}，用于移动止损检查
    top_n: int = 15,
    period: str = '1y',
    universe: str = 'sp500',
    extra: list[str] = None,
    use_insider: bool = True,
    insider_only: bool = False,
    insider_days: int = None,
):
    held_positions = [s.upper() for s in (held_positions or [])]
    extra_tickers  = [s.upper() for s in (extra or [])]
    avg_costs      = {k.upper(): v for k, v in (avg_costs or {}).items()}

    # ── 获取股票池 ────────────────────────────────────────────
    all_extra = list(set(held_positions + extra_tickers))
    tickers = get_tickers(universe, extra=all_extra if all_extra else None)

    # ── 通过 DataStore 获取数据（含 SPY）────────────────────
    days  = _PERIOD_DAYS.get(period, 400)
    start = (date.today() - timedelta(days=days)).strftime('%Y-%m-%d')
    end   = date.today().strftime('%Y-%m-%d')
    all_tickers = list(set(tickers + ['SPY']))

    store    = DataStore()
    all_data = store.get(all_tickers, start=start, end=end, min_rows=80)

    data      = {sym: df for sym, df in all_data.items() if sym != 'SPY'}
    spy_close = all_data['SPY']['close'] if 'SPY' in all_data else None
    if spy_close is None:
        print('SPY 数据获取失败')
        return

    # ── 内部人士买入数据 ─────────────────────────────────────
    insider_map: dict[str, dict] = {}
    if use_insider:
        insider_map = get_insider_buys(
            days        = insider_days or config.INSIDER_DAYS,
            min_value_k = config.INSIDER_MIN_VALUE_K,
        )

    # ── 运行策略 ─────────────────────────────────────────────
    strategy = RSMomentum(vol_shrink_ratio=config.VOL_SHRINK_RATIO)
    strategy.set_spy(spy_close)

    buy_candidates  = []   # 今日出现买入信号
    sell_alerts     = []   # 持仓出现量价背离
    rs_ranking      = []   # 所有股票的 RS 排名

    for symbol, df in data.items():
        try:
            result = strategy.generate_signals(df)
            latest = result.iloc[-1]
            rs     = latest.get('rs_score', float('nan'))

            insider       = insider_map.get(symbol, {})
            insider_score = insider.get('score', 0)
            vol_ratio     = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 0

            rs_ranking.append({
                'symbol':       symbol,
                'rs_score':     rs,
                'close':        latest['close'],
                'vol_ratio':    vol_ratio,
                'insider_score': insider_score,
            })

            sig = latest['signal']
            if sig == 1:
                buy_candidates.append({
                    'symbol':        symbol,
                    'close':         latest['close'],
                    'rs_score':      rs,
                    'vol_ratio':     vol_ratio,
                    'breakout':      latest['breakout'],
                    'insider_score': insider_score,
                })
            # 持仓报警：量价背离 + 移动止损
            if symbol in held_positions:
                cur_close = float(latest['close'])
                vol_ratio = latest['volume'] / latest['vol_ma20'] if latest['vol_ma20'] > 0 else 0
                if sig == -1:
                    sell_alerts.append({
                        'symbol': symbol, 'close': cur_close,
                        'rs_score': rs, 'vol_ratio': vol_ratio,
                        'reason': '量价背离（新高缩量）',
                    })
                # 移动止损：需要 avg_cost
                if symbol in avg_costs:
                    avg_cost = avg_costs[symbol]
                    peak     = float(result['close'].max())
                    peak_ret = (peak - avg_cost) / avg_cost
                    trail_ret = (cur_close - peak) / peak
                    if (peak_ret >= config.TRAIL_STOP_ACTIVATE_PCT
                            and trail_ret <= config.TRAIL_STOP_PCT):
                        sell_alerts.append({
                            'symbol': symbol, 'close': cur_close,
                            'rs_score': rs, 'vol_ratio': vol_ratio,
                            'reason': f'移动止损（峰值 ${peak:.2f} +{peak_ret:.1%}，回撤 {trail_ret:.1%}）',
                        })
        except Exception:
            pass

    # insider_only 过滤：只保留有内部人买入的候选
    if insider_only:
        buy_candidates = [c for c in buy_candidates if c.get('insider_score', 0) > 0]

    # ── 输出结果 ─────────────────────────────────────────────
    _print_buy_signals(buy_candidates, top_n, use_insider)
    _print_sell_alerts(sell_alerts, held_positions, rs_ranking)
    _print_rs_ranking(rs_ranking, top_n, use_insider)
    if use_insider:
        _print_insider_buys(insider_map, rs_ranking)


def _print_buy_signals(candidates: list, top_n: int, show_insider: bool = True):
    print(f"\n{'='*60}")
    print(f"  买入信号（今日 RS跑赢+突破+放量）共 {len(candidates)} 只")
    print(f"{'='*60}")
    if not candidates:
        print("  今日无买入信号")
        return
    df = pd.DataFrame(candidates).sort_values('rs_score', ascending=False).head(top_n)
    if show_insider:
        print(f"  {lj('股票',8)}{rj('收盘价',10)}{rj('RS得分',10)}{rj('量比',10)}{rj('内部人',8)}")
        print(f"  {'-'*46}")
        for _, r in df.iterrows():
            rs_str  = f"{r['rs_score']:.2f}" if pd.notna(r['rs_score']) else '-'
            vol_str = f"{r['vol_ratio']:.1f}x"
            stars   = score_to_stars(int(r.get('insider_score', 0)))
            print(f"  {r['symbol']:<8}{r['close']:>10.2f}{rs_str:>10}{vol_str:>10}{stars:>8}")
    else:
        print(f"  {lj('股票',8)}{rj('收盘价',10)}{rj('RS得分',10)}{rj('量比',10)}")
        print(f"  {'-'*38}")
        for _, r in df.iterrows():
            rs_str  = f"{r['rs_score']:.2f}" if pd.notna(r['rs_score']) else '-'
            vol_str = f"{r['vol_ratio']:.1f}x"
            print(f"  {r['symbol']:<8}{r['close']:>10.2f}{rs_str:>10}{vol_str:>10}")


def _print_sell_alerts(alerts: list, held: list, rs_ranking: list):
    print(f"\n{'='*60}")
    print(f"  持仓卖出报警（量价背离）")
    print(f"{'='*60}")
    if not held:
        print("  未指定持仓（用 --held NVDA AMD 来监控）")
        return
    if not alerts:
        print(f"  持仓 {held} 今日无报警信号")
        return
    print(f"  {lj('股票',8)}{rj('收盘价',10)}{rj('RS得分',10)}{rj('量比',10)}  原因")
    print(f"  {'-'*44}")
    for a in alerts:
        rs_str  = f"{a['rs_score']:.2f}" if pd.notna(a['rs_score']) else '-'
        vol_str = f"{a['vol_ratio']:.1f}x"
        print(f"  {a['symbol']:<8}{a['close']:>10.2f}{rs_str:>10}{vol_str:>10}  {a['reason']}")


def _print_rs_ranking(rs_ranking: list, top_n: int, show_insider: bool = True):
    print(f"\n{'='*60}")
    print(f"  RS 强度排名 TOP {top_n}（跑赢 SPY 最多的股票）")
    print(f"{'='*60}")
    df = pd.DataFrame(rs_ranking).dropna(subset=['rs_score'])
    df = df.sort_values('rs_score', ascending=False).head(top_n)
    if show_insider:
        print(f"  {lj('排名',5)}{lj('股票',8)}{rj('收盘价',10)}{rj('RS得分',10)}{rj('量比',8)}{rj('内部人',8)}")
        print(f"  {'-'*49}")
        for i, (_, r) in enumerate(df.iterrows(), 1):
            rs_str  = f"{r['rs_score']:.2f}"
            vol_str = f"{r['vol_ratio']:.1f}x"
            stars   = score_to_stars(int(r.get('insider_score', 0)))
            print(f"  {i:<5}{r['symbol']:<8}{r['close']:>10.2f}{rs_str:>10}{vol_str:>8}{stars:>8}")
    else:
        print(f"  {lj('排名',5)}{lj('股票',8)}{rj('收盘价',10)}{rj('RS得分',10)}{rj('量比',8)}")
        print(f"  {'-'*41}")
        for i, (_, r) in enumerate(df.iterrows(), 1):
            rs_str  = f"{r['rs_score']:.2f}"
            vol_str = f"{r['vol_ratio']:.1f}x"
            print(f"  {i:<5}{r['symbol']:<8}{r['close']:>10.2f}{rs_str:>10}{vol_str:>8}")


def _print_insider_buys(insider_map: dict, rs_ranking: list):
    """显示股票池内所有有内部人买入记录的股票，按买入金额降序。"""
    print(f"\n{'='*60}")
    print(f"  内部人买入（近期高管/董事主动买入，按金额排序）")
    print(f"{'='*60}")
    if not insider_map:
        print("  无内部人买入数据（网络失败或已用 --no-insider）")
        return

    # 用 rs_ranking 构建 symbol→{close, rs_score} 快查表
    price_map = {r['symbol']: r for r in rs_ranking}

    # 只保留在当前股票池中的 ticker
    rows = []
    for sym, info in insider_map.items():
        if sym not in price_map:
            continue
        pr = price_map[sym]
        rows.append({
            'symbol':      sym,
            'close':       pr['close'],
            'rs_score':    pr['rs_score'],
            'vol_ratio':   pr['vol_ratio'],
            'score':       info['score'],
            'total_value': info['total_value'],
            'count':       info['count'],
            'last_date':   info.get('last_date', ''),
        })

    if not rows:
        print("  当前股票池中无内部人买入记录")
        return

    rows.sort(key=lambda x: x['total_value'], reverse=True)

    print(f"  {lj('股票',8)}{rj('收盘价',10)}{rj('RS得分',10)}{rj('内部人',8)}"
          f"{rj('买入金额',14)}{rj('人数',6)}  最近日期")
    print(f"  {'-'*62}")
    for r in rows:
        rs_str    = f"{r['rs_score']:.2f}" if pd.notna(r['rs_score']) else '-'
        stars     = score_to_stars(int(r['score']))
        val_str   = f"${r['total_value']:,.0f}"
        count_str = f"{r['count']}人"
        print(f"  {r['symbol']:<8}{r['close']:>10.2f}{rs_str:>10}{stars:>8}"
              f"{val_str:>14}{count_str:>6}  {r['last_date']}")


def main():
    parser = argparse.ArgumentParser(description='RS 动量选股扫描器（独立运行，无需 IB）')
    parser.add_argument('--top',      type=int, default=15,  help='显示前 N 名买入信号')
    parser.add_argument('--held',     nargs='+', default=[], help='当前持仓，用于卖出报警')
    parser.add_argument('--avg-cost', nargs='+', default=[], metavar='SYM:COST',
                        help='持仓均价，用于移动止损检查，如 NVDA:850 AMD:115')
    parser.add_argument('--extra',    nargs='+', default=[], help='追加自选股（如 TSLA BTC-USD）')
    parser.add_argument('--period',       default='1y',    help='历史数据跨度（默认 1y）')
    parser.add_argument('--universe',     default='sp500', help='股票池：sp500 / nasdaq100 / russell2000')
    parser.add_argument('--no-insider',   action='store_true', help='跳过内部人士数据（离线/加速）')
    parser.add_argument('--insider-only', action='store_true', help='只显示有内部人买入的候选')
    parser.add_argument('--insider-days', type=int, default=None,
                        help=f'内部人买入观察窗口（天），默认 {config.INSIDER_DAYS} 天')
    args = parser.parse_args()

    avg_costs = {}
    for item in args.avg_cost:
        try:
            sym, cost = item.split(':')
            avg_costs[sym.upper()] = float(cost)
        except ValueError:
            print(f'  [警告] --avg-cost 格式错误（期望 SYM:COST）：{item}')

    run_scanner(
        held_positions=args.held,
        avg_costs=avg_costs if avg_costs else None,
        top_n=args.top,
        period=args.period,
        universe=args.universe,
        extra=args.extra,
        use_insider=not args.no_insider,
        insider_only=args.insider_only,
        insider_days=args.insider_days,
    )


if __name__ == '__main__':
    main()
