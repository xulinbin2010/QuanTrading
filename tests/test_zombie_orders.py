"""
tests/test_zombie_orders.py

僵尸订单清理 + 成交确认测试场景。

测试对象：
  - confirm_fills._fetch_ib_data / run 核心逻辑（通过内联函数测试，绕过 IB 连接）
  - auto_trader._execute_inner 中 existing_orders / _cancel_existing 逻辑

运行：
    python -m tests.test_zombie_orders
    python -m pytest tests/test_zombie_orders.py -v
"""

from __future__ import annotations

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import unittest
import unittest.mock as mock
from collections import defaultdict
from datetime import datetime, timedelta

# ──────────────────────────────────────────────────────────
#  simulator 导入
# ──────────────────────────────────────────────────────────
from tests.simulator.mock_ib import (
    MockIB,
    MockTrade,
    MockOrder,
    MockContract,
    MockOrderStatus,
    MockFill,
    MockExecution,
)
from tests.simulator.mock_db import MockDatabase as MockDB
from tests.simulator.fixtures import SAMPLE_ORDERS_6M


# ──────────────────────────────────────────────────────────
#  confirm_fills 核心逻辑内联（绕过 IB 连接 / DB 连接）
#
#  完全复制 confirm_fills._fetch_ib_data 的处理逻辑，
#  接受 MockIB 实例直接调用。
# ──────────────────────────────────────────────────────────

def _fetch_ib_data_mock(ib: MockIB) -> tuple[dict, dict]:
    """
    模拟 confirm_fills._fetch_ib_data，直接接受 MockIB。
    不调用 ib.sleep()，不依赖 ExecutionFilter。

    返回 (fill_map, trade_map)，与生产代码格式完全一致。
    """
    fills = ib.reqExecutions()

    agg: dict[int, dict] = defaultdict(
        lambda: {'shares': 0.0, 'cost': 0.0, 'symbol': '', 'side': '', 'time': None}
    )
    for f in fills:
        oid = f.execution.orderId
        agg[oid]['shares'] += f.execution.shares
        agg[oid]['cost']   += f.execution.shares * f.execution.price
        agg[oid]['symbol']  = f.contract.symbol
        agg[oid]['side']    = f.execution.side
        agg[oid]['time']    = f.execution.time

    fill_map: dict[int, dict] = {}
    for oid, d in agg.items():
        fill_map[oid] = {
            'shares':    d['shares'],
            'avg_price': d['cost'] / d['shares'] if d['shares'] else 0,
            'symbol':    d['symbol'],
            'side':      d['side'],
            'time':      d['time'],
        }

    trade_map = {t.order.orderId: t for t in ib.trades()}
    return fill_map, trade_map


def _run_confirm_fills(
    pending_orders: list[tuple],
    fill_map: dict,
    trade_map: dict,
    db: MockDB,
    warning_log: list[str],
) -> dict:
    """
    模拟 confirm_fills.run() 的核心 for 循环。

    参数：
      pending_orders  get_pending_orders 返回的 tuple 列表
      fill_map        _fetch_ib_data 返回的 fill_map
      trade_map       _fetch_ib_data 返回的 trade_map
      db              MockDB 实例
      warning_log     收集 warning 消息的列表

    返回统计 dict：
      {'filled': int, 'partial': int, 'cancelled': int, 'unfilled': int}
    """
    filled_count    = 0
    partial_count   = 0
    cancelled_count = 0
    unfilled_count  = 0

    for row in pending_orders:
        db_id, symbol, action, order_type, qty, limit_price, order_id = row

        # ① reqExecutions 路径
        fill = fill_map.get(order_id)
        if fill:
            filled_qty = fill['shares']
            remainder  = int(qty) - int(filled_qty)
            is_partial = remainder > 0 and int(filled_qty) > 0
            status     = 'PartialFill' if is_partial else 'Filled'
            db.update_order_fill(order_id, fill['avg_price'], filled_qty, status)
            filled_count += 1
            if is_partial:
                partial_count += 1
            continue

        # ② ib.trades() 备用路径
        trade = trade_map.get(order_id)
        if trade:
            status     = trade.orderStatus.status
            filled_qty = trade.orderStatus.filled
            avg_price  = trade.orderStatus.avgFillPrice

            if status == 'Filled':
                db.update_order_fill(order_id, avg_price, filled_qty, 'Filled')
                filled_count += 1
            elif status in ('Cancelled', 'ApiCancelled', 'Inactive'):
                db.update_order_fill(order_id, None, 0, status)
                cancelled_count += 1
            else:
                warning_log.append(
                    f'[{symbol}] order_id={order_id} 状态仍为 {status}，稍后再确认'
                )
                unfilled_count += 1
            continue

        # ③ 两个来源都找不到
        warning_log.append(
            f'[{symbol}] order_id={order_id} 在 IB 中未找到'
        )
        unfilled_count += 1

    return {
        'filled':    filled_count,
        'partial':   partial_count,
        'cancelled': cancelled_count,
        'unfilled':  unfilled_count,
    }


