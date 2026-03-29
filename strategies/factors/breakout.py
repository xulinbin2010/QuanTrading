import pandas as pd


def compute_breakout(df: pd.DataFrame, period: int = 50) -> pd.DataFrame:
    """
    添加 prev_high、breakout 列。
    breakout = 收盘价 > 前 period 日最高收盘价（shift(1) 排除当天自身）。
    prev_high 是中间列，供下游使用后可自行丢弃。
    """
    df['prev_high'] = df['close'].shift(1).rolling(period).max()
    df['breakout'] = df['close'] > df['prev_high']
    return df
