"""
仓位计算 + 资金预算异常的测试套件

测试 auto_trader._execute_inner() 中的资金计算逻辑，不连接 IB Gateway 也不访问 DB。
直接提取核心公式并通过 MockIB 验证下单数量。

运行方式：
    python -m tests.test_position_sizing
    python tests/test_position_sizing.py
"""

from __future__ import annotations

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import types
import importlib
import unittest
from unittest.mock import MagicMock, patch

# ── 路径保障：从项目根目录导入 ──────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tests.simulator.mock_ib import (
    MockIB,
    MockPosition,
    MockContract,
    MockAccountValue,
)


# ══════════════════════════════════════════════════════════════
#  纯公式层：与 auto_trader._execute_inner() 第 465-472 行对齐
#  每个测试场景通过这层函数计算预期值，再对照下单数量
# ══════════════════════════════════════════════════════════════

def compute_budget(
    net_liq: float,
    cash: float,
    cash_reserve_pct: float,
    position_pct: float,
    eff_max_pos: int,
    n_held: int,
) -> tuple[float, float, int, float]:
    """
    复刻 _execute_inner() 资金计算逻辑，返回 (min_cash, deployable, slots, budget_per_pos)。
    """
    min_cash = net_liq * cash_reserve_pct
    deployable = max(0.0, cash - min_cash)
    slots = eff_max_pos - n_held
    budget_per_pos = min(
        deployable / max(1, slots),
        net_liq * position_pct,
    )
    return min_cash, deployable, slots, budget_per_pos


def expected_qty(budget_per_pos: float, price: float) -> int:
    """当无 ATR 数据时，使用固定比例计算股数（同 auto_trader.py 第 770 行）。"""
    return int(budget_per_pos / price)


# ══════════════════════════════════════════════════════════════
#  辅助：构造最小化可调用的 _execute_inner 环境
#
#  策略：对 _execute_inner 做手术级 patch，只运行资金计算 + 买入下单
#  部分，跳过 IB 连接、DB、DataStore、earnings、sector_info 等外部依赖。
# ══════════════════════════════════════════════════════════════

def _make_fake_config(
    max_positions: int = 6,
    position_pct: float = 0.15,
    cash_reserve_pct: float | None = None,
    mss_bull_max_pos: int = 6,
    mss_bull_threshold: float = 0.5,
    mss_bear_threshold: float = 0.0,
    mss_bear_max_pos: int = 4,
    target_risk_per_pos: float = 0.03,
    atr_stop_multiplier: float = 2.5,
    atr_stop_floor: float = -0.20,
    stop_loss_pct: float = -0.15,
    trail_stop_activate_pct: float = 0.08,
    trail_stop_pct: float = -0.07,
    max_per_sector: int = 3,
    max_entry_slippage: float = 0.01,
    earnings_avoid_days: int = 0,
    breadth_max_pos: int = 4,
    mss_bull_trail_activate: float = 0.08,
    mss_bull_trail_pct: float = -0.07,
    mss_bear_trail_activate: float = 0.05,
    mss_bear_trail_pct: float = -0.05,
    time_stop_days: int = 0,
    time_stop_min_return: float = 0.05,
    atm_stop_multiplier: float = 2.5,
) -> types.ModuleType:
    """返回一个最小化 config 模块替代，避免 DB 连接。"""
    cfg = types.ModuleType('_fake_config')
    if cash_reserve_pct is None:
        cash_reserve_pct = max(0.0, 1.0 - max_positions * position_pct)
    cfg.MAX_POSITIONS = max_positions
    cfg.POSITION_PCT = position_pct
    cfg.CASH_RESERVE_PCT = cash_reserve_pct
    cfg.STOP_LOSS_PCT = stop_loss_pct
    cfg.MAX_PER_SECTOR = max_per_sector
    cfg.TRAIL_STOP_ACTIVATE_PCT = trail_stop_activate_pct
    cfg.TRAIL_STOP_PCT = trail_stop_pct
    cfg.ATR_STOP_MULTIPLIER = atr_stop_multiplier
    cfg.ATR_STOP_FLOOR = atr_stop_floor
    cfg.TARGET_RISK_PER_POS = target_risk_per_pos
    cfg.TIME_STOP_DAYS = time_stop_days
    cfg.TIME_STOP_MIN_RETURN = time_stop_min_return
    cfg.MAX_ENTRY_SLIPPAGE = max_entry_slippage
    cfg.EARNINGS_AVOID_DAYS = earnings_avoid_days
    cfg.BREADTH_MAX_POS = breadth_max_pos
    cfg.MSS_BULL_THRESHOLD = mss_bull_threshold
    cfg.MSS_BEAR_THRESHOLD = mss_bear_threshold
    cfg.MSS_BULL_MAX_POS = mss_bull_max_pos
    cfg.MSS_BULL_TRAIL_ACTIVATE = mss_bull_trail_activate
    cfg.MSS_BULL_TRAIL_PCT = mss_bull_trail_pct
    cfg.MSS_BEAR_MAX_POS = mss_bear_max_pos
    cfg.MSS_BEAR_TRAIL_ACTIVATE = mss_bear_trail_activate
    cfg.MSS_BEAR_TRAIL_PCT = mss_bear_trail_pct
    return cfg


