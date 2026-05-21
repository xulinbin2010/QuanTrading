import pandas as pd


def compute_breakout(df: pd.DataFrame, period: int = 50, proximity_pct: float = 0.0) -> pd.DataFrame:
    """
    添加 prev_high、breakout 列。
    breakout = 收盘价 >= 前 period 日最高收盘价 × (1 - proximity_pct)。
    proximity_pct=0.0（默认）表示必须严格突破新高；
    proximity_pct=0.05 表示在高点 95% 以内即视为"准突破"。
    prev_high 是中间列，供下游使用后可自行丢弃。
    """
    df['prev_high'] = df['close'].shift(1).rolling(period).max()
    threshold = df['prev_high'] * (1.0 - proximity_pct)
    df['breakout'] = df['close'] >= threshold
    return df
