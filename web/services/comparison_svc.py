"""收益对比服务：获取多标的历史价格，归一化并计算对比指标"""
from __future__ import annotations
import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from core.data_store import DataStore

PALETTE = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316', '#ec4899']


def get_comparison(symbols: list[str], start: str, end: str | None = None) -> dict:
    store = DataStore()

    try:
        data = store.get(symbols, start=start, end=end)
    except Exception as e:
        _logger.error(f'[ComparisonSvc] 获取数据失败: {e}')
        raise

    valid = {sym: df for sym, df in data.items() if df is not None and not df.empty}
    missing = [s for s in symbols if s not in valid]

    if not valid:
        return {'series': [], 'metrics': [], 'error': '没有可用数据', 'missing': missing}

    # 取各标的日期的交集（共同交易日）
    date_sets = [set(df.index) for df in valid.values()]
    common_dates = sorted(set.intersection(*date_sets))

    if not common_dates:
        return {'series': [], 'metrics': [], 'error': '所选时间段内各标的无共同交易日', 'missing': missing}

    closes: dict[str, pd.Series] = {}
    series = []

    for i, (sym, df) in enumerate(valid.items()):
        close = df.loc[df.index.isin(common_dates), 'close']
        base = close.iloc[0]
        normalized = (close / base * 100).round(3)
        closes[sym] = close
        series.append({
            'symbol': sym,
            'color': PALETTE[i % len(PALETTE)],
            'data': [
                {'date': str(d.date()), 'value': float(v)}
                for d, v in zip(normalized.index, normalized.values)
            ],
        })

    metrics = []
    for sym, close in closes.items():
        vals = close.values.astype(float)
        total_return = vals[-1] / vals[0] - 1
        n_years = max(len(vals) / 252, 1 / 252)
        annual_return = (1 + total_return) ** (1 / n_years) - 1

        peak = np.maximum.accumulate(vals)
        max_dd = float(((vals - peak) / peak).min())

        daily_rets = np.diff(vals) / vals[:-1]
        std = daily_rets.std()
        sharpe = float(daily_rets.mean() / std * np.sqrt(252)) if std > 0 else 0.0
        vol = float(std * np.sqrt(252))

        metrics.append({
            'symbol': sym,
            'total_return': round(total_return, 4),
            'annual_return': round(annual_return, 4),
            'max_drawdown': round(max_dd, 4),
            'sharpe': round(sharpe, 2),
            'volatility': round(vol, 4),
        })

    corr_matrix = None
    if len(closes) >= 2:
        price_df = pd.DataFrame(closes)
        daily_rets_df = price_df.pct_change().dropna()
        corr = daily_rets_df.corr().round(3)
        corr_matrix = {
            'symbols': list(corr.columns),
            'values': corr.values.tolist(),
        }

    return {
        'series': series,
        'metrics': metrics,
        'start_date': str(common_dates[0].date()),
        'end_date': str(common_dates[-1].date()),
        'corr_matrix': corr_matrix,
        'missing': missing,
    }
