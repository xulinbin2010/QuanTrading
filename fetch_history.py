"""
独立运行的历史数据拉取脚本。

用法：
  python fetch_history.py              # 拉取默认股票列表的日线
  python fetch_history.py --update     # 只做增量更新
  python fetch_history.py --symbol NVDA --bar 1 day
"""
import argparse
from core.connection import IBConnection
from core.database import Database
from core.historical_data import HistoricalData

# 默认关注的股票和周期
DEFAULT_SYMBOLS = ['NVDA', 'AAPL', 'TSLA', 'MSFT', 'AMZN', 'META', 'GOOGL']
DEFAULT_BAR_SIZES = ['1 day', '1 hour']


def main():
    parser = argparse.ArgumentParser(description='IBKR 历史K线拉取')
    parser.add_argument('--symbol', nargs='+', help='指定股票代码，如 NVDA AAPL')
    parser.add_argument('--bar', nargs='+', help='指定K线周期，如 "1 day" "1 hour"')
    parser.add_argument('--update', action='store_true', help='只做增量更新（跳过已有完整历史）')
    args = parser.parse_args()

    symbols = args.symbol or DEFAULT_SYMBOLS
    bar_sizes = args.bar or DEFAULT_BAR_SIZES

    db = Database()
    db.connect()

    conn = IBConnection()
    ib = conn.connect()

    hd = HistoricalData(ib, db)

    if args.update:
        # 只更新已有数据
        print("=== 增量更新模式 ===")
        for symbol in symbols:
            for bar_size in bar_sizes:
                count = db.get_klines_count(symbol, bar_size)
                if count == 0:
                    print(f"跳过 {symbol} [{bar_size}]：本地无数据，请先完整拉取")
                    continue
                hd.update(symbol, bar_size)
    else:
        # 完整拉取（已有数据则自动走增量）
        print(f"=== 拉取模式：{symbols} x {bar_sizes} ===")
        hd.fetch_all(symbols, bar_sizes)

    # 拉完后展示每只股票日线的最近5根
    print("\n=== 数据预览 ===")
    for symbol in symbols:
        hd.print_klines(symbol, '1 day', limit=5)

    conn.disconnect()
    db.close()


if __name__ == '__main__':
    main()
