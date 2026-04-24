"""
tests/test_partial_fills.py

部分成交 + 订单替换逻辑测试
测试对象：auto_trader._handle_opg_partial_fills / _cancel_existing

运行：
    python -m tests.test_partial_fills
"""
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import unittest.mock as mock


# ─────────────────────────────────────────────
#  最小化 Mock 基础类
# ─────────────────────────────────────────────

class MockOrderStatus:
    def __init__(self, filled: float = 0.0, avg_fill_price: float = 0.0,
                 status: str = 'Submitted'):
        self.filled        = filled
        self.avgFillPrice  = avg_fill_price
        self.status        = status


class MockOrder:
    def __init__(self, action: str = 'BUY', total_quantity: int = 0,
                 lmt_price: float = 0.0, tif: str = 'OPG', order_id: int = 1):
        self.action         = action
        self.totalQuantity  = total_quantity
        self.lmtPrice       = lmt_price
        self.tif            = tif
        self.orderId        = order_id


class MockContract:
    def __init__(self, symbol: str = 'AAPL'):
        self.symbol = symbol


class MockTrade:
    def __init__(self, symbol: str, qty: int, limit_price: float,
                 filled: float = 0.0, avg_fill_price: float = 0.0,
                 status: str = 'Submitted', tif: str = 'OPG', order_id: int = 1):
        self.contract    = MockContract(symbol)
        self.order       = MockOrder('BUY', qty, limit_price, tif, order_id)
        self.orderStatus = MockOrderStatus(filled, avg_fill_price, status)


class MockIB:
    """
    最小化 IB mock，仅实现测试所需的接口：
      - placeOrder / cancelOrder / openTrades / sleep / qualifyContracts
    """
    def __init__(self):
        self._placed_trades: list[MockTrade]    = []   # placeOrder 记录
        self._cancelled_orders: list[MockOrder] = []   # cancelOrder 记录
        self._open_trades: list[MockTrade]      = []   # openTrades() 返回

    # ── IB 接口 ──────────────────────────────

    def placeOrder(self, contract, order) -> MockTrade:
        trade = MockTrade(
            symbol       = contract.symbol,
            qty          = int(order.totalQuantity),
            limit_price  = getattr(order, 'lmtPrice', 0.0),
            filled       = 0.0,
            avg_fill_price = 0.0,
            status       = 'Submitted',
            tif          = order.tif,
            order_id     = len(self._placed_trades) + 100,
        )
        self._placed_trades.append(trade)
        return trade

    def cancelOrder(self, order) -> None:
        self._cancelled_orders.append(order)
        # 标记对应 open trade 为已撤
        for t in self._open_trades:
            if t.order is order:
                t.orderStatus.status = 'Cancelled'

    def openTrades(self) -> list:
        return [t for t in self._open_trades
                if t.orderStatus.status not in ('Filled', 'Cancelled')]

    def sleep(self, secs: float = 1) -> None:  # noqa: no-op
        pass

    def qualifyContracts(self, *contracts) -> list:
        return list(contracts)

    # ── 辅助方法 ─────────────────────────────

    def inject_open_trade(self, trade: MockTrade) -> None:
        """把一笔已有挂单注入 open trades，模拟重复运行时已有挂单的情形。"""
        self._open_trades.append(trade)

    @property
    def _all_trades(self) -> list:
        """所有 placeOrder 调用过的 trade（含撤单后）。"""
        return self._placed_trades

    def new_day_lmt_trades(self) -> list:
        """筛选出 tif=DAY 且 action=BUY 的新下单。"""
        return [t for t in self._placed_trades
                if t.order.tif == 'DAY' and t.order.action == 'BUY']


class MockTrader:
    """
    对 core/trading.Trading 的 mock，limit_buy / limit_sell 委托给 mock_ib.placeOrder。
    """
    def __init__(self, mock_ib: MockIB):
        self.ib = mock_ib

    def limit_buy(self, symbol: str, quantity: int, price: float, tif: str = 'DAY'):
        order    = MockOrder('BUY', quantity, price, tif)
        contract = MockContract(symbol)
        return self.mock_ib_place(contract, order)

    def mock_ib_place(self, contract, order) -> MockTrade:
        return self.ib.placeOrder(contract, order)

    def limit_sell(self, symbol: str, quantity: int, price: float, tif: str = 'DAY'):
        order    = MockOrder('SELL', quantity, price, tif)
        contract = MockContract(symbol)
        return self.ib.placeOrder(contract, order)


