from ib_insync import Stock, MarketOrder, LimitOrder
from core.fmt import lj, rj


class Trading:
    def __init__(self, ib, db=None):
        self.ib = ib
        self.db = db  # Database 实例，传入后自动记录每笔订单

    def _get_contract(self, symbol: str) -> Stock:
        contract = Stock(symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)
        return contract

    def _save(self, trade, order_type, price=None):
        if not self.db:
            return
        try:
            o = trade.order
            self.db.save_order(
                symbol=trade.contract.symbol,
                action=o.action,
                order_type=order_type,
                quantity=o.totalQuantity,
                price=price,
                status=trade.orderStatus.status,
                order_id=o.orderId,
            )
        except Exception as e:
            print(f"[警告] 订单存库失败：{e}（不影响交易）")

    def _place(self, symbol, order, order_type, label, price=None):
        """统一下单逻辑"""
        try:
            contract = self._get_contract(symbol)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            self._save(trade, order_type, price)
            price_info = f" @ {price}" if price else ""
            print(f"[{label}] {symbol} x{order.totalQuantity}{price_info}  状态: {trade.orderStatus.status}")
            return trade
        except Exception as e:
            print(f"[下单失败] {label} {symbol}：{e}")
            return None

    def market_buy(self, symbol: str, quantity: int, tif: str = 'DAY'):
        order = MarketOrder('BUY', quantity, tif=tif)
        return self._place(symbol, order, 'MKT', f'市价买入({tif})')

    def market_sell(self, symbol: str, quantity: int, tif: str = 'DAY'):
        order = MarketOrder('SELL', quantity, tif=tif)
        return self._place(symbol, order, 'MKT', f'市价卖出({tif})')

    def limit_buy(self, symbol: str, quantity: int, price: float, tif: str = 'DAY'):
        order = LimitOrder('BUY', quantity, price, tif=tif)
        return self._place(symbol, order, 'LMT', '限价买入', price)

    def limit_sell(self, symbol: str, quantity: int, price: float, tif: str = 'DAY'):
        order = LimitOrder('SELL', quantity, price, tif=tif)
        return self._place(symbol, order, 'LMT', '限价卖出', price)

    def print_open_orders(self):
        """查看所有未成交订单"""
        trades = self.ib.openTrades()
        if not trades:
            print("\n当前无未成交订单")
            return
        print("\n===== 未成交订单 =====")
        print(f"{lj('股票',8)}{lj('方向',6)}{rj('数量',8)}{rj('价格',10)}{rj('状态',15)}")
        print("-" * 50)
        for t in trades:
            symbol = t.contract.symbol
            action = t.order.action
            qty    = int(t.order.totalQuantity)
            price  = getattr(t.order, 'lmtPrice', None)
            price_str = f"{price:.2f}" if price else "市价"
            status = t.orderStatus.status
            print(f"{symbol:<8}{action:<6}{qty:>8}{price_str:>10}{status:>15}")

    def cancel_all_orders(self):
        """撤销所有未成交订单"""
        trades = self.ib.openTrades()
        for t in trades:
            try:
                self.ib.cancelOrder(t.order)
            except Exception as e:
                print(f"撤销 {t.contract.symbol} 订单失败：{e}")
        self.ib.sleep(1)
        print(f"已撤销 {len(trades)} 笔订单")
