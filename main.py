from core.connection import IBConnection
from core.market_data import MarketData
from core.account import Account
from core.trading import Trading
from core.database import Database


def main():
    # 数据库（失败不阻塞程序）
    db = Database()
    db.connect()

    # IB Gateway
    conn = IBConnection()
    ib = conn.connect()

    market = MarketData(ib)
    account = Account(ib, db=db)
    trader = Trading(ib, db=db)

    # 默认监控的股票
    SYMBOLS = ['NVDA', 'AAPL', 'TSLA', 'MSFT']
    market.subscribe(SYMBOLS)

    while True:
        print("\n" + "=" * 40)
        print("  量化交易系统（模拟盘）")
        print("=" * 40)
        print("  1.  查看实时价格")
        print("  2.  持续监控价格")
        print("  3.  设置价格报警")
        print("  4.  查看账户余额和持仓")
        print("  5.  市价买入")
        print("  6.  市价卖出")
        print("  7.  限价买入")
        print("  8.  限价卖出")
        print("  9.  查看未成交订单")
        print("  10. 撤销所有订单")
        print("  11. 查看交易记录（数据库）")
        print("  12. 查看账户净值历史（数据库）")
        print("  0.  退出")
        print("-" * 40)

        choice = input("请选择：").strip()

        try:
            if choice == '1':
                market.print_prices()

            elif choice == '2':
                rounds = input("刷新次数（默认12次=1分钟）：").strip()
                rounds = int(rounds) if rounds.isdigit() else 12
                market.monitor(interval=5, rounds=rounds)

            elif choice == '3':
                sym = input("股票代码（如 NVDA）：").strip().upper()
                above = input("上穿报警价（不设置直接回车）：").strip()
                below = input("下穿报警价（不设置直接回车）：").strip()
                market.set_alert(
                    sym,
                    above=float(above) if above else None,
                    below=float(below) if below else None,
                )

            elif choice == '4':
                account.summary()

            elif choice == '5':
                sym = input("股票代码：").strip().upper()
                qty = int(input("买入数量：").strip())
                trader.market_buy(sym, qty)

            elif choice == '6':
                sym = input("股票代码：").strip().upper()
                qty = int(input("卖出数量：").strip())
                trader.market_sell(sym, qty)

            elif choice == '7':
                sym = input("股票代码：").strip().upper()
                qty = int(input("买入数量：").strip())
                price = float(input("限价：").strip())
                trader.limit_buy(sym, qty, price)

            elif choice == '8':
                sym = input("股票代码：").strip().upper()
                qty = int(input("卖出数量：").strip())
                price = float(input("限价：").strip())
                trader.limit_sell(sym, qty, price)

            elif choice == '9':
                trader.print_open_orders()

            elif choice == '10':
                trader.cancel_all_orders()

            elif choice == '11':
                sym = input("按股票筛选（全部直接回车）：").strip().upper()
                db.print_orders(symbol=sym if sym else None)

            elif choice == '12':
                db.print_account_history()

            elif choice == '0':
                market.cancel_all()
                conn.disconnect()
                db.close()
                break

            else:
                print("无效选项，请重新输入")

        except KeyboardInterrupt:
            print("\n操作已取消")
        except ValueError as e:
            print(f"输入格式错误：{e}")
        except Exception as e:
            print(f"操作失败：{e}")


if __name__ == '__main__':
    main()
