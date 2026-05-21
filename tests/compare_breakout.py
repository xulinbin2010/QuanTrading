"""
对比：严格突破 vs 宽松接近高点

用法：
  python -m tests.compare_breakout --start 2022-01-01 --end 2024-12-31
  python -m tests.compare_breakout --start 2024-01-01
"""
import argparse
import warnings
from tests.backtest_rs import run_backtest

warnings.filterwarnings('ignore')

SCENARIOS = [
    ('严格突破（当前）', 0.00),
    ('宽松 -3%',        0.03),
    ('宽松 -5%',        0.05),
    ('宽松 -8%',        0.08),
]


def fmt(v, fmt_str):
    try:
        return fmt_str.format(v)
    except Exception:
        return '  N/A'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',    default='2022-01-01')
    parser.add_argument('--end',      default=None)
    parser.add_argument('--universe', default='sp500')
    args = parser.parse_args()

    print(f'\n回测区间：{args.start} ~ {args.end or "今天"}  股票池：{args.universe}')
    print(f'{"=" * 76}')

    results = []
    for label, pct in SCENARIOS:
        print(f'  运行 [{label}] ...', flush=True)
        r = run_backtest(
            start=args.start,
            end=args.end,
            universe=args.universe,
            breakout_proximity_pct=pct,
        )
        s      = r.get('summary', {})
        trades = r.get('trades', [])
        wins   = [t for t in trades if t.get('pnl', 0) > 0]
        n      = len(trades)
        results.append({
            'label':    label,
            'total':    s.get('total_return'),
            'spy':      s.get('spy_return'),
            'sharpe':   s.get('sharpe'),
            'max_dd':   s.get('max_drawdown'),
            'n':        n,
            'win_rate': len(wins) / n if n else None,
            'avg_hold': s.get('avg_hold_days'),
        })

    spy_ret = results[0]['spy'] or 0
    print(f'\n{"=" * 76}')
    hdr = f"  {'策略版本':<16}  {'总收益':>8}  {'超额':>8}  {'Sharpe':>7}  {'最大回撤':>8}  {'笔数':>5}  {'胜率':>6}  {'均持天':>6}"
    print(hdr)
    print(f"  {'-' * 72}")
    for r in results:
        exc = (r['total'] or 0) - spy_ret
        print(
            f"  {r['label']:<16}"
            f"  {fmt(r['total'],    '{:>+.1%}'):>8}"
            f"  {fmt(exc,           '{:>+.1%}'):>8}"
            f"  {fmt(r['sharpe'],   '{:>7.2f}'):>7}"
            f"  {fmt(r['max_dd'],   '{:>+.1%}'):>8}"
            f"  {r['n']:>5}"
            f"  {fmt(r['win_rate'], '{:>5.1%}'):>6}"
            f"  {fmt(r['avg_hold'], '{:>6.1f}'):>6}"
        )
    print(f'{"=" * 76}')
    print(f'  SPY 同期：{fmt(spy_ret, "{:+.1%}")}')
    print()


if __name__ == '__main__':
    main()
