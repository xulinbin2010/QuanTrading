"""
股票池策略抽象（PoolPolicy）+ 入场得分 + 统一槽位分配器（PoolAllocator）。

把原先散落在 auto_trader 4 处的「AI 优先」意图收敛到一处：
  1. 双路扫描参数（宽松/严格）       → PoolPolicy.signal_params
  2. 跨池置顶（原 ai_priority_bonus）  → PoolPolicy.rank_tier（0=最优先）
  3. 入场得分（原 ai_boost 已删）      → entry_score()，权重进 config
  4. 行业豁免 + 非 AI 压制            → PoolAllocator（sector_cap_exempt + max_positions 池配额）

设计要点：
- 本模块不 import auto_trader（避免循环依赖）；ai_universe 成员加载内聚到这里。
- entry_score 不再含 ai_boost 乘法项和 +0.5 加法项：跨池次序由 rank_tier 元组承载，
  入场得分只比同池内的技术质量（rs × 综合 boost）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import config

_ROOT = Path(__file__).resolve().parents[1]


# ══════════════════════════════════════════════════════════════════
#  PoolPolicy
# ══════════════════════════════════════════════════════════════════

@dataclass
class PoolPolicy:
    """单个股票池的策略配置。"""
    name: str                       # 'ai_priority' / 'sp500_base'
    members: set[str] | None        # 成员集合（大写）；None = 兜底池（不在其他池的都归这里）
    signal_params: dict             # 传给 RSMomentum 的覆盖参数（proximity_pct / vol_multiplier 等）
    rank_tier: int                  # 0 = 最优先（替代 ai_priority_bonus），数值越小越靠前
    max_positions: int | None       # 本池持仓配额（含存量口径）；None = 不限制
    sector_cap_exempt: bool         # True = 豁免 MAX_PER_SECTOR 且不计入行业计数
    stop_overrides: dict | None = None   # 预留：阶段 3 主题熔断 / 止损覆盖用

    def contains(self, symbol: str) -> bool:
        return self.members is not None and symbol.upper() in self.members


# ══════════════════════════════════════════════════════════════════
#  ai_universe.json 成员加载
# ══════════════════════════════════════════════════════════════════

def load_ai_priority_set() -> set[str]:
    """
    实盘 AI 优先池成员（大写集合）= ai_universe.json 里 trade_priority 为真的成员。

    watchlist 与实盘优先池解耦：ai_universe.json 顶层 `trade_priority` 映射
    {symbol: bool} 控制某只是否进入实盘优先池。缺省（不在映射里）视为 True，
    保持向后兼容（历史成员全部享受优先池待遇）；UI 新增成员默认 False（研究观察）。
    注意：AI 追踪页的 watchlist 展示仍读全部 symbols，不受此过滤影响。
    """
    f = _ROOT / 'data' / 'ai_universe.json'
    if not f.exists():
        return set()
    try:
        u = json.loads(f.read_text(encoding='utf-8'))
        tp = {str(k).upper(): bool(v) for k, v in (u.get('trade_priority') or {}).items()}
        out: set[str] = set()
        for gv in u.get('groups', {}).values():
            for s in gv.get('symbols', []):
                su = str(s).upper()
                if tp.get(su, True):   # 缺省 True，向后兼容
                    out.add(su)
        return out
    except Exception:
        return set()


def load_ai_tracker_boost_map(max_boost: float | None = None) -> dict[str, float]:
    """
    返回 {symbol: ai_tracker_boost}，叠加到 entry_score 的乘法项，仅影响 AI 池内次序。

    规则（max_boost 默认取 config.ENTRY_AI_TRACKER_BOOST_MAX=0.20，等价历史行为）：
      - AI 成员且有追踪器评分(0–15)：boost = min((score/15) × max_boost, max_boost)
      - AI 成员但无评分缓存：        boost = max_boost / 2   （历史固定 0.10 = 0.20/2）
      - 非 AI 成员：                  boost = 0.0
    max_boost=0 时全部为 0 → 完全取消追踪器对排序的影响。
    """
    if max_boost is None:
        max_boost = getattr(config, 'ENTRY_AI_TRACKER_BOOST_MAX', 0.20)
    boost: dict[str, float] = {}
    if max_boost <= 0:
        return boost
    base = max_boost / 2.0
    for s in load_ai_priority_set():
        boost[s] = base
    if not boost:
        return boost
    cf = _ROOT / 'data' / 'ai_tracker_cache.json'
    if cf.exists():
        try:
            cached = json.loads(cf.read_text(encoding='utf-8'))
            for row in cached.get('rows', []):
                sym = str(row.get('symbol', '')).upper()
                sc  = row.get('score')
                if sym in boost and sc is not None:
                    boost[sym] = min((sc / 15) * max_boost, max_boost)
        except Exception:
            pass
    return boost


# ══════════════════════════════════════════════════════════════════
#  策略池构建
# ══════════════════════════════════════════════════════════════════

def build_pool_policies(
    ai_set: set[str] | None = None,
    ai_theme_brake: bool = False,
) -> list[PoolPolicy]:
    """
    构建默认双池：AI 优先池（tier 0，宽松扫描，行业豁免，无配额）+
    SP500 兜底池（tier 1，严格扫描，受行业上限，配额 = MAX_NON_AI_POS）。

    ai_theme_brake=True（AI 主题熔断触发）时，AI 池降级：signal_params 回退严格、
    sector_cap_exempt=False；但仍保留 rank_tier=0（不清空/不降优先级，只收紧扫描与集中度）。

    返回按 rank_tier 升序排列的列表（AI 在前）。
    """
    if ai_set is None:
        ai_set = load_ai_priority_set()

    ai_policy = PoolPolicy(
        name='ai_priority',
        members=ai_set,
        # 正常：宽松扫描（近高点 -15%、取消放量）；熔断：回退严格（与 base 同参数）
        signal_params={} if ai_theme_brake else {'breakout_proximity_pct': 0.15, 'vol_multiplier': 0.0},
        rank_tier=0,
        max_positions=None,           # AI 主线不设池配额
        sector_cap_exempt=(not ai_theme_brake),  # 熔断时取消行业豁免，防 AI 弱势期书向半导体过度集中
    )
    base_policy = PoolPolicy(
        name='sp500_base',
        members=None,                 # 兜底：不在 AI 池的都归这里
        signal_params={},             # 严格：用 RSMomentum 默认 5 条件
        rank_tier=1,
        # MAX_NON_AI_POS 语义迁移为本池配额（保留旧 config key 做兼容映射）
        max_positions=getattr(config, 'MAX_NON_AI_POS', 1),
        sector_cap_exempt=False,
    )
    return [ai_policy, base_policy]


AI_THEME_ETF = 'SMH'   # 半导体 ETF，作为 AI 主题相对强度代理


def ai_theme_brake_series(smh_close, spy_close, ma: int | None = None):
    """
    AI 主题熔断逐日布尔序列：SMH/SPY 相对强度比值 < 其 N 日均线 → True（AI 主题走弱）。

    用 DataStore 现有 SMH + SPY 收盘价计算，不引入新数据源。返回与 spy_close 同索引的
    bool Series（数据不足 ma+1 行的前段为 False）。
    """
    import pandas as pd
    if ma is None:
        ma = getattr(config, 'AI_THEME_BRAKE_MA', 50)
    if smh_close is None or spy_close is None or len(smh_close) == 0:
        return pd.Series(False, index=getattr(spy_close, 'index', None))
    spy = spy_close.copy()
    smh = smh_close.reindex(spy.index).ffill()
    ratio = smh / spy
    ma_series = ratio.rolling(ma).mean()
    brake = (ratio < ma_series)
    return brake.fillna(False)


def ai_theme_brake_now(smh_close, spy_close, ma: int | None = None) -> bool:
    """当前是否触发 AI 主题熔断（取逐日序列最后一个值）。受 AI_THEME_BRAKE_ENABLED 开关控制（默认关闭）。"""
    if not getattr(config, 'AI_THEME_BRAKE_ENABLED', False):
        return False
    s = ai_theme_brake_series(smh_close, spy_close, ma)
    if s is None or len(s) == 0:
        return False
    return bool(s.iloc[-1])


def classify(symbol: str, policies: list[PoolPolicy]) -> PoolPolicy:
    """给 symbol 找归属池：先匹配有成员名单的池，否则落到兜底池（members=None）。"""
    sym = symbol.upper()
    for p in policies:
        if p.members is not None and sym in p.members:
            return p
    for p in policies:
        if p.members is None:
            return p
    return policies[-1]


# ══════════════════════════════════════════════════════════════════
#  入场得分（共享：auto_trader.scan_signals + backtest_rs）
# ══════════════════════════════════════════════════════════════════

def entry_score(
    sig: dict,
    *,
    vol_boost_max: float = 0.15,
    proximity_boost_max: float = 0.10,
    insider_boost_max: float = 0.10,
) -> float:
    """
    入场得分（同池内排序用）：rs × (1 + 量比加成 + 近高点加成 + 内幕加成 + AI 追踪器加成)。

    与旧版差异：删除了 +0.5 置顶项（ai_priority_bonus）——跨池次序改由 rank_tier 承载。
    AI 追踪器加成（原 ai_boost）保留为 sig['ai_tracker_boost']，权重由 config
    ENTRY_AI_TRACKER_BOOST_MAX 控制，仅影响 AI 池内次序（不参与跨池），默认值与历史一致。

    买入候选恒满足 rs > 0（RSMomentum 硬条件），故乘法 boost 恒正向放大，无负值放大隐患。
    权重 vol/proximity/insider 默认与历史一致，实际由 config 注入（DB 可改）。
    """
    rs       = sig.get('rs_score', 0) or 0
    vol      = sig.get('vol_ratio', 1.0) or 1.0
    drawdown = sig.get('drawdown_from_high', -0.15)
    if drawdown is None:
        drawdown = -0.15
    insider  = sig.get('insider_score', 0) or 0
    ai_boost = sig.get('ai_tracker_boost', 0.0) or 0.0

    vol_boost       = min(vol / 3.0, 1.0) * vol_boost_max
    proximity_boost = max(0.0, (drawdown + 0.30) / 0.30) * proximity_boost_max
    insider_boost   = min(insider / 10.0, 1.0) * insider_boost_max
    return rs * (1 + vol_boost + proximity_boost + insider_boost + ai_boost)


def entry_score_from_config(sig: dict) -> float:
    """从 config 读权重的便捷包装（DB config_store 改动即时生效）。"""
    return entry_score(
        sig,
        vol_boost_max=getattr(config, 'ENTRY_VOL_BOOST_MAX', 0.15),
        proximity_boost_max=getattr(config, 'ENTRY_PROXIMITY_BOOST_MAX', 0.10),
        insider_boost_max=getattr(config, 'ENTRY_INSIDER_BOOST_MAX', 0.10),
    )


def sort_key(sig: dict) -> tuple:
    """排序 key：(rank_tier 升序, entry_score 降序)。配合 sorted(...) 默认升序使用。"""
    return (sig.get('rank_tier', 99), -entry_score_from_config(sig))


# ══════════════════════════════════════════════════════════════════
#  统一槽位分配器
# ══════════════════════════════════════════════════════════════════

class PoolAllocator:
    """
    统一槽位分配：每池配额 + 全局上限 + 行业上限（按池 exempt）。

    用法（在已按 sort_key 排好序的候选上逐一处理）：
        alloc = PoolAllocator(policies, global_slots=slots, global_cap=eff_max_pos,
                              real_held_count=real_held, sector_cap=MAX_PER_SECTOR,
                              sector_counts=sector_counts, pool_counts={'sp500_base': non_ai_held})
        for sig in candidates:
            if alloc.should_stop():
                break
            # 执行前置检查（held / pending）由 caller 处理，与旧逻辑对齐
            reason = alloc.structural_skip(sig)   # 行业/池配额
            if reason:
                print(reason); continue
            # 执行特定检查（earnings / qty<=0）由 caller 处理
            ... place order ...
            alloc.commit(sig)

    结构性决策（slots/全局上限/硬闸/行业/池配额）在此；执行特定决策
    （已持仓/财报/qty）留在 caller——后者跳过的候选不应消耗行业/池配额，
    故 commit() 仅在真正下单后调用。
    """

    def __init__(
        self,
        policies: list[PoolPolicy],
        *,
        global_slots: int,
        global_cap: int,
        real_held_count: int,
        sector_cap: int,
        sector_counts: dict[str, int] | None = None,
        pool_counts: dict[str, int] | None = None,
    ):
        self.policies      = {p.name: p for p in policies}
        self._policy_list  = policies
        self.global_slots  = global_slots
        self.global_cap    = global_cap
        self.real_held     = real_held_count
        self.sector_cap    = sector_cap
        self.sector_counts = dict(sector_counts or {})
        self.pool_counts   = dict(pool_counts or {})
        self.executed      = 0

    def _policy_of(self, sig: dict) -> PoolPolicy:
        name = sig.get('pool')
        if name and name in self.policies:
            return self.policies[name]
        return classify(sig['symbol'], self._policy_list)

    def should_stop(self) -> bool:
        """全局槽位用尽，或实仓硬闸触发（真实持仓 + 本轮已买 ≥ 上限）。"""
        if self.executed >= self.global_slots:
            return True
        if self.real_held + self.executed >= self.global_cap:
            return True
        return False

    def stop_reason(self) -> str:
        if self.real_held + self.executed >= self.global_cap:
            return (f"[实仓硬闸] IB 实际持仓 {self.real_held} + 本轮已买 {self.executed} "
                    f"已达上限 {self.global_cap} → 停止买入（预期卖出未成交不腾槽，防超仓）")
        return f"仓位已满（已买 {self.executed} / 槽位 {self.global_slots}）"

    def structural_skip(self, sig: dict) -> str | None:
        """行业上限 + 池配额检查；通过返回 None，否则返回可打印的跳过原因。"""
        pol = self._policy_of(sig)
        sec = sig.get('sector') or 'Unknown'
        if not pol.sector_cap_exempt and self.sector_counts.get(sec, 0) >= self.sector_cap:
            return (f"行业[{sec}]已持有 {self.sector_counts[sec]} 只"
                    f"（上限 {self.sector_cap}），跳过")
        if pol.max_positions is not None and self.pool_counts.get(pol.name, 0) >= pol.max_positions:
            return (f"非 AI 动量票({pol.name})已达配额 {pol.max_positions} 只"
                    f"（书向 AI 主线倾斜），跳过")
        return None

    def commit(self, sig: dict):
        """真正下单后调用：占用一个全局槽，并按池规则占用行业/池配额。"""
        pol = self._policy_of(sig)
        self.executed += 1
        if not pol.sector_cap_exempt:
            sec = sig.get('sector') or 'Unknown'
            self.sector_counts[sec] = self.sector_counts.get(sec, 0) + 1
            self.pool_counts[pol.name] = self.pool_counts.get(pol.name, 0) + 1