def _detect_zombie_orders(
    open_trades: list[MockTrade],
    tif: str,
    cutoff_hours: int = 24,
) -> list[MockTrade]:
    """
    从 openTrades() 结果中识别"僵尸订单"：
    Submitted 状态 + 提交时间超过 cutoff_hours 小时。

    由于 MockTrade 本身不携带提交时间戳，此函数接受一个
    submission_time_map: {orderId: datetime}，如未提供则对
    所有 Submitted 单视为僵尸（模拟"今日非交易时段"场景）。
    """
    zombies = []
    for trade in open_trades:
        if trade.order.tif != tif:
            continue
        if trade.orderStatus.status == 'Submitted':
            zombies.append(trade)
    return zombies


def _build_existing_orders(
    open_trades: list[MockTrade],
    tif: str,
) -> dict[str, dict[str, MockTrade]]:
    """
    模拟 auto_trader._execute_inner 中 existing_orders 构建逻辑。
    返回 {symbol: {'BUY': trade, ...}} 结构。
    """
    existing: dict[str, dict[str, MockTrade]] = {}
    for t in open_trades:
        if t.order.tif != tif:
            continue
        sym = t.contract.symbol
        act = t.order.action
        existing.setdefault(sym, {})[act] = t
    return existing


def _cancel_existing_from_map(
    existing_orders: dict[str, dict[str, MockTrade]],
    sym: str,
    action: str,
    ib: MockIB,
) -> bool:
    """
    模拟 auto_trader._execute_inner 中的 _cancel_existing 闭包。
    返回是否真正撤销了挂单。
    """
    entry = existing_orders.get(sym, {}).get(action)
    if entry is None:
        return False
    ib.cancelOrder(entry.order)
    return True


# ══════════════════════════════════════════════════════════
#  测试类
# ══════════════════════════════════════════════════════════

