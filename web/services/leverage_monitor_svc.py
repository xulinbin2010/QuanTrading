"""韩国 / 美国杠杆产品与融资压力监控。

目标不是做一个普通 ETF 涨跌榜，而是拆开两个容易混淆的状态：

1. crowding（杠杆堆积）：融资余额处于历史高位、仍在加速；
2. unwind（去杠杆压力）：多头杠杆品放量下跌、反向品放量上涨、跟踪偏差扩大。

行情由 yfinance 批量下载，属于免费源（盘中可能延迟，成交量为 forming bar）。
美国融资余额来自 FINRA 官方月度 Margin Statistics。
韩国信用融资优先读取用户配置的官方 data.go.kr/KOFIA JSON API；未配置时明确返回 unavailable，
不使用新闻数字或第三方估算值冒充官方时序。
"""
from __future__ import annotations

import io
import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

_logger = logging.getLogger(__name__)

_CACHE_FILE = Path("data/.leverage_monitor_cache.json")
_HISTORY_FILE = Path("data/.leverage_monitor_history.json")
_CACHE_TTL_SECONDS = 15 * 60
_HISTORY_LIMIT = 480
METHODOLOGY_VERSION = "2.0"
_LOCK = threading.Lock()
_MEM_CACHE: tuple[float, dict[str, Any]] | None = None

FINRA_URL = "https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics"
FINRA_HISTORY_XLSX = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
KOREA_OFFICIAL_URL = (
    "https://www.data.go.kr/data/15094809/openapi.do"
)


@dataclass(frozen=True)
class LeveragedProduct:
    symbol: str
    name: str
    market: str
    leverage: float
    benchmark: str
    theme: str
    provider: str


# 只放能代表风险偏好的高流动性核心组；不是把所有单股杠杆 ETF 全量塞进来。
# 产品清单是显式配置，避免 yfinance 单次 API 失败被误判为产品退市并自动删除。
PRODUCTS: tuple[LeveragedProduct, ...] = (
    LeveragedProduct("TQQQ", "ProShares UltraPro QQQ", "US", 3.0, "QQQ", "纳指100", "ProShares"),
    LeveragedProduct("SQQQ", "ProShares UltraPro Short QQQ", "US", -3.0, "QQQ", "纳指100", "ProShares"),
    LeveragedProduct("UPRO", "ProShares UltraPro S&P 500", "US", 3.0, "SPY", "标普500", "ProShares"),
    LeveragedProduct("SPXU", "ProShares UltraPro Short S&P 500", "US", -3.0, "SPY", "标普500", "ProShares"),
    LeveragedProduct("SOXL", "Direxion Daily Semiconductor Bull", "US", 3.0, "SOXX", "半导体", "Direxion"),
    LeveragedProduct("SOXS", "Direxion Daily Semiconductor Bear", "US", -3.0, "SOXX", "半导体", "Direxion"),
    LeveragedProduct("TECL", "Direxion Daily Technology Bull", "US", 3.0, "XLK", "科技", "Direxion"),
    LeveragedProduct("TECS", "Direxion Daily Technology Bear", "US", -3.0, "XLK", "科技", "Direxion"),
    LeveragedProduct("TNA", "Direxion Daily Small Cap Bull", "US", 3.0, "IWM", "小盘股", "Direxion"),
    LeveragedProduct("TZA", "Direxion Daily Small Cap Bear", "US", -3.0, "IWM", "小盘股", "Direxion"),
    LeveragedProduct("FAS", "Direxion Daily Financial Bull", "US", 3.0, "XLF", "金融", "Direxion"),
    LeveragedProduct("FAZ", "Direxion Daily Financial Bear", "US", -3.0, "XLF", "金融", "Direxion"),
    LeveragedProduct("LABU", "Direxion Daily S&P Biotech Bull", "US", 3.0, "XBI", "生物科技", "Direxion"),
    LeveragedProduct("LABD", "Direxion Daily S&P Biotech Bear", "US", -3.0, "XBI", "生物科技", "Direxion"),
    LeveragedProduct("NVDL", "GraniteShares 2x Long NVDA", "US", 2.0, "NVDA", "单股·NVDA", "GraniteShares"),
    LeveragedProduct("TSLL", "Direxion Daily TSLA Bull", "US", 2.0, "TSLA", "单股·TSLA", "Direxion"),
    LeveragedProduct("MSTU", "T-Rex 2x Long MSTR", "US", 2.0, "MSTR", "单股·MSTR", "T-Rex"),
    LeveragedProduct("MUU", "Direxion Daily MU Bull", "US", 2.0, "MU", "单股·MU", "Direxion"),
    LeveragedProduct("ARMG", "Leverage Shares 2x Long ARM", "US", 2.0, "ARM", "单股·ARM", "Leverage Shares"),
    LeveragedProduct("RAM", "T-REX 2x Long DRAM", "US", 2.0, "DRAM", "存储·DRAM", "T-REX"),
    LeveragedProduct("BITX", "2x Bitcoin Strategy ETF", "US", 2.0, "BTC-USD", "加密资产", "Volatility Shares"),
    LeveragedProduct("122630.KS", "KODEX 레버리지", "KR", 2.0, "069500.KS", "KOSPI200", "Samsung"),
    LeveragedProduct("252670.KS", "KODEX 200선물인버스2X", "KR", -2.0, "069500.KS", "KOSPI200", "Samsung"),
    LeveragedProduct("233740.KS", "KODEX 코스닥150레버리지", "KR", 2.0, "229200.KS", "KOSDAQ150", "Samsung"),
    LeveragedProduct("251340.KS", "KODEX 코스닥150선물인버스", "KR", -1.0, "229200.KS", "KOSDAQ150", "Samsung"),
)

