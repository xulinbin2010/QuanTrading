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
_CACHE_TTL_SECONDS = 15 * 60
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
    LeveragedProduct("BITX", "2x Bitcoin Strategy ETF", "US", 2.0, "BTC-USD", "加密资产", "Volatility Shares"),
    LeveragedProduct("122630.KS", "KODEX 레버리지", "KR", 2.0, "069500.KS", "KOSPI200", "Samsung"),
    LeveragedProduct("252670.KS", "KODEX 200선물인버스2X", "KR", -2.0, "069500.KS", "KOSPI200", "Samsung"),
    LeveragedProduct("233740.KS", "KODEX 코스닥150레버리지", "KR", 2.0, "229200.KS", "KOSDAQ150", "Samsung"),
    LeveragedProduct("251340.KS", "KODEX 코스닥150선물인버스", "KR", -1.0, "229200.KS", "KOSDAQ150", "Samsung"),
)


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

    def add(name: str, value: float | None, raw_points: float, max_points: float) -> None:
        components.append({
            "name": name,
            "value": _finite(value, 6),
            "points": round(max(0.0, min(raw_points, max_points)), 1),
            "max_points": max_points,
        })

    if long_ret is not None:
        add("多头杠杆下跌", long_ret, max(-long_ret, 0) / 0.06 * 30, 30)
    if long_vol is not None:
        add("多头成交放大", long_vol, max(long_vol - 1, 0) / 2 * 15, 15)
    if inverse_ret is not None:
        add("反向产品上涨", inverse_ret, max(inverse_ret, 0) / 0.06 * 20, 20)
    if inverse_vol is not None:
        add("反向成交放大", inverse_vol, max(inverse_vol - 1, 0) / 2 * 15, 15)
    if gap_abs is not None:
        add("跟踪偏差扩大", gap_abs, gap_abs / 0.02 * 10, 10)
    if funding and funding.get("available") and funding.get("mom") is not None:
        mom = float(funding["mom"])
        add("融资余额收缩", mom, max(-mom, 0) / 0.05 * 10, 10)

    points = sum(float(c["points"]) for c in components)
    possible = sum(float(c["max_points"]) for c in components)
    score = round(points / possible * 100, 1) if possible else None
    level = "high" if score is not None and score >= 65 else "mid" if score is not None and score >= 35 else "low"
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

    scores = [
        markets[key]["unwind_score"]
        for key in ("us", "kr")
        if markets[key].get("unwind_score") is not None
    ]
    global_score = round(max(scores), 1) if scores else None
    global_level = (
        "high" if global_score is not None and global_score >= 65
        else "mid" if global_score is not None and global_score >= 35
        else "low"
    )
    us_score = markets["us"].get("unwind_score")
    kr_score = markets["kr"].get("unwind_score")
    now_utc = datetime.now(timezone.utc)
    warnings = [
        "yfinance 为免费行情源：盘中可能延迟；非交易所实时 feed。",
        "盘中最新日 K 的成交量按已过交易时段比例折算为 pace，属于估算值。",
        "杠杆/反向 ETF 追踪的是每日目标倍数；20 日 tracking drag 按每日复利目标计算。",
        "融资余额是慢变量：FINRA 为月度滞后数据，不能单独用于 intraday 判断。",
    ]
    if market_error:
        warnings.append(f"本次行情下载失败：{market_error}")
    return {
        "generated_at": now_utc.isoformat(timespec="seconds"),
        "market_data_source": "yfinance",
        "market_data_quality": "delayed_or_near_real_time",
        "is_stale": False,
        "summary": {
            "unwind_score": global_score,
            "unwind_level": global_level,
            "dominant_market": (
                "US" if float(us_score if us_score is not None else -1) >= float(kr_score if kr_score is not None else -1)
                else "KR"
            ) if scores else None,
        },
        "markets": markets,
        "funding": funding,
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
        "methodology_version": "1.0",
    }


def get_dashboard(force: bool = False) -> dict[str, Any]:
    """返回杠杆压力 dashboard；15 分钟缓存，网络失败时退回最近成功缓存。"""
    global _MEM_CACHE
    now = time.time()
    if not force and _MEM_CACHE and now - _MEM_CACHE[0] < _CACHE_TTL_SECONDS:
        return _MEM_CACHE[1]

    disk = _read_disk_cache()
    if not force and disk:
        generated = pd.to_datetime(disk.get("generated_at"), utc=True, errors="coerce")
        if not pd.isna(generated) and (pd.Timestamp.now(tz="UTC") - generated).total_seconds() < _CACHE_TTL_SECONDS:
            _MEM_CACHE = (now, disk)
            return disk

    with _LOCK:
        if not force and _MEM_CACHE and now - _MEM_CACHE[0] < _CACHE_TTL_SECONDS:
            return _MEM_CACHE[1]
        try:
            payload = _json_safe(_build_dashboard())
            usable = any(row.get("available") for row in payload.get("products", []))
            if not usable and disk:
                fallback = dict(disk)
                fallback["is_stale"] = True
                fallback["stale_reason"] = "本次行情不可用，展示最近成功缓存"
                _MEM_CACHE = (time.time(), fallback)
                return fallback
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
                return fallback
            raise


__all__ = [
    "PRODUCTS",
    "get_dashboard",
    "_parse_finra_html",
    "_parse_finra_excel",
    "_parse_korea_margin_items",
    "_product_row",
    "_market_aggregate",
]