class TestZombieOrders(unittest.TestCase):

    TODAY = datetime.now().strftime('%Y-%m-%d')

    def setUp(self) -> None:
        self.ib = MockIB()
        self.db = MockDB()
        self.warnings: list[str] = []

    # ──────────────────────────────────────────────────────
    #  辅助：向 MockIB 中注入已成交订单（模拟跨 session 查询场景）
    # ──────────────────────────────────────────────────────

    def _inject_filled_trade(
        self,
        order_id: int,
        symbol: str,
        action: str,
        qty: float,
        avg_price: float,
        tif: str = 'OPG',
    ) -> MockTrade:
        """直接向 mock_ib._all_trades 插入一笔 Filled 订单。"""
        contract = MockContract(symbol=symbol)
        order    = MockOrder(
            orderId=order_id,
            action=action,
            totalQuantity=qty,
            orderType='LMT',
            tif=tif,
            lmtPrice=avg_price,
        )
        order_status = MockOrderStatus(
            status='Filled',
            filled=qty,
            remaining=0.0,
            avgFillPrice=avg_price,
            orderId=order_id,
        )
        trade = MockTrade(contract=contract, order=order, orderStatus=order_status)
        self.ib._all_trades.append(trade)
        # Filled 单不放入 open_trades
        return trade

    def _inject_submitted_trade(
        self,
        order_id: int,
        symbol: str,
        action: str,
        qty: float,
        lmt_price: float,
        tif: str = 'OPG',
        filled: float = 0.0,
        status: str = 'Submitted',
    ) -> MockTrade:
        """直接向 mock_ib 注入一笔挂单（Submitted / PartiallyFilled）。"""
        contract = MockContract(symbol=symbol)
        order    = MockOrder(
            orderId=order_id,
            action=action,
            totalQuantity=qty,
            orderType='LMT',
            tif=tif,
            lmtPrice=lmt_price,
        )
        order_status = MockOrderStatus(
            status=status,
            filled=filled,
            remaining=qty - filled,
            avgFillPrice=(lmt_price if filled > 0 else 0.0),
            orderId=order_id,
        )
        trade = MockTrade(contract=contract, order=order, orderStatus=order_status)
        self.ib._open_trades.append(trade)
        self.ib._all_trades.append(trade)
        return trade

    # ──────────────────────────────────────────────────────
    #  Scenario 1: 正常成交回写
    # ──────────────────────────────────────────────────────

    def test_scenario1_normal_fill_writeback(self) -> None:
        """
        IB 返回2笔已成交订单，DB 里对应 filled_price=None 的记录
        应被正确更新。
        """
        # ── 预置 IB 成交数据 ──────────────────────
        self._inject_filled_trade(1001, 'NVDA', 'BUY', 50, 480.0)
        self._inject_filled_trade(1002, 'AAPL', 'BUY', 60, 225.0)

        # ── 预置 DB 待确认订单 ────────────────────
        self.db.inject_orders([
            {
                'symbol': 'NVDA', 'action': 'BUY', 'order_type': 'LMT',
                'quantity': 50, 'price': 479.0, 'filled_price': None,
                'status': 'Submitted', 'order_id': 1001,
                'created_at': datetime.now(),
            },
            {
                'symbol': 'AAPL', 'action': 'BUY', 'order_type': 'LMT',
                'quantity': 60, 'price': 224.0, 'filled_price': None,
                'status': 'Submitted', 'order_id': 1002,
                'created_at': datetime.now(),
            },
        ])

        # ── 执行 ──────────────────────────────────
        fill_map, trade_map = _fetch_ib_data_mock(self.ib)
        pending = self.db.get_pending_orders(self.TODAY)
        stats = _run_confirm_fills(pending, fill_map, trade_map, self.db, self.warnings)

        # ── 断言 ──────────────────────────────────
        self.assertEqual(stats['filled'], 2, '应有2笔成交')
        self.assertEqual(stats['unfilled'], 0, '不应有未处理单')

        nvda_row = self.db.get_order_by_id(1001)
        aapl_row = self.db.get_order_by_id(1002)

        self.assertIsNotNone(nvda_row, 'NVDA 订单应存在')
        self.assertIsNotNone(aapl_row, 'AAPL 订单应存在')
        self.assertAlmostEqual(nvda_row.filled_price, 480.0, places=2,
                               msg='NVDA filled_price 应被更新为 480.0')
        self.assertAlmostEqual(aapl_row.filled_price, 225.0, places=2,
                               msg='AAPL filled_price 应被更新为 225.0')
        self.assertEqual(nvda_row.status, 'Filled')
        self.assertEqual(aapl_row.status, 'Filled')

    # ──────────────────────────────────────────────────────
    #  Scenario 2: 僵尸订单检测
    # ──────────────────────────────────────────────────────

    def test_scenario2_zombie_order_detection(self) -> None:
        """
        openTrades() 返回3笔 status='Submitted' 的订单（超过24小时），
        应全部被识别为僵尸。
        """
        # 注入3笔挂单
        self._inject_submitted_trade(2001, 'META', 'BUY',  25, 580.0, tif='OPG')
        self._inject_submitted_trade(2002, 'TSLA', 'BUY',  15, 350.0, tif='OPG')
        self._inject_submitted_trade(2003, 'AMZN', 'SELL', 10, 210.0, tif='OPG')

        open_trades = self.ib.openTrades()
        self.assertEqual(len(open_trades), 3, '应有3笔开放挂单')

        # 今日非交易时段：所有 Submitted 单视为僵尸
        zombies = _detect_zombie_orders(open_trades, tif='OPG')

        self.assertEqual(len(zombies), 3, '应识别出3笔僵尸订单')
        zombie_ids = {z.order.orderId for z in zombies}
        self.assertIn(2001, zombie_ids)
        self.assertIn(2002, zombie_ids)
        self.assertIn(2003, zombie_ids)

    # ──────────────────────────────────────────────────────
    #  Scenario 3: 跨日僵尸 OPG 清理
    # ──────────────────────────────────────────────────────

    def test_scenario3_stale_opg_cancelled_on_new_run(self) -> None:
        """
        前日 OPG 订单今天还在 openTrades()（未成交也未撤）。
        今天再次运行 auto_trader 时，_cancel_existing 应撤销旧 OPG 单。
        """
        # 前日遗留的 OPG 挂单
        self._inject_submitted_trade(3001, 'MSFT', 'BUY', 30, 420.0, tif='OPG')

        open_trades = self.ib.openTrades()
        existing = _build_existing_orders(open_trades, tif='OPG')

        # 验证 MSFT BUY 挂单被识别
        self.assertIn('MSFT', existing)
        self.assertIn('BUY', existing['MSFT'])

        # 执行撤单
        cancelled = _cancel_existing_from_map(existing, 'MSFT', 'BUY', self.ib)

        # 断言
        self.assertTrue(cancelled, '_cancel_existing 应返回 True（实际撤了单）')
        # 撤单后 openTrades 中不应再有该订单
        remaining = self.ib.openTrades()
        remaining_ids = {t.order.orderId for t in remaining}
        self.assertNotIn(3001, remaining_ids, '旧 OPG 单应从 openTrades 中移除')

        # _all_trades 中状态应变为 Cancelled
        all_trades_map = {t.order.orderId: t for t in self.ib._all_trades}
        self.assertEqual(
            all_trades_map[3001].orderStatus.status, 'Cancelled',
            '旧 OPG 单 orderStatus 应变为 Cancelled'
        )

    # ──────────────────────────────────────────────────────
    #  Scenario 4: confirm_fills 找不到对应 order_id
    # ──────────────────────────────────────────────────────

    def test_scenario4_unknown_order_id_no_crash(self) -> None:
        """
        IB 返回成交 order_id=9999，但 DB 里没有这条记录。
        confirm_fills 应记录 warning，不 crash，不更新任何 filled_price。
        """
        # IB 返回 9999 的成交（但 DB 里没有对应记录）
        self._inject_filled_trade(9999, 'GOOGL', 'BUY', 40, 175.0)

        # DB 里有另一笔订单（order_id=8888，与 9999 无关）
        self.db.inject_orders([
            {
                'symbol': 'GOOGL', 'action': 'BUY', 'order_type': 'LMT',
                'quantity': 40, 'price': 174.0, 'filled_price': None,
                'status': 'Submitted', 'order_id': 8888,
                'created_at': datetime.now(),
            },
        ])

        fill_map, trade_map = _fetch_ib_data_mock(self.ib)
        pending = self.db.get_pending_orders(self.TODAY)

        # pending 里只有 8888，fill_map 里只有 9999 → 匹配不上
        stats = _run_confirm_fills(pending, fill_map, trade_map, self.db, self.warnings)

        # 不应 crash，unfilled=1，warnings 里有提示
        self.assertEqual(stats['unfilled'], 1, '找不到匹配应计入 unfilled')
        self.assertEqual(len(self.warnings), 1, '应记录1条 warning')
        self.assertIn('8888', self.warnings[0], 'warning 应包含 order_id=8888')

        # 8888 的 filled_price 不应被改动
        row = self.db.get_order_by_id(8888)
        self.assertIsNone(row.filled_price, '未找到匹配时 filled_price 不应被修改')

    # ──────────────────────────────────────────────────────
    #  Scenario 5: 部分成交订单跨日，触发撤旧换新
    # ──────────────────────────────────────────────────────

    def test_scenario5_partial_fill_triggers_cancel_existing(self) -> None:
        """
        昨天 MSFT 下 OPG 30股，成交15股（PartiallyFilled），
        今天 openTrades() 还返回这笔单（剩余15股）。
        今天再次运行时，检测到 MSFT 有 BUY 挂单，触发撤旧换新。
        """
        # 注入 PartiallyFilled 挂单（15/30 成交）
        self._inject_submitted_trade(
            5001, 'MSFT', 'BUY', 30, 420.0,
            tif='OPG', filled=15.0, status='PartiallyFilled',
        )

        open_trades = self.ib.openTrades()
        existing = _build_existing_orders(open_trades, tif='OPG')

        # MSFT BUY 挂单应被检测到
        self.assertIn('MSFT', existing, 'MSFT 应出现在 existing_orders 中')
        self.assertIn('BUY', existing['MSFT'], 'BUY 方向应被检测到')

        msft_trade = existing['MSFT']['BUY']
        self.assertEqual(msft_trade.orderStatus.status, 'PartiallyFilled')
        self.assertEqual(msft_trade.orderStatus.filled, 15.0)

        # 执行撤单（为新单让路）
        cancelled = _cancel_existing_from_map(existing, 'MSFT', 'BUY', self.ib)
        self.assertTrue(cancelled, '部分成交挂单应能被撤销')

        # 撤单后 openTrades 不再包含该单
        remaining_ids = {t.order.orderId for t in self.ib.openTrades()}
        self.assertNotIn(5001, remaining_ids, '撤销后不应出现在 openTrades 中')

    # ──────────────────────────────────────────────────────
    #  Scenario 6: reqExecutions 返回空（无成交）
    # ──────────────────────────────────────────────────────

    def test_scenario6_empty_executions_no_update(self) -> None:
        """
        reqExecutions() 返回空列表（今日无成交），
        confirm_fills 核心逻辑不应更新任何 filled_price，且不 crash。
        """
        # DB 里有2笔待确认订单
        self.db.inject_orders([
            {
                'symbol': 'NVDA', 'action': 'BUY', 'order_type': 'LMT',
                'quantity': 50, 'price': 480.0, 'filled_price': None,
                'status': 'Submitted', 'order_id': 6001,
                'created_at': datetime.now(),
            },
            {
                'symbol': 'AAPL', 'action': 'BUY', 'order_type': 'LMT',
                'quantity': 60, 'price': 225.0, 'filled_price': None,
                'status': 'Submitted', 'order_id': 6002,
                'created_at': datetime.now(),
            },
        ])

        # MockIB 里没有任何成交记录 → reqExecutions 返回 []
        fill_map, trade_map = _fetch_ib_data_mock(self.ib)
        self.assertEqual(len(fill_map), 0, 'fill_map 应为空')
        self.assertEqual(len(trade_map), 0, 'trade_map 应为空')

        pending = self.db.get_pending_orders(self.TODAY)
        self.assertEqual(len(pending), 2, '应有2笔待确认订单')

        # 执行（不应 crash）
        stats = _run_confirm_fills(pending, fill_map, trade_map, self.db, self.warnings)

        # 断言：没有任何成交被回写
        self.assertEqual(stats['filled'], 0, '无成交时 filled 应为 0')
        self.assertEqual(self.db.update_call_count(), 0, 'update_order_fill 不应被调用')

        # 两笔订单的 filled_price 仍应为 None
        row6001 = self.db.get_order_by_id(6001)
        row6002 = self.db.get_order_by_id(6002)
        self.assertIsNone(row6001.filled_price)
        self.assertIsNone(row6002.filled_price)

    # ──────────────────────────────────────────────────────
    #  Scenario 7: 重复 confirm（幂等性）
    # ──────────────────────────────────────────────────────

    def test_scenario7_idempotent_confirm(self) -> None:
        """
        某订单已经有 filled_price=480.0，再次运行 confirm_fills：
        1. get_pending_orders 不会返回已确认的订单（filled_price 不为 None）
        2. 即使 IB 再次返回该成交，DB 里的 filled_price 不会被错误覆盖
        """
        # 订单已确认（filled_price=480.0）
        self.db.inject_orders([
            {
                'symbol': 'NVDA', 'action': 'BUY', 'order_type': 'LMT',
                'quantity': 50, 'price': 479.0, 'filled_price': 480.0,
                'status': 'Filled', 'order_id': 7001,
                'created_at': datetime.now(),
            },
        ])

        # IB 仍然返回这笔成交（模拟 IB 端缓存未清）
        self._inject_filled_trade(7001, 'NVDA', 'BUY', 50, 480.0)

        fill_map, trade_map = _fetch_ib_data_mock(self.ib)

        # 关键：get_pending_orders 过滤掉 filled_price 不为 None 的订单
        pending = self.db.get_pending_orders(self.TODAY)
        self.assertEqual(len(pending), 0, '已确认订单不应出现在 pending 列表中')

        # 执行（pending 为空，不进入循环）
        stats = _run_confirm_fills(pending, fill_map, trade_map, self.db, self.warnings)

        # update_order_fill 不应被调用
        self.assertEqual(stats['filled'], 0)
        self.assertEqual(self.db.update_call_count(), 0, 'update_order_fill 不应被调用')

        # filled_price 仍为原始值 480.0
        row = self.db.get_order_by_id(7001)
        self.assertAlmostEqual(
            row.filled_price, 480.0, places=2,
            msg='重复 confirm 不应覆盖已有 filled_price'
        )


