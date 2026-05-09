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
        # 统一 DatetimeIndex 单位，避免 pandas 2.x "Cannot losslessly convert units"
        idx = df.index.as_unit('us') if hasattr(df.index, 'as_unit') else df.index
        spy_aligned = spy_close.copy()
        if hasattr(spy_aligned.index, 'as_unit'):
            spy_aligned.index = spy_aligned.index.as_unit('us')
        spy = spy_aligned.reindex(idx).ffill()
        spy_ret = spy / spy.shift(period) - 1
        df['rs_score'] = stock_ret - spy_ret
    else:
        df['rs_score'] = stock_ret
    return df