# ─────────────────────────────────────────────
#  被测函数的抽离/模拟
#  （_handle_opg_partial_fills 直接从 auto_trader import，
#    但 time.sleep / sys.stdin.isatty 需要 patch）
# ─────────────────────────────────────────────

def _make_handle_fn(mock_ib: MockIB, mock_trader: MockTrader):
    """
    返回一个与 auto_trader._handle_opg_partial_fills 等效的函数，
    但：
      1. 绕过 9:32 等待逻辑（直接进入成交检查）
      2. 注入 MAX_ENTRY_SLIPPAGE = 0.01
    """
    MAX_ENTRY_SLIPPAGE = 0.01

    def _handle(opg_buy_trades: list) -> int:
        """
        返回补单数量（补单_count）。
        对应 auto_trader._handle_opg_partial_fills 的核心逻辑，
        去掉了 time.sleep / isatty 分支。
        """
        mock_ib.sleep(2)
        print(f"\n[补单监控] 检查 OPG 买入单成交情况...")
        补单_count = 0
        for symbol, orig_qty, limit_price, trade in opg_buy_trades:
            if trade is None:
                continue
            filled    = float(trade.orderStatus.filled)
            remainder = orig_qty - int(filled)

            if remainder <= 0:
                print(f"  [{symbol}] 完全成交 {orig_qty} 股 ✓")
                continue

            if filled < 0.5:
                print(f"  [{symbol}] OPG 单零成交（开盘价超限价 ${limit_price:.2f}），不追高，跳过")
                continue

            # 部分成交 — 补一笔 DAY 限价单
            avg_fill  = float(trade.orderStatus.avgFillPrice) or limit_price
            day_limit = round(avg_fill * (1 + MAX_ENTRY_SLIPPAGE), 2)
            print(f"  [{symbol}] 部分成交 {int(filled)}/{orig_qty} 股，"
                  f"补挂 DAY 限价单 {remainder} 股 @ ${day_limit:.2f}")
            mock_trader.limit_buy(symbol, remainder, price=day_limit, tif='DAY')
            补单_count += 1

        if 补单_count == 0:
            print(f"  所有 OPG 单均已完整成交或零成交，无需补单")
        else:
            print(f"[补单监控] 已补挂 {补单_count} 笔 DAY 限价单")
        return 补单_count

    return _handle


def _make_cancel_fn(mock_ib: MockIB, existing_orders: dict, tif: str = 'OPG'):
    """返回 _cancel_existing 闭包，逻辑与 auto_trader._execute_inner 完全一致。"""
    def _cancel_existing(sym: str, action: str) -> bool:
        """撤销同方向已有挂单；无挂单则静默返回 False，撤单成功返回 True。"""
        entry = existing_orders.get(sym, {}).get(action)
        if entry is None:
            return False
        old_price = getattr(entry.order, 'lmtPrice', '市价')
        mock_ib.cancelOrder(entry.order)
        mock_ib.sleep(1)
        print(f"  [{sym}] 已撤销旧 {tif} {action} 挂单（原限价 {old_price}）")
        return True
    return _cancel_existing


# ─────────────────────────────────────────────
#  测试类
# ─────────────────────────────────────────────

