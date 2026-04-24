"""
tests/test_sector_limits.py

行业集中度限制 + 重复订单防护 单元测试

测试的真实代码路径（auto_trader.py 第 733-806 行）：
  - 行业集中度过滤：sector_counts + MAX_PER_SECTOR 检查（第 751-754 行）
  - 行业计数递增：  sector_counts[sec] += 1 on each executed buy（第 805 行）
  - 重复订单防护：  _cancel_existing(sym, action) → ib.cancelOrder()（第 455-463 行）

设计原则：
  - 不调用 _execute_inner（依赖 IB/DB/DataStore）
  - 提取核心逻辑为本文件内的 helpers，镜像真实实现
  - 使用 tests/simulator/mock_ib.MockIB 作为 IB mock
  - 使用 tests/simulator/fixtures.SECTOR_MAP 作为行业数据
  - 不需要网络、MySQL、IB Gateway

可独立运行：python tests/test_sector_limits.py
"""

from __future__ import annotations

import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import traceback
from typing import Any

# ────────────────────────────────────────────────────────────
#  导入 fixtures 和 MockIB
# ────────────────────────────────────────────────────────────
from tests.simulator.mock_ib import MockIB, MockPosition, MockContract, MockOrder, MockTrade, MockOrderStatus
from tests.simulator.fixtures import SECTOR_MAP, ACCOUNT_STATE


# ────────────────────────────────────────────────────────────
#  从 config 读取 MAX_PER_SECTOR（保持与生产一致）
# ────────────────────────────────────────────────────────────
MAX_PER_SECTOR = 3  # 测试固定用3，不跟随DB运行时配置


# ────────────────────────────────────────────────────────────
#  本地 helpers：镜像 auto_trader._execute_inner 的关键逻辑
#
#  之所以提取为 helpers 而非直接调用 _execute_inner：
#    - _execute_inner 强依赖 IB 账户余额查询、DataStore、
#      Trading 下单方法等重型基础设施
#    - 行业集中度和重复订单防护是两段独立的纯逻辑，
#      可以原样复制后用 mock 数据驱动
# ────────────────────────────────────────────────────────────

def _make_existing_orders(ib: MockIB, tif: str = "DAY") -> dict[str, dict[str, Any]]:
    """
    镜像 auto_trader._execute_inner 第 443-449 行：
    从 ib.openTrades() 构建 existing_orders dict（按 tif 过滤）。
    返回：{symbol: {action: trade}}
    """
    existing_orders: dict[str, dict[str, Any]] = {}
    for t in ib.openTrades():
        if t.order.tif != tif:
            continue
        sym = t.contract.symbol
        act = t.order.action
        existing_orders.setdefault(sym, {})[act] = t
    return existing_orders


def _cancel_existing(ib: MockIB, existing_orders: dict, sym: str, action: str) -> None:
    """
    镜像 auto_trader._execute_inner 第 455-463 行：
    撤销同方向已有挂单，无挂单则静默返回。
    """
    entry = existing_orders.get(sym, {}).get(action)
    if entry is None:
        return
    ib.cancelOrder(entry.order)
    ib.sleep(1)


