"""
简单回测引擎。

用法：
  python -m tests.backtest                        # 回测 NVDA，MA(10,30)
  python -m tests.backtest --symbol AAPL --fast 5 --slow 20
  python -m tests.backtest --symbol NVDA AAPL TSLA  # 批量回测
"""
import argparse
import yfinance as yf
import pandas as pd
import numpy as np
from strategies.ma_crossover import MACrossover


def fetch_data(symbol: str, period: str = '5y') -> pd.DataFrame:
    """用 yfinance 拉日线数据，返回标准 OHLCV DataFrame"""
    print(f"拉取 {symbol} 历史数据（{period}）...")
    df = yf.download(symbol, period=period, auto_adjust=True, progress=False)
    # 新版 yfinance 返回 MultiIndex 列，压平处理
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0].lower() for c in df.columns]
    else:
        df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
    return df


def run_backtest(df: pd.DataFrame, strategy, initial_cash: float = 100_000.0) -> dict:
    """
    执行回测，返回统计指标。

    规则：
      - 买入信号：用全部现金买入（按收盘价）
      - 卖出信号：清仓（按收盘价）
      - 不做空，不加杠杆
    """
    df = strategy.generate_signals(df).dropna(subset=['ma_fast', 'ma_slow'])

    cash = initial_cash
    shares = 0
    trades = []  # (dt, action, price, shares, pnl)
    equity_curve = []

    for dt, row in df.iterrows():
        price = row['close']
        sig   = row['signal']

        if sig == 1 and cash > 0:           # 买入
            shares = cash / price
            cash = 0.0
            trades.append((dt, 'BUY', price, shares, 0))

        elif sig == -1 and shares > 0:       # 卖出
            proceeds = shares * price
            pnl = proceeds - trades[-1][2] * shares if trades else 0
            cash = proceeds
            trades.append((dt, 'SELL', price, shares, pnl))
            shares = 0

        equity = cash + shares * price
        equity_curve.append({'dt': dt, 'equity': equity})

    # 期末若还持仓，按最后收盘价估值
    final_price = df['close'].iloc[-1]
    final_equity = cash + shares * final_price

    equity_df = pd.DataFrame(equity_curve).set_index('dt')
    equity_df['returns'] = equity_df['equity'].pct_change()

    # 最大回撤
    roll_max = equity_df['equity'].cummax()
    drawdown = (equity_df['equity'] - roll_max) / roll_max
    max_drawdown = drawdown.min()

    # 胜率
    sell_trades = [t for t in trades if t[1] == 'SELL']
    win_trades  = [t for t in sell_trades if t[4] > 0]
    win_rate = len(win_trades) / len(sell_trades) if sell_trades else 0

    # 年化收益
    days = (df.index[-1] - df.index[0]).days
    total_return = (final_equity - initial_cash) / initial_cash
    annual_return = (1 + total_return) ** (365 / days) - 1 if days > 0 else 0

    # Sharpe（简化，无风险利率=0）
    sharpe = (equity_df['returns'].mean() / equity_df['returns'].std()) * np.sqrt(252) \
             if equity_df['returns'].std() > 0 else 0

    return {
        'strategy':      strategy.name,
        'initial_cash':  initial_cash,
        'final_equity':  final_equity,
        'total_return':  total_return,
        'annual_return': annual_return,
        'max_drawdown':  max_drawdown,
        'sharpe':        sharpe,
        'total_trades':  len(sell_trades),
        'win_rate':      win_rate,
        'trades':        trades,
        'equity_df':     equity_df,
    }


def print_report(symbol: str, result: dict):
    r = result
    print(f"\n{'=' * 50}")
    print(f"  回测报告：{symbol}  |  {r['strategy']}")
    print(f"{'=' * 50}")
    print(f"  初始资金      ${r['initial_cash']:>12,.0f}")
    print(f"  最终净值      ${r['final_equity']:>12,.0f}")
    print(f"  总收益率      {r['total_return']:>12.1%}")
    print(f"  年化收益      {r['annual_return']:>12.1%}")
    print(f"  最大回撤      {r['max_drawdown']:>12.1%}")
    print(f"  Sharpe 比率   {r['sharpe']:>12.2f}")
    print(f"  交易次数      {r['total_trades']:>12}")
    print(f"  胜率          {r['win_rate']:>12.1%}")

    # 打印最近5笔交易
    trades = r['trades'][-10:]
    if trades:
        print(f"\n  最近 {len(trades)} 笔交易：")
        print(f"  {'日期':<14}{'方向':<6}{'价格':>10}{'股数':>10}{'盈亏':>12}")
        print(f"  {'-' * 54}")
        for t in trades:
            dt_str = t[0].strftime('%Y-%m-%d') if hasattr(t[0], 'strftime') else str(t[0])[:10]
            pnl_str = f"{t[4]:>+.0f}" if t[1] == 'SELL' else '-'
            print(f"  {dt_str:<14}{t[1]:<6}{t[2]:>10.2f}{t[3]:>10.1f}{pnl_str:>12}")


def main():
    parser = argparse.ArgumentParser(description='均线交叉策略回测')
    parser.add_argument('--symbol', nargs='+', default=['NVDA'])
    parser.add_argument('--fast',   type=int, default=10)
    parser.add_argument('--slow',   type=int, default=30)
    parser.add_argument('--period', default='5y', help='历史数据时间跨度，如 1y 2y 5y')
    parser.add_argument('--cash',   type=float, default=100_000)
    args = parser.parse_args()

    strategy = MACrossover(fast=args.fast, slow=args.slow)

    for symbol in args.symbol:
        df = fetch_data(symbol, period=args.period)
        result = run_backtest(df, strategy, initial_cash=args.cash)
        print_report(symbol, result)


if __name__ == '__main__':
    main()
