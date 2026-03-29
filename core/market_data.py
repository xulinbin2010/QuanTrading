import math
from ib_insync import Stock
from core.fmt import lj, rj


class MarketData:
    def __init__(self, ib):
        self.ib = ib
        self.tickers = {}       # symbol -> ticker
        self.alerts = {}        # symbol -> {'above': price, 'below': price}

    def subscribe(self, symbols: list[str]):
        """订阅多只股票的实时行情（无实时权限时自动降级为延迟行情）"""
        self.ib.reqMarketDataType(4)  # 1=实时, 3=延迟, 4=延迟冻结
        for symbol in symbols:
            try:
                contract = Stock(symbol, 'SMART', 'USD')
                self.ib.qualifyContracts(contract)
                ticker = self.ib.reqMktData(contract)
                self.tickers[symbol] = ticker
                print(f"已订阅 {symbol}")
            except Exception as e:
                print(f"订阅 {symbol} 失败：{e}")

    def set_alert(self, symbol: str, above: float = None, below: float = None):
        """设置价格报警：above=价格上穿时报警，below=价格下穿时报警"""
        self.alerts[symbol] = {'above': above, 'below': below}
        print(f"{symbol} 报警设置：上穿={above}, 下穿={below}")

    def _fmt(self, val):
        """格式化数值，nan 或无效值显示为 -"""
        if val is None or (isinstance(val, float) and (math.isnan(val) or val <= 0)):
            return '-'
        return f"{val:.2f}" if isinstance(val, float) else str(val)

    def print_prices(self):
        """打印当前所有订阅股票的价格"""
        print(f"\n{'=' * 56}")
        print(f"{lj('股票',8)}{rj('最新价',10)}{rj('买价',10)}{rj('卖价',10)}{rj('成交量',14)}")
        print("-" * 56)
        for symbol, ticker in self.tickers.items():
            last = self._fmt(ticker.last)
            bid  = self._fmt(ticker.bid)
            ask  = self._fmt(ticker.ask)
            vol  = self._fmt(ticker.volume)
            print(f"{symbol:<8}{last:>10}{bid:>10}{ask:>10}{vol:>14}")

    def check_alerts(self):
        """检查价格是否触发报警"""
        for symbol, ticker in self.tickers.items():
            price = ticker.last
            if price is None or (isinstance(price, float) and math.isnan(price)):
                continue
            alert = self.alerts.get(symbol, {})
            if alert.get('above') and price >= alert['above']:
                print(f"  *** [报警] {symbol} 价格 {price:.2f} 已上穿 {alert['above']} ***")
            if alert.get('below') and price <= alert['below']:
                print(f"  *** [报警] {symbol} 价格 {price:.2f} 已下穿 {alert['below']} ***")

    def monitor(self, interval: int = 5, rounds: int = 10):
        """每隔 interval 秒刷新一次，共刷新 rounds 次"""
        print(f"\n开始监控，每 {interval} 秒刷新（共 {rounds} 次），按 Ctrl+C 退出")
        try:
            for i in range(rounds):
                self.ib.sleep(interval)
                self.print_prices()
                self.check_alerts()
        except KeyboardInterrupt:
            print("\n监控已停止")

    def cancel_all(self):
        for symbol, ticker in self.tickers.items():
            try:
                self.ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        self.tickers.clear()
        print("已取消所有行情订阅")
