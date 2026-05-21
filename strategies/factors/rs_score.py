import pandas as pd
import numpy as np


def _parse_weights(weights: str) -> list[tuple[int, float]] | None:
    """
    解析多窗口加权字符串，如 '21:0.4,63:0.3,126:0.2,252:0.1'。

    返回 [(period, weight), ...]，失败或为空时返回 None（落回单窗口模式）。
    权重不强制归一化（调用方按需处理）。
    """
    if not weights or not isinstance(weights, str):
        return None
    pairs: list[tuple[int, float]] = []
    for item in weights.split(','):
        item = item.strip()
        if not item:
            continue
        if ':' not in item:
            return None
        p_str, w_str = item.split(':', 1)
        try:
            p = int(p_str.strip())
            w = float(w_str.strip())
        except ValueError:
            return None
        if p <= 0 or w <= 0:
            return None
        pairs.append((p, w))
    return pairs or None


def compute_rs_score(
    df: pd.DataFrame,
    spy_close: pd.Series | None,
    period: int = 63,
    weights: str = "",
) -> pd.DataFrame:
    """
    添加 rs_score 列：个股 N 日收益率 - SPY N 日收益率。

    两种模式：
      - 单窗口（默认）：rs = stock_ret(period) - spy_ret(period)
      - 多窗口加权：传入 weights="21:0.4,63:0.3,126:0.2,252:0.1"
        rs = Σ wᵢ × [stock_retᵢ - spy_retᵢ] / Σ wᵢ

    spy_close 为 None 时退化为绝对收益率（不减 SPY）。
    """
    parsed = _parse_weights(weights)

    # ── 准备 SPY 对齐序列（多窗口共用）────────────────────────────
    spy: pd.Series | None = None
    if spy_close is not None:
        idx = df.index.as_unit('us') if hasattr(df.index, 'as_unit') else df.index
        spy_aligned = spy_close.copy()
        if hasattr(spy_aligned.index, 'as_unit'):
            spy_aligned.index = spy_aligned.index.as_unit('us')
        spy = spy_aligned.reindex(idx).ffill()

    def _rs_one(p: int) -> pd.Series:
        stock_ret = df['close'] / df['close'].shift(p) - 1
        if spy is None:
            return stock_ret
        spy_ret = spy / spy.shift(p) - 1
        return stock_ret - spy_ret

    if parsed:
        # 每行独立加权：缺失窗口（数据不够长导致 NaN）不参与，权重在剩余窗口间归一化。
        # 例：股票仅 100 天历史 + weights=21:0.4,63:0.3,126:0.2,252:0.1
        #     → 末行只用 21d 和 63d 两个窗口，权重重新分摊为 0.4/0.7 + 0.3/0.7
        rs_df = pd.concat([_rs_one(p) for p, _ in parsed], axis=1)
        rs_df.columns = list(range(len(parsed)))
        weights_arr = np.array([w for _, w in parsed], dtype=float)

        mask     = (~rs_df.isna()).to_numpy().astype(float)
        rs_vals  = rs_df.fillna(0).to_numpy()
        sum_w    = mask @ weights_arr
        weighted = (rs_vals * weights_arr).sum(axis=1)

        with np.errstate(divide='ignore', invalid='ignore'):
            rs_score = np.where(sum_w > 0, weighted / sum_w, np.nan)
        df['rs_score'] = pd.Series(rs_score, index=df.index)
    else:
        df['rs_score'] = _rs_one(period)

    return df