def _apply_sector_filter(
    buy_list: list[dict],
    position_symbols: list[str],
    sector_lookup: dict[str, str],
    max_per_sector: int = MAX_PER_SECTOR,
) -> tuple[list[str], list[str]]:
    """
    镜像 auto_trader._execute_inner 第 733-806 行中的行业过滤子逻辑。

    参数：
      buy_list         买入信号列表，每项含 'symbol' 和 'sector'
      position_symbols 当前持仓 symbol 列表（不含现金等价 ETF）
      sector_lookup    {symbol: sector} 映射（替代 get_stock_info() 网络调用）
      max_per_sector   行业持仓上限

    返回：
      allowed  被允许买入的 symbol 列表（按处理顺序）
      skipped  因行业满载被跳过的 symbol 列表

    注：本 helper 只模拟行业过滤，不处理资金/槽位/财报/量价等其他过滤。
    """
    # 第 733-739 行：统计当前持仓的行业分布
    sector_counts: dict[str, int] = {}
    for sym in position_symbols:
        sec = sector_lookup.get(sym) or "Unknown"
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    allowed: list[str] = []
    skipped: list[str] = []

    for sig in buy_list:
        symbol = sig["symbol"]
        # 第 747-749 行：已持仓跳过（本 helper 不处理，测试场景保证不重叠）
        # 第 751-754 行：行业集中度检查
        sec = sig.get("sector") or "Unknown"
        if sector_counts.get(sec, 0) >= max_per_sector:
            skipped.append(symbol)
            continue
        # 第 805 行：执行后行业计数 +1
        sector_counts[sec] = sector_counts.get(sec, 0) + 1
        allowed.append(symbol)

    return allowed, skipped


# ────────────────────────────────────────────────────────────
#  工具函数
# ────────────────────────────────────────────────────────────

def _result(scenario: str, status: str, details: str, errors: list | None = None) -> dict:
    return {
        "scenario": scenario,
        "status": status,
        "details": details,
        "errors": errors or [],
    }


def _make_buy_signal(symbol: str, sector: str | None = None, price: float = 100.0) -> dict:
    """构造最小合法的 buy signal dict，sector 字段对齐 auto_trader 格式。"""
    return {
        "symbol": symbol,
        "rs_score": 0.05,
        "close": price,
        "vol_ratio": 1.8,
        "sector": sector or SECTOR_MAP.get(symbol, "Unknown"),
        "industry": sector or SECTOR_MAP.get(symbol, "Unknown"),
    }


# ────────────────────────────────────────────────────────────
#  Scenario 1: 行业未满 — 允许买入
# ────────────────────────────────────────────────────────────

