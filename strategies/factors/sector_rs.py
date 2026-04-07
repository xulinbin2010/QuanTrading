"""
行业相对强度因子

通过 GICS 行业 ETF 代理，计算：
  sector_rs       = 行业ETF收益 - SPY收益（行业跑赢/跑输大盘的幅度）
  stock_vs_sector = 个股收益 - 行业ETF收益（个股在行业内的相对强度）

组合解读：
  stock_vs_sector > 0 AND sector_rs > 0 → 个股跑赢行业，行业也跑赢大市（最强）
  stock_vs_sector > 0 AND sector_rs < 0 → 个股在弱势行业中相对强，需谨慎
  stock_vs_sector < 0 → 个股在行业内处于弱势，不推荐买入

计算依赖：
  - spy_close: SPY 收盘价 Series（已在 factor_svc 中加载）
  - sector_etf_close: 该股票对应行业 ETF 的收盘价 Series（factor_svc 额外加载）
  - 若 sector_etf_close 为 None（未知行业），退化为仅与 SPY 比较
"""
from __future__ import annotations
import pandas as pd

# GICS 行业 → ETF 代码映射（yfinance sector 字段的常见值）
SECTOR_ETFS: dict[str, str] = {
    'Technology':              'XLK',
    'Healthcare':              'XLV',
    'Financial Services':      'XLF',
    'Consumer Cyclical':       'XLY',
    'Communication Services':  'XLC',
    'Industrials':             'XLI',
    'Consumer Defensive':      'XLP',
    'Energy':                  'XLE',
    'Basic Materials':         'XLB',
    'Real Estate':             'XLRE',
    'Utilities':               'XLU',
}

# 所有需要预加载的行业 ETF 列表
ALL_SECTOR_ETFS: list[str] = list(set(SECTOR_ETFS.values()))


def compute_sector_rs(
    df: pd.DataFrame,
    sector_etf_close: pd.Series | None = None,
    spy_close: pd.Series | None = None,
    period: int = 63,
) -> pd.DataFrame:
    """
    添加 sector_rs、stock_vs_sector 列。

    参数：
      df               : 个股 OHLCV DataFrame（含 DatetimeIndex）
      sector_etf_close : 对应行业 ETF 的收盘价 Series，None 表示行业未知
      spy_close        : SPY 收盘价 Series
      period           : 收益率计算窗口（交易日）
    """
    if spy_close is None:
        df['sector_rs']       = 0.0
        df['stock_vs_sector'] = 0.0
        return df

    spy = spy_close.reindex(df.index).ffill()
    spy_ret = spy / spy.shift(period) - 1

    stock_ret = df['close'] / df['close'].shift(period) - 1

    if sector_etf_close is not None:
        sect = sector_etf_close.reindex(df.index).ffill()
        sect_ret = sect / sect.shift(period) - 1
        df['sector_rs']       = sect_ret - spy_ret
        df['stock_vs_sector'] = stock_ret - sect_ret
    else:
        # 行业未知：退化为个股 vs SPY
        df['sector_rs']       = 0.0
        df['stock_vs_sector'] = stock_ret - spy_ret

    return df
