"""
因子注册表：统一描述所有因子的元数据、参数、类型。

仅含 RSMomentum 生产策略实际使用的技术因子（5 个买卖条件 + 2 个依赖项）：
  - rs_score / breakout / volume_surge / drawdown_filter / trend_filter  → 买入条件
  - volume_divergence                                                    → 卖出报警
  - volume_ma / atr                                                      → 依赖项（不作独立信号）

基本面快照（PE/PB/ROE/营收增长等）不在此注册表里：它们由 get_stock_info 直接产出，
在市场扫描表 / K 线详情中展示，不参与时序信号 / 回测 / 优化。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable


@dataclass
class FactorMeta:
    key: str                          # 唯一标识，如 "rs_score"
    name: str                         # 中文名，如 "RS 相对强度"
    category: str                     # 分类：momentum / volume / trend
    data_type: str                    # "technical"
    compute_fn: Callable              # 纯函数引用
    output_columns: list[str]         # 输出到 df 的列名
    signal_column: str                # 主信号列，用于排序/过滤逻辑
    signal_type: str                  # "score"（连续，越高越好）| "filter"（布尔，True=通过）
    params: dict                      # {param_name: (default, type, 描述)}
    default_enabled: bool = True      # 默认是否启用
    is_dependency: bool = False       # 纯依赖项（不作为独立信号，不出现在 UI 可选列表）
    display_only: bool = False        # 仅展示用，不参与买卖信号/回测


# ── 延迟导入避免循环依赖 ─────────────────────────────────────────

def _build_registry() -> dict[str, FactorMeta]:
    from .rs_score        import compute_rs_score
    from .breakout        import compute_breakout
    from .volume          import compute_volume_ma, compute_volume_surge, compute_volume_divergence
    from .drawdown        import compute_drawdown_filter
    from .atr             import compute_atr
    from .trend           import compute_trend_filter

    return {
        # ── 动量因子 ───────────────────────────────────────────
        "rs_score": FactorMeta(
            key="rs_score", name="RS 相对强度", category="momentum",
            data_type="technical", compute_fn=compute_rs_score,
            output_columns=["rs_score"], signal_column="rs_score",
            signal_type="score",
            params={
                "period":  (63, int, "RS 计算窗口（交易日，weights 为空时生效）"),
                "weights": ("", str, "多窗口加权（如 '21:0.4,63:0.3,126:0.2,252:0.1'），留空=单窗口"),
            },
        ),
        "breakout": FactorMeta(
            key="breakout", name="价格突破", category="momentum",
            data_type="technical", compute_fn=compute_breakout,
            output_columns=["prev_high", "breakout"], signal_column="breakout",
            signal_type="filter",
            params={
                "period":        (50,   int,   "突破判断窗口（交易日）"),
                "proximity_pct": (0.0,  float, "宽松度：0=严格创新高，0.05=在高点95%内即视为突破"),
            },
        ),
        # ── 成交量因子 ─────────────────────────────────────────
        "volume_ma": FactorMeta(
            key="volume_ma", name="成交量均线", category="volume",
            data_type="technical", compute_fn=compute_volume_ma,
            output_columns=["vol_ma20"], signal_column="vol_ma20",
            signal_type="score",
            params={"period": (20, int, "成交量均线窗口（交易日）")},
            is_dependency=True,   # 纯基础计算，供 volume_surge/volume_divergence 使用，不作为独立信号
        ),
        "volume_surge": FactorMeta(
            key="volume_surge", name="量能突破", category="volume",
            data_type="technical", compute_fn=compute_volume_surge,
            output_columns=["vol_surge"], signal_column="vol_surge",
            signal_type="filter",
            params={"multiplier": (1.5, float, "成交量超过均量的倍数才算放量")},
        ),
        "volume_divergence": FactorMeta(
            key="volume_divergence", name="量价背离", category="volume",
            data_type="technical", compute_fn=compute_volume_divergence,
            output_columns=["at_new_high", "vol_shrink"], signal_column="vol_shrink",
            signal_type="sell_alert",
            params={
                "breakout_period": (50, int, "判断新高的回看窗口"),
                "shrink_ratio": (0.7, float, "缩量阈值（低于均量×此比例触发）"),
            },
            default_enabled=True,
        ),
        # ── 趋势因子 ───────────────────────────────────────────
        "trend_filter": FactorMeta(
            key="trend_filter", name="趋势过滤", category="trend",
            data_type="technical", compute_fn=compute_trend_filter,
            output_columns=["ma_fast", "ma_slow", "uptrend"], signal_column="uptrend",
            signal_type="filter",
            params={
                "fast": (10, int, "快均线周期"),
                "slow": (20, int, "慢均线周期"),
            },
        ),
        # ── 风险控制因子 ───────────────────────────────────────
        "drawdown_filter": FactorMeta(
            key="drawdown_filter", name="崩跌过滤", category="momentum",
            data_type="technical", compute_fn=compute_drawdown_filter,
            output_columns=["drawdown_from_high", "not_crashed"], signal_column="not_crashed",
            signal_type="filter",
            params={
                "max_drawdown": (-0.30, float, "距52周高点最大允许跌幅"),
                "lookback": (252, int, "高点回看窗口（交易日）"),
            },
        ),
        "atr": FactorMeta(
            key="atr", name="ATR 波动率", category="momentum",
            data_type="technical", compute_fn=compute_atr,
            output_columns=["atr14"], signal_column="atr14",
            signal_type="score",
            params={"period": (14, int, "ATR 计算周期")},
            default_enabled=True,
            is_dependency=True,  # 用于ATR自适应止损，不作为独立买入信号
        ),
    }


# 惰性单例：第一次访问时构建，避免模块导入时的循环依赖
_registry: dict[str, FactorMeta] | None = None


def get_registry() -> dict[str, FactorMeta]:
    global _registry
    if _registry is None:
        _registry = _build_registry()
    return _registry


# 向外暴露的便捷别名
FACTOR_REGISTRY = property(lambda self: get_registry())
