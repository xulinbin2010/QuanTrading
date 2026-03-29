import pandas as pd


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """
    添加 atr{period} 列（真实波幅均值，用于仓位 sizing）。
    列名固定为 atr14（period=14 时）以兼容下游消费方。
    """
    hl = df['high'] - df['low']
    hc = (df['high'] - df['close'].shift(1)).abs()
    lc = (df['low']  - df['close'].shift(1)).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df[f'atr{period}'] = tr.rolling(period).mean()
    return df
