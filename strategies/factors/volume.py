import pandas as pd


def compute_volume_ma(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """添加 vol_ma20 列（成交量 N 日均线）。"""
    df['vol_ma20'] = df['volume'].rolling(period).mean()
    return df


def compute_volume_surge(df: pd.DataFrame, multiplier: float = 1.5) -> pd.DataFrame:
    """
    添加 vol_surge 列：当日成交量 > vol_ma20 × multiplier。
    需先调用 compute_volume_ma() 确保 vol_ma20 列存在。
    """
    df['vol_surge'] = df['volume'] > df['vol_ma20'] * multiplier
    return df


def compute_volume_divergence(
    df: pd.DataFrame,
    breakout_period: int = 50,
    shrink_ratio: float = 0.7,
) -> pd.DataFrame:
    """
    添加 at_new_high、vol_shrink 列（量价背离顶部信号）。
    at_new_high = 价格达到近 breakout_period 日新高
    vol_shrink  = 成交量低于 vol_ma20 × shrink_ratio（显著缩量）
    需先调用 compute_volume_ma() 确保 vol_ma20 列存在。
    """
    df['at_new_high'] = df['close'] >= df['close'].rolling(breakout_period).max()
    df['vol_shrink'] = df['volume'] < df['vol_ma20'] * shrink_ratio
    return df
