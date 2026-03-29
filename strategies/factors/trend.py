import pandas as pd


def compute_trend_filter(
    df: pd.DataFrame,
    fast: int = 50,
    slow: int = 200,
) -> pd.DataFrame:
    """
    添加 ma50、ma200、uptrend 列（黄金交叉趋势过滤）。
    uptrend = MA_fast > MA_slow，减少熊市假突破。
    slow MA 使用 min_periods=100 允许历史数据较短时仍能计算。
    """
    df['ma50']    = df['close'].rolling(fast).mean()
    df['ma200']   = df['close'].rolling(slow, min_periods=100).mean()
    df['uptrend'] = df['ma50'] > df['ma200']
    return df
