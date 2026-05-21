"""
因子预计算缓存 — 供优化器使用。

将所有技术因子信号对全部股票预计算一次，每个因子组合回测只需从
缓存中做布尔列 AND 组合，省去 ~72,000 次重复 rolling-window 计算。

公开 API：
  PrecomputedCache        — 数据容器
  precompute_all_factors  — 构建缓存（每次优化调用一次）
  build_signal_from_cache — 为指定因子组合生成 signal 列（每次 combo 回测调用）
"""
from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd


@dataclass
class PrecomputedCache:
    """
    存储所有股票的完整因子列 + ATR + 市场宽度。

    signals[sym]：含全部技术因子输出列的 DataFrame（不含 signal 列）。
    atr_series[sym]：atr14 Series，供自适应止损使用。
    breadth_series：市场宽度（% 股票站上 MA200），按日期索引。
    spy_close：SPY 收盘价 Series。
    """
    signals:        dict[str, pd.DataFrame]
    atr_series:     dict[str, pd.Series]
    breadth_series: pd.Series
    spy_close:      pd.Series


def precompute_all_factors(
    stock_data: dict[str, pd.DataFrame],
    spy_close:  pd.Series,
) -> PrecomputedCache:
    """
    对 stock_data 中所有股票计算全部技术因子，返回 PrecomputedCache。

    每只股票只做一次 df.copy() 和一套 rolling-window 计算（使用注册表默认参数）。
    """
    from .factors.registry import get_registry
    registry = get_registry()

    # 只取技术因子（跳过基本面），按注册顺序
    tech_factors = [
        (key, meta)
        for key, meta in registry.items()
        if meta.data_type == 'technical'
    ]

    signals:    dict[str, pd.DataFrame] = {}
    atr_series: dict[str, pd.Series]   = {}

    for sym, raw_df in stock_data.items():
        if raw_df is None or len(raw_df) < 5:
            continue
        try:
            df = raw_df.copy()   # ← 每只股票仅一次 copy
            vol_ma_done = False

            for key, meta in tech_factors:
                # volume_surge / volume_divergence 需要 vol_ma20 先算好
                if key in ('volume_surge', 'volume_divergence') and not vol_ma_done:
                    vol_meta = registry['volume_ma']
                    default_params = {
                        p: v[0] for p, v in vol_meta.params.items()
                    }
                    df = vol_meta.compute_fn(df, **default_params)
                    vol_ma_done = True
                if key == 'volume_ma':
                    vol_ma_done = True

                # 构建调用参数（使用注册表默认值）
                if key == 'rs_score':
                    period  = meta.params['period'][0]
                    weights = meta.params.get('weights', ('',))[0]
                    df = meta.compute_fn(df, spy_close, period=period, weights=weights)
                else:
                    kw = {p: v[0] for p, v in meta.params.items()}
                    df = meta.compute_fn(df, **kw)

            signals[sym] = df

            # 提取 atr14（供自适应止损使用）
            if 'atr14' in df.columns:
                atr_series[sym] = df['atr14']

        except Exception:
            pass  # 数据不足时跳过，不影响其他股票

    # ── 市场宽度：% 股票站上 MA200 ──────────────────────────────
    breadth_series = pd.Series(dtype=float)
    close_cols = {
        s: df['close']
        for s, df in stock_data.items()
        if s in signals and len(df) >= 201
    }
    if close_cols:
        close_matrix  = pd.DataFrame(close_cols)
        ma200_matrix   = close_matrix.rolling(200).mean()
        breadth_series = (close_matrix > ma200_matrix).mean(axis=1)

    return PrecomputedCache(
        signals=signals,
        atr_series=atr_series,
        breadth_series=breadth_series,
        spy_close=spy_close,
    )


def build_signal_from_cache(
    full_df:  pd.DataFrame,
    factors:  list[str],
    registry: dict,
    score_threshold: float = 0.0,
) -> pd.DataFrame:
    """
    从预计算列构建该 combo 的 signal 列，返回仅含 (signal, rs_score) 的轻量 DataFrame。

    逻辑与 DynamicFactorStrategy.generate_signals() 完全一致：
      买入 = AND(filter 因子) AND (score 因子 > threshold)
      卖出 = volume_divergence 启用时：at_new_high & vol_shrink & signal != 1
    """
    buy_mask = pd.Series(True, index=full_df.index)

    for key in factors:
        if key not in registry:
            continue
        meta = registry[key]
        if meta.data_type != 'technical':
            continue
        col = meta.signal_column
        if col not in full_df.columns:
            continue

        if meta.signal_type == 'filter':
            if key == 'volume_divergence':
                continue   # 卖出信号，不参与买入
            buy_mask &= full_df[col].fillna(False).astype(bool)
        elif meta.signal_type == 'score':
            buy_mask &= full_df[col].fillna(-999) > score_threshold

    signal = pd.Series(0, index=full_df.index, dtype=int)
    signal[buy_mask] = 1

    # 量价背离卖出报警
    if 'volume_divergence' in factors:
        if 'at_new_high' in full_df.columns and 'vol_shrink' in full_df.columns:
            sell_alert = (
                full_df['at_new_high'].fillna(False).astype(bool)
                & full_df['vol_shrink'].fillna(False).astype(bool)
                & (signal != 1)
            )
            signal[sell_alert] = -1

    # 返回仅含必要列的轻量 DataFrame（signal + rs_score 用于排名）
    result = pd.DataFrame({'signal': signal}, index=full_df.index)
    if 'rs_score' in full_df.columns:
        result['rs_score'] = full_df['rs_score']
    else:
        result['rs_score'] = 0.0

    return result
