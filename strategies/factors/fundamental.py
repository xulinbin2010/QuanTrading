"""
基本面因子函数。

签名：(info: dict) -> dict
  - 输入：yfinance Ticker.info 字典（已由 get_stock_info() 缓存）
  - 输出：{column_name: value} 标量字典，value 可为 None（数据缺失时）

这些因子是快照型，不产生时序数据，仅用于因子看板横截面扫描。
"""
from __future__ import annotations


def compute_revenue_growth(info: dict) -> dict:
    """营收同比增长率（yfinance revenueGrowth，已为小数）"""
    v = info.get('revenue_growth')
    return {'revenue_growth': round(float(v), 4) if v is not None else None}


def compute_earnings_growth(info: dict) -> dict:
    """每股盈利同比增长率（yfinance earningsGrowth，已为小数）"""
    v = info.get('earnings_growth')
    return {'earnings_growth': round(float(v), 4) if v is not None else None}


def compute_roe(info: dict) -> dict:
    """净资产收益率 ROE（yfinance returnOnEquity，已为小数）"""
    v = info.get('roe')
    return {'roe': round(float(v), 4) if v is not None else None}


def compute_debt_to_equity(info: dict) -> dict:
    """
    负债权益比 D/E。
    yfinance 返回百分比形式（如 150 表示 1.5x），转换为小数。
    越低质量越好（反向因子）。
    """
    v = info.get('debt_to_equity')
    if v is None:
        return {'debt_to_equity': None}
    return {'debt_to_equity': round(float(v) / 100, 4)}


def compute_fcf_yield(info: dict) -> dict:
    """
    自由现金流收益率 = freeCashflow / marketCap。
    越高越好（类似债券收益率逻辑）。
    """
    fcf = info.get('free_cashflow')
    mc  = info.get('market_cap_b')
    if fcf is not None and mc is not None and mc > 0:
        return {'fcf_yield': round(float(fcf) / (float(mc) * 1e9), 4)}
    return {'fcf_yield': None}


def compute_pe_ratio(info: dict) -> dict:
    """市盈率 PE（trailing twelve months）"""
    v = info.get('pe_ratio')
    return {'pe_ratio': round(float(v), 2) if v is not None else None}


def compute_pb_ratio(info: dict) -> dict:
    """市净率 PB"""
    v = info.get('pb_ratio')
    return {'pb_ratio': round(float(v), 2) if v is not None else None}
