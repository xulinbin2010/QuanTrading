"""
Mock IB Gateway — 与 ib_insync.IB 接口兼容，用于单元测试和集成测试。

不依赖任何网络连接，所有操作完全在内存中完成。
支持故障注入（qualify 失败、place 失败、部分成交、断线模拟、仓位数量异常）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


# ──────────────────────────────────────────────────────────
#  数据类（与 ib_insync 对象属性保持一致）
# ──────────────────────────────────────────────────────────

@dataclass
class MockContract:
    symbol: str
    secType: str = 'STK'
    exchange: str = 'SMART'
    currency: str = 'USD'
    conId: int = 0


@dataclass
class MockOrder:
    orderId: int
    action: str          # 'BUY' or 'SELL'
    totalQuantity: float
    orderType: str       # 'MKT' or 'LMT'
    tif: str = 'DAY'
    lmtPrice: float = 0.0


@dataclass
class MockOrderStatus:
    status: str = 'Submitted'
    filled: float = 0.0
    remaining: float = 0.0
    avgFillPrice: float = 0.0
    orderId: int = 0


@dataclass
class MockTrade:
    contract: MockContract
    order: MockOrder
    orderStatus: MockOrderStatus


@dataclass
class MockPosition:
    contract: MockContract
    position: float
    avgCost: float
    account: str = 'DU123456'


@dataclass
class MockPortfolioItem:
    contract: MockContract
    position: float
    marketPrice: float
    marketValue: float
    averageCost: float
    unrealizedPNL: float
    realizedPNL: float
    account: str = 'DU123456'


@dataclass
class MockAccountValue:
    tag: str
    val: str
    currency: str
    account: str = 'DU123456'

    # account.py 读取 v.value，而 ib_insync 的 AccountValue 也有 .value 属性
    # 用 property 兼容两种访问方式
    @property
    def value(self) -> str:
        return self.val


# ──────────────────────────────────────────────────────────
#  MockFill / MockExecution：供 reqExecutions() 使用
#  与 confirm_fills.py 中的访问路径一致：
#    f.execution.orderId / f.execution.shares / f.execution.price
#    f.execution.side / f.execution.time / f.contract.symbol
# ──────────────────────────────────────────────────────────

@dataclass
class MockExecution:
    orderId: int
    shares: float
    price: float
    side: str         # 'BOT' or 'SLD'
    time: datetime = field(default_factory=datetime.now)


@dataclass
class MockFill:
    contract: MockContract
    execution: MockExecution


# ──────────────────────────────────────────────────────────
#  MockEvent：模拟 ib_insync Event，支持 += / -= / emit
# ──────────────────────────────────────────────────────────

class MockEvent:
    """模拟 ib_insync Event，支持 += / -= / 调用"""

    def __init__(self) -> None:
        self._handlers: list = []

    def __iadd__(self, fn):
        self._handlers.append(fn)
        return self

    def __isub__(self, fn):
        if fn in self._handlers:
            self._handlers.remove(fn)
        return self

    def emit(self, *args):
        for h in self._handlers:
            h(*args)

    def clear(self):
        self._handlers.clear()


# ──────────────────────────────────────────────────────────
#  MockIB：核心 Mock 对象
# ──────────────────────────────────────────────────────────

class MockIB:
    """
    ib_insync.IB 的完整 Mock 实现。

    设计原则：
    - 所有写操作立即生效（无异步延迟）
    - sleep() 是 no-op，避免测试等待
    - 故障注入通过 inject_* 方法控制，与业务逻辑完全解耦
    """

    def __init__(self) -> None:
        self._connected: bool = True
        self._order_id_counter: int = 1000
        self._positions: list[MockPosition] = []
        self._open_trades: list[MockTrade] = []
        self._all_trades: list[MockTrade] = []
        self._portfolio: list[MockPortfolioItem] = []
        self._account_values: list[MockAccountValue] = []
        self.disconnectedEvent: MockEvent = MockEvent()
        self.RequestTimeout: int = 60

        # ── 故障注入状态 ──────────────────────────────────
        self._qualify_fail_symbols: set[str] = set()
        self._place_fail_symbols: set[str] = set()
        self._partial_fill_ratio: dict[str, float] = {}   # symbol -> 0.0~1.0
        self._disconnect_after_orders: int = -1            # -1 = 永不断线
        self._orders_placed: int = 0
        self._position_sizing_override: dict[str, int] = {}  # symbol -> qty
        self._cancelled: list[MockOrder] = []              # 被 cancelOrder() 撤销的订单

    # ──────────────────────────────────────────────────────
    #  核心 IB 接口
    # ──────────────────────────────────────────────────────

    def qualifyContracts(self, *contracts) -> list:
        """
        验证合约。
        - 若 symbol 在 _qualify_fail_symbols，则抛出 RuntimeError（模拟合约不存在）。
        - 否则为每个合约分配确定性 conId（hash % 10000），返回合约列表。
        """
        result = []
        for contract in contracts:
            symbol = getattr(contract, 'symbol', '')
            if symbol in self._qualify_fail_symbols:
                raise RuntimeError(
                    f"[MockIB] qualifyContracts 失败：{symbol} 合约无法验证"
                )
            # 分配确定性 conId（相同 symbol 始终相同，便于断言）
            contract.conId = abs(hash(symbol)) % 10000
            result.append(contract)
        return result

    def placeOrder(self, contract, order) -> MockTrade:
        """
        下单。
        - 若 symbol 在 _place_fail_symbols，则抛出 RuntimeError。
        - 根据 _partial_fill_ratio 控制成交数量（默认全量成交）。
        - 若 _position_sizing_override 有配置，强制替换 totalQuantity。
        - 触发断线检查（_disconnect_after_orders）。
        """
        symbol = getattr(contract, 'symbol', '')

        if symbol in self._place_fail_symbols:
            raise RuntimeError(
                f"[MockIB] placeOrder 失败：{symbol} 下单被拒绝"
            )

        # 仓位数量注入（模拟 APA 100 股异常等场景）
        if symbol in self._position_sizing_override:
            order.totalQuantity = float(self._position_sizing_override[symbol])

        # 分配订单 ID（若调用方已设置则保留，否则自动分配）
        if not hasattr(order, 'orderId') or order.orderId == 0:
            order.orderId = self._order_id_counter
            self._order_id_counter += 1

        # 计算成交数量
        total_qty = float(order.totalQuantity)
        ratio = self._partial_fill_ratio.get(symbol, 1.0)
        filled_qty = round(total_qty * ratio, 2)
        remaining_qty = total_qty - filled_qty

        # 限价单：avgFillPrice 用 lmtPrice；市价单用 0（模拟未知）
        avg_fill_price = getattr(order, 'lmtPrice', 0.0) if filled_qty > 0 else 0.0

        if filled_qty >= total_qty:
            status = 'Filled'
        elif filled_qty > 0:
            status = 'PartiallyFilled'
        else:
            status = 'Submitted'   # 零成交：报价超限，仍挂单

        order_status = MockOrderStatus(
            status=status,
            filled=filled_qty,
            remaining=remaining_qty,
            avgFillPrice=avg_fill_price,
            orderId=order.orderId,
        )

        # 构造 MockContract（用传入的 contract 属性）
        mock_contract = MockContract(
            symbol=symbol,
            secType=getattr(contract, 'secType', 'STK'),
            exchange=getattr(contract, 'exchange', 'SMART'),
            currency=getattr(contract, 'currency', 'USD'),
            conId=getattr(contract, 'conId', 0),
        )

        trade = MockTrade(
            contract=mock_contract,
            order=order,
            orderStatus=order_status,
        )

        # 已完全成交的单不放入 open_trades；其余放入
        if status != 'Filled':
            self._open_trades.append(trade)
        self._all_trades.append(trade)

        self._orders_placed += 1

        # 断线注入检查
        if (self._disconnect_after_orders > 0
                and self._orders_placed >= self._disconnect_after_orders):
            self.disconnect()

        return trade

    def sleep(self, n) -> None:
        """测试中不实际等待，立即返回。"""
        pass

    def positions(self) -> list[MockPosition]:
        return self._positions

    def openTrades(self) -> list[MockTrade]:
        """返回状态不为 Filled / Cancelled 的挂单。"""
        return [
            t for t in self._open_trades
            if t.orderStatus.status not in ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')
        ]

    def cancelOrder(self, order) -> None:
        """撤销指定 order，将 trade status 设为 Cancelled，并从 open_trades 移除。
        同时记录到 _cancelled（便于测试断言 "哪些单被撤了"）。
        """
        order_id = getattr(order, 'orderId', None)
        for trade in list(self._open_trades):
            if trade.order.orderId == order_id:
                trade.orderStatus.status = 'Cancelled'
                self._open_trades.remove(trade)
                self._cancelled.append(order)
                return

    def reqExecutions(self, filter=None) -> list[MockFill]:
        """
        返回所有已成交订单的 MockFill 列表。
        与 confirm_fills.py 的访问路径兼容：
          f.execution.orderId / shares / price / side / time
          f.contract.symbol
        """
        fills = []
        for trade in self._all_trades:
            filled = trade.orderStatus.filled
            if filled <= 0:
                continue
            side = 'BOT' if trade.order.action == 'BUY' else 'SLD'
            execution = MockExecution(
                orderId=trade.order.orderId,
                shares=filled,
                price=trade.orderStatus.avgFillPrice,
                side=side,
                time=datetime.now(),
            )
            fills.append(MockFill(
                contract=trade.contract,
                execution=execution,
            ))
        return fills

    def trades(self) -> list[MockTrade]:
        """返回当前 session 所有订单（含已取消、已成交）。"""
        return self._all_trades

    def isConnected(self) -> bool:
        return self._connected

    def managedAccounts(self) -> list[str]:
        return ['DU123456']

    def portfolio(self) -> list[MockPortfolioItem]:
        return self._portfolio

    def accountValues(self) -> list[MockAccountValue]:
        return self._account_values

    def disconnect(self) -> None:
        self._connected = False
        self.disconnectedEvent.emit()

    def reconnect(self) -> None:
        self._connected = True

    # ──────────────────────────────────────────────────────
    #  故障注入 API
    # ──────────────────────────────────────────────────────

    def set_positions(self, positions: list[MockPosition]) -> None:
        """设置持仓状态（替换全量）。"""
        self._positions = list(positions)

    def set_account_values(
        self,
        net_liq: float,
        cash: float,
        buying_power: float,
    ) -> None:
        """
        设置账户余额，自动构造 accountValues 列表。
        tag 名与 ib_insync AccountValue.tag 保持一致（account.py 使用）。
        """
        self._account_values = [
            MockAccountValue(tag='NetLiquidation',  val=str(net_liq),    currency='USD'),
            MockAccountValue(tag='TotalCashValue',  val=str(cash),       currency='USD'),
            MockAccountValue(tag='BuyingPower',     val=str(buying_power), currency='USD'),
            MockAccountValue(tag='UnrealizedPnL',   val='0.0',           currency='USD'),
            MockAccountValue(tag='RealizedPnL',     val='0.0',           currency='USD'),
        ]

    def inject_qualify_fail(self, *symbols: str) -> None:
        """让指定 symbol 的 qualifyContracts() 抛出 RuntimeError。"""
        self._qualify_fail_symbols.update(s.upper() for s in symbols)

    def inject_place_fail(self, *symbols: str) -> None:
        """让指定 symbol 的 placeOrder() 抛出 RuntimeError。"""
        self._place_fail_symbols.update(s.upper() for s in symbols)

    def inject_partial_fill(self, symbol: str, ratio: float) -> None:
        """
        控制指定 symbol 的成交比例。
          ratio=1.0  → 完全成交（默认）
          ratio=0.5  → 50% 成交（PartiallyFilled）
          ratio=0.0  → 零成交（Submitted，模拟开盘价超限）
        """
        self._partial_fill_ratio[symbol.upper()] = max(0.0, min(1.0, ratio))

    def inject_disconnect_after(self, n_orders: int) -> None:
        """
        在第 n_orders 笔订单提交后自动触发 disconnect()。
        设为 -1 禁用（默认）。
        """
        self._disconnect_after_orders = n_orders
        self._orders_placed = 0   # 重置计数器

    def inject_position_sizing_error(self, symbol: str, override_qty: int) -> None:
        """
        placeOrder 时强制将 totalQuantity 替换为 override_qty。
        用于模拟 APA 100 股异常、数量计算错误等场景。
        """
        self._position_sizing_override[symbol.upper()] = override_qty

    def clear_faults(self) -> None:
        """清除所有故障注入状态，恢复正常行为。"""
        self._qualify_fail_symbols.clear()
        self._place_fail_symbols.clear()
        self._partial_fill_ratio.clear()
        self._position_sizing_override.clear()
        self._disconnect_after_orders = -1
        self._orders_placed = 0
        # 恢复连接（若因注入而断线）
        if not self._connected:
            self.reconnect()

    # ──────────────────────────────────────────────────────
    #  辅助方法（测试断言用）
    # ──────────────────────────────────────────────────────

    def get_trade_for(self, symbol: str) -> MockTrade | None:
        """按 symbol 查找最近一笔 trade（_all_trades 中最后一条匹配）。"""
        for trade in reversed(self._all_trades):
            if trade.contract.symbol == symbol:
                return trade
        return None

    def reset(self) -> None:
        """完整重置 MockIB 到初始状态（每个测试用例 setUp 时调用）。"""
        self._connected = True
        self._order_id_counter = 1000
        self._positions = []
        self._open_trades = []
        self._all_trades = []
        self._portfolio = []
        self._account_values = []
        self._cancelled = []
        self.disconnectedEvent = MockEvent()
        self.clear_faults()

    # ──────────────────────────────────────────────────────
    #  测试辅助：快速构建持仓 / 挂单（不走 placeOrder 流程）
    # ──────────────────────────────────────────────────────

    def add_position(self, symbol: str, qty: float = 100,
                     avg_cost: float = 100.0) -> 'MockPosition':
        """快速添加一笔持仓（绕过下单流程，直接写入 _positions）。"""
        contract = MockContract(symbol=symbol)
        pos = MockPosition(contract=contract, position=qty, avgCost=avg_cost)
        self._positions.append(pos)
        return pos

    def add_open_trade(self, symbol: str, action: str, qty: float = 100,
                       price: float = 100.0, tif: str = 'DAY') -> 'MockTrade':
        """快速添加一笔挂单到 _open_trades（状态 Submitted，不走 placeOrder 流程）。
        用于测试"已有旧单"场景，无需注入 partial_fill_ratio=0 的副作用。
        """
        contract = MockContract(symbol=symbol)
        order = MockOrder(
            orderId=self._order_id_counter,
            action=action,
            totalQuantity=qty,
            orderType='LMT',
            tif=tif,
            lmtPrice=price,
        )
        self._order_id_counter += 1
        order_status = MockOrderStatus(
            status='Submitted',
            filled=0.0,
            remaining=qty,
            avgFillPrice=0.0,
            orderId=order.orderId,
        )
        trade = MockTrade(contract=contract, order=order, orderStatus=order_status)
        self._open_trades.append(trade)
        self._all_trades.append(trade)
        return trade
