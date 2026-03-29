import pandas as pd


def compute_rs_score(
    df: pd.DataFrame,
    spy_close: pd.Series | None,
    period: int = 63,
) -> pd.DataFrame:
    """
    添加 rs_score 列：个股 N 日收益率 - SPY N 日收益率。
    rs_score > 0 = 跑赢 SPY，< 0 = 跑输 SPY。
    spy_close 为 None 时直接用个股绝对收益率。
    """
    stock_ret = df['close'] / df['close'].shift(period) - 1
    if spy_close is not None:
        spy = spy_close.reindex(df.index).ffill()
        spy_ret = spy / spy.shift(period) - 1
        df['rs_score'] = stock_ret - spy_ret
    else:
        df['rs_score'] = stock_ret
    return df
