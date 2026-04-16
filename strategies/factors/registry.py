"""
因子注册表：统一描述所有因子的元数据、参数、类型。

两类因子：
  - technical   : 输入为 OHLCV DataFrame，输出为添加了新列的 DataFrame
  - fundamental : 输入为 yfinance info dict（快照），输出为 {col: value} 标量字典
                  基本面因子仅用于因子看板扫描，不参与时序回测。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class FactorMeta:
    key: str                          # 唯一标识，如 "rs_score"
    name: str                         # 中文名，如 "RS 相对强度"
    category: str                     # 分类：momentum / volume / trend / growth / quality / value
    data_type: str                    # "technical" | "fundamental"
    compute_fn: Callable              # 纯函数引用
    output_columns: list[str]         # 输出到 df 的列名（technical）或 dict key（fundamental）
    signal_column: str                # 主信号列，用于排序/过滤逻辑
    signal_type: str                  # "score"（连续，越高越好）| "filter"（布尔，True=通过）
    params: dict                      # {param_name: (default, type, 描述)}
    default_enabled: bool = True      # 默认是否启用
    is_dependency: bool = False       # 纯依赖项（不作为独立信号，不出现在优化器/UI 可选列表）
    display_only: bool = False        # 仅展示用，不参与买卖信号/回测/优化


# ── 延迟导入避免循环依赖 ─────────────────────────────────────────

def _build_registry() -> dict[str, FactorMeta]:
    from .rs_score        import compute_rs_score
    from .breakout        import compute_breakout
    from .volume          import compute_volume_ma, compute_volume_surge, compute_volume_divergence
    from .volume_profile  import compute_obv_trend
    from .drawdown        import compute_drawdown_filter
    from .atr             import compute_atr
    from .trend           import compute_trend_filter
    from .volatility      import compute_volatility_filter
    from .momentum_quality import compute_momentum_quality
    from .fundamental import (
        compute_revenue_growth, compute_earnings_growth,
        compute_roe, compute_debt_to_equity, compute_fcf_yield,
        compute_pe_ratio, compute_pb_ratio,
    )
    from .earnings_avoid import compute_earnings_avoid_placeholder
    from .sector_rs      import compute_sector_rs

    return {
        # ── 动量因子 ───────────────────────────────────────────
        "rs_score": FactorMeta(
            key="rs_score", name="RS 相对强度", category="momentum",
            data_type="technical", compute_fn=compute_rs_score,
            output_columns=["rs_score"], signal_column="rs_score",
            signal_type="score",
            params={"period": (63, int, "RS 计算窗口（交易日）")},
        ),
        "breakout": FactorMeta(
            key="breakout", name="价格突破", category="momentum",
            data_type="technical", compute_fn=compute_breakout,
            output_columns=["prev_high", "breakout"], signal_column="breakout",
            signal_type="filter",
            params={"period": (50, int, "突破判断窗口（交易日）")},
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
        # ── 行业相对强度因子 ───────────────────────────────────
        "sector_rs": FactorMeta(
            key="sector_rs", name="行业相对强度", category="momentum",
            data_type="technical", compute_fn=compute_sector_rs,
            output_columns=["sector_rs", "stock_vs_sector"],
            signal_column="stock_vs_sector",
            signal_type="score",
            params={"period": (63, int, "行业RS计算窗口（交易日），建议与RS窗口一致")},
            default_enabled=False,
        ),
        # ── 动量质量因子 ───────────────────────────────────────
        "momentum_quality": FactorMeta(
            key="momentum_quality", name="动量质量", category="momentum",
            data_type="technical", compute_fn=compute_momentum_quality,
            output_columns=["momentum_quality"], signal_column="momentum_quality",
            signal_type="score",
            params={"period": (63, int, "回归窗口（交易日），建议与 RS 窗口一致")},
            default_enabled=False,
        ),
        # ── 量能趋势因子 ───────────────────────────────────────
        "obv_trend": FactorMeta(
            key="obv_trend", name="OBV 资金趋势", category="volume",
            data_type="technical", compute_fn=compute_obv_trend,
            output_columns=["obv_trend"], signal_column="obv_trend",
            signal_type="score",
            params={"period": (20, int, "OBV 斜率计算窗口（交易日）")},
            default_enabled=False,
        ),
        # ── 波动率过滤因子 ─────────────────────────────────────
        "volatility_filter": FactorMeta(
            key="volatility_filter", name="波动率过滤", category="trend",
            data_type="technical", compute_fn=compute_volatility_filter,
            output_columns=["atr_pct", "vol_ok"], signal_column="vol_ok",
            signal_type="filter",
            params={"max_atr_pct": (0.05, float, "ATR/价格最大允许比例（如 0.05 = 5%）")},
            default_enabled=False,
        ),
        # ── 成长因子（基本面，快照）────────────────────────────
        "revenue_growth": FactorMeta(
            key="revenue_growth", name="营收增长率", category="growth",
            data_type="fundamental", compute_fn=compute_revenue_growth,
            output_columns=["revenue_growth"], signal_column="revenue_growth",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        "earnings_growth": FactorMeta(
            key="earnings_growth", name="盈利增长率", category="growth",
            data_type="fundamental", compute_fn=compute_earnings_growth,
            output_columns=["earnings_growth"], signal_column="earnings_growth",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        # ── 质量因子（基本面，快照）────────────────────────────
        "roe": FactorMeta(
            key="roe", name="ROE 净资产收益率", category="quality",
            data_type="fundamental", compute_fn=compute_roe,
            output_columns=["roe"], signal_column="roe",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        "debt_to_equity": FactorMeta(
            key="debt_to_equity", name="负债权益比", category="quality",
            data_type="fundamental", compute_fn=compute_debt_to_equity,
            output_columns=["debt_to_equity"], signal_column="debt_to_equity",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        "fcf_yield": FactorMeta(
            key="fcf_yield", name="自由现金流收益率", category="quality",
            data_type="fundamental", compute_fn=compute_fcf_yield,
            output_columns=["fcf_yield"], signal_column="fcf_yield",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        # ── 估值因子（基本面，快照）────────────────────────────
        "pe_ratio": FactorMeta(
            key="pe_ratio", name="市盈率 PE", category="value",
            data_type="fundamental", compute_fn=compute_pe_ratio,
            output_columns=["pe_ratio"], signal_column="pe_ratio",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        "pb_ratio": FactorMeta(
            key="pb_ratio", name="市净率 PB", category="value",
            data_type="fundamental", compute_fn=compute_pb_ratio,
            output_columns=["pb_ratio"], signal_column="pb_ratio",
            signal_type="score",
            params={},
            default_enabled=False,
        ),
        # ── 财报回避（快照，display_only）──────────────────────
        "earnings_avoid": FactorMeta(
            key="earnings_avoid", name="财报回避", category="quality",
            data_type="fundamental", compute_fn=compute_earnings_avoid_placeholder,
            output_columns=["earnings_safe"], signal_column="earnings_safe",
            signal_type="filter",
            params={"within_days": (2, int, "财报前回避天数（日历日）")},
            default_enabled=False,
            display_only=True,  # 仅判断当前时刻，无法历史回测
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