# ══════════════════════════════════════════════════════════
#  __main__ 入口：收集结果并输出 JSON 报告
# ══════════════════════════════════════════════════════════

def _run_suite_and_report() -> int:
    """
    运行测试套件，以 JSON 格式输出测试报告。
    返回 exit code（0=全部通过，1=有失败）。
    """
    loader = unittest.TestLoader()
    suite  = loader.loadTestsFromTestCase(TestZombieOrders)

    # 收集每条用例结果
    results_detail: list[dict] = []
    passed = 0
    failed = 0

    class _Collector(unittest.TextTestResult):
        def __init__(self, stream, descriptions, verbosity):
            super().__init__(stream, descriptions, verbosity)
            self._case_results: list[dict] = []

        def addSuccess(self, test):
            super().addSuccess(test)
            self._case_results.append({'name': test._testMethodName, 'status': 'passed'})

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self._case_results.append({
                'name':   test._testMethodName,
                'status': 'failed',
                'detail': self._exc_info_to_string(err, test),
            })

        def addError(self, test, err):
            super().addError(test, err)
            self._case_results.append({
                'name':   test._testMethodName,
                'status': 'error',
                'detail': self._exc_info_to_string(err, test),
            })

    import io
    stream = io.StringIO()
    runner = unittest.TextTestRunner(
        stream=stream,
        resultclass=_Collector,
        verbosity=2,
    )
    result = runner.run(suite)

    for item in result._case_results:
        results_detail.append(item)
        if item['status'] == 'passed':
            passed += 1
        else:
            failed += 1

    exit_code = 0 if failed == 0 else 1

    report = {
        'test_class': 'zombie_orders',
        'results':    results_detail,
        'passed':     passed,
        'failed':     failed,
        'exit_code':  exit_code,
    }

    print(json.dumps(report, ensure_ascii=False))
    return exit_code


if __name__ == '__main__':
    sys.exit(_run_suite_and_report())
