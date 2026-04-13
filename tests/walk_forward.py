"""
Walk-Forward 验证 —— 滚动窗口检验策略是否过拟合。

用法（CLI）：
  python -m tests.walk_forward
  python -m tests.walk_forward --train 24 --test 12 --start 2020-01-01
  python -m tests.walk_forward --train 18 --test 6  --start 2019-01-01

说明：
  使用固定默认参数（不调参），验证策略在样本外（OOS）是否一致。
  - IS Sharpe（训练期）vs OOS Sharpe（测试期）对比
  - IS-OOS gap 越小说明过拟合越轻
  - avg OOS Sharpe > 0.5 说明策略在各时期均有效
"""
import argparse
import warnings
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
import numpy as np

warnings.filterwarnings('ignore')


def walk_forward(
    train_months: int = 24,
    test_months: int = 12,
    total_start: str = '2020-01-01',
    total_end: str = None,
    universe: str = 'sp500',
    top: int = 10,
) -> dict:
    """
    滚动窗口验证（固定参数，不调参）。

    返回：
    {
        'windows': [{
            'train_start', 'train_end', 'test_start', 'test_end',
            'is_return', 'is_sharpe',
            'oos_return', 'oos_sharpe', 'oos_max_dd',
            'oos_trades', 'oos_win_rate',
        }, ...],
        'summary': {
            'n_windows', 'avg_oos_return', 'avg_oos_sharpe',
            'avg_is_sharpe', 'is_oos_gap',
            'positive_oos_pct',
        }
    }
    """
    from core.data_store import DataStore
    from core.universe import get_tickers, get_stock_info
    from tests.backtest_rs import run_backtest

    if total_end is None:
        total_end = datetime.today().strftime('%Y-%m-%d')

    total_start_dt = pd.Timestamp(total_start)
    total_end_dt   = pd.Timestamp(total_end)

    # ── 切分窗口 ──────────────────────────────────────────
    windows = []
    cursor = total_start_dt
    while True:
        train_start = cursor
        train_end   = cursor + relativedelta(months=train_months) - timedelta(days=1)
        test_start  = train_end + timedelta(days=1)
        test_end    = test_start + relativedelta(months=test_months) - timedelta(days=1)

        if test_end > total_end_dt:
            break

        windows.append((train_start, train_end, test_start, test_end))
        cursor = cursor + relativedelta(months=test_months)  # 向前滚动 test_months

    if not windows:
        raise ValueError(
            f"时间范围不够：需要至少 {train_months + test_months} 个月，"
            f"实际 {total_start} ~ {total_end}"
        )

    # ── 一次性加载所有数据 ────────────────────────────────
    tickers  = get_tickers(universe)
    all_syms = list(set(tickers + ['SPY', '^VIX']))

    # 最早需要的数据：第一个训练期开始前 140 天（warm-up）
    dl_start = (total_start_dt - timedelta(days=140)).strftime('%Y-%m-%d')
    dl_end   = total_end

    print(f"加载数据（{universe}，{dl_start} ~ {dl_end}）...")
    store    = DataStore()
    full_data = store.get(all_syms, start=dl_start, end=dl_end, min_rows=40)
    print(f"  加载完成，{len(full_data)} 只股票")

    # 一次性获取 stock_info（行业/市值，不随时间变化）
    print("获取股票基本面信息...")
    stock_syms   = [s for s in tickers if s in full_data]
    stock_info   = get_stock_info(stock_syms)
    print(f"  获取完成，{len(stock_info)} 只")

    # ── 逐窗口运行 ────────────────────────────────────────
    results = []
    n = len(windows)
    for idx, (train_start, train_end, test_start, test_end) in enumerate(windows):
        ts = train_start.strftime('%Y-%m-%d')
        te = train_end.strftime('%Y-%m-%d')
        ss = test_start.strftime('%Y-%m-%d')
        se = test_end.strftime('%Y-%m-%d')

        print(f"\n[{idx+1}/{n}] 训练 {ts}~{te}  |  测试 {ss}~{se}")

        # 为当前窗口过滤数据（避免未来数据泄漏）
        def _filter(end_ts):
            return {sym: df[df.index <= end_ts] for sym, df in full_data.items()
                    if not df[df.index <= end_ts].empty}

        train_data = _filter(train_end)
        test_data  = _filter(test_end)

        # IS 回测（训练期）
        try:
            is_result = run_backtest(
                start=ts, end=te, universe=universe, top=top,
                preloaded_data=train_data, preloaded_info=stock_info,
            )
            is_s  = is_result['summary']
            is_ret    = is_s['total_return']
            is_sharpe = is_s['sharpe']
            print(f"  IS  收益 {is_ret:+.1%}  Sharpe {is_sharpe:.2f}")
        except Exception as e:
            print(f"  IS  失败: {e}")
            is_ret, is_sharpe = None, None

        # OOS 回测（测试期）
        try:
            oos_result = run_backtest(
                start=ss, end=se, universe=universe, top=top,
                preloaded_data=test_data, preloaded_info=stock_info,
            )
            oos_s      = oos_result['summary']
            oos_ret    = oos_s['total_return']
            oos_sharpe = oos_s['sharpe']
            oos_max_dd = oos_s['max_drawdown']
            oos_trades = oos_s['total_trades']
            oos_wr     = oos_s['win_rate']
            print(f"  OOS 收益 {oos_ret:+.1%}  Sharpe {oos_sharpe:.2f}  MaxDD {oos_max_dd:.1%}")
        except Exception as e:
            print(f"  OOS 失败: {e}")
            oos_ret = oos_sharpe = oos_max_dd = oos_trades = oos_wr = None

        results.append({
            'train_start':  ts,
            'train_end':    te,
            'test_start':   ss,
            'test_end':     se,
            'is_return':    round(is_ret, 4)    if is_ret    is not None else None,
            'is_sharpe':    round(is_sharpe, 3) if is_sharpe is not None else None,
            'oos_return':   round(oos_ret, 4)    if oos_ret    is not None else None,
            'oos_sharpe':   round(oos_sharpe, 3) if oos_sharpe is not None else None,
            'oos_max_dd':   round(oos_max_dd, 4) if oos_max_dd is not None else None,
            'oos_trades':   oos_trades,
            'oos_win_rate': round(oos_wr, 4)     if oos_wr is not None else None,
        })

    # ── 汇总统计 ──────────────────────────────────────────
    valid_is  = [r['is_sharpe']  for r in results if r['is_sharpe']  is not None]
    valid_oos = [r['oos_sharpe'] for r in results if r['oos_sharpe'] is not None]
    valid_ret = [r['oos_return'] for r in results if r['oos_return'] is not None]

    avg_is_sharpe  = round(float(np.mean(valid_is)),  3) if valid_is  else None
    avg_oos_sharpe = round(float(np.mean(valid_oos)), 3) if valid_oos else None
    avg_oos_return = round(float(np.mean(valid_ret)), 4) if valid_ret else None
    is_oos_gap     = round(avg_is_sharpe - avg_oos_sharpe, 3) \
                     if avg_is_sharpe is not None and avg_oos_sharpe is not None else None
    positive_pct   = round(sum(1 for v in valid_oos if v > 0) / len(valid_oos), 3) \
                     if valid_oos else None

    summary = {
        'n_windows':        len(results),
        'avg_is_sharpe':    avg_is_sharpe,
        'avg_oos_sharpe':   avg_oos_sharpe,
        'avg_oos_return':   avg_oos_return,
        'is_oos_gap':       is_oos_gap,
        'positive_oos_pct': positive_pct,
    }

    return {'windows': results, 'summary': summary}


