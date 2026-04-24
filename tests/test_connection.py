"""
tests/test_connection.py

测试场景：IB 连接断开 + reqAccountUpdates 失败
- 所有 scenario 不依赖真实 IB Gateway / MySQL，完全使用内置 Mock
- 可独立运行：python tests/test_connection.py

测试的真实代码路径：
  core/trading.py   Trading._place()      → qualifyContracts + placeOrder
  core/account.py   Account._get_value()  → accountValues()
  core/connection.py IBConnection._on_disconnected() → disconnectedEvent 回调
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

# ─────────────────────────────────────────────
# 内置 Mock：不依赖 tests/simulator/ 目录
# ─────────────────────────────────────────────

@dataclass
class FakeOrderStatus:
    status: str = 'Submitted'
    filled: float = 0.0
    avgFillPrice: float = 0.0


@dataclass
class FakeOrder:
    orderId: int = 1
    action: str = 'BUY'
    totalQuantity: int = 100
    tif: str = 'DAY'


@dataclass
class FakeContract:
    symbol: str = 'AAPL'
    secType: str = 'STK'
    exchange: str = 'SMART'
    currency: str = 'USD'


@dataclass
class FakeTrade:
    contract: FakeContract = field(default_factory=FakeContract)
    order: FakeOrder = field(default_factory=FakeOrder)
    orderStatus: FakeOrderStatus = field(default_factory=FakeOrderStatus)


@dataclass
class FakeAccountValue:
    tag: str
    value: str
    currency: str = 'USD'
    account: str = 'DU123456'


class DisconnectedEvent:
    """
    ib_insync 风格的事件对象：支持 += / -= 操作符注册/注销回调，
    并支持 emit() 触发所有已注册的回调。
    """
    def __init__(self):
        self._handlers = []
        self.emit_count = 0

    def __iadd__(self, handler):
        if handler not in self._handlers:
            self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass
        return self

    def emit(self, *args, **kwargs):
        self.emit_count += 1
        for h in list(self._handlers):
            h(*args, **kwargs)


class MockIB:
    """
    轻量级 IB Mock，精确模拟 ib_insync.IB 被 Trading / Account 使用到的方法。

    注入点：
      inject_disconnect_after(n)  — 第 n 笔 placeOrder 调用之后将 _connected 设为 False
      inject_qualify_fail(sym)    — qualifyContracts 时对指定 symbol 抛 RuntimeError
      inject_qualify_delay_disconnect(sym) — qualify 调用后断连（模拟超时场景）
      inject_place_fail(sym)      — placeOrder 时对指定 symbol 抛 RuntimeError
      reconnect()                 — 模拟重连成功
    """

    def __init__(self):
        self._connected = True
        self._account_values: list[FakeAccountValue] = []
        self._positions = []
        self._open_trades = []
        self._place_count = 0           # 已成功 placeOrder 次数
        self._disconnect_after: Optional[int] = None  # 第 N 笔后断连
        self._qualify_fail_syms: set[str] = set()
        self._qualify_delay_disconnect_syms: set[str] = set()
        self._place_fail_syms: set[str] = set()
        self.disconnectedEvent = DisconnectedEvent()
        self._placed_trades: list[FakeTrade] = []

    # ── 注入控制 ────────────────────────────────────────────────────────

    def set_account_values(self, net_liq: float, cash: float, buying_power: float):
        self._account_values = [
            FakeAccountValue('NetLiquidation',  str(net_liq),    'USD'),
            FakeAccountValue('TotalCashValue',  str(cash),       'USD'),
            FakeAccountValue('BuyingPower',     str(buying_power), 'USD'),
            FakeAccountValue('UnrealizedPnL',   '0.0',           'USD'),
            FakeAccountValue('RealizedPnL',     '0.0',           'USD'),
        ]

    def inject_disconnect_after(self, n_orders: int):
        """第 n_orders 笔 placeOrder 成功后，触发断连事件"""
        self._disconnect_after = n_orders

    def inject_qualify_fail(self, symbol: str):
        """qualifyContracts 时对该 symbol 抛出 RuntimeError"""
        self._qualify_fail_syms.add(symbol.upper())

    def inject_qualify_delay_disconnect(self, symbol: str):
        """qualifyContracts 返回后立即将连接标记为 False（模拟超时导致断连）"""
        self._qualify_delay_disconnect_syms.add(symbol.upper())

    def inject_place_fail(self, symbol: str):
        """placeOrder 时对该 symbol 抛出 RuntimeError"""
        self._place_fail_syms.add(symbol.upper())

    def reconnect(self):
        """模拟重连成功：恢复 _connected 并重置断连计数器"""
        self._connected = True
        self._place_count = 0
        self._disconnect_after = None

    # ── ib_insync 接口模拟 ──────────────────────────────────────────────

    def isConnected(self) -> bool:
        return self._connected

    def accountValues(self) -> list[FakeAccountValue]:
        return list(self._account_values)

    def positions(self) -> list:
        return list(self._positions)

    def openTrades(self) -> list:
        return list(self._open_trades)

    def qualifyContracts(self, contract: FakeContract) -> list:
        sym = contract.symbol.upper()
        if sym in self._qualify_fail_syms:
            raise RuntimeError(f"qualifyContracts 失败：{sym} 无法解析合约")
        if sym in self._qualify_delay_disconnect_syms:
            # 模拟 qualify 完成后连接已断
            self._connected = False
        return [contract]

    def placeOrder(self, contract: FakeContract, order: FakeOrder) -> FakeTrade:
        sym = contract.symbol.upper()
        if not self._connected:
            raise ConnectionError(f"IB 连接已断开，无法下单 {sym}")
        if sym in self._place_fail_syms:
            raise RuntimeError(f"placeOrder 失败：{sym}")
        # 成功下单
        trade = FakeTrade(
            contract=contract,
            order=order,
            orderStatus=FakeOrderStatus(status='Submitted'),
        )
        self._placed_trades.append(trade)
        self._place_count += 1
        # 检查是否需要在本次成功下单后触发断连
        if (self._disconnect_after is not None and
                self._place_count >= self._disconnect_after):
            self._connected = False
            self.disconnectedEvent.emit()
        return trade

    def cancelOrder(self, order):
        pass

    def sleep(self, secs: float = 1):
        """ib_insync 的 sleep 用于 pump event loop，这里直接跳过"""
        pass

    def managedAccounts(self) -> list[str]:
        return ['DU123456']


class MockDB:
    """最小 Database Mock：只需不报错即可，不验证持久化。"""

    def connect(self): pass
    def close(self): pass

    def save_order(self, **kwargs): pass
    def save_account_snapshot(self, *args, **kwargs): pass
    def save_signals(self, *args, **kwargs): pass
    def get_orders(self, symbol=None, limit=50): return []


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def _make_order(action='BUY', qty=100):
    """创建一个满足 Trading._place 签名的 ib_insync Order-like 对象"""
    from ib_insync import MarketOrder
    return MarketOrder(action, qty, tif='DAY')


def _result(scenario: str, status: str, details: str,
            orders_placed: int = 0, errors: list = None) -> dict:
    return {
        "scenario": scenario,
        "status": status,
        "details": details,
        "orders_placed": orders_placed,
        "errors": errors or [],
    }


# ─────────────────────────────────────────────
# Scenario 1: 下单中途断连
# ─────────────────────────────────────────────

def scenario_connection_drop_mid_order() -> dict:
    """
    构造 3 只股票的买入序列，在第 1 笔 placeOrder 成功后注入断连。
    预期：
      - 第 1 笔 (NVDA) 成功
      - 第 2 笔 (AAPL) 因断连 _place 返回 None
      - mock_ib.isConnected() == False
      - disconnectedEvent 被触发至少 1 次
    """
    from core.trading import Trading

    mock_ib = MockIB()
    mock_ib.set_account_values(60000, 45000, 45000)
    mock_ib.inject_disconnect_after(n_orders=1)

    mock_db = MockDB()
    trader = Trading(mock_ib, db=mock_db)

    errors = []
    results_detail = []

    symbols = ['NVDA', 'AAPL', 'MSFT']
    for sym in symbols:
        order = _make_order('BUY', 10)
        trade = trader._place(sym, order, 'MKT', f'市价买入(DAY)')
        results_detail.append({'symbol': sym, 'trade': trade is not None})

    orders_placed = mock_ib._place_count  # 真实成功到达 placeOrder 的次数

    # 断言：第 1 笔成功
    if not results_detail[0]['trade']:
        errors.append("NVDA (第1笔) 应该成功但返回了 None")

    # 断言：第 2 笔因断连返回 None
    if results_detail[1]['trade']:
        errors.append("AAPL (第2笔) 在断连后应返回 None，但返回了 Trade 对象")

    # 断言：连接状态已变为 False
    if mock_ib.isConnected():
        errors.append("断连注入后 isConnected() 应返回 False")

    # 断言：disconnectedEvent 被触发
    if mock_ib.disconnectedEvent.emit_count < 1:
        errors.append("disconnectedEvent 未被触发")

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"orders_placed={orders_placed}, "
        f"isConnected={mock_ib.isConnected()}, "
        f"disconnectedEvent.emit_count={mock_ib.disconnectedEvent.emit_count}, "
        f"per_symbol={results_detail}"
    )
    return _result("connection_drop_mid_order", status, detail, orders_placed, errors)


# ─────────────────────────────────────────────
# Scenario 2: qualifyContracts 失败
# ─────────────────────────────────────────────

def scenario_qualify_fail() -> dict:
    """
    对 NVDA 注入 qualifyContracts 失败。
    预期：
      - Trading._place('NVDA', ...) 返回 None（内部已 try/except，不对外抛出）
      - AAPL / MSFT 不受影响，正常返回 FakeTrade
    """
    from core.trading import Trading

    mock_ib = MockIB()
    mock_ib.set_account_values(60000, 45000, 45000)
    mock_ib.inject_qualify_fail('NVDA')

    mock_db = MockDB()
    trader = Trading(mock_ib, db=mock_db)

    errors = []

    nvda_trade = trader._place('NVDA', _make_order('BUY', 10), 'MKT', '市价买入(DAY)')
    aapl_trade = trader._place('AAPL', _make_order('BUY', 5),  'MKT', '市价买入(DAY)')
    msft_trade = trader._place('MSFT', _make_order('BUY', 7),  'MKT', '市价买入(DAY)')

    if nvda_trade is not None:
        errors.append("NVDA qualify 失败后 _place 应返回 None，但返回了 Trade 对象")

    if aapl_trade is None:
        errors.append("AAPL 不应受 NVDA qualify 失败影响，但 _place 返回了 None")

    if msft_trade is None:
        errors.append("MSFT 不应受 NVDA qualify 失败影响，但 _place 返回了 None")

    # 连接应仍然正常
    if not mock_ib.isConnected():
        errors.append("qualify 失败不应导致连接断开")

    orders_placed = mock_ib._place_count
    status = "PASS" if not errors else "FAIL"
    detail = (
        f"nvda_trade={'None' if nvda_trade is None else 'Trade'}, "
        f"aapl_trade={'Trade' if aapl_trade else 'None'}, "
        f"msft_trade={'Trade' if msft_trade else 'None'}, "
        f"orders_placed={orders_placed}"
    )
    return _result("qualify_fail_isolation", status, detail, orders_placed, errors)


# ─────────────────────────────────────────────
# Scenario 3: accountValues 返回空
# ─────────────────────────────────────────────

def scenario_account_values_empty() -> dict:
    """
    将 _account_values 清空模拟 reqAccountUpdates 失败。
    预期：
      - Account._get_value('NetLiquidation') 返回 0.0，不 crash
      - 基于 0.0 的 budget_per_pos 计算不抛出异常
    """
    from core.account import Account

    mock_ib = MockIB()
    mock_ib._account_values = []   # 模拟 reqAccountUpdates 失败：空列表

    mock_db = MockDB()
    account = Account(mock_ib, db=mock_db)

    errors = []
    net_liq = None
    budget_per_pos = None
    crash = False

    try:
        net_liq = account._get_value('NetLiquidation')
        cash    = account._get_value('TotalCashValue')

        # 模拟 _execute_inner 中的 budget_per_pos 计算
        POSITION_PCT  = 0.15
        CASH_RESERVE  = 0.10
        MAX_POS       = 6
        n_held        = 0
        min_cash      = net_liq * CASH_RESERVE
        deployable    = max(0.0, cash - min_cash)
        slots         = MAX_POS - n_held
        budget_per_pos = min(
            deployable / max(1, slots),
            net_liq * POSITION_PCT,
        )
    except Exception as e:
        crash = True
        errors.append(f"账户数据为空时发生崩溃：{e}\n{traceback.format_exc()}")

    if not crash:
        if net_liq is None or net_liq != 0.0:
            errors.append(f"accountValues 为空时 _get_value 应返回 0.0，实际={net_liq}")
        if budget_per_pos is None or budget_per_pos != 0.0:
            errors.append(f"net_liq=0 时 budget_per_pos 应为 0.0，实际={budget_per_pos}")

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"net_liq={net_liq}, budget_per_pos={budget_per_pos}, crash={crash}"
    )
    return _result("account_values_empty", status, detail, 0, errors)


# ─────────────────────────────────────────────
# Scenario 4: 重连后继续下单
# ─────────────────────────────────────────────

def scenario_reconnect_and_continue() -> dict:
    """
    inject_disconnect_after(1)：第 1 笔成功后断连。
    调用 mock_ib.reconnect() 模拟重连成功。
    继续下第 2 单，验证成功。
    """
    from core.trading import Trading

    mock_ib = MockIB()
    mock_ib.set_account_values(60000, 45000, 45000)
    mock_ib.inject_disconnect_after(n_orders=1)

    mock_db = MockDB()
    trader = Trading(mock_ib, db=mock_db)

    errors = []

    # 第 1 笔：应成功，随后断连
    trade1 = trader._place('NVDA', _make_order('BUY', 10), 'MKT', '市价买入(DAY)')
    if trade1 is None:
        errors.append("NVDA 第1笔下单应成功，返回了 None")

    # 确认断连
    if mock_ib.isConnected():
        errors.append("第1笔下单后应触发断连，但 isConnected() 仍为 True")

    # 模拟重连
    mock_ib.reconnect()

    if not mock_ib.isConnected():
        errors.append("reconnect() 后 isConnected() 应为 True")

    # 第 2 笔：重连后应成功
    trade2 = trader._place('AAPL', _make_order('BUY', 5), 'MKT', '市价买入(DAY)')
    if trade2 is None:
        errors.append("重连后 AAPL 第2笔下单应成功，返回了 None")

    orders_placed = len(mock_ib._placed_trades)
    status = "PASS" if not errors else "FAIL"
    detail = (
        f"trade1={'Trade' if trade1 else 'None'}, "
        f"reconnect=True, "
        f"trade2={'Trade' if trade2 else 'None'}, "
        f"total_placed={orders_placed}"
    )
    return _result("reconnect_and_continue", status, detail, orders_placed, errors)


# ─────────────────────────────────────────────
# Scenario 5: 连接超时（qualify 调用后断连）
# ─────────────────────────────────────────────

def scenario_qualify_timeout_disconnect() -> dict:
    """
    inject_qualify_delay_disconnect('NVDA')：
    qualifyContracts 执行后立即将 _connected 置为 False，
    模拟 qualify 极慢期间连接超时断开的场景。

    此时 placeOrder 将因 _connected=False 抛出 ConnectionError，
    Trading._place 的 except 块应捕获并返回 None。

    验证：
      - _place 优雅返回 None，不 hang，不对外抛出
      - AAPL（未注入延迟断连）此时 isConnected()=False，
        因此 placeOrder 也会抛 ConnectionError → _place 返回 None
        (这是正确的级联断连行为，验证整体鲁棒性)
    """
    from core.trading import Trading

    mock_ib = MockIB()
    mock_ib.set_account_values(60000, 45000, 45000)
    mock_ib.inject_qualify_delay_disconnect('NVDA')

    mock_db = MockDB()
    trader = Trading(mock_ib, db=mock_db)

    errors = []
    raised = False
    nvda_trade = None

    try:
        nvda_trade = trader._place('NVDA', _make_order('BUY', 10), 'MKT', '市价买入(DAY)')
    except Exception as e:
        raised = True
        errors.append(f"_place 不应向调用方抛出异常，但抛出了：{type(e).__name__}: {e}")

    # 断言 1：_place 应优雅返回 None，不对外抛出
    if raised:
        pass  # 错误已记录
    elif nvda_trade is not None:
        # qualify 后连接已断，placeOrder 会抛 ConnectionError，
        # _place 的 except 捕获 → 返回 None
        errors.append("NVDA qualify 后连接断开，_place 应返回 None，但返回了 Trade 对象")

    # 断言 2：isConnected 应为 False
    if mock_ib.isConnected():
        errors.append("qualify_delay_disconnect 注入后 isConnected() 应为 False")

    # 断言 3：不应有任何订单实际进入 placeOrder（连接已在 qualify 后断）
    if mock_ib._place_count > 0:
        errors.append(f"qualify 后断连，不应有订单到达 placeOrder，但 _place_count={mock_ib._place_count}")

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"nvda_trade={'None' if nvda_trade is None else 'Trade'}, "
        f"raised={raised}, "
        f"isConnected={mock_ib.isConnected()}, "
        f"place_count={mock_ib._place_count}"
    )
    return _result("qualify_timeout_disconnect", status, detail, mock_ib._place_count, errors)


# ─────────────────────────────────────────────
# 汇总运行
# ─────────────────────────────────────────────

def run_all() -> dict:
    scenarios = [
        scenario_connection_drop_mid_order,
        scenario_qualify_fail,
        scenario_account_values_empty,
        scenario_reconnect_and_continue,
        scenario_qualify_timeout_disconnect,
    ]

    results = []
    for fn in scenarios:
        try:
            r = fn()
        except Exception as e:
            r = _result(
                scenario=fn.__name__,
                status="FAIL",
                details=f"Scenario 本身抛出未捕获异常",
                errors=[f"{type(e).__name__}: {e}", traceback.format_exc()],
            )
        # 实时打印每条结果，便于调试
        print(json.dumps(r, ensure_ascii=False))
        results.append(r)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = len(results) - passed

    report = {
        "test_class": "connection_drops",
        "results": results,
        "passed": passed,
        "failed": failed,
        "exit_code": 0 if failed == 0 else 1,
    }
    return report


if __name__ == '__main__':
    report = run_all()
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report['failed'] == 0 else 1)