def scenario_sector_not_full_allows_buy() -> dict:
    """
    持仓：NVDA + AMAT（半导体 2 只）
    信号：KLAC（半导体）
    MAX_PER_SECTOR = 3
    预期：KLAC 被允许买入
    """
    positions = ["NVDA", "AMAT"]
    buy_list = [_make_buy_signal("KLAC", "Semiconductors")]

    allowed, skipped = _apply_sector_filter(
        buy_list=buy_list,
        position_symbols=positions,
        sector_lookup={
            "NVDA": "Semiconductors",
            "AMAT": "Semiconductors",
            "KLAC": "Semiconductors",
        },
        max_per_sector=MAX_PER_SECTOR,
    )

    errors = []
    if "KLAC" not in allowed:
        errors.append(
            f"半导体行业持仓2只 < MAX_PER_SECTOR={MAX_PER_SECTOR}，KLAC 应被允许买入，"
            f"但出现在 skipped={skipped}"
        )
    if "KLAC" in skipped:
        errors.append(f"KLAC 不应出现在 skipped 列表中")

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"positions={positions}, signal=KLAC(半导体), "
        f"sector_count_before=2, MAX_PER_SECTOR={MAX_PER_SECTOR}, "
        f"allowed={allowed}, skipped={skipped}"
    )
    return _result("sector_not_full_allows_buy", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  Scenario 2: 行业已满 — 拒绝买入，其他行业不受影响
# ────────────────────────────────────────────────────────────

def scenario_sector_full_rejects_buy() -> dict:
    """
    持仓：NVDA + AMAT + KLAC（半导体 3 只，已满）
    信号：LRCX（半导体）+ AAPL（科技）
    预期：LRCX 被跳过，AAPL 被允许
    """
    positions = ["NVDA", "AMAT", "KLAC"]
    buy_list = [
        _make_buy_signal("LRCX", "Semiconductors"),
        _make_buy_signal("AAPL", "Technology"),
    ]
    sector_lookup = {
        "NVDA": "Semiconductors",
        "AMAT": "Semiconductors",
        "KLAC": "Semiconductors",
        "LRCX": "Semiconductors",
        "AAPL": "Technology",
    }

    allowed, skipped = _apply_sector_filter(
        buy_list=buy_list,
        position_symbols=positions,
        sector_lookup=sector_lookup,
        max_per_sector=MAX_PER_SECTOR,
    )

    errors = []
    if "LRCX" not in skipped:
        errors.append(
            f"半导体行业持仓={MAX_PER_SECTOR}只（已满），LRCX 应被跳过，"
            f"但出现在 allowed={allowed}"
        )
    if "AAPL" not in allowed:
        errors.append(
            f"科技行业持仓0只，AAPL 不应受影响，应被允许，"
            f"但出现在 skipped={skipped}"
        )

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"positions={positions}, signals=[LRCX(半导体), AAPL(科技)], "
        f"sector_semi_count=3 (满), MAX_PER_SECTOR={MAX_PER_SECTOR}, "
        f"allowed={allowed}, skipped={skipped}"
    )
    return _result("sector_full_rejects_buy", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  Scenario 3: 行业满载 + SELL 先执行让出槽位，BUY 后执行可买
# ────────────────────────────────────────────────────────────

def scenario_sell_before_buy_frees_sector_slot() -> dict:
    """
    持仓：NVDA + AMAT + KLAC（半导体 3 只）
    信号：NVDA SELL + LRCX BUY（半导体）

    auto_trader 的真实执行顺序：
      1. 止损/卖出报警（SELL）先处理
      2. exiting_syms 记录将退出的持仓
      3. 在 _apply_sector_filter 里，模拟 "NVDA 已退出后的持仓" 来计算行业计数

    本 scenario 验证：从 positions 中去除 NVDA 后，半导体仅剩 2 只，LRCX 应被允许。
    """
    # Step 1: 卖出 NVDA（从持仓中移除）
    positions_before_sell = ["NVDA", "AMAT", "KLAC"]
    sell_syms = {"NVDA"}

    # Step 2: 剩余持仓（镜像 _execute_inner 第 686-697 行的 exiting_syms 逻辑）
    positions_after_sell = [s for s in positions_before_sell if s not in sell_syms]

    # Step 3: 用剩余持仓做行业过滤
    buy_list = [_make_buy_signal("LRCX", "Semiconductors")]
    sector_lookup = {
        "AMAT": "Semiconductors",
        "KLAC": "Semiconductors",
        "LRCX": "Semiconductors",
    }

    allowed, skipped = _apply_sector_filter(
        buy_list=buy_list,
        position_symbols=positions_after_sell,
        sector_lookup=sector_lookup,
        max_per_sector=MAX_PER_SECTOR,
    )

    errors = []
    if "LRCX" not in allowed:
        errors.append(
            f"NVDA 卖出后半导体剩余 {len(positions_after_sell)} 只 < MAX_PER_SECTOR={MAX_PER_SECTOR}，"
            f"LRCX 应被允许，但出现在 skipped={skipped}"
        )

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"positions_before_sell={positions_before_sell}, "
        f"sell=[NVDA], positions_after_sell={positions_after_sell}, "
        f"signal=LRCX(半导体), "
        f"allowed={allowed}, skipped={skipped}"
    )
    return _result("sell_before_buy_frees_sector_slot", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  Scenario 4: 重复买入防护（同 symbol 两次 BUY 信号）
# ────────────────────────────────────────────────────────────

def scenario_duplicate_buy_protection() -> dict:
    """
    existing_orders 中已有 NVDA BUY 挂单。
    新信号再次 BUY NVDA。
    预期：
      - 旧 BUY 单被撤销（ib._cancelled 有1条）
      - 新 BUY 单被提交（ib._open_trades 中 NVDA BUY 只有1笔）
      - 不发生双重下单
    """
    ib = MockIB()
    tif = "DAY"

    # 预置旧 BUY 挂单
    old_trade = ib.add_open_trade("NVDA", "BUY", qty=50, price=480.0, tif=tif)
    old_order_id = old_trade.order.orderId

    # 构建 existing_orders（镜像 auto_trader 第 443-449 行）
    existing_orders = _make_existing_orders(ib, tif=tif)

    errors = []

    # 断言：existing_orders 已识别到旧单
    if "NVDA" not in existing_orders or "BUY" not in existing_orders["NVDA"]:
        errors.append(f"existing_orders 未识别到 NVDA BUY 挂单：{existing_orders}")

    # 模拟"撤旧换新"：先撤旧单
    _cancel_existing(ib, existing_orders, "NVDA", "BUY")

    # 断言：旧单已被撤销，open_trades 中不再有旧单
    open_nvda_buys = [
        t for t in ib.openTrades()
        if t.contract.symbol == "NVDA" and t.order.action == "BUY"
    ]
    if open_nvda_buys:
        errors.append(
            f"旧 NVDA BUY 单应已被撤销，但 openTrades 仍有：{open_nvda_buys}"
        )

    cancelled_ids = [o.orderId for o in ib._cancelled]
    if old_order_id not in cancelled_ids:
        errors.append(
            f"旧 NVDA BUY orderId={old_order_id} 应出现在 _cancelled 中，"
            f"但 _cancelled={cancelled_ids}"
        )

    # 模拟提交新单
    new_trade = ib.add_open_trade("NVDA", "BUY", qty=55, price=482.0, tif=tif)

    # 断言：现在 NVDA BUY 只有1笔有效挂单
    final_nvda_buys = [
        t for t in ib.openTrades()
        if t.contract.symbol == "NVDA" and t.order.action == "BUY"
    ]
    if len(final_nvda_buys) != 1:
        errors.append(
            f"撤旧换新后 NVDA BUY 应只有1笔有效挂单，实际={len(final_nvda_buys)}"
        )

    # 断言：_cancelled 中只有1笔取消（防止误撤新单）
    if len(ib._cancelled) != 1:
        errors.append(
            f"应只取消1笔旧单，实际取消了 {len(ib._cancelled)} 笔"
        )

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"old_order_id={old_order_id}, "
        f"cancelled={[o.orderId for o in ib._cancelled]}, "
        f"final_open_nvda_buys={len(final_nvda_buys)}, "
        f"new_trade_id={new_trade.order.orderId}"
    )
    return _result("duplicate_buy_protection", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  Scenario 5: 重复卖出防护
# ────────────────────────────────────────────────────────────

def scenario_duplicate_sell_protection() -> dict:
    """
    已有 NVDA SELL 挂单（止损触发后下单）。
    移动止损/时间止损逻辑再次触发 NVDA SELL。
    预期：
      - 旧 SELL 被撤
      - 新 SELL 被提交
      - 最终只有1笔 SELL 挂单
    """
    ib = MockIB()
    tif = "DAY"

    # 预置旧 SELL 挂单
    old_sell = ib.add_open_trade("NVDA", "SELL", qty=50, price=460.0, tif=tif)
    old_sell_id = old_sell.order.orderId

    existing_orders = _make_existing_orders(ib, tif=tif)

    errors = []

    # 断言：旧 SELL 被识别
    if "NVDA" not in existing_orders or "SELL" not in existing_orders["NVDA"]:
        errors.append(f"existing_orders 未识别到 NVDA SELL 挂单：{existing_orders}")

    # 撤旧 SELL
    _cancel_existing(ib, existing_orders, "NVDA", "SELL")

    # 断言：旧 SELL 已撤
    open_nvda_sells_after_cancel = [
        t for t in ib.openTrades()
        if t.contract.symbol == "NVDA" and t.order.action == "SELL"
    ]
    if open_nvda_sells_after_cancel:
        errors.append(
            f"旧 NVDA SELL 应已被撤，openTrades 仍有：{open_nvda_sells_after_cancel}"
        )

    cancelled_ids = [o.orderId for o in ib._cancelled]
    if old_sell_id not in cancelled_ids:
        errors.append(
            f"旧 NVDA SELL orderId={old_sell_id} 应在 _cancelled 中，"
            f"_cancelled={cancelled_ids}"
        )

    # 提交新 SELL
    new_sell = ib.add_open_trade("NVDA", "SELL", qty=50, price=455.0, tif=tif)

    # 断言：只有1笔 SELL 挂单
    final_nvda_sells = [
        t for t in ib.openTrades()
        if t.contract.symbol == "NVDA" and t.order.action == "SELL"
    ]
    if len(final_nvda_sells) != 1:
        errors.append(
            f"最终 NVDA SELL 挂单应为1笔，实际={len(final_nvda_sells)}"
        )

    # 断言：不影响同 symbol 的 BUY 方向（无 BUY 被误撤）
    if len(ib._cancelled) != 1:
        errors.append(
            f"应只取消1笔（旧 SELL），实际取消={len(ib._cancelled)} 笔"
        )

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"old_sell_id={old_sell_id}, "
        f"cancelled={[o.orderId for o in ib._cancelled]}, "
        f"final_open_nvda_sells={len(final_nvda_sells)}, "
        f"new_sell_id={new_sell.order.orderId}"
    )
    return _result("duplicate_sell_protection", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  Scenario 6: 多行业混合 — 精确计数
# ────────────────────────────────────────────────────────────

def scenario_multi_sector_precise_counting() -> dict:
    """
    持仓：
      半导体：NVDA + AMAT + KLAC（3只，满）
      科技：  AAPL + MSFT（2只）
      通信：  GOOGL（1只）

    信号：
      TSM（半导体）→ 应被拒绝（满）
      META（通信） → 应被允许（通信 1→2）
      AMZN（通信） → 应被允许（通信 2→3）

    注：MAX_PER_SECTOR=3，过滤条件为 count >= 3 时拒绝；
      通信现有1只，META允许后2只，AMZN允许后3只 — 三者都通过
    """
    positions = ["NVDA", "AMAT", "KLAC", "AAPL", "MSFT", "GOOGL"]
    sector_lookup = {
        "NVDA":  "Semiconductors",
        "AMAT":  "Semiconductors",
        "KLAC":  "Semiconductors",
        "AAPL":  "Technology",
        "MSFT":  "Technology",
        "GOOGL": "Communication Services",
        "TSM":   "Semiconductors",
        "META":  "Communication Services",
        "AMZN":  "Communication Services",
    }
    buy_list = [
        _make_buy_signal("TSM",  "Semiconductors"),
        _make_buy_signal("META", "Communication Services"),
        _make_buy_signal("AMZN", "Communication Services"),
    ]

    allowed, skipped = _apply_sector_filter(
        buy_list=buy_list,
        position_symbols=positions,
        sector_lookup=sector_lookup,
        max_per_sector=MAX_PER_SECTOR,
    )

    errors = []

    # TSM：半导体已满 → 应被跳过
    if "TSM" not in skipped:
        errors.append(
            f"半导体持仓={MAX_PER_SECTOR}只（满），TSM 应被跳过，"
            f"但出现在 allowed={allowed}"
        )

    # META：通信持仓1只 → 应被允许（1 < MAX_PER_SECTOR=3）
    if "META" not in allowed:
        errors.append(
            f"通信持仓1只 < MAX_PER_SECTOR={MAX_PER_SECTOR}，META 应被允许，"
            f"但出现在 skipped={skipped}"
        )

    # AMZN：通信此时为2只（META 已被计入）→ 应被允许（2 < 3）
    if "AMZN" not in allowed:
        errors.append(
            f"META 被允许后通信持仓2只 < MAX_PER_SECTOR={MAX_PER_SECTOR}，"
            f"AMZN 应被允许，但出现在 skipped={skipped}"
        )

    # 科技行业不受影响（未有买入信号，但计数正确是前提）
    # 验证：skipped 只有 TSM
    if len(skipped) != 1:
        errors.append(
            f"应只有 TSM 被跳过（1只），实际 skipped={skipped}"
        )
    if len(allowed) != 2:
        errors.append(
            f"应有 META + AMZN 被允许（2只），实际 allowed={allowed}"
        )

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"positions={positions}, "
        f"sector_counts_before={{半导体:3, 科技:2, 通信:1}}, "
        f"MAX_PER_SECTOR={MAX_PER_SECTOR}, "
        f"allowed={allowed}, skipped={skipped}"
    )
    return _result("multi_sector_precise_counting", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  辅助：cancel_existing 不影响异方向挂单
# ────────────────────────────────────────────────────────────

def scenario_cancel_does_not_affect_opposite_direction() -> dict:
    """
    同一 symbol NVDA 同时有 BUY 和 SELL 挂单（极端场景）。
    执行 _cancel_existing(NVDA, 'BUY') 后：
      - BUY 单被撤
      - SELL 单应保持不变
    """
    ib = MockIB()
    tif = "DAY"

    buy_trade  = ib.add_open_trade("NVDA", "BUY",  qty=50, price=480.0, tif=tif)
    sell_trade = ib.add_open_trade("NVDA", "SELL", qty=50, price=460.0, tif=tif)

    existing_orders = _make_existing_orders(ib, tif=tif)

    errors = []

    # 只撤 BUY 方向
    _cancel_existing(ib, existing_orders, "NVDA", "BUY")

    # BUY 应被撤
    open_buys = [
        t for t in ib.openTrades()
        if t.contract.symbol == "NVDA" and t.order.action == "BUY"
    ]
    if open_buys:
        errors.append(f"NVDA BUY 应被撤，仍有 {len(open_buys)} 笔")

    # SELL 应保持
    open_sells = [
        t for t in ib.openTrades()
        if t.contract.symbol == "NVDA" and t.order.action == "SELL"
    ]
    if len(open_sells) != 1:
        errors.append(
            f"NVDA SELL 不应受影响，应有1笔，实际={len(open_sells)}"
        )

    # 取消数量应为1
    if len(ib._cancelled) != 1:
        errors.append(f"应取消1笔（BUY），实际取消={len(ib._cancelled)} 笔")

    if ib._cancelled and ib._cancelled[0].action != "BUY":
        errors.append(
            f"被取消的应是 BUY 方向，实际={ib._cancelled[0].action}"
        )

    status = "PASS" if not errors else "FAIL"
    detail = (
        f"buy_trade_id={buy_trade.order.orderId}, "
        f"sell_trade_id={sell_trade.order.orderId}, "
        f"cancelled={[o.orderId for o in ib._cancelled]}, "
        f"open_sells_after={len(open_sells)}"
    )
    return _result("cancel_does_not_affect_opposite_direction", status, detail, errors)


# ────────────────────────────────────────────────────────────
#  主运行函数
# ────────────────────────────────────────────────────────────

SCENARIOS = [
    scenario_sector_not_full_allows_buy,
    scenario_sector_full_rejects_buy,
    scenario_sell_before_buy_frees_sector_slot,
    scenario_duplicate_buy_protection,
    scenario_duplicate_sell_protection,
    scenario_multi_sector_precise_counting,
    scenario_cancel_does_not_affect_opposite_direction,
]


def run_all() -> dict:
    results = []
    for fn in SCENARIOS:
        try:
            r = fn()
        except Exception as e:
            r = _result(
                scenario=fn.__name__,
                status="FAIL",
                details="Scenario 本身抛出未捕获异常",
                errors=[f"{type(e).__name__}: {e}", traceback.format_exc()],
            )
        print(json.dumps(r, ensure_ascii=False))
        results.append(r)

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = len(results) - passed

    report = {
        "test_class": "sector_limits",
        "results": results,
        "passed": passed,
        "failed": failed,
        "exit_code": 0 if failed == 0 else 1,
    }
    return report


if __name__ == "__main__":
    report = run_all()
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(0 if report["failed"] == 0 else 1)
