from core.fmt import lj, rj


class Account:
    def __init__(self, ib, db=None):
        self.ib = ib
        self.db = db

    def _get_value(self, tag):
        """从账户数据中取一个字段的值"""
        for v in self.ib.accountValues():
            if v.tag == tag and v.currency == 'USD':
                return float(v.value)
        return 0.0

    def print_balance(self):
        """打印账户余额核心指标"""
        net_liq   = self._get_value('NetLiquidation')
        cash      = self._get_value('TotalCashValue')
        unrealized = self._get_value('UnrealizedPnL')
        realized   = self._get_value('RealizedPnL')
        buying     = self._get_value('BuyingPower')

        print("\n===== 账户余额 =====")
        print(f"  {lj('净资产',20)} {net_liq:>15.2f} USD")
        print(f"  {lj('现金',20)} {cash:>15.2f} USD")
        print(f"  {lj('浮动盈亏',18)} {unrealized:>15.2f} USD")
        print(f"  {lj('已实现盈亏',17)} {realized:>15.2f} USD")
        print(f"  {lj('购买力',19)} {buying:>15.2f} USD")

        # 自动保存快照到数据库
        if self.db:
            try:
                self.db.save_account_snapshot(net_liq, cash, unrealized, realized, buying)
            except Exception as e:
                print(f"  [警告] 快照存库失败：{e}")

    def print_positions(self):
        """打印当前持仓"""
        positions = self.ib.positions()
        if not positions:
            print("\n当前无持仓")
            return
        print("\n===== 当前持仓 =====")
        print(f"{lj('股票',8)}{rj('数量',8)}{rj('均价',12)}{rj('市值',14)}")
        print("-" * 44)
        for pos in positions:
            symbol = pos.contract.symbol
            qty = pos.position
            avg = pos.avgCost
            print(f"{symbol:<8}{qty:>8.0f}{avg:>12.2f}{qty * avg:>14.2f}")

    def summary(self):
        self.print_balance()
        self.print_positions()
