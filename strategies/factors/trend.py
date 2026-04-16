import pandas as pd


def compute_trend_filter(
    df: pd.DataFrame,
    fast: int = 10,
    slow: int = 20,
) -> pd.DataFrame:
    """
    添加 ma_fast、ma_slow、uptrend 列（短期趋势过滤）。
    uptrend = MA_fast > MA_slow，过滤短期下行趋势。
    """
    df['ma_fast'] = df['close'].rolling(fast).mean()
    df['ma_slow'] = df['close'].rolling(slow).mean()
    df['uptrend'] = df['ma_fast'] > df['ma_slow']
    return df