def _make_buy_signal(
    symbol: str,
    price: float,
    sector: str = 'Technology',
    industry: str = 'Semiconductors',
    rs_score: float = 0.05,
    vol_ratio: float = 2.0,
) -> dict:
    return {
        'symbol': symbol,
        'close': price,
        'sector': sector,
        'industry': industry,
        'rs_score': rs_score,
        'vol_ratio': vol_ratio,
    }


# ══════════════════════════════════════════════════════════════
#  轻量执行引擎：直接从 auto_trader._execute_inner 提取资金计算
#  + 下单决策，使用 MockIB 替代真实 IB
# ══════════════════════════════════════════════════════════════

CASH_EQUIV = {'SGOV', 'BIL', 'USFR'}


def run_position_sizing(
    mock_ib: MockIB,
    signals: dict,
    cfg,
    dry_run: bool = False,
) -> dict:
    """
    精简版 _execute_inner 资金计算 + 买入下单流程（无止损/卖出/DataStore/DB 依赖）。

    返回 execution_result 字典，包含：
        budget_per_pos, slots, deployable, orders: list[dict]
    """
    from core.account import Account
    from core.trading import Trading

    account = Account(mock_ib)
    trader = Trading(mock_ib, db=None)

    net_liq = account._get_value('NetLiquidation')
    cash = account._get_value('TotalCashValue')

    positions = {p.contract.symbol: p for p in mock_ib.positions()}

    # 分离现金等价 ETF
    cash_equiv_pos = {s: p for s, p in positions.items() if s in CASH_EQUIV}
    stock_positions = {s: p for s, p in positions.items() if s not in CASH_EQUIV}

    if cash_equiv_pos:
        sgov_value = sum(abs(p.position) * p.avgCost for p in cash_equiv_pos.values())
        cash += sgov_value

    n_held = len(stock_positions)

    # MSS 自适应
    mss = signals.get('_mss', 0.0)
    bull_thr = getattr(cfg, 'MSS_BULL_THRESHOLD', 0.5)
    bear_thr = getattr(cfg, 'MSS_BEAR_THRESHOLD', 0.0)
    if mss >= bull_thr:
        eff_max_pos = getattr(cfg, 'MSS_BULL_MAX_POS', 6)
    elif mss < bear_thr:
        eff_max_pos = getattr(cfg, 'MSS_BEAR_MAX_POS', 4)
    else:
        eff_max_pos = cfg.MAX_POSITIONS

    CASH_RESERVE_PCT = cfg.CASH_RESERVE_PCT
    POSITION_PCT = cfg.POSITION_PCT
    MAX_ENTRY_SLIPPAGE = cfg.MAX_ENTRY_SLIPPAGE

    # 资金计算（对齐 _execute_inner 第 465-472 行）
    min_cash = net_liq * CASH_RESERVE_PCT
    deployable = max(0.0, cash - min_cash)
    slots = eff_max_pos - n_held
    budget_per_pos = min(
        deployable / max(1, slots),
        net_liq * POSITION_PCT,
    )

    buy_list = signals.get('buy', [])
    vix_brake = signals.get('_vix_brake', False)
    breadth_cap = signals.get('_breadth_cap', False)

    if vix_brake:
        buy_list = []
    elif breadth_cap:
        effective_max_pos = min(eff_max_pos, cfg.BREADTH_MAX_POS)
        slots = max(0, effective_max_pos - n_held)

    orders_placed = []
    executed = 0
    sector_counts: dict[str, int] = {}

    for sig in buy_list:
        if executed >= slots:
            break
        symbol = sig['symbol']
        if symbol in stock_positions:
            continue

        sec = sig.get('sector') or 'Unknown'
        if sector_counts.get(sec, 0) >= cfg.MAX_PER_SECTOR:
            continue

        if cfg.EARNINGS_AVOID_DAYS > 0:
            # 测试时不调用 has_upcoming_earnings，假设全部放行
            pass

        # 仓位计算：无 ATR 时用固定比例（对齐 auto_trader.py 第 769-771 行）
        atr14 = signals.get('_atr', {}).get(symbol)
        if atr14 is not None and atr14 > 0:
            target_risk_dollars = net_liq * cfg.TARGET_RISK_PER_POS
            stop_distance = cfg.ATR_STOP_MULTIPLIER * atr14
            qty_by_risk = int(target_risk_dollars / stop_distance)
            qty_by_pct = int(net_liq * POSITION_PCT / sig['close'])
            qty = min(qty_by_risk, qty_by_pct) if qty_by_risk > 0 else qty_by_pct
        else:
            qty = int(budget_per_pos / sig['close'])

        if qty <= 0:
            continue

        limit_price = round(sig['close'] * (1 + MAX_ENTRY_SLIPPAGE), 2)

        if not dry_run:
            from ib_insync import Stock, LimitOrder
            contract = Stock(symbol, 'SMART', 'USD')
            order = LimitOrder('BUY', qty, limit_price, tif='OPG')
            trade = mock_ib.placeOrder(contract, order)
            actual_qty = int(trade.order.totalQuantity)
        else:
            actual_qty = qty
            trade = None

        orders_placed.append({
            'symbol': symbol,
            'expected_qty': qty,
            'actual_qty': actual_qty,
            'limit_price': limit_price,
            'trade': trade,
        })
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        executed += 1

    return {
        'net_liq': net_liq,
        'cash': cash,
        'min_cash': min_cash,
        'deployable': deployable,
        'slots': slots,
        'budget_per_pos': budget_per_pos,
        'n_held': n_held,
        'eff_max_pos': eff_max_pos,
        'orders': orders_placed,
    }


