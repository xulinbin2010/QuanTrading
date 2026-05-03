"""收益对比服务：获取多标的历史价格，归一化并计算对比指标

基准价取法：多拉 7 天，找 start 当天或之前最近的交易日收盘价作为归一化基准（0%），
确保节假日/周末选为起始时 YTD 计算正确（例如选 Jan 1 → 基准用 Dec 31 收盘）。
"""
from __future__ import annotations
import sys
import os
import logging
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_logger = logging.getLogger(__name__)

import numpy as np
import pandas as pd
from core.data_store import DataStore

PALETTE = ['#3b82f6', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4', '#f97316', '#ec4899']

_STOCKS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'stocks'


def _true_data_start(symbol: str) -> str | None:
    """直接从 parquet 读取该标的真正的最早数据日期（不被 fetch 窗口截断）。"""
    path = _STOCKS_DIR / f'{symbol}.parquet'
    if not path.exists():
        return None
    try:
        idx = pd.read_parquet(path, columns=['close']).index
        if idx.empty:
            return None
        return str(idx[0].date())
    except Exception:
        return None


def _prev_trading_day_close(df: pd.DataFrame, start_str: str) -> tuple[float, str]:
    """返回 (基准收盘价, 基准日期字符串)：start 当天有数据用当天，否则往前找最近交易日。"""
    start_dt = pd.Timestamp(start_str)
    # 找 start 当天或之前最近的交易日
    candidates = df.index[df.index <= start_dt]
    if candidates.empty:
        # start 之前没有数据，用第一条
        base_idx = df.index[0]
    else:
        base_idx = candidates[-1]
    return float(df.loc[base_idx, 'close']), str(base_idx.date())


def get_comparison(symbols: list[str], start: str, end: str | None = None) -> dict:
    store = DataStore()

    # 多拉 7 天，保证能取到 start 前最近交易日的收盘价
    fetch_start = str((date.fromisoformat(start) - timedelta(days=7)))

    try:
        # min_rows=2：放行新上市的 ETF（如 DRAM 仅 22 个交易日）
        data = store.get(symbols, start=fetch_start, end=end, min_rows=2)
    except Exception as e:
        _logger.error(f'[ComparisonSvc] 获取数据失败: {e}')
        raise

    valid = {sym: df for sym, df in data.items() if df is not None and not df.empty}
    missing = [s for s in symbols if s not in valid]

    if not valid:
        return {'series': [], 'metrics': [], 'error': '没有可用数据', 'missing': missing}

    # 展示区间：start 当天或之后第一个交易日起
    start_ts = pd.Timestamp(start)

    # 取各标的展示日期的交集（start 之后的共同交易日）
    display_sets = [set(df.index[df.index >= start_ts]) for df in valid.values()]
    common_dates = sorted(set.intersection(*display_sets))

    if not common_dates:
        return {'series': [], 'metrics': [], 'error': '所选时间段内各标的无共同交易日', 'missing': missing}

    closes: dict[str, pd.Series] = {}   # 展示区间的收盘价（用于指标计算）
    series = []
    base_info: dict[str, dict] = {}     # 各标的基准日期信息

    for i, (sym, df) in enumerate(valid.items()):
        # 直接读 parquet 取真正的数据起点（df.index[0] 是 fetch 窗口起点，不准）
        sym_data_start = _true_data_start(sym) or str(df.index[0].date())

        # 基准价：start 当天或之前最近交易日
        base_price, base_date = _prev_trading_day_close(df, start)

        # 展示数据：共同交易日
        display_close = df.loc[df.index.isin(common_dates), 'close']
        normalized = (display_close / base_price * 100).round(3)

        closes[sym] = display_close
        base_info[sym] = {'base_date': base_date, 'base_price': round(base_price, 4)}

        series.append({
            'symbol': sym,
            'color': PALETTE[i % len(PALETTE)],
            'data': [
                {'date': str(d.date()), 'value': float(v)}
                for d, v in zip(normalized.index, normalized.values)
            ],
            'base_date': base_date,
            'base_price': round(base_price, 4),
            'data_start': sym_data_start,   # 该标的原始数据的最早日期
        })

    metrics = []
    for sym, close in closes.items():
        vals = close.values.astype(float)
        base_price = base_info[sym]['base_price']

        # total_return 从基准价到最新收盘（包含展示区间外的基准日）
        total_return = vals[-1] / base_price - 1
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
            'base_date': base_info[sym]['base_date'],
            'base_price': base_info[sym]['base_price'],
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
