import pandas as pd


def compute_drawdown_filter(
    df: pd.DataFrame,
    max_drawdown: float = -0.30,
    lookback: int = 252,
) -> pd.DataFrame:
    """
    添加 drawdown_from_high、not_crashed 列。
    not_crashed = 距 lookback 日最高点回撤未超过 max_drawdown（如 -30%）。
    min_periods=63 确保至少有3个月数据才计算。
    """
    high = df['close'].rolling(lookback, min_periods=63).max()
    df['drawdown_from_high'] = (df['close'] - high) / high
    df['not_crashed'] = df['drawdown_from_high'] > max_drawdown
    return df