# ══════════════════════════════════════════════════════════════
#  测试类
# ══════════════════════════════════════════════════════════════

class TestPositionSizing(unittest.TestCase):
    """仓位计算 + 资金预算异常测试套件"""

    def setUp(self):
        self.ib = MockIB()
        # 默认 config：MAX_POSITIONS=6, POSITION_PCT=0.15, CASH_RESERVE_PCT=0.10
        self.cfg = _make_fake_config(
            max_positions=6,
            position_pct=0.15,
            cash_reserve_pct=0.10,
        )

    def tearDown(self):
        self.ib.reset()

    # ──────────────────────────────────────────────────────────
    #  Scenario 1: 标准计算正确性
    # ──────────────────────────────────────────────────────────
    def test_scenario1_standard_calculation(self):
        """
        net_liq=60000, cash=45000, n_held=3
        min_cash = 60000 * 0.10 = 6000
        deployable = 45000 - 6000 = 39000
        slots = 6 - 3 = 3
        budget = min(39000/3=13000, 60000*0.15=9000) = 9000
        price=200 → qty = int(9000/200) = 45
        """
        # 配置账户
        self.ib.set_account_values(net_liq=60000, cash=45000, buying_power=45000)

        # 3只持仓（占槽位）
        for sym in ['NVDA', 'AMD', 'META']:
            pos = MockPosition(
                contract=MockContract(symbol=sym),
                position=50.0,
                avgCost=100.0,
            )
            self.ib._positions.append(pos)

        # 买入信号：1只股票，股价200，无ATR
        signals = {
            'buy': [_make_buy_signal('AAPL', price=200.0)],
            '_mss': 0.3,   # 普通区间，eff_max_pos = MAX_POSITIONS = 6
        }

        # 纯公式验证
        min_cash, deployable, slots, budget = compute_budget(
            net_liq=60000, cash=45000,
            cash_reserve_pct=0.10, position_pct=0.15,
            eff_max_pos=6, n_held=3,
        )
        self.assertAlmostEqual(min_cash, 6000.0)
        self.assertAlmostEqual(deployable, 39000.0)
        self.assertEqual(slots, 3)
        self.assertAlmostEqual(budget, 9000.0)
        self.assertEqual(expected_qty(budget, 200.0), 45)

        # 执行引擎验证（dry_run=True，不实际下单，绕过 ib_insync Stock 导入）
        result = run_position_sizing(self.ib, signals, self.cfg, dry_run=True)

        self.assertAlmostEqual(result['min_cash'], 6000.0)
        self.assertAlmostEqual(result['deployable'], 39000.0)
        self.assertEqual(result['slots'], 3)
        self.assertAlmostEqual(result['budget_per_pos'], 9000.0)
        self.assertEqual(len(result['orders']), 1)
        self.assertEqual(result['orders'][0]['expected_qty'], 45)
        self.assertEqual(result['orders'][0]['actual_qty'], 45)

    # ──────────────────────────────────────────────────────────
    #  Scenario 2: 现金不足 — 空仓保留，不下任何买单
    # ──────────────────────────────────────────────────────────
    def test_scenario2_insufficient_cash_no_orders(self):
        """
        net_liq=60000, cash=5000（低于 min_cash=6000）
        deployable = max(0, 5000-6000) = 0
        budget_per_pos = 0 → 所有 qty=0，跳过
        预期：不下任何买单
        """
        self.ib.set_account_values(net_liq=60000, cash=5000, buying_power=5000)

        signals = {
            'buy': [_make_buy_signal('TSLA', price=200.0)],
            '_mss': 0.3,
        }

        # 纯公式
        min_cash, deployable, slots, budget = compute_budget(
            net_liq=60000, cash=5000,
            cash_reserve_pct=0.10, position_pct=0.15,
            eff_max_pos=6, n_held=0,
        )
        self.assertAlmostEqual(min_cash, 6000.0)
        self.assertAlmostEqual(deployable, 0.0)   # max(0, 5000-6000)=0
        self.assertAlmostEqual(budget, 0.0)
        self.assertEqual(expected_qty(budget, 200.0), 0)

        result = run_position_sizing(self.ib, signals, self.cfg, dry_run=True)

        self.assertAlmostEqual(result['deployable'], 0.0)
        self.assertAlmostEqual(result['budget_per_pos'], 0.0)
        # qty=0 时 skip，orders 为空
        self.assertEqual(len(result['orders']), 0, "现金不足时不应下任何买单")

    # ──────────────────────────────────────────────────────────
    #  Scenario 3: 全仓满 — 无新买单
    # ──────────────────────────────────────────────────────────
    def test_scenario3_full_positions_no_new_orders(self):
        """
        n_held = MAX_POSITIONS = 6，slots = 0
        预期：无新买单
        """
        self.ib.set_account_values(net_liq=60000, cash=9000, buying_power=9000)

        # 6只满仓
        for sym in ['NVDA', 'AMD', 'META', 'AAPL', 'MSFT', 'GOOGL']:
            pos = MockPosition(
                contract=MockContract(symbol=sym),
                position=45.0,
                avgCost=200.0,
            )
            self.ib._positions.append(pos)

        signals = {
            'buy': [_make_buy_signal('TSLA', price=200.0)],
            '_mss': 0.3,
        }

        # 纯公式
        _, _, slots, _ = compute_budget(
            net_liq=60000, cash=9000,
            cash_reserve_pct=0.10, position_pct=0.15,
            eff_max_pos=6, n_held=6,
        )
        self.assertEqual(slots, 0)

        result = run_position_sizing(self.ib, signals, self.cfg, dry_run=True)

        self.assertEqual(result['slots'], 0)
        self.assertEqual(len(result['orders']), 0, "全仓满时不应开新仓")

    # ──────────────────────────────────────────────────────────
    #  Scenario 4: APA 100 股异常复现 + 检测
    # ──────────────────────────────────────────────────────────
    def test_scenario4_apa_quantity_anomaly_detection(self):
        """
        全现金状态：net_liq=60000, cash=60000, n_held=0
        price=200 → 预期 qty=45
        MockIB inject override_qty=100 → actual_qty=100
        验证：偏差 > 50% → ANOMALY
        同时验证其他股票 qty 正常（NVDA price=200 → qty=45）
        """
        self.ib.set_account_values(net_liq=60000, cash=60000, buying_power=60000)

        # APA 注入100股异常
        self.ib.inject_position_sizing_error('APA', override_qty=100)

        signals = {
            'buy': [
                _make_buy_signal('APA', price=200.0, sector='Energy'),
                _make_buy_signal('NVDA', price=200.0, sector='Technology'),
            ],
            '_mss': 0.3,
        }

        # 预期 qty（无ATR时使用 budget 计算）
        _, _, _, budget = compute_budget(
            net_liq=60000, cash=60000,
            cash_reserve_pct=0.10, position_pct=0.15,
            eff_max_pos=6, n_held=0,
        )
        exp_qty = expected_qty(budget, 200.0)
        self.assertEqual(exp_qty, 45)

        # 需要实际下单才能看到 inject 效果，所以用 dry_run=False + 绕过 ib_insync
        # 直接测试 MockIB.placeOrder 的 inject 行为
        from tests.simulator.mock_ib import MockContract as MC, MockOrder
        contract_apa = MC(symbol='APA')
        order_apa = MockOrder(orderId=0, action='BUY',
                               totalQuantity=45.0, orderType='LMT',
                               tif='OPG', lmtPrice=202.0)
        trade_apa = self.ib.placeOrder(contract_apa, order_apa)

        # MockIB 应已将 qty 替换为 100
        actual_qty_apa = int(trade_apa.order.totalQuantity)
        self.assertEqual(actual_qty_apa, 100)

        # 检测异常：偏差比例 > 50%
        deviation = abs(actual_qty_apa - exp_qty) / exp_qty
        self.assertGreater(deviation, 0.50,
                           f"APA qty={actual_qty_apa} vs expected={exp_qty}，偏差{deviation:.0%}，应触发 ANOMALY")
        anomaly_detected = deviation > 0.50
        self.assertTrue(anomaly_detected, "应检测到 ANOMALY: qty=100 偏离预期 45 超过 50%")

        # 验证 NVDA 不受注入影响
        contract_nvda = MC(symbol='NVDA')
        order_nvda = MockOrder(orderId=0, action='BUY',
                               totalQuantity=45.0, orderType='LMT',
                               tif='OPG', lmtPrice=202.0)
        trade_nvda = self.ib.placeOrder(contract_nvda, order_nvda)
        actual_qty_nvda = int(trade_nvda.order.totalQuantity)
        self.assertEqual(actual_qty_nvda, 45,
                         "NVDA 未注入异常，qty 应保持正常值 45")
        nvda_deviation = abs(actual_qty_nvda - exp_qty) / exp_qty
        self.assertLessEqual(nvda_deviation, 0.10, "NVDA 计算正常，偏差应 ≤ 10%")

    # ──────────────────────────────────────────────────────────
    #  Scenario 5: 现金等价 ETF 不占槽位，市值计入 cash
    # ──────────────────────────────────────────────────────────
    def test_scenario5_cash_equiv_etf_not_counted_as_slot(self):
        """
        持仓：NVDA（stock）+ SGOV（现金等价 ETF）
        n_held 应 = 1（SGOV 不计入槽位）
        SGOV 市值应计入 cash
        slots = MAX_POSITIONS - 1 = 5
        """
        # 账户：仅显示股票持仓对应的现金（SGOV 市值单独在持仓里）
        self.ib.set_account_values(net_liq=60000, cash=30000, buying_power=30000)

        # NVDA 持仓（占槽位）
        self.ib._positions.append(MockPosition(
            contract=MockContract(symbol='NVDA'),
            position=45.0,
            avgCost=200.0,
        ))
        # SGOV 持仓：市值 = 100 * 100 = 10000（不占槽位）
        self.ib._positions.append(MockPosition(
            contract=MockContract(symbol='SGOV'),
            position=100.0,
            avgCost=100.0,
        ))

        signals = {
            'buy': [_make_buy_signal('AAPL', price=200.0)],
            '_mss': 0.3,
        }

        result = run_position_sizing(self.ib, signals, self.cfg, dry_run=True)

        # SGOV 不计入 n_held
        self.assertEqual(result['n_held'], 1, "SGOV 不应计入持仓槽位，n_held 应 = 1")
        # slots = 6 - 1 = 5（而非 6-2=4）
        self.assertEqual(result['slots'], 5, "slots 应基于 n_held=1 计算，为 5")
        # SGOV 市值 10000 计入 cash：cash = 30000 + 10000 = 40000
        self.assertAlmostEqual(result['cash'], 40000.0,
                               msg="SGOV 市值 10000 应已计入 cash")

    # ──────────────────────────────────────────────────────────
    #  Scenario 6: MSS 牛市模式扩仓
    # ──────────────────────────────────────────────────────────
    def test_scenario6_mss_bull_mode_expanded_slots(self):
        """
        signals['_mss'] = 0.8（> MSS_BULL_THRESHOLD=0.5）
        eff_max_pos = MSS_BULL_MAX_POS（config 读取，这里设为 8）
        n_held=3 → slots = 8 - 3 = 5（而非 6-3=3）
        """
        cfg_bull = _make_fake_config(
            max_positions=6,
            position_pct=0.15,
            cash_reserve_pct=0.10,
            mss_bull_max_pos=8,         # 牛市扩至 8 仓
            mss_bull_threshold=0.5,
        )

        self.ib.set_account_values(net_liq=60000, cash=45000, buying_power=45000)

        # 3只已持仓
        for sym in ['NVDA', 'AMD', 'META']:
            self.ib._positions.append(MockPosition(
                contract=MockContract(symbol=sym),
                position=45.0,
                avgCost=200.0,
            ))

        # MSS=0.8 → 牛市
        signals = {
            'buy': [_make_buy_signal('AAPL', price=200.0, sector='Technology')],
            '_mss': 0.8,
        }

        result = run_position_sizing(self.ib, signals, cfg_bull, dry_run=True)

        # eff_max_pos 应为 8
        self.assertEqual(result['eff_max_pos'], 8,
                         "MSS=0.8 触发牛市，eff_max_pos 应为 8")
        # slots = 8 - 3 = 5
        self.assertEqual(result['slots'], 5,
                         "牛市 slots = MSS_BULL_MAX_POS(8) - n_held(3) = 5")
        # 非牛市 slots 应为 3（对比验证）
        result_normal = run_position_sizing(
            self.ib,
            {**signals, '_mss': 0.3},   # 普通区间
            cfg_bull,
            dry_run=True,
        )
        self.assertEqual(result_normal['slots'], 3,
                         "普通 MSS slots = MAX_POSITIONS(6) - n_held(3) = 3")

    # ──────────────────────────────────────────────────────────
    #  Scenario 7: 单仓预算上限（POSITION_PCT 约束）
    # ──────────────────────────────────────────────────────────
    def test_scenario7_position_pct_cap_on_budget(self):
        """
        net_liq=60000, cash=60000, n_held=0, slots=6
        deployable/slots = 60000*0.90/6 = 9000（含保留现金扣除后）
        net_liq*POSITION_PCT = 60000*0.15 = 9000
        budget_per_pos = min(9000, 9000) = 9000

        更强验证：调大 cash 使 deployable/slots > POSITION_PCT*net_liq
        net_liq=60000, cash=60000, slots=1 → deployable=54000 → deployable/1=54000
        POSITION_PCT cap = 9000 → budget 应取 9000
        """
        self.ib.set_account_values(net_liq=60000, cash=60000, buying_power=60000)

        # 标准场景：6槽位
        min_cash, deployable, slots, budget = compute_budget(
            net_liq=60000, cash=60000,
            cash_reserve_pct=0.10, position_pct=0.15,
            eff_max_pos=6, n_held=0,
        )
        self.assertAlmostEqual(min_cash, 6000.0)
        self.assertAlmostEqual(deployable, 54000.0)   # 60000 - 6000
        self.assertEqual(slots, 6)
        # deployable/slots = 54000/6 = 9000 == net_liq*0.15 = 9000 → min(9000,9000)=9000
        self.assertAlmostEqual(budget, 9000.0)

        # 验证 POSITION_PCT 约束起作用：只有1个槽时 deployable/slots=54000 >> 9000
        min_cash2, deployable2, slots2, budget2 = compute_budget(
            net_liq=60000, cash=60000,
            cash_reserve_pct=0.10, position_pct=0.15,
            eff_max_pos=1, n_held=0,
        )
        self.assertAlmostEqual(deployable2, 54000.0)
        self.assertEqual(slots2, 1)
        # deployable/1 = 54000，但 POSITION_PCT cap = 9000
        self.assertAlmostEqual(budget2, 9000.0,
                               msg="单槽时 POSITION_PCT(15%×60000=9000) 应约束 budget")
        self.assertLess(budget2, deployable2,
                        "budget 应小于 deployable（POSITION_PCT 起到上限约束作用）")

        # 执行引擎对齐验证：price=200 → qty=45
        signals = {
            'buy': [_make_buy_signal('AAPL', price=200.0)],
            '_mss': 0.3,
        }
        result = run_position_sizing(self.ib, signals, self.cfg, dry_run=True)
        self.assertAlmostEqual(result['budget_per_pos'], 9000.0)
        self.assertEqual(result['orders'][0]['expected_qty'], 45)


