"""
阶段 1 等价性验证：旧「ai_boost + ai_priority_bonus + inline 槽位循环」逻辑
                  vs 新「rank_tier 元组排序 + PoolAllocator」逻辑。

对最近一个交易日的真实候选(scan_signals 输出)做两件事：
  1. 排序 diff：旧 entry_score 降序 vs 新 (rank_tier, entry_score) 元组
  2. 选股 diff：在若干持仓场景下，旧 inline 循环 vs PoolAllocator 选出的买入列表

旧逻辑用本文件内冻结的副本(还原重构前 auto_trader 的行为)，不依赖被改动的代码。

用法：
  python -m tests.compare_pool_refactor
  python -m tests.compare_pool_refactor --universe ai
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════
#  旧逻辑冻结副本（重构前 auto_trader 的行为）
# ══════════════════════════════════════════════════════════════════

def _old_load_ai_boost_map() -> dict[str, float]:
    """还原旧 _load_ai_boost_map：ai_universe 基础 0.10，有追踪器评分则 (score/15)*0.20。"""
    boost_map: dict[str, float] = {}
    uf = ROOT / 'data' / 'ai_universe.json'
    if not uf.exists():
        return boost_map
    u = json.loads(uf.read_text(encoding='utf-8'))
    for gv in u.get('groups', {}).values():
        for s in gv.get('symbols', []):
            boost_map[s.upper()] = 0.10
    cf = ROOT / 'data' / 'ai_tracker_cache.json'
    if cf.exists():
        cached = json.loads(cf.read_text(encoding='utf-8'))
        for row in cached.get('rows', []):
            sym = row.get('symbol', '').upper()
            sc  = row.get('score')
            if sym in boost_map and sc is not None:
                boost_map[sym] = min((sc / 15) * 0.20, 0.20)
    return boost_map


def _old_entry_score(sig: dict, ai_boost_map: dict[str, float]) -> float:
    """还原旧 _entry_score：rs × (1 + vol + prox + insider + ai_boost) + 0.5(若 ai_priority)。"""
    rs       = sig.get('rs_score', 0)
    vol      = sig.get('vol_ratio', 1.0)
    drawdown = sig.get('drawdown_from_high', -0.15)
    insider  = sig.get('insider_score', 0)
    ai       = ai_boost_map.get(sig['symbol'], 0.0)
    vol_boost       = min(vol / 3.0, 1.0) * 0.15
    proximity_boost = max(0.0, (drawdown + 0.30) / 0.30) * 0.10
    insider_boost   = min(insider / 10.0, 1.0) * 0.10
    base = rs * (1 + vol_boost + proximity_boost + insider_boost + ai)
    if sig.get('ai_priority'):
        base += 0.5
    return base


def _old_select(candidates: list[dict], *, ai_set: set[str], slots: int, global_cap: int,
                real_held: int, sector_cap: int, max_non_ai: int,
                sector_counts: dict[str, int], non_ai_held: int) -> list[str]:
    """还原旧 execute inline 买入循环的结构性选股（假设所有候选都可下单：跳过 qty/earnings）。"""
    sector_counts = dict(sector_counts)
    picks = []
    executed = 0
    for sig in candidates:
        if executed >= slots:
            break
        if real_held + executed >= global_cap:
            break
        symbol = sig['symbol']
        sec = sig.get('sector') or 'Unknown'
        is_ai = symbol.upper() in ai_set
        if not is_ai and sector_counts.get(sec, 0) >= sector_cap:
            continue
        if not is_ai and non_ai_held >= max_non_ai:
            continue
        picks.append(symbol)
        if not is_ai:
            sector_counts[sec] = sector_counts.get(sec, 0) + 1
            non_ai_held += 1
        executed += 1
    return picks


# ══════════════════════════════════════════════════════════════════
#  新逻辑选股（PoolAllocator，同样假设候选都可下单）
# ══════════════════════════════════════════════════════════════════

def _new_select(candidates: list[dict], policies, *, slots: int, global_cap: int,
                real_held: int, sector_cap: int,
                sector_counts: dict[str, int], non_ai_held: int) -> list[str]:
    from core.pool_policy import PoolAllocator
    alloc = PoolAllocator(policies, global_slots=slots, global_cap=global_cap,
                          real_held_count=real_held, sector_cap=sector_cap,
                          sector_counts=sector_counts,
                          pool_counts={'sp500_base': non_ai_held})
    picks = []
    for sig in candidates:
        if alloc.executed >= alloc.global_slots:
            break
        if alloc.real_held + alloc.executed >= alloc.global_cap:
            break
        if alloc.structural_skip(sig):
            continue
        picks.append(sig['symbol'])
        alloc.commit(sig)
    return picks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--universe', default='ai')
    args = ap.parse_args()

    import config
    from auto_trader import scan_signals
    from core.pool_policy import build_pool_policies, sort_key, load_ai_priority_set

    print("运行 scan_signals（新逻辑，取最近一个交易日真实候选）...\n")
    raw = scan_signals(
        held_symbols=[], extra=[], universe=args.universe,
        min_cap_b=config.MIN_CAP_B, max_cap_b=config.MAX_CAP_B,
        deny_industries=config.DENY_INDUSTRIES,
    )
    candidates = raw['buy']   # 已按新 sort_key 排好序
    if not candidates:
        print("⚠️  当日无买入候选（可能熔断/无信号），无法对比。换个交易日或股票池再试。")
        return

    policies      = build_pool_policies()
    ai_set        = load_ai_priority_set()
    ai_boost_map  = _old_load_ai_boost_map()

    # ── 1) 排序 diff ────────────────────────────────────────────
    new_order = [s['symbol'] for s in candidates]
    old_sorted = sorted(candidates, key=lambda s: _old_entry_score(s, ai_boost_map), reverse=True)
    old_order = [s['symbol'] for s in old_sorted]

    print("=" * 72)
    print(f"  排序对比（共 {len(candidates)} 只候选）")
    print("=" * 72)
    print(f"  {'#':>3}  {'新(rank_tier)':<22}{'旧(ai_boost+0.5)':<22} 同?")
    for i in range(len(candidates)):
        n, o = new_order[i], old_order[i]
        flag = '✓' if n == o else '✗'
        n_ai = '⭐' if n.upper() in ai_set else '  '
        print(f"  {i+1:>3}  {n_ai}{n:<20}{o:<22} {flag}")
    order_same = new_order == old_order
    # 跨池结构是否一致：AI 票是否都排在非 AI 票之前
    def _tier_blocks(order):
        return [s.upper() in ai_set for s in order]
    cross_same = _tier_blocks(new_order) == _tier_blocks(old_order)
    print(f"\n  完全同序: {order_same}   跨池分层(AI 全在前)一致: {cross_same}")
    if not order_same:
        # 找出仅在 AI 池内部错位的对
        reorders = [(i+1, new_order[i], old_order[i]) for i in range(len(candidates))
                    if new_order[i] != old_order[i]]
        all_ai = all(a.upper() in ai_set and b.upper() in ai_set for _, a, b in reorders)
        print(f"  错位 {len(reorders)} 处，是否全部发生在 AI 池内部: {all_ai}")
        print("  （AI 池内部错位 = 删除 ai_boost 追踪器评分微调的预期结果，不影响跨池下单结构）")

    # ── 2) 选股 diff（多场景）────────────────────────────────────
    print("\n" + "=" * 72)
    print("  选股对比（假设候选均可下单，隔离结构性分配逻辑）")
    print("=" * 72)
    max_non_ai = getattr(config, 'MAX_NON_AI_POS', 1)
    sector_cap = config.MAX_PER_SECTOR

    scenarios = [
        ("空仓 cap=6", dict(slots=6, global_cap=6, real_held=0, sector_counts={}, non_ai_held=0)),
        ("空仓 cap=4(熊市/宽度)", dict(slots=4, global_cap=4, real_held=0, sector_counts={}, non_ai_held=0)),
        ("已持2只(1非AI Tech) cap=6", dict(slots=4, global_cap=6, real_held=2,
                                           sector_counts={'Technology': 2}, non_ai_held=1)),
    ]
    all_match = True
    for label, sc in scenarios:
        old_picks = _old_select(old_sorted, ai_set=ai_set, sector_cap=sector_cap,
                                 max_non_ai=max_non_ai, **sc)
        new_picks = _new_select(candidates, policies, sector_cap=sector_cap, **sc)
        match = old_picks == new_picks
        all_match &= match
        print(f"\n  [{label}]  一致: {'✓' if match else '✗ 不一致!'}")
        print(f"    旧: {old_picks}")
        print(f"    新: {new_picks}")

    print("\n" + "=" * 72)
    print(f"  结论：选股列表全场景一致 = {all_match}   完全同序 = {order_same}")
    print(f"        (ENTRY_AI_TRACKER_BOOST_MAX 默认 0.20 复刻历史 ai_boost；")
    print(f"         ai_priority_bonus(+0.5) 已删，跨池改由 rank_tier 承载)")
    print("=" * 72)


if __name__ == '__main__':
    main()