_KNOWN_LEVERAGE = {product.symbol.upper(): abs(product.leverage) for product in PRODUCTS}


def known_leverage_for(symbol: str) -> float:
    """返回显式产品注册表中的日目标杠杆；未知产品按 1x。

    注册表只增不自动删，避免一次行情失败把产品误判为失效。
    """
    key = str(symbol).upper()
    if key not in _KNOWN_LEVERAGE and key.isdigit() and len(key) == 6:
        key = f"{key}.KS"
    return float(_KNOWN_LEVERAGE.get(key, 1.0))


def _finite(value: Any, digits: int | None = None) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return round(num, digits) if digits is not None else num


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return _finite(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def _read_disk_cache() -> dict[str, Any] | None:
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return raw
    except Exception:
        pass
    return None


def _write_disk_cache(payload: dict[str, Any]) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(_json_safe(payload), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        _logger.warning("[LeverageMonitor] 写缓存失败：%s", exc)


def _download_prices(symbols: list[str]) -> dict[str, pd.DataFrame]:
    import yfinance as yf

    raw = yf.download(
        symbols,
        period="6mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="ticker",
        threads=True,
        timeout=25,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out

    for symbol in symbols:
        frame: pd.DataFrame | None = None
        try:
            if isinstance(raw.columns, pd.MultiIndex):
                level0 = raw.columns.get_level_values(0)
                level1 = raw.columns.get_level_values(1)
                if symbol in level0:
                    frame = raw[symbol].copy()
                elif symbol in level1:
                    frame = raw.xs(symbol, axis=1, level=1).copy()
            elif len(symbols) == 1:
                frame = raw.copy()
        except Exception:
            frame = None
        if frame is None or frame.empty:
            continue
        frame.columns = [str(c).lower().replace(" ", "_") for c in frame.columns]
        needed = [c for c in ("open", "high", "low", "close", "volume") if c in frame.columns]
        frame = frame[needed].dropna(subset=["close"]).sort_index()
        if not frame.empty:
            out[symbol] = frame
    return out


def _session_progress(market: str, latest_index: Any) -> tuple[float, bool]:
    """返回当日交易时段完成比例，以及 latest bar 是否仍是 forming bar。"""
    tz_name, open_min, close_min = (
        ("America/New_York", 9 * 60 + 30, 16 * 60)
        if market == "US"
        else ("Asia/Seoul", 9 * 60, 15 * 60 + 30)
    )
    now = datetime.now(ZoneInfo(tz_name))
    try:
        latest_date = pd.Timestamp(latest_index).date()
    except Exception:
        return 1.0, False
    if now.weekday() >= 5 or latest_date != now.date():
        return 1.0, False
    minute = now.hour * 60 + now.minute
    if minute < open_min:
        return 1.0, False
    if minute >= close_min:
        return 1.0, False
    progress = max(0.12, min(1.0, (minute - open_min) / (close_min - open_min)))
    return progress, True


def _return(close: pd.Series, sessions: int) -> float | None:
    clean = close.dropna()
    if len(clean) <= sessions:
        return None
    ref = float(clean.iloc[-1 - sessions])
    return float(clean.iloc[-1] / ref - 1) if ref else None


def _tracking_metrics(
    product: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    leverage: float,
    lookback: int = 20,
) -> tuple[float | None, float | None]:
    if benchmark is None or product.empty or benchmark.empty:
        return None, None
    joined = pd.DataFrame(
        {"product": product["close"], "benchmark": benchmark["close"]}
    ).dropna()
    returns = joined.pct_change(fill_method=None).dropna().tail(lookback)
    if returns.empty:
        return None, None
    one_day_gap = float(returns["product"].iloc[-1] - leverage * returns["benchmark"].iloc[-1])
    if len(returns) < 5:
        return one_day_gap, None
    actual = float((1 + returns["product"]).prod() - 1)
    daily_target = 1 + leverage * returns["benchmark"]
    # 极端情况下理论日目标可低于 -100%，此时不输出没有金融含义的复利结果。
    if (daily_target <= 0).any():
        return one_day_gap, None
    theoretical = float(daily_target.prod() - 1)
    return one_day_gap, actual - theoretical


def _product_row(product: LeveragedProduct, prices: dict[str, pd.DataFrame]) -> dict[str, Any]:
    meta = asdict(product)
    df = prices.get(product.symbol)
    if df is None or len(df) < 3:
        return {**meta, "available": False, "error": "行情不可用"}

    close = df["close"].dropna()
    volume = df.get("volume", pd.Series(dtype=float)).reindex(close.index).fillna(0)
    progress, forming = _session_progress(product.market, close.index[-1])
    avg20 = float(volume.iloc[-21:-1].mean()) if len(volume) > 21 else float(volume.iloc[:-1].mean())
    current_volume = float(volume.iloc[-1])
    volume_pace = current_volume / progress / avg20 if avg20 > 0 else None
    ret_1d = _return(close, 1)
    gap_1d, drag_20d = _tracking_metrics(
        df, prices.get(product.benchmark), product.leverage, lookback=20
    )
    daily = close.pct_change(fill_method=None).dropna().tail(20)
    rv20 = float(daily.std(ddof=0) * math.sqrt(252)) if len(daily) >= 5 else None
    peak20 = float(close.tail(20).max())
    drawdown20 = float(close.iloc[-1] / peak20 - 1) if peak20 else None
    dollar_volume = float(close.iloc[-1] * current_volume)

    # 行级“异常度”不判断多空，只表达价格、量能、跟踪偏差是否异常。
    anomaly = 0.0
    anomaly += min(abs(ret_1d or 0) / 0.08, 1.0) * 35
    anomaly += min(max((volume_pace or 1) - 1, 0) / 2, 1.0) * 30
    anomaly += min(abs(gap_1d or 0) / 0.025, 1.0) * 20
    anomaly += min(abs(drag_20d or 0) / 0.08, 1.0) * 15

    return {
        **meta,
        "available": True,
        "direction": "long" if product.leverage > 0 else "inverse",
        "latest_date": pd.Timestamp(close.index[-1]).strftime("%Y-%m-%d"),
        "price": _finite(close.iloc[-1], 4),
        "currency": "USD" if product.market == "US" else "KRW",
        "ret_1d": _finite(ret_1d, 6),
        "ret_5d": _finite(_return(close, 5), 6),
        "ret_20d": _finite(_return(close, 20), 6),
        "drawdown_20d": _finite(drawdown20, 6),
        "realized_vol_20d": _finite(rv20, 6),
        "volume": int(current_volume),
        "avg_volume_20d": int(avg20) if avg20 > 0 else None,
        "volume_ratio": _finite(volume_pace, 4),
        "volume_estimated": forming,
        "dollar_volume": _finite(dollar_volume, 0),
        "tracking_gap_1d": _finite(gap_1d, 6),
        "tracking_drag_20d": _finite(drag_20d, 6),
        "anomaly_score": round(anomaly, 1),
    }


def _weighted_mean(rows: list[dict[str, Any]], key: str) -> float | None:
    valid = [r for r in rows if r.get(key) is not None]
    if not valid:
        return None
    weights = np.array([math.log1p(max(float(r.get("dollar_volume") or 0), 0)) for r in valid])
    if float(weights.sum()) <= 0:
        weights = np.ones(len(valid))
    values = np.array([float(r[key]) for r in valid])
    return float(np.average(values, weights=weights))


def _market_aggregate(rows: list[dict[str, Any]], funding: dict[str, Any] | None) -> dict[str, Any]:
    available = [r for r in rows if r.get("available")]
    latest_dates = [str(r["latest_date"]) for r in available if r.get("latest_date")]
    as_of = max(latest_dates) if latest_dates else None
    older_bar_count = sum(1 for value in latest_dates if as_of and value != as_of)
    longs = [r for r in available if r.get("direction") == "long"]
    inverses = [r for r in available if r.get("direction") == "inverse"]
    long_ret = _weighted_mean(longs, "ret_1d")
    long_vol = _weighted_mean(longs, "volume_ratio")
    inverse_ret = _weighted_mean(inverses, "ret_1d")
    inverse_vol = _weighted_mean(inverses, "volume_ratio")
    gap_abs_values = [abs(float(r["tracking_gap_1d"])) for r in available if r.get("tracking_gap_1d") is not None]
    gap_abs = float(np.median(gap_abs_values)) if gap_abs_values else None

    components: list[dict[str, Any]] = []

    def add(
        name: str,
        value: float | None,
        raw_points: float,
        max_points: float,
        *,
        quality: float = 1.0,
        provisional: bool = False,
    ) -> None:
        effective_quality = max(0.0, min(float(quality), 1.0))
        components.append({
            "name": name,
            "value": _finite(value, 6),
            # 固定权重；其它证据缺失时，不把少量可用项重新归一化成满分。
            "points": round(max(0.0, min(raw_points, max_points)) * effective_quality, 1),
            "max_points": max_points,
            "evidence_points": round(max_points * effective_quality, 1),
            "quality": round(effective_quality, 2),
            "provisional": provisional,
        })

    if long_ret is not None:
        add("多头杠杆下跌", long_ret, max(-long_ret, 0) / 0.06 * 35, 35)
    if long_vol is not None:
        forming = any(bool(r.get("volume_estimated")) for r in longs)
        add(
            "多头成交放大", long_vol, max(long_vol - 1, 0) / 2 * 20, 20,
            # 日内成交量呈 U 型，只有日 K 无法做可靠的同时间季节性校正；
            # forming bar 只展示 preliminary 值，不进入正式 Trigger 分数。
            quality=0.0 if forming else 1.0,
            provisional=forming,
        )
    if inverse_ret is not None:
        add("反向产品上涨", inverse_ret, max(inverse_ret, 0) / 0.06 * 20, 20)
    if inverse_vol is not None:
        forming = any(bool(r.get("volume_estimated")) for r in inverses)
        add(
            "反向成交放大", inverse_vol, max(inverse_vol - 1, 0) / 2 * 15, 15,
            quality=0.0 if forming else 1.0,
            provisional=forming,
        )
    if gap_abs is not None:
        add("跟踪偏差扩大", gap_abs, gap_abs / 0.02 * 10, 10)

    points = sum(float(c["points"]) for c in components)
    coverage = round(sum(float(c["evidence_points"]) for c in components), 1)
    score = round(points, 1) if coverage >= 60 else None
    level = "high" if score is not None and score >= 65 else "mid" if score is not None and score >= 35 else "low"
    confidence = (
        "high" if coverage >= 90 and older_bar_count == 0
        else "medium" if coverage >= 70
        else "low"
    )
    return {
        "available_products": len(available),
        "configured_products": len(rows),
        "as_of": as_of,
        "older_bar_count": older_bar_count,
        "long_ret_1d": _finite(long_ret, 6),
        "long_volume_ratio": _finite(long_vol, 4),
        "inverse_ret_1d": _finite(inverse_ret, 6),
        "inverse_volume_ratio": _finite(inverse_vol, 4),
        "median_abs_tracking_gap_1d": _finite(gap_abs, 6),
        "unwind_score": score,
        "unwind_level": level,
        "evidence_coverage": coverage,
        "confidence": confidence,
        "score_components": components,
    }


def _parse_finra_html(html: str) -> list[dict[str, Any]]:
    tables = pd.read_html(io.StringIO(html))
    table: pd.DataFrame | None = None
    for candidate in tables:
        columns = " ".join(str(c) for c in candidate.columns)
        if "Debit Balances" in columns and "Month/Year" in columns:
            table = candidate
            break
    if table is None:
        raise ValueError("FINRA 页面中未找到 Margin Statistics 表")

    rows: list[dict[str, Any]] = []
    for _, raw in table.iterrows():
        label = str(raw.iloc[0]).strip()
        parsed = pd.to_datetime(label, format="%b-%y", errors="coerce")
        if pd.isna(parsed):
            continue

        def number(value: Any) -> float | None:
            text = str(value).replace(",", "").replace("$", "").strip()
            return _finite(text)

        debit = number(raw.iloc[1])
        cash_credit = number(raw.iloc[2]) if len(raw) > 2 else None
        margin_credit = number(raw.iloc[3]) if len(raw) > 3 else None
        if debit is None:
            continue
        rows.append({
            "date": parsed.strftime("%Y-%m-%d"),
            "debit_usd_m": debit,
            "cash_credit_usd_m": cash_credit,
            "margin_credit_usd_m": margin_credit,
        })
    return sorted(rows, key=lambda row: row["date"])


def _parse_finra_excel(content: bytes) -> list[dict[str, Any]]:
    table = pd.read_excel(io.BytesIO(content), sheet_name="Customer Margin Balances")
    if table.empty or len(table.columns) < 2:
        raise ValueError("FINRA 历史 Excel 结构为空")
    rows: list[dict[str, Any]] = []
    for _, raw in table.iterrows():
        parsed = pd.to_datetime(str(raw.iloc[0]), format="%Y-%m", errors="coerce")
        debit = _finite(raw.iloc[1])
        if pd.isna(parsed) or debit is None:
            continue
        rows.append({
            "date": parsed.strftime("%Y-%m-%d"),
            "debit_usd_m": debit,
            "cash_credit_usd_m": _finite(raw.iloc[2]) if len(raw) > 2 else None,
            "margin_credit_usd_m": _finite(raw.iloc[3]) if len(raw) > 3 else None,
        })
    return sorted(rows, key=lambda row: row["date"])


def _funding_stats(
    history: list[dict[str, Any]],
    value_key: str,
    source: str,
    source_url: str,
    unit: str,
    label: str,
) -> dict[str, Any]:
    if len(history) < 2:
        return {"available": False, "error": "有效历史不足 2 期", "source": source}
    values = [float(r[value_key]) for r in history if r.get(value_key) is not None]
    if len(values) < 2:
        return {"available": False, "error": "有效余额不足 2 期", "source": source}
    latest = history[-1]
    latest_value = float(latest[value_key])
    prev_value = float(history[-2][value_key])
    mom = latest_value / prev_value - 1 if prev_value else None
    yoy = latest_value / float(history[-13][value_key]) - 1 if len(history) >= 13 and history[-13].get(value_key) else None
    trailing = np.array(values[-36:], dtype=float)
    percentile = float((trailing <= latest_value).mean()) if len(trailing) else None
    zscore = (
        float((latest_value - trailing.mean()) / trailing.std(ddof=0))
        if len(trailing) >= 6 and trailing.std(ddof=0) > 0
        else None
    )
    # 堆积热度：历史分位为主；同比加速只做少量修正。
    crowding = (percentile or 0) * 80 + min(max((yoy or 0) / 0.25, 0), 1) * 20
    return {
        "available": True,
        "label": label,
        "source": source,
        "source_url": source_url,
        "frequency": "monthly" if source == "FINRA" else "daily",
        "unit": unit,
        "as_of": latest["date"],
        "latest": _finite(latest_value, 2),
        "mom": _finite(mom, 6),
        "yoy": _finite(yoy, 6),
        "percentile_36": _finite(percentile, 4),
        "zscore_36": _finite(zscore, 3),
        "history_window": int(len(trailing)),
        "crowding_score": round(crowding, 1),
        "history": history[-60:],
    }


def _fetch_us_margin() -> dict[str, Any]:
    try:
        response = requests.get(
            FINRA_HISTORY_XLSX,
            headers={"User-Agent": "QuanTrading/1.0 leverage-monitor"},
            timeout=18,
        )
        response.raise_for_status()
        history = _parse_finra_excel(response.content)
        if not history:
            raise ValueError("FINRA 历史 Excel 没有有效记录")
    except Exception as excel_exc:
        _logger.warning("[LeverageMonitor] FINRA 历史 Excel 失败，退回网页表：%s", excel_exc)
        try:
            response = requests.get(
                FINRA_URL,
                headers={"User-Agent": "QuanTrading/1.0 leverage-monitor"},
                timeout=18,
            )
            response.raise_for_status()
            history = _parse_finra_html(response.text)
        except Exception as html_exc:
            return {
                "available": False,
                "source": "FINRA",
                "source_url": FINRA_URL,
                "error": f"Excel: {excel_exc}; HTML: {html_exc}",
                "frequency": "monthly",
            }
    try:
        result = _funding_stats(
            history,
            value_key="debit_usd_m",
            source="FINRA",
            source_url=FINRA_URL,
            unit="USD million",
            label="美国客户证券融资借方余额",
        )
        result["publication_lag"] = "月末统计，通常次月第三周发布"
        return result
    except Exception as exc:
        return {
            "available": False,
            "source": "FINRA",
            "source_url": FINRA_URL,
            "error": str(exc),
            "frequency": "monthly",
        }


def _find_items(payload: Any) -> list[dict[str, Any]]:
    """兼容 data.go.kr 常见 response.body.items.item 和直接数组两种 JSON。"""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("item", "items", "data", "list", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
        if isinstance(value, dict):
            nested = _find_items(value)
            if nested:
                return nested
    for value in payload.values():
        if isinstance(value, (dict, list)):
            nested = _find_items(value)
            if nested:
                return nested
    return []


def _parse_korea_margin_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    date_keys = ("basDt", "date", "trdDd", "stndDt", "일자", "기준일")
    total_keys = (
        "crdtLoanBal", "creditBalance", "totalBalance", "totAmt",
        "신용거래융자", "신용공여잔고", "합계",
    )
    kospi_keys = ("kospi", "kospiBalance", "유가증권")
    kosdaq_keys = ("kosdaq", "kosdaqBalance", "코스닥")

    def pick(item: dict[str, Any], keys: tuple[str, ...]) -> Any:
        lowered = {str(k).lower(): v for k, v in item.items()}
        for key in keys:
            if key in item:
                return item[key]
            if key.lower() in lowered:
                return lowered[key.lower()]
        return None

    def numeric(value: Any) -> float | None:
        if value is None:
            return None
        return _finite(str(value).replace(",", "").replace("억원", "").strip())

    rows: list[dict[str, Any]] = []
    for item in items:
        raw_date = pick(item, date_keys)
        parsed_date = pd.to_datetime(str(raw_date), errors="coerce")
        total = numeric(pick(item, total_keys))
        kospi = numeric(pick(item, kospi_keys))
        kosdaq = numeric(pick(item, kosdaq_keys))
        if total is None and kospi is not None and kosdaq is not None:
            total = kospi + kosdaq
        if pd.isna(parsed_date) or total is None:
            continue
        rows.append({
            "date": parsed_date.strftime("%Y-%m-%d"),
            "credit_krw_100m": total,
            "kospi_krw_100m": kospi,
            "kosdaq_krw_100m": kosdaq,
        })
    dedup = {row["date"]: row for row in rows}
    return [dedup[key] for key in sorted(dedup)]


def _fetch_korea_margin() -> dict[str, Any]:
    """读取官方接口。

    配置方式（均放 .env，不进 git）：
      KOREA_MARGIN_API_URL=https://apis.data.go.kr/.../operation
      DATA_GO_KR_SERVICE_KEY=...

    API URL 由 data.go.kr「金融委员会_金融投资协会综合统计信息」申请后复制。
    不把尚未确认的 operation path 硬编码进项目，避免接口调整后静默抓错表。
    """
    url = os.getenv("KOREA_MARGIN_API_URL", "").strip()
    key = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not url or not key:
        return {
            "available": False,
            "source": "KOFIA / data.go.kr",
            "source_url": KOREA_OFFICIAL_URL,
            "frequency": "daily",
            "error": "未配置官方 API",
            "setup_required": True,
            "setup_hint": "在 .env 配置 KOREA_MARGIN_API_URL 与 DATA_GO_KR_SERVICE_KEY",
        }
    try:
        extra_params: dict[str, Any] = {}
        raw_extra = os.getenv("KOREA_MARGIN_API_PARAMS_JSON", "").strip()
        if raw_extra:
            parsed_extra = json.loads(raw_extra)
            if isinstance(parsed_extra, dict):
                extra_params = parsed_extra
        response = requests.get(
            url,
            params={
                "serviceKey": key,
                "pageNo": 1,
                "numOfRows": 120,
                "resultType": "json",
                **extra_params,
            },
            timeout=18,
        )
        response.raise_for_status()
        items = _find_items(response.json())
        history = _parse_korea_margin_items(items)
        result = _funding_stats(
            history,
            value_key="credit_krw_100m",
            source="KOFIA / data.go.kr",
            source_url=KOREA_OFFICIAL_URL,
            unit="KRW 100 million",
            label="韩国信用交易融资余额",
        )
        result["publication_lag"] = "官方日度数据，以接口返回的基准日为准"
        return result
    except Exception as exc:
        return {
            "available": False,
            "source": "KOFIA / data.go.kr",
            "source_url": KOREA_OFFICIAL_URL,
            "frequency": "daily",
            "error": str(exc),
        }


def _load_personal_snapshot() -> dict[str, Any]:
    """读取本地实盘诊断快照，生成不依赖模型的个人账户风险指标。"""
    try:
        from web.services import account_doctor_svc

        saved = account_doctor_svc.get_latest() or {}
    except Exception as exc:
        return {"available": False, "error": f"读取实盘诊断失败：{exc}"}
    account = saved.get("account") or {}
    positions = saved.get("positions") or []
    net_liq = _finite(account.get("net_liq"))
    if not net_liq or net_liq <= 0:
        return {
            "available": False,
            "error": "尚无有效实盘诊断快照",
            "setup_hint": "先在「实盘诊断」粘贴并确认账户与持仓",
        }

    gross_long = _finite(account.get("gross_long")) or sum(
        float(_finite(p.get("market_value_usd")) or 0) for p in positions
    )
    maint = _finite(account.get("maint_margin"))
    excess = _finite(account.get("excess_liquidity"))
    settled_cash = _finite(account.get("settled_cash"))
    equity_with_loan = (
        maint + excess
        if maint is not None and excess is not None
        else net_liq
    )
    cushion = excess / equity_with_loan if excess is not None and equity_with_loan else None

    total_exposure = 0.0
    embedded_extra = 0.0
    market_exposure = {"US": 0.0, "KR": 0.0}
    leveraged_positions: list[dict[str, Any]] = []
    for row in positions:
        symbol = str(row.get("symbol") or "").upper()
        market_value = float(_finite(row.get("market_value_usd")) or 0)
        saved_leverage = abs(float(_finite(row.get("leverage_factor")) or 1.0))
        leverage = max(saved_leverage, known_leverage_for(symbol))
        exposure = market_value * leverage
        market = (
            "KR"
            if str(row.get("currency") or "").upper() == "KRW"
            or symbol.endswith(".KS")
            or symbol.isdigit()
            else "US"
        )
        market_exposure[market] += exposure
        total_exposure += exposure
        embedded_extra += market_value * max(leverage - 1.0, 0.0)
        if leverage > 1:
            leveraged_positions.append({
                "symbol": symbol,
                "market_value_usd": round(market_value, 2),
                "leverage": leverage,
                "exposure_usd": round(exposure, 2),
            })
    market_total = sum(market_exposure.values())
    market_weights = {
        market: round(value / market_total, 4) if market_total else 0.0
        for market, value in market_exposure.items()
    }

    debt = max(-(settled_cash or 0), 0.0)
    margin_cushion_pct = cushion * 100 if cushion is not None else None
    if margin_cushion_pct is None:
        pressure_level = "unknown"
    elif margin_cushion_pct < 15:
        pressure_level = "critical"
    elif margin_cushion_pct < 25:
        pressure_level = "high"
    elif margin_cushion_pct < 35:
        pressure_level = "mid"
    else:
        pressure_level = "low"

    return {
        "available": True,
        "source": "本地实盘诊断快照",
        "as_of": saved.get("as_of"),
        "net_liq": round(net_liq, 2),
        "gross_long": round(gross_long, 2),
        "maint_margin": _finite(maint, 2),
        "excess_liquidity": _finite(excess, 2),
        "equity_with_loan": _finite(equity_with_loan, 2),
        "margin_cushion_pct": _finite(margin_cushion_pct, 1),
        "pressure_level": pressure_level,
        "settled_cash": _finite(settled_cash, 2),
        "margin_debt": round(debt, 2),
        "broker_leverage": round(gross_long / net_liq, 3) if gross_long else 0.0,
        "embedded_extra_exposure": round(embedded_extra, 2),
        "effective_exposure": round(total_exposure, 2),
        "effective_leverage": round(total_exposure / net_liq, 3),
        "market_weights": market_weights,
        "leveraged_positions": leveraged_positions,
        "stress_trigger_shock": (saved.get("stress") or {}).get("trigger_shock"),
    }


def _weighted_score(
    values: dict[str, float | None],
    weights: dict[str, float] | None,
    *,
    fallback: str,
) -> tuple[float | None, str]:
    valid = {key: float(value) for key, value in values.items() if value is not None}
    if not valid:
        return None, "unavailable"
    if weights:
        usable = {
            key: max(float(weights.get(key.upper(), 0)), 0.0)
            for key in valid
        }
        total = sum(usable.values())
        if total > 0:
            return (
                round(sum(valid[key] * usable[key] for key in valid) / total, 1),
                "personal_exposure_weighted",
            )
    if fallback == "max":
        return round(max(valid.values()), 1), "worst_market"
    return round(sum(valid.values()) / len(valid), 1), "available_market_mean"


def _state_summary(
    markets: dict[str, dict[str, Any]],
    funding: dict[str, dict[str, Any]],
    personal: dict[str, Any],
) -> dict[str, Any]:
    weights = personal.get("market_weights") if personal.get("available") else None
    trigger, trigger_method = _weighted_score(
        {
            "US": markets["us"].get("unwind_score"),
            "KR": markets["kr"].get("unwind_score"),
        },
        weights,
        fallback="max",
    )
    crowding, crowding_method = _weighted_score(
        {
            "US": funding["us"].get("crowding_score") if funding["us"].get("available") else None,
            "KR": funding["kr"].get("crowding_score") if funding["kr"].get("available") else None,
        },
        weights,
        fallback="mean",
    )
    trigger_coverage, _ = _weighted_score(
        {
            "US": markets["us"].get("evidence_coverage"),
            "KR": markets["kr"].get("evidence_coverage"),
        },
        weights,
        fallback="mean",
    )
    funding_coverage = 0.0
    if weights:
        funding_coverage = sum(
            float(weights.get(market, 0))
            for market, key in (("US", "us"), ("KR", "kr"))
            if funding[key].get("available")
        ) * 100
    else:
        funding_coverage = sum(1 for key in ("us", "kr") if funding[key].get("available")) / 2 * 100
    coverage = (
        round((trigger_coverage or 0) * 0.75 + funding_coverage * 0.25, 1)
        if trigger is not None
        else 0.0
    )
    confidence = "high" if coverage >= 85 else "medium" if coverage >= 65 else "low"

    trigger_band = "high" if (trigger or 0) >= 65 else "mid" if (trigger or 0) >= 35 else "low"
    crowding_band = "high" if (crowding or 0) >= 65 else "mid" if (crowding or 0) >= 40 else "low"
    if trigger_band == "high" and crowding_band != "low":
        state, state_label, level = "forced_unwind", "强制去杠杆风险", "high"
    elif trigger_band == "high":
        state, state_label, level = "shock", "短期冲击", "high"
    elif trigger_band == "mid" and crowding_band == "high":
        state, state_label, level = "unwind_heating", "去杠杆升温", "mid"
    elif crowding_band == "high":
        state, state_label, level = "crowded", "高位拥挤", "mid"
    elif trigger_band == "mid" or crowding_band == "mid":
        state, state_label, level = "watch", "观察", "mid"
    else:
        state, state_label, level = "normal", "正常", "low"

    market_scores = {
        "US": markets["us"].get("unwind_score"),
        "KR": markets["kr"].get("unwind_score"),
    }
    available_scores = {key: value for key, value in market_scores.items() if value is not None}
    dominant_market = max(available_scores, key=available_scores.get) if available_scores else None
    return {
        # 旧字段保留给统一风险驾驶舱使用。
        "unwind_score": trigger,
        "unwind_level": level,
        "dominant_market": dominant_market,
        "trigger_score": trigger,
        "trigger_band": trigger_band,
        "crowding_score": crowding,
        "crowding_band": crowding_band,
        "state": state,
        "state_label": state_label,
        "confidence": confidence,
        "evidence_coverage": coverage,
        "trigger_method": trigger_method,
        "crowding_method": crowding_method,
    }


def _risk_posture(summary: dict[str, Any], personal: dict[str, Any]) -> dict[str, Any]:
    cushion = personal.get("margin_cushion_pct") if personal.get("available") else None
    state = summary.get("state")
    if (cushion is not None and cushion < 15) or state == "forced_unwind":
        code, label, level = "reduce", "降低杠杆敞口", "high"
        reason = "市场触发与账户脆弱度至少一项进入高风险区"
    elif (cushion is not None and cushion < 25) or state == "shock":
        code, label, level = "stop_add", "停止新增杠杆", "high"
        reason = "保证金缓冲偏薄或即时冲击已进入高位"
    elif (cushion is not None and cushion < 35) or state in {"unwind_heating", "crowded", "watch"}:
        code, label, level = "defensive", "谨慎 · 只减不加", "mid"
        reason = "账户缓冲或市场证据处于警戒区"
    else:
        code, label, level = "normal", "维持纪律", "low"
        reason = "可用证据尚未形成明显去杠杆共振"
    return {
        "code": code,
        "label": label,
        "level": level,
        "reason": reason,
        "core": "核心中长期仓复核 thesis，不因单一压力分数机械清仓",
        "tactical": (
            "暂停新增 2X/3X 与 margin，优先检查同主题重复敞口"
            if code != "normal"
            else "波段仓继续遵守既定仓位上限，不把 Buying Power 当安全垫"
        ),
        "automated_action": False,
    }


def _evidence_ledger(
    markets: dict[str, dict[str, Any]],
    funding: dict[str, dict[str, Any]],
    personal: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market, key in (("US", "us"), ("KR", "kr")):
        aggregate = markets[key]
        for component in aggregate.get("score_components") or []:
            ratio = (
                float(component.get("points") or 0) / float(component.get("max_points") or 1)
            )
            rows.append({
                "key": f"{key}_{component['name']}",
                "layer": "trigger",
                "market": market,
                "label": component["name"],
                "value": component.get("value"),
                "points": component.get("points"),
                "max_points": component.get("max_points"),
                "status": "high" if ratio >= 0.65 else "mid" if ratio >= 0.35 else "low",
                "source": "Yahoo Finance（日K）",
                "as_of": aggregate.get("as_of"),
                "frequency": "daily / forming bar",
                "confidence": "low" if component.get("provisional") else aggregate.get("confidence"),
                "provisional": bool(component.get("provisional")),
            })
        fund = funding[key]
        if fund.get("available"):
            score = float(fund.get("crowding_score") or 0)
            rows.append({
                "key": f"{key}_funding",
                "layer": "crowding",
                "market": market,
                "label": fund.get("label"),
                "value": fund.get("latest"),
                "comparison": {
                    "mom": fund.get("mom"),
                    "yoy": fund.get("yoy"),
                    "percentile": fund.get("percentile_36"),
                },
                "status": "high" if score >= 65 else "mid" if score >= 40 else "low",
                "source": fund.get("source"),
                "as_of": fund.get("as_of"),
                "frequency": fund.get("frequency"),
                "confidence": "high",
                "provisional": False,
            })
    if personal.get("available"):
        cushion = personal.get("margin_cushion_pct")
        rows.extend([
            {
                "key": "personal_margin_cushion",
                "layer": "account",
                "market": "ACCOUNT",
                "label": "保证金缓冲",
                "value": cushion,
                "status": "high" if cushion is not None and cushion < 20 else "mid" if cushion is not None and cushion < 35 else "low",
                "source": personal.get("source"),
                "as_of": personal.get("as_of"),
                "frequency": "manual snapshot",
                "confidence": "high",
                "provisional": False,
            },
            {
                "key": "personal_effective_leverage",
                "layer": "account",
                "market": "ACCOUNT",
                "label": "穿透后有效杠杆",
                "value": personal.get("effective_leverage"),
                "status": "high" if float(personal.get("effective_leverage") or 0) >= 1.5 else "mid" if float(personal.get("effective_leverage") or 0) >= 1.15 else "low",
                "source": personal.get("source"),
                "as_of": personal.get("as_of"),
                "frequency": "manual snapshot",
                "confidence": "high",
                "provisional": False,
            },
        ])
    return rows


def _history_read() -> list[dict[str, Any]]:
    try:
        raw = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except Exception:
        return []


def _history_append(payload: dict[str, Any]) -> list[dict[str, Any]]:
    history = _history_read()
    summary = payload.get("summary") or {}
    personal = payload.get("personal") or {}
    point = {
        "generated_at": payload.get("generated_at"),
        "trigger_score": summary.get("trigger_score"),
        "crowding_score": summary.get("crowding_score"),
        "state": summary.get("state"),
        "confidence": summary.get("confidence"),
        "evidence_coverage": summary.get("evidence_coverage"),
        "us_trigger": (payload.get("markets") or {}).get("us", {}).get("unwind_score"),
        "kr_trigger": (payload.get("markets") or {}).get("kr", {}).get("unwind_score"),
        "margin_cushion_pct": personal.get("margin_cushion_pct"),
        "effective_leverage": personal.get("effective_leverage"),
        "methodology_version": payload.get("methodology_version"),
    }
    if not history or history[-1].get("generated_at") != point["generated_at"]:
        history.append(point)
    history = history[-_HISTORY_LIMIT:]
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_FILE.write_text(
            json.dumps(_json_safe(history), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        _logger.warning("[LeverageMonitor] 写历史快照失败：%s", exc)
    return history


def _build_dashboard() -> dict[str, Any]:
    requested = sorted({p.symbol for p in PRODUCTS} | {p.benchmark for p in PRODUCTS})
    market_error: str | None = None
    try:
        prices = _download_prices(requested)
    except Exception as exc:
        prices = {}
        market_error = str(exc)

    funding = {
        "us": _fetch_us_margin(),
        "kr": _fetch_korea_margin(),
    }
    rows = [_product_row(product, prices) for product in PRODUCTS]
    us_rows = [row for row in rows if row["market"] == "US"]
    kr_rows = [row for row in rows if row["market"] == "KR"]
    markets = {
        "us": _market_aggregate(us_rows, funding["us"]),
        "kr": _market_aggregate(kr_rows, funding["kr"]),
    }
    personal = _load_personal_snapshot()
    summary = _state_summary(markets, funding, personal)
    posture = _risk_posture(summary, personal)
    now_utc = datetime.now(timezone.utc)
    warnings = [
        "yfinance 为免费行情源：盘中可能延迟；非交易所实时 feed。",
        "盘中最新日 K 的成交量 pace 仅作 preliminary 展示，不进入正式 Trigger 分数。",
        "杠杆/反向 ETF 追踪的是每日目标倍数；20 日 tracking drag 按每日复利目标计算。",
        "融资余额是慢变量：FINRA 为月度滞后数据，不能单独用于 intraday 判断。",
    ]
    if market_error:
        warnings.append(f"本次行情下载失败：{market_error}")
    payload = {
        "generated_at": now_utc.isoformat(timespec="seconds"),
        "market_data_source": "yfinance",
        "market_data_quality": "delayed_or_near_real_time",
        "is_stale": False,
        "summary": summary,
        "posture": posture,
        "personal": personal,
        "markets": markets,
        "funding": funding,
        "evidence": _evidence_ledger(markets, funding, personal),
        "products": rows,
        "sources": [
            {
                "name": "FINRA Margin Statistics",
                "url": FINRA_URL,
                "frequency": "monthly",
            },
            {
                "name": "KOFIA / data.go.kr 信用公与余额",
                "url": KOREA_OFFICIAL_URL,
                "frequency": "daily",
            },
            {
                "name": "Yahoo Finance market data",
                "url": "https://finance.yahoo.com/",
                "frequency": "delayed / daily forming bar",
            },
        ],
        "warnings": warnings,
        "methodology_version": METHODOLOGY_VERSION,
    }
    payload["history"] = _history_append(payload)
    return payload


def _with_current_personal(payload: dict[str, Any]) -> dict[str, Any]:
    """市场 cache 可复用，但账户快照每次读取，避免新诊断被 15 分钟 cache 遮住。"""
    markets = payload.get("markets") or {}
    funding = payload.get("funding") or {}
    if not all(key in markets for key in ("us", "kr")) or not all(key in funding for key in ("us", "kr")):
        return payload
    refreshed = dict(payload)
    personal = _load_personal_snapshot()
    summary = _state_summary(markets, funding, personal)
    refreshed["personal"] = personal
    refreshed["summary"] = summary
    refreshed["posture"] = _risk_posture(summary, personal)
    refreshed["evidence"] = _evidence_ledger(markets, funding, personal)
    return refreshed


def get_dashboard(force: bool = False) -> dict[str, Any]:
    """返回杠杆压力 dashboard；15 分钟缓存，网络失败时退回最近成功缓存。"""
    global _MEM_CACHE
    now = time.time()
    if not force and _MEM_CACHE and now - _MEM_CACHE[0] < _CACHE_TTL_SECONDS:
        return _with_current_personal(_MEM_CACHE[1])

    disk = _read_disk_cache()
    # 方法升级后旧 cache 缺少二维状态、账户关联和证据质量字段，不能继续冒充新结果。
    if disk and disk.get("methodology_version") != METHODOLOGY_VERSION:
        disk = None
    if not force and disk:
        generated = pd.to_datetime(disk.get("generated_at"), utc=True, errors="coerce")
        if not pd.isna(generated) and (pd.Timestamp.now(tz="UTC") - generated).total_seconds() < _CACHE_TTL_SECONDS:
            _MEM_CACHE = (now, disk)
            return _with_current_personal(disk)

    with _LOCK:
        if not force and _MEM_CACHE and now - _MEM_CACHE[0] < _CACHE_TTL_SECONDS:
            return _with_current_personal(_MEM_CACHE[1])
        try:
            payload = _json_safe(_build_dashboard())
            usable = any(row.get("available") for row in payload.get("products", []))
            if not usable and disk:
                fallback = dict(disk)
                fallback["is_stale"] = True
                fallback["stale_reason"] = "本次行情不可用，展示最近成功缓存"
                _MEM_CACHE = (time.time(), fallback)
                return _with_current_personal(fallback)
            _MEM_CACHE = (time.time(), payload)
            if usable:
                _write_disk_cache(payload)
            return payload
        except Exception as exc:
            if disk:
                fallback = dict(disk)
                fallback["is_stale"] = True
                fallback["stale_reason"] = str(exc)
                _MEM_CACHE = (time.time(), fallback)
                return _with_current_personal(fallback)
            raise


__all__ = [
    "PRODUCTS",
    "get_dashboard",
    "known_leverage_for",
    "_parse_finra_html",
    "_parse_finra_excel",
    "_parse_korea_margin_items",
    "_product_row",
    "_market_aggregate",
]