# ══════════════════════════════════════════════════════════════
#  JSON 报告输出
# ══════════════════════════════════════════════════════════════

def run_tests_with_json_report() -> int:
    """运行所有测试并输出 JSON 格式报告，返回 exit_code（0=全通过，1=有失败）。"""
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestPositionSizing)

    results: list[dict] = []
    passed = 0
    failed = 0

    for test in suite:
        test_name = test._testMethodName
        runner = unittest.TextTestRunner(stream=open(os.devnull, 'w'), verbosity=0)
        result = runner.run(unittest.TestSuite([test]))

        if result.wasSuccessful():
            status = 'PASS'
            passed += 1
            detail = None
        else:
            status = 'FAIL'
            failed += 1
            errors = result.errors + result.failures
            detail = errors[0][1].strip().split('\n')[-1] if errors else 'Unknown error'

        results.append({
            'test': test_name,
            'status': status,
            'detail': detail,
        })

    report = {
        'test_class': 'position_sizing',
        'results': results,
        'passed': passed,
        'failed': failed,
        'exit_code': 0 if failed == 0 else 1,
    }

    print(json.dumps(report, ensure_ascii=False))
    return report['exit_code']


# ══════════════════════════════════════════════════════════════
#  入口
# ══════════════════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='仓位计算 + 资金预算异常测试')
    parser.add_argument('--json', action='store_true', help='输出 JSON 格式报告')
    parser.add_argument('--verbose', '-v', action='store_true', help='详细输出')
    args = parser.parse_args()

    if args.json:
        sys.exit(run_tests_with_json_report())
    else:
        verbosity = 2 if args.verbose else 1
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromTestCase(TestPositionSizing)
        runner = unittest.TextTestRunner(verbosity=verbosity)
        result = runner.run(suite)

        # 无论是否指定 --json，末尾都打印简要 JSON 摘要
        print()
        summary = {
            'test_class': 'position_sizing',
            'passed': result.testsRun - len(result.failures) - len(result.errors),
            'failed': len(result.failures) + len(result.errors),
            'exit_code': 0 if result.wasSuccessful() else 1,
        }
        print(json.dumps(summary, ensure_ascii=False))
        sys.exit(0 if result.wasSuccessful() else 1)
