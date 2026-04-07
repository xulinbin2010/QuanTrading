import numpy as np
import pandas as pd


def compute_momentum_quality(
    df: pd.DataFrame,
    period: int = 63,
) -> pd.DataFrame:
    """
    添加 momentum_quality 列（动量质量，范围 0-1）。

    计算方式：log(close) 在过去 period 天的线性回归 R²。
      - 高 R²（≈1）= 价格沿直线平稳上涨，趋势性强
      - 低 R²（≈0）= 价格震荡、跳空，动量噪音大

    与 rs_score 配合使用：rs_score 衡量涨幅大小，momentum_quality 衡量涨幅质量。
    一个 R²>0.85、rs_score>0 的股票比单纯高 rs_score 但震荡的股票更可信。
    """
    log_close = np.log(df['close'].clip(lower=1e-9))

    def _r_squared(vals: np.ndarray) -> float:
        if len(vals) < period or np.isnan(vals).any():
            return np.nan
        x = np.arange(len(vals), dtype=float)
        # corrcoef 计算 x 和 y 的皮尔逊相关系数，R² = r²
        corr = np.corrcoef(x, vals)[0, 1]
        return corr * corr

    df['momentum_quality'] = (
        log_close
        .rolling(period)
        .apply(_r_squared, raw=True)
    )
    return df