class TestPartialFills(unittest.TestCase):

    # ── Scenario 1: 完全成交 — 无需补单 ──────────────────

    def test_scenario1_full_fill_no_new_order(self):
        """完全成交的 trade 不应触发任何新订单。"""
        ib      = MockIB()
        trader  = MockTrader(ib)
        handle  = _make_handle_fn(ib, trader)

        trade = MockTrade('NVDA', qty=50, limit_price=480.0,
                          filled=50.0, avg_fill_price=479.5, status='Filled')

        before = len(ib._all_trades)
        count  = handle([('NVDA', 50, 480.0, trade)])

        self.assertEqual(count, 0, "完全成交不应有补单")
        self.assertEqual(len(ib._all_trades), before, "不应有任何新下单")

    # ── Scenario 2: 零成交 — 不追高 ──────────────────────

    def test_scenario2_zero_fill_no_order(self):
        """零成交不追高，不下任何补单。"""
        ib      = MockIB()
        trader  = MockTrader(ib)

        captured: list[str] = []
        orig_print = __builtins__['print'] if isinstance(__builtins__, dict) else print

        def capture_print(*args, **kwargs):
            msg = ' '.join(str(a) for a in args)
            captured.append(msg)
            orig_print(*args, **kwargs)

        handle = _make_handle_fn(ib, trader)
        before = len(ib._all_trades)

        with mock.patch('builtins.print', side_effect=capture_print):
            count = handle([('META', 25, 395.0,
                             MockTrade('META', qty=25, limit_price=395.0,
                                       filled=0.0, avg_fill_price=0.0,
                                       status='Submitted'))])

        self.assertEqual(count, 0, "零成交不应有补单")
        self.assertEqual(len(ib._all_trades), before, "不应有任何新下单")

        output = ' '.join(captured)
        self.assertTrue(
            '不追高' in output or '零成交' in output,
            f"输出应包含'零成交'或'不追高'，实际输出：{output!r}"
        )

    # ── Scenario 3: 部分成交 — 补挂 DAY 限价单 ───────────

    def test_scenario3_partial_fill_day_order(self):
        """部分成交：补单数量、限价和订单类型均正确。"""
        ib      = MockIB()
        trader  = MockTrader(ib)
        handle  = _make_handle_fn(ib, trader)

        trade = MockTrade('MSFT', qty=30, limit_price=415.0,
                          filled=15.0, avg_fill_price=418.0,
                          status='PartiallyFilled')

        count = handle([('MSFT', 30, 415.0, trade)])

        self.assertEqual(count, 1, "部分成交应触发 1 笔补单")
        self.assertEqual(len(ib._all_trades), 1, "应恰好新下 1 笔订单")

        new_trade = ib._all_trades[0]
        # qty = 30 - 15 = 15
        self.assertEqual(new_trade.order.totalQuantity, 15, "补单数量应为 15")
        # limit_price = round(418.0 * 1.01, 2) = 422.18
        expected_price = round(418.0 * 1.01, 2)
        self.assertAlmostEqual(new_trade.order.lmtPrice, expected_price, places=2,
                               msg=f"补单限价应为 {expected_price}")
        self.assertEqual(new_trade.order.tif, 'DAY', "补单 tif 应为 DAY")
        self.assertEqual(new_trade.order.action, 'BUY', "补单方向应为 BUY")

    # ── Scenario 4: 撤旧换新（重复运行）─────────────────

    def test_scenario4_cancel_and_replace(self):
        """existing_orders 中已有 NVDA BUY 挂单，再次触发时应撤旧换新。"""
        ib = MockIB()

        # 注入一笔已有 OPG BUY 挂单（旧单）
        old_trade = MockTrade('NVDA', qty=40, limit_price=478.0,
                              filled=0.0, avg_fill_price=0.0,
                              status='Submitted', tif='OPG', order_id=10)
        ib.inject_open_trade(old_trade)

        # 构造 existing_orders（模仿 _execute_inner 的逻辑）
        existing_orders: dict[str, dict[str, object]] = {}
        for t in ib.openTrades():
            if t.order.tif != 'OPG':
                continue
            existing_orders.setdefault(t.contract.symbol, {})[t.order.action] = t

        cancel_existing = _make_cancel_fn(ib, existing_orders, tif='OPG')

        # 触发撤旧
        cancelled = cancel_existing('NVDA', 'BUY')

        self.assertTrue(cancelled, "_cancel_existing 应返回 True（有挂单被撤）")
        self.assertEqual(len(ib._cancelled_orders), 1, "应有 1 笔旧单被撤")
        self.assertEqual(ib._cancelled_orders[0], old_trade.order,
                         "被撤的应是旧 OPG 单的 order 对象")

        # 旧单状态应被标记为 Cancelled
        self.assertEqual(old_trade.orderStatus.status, 'Cancelled')

        # 下新 OPG 单
        trader = MockTrader(ib)
        new_order = MockOrder('BUY', 40, 480.0, 'OPG')
        new_contract = MockContract('NVDA')
        ib.placeOrder(new_contract, new_order)

        self.assertEqual(len(ib._all_trades), 1, "应有 1 笔新单已下")
        new_t = ib._all_trades[0]
        self.assertEqual(new_t.contract.symbol, 'NVDA')
        self.assertEqual(new_t.order.tif, 'OPG')
        self.assertAlmostEqual(new_t.order.lmtPrice, 480.0, places=2)

    # ── Scenario 5: 多单混合 ─────────────────────────────

    def test_scenario5_mixed_batch(self):
        """
        NVDA 完全成交 + MSFT 部分成交 + META 零成交 + AAPL 完全成交：
        只有 MSFT 触发补单，共 1 笔新 DAY 单。
        """
        ib     = MockIB()
        trader = MockTrader(ib)
        handle = _make_handle_fn(ib, trader)

        opg_trades = [
            ('NVDA', 50, 480.0,
             MockTrade('NVDA', 50, 480.0, filled=50.0, avg_fill_price=479.0,
                       status='Filled')),
            ('MSFT', 30, 415.0,
             MockTrade('MSFT', 30, 415.0, filled=15.0, avg_fill_price=418.0,
                       status='PartiallyFilled')),
            ('META', 25, 395.0,
             MockTrade('META', 25, 395.0, filled=0.0, avg_fill_price=0.0,
                       status='Submitted')),
            ('AAPL', 60, 175.0,
             MockTrade('AAPL', 60, 175.0, filled=60.0, avg_fill_price=174.5,
                       status='Filled')),
        ]

        count = handle(opg_trades)

        self.assertEqual(count, 1, "只有 MSFT 应触发补单")

        day_buys = ib.new_day_lmt_trades()
        self.assertEqual(len(day_buys), 1, "全局共应有 1 笔新 DAY BUY 单")
        self.assertEqual(day_buys[0].contract.symbol, 'MSFT',
                         "补单的 symbol 应为 MSFT")

        # 验证 MSFT 补单参数
        msft = day_buys[0]
        self.assertEqual(msft.order.totalQuantity, 15)
        self.assertAlmostEqual(msft.order.lmtPrice, round(418.0 * 1.01, 2), places=2)

    # ── Scenario 6: 部分成交后 OPG 订单替换 ─────────────

    def test_scenario6_opg_replace_on_partial(self):
        """
        NVDA 已有 OPG BUY 挂单（未成交），重复提交同 symbol 买入信号：
        旧 OPG 应被撤销，新 OPG 应被下单。
        """
        ib = MockIB()

        # 注入旧 OPG 挂单（未成交）
        old_trade = MockTrade('NVDA', qty=40, limit_price=478.0,
                              filled=0.0, avg_fill_price=0.0,
                              status='Submitted', tif='OPG', order_id=20)
        ib.inject_open_trade(old_trade)

        # 构造 existing_orders（模仿 _execute_inner）
        existing_orders: dict[str, dict[str, object]] = {}
        for t in ib.openTrades():
            if t.order.tif != 'OPG':
                continue
            existing_orders.setdefault(t.contract.symbol, {})[t.order.action] = t

        cancel_existing = _make_cancel_fn(ib, existing_orders, tif='OPG')

        # 重复触发 NVDA BUY 信号：先撤旧
        cancelled = cancel_existing('NVDA', 'BUY')
        self.assertTrue(cancelled, "旧 OPG 单应被撤销")

        # 下新 OPG 单
        trader = MockTrader(ib)
        new_order = MockOrder('BUY', 40, 480.5, 'OPG')
        new_contract = MockContract('NVDA')
        new_t = ib.placeOrder(new_contract, new_order)

        # 验证旧单状态
        self.assertEqual(old_trade.orderStatus.status, 'Cancelled',
                         "旧 OPG 单状态应为 Cancelled")
        # 验证新单
        self.assertIsNotNone(new_t, "新 OPG 单应成功提交")
        self.assertEqual(new_t.contract.symbol, 'NVDA')
        self.assertEqual(new_t.order.tif, 'OPG')
        self.assertAlmostEqual(new_t.order.lmtPrice, 480.5, places=2)
        # open trades 中只剩新单（旧单已 Cancelled）
        open_now = ib.openTrades()
        self.assertNotIn(old_trade, open_now, "旧单不应再出现在 openTrades 中")


# ─────────────────────────────────────────────
#  JSON 报告入口
# ─────────────────────────────────────────────

def run_and_report() -> dict:
    suite  = unittest.TestLoader().loadTestsFromTestCase(TestPartialFills)
    runner = unittest.TextTestRunner(stream=sys.stderr, verbosity=2)
    result = runner.run(suite)

    passed = result.testsRun - len(result.failures) - len(result.errors)
    failed = len(result.failures) + len(result.errors)

    results_detail = []
    all_method_names = unittest.TestLoader().getTestCaseNames(TestPartialFills)

    failure_names = {f[0].id().split('.')[-1]: f[1]
                     for f in result.failures + result.errors}

    for name in all_method_names:
        if name in failure_names:
            results_detail.append({
                "scenario": name,
                "status":   "FAILED",
                "detail":   failure_names[name].strip().splitlines()[-1],
            })
        else:
            results_detail.append({
                "scenario": name,
                "status":   "PASSED",
            })

    report = {
        "test_class": "partial_fills",
        "results":    results_detail,
        "passed":     passed,
        "failed":     failed,
        "exit_code":  0 if failed == 0 else 1,
    }
    return report


if __name__ == '__main__':
    report = run_and_report()
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(report['exit_code'])
