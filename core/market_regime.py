"""
Market Strength Score (MSS) — 市场强度评分

MSS ∈ [-1, +1]，由三个维度加权合成：
  SPY趋势分   = (SPY > MA20) × 0.2 + (SPY > MA50) × 0.2      → [0, 0.4]
  市场宽度分   = breadth_pct × 0.4                              → [0, 0.4]
  VIX得分     = clip((30 - VIX) / 100, -0.2, +0.2)            → [-0.2, +0.2]

MSS 用途：
  ≥ 0.5   强牛市 → 放宽止损、增加仓位上限
  0~0.5   温和   → 使用 config 默认值
  < 0.0   弱势   → 收紧止损、压缩仓位
"""
from __future__ import annotations
import pandas as pd


def compute_mss(
    spy_close:   pd.Series,
    vix:         float | None,
    breadth_pct: float | None,
) -> float:
    """单点 MSS，供 auto_trader / scan_signals 实时使用。"""
    if len(spy_close) < 50:
        return 0.0
    ma20 = float(spy_close.rolling(20).mean().iloc[-1])
    ma50 = float(spy_close.rolling(50).mean().iloc[-1])
    cur  = float(spy_close.iloc[-1])

    spy_score     = (0.2 if cur > ma20 else 0.0) + (0.2 if cur > ma50 else 0.0)
    breadth_score = float(breadth_pct) * 0.4 if breadth_pct is not None else 0.2
    vix_score     = min(0.2, max(-0.2, (30.0 - vix) / 100.0)) if vix is not None else 0.0

    return min(1.0, max(-1.0, spy_score + breadth_score + vix_score))


def compute_mss_series(
    spy_close:      pd.Series,
    vix_series:     pd.Series,
    breadth_series: pd.Series,
) -> pd.Series:
    """逐日 MSS 序列，供 backtest_rs 回测循环使用。"""
    if len(spy_close) < 50:
        return pd.Series(0.0, index=spy_close.index)

    ma20 = spy_close.rolling(20).mean()
    ma50 = spy_close.rolling(50).mean()
    spy_score = (
        (spy_close > ma20).astype(float) * 0.2
        + (spy_close > ma50).astype(float) * 0.2
    )
    breadth_al    = breadth_series.reindex(spy_close.index).ffill().fillna(0.5)
    breadth_score = breadth_al * 0.4
    vix_al        = vix_series.reindex(spy_close.index).ffill().fillna(20.0)
    vix_score     = ((30.0 - vix_al) / 100.0).clip(-0.2, 0.2)

    return (spy_score + breadth_score + vix_score).clip(-1.0, 1.0)


def mss_label(mss: float) -> str:
    if mss >= 0.5:
        return '强牛市'
    if mss >= 0.0:
        return '温和'
    return '弱势/熊市'
