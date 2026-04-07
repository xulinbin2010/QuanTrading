import pandas as pd


def compute_volatility_filter(
    df: pd.DataFrame,
    max_atr_pct: float = 0.05,
) -> pd.DataFrame:
    """
    添加 atr_pct、vol_ok 列（波动率过滤）。

    atr_pct = ATR14 / close（归一化波动率）
    vol_ok  = atr_pct <= max_atr_pct

    过滤掉波动率过高的股票（默认 ATR/价格 > 5% 则排除）。
    ATR/价格 过高意味着仓位被迫压缩，或止损太宽，不适合该策略的风控体系。

    依赖：需先调用 compute_atr(df, period=14)，确保 atr14 列存在。
    """
    if 'atr14' not in df.columns:
        from .atr import compute_atr
        df = compute_atr(df, period=14)

    df['atr_pct'] = df['atr14'] / df['close']
    df['vol_ok'] = df['atr_pct'] <= max_atr_pct
    return df
