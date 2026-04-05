"""
财报日历回避模块

用法：
  from core.earnings import has_upcoming_earnings
  if has_upcoming_earnings('NVDA', within_days=2):
      # 跳过买入

数据来源：yfinance ticker.calendar（包含 Earnings Date）
缓存：.earnings_cache.pkl，TTL 12 小时（盘前运行一次即可）
失败降级：网络/解析失败时返回 False，不因数据缺失误杀买入
"""
from __future__ import annotations
import pickle
from datetime import date, datetime, timedelta
from pathlib import Path

import yfinance as yf

_CACHE_FILE = Path('.earnings_cache.pkl')
_CACHE_TTL  = timedelta(hours=12)


def _load_cache() -> dict:
    if not _CACHE_FILE.exists():
        return {}
    try:
        with open(_CACHE_FILE, 'rb') as f:
            stored = pickle.load(f)
        if datetime.now() - stored.get('_time', datetime.min) >= _CACHE_TTL:
            return {}
        return stored.get('data', {})
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        with open(_CACHE_FILE, 'wb') as f:
            pickle.dump({'_time': datetime.now(), 'data': data}, f)
    except Exception:
        pass


def _fetch_earnings_date(symbol: str) -> date | None:
    """返回最近一次未来财报日期，获取失败返回 None。"""
    try:
        cal = yf.Ticker(symbol).calendar
        if not cal:
            return None
        ed = cal.get('Earnings Date')
        if ed is None:
            return None
        # ed 可能是单个 Timestamp 或 list
        if not isinstance(ed, list):
            ed = [ed]
        today = date.today()
        future = [d.date() if hasattr(d, 'date') else d for d in ed if d is not None]
        future = [d for d in future if d >= today]
        return min(future) if future else None
    except Exception:
        return None


def has_upcoming_earnings(symbol: str, within_days: int = 2) -> bool:
    """
    检查 symbol 是否在 within_days 个日历日内有财报。

    - 获取失败时返回 False（保守降级，不误杀）
    - 结果缓存 12 小时，同一天多次调用只联网一次
    """
    if within_days <= 0:
        return False

    cache = _load_cache()
    if symbol in cache:
        ed = cache[symbol]
    else:
        ed = _fetch_earnings_date(symbol)
        cache[symbol] = ed
        _save_cache(cache)

    if ed is None:
        return False

    today  = date.today()
    cutoff = today + timedelta(days=within_days)
    return today <= ed <= cutoff


def prefetch_earnings(symbols: list[str]) -> dict[str, date | None]:
    """
    批量预取财报日期（每只逐个调用，结果写入缓存）。
    在 scan_signals() 阶段统一预取，避免 execute() 逐只联网。
    """
    cache = _load_cache()
    missing = [s for s in symbols if s not in cache]
    for sym in missing:
        cache[sym] = _fetch_earnings_date(sym)
    if missing:
        _save_cache(cache)
    return {s: cache.get(s) for s in symbols}