def _print_results(result: dict):
    """CLI 格式化输出"""
    from core.fmt import lj, rj
    windows = result['windows']
    s       = result['summary']

    print()
    print('=' * 78)
    print('  Walk-Forward 验证报告')
    print('=' * 78)
    print(f"  {'测试窗口':<22} {'IS收益':>8} {'IS Sharpe':>10} {'OOS收益':>8} {'OOS Sharpe':>11} {'最大回撤':>8}")
    print('-' * 78)
    for w in windows:
        label     = f"{w['test_start'][:7]}~{w['test_end'][:7]}"
        is_r      = f"{w['is_return']:+.1%}"  if w['is_return']    is not None else '-'
        is_sh     = f"{w['is_sharpe']:.2f}"   if w['is_sharpe']    is not None else '-'
        oos_r     = f"{w['oos_return']:+.1%}" if w['oos_return']   is not None else '-'
        oos_sh_v  = w['oos_sharpe']
        oos_sh    = f"{oos_sh_v:.2f}"         if oos_sh_v is not None else '-'
        oos_dd    = f"{w['oos_max_dd']:.1%}"  if w['oos_max_dd']   is not None else '-'

        # 低 OOS Sharpe 标红（用 ASCII 符号标记）
        flag = ' !' if oos_sh_v is not None and oos_sh_v < 0 else '  '
        print(f"  {label:<22} {is_r:>8} {is_sh:>10} {oos_r:>8} {oos_sh:>11} {oos_dd:>8}{flag}")

    print('=' * 78)
    print(f"  窗口数量：{s['n_windows']}")
    print(f"  平均 IS  Sharpe：{s['avg_is_sharpe']:.3f}" if s['avg_is_sharpe'] is not None else "  平均 IS  Sharpe：-")
    print(f"  平均 OOS Sharpe：{s['avg_oos_sharpe']:.3f}" if s['avg_oos_sharpe'] is not None else "  平均 OOS Sharpe：-")
    print(f"  平均 OOS 年化收益：{s['avg_oos_return']:+.1%}" if s['avg_oos_return'] is not None else "  平均 OOS 年化收益：-")

    if s['is_oos_gap'] is not None:
        gap = s['is_oos_gap']
        if gap > 1.0:
            verdict = '⚠  IS-OOS 差距较大，存在明显过拟合风险'
        elif gap > 0.5:
            verdict = '△  IS-OOS 差距中等，参数可能有一定过拟合'
        else:
            verdict = '✓  IS-OOS 差距较小，策略泛化性良好'
        print(f"  IS-OOS Sharpe 差：{gap:+.3f}  {verdict}")

    if s['positive_oos_pct'] is not None:
        print(f"  OOS 正 Sharpe 比例：{s['positive_oos_pct']:.0%}")
    print('=' * 78)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Walk-Forward 验证')
    parser.add_argument('--train',    type=int,   default=24,          help='训练窗口（月），默认 24')
    parser.add_argument('--test',     type=int,   default=12,          help='测试窗口（月），默认 12')
    parser.add_argument('--start',    default='2020-01-01',            help='总起始日期')
    parser.add_argument('--end',      default=None,                    help='总结束日期（默认今天）')
    parser.add_argument('--universe', default='sp500',                 help='股票池')
    parser.add_argument('--top',      type=int,   default=10,          help='最大持仓数')
    args = parser.parse_args()

    result = walk_forward(
        train_months=args.train,
        test_months=args.test,
        total_start=args.start,
        total_end=args.end,
        universe=args.universe,
        top=args.top,
    )
    _print_results(result)
