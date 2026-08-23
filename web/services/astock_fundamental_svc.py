"""A 股主题股票池基本面研究服务。

数据边界：
  - 估值：AKShare 东方财富全市场快照；失败时切腾讯全市场行情备用源
  - 财报：AKShare 东方财富业绩报表（按报告期批量拉取）
  - 历史 PE：用户打开单股详情时按需拉取百度股市通 PE(TTM) 序列

本服务与 astock_momentum_svc 完全分离。基本面接口失败、过期或部分覆盖时，
不会影响动能扫描和调仓接口；所有数据块都带 source/status/fetched_at，空值
保持 None，不用 0 伪装成有效数据。
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from core import astock_universe as _au

_logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]

_SUMMARY_CACHE = ROOT / 'data' / '.astock_fundamental_summary_cache.json'
_DETAIL_CACHE_DIR = ROOT / 'data' / '.astock_fundamental_detail'
_SUMMARY_TTL_MINUTES = 15
_DETAIL_TTL_HOURS = 24
_REPORT_LOOKBACK = 10
_CACHE_SCHEMA_VERSION = 3
_CACHE_LOCK = threading.Lock()
_TX_SPOT_URL = 'https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList'
_TX_SPOT_PAGE_SIZE = 200
_TX_SPOT_MAX_WORKERS = 8
_VALUATION_FIELDS = (
    'price', 'total_market_cap_yi', 'float_market_cap_yi',
    'pe_dynamic', 'pb', 'quote_change_pct',
)

_REPORT_FIELDS = {
    'revenue': '营业总收入-营业总收入',
    'revenue_yoy': '营业总收入-同比增长',
    'net_profit': '净利润-净利润',
    'net_profit_yoy': '净利润-同比增长',
    'eps': '每股收益',
    'roe': '净资产收益率',
    'gross_margin': '销售毛利率',
    'announcement_date': '最新公告日期',
}


def _clean_floats(obj: Any) -> Any:
    """把 NaN/Inf 和 numpy 标量清理成 JSON 安全的 Python 值。"""
    if isinstance(obj, dict):
        return {k: _clean_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_floats(v) for v in obj]
    if isinstance(obj, tuple):
        return [_clean_floats(v) for v in obj]
    if isinstance(obj, (float, int)) and not isinstance(obj, bool):
        value = float(obj)
        if not math.isfinite(value):
            return None
        return value if isinstance(obj, float) else int(obj)
    try:
        if hasattr(obj, 'item'):
            return _clean_floats(obj.item())
    except (AttributeError, TypeError, ValueError, OverflowError):
        pass
    return obj


def _now() -> datetime:
    # Cache timestamps intentionally follow the project's existing local-naive
    # datetime contract so old cache files remain comparable.
    return datetime.now()  # noqa: DTZ005


def _iso(dt: datetime | date | None) -> str | None:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt.isoformat(timespec='seconds')
    return dt.isoformat()


def _to_float(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        parsed = pd.to_datetime(value, errors='coerce')
        if pd.isna(parsed):
            return None
        return parsed.date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _period_parts(period: str) -> tuple[int, int] | None:
    """Return (year, quarter) for a YYYYMMDD report-period string."""
    try:
        year = int(period[:4])
        month = int(period[4:6])
    except (TypeError, ValueError):
        return None
    quarter = {3: 1, 6: 2, 9: 3, 12: 4}.get(month)
    return (year, quarter) if quarter else None


def _period_label(period: str) -> str:
    parts = _period_parts(period)
    return f'{parts[0]}Q{parts[1]}' if parts else period


def _candidate_report_periods(today: date | None = None, limit: int = _REPORT_LOOKBACK) -> list[str]:
    """Return completed calendar report-period dates, newest first.

    The current quarter end is included only after it has passed; this avoids
    requesting a future report date while still allowing the latest completed
    period to be fetched during the reporting season.
    """
    today = today or date.today()  # noqa: DTZ011
    periods: list[str] = []
    for year in range(today.year, today.year - 5, -1):
        for month, day in ((12, 31), (9, 30), (6, 30), (3, 31)):
            period_date = date(year, month, day)
            if period_date <= today:
                periods.append(period_date.strftime('%Y%m%d'))
    return periods[:limit]


def _load_theme_universe() -> tuple[list[str], dict[str, dict[str, str]]]:
    """Load current theme symbols with board/subcategory labels."""
    themes = _au.load_themes().get('groups', {})
    codes: list[str] = []
    meta: dict[str, dict[str, str]] = {}
    for subcat, cfg in themes.items():
        board = _au.board_of(subcat)
        board_cfg = _au.BOARDS.get(board, {})
        for raw in cfg.get('symbols', []):
            code = str(raw).zfill(6)
            if code in meta:
                continue
            codes.append(code)
            meta[code] = {
                'group': board,
                'group_label': str(board_cfg.get('label', board)),
                'subcat': subcat,
                'subcat_label': str(cfg.get('label', subcat)),
            }
    names = _au.get_astock_names(codes)
    for code in codes:
        meta[code]['name'] = names.get(code, code)
    return codes, meta


def _source_state(
    name: str,
    status: str,
    fetched_at: str | None,
    *,
    coverage: int = 0,
    total: int = 0,
    stale: bool = False,
    error: str | None = None,
    data_as_of: str | None = None,
    field_coverage: dict[str, int] | None = None,
    fallback_used: bool = False,
    fallback_from: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    return {
        'name': name,
        'status': status,
        'fetched_at': fetched_at,
        'data_as_of': data_as_of,
        'delay': 'unknown',
        'coverage': coverage,
        'total': total,
        'stale': stale,
        'error': error,
        'field_coverage': field_coverage or {},
        'fallback_used': fallback_used,
        'fallback_from': fallback_from,
        'notes': notes,
    }


def _read_json(path: Path) -> dict | None:
    try:
        with path.open(encoding='utf-8') as f:
            value = json.load(f)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_json_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(_clean_floats(data), f, ensure_ascii=False, allow_nan=False)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _cache_is_fresh(updated: str | None, *, minutes: int | None = None, hours: int | None = None) -> bool:
    if not updated:
        return False
    try:
        dt = datetime.fromisoformat(updated)
    except (TypeError, ValueError):
        return False
    age = _now() - dt
    if minutes is not None:
        return age < timedelta(minutes=minutes)
    if hours is not None:
        return age < timedelta(hours=hours)
    return False


def _field_coverage(rows: dict[str, dict], codes: list[str]) -> dict[str, int]:
    return {
        field: sum(1 for code in codes if rows.get(code, {}).get(field) is not None)
        for field in _VALUATION_FIELDS
    }


def _normalize_eastmoney_valuation_row(raw: pd.Series) -> dict[str, float | None]:
    market_cap = _to_float(raw.get('总市值'))
    float_market_cap = _to_float(raw.get('流通市值'))
    quote_change = _to_float(raw.get('涨跌幅'))
    return {
        'price': _to_float(raw.get('最新价')),
        'total_market_cap_yi': market_cap / 1e8 if market_cap is not None else None,
        'float_market_cap_yi': float_market_cap / 1e8 if float_market_cap is not None else None,
        'pe_dynamic': _to_float(raw.get('市盈率-动态')),
        'pb': _to_float(raw.get('市净率')),
        'quote_change_pct': quote_change / 100 if quote_change is not None else None,
    }


def _normalize_tencent_valuation_row(raw: dict[str, Any]) -> tuple[str, dict[str, float | None]] | None:
    """Normalize Tencent rank fields; market-cap values are already in 亿元.

    Tencent exposes ``pe_ttm`` rather than Eastmoney's dynamic PE. It is used
    only as a transparent fallback so the table remains useful when the main
    snapshot is unavailable; the source note tells the user that the PE
    denominator is TTM, not the Eastmoney dynamic-PE definition.
    """
    raw_code = str(raw.get('code') or '').strip().lower()
    code = raw_code[-6:] if raw_code[-6:].isdigit() else ''
    if len(code) != 6:
        return None
    quote_change = _to_float(raw.get('zdf'))
    return code, {
        'price': _to_float(raw.get('zxj')),
        'total_market_cap_yi': _to_float(raw.get('zsz')),
        'float_market_cap_yi': _to_float(raw.get('ltsz')),
        'pe_dynamic': _to_float(raw.get('pe_ttm')),
        'pb': _to_float(raw.get('pn')),
        'quote_change_pct': quote_change / 100 if quote_change is not None else None,
    }


def _fetch_tencent_page(offset: int) -> list[dict]:
    response = requests.get(
        _TX_SPOT_URL,
        params={
            '_appver': '11.17.0',
            'board_code': 'aStock',
            'sort_type': 'price',
            'direct': 'down',
            'offset': str(offset),
            'count': str(_TX_SPOT_PAGE_SIZE),
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get('code') not in (0, None):
        raise RuntimeError(f"Tencent quote returned code={payload.get('code')}")
    return list((payload.get('data') or {}).get('rank_list') or [])


def _fetch_tencent_valuation(codes: list[str]) -> tuple[dict[str, dict], dict]:
    """Fetch Tencent's paginated A-share rank endpoint as valuation fallback.

    The request shape mirrors ``ak.stock_zh_a_spot_tx`` but fetches pages in
    parallel because the AKShare wrapper intentionally serializes all pages.
    This keeps a fallback refresh within the frontend's 180-second timeout.
    """
    fetched_at = _iso(_now())
    try:
        # The endpoint has no stable public schema documentation; the first
        # page is also used to discover the current number of pages.
        probe_response = requests.get(
            _TX_SPOT_URL,
            params={
                '_appver': '11.17.0',
                'board_code': 'aStock',
                'sort_type': 'price',
                'direct': 'down',
                'offset': '0',
                'count': str(_TX_SPOT_PAGE_SIZE),
            },
            timeout=30,
        )
        probe_response.raise_for_status()
        probe = probe_response.json()
        first_data = probe.get('data') or {}
        first = list(first_data.get('rank_list') or [])
        total = int(first_data.get('total') or len(first))
        offsets = list(range(_TX_SPOT_PAGE_SIZE, total, _TX_SPOT_PAGE_SIZE))
        rows = list(first)
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=_TX_SPOT_MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_tencent_page, offset): offset for offset in offsets}
            for future in as_completed(futures):
                offset = futures[future]
                try:
                    rows.extend(future.result())
                except Exception as exc:  # noqa: BLE001
                    errors.append(f'offset={offset}: {str(exc)[:120]}')

        result: dict[str, dict] = {}
        wanted = set(codes)
        for raw in rows:
            normalized = _normalize_tencent_valuation_row(raw)
            if normalized is None:
                continue
            code, values = normalized
            if code in wanted:
                result[code] = values
        coverage = len(result)
        status = 'ok' if coverage == len(codes) else ('partial' if coverage else 'unavailable')
        return result, _source_state(
            'tencent_spot', status, fetched_at,
            coverage=coverage, total=len(codes),
            field_coverage=_field_coverage(result, codes),
            error='; '.join(errors[:3]) if errors else None,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.warning('[AStockFundamental] Tencent valuation fallback failed: %s', exc)
        return {}, _source_state(
            'tencent_spot', 'unavailable', fetched_at,
            coverage=0, total=len(codes), error=str(exc)[:240],
            field_coverage={field: 0 for field in _VALUATION_FIELDS},
        )


def _fetch_valuation(codes: list[str]) -> tuple[dict[str, dict], dict]:
    """Fetch the primary spot source, then fill unavailable fields from Tencent."""
    fetched_at = _iso(_now())
    primary_rows: dict[str, dict] = {}
    primary_error: str | None = None
    try:
        from core.astock_data_store import ak
        spot = ak.stock_zh_a_spot_em()
        if spot is None or spot.empty:
            raise RuntimeError('stock_zh_a_spot_em returned empty data')
        wanted = set(codes)
        for _, raw in spot.iterrows():
            code = str(raw.get('代码', '')).strip().zfill(6)
            if code in wanted:
                primary_rows[code] = _normalize_eastmoney_valuation_row(raw)
    except Exception as exc:  # noqa: BLE001
        primary_error = str(exc)[:240]
        _logger.warning('[AStockFundamental] valuation fetch failed: %s', exc)

    primary_state = _source_state(
        'eastmoney_spot',
        'ok' if len(primary_rows) == len(codes) else ('partial' if primary_rows else 'unavailable'),
        fetched_at,
        coverage=len(primary_rows), total=len(codes),
        field_coverage=_field_coverage(primary_rows, codes),
        error=primary_error,
    )
    missing_fields = any(
        primary_rows.get(code, {}).get(field) is None
        for code in codes for field in _VALUATION_FIELDS
    )
    if not missing_fields:
        return primary_rows, primary_state

    fallback_rows, fallback_state = _fetch_tencent_valuation(codes)
    merged: dict[str, dict] = {}
    fallback_used = False
    for code in codes:
        values = dict(primary_rows.get(code, {}))
        alternate = fallback_rows.get(code, {})
        for field in _VALUATION_FIELDS:
            if values.get(field) is None and alternate.get(field) is not None:
                values[field] = alternate[field]
                fallback_used = True
        if values:
            merged[code] = values

    if not fallback_used:
        return primary_rows, primary_state
    coverage = len(merged)
    return merged, _source_state(
        'tencent_spot' if not primary_rows else 'eastmoney_spot+tencent_spot',
        'ok' if coverage == len(codes) else 'partial', fetched_at,
        coverage=coverage, total=len(codes),
        field_coverage=_field_coverage(merged, codes),
        fallback_used=True, fallback_from='eastmoney_spot',
        notes='备用 PE 字段为腾讯 PE(TTM)，不等同于东方财富动态 PE',
        error='; '.join(x for x in (primary_error, fallback_state.get('error')) if x) or None,
    )


def _normalize_report_row(raw: pd.Series, period: str) -> dict[str, Any]:
    """Normalize one stock_yjbb_em row.

    AKShare exposes report metrics in yuan / percentage points. The API
    contract uses 亿元 for amounts and decimal fractions for percentages.
    """
    def amount_yi(field: str) -> float | None:
        value = _to_float(raw.get(field))
        return round(value / 1e8, 4) if value is not None else None

    def pct_decimal(field: str) -> float | None:
        value = _to_float(raw.get(field))
        return round(value / 100, 6) if value is not None else None

    return {
        'report_period': period,
        'report_label': _period_label(period),
        'report_year': _period_parts(period)[0] if _period_parts(period) else None,
        'report_quarter': _period_parts(period)[1] if _period_parts(period) else None,
        'is_cumulative': True,
        'revenue_yi': amount_yi(_REPORT_FIELDS['revenue']),
        'revenue_yoy': pct_decimal(_REPORT_FIELDS['revenue_yoy']),
        'net_profit_yi': amount_yi(_REPORT_FIELDS['net_profit']),
        'net_profit_yoy': pct_decimal(_REPORT_FIELDS['net_profit_yoy']),
        'eps': _to_float(raw.get(_REPORT_FIELDS['eps'])),
        'roe': pct_decimal(_REPORT_FIELDS['roe']),
        'gross_margin': pct_decimal(_REPORT_FIELDS['gross_margin']),
        'announcement_date': _to_date(raw.get(_REPORT_FIELDS['announcement_date'])),
    }


def _fetch_financials(codes: list[str]) -> tuple[dict[str, list[dict]], dict]:
    """Fetch recent report periods in bulk and return code -> records."""
    fetched_at = _iso(_now())
    records: dict[str, list[dict]] = {code: [] for code in codes}
    successful_periods = 0
    errors: list[str] = []
    try:
        for period in _candidate_report_periods():
            try:
                # This is the same RPT_LICO_FN_CPD endpoint used by
                # AKShare.stock_yjbb_em, but with a larger page size. The
                # AKShare wrapper defaults to 500 rows and can make a single
                # report period fan out into 10+ requests; 5,000 keeps the
                # source and fields identical while reducing round trips.
                frame = _fetch_yjbb_period(period, codes)
                if frame is None or frame.empty:
                    continue
                successful_periods += 1
                for _, raw in frame.iterrows():
                    code = str(raw.get('股票代码', '')).strip().zfill(6)
                    if code not in records:
                        continue
                    records[code].append(_normalize_report_row(raw, period))
            except Exception as exc:  # noqa: BLE001
                errors.append(f'{period}: {str(exc)[:120]}')
                _logger.warning('[AStockFundamental] report %s failed: %s', period, exc)
    except Exception as exc:  # noqa: BLE001
        errors.append(str(exc)[:120])

    for code, rows in records.items():
        dedup: dict[str, dict] = {}
        for row in rows:
            dedup[row['report_period']] = row
        records[code] = [dedup[k] for k in sorted(dedup)]
    coverage = sum(1 for rows in records.values() if rows)
    status = 'ok' if coverage == len(codes) and successful_periods else (
        'partial' if coverage or successful_periods else 'unavailable'
    )
    return records, _source_state(
        'eastmoney_yjbb', status, fetched_at,
        coverage=coverage, total=len(codes),
        error='; '.join(errors[:3]) if errors else None,
    )


def _fetch_yjbb_period(period: str, codes: list[str]) -> pd.DataFrame:
    """Fetch one report period with an Eastmoney/AKShare-compatible filter.

    ``stock_yjbb_em`` exposes only the report date and downloads the whole
    market.  The underlying endpoint also accepts a numeric ``SECURITY_CODE
    in (...)`` predicate; restricting it to the approved theme universe keeps
    the response to one page in normal cases without changing the source
    fields or financial definitions.
    """
    url = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
    params = {
        'sortColumns': 'UPDATE_DATE,SECURITY_CODE',
        'sortTypes': '-1,-1',
        'pageSize': '500',
        'pageNumber': '1',
        'reportName': 'RPT_LICO_FN_CPD',
        'columns': 'ALL',
        'filter': (
            f"(REPORTDATE='{period[:4]}-{period[4:6]}-{period[6:]}')"
            f"(SECURITY_CODE in ({','.join(str(code).zfill(6) for code in codes)}))"
        ),
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    payload = response.json()
    result = payload.get('result') or {}
    rows = list(result.get('data') or [])
    pages = int(result.get('pages') or 1)
    for page in range(2, pages + 1):
        page_params = dict(params, pageNumber=str(page))
        page_response = requests.get(url, params=page_params, timeout=30)
        page_response.raise_for_status()
        page_result = (page_response.json().get('result') or {})
        rows.extend(page_result.get('data') or [])
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).rename(columns={
        'SECURITY_CODE': '股票代码',
        'BASIC_EPS': '每股收益',
        'TOTAL_OPERATE_INCOME': '营业总收入-营业总收入',
        'YSTZ': '营业总收入-同比增长',
        'PARENT_NETPROFIT': '净利润-净利润',
        'SJLTZ': '净利润-同比增长',
        'WEIGHTAVG_ROE': '净资产收益率',
        'XSMLL': '销售毛利率',
        'NOTICE_DATE': '最新公告日期',
    })
    return frame


def _record_by_period(records: list[dict], year: int, quarter: int) -> dict | None:
    return next((r for r in records
                 if r.get('report_year') == year and r.get('report_quarter') == quarter), None)


def _derive_single_quarter(records: list[dict], current: dict) -> tuple[float | None, float | None, bool]:
    """Derive one quarter from cumulative report values when adjacent data exists."""
    year = current.get('report_year')
    quarter = current.get('report_quarter')
    if not isinstance(year, int) or not isinstance(quarter, int):
        return None, None, False
    if quarter == 1:
        prior = None
    else:
        prior = _record_by_period(records, year, quarter - 1)
    if quarter != 1 and prior is None:
        return None, None, False
    revenue = current.get('revenue_yi')
    net_profit = current.get('net_profit_yi')
    if revenue is None or net_profit is None:
        return None, None, False
    if prior is not None:
        if prior.get('revenue_yi') is None or prior.get('net_profit_yi') is None:
            return None, None, False
        revenue -= prior['revenue_yi']
        net_profit -= prior['net_profit_yi']
    return round(revenue, 4), round(net_profit, 4), True


def _calculate_ttm(
    records: list[dict], latest: dict | None = None,
) -> tuple[float | None, float | None, str]:
    """Return (revenue_ttm, net_profit_ttm, state) for a report period."""
    if not records:
        return None, None, 'missing'
    latest = latest or max(records, key=lambda row: row.get('report_period', ''))
    year = latest.get('report_year')
    quarter = latest.get('report_quarter')
    revenue = latest.get('revenue_yi')
    net_profit = latest.get('net_profit_yi')
    if not isinstance(year, int) or not isinstance(quarter, int):
        return None, None, 'missing'
    if revenue is None or net_profit is None:
        return None, None, 'missing'
    if quarter == 4:
        ttm_revenue, ttm_profit = revenue, net_profit
    else:
        prior_fy = _record_by_period(records, year - 1, 4)
        prior_same = _record_by_period(records, year - 1, quarter)
        if prior_fy is None or prior_same is None:
            return None, None, 'not_derivable'
        if any(v is None for v in (
            prior_fy.get('revenue_yi'), prior_fy.get('net_profit_yi'),
            prior_same.get('revenue_yi'), prior_same.get('net_profit_yi'),
        )):
            return None, None, 'not_derivable'
        ttm_revenue = revenue + prior_fy['revenue_yi'] - prior_same['revenue_yi']
        ttm_profit = net_profit + prior_fy['net_profit_yi'] - prior_same['net_profit_yi']
    state = 'valid' if ttm_profit > 0 else ('loss_making' if ttm_profit < 0 else 'zero_earnings')
    return round(ttm_revenue, 4), round(ttm_profit, 4), state


def _growth_rate(current: float | None, prior: float | None) -> float | None:
    """Return a growth rate only when the prior denominator is positive."""
    if current is None or prior is None or prior <= 0:
        return None
    return round(current / prior - 1, 6)


def _ttm_growth(records: list[dict]) -> tuple[float | None, float | None, str]:
    """Return TTM revenue growth, TTM profit growth and comparability state."""
    if not records:
        return None, None, 'missing'
    latest = max(records, key=lambda row: row.get('report_period', ''))
    year = latest.get('report_year')
    quarter = latest.get('report_quarter')
    if not isinstance(year, int) or not isinstance(quarter, int):
        return None, None, 'missing'
    current_revenue, current_profit, _ = _calculate_ttm(records, latest)
    prior_report = _record_by_period(records, year - 1, quarter)
    if prior_report is None:
        return None, None, 'not_derivable'
    prior_revenue, prior_profit, _ = _calculate_ttm(records, prior_report)
    revenue_growth = _growth_rate(current_revenue, prior_revenue)
    profit_growth = _growth_rate(current_profit, prior_profit)
    if revenue_growth is None:
        return None, profit_growth, 'not_derivable'
    # A negative/zero prior profit makes percentage profit growth misleading;
    # retain the revenue growth point and mark the profit color as non-comparable.
    return revenue_growth, profit_growth, 'valid' if profit_growth is not None else 'profit_not_comparable'


def _enrich_periods(records: list[dict]) -> list[dict]:
    out: list[dict] = []
    for current in records:
        row = dict(current)
        single_revenue, single_profit, derivable = _derive_single_quarter(records, current)
        row.update({
            'single_quarter_revenue_yi': single_revenue,
            'single_quarter_net_profit_yi': single_profit,
            'single_quarter_derivable': derivable,
        })
        out.append(row)
    return out


def _build_summary_row(code: str, meta: dict, valuation: dict, records: list[dict]) -> dict:
    latest = max(records, key=lambda row: row.get('report_period', '')) if records else None
    enriched = _enrich_periods(records)
    ttm_revenue, ttm_profit, pe_state = _calculate_ttm(records)
    ttm_revenue_yoy, ttm_net_profit_yoy, ttm_growth_state = _ttm_growth(records)
    total_cap = valuation.get('total_market_cap_yi')
    pe_ttm = total_cap / ttm_profit if total_cap and ttm_profit and ttm_profit > 0 else None
    earnings_yield = (
        ttm_profit / total_cap
        if ttm_profit is not None and total_cap is not None and total_cap > 0 else None
    )
    quality_status = 'missing'
    if pe_state == 'loss_making':
        quality_status = 'loss_making'
    elif pe_state == 'valid':
        roe = latest.get('roe') if latest else None
        net_profit_yoy = latest.get('net_profit_yoy') if latest else None
        if roe is not None and roe >= 0.15 and (net_profit_yoy is None or net_profit_yoy >= 0):
            quality_status = 'strong'
        elif roe is not None and roe >= 0:
            quality_status = 'mixed'
        else:
            quality_status = 'weak'
    return {
        'symbol': code,
        'name': meta.get('name', code),
        'group': meta.get('group'),
        'group_label': meta.get('group_label'),
        'subcat': meta.get('subcat'),
        'subcat_label': meta.get('subcat_label'),
        'price': valuation.get('price'),
        'quote_change_pct': valuation.get('quote_change_pct'),
        'total_market_cap_yi': total_cap,
        'float_market_cap_yi': valuation.get('float_market_cap_yi'),
        'pe_ttm': round(pe_ttm, 2) if pe_ttm is not None else None,
        'earnings_yield': round(earnings_yield, 6) if earnings_yield is not None else None,
        'pe_dynamic': valuation.get('pe_dynamic'),
        'pb': valuation.get('pb'),
        'pe_state': pe_state,
        'quality_status': quality_status,
        'ttm_growth_state': ttm_growth_state,
        'ttm_revenue_yi': ttm_revenue,
        'ttm_net_profit_yi': ttm_profit,
        'ttm_revenue_yoy': ttm_revenue_yoy,
        'ttm_net_profit_yoy': ttm_net_profit_yoy,
        'latest_report_period': latest.get('report_period') if latest else None,
        'latest_report_label': latest.get('report_label') if latest else None,
        'latest_announcement_date': latest.get('announcement_date') if latest else None,
        'revenue_yi': latest.get('revenue_yi') if latest else None,
        'revenue_yoy': latest.get('revenue_yoy') if latest else None,
        'net_profit_yi': latest.get('net_profit_yi') if latest else None,
        'net_profit_yoy': latest.get('net_profit_yoy') if latest else None,
        'eps': latest.get('eps') if latest else None,
        'roe': latest.get('roe') if latest else None,
        'gross_margin': latest.get('gross_margin') if latest else None,
        'financial_period_count': len(enriched),
        'financial_status': 'ok' if latest else 'unavailable',
    }


def _overall_status(source_status: dict[str, dict]) -> str:
    statuses = [s.get('status') for s in source_status.values()]
    if statuses and all(s == 'ok' for s in statuses):
        return 'ok'
    if any(s in ('ok', 'partial') for s in statuses):
        return 'partial'
    return 'unavailable'


def _build_summary() -> dict:
    codes, meta = _load_theme_universe()
    valuation, valuation_state = _fetch_valuation(codes)
    financials, financial_state = _fetch_financials(codes)
    rows = [_build_summary_row(code, meta[code], valuation.get(code, {}), financials.get(code, []))
            for code in codes]
    fetched_at = _iso(_now())
    result = {
        'schema_version': _CACHE_SCHEMA_VERSION,
        'status': _overall_status({'valuation': valuation_state, 'financial': financial_state}),
        'as_of': fetched_at,
        'last_updated': fetched_at,
        'universe_count': len(codes),
        'coverage': {
            'valuation': valuation_state.get('coverage', 0),
            'financial': financial_state.get('coverage', 0),
        },
        'source_status': {
            'valuation': valuation_state,
            'financial': financial_state,
        },
        'report_periods': _candidate_report_periods()[:_REPORT_LOOKBACK],
        'rows': rows,
        '_financials': financials,
        '_meta': meta,
    }
    # Private fields are kept in the on-disk cache for detail reconstruction,
    # but stripped from the API response by get_fundamentals().
    return _clean_floats(result)


def _public_summary(data: dict) -> dict:
    return {k: v for k, v in data.items() if not k.startswith('_')}


def get_fundamentals(force: bool = False) -> dict:
    """Return the theme-pool fundamental summary with source status."""
    with _CACHE_LOCK:
        cached = _read_json(_SUMMARY_CACHE)
        if (
            not force
            and cached
            and cached.get('schema_version') == _CACHE_SCHEMA_VERSION
            and _cache_is_fresh(cached.get('as_of'), minutes=_SUMMARY_TTL_MINUTES)
        ):
            return _public_summary(cached)
        try:
            fresh = _build_summary()
            _write_json_atomic(_SUMMARY_CACHE, fresh)
            return _public_summary(fresh)
        except Exception as exc:
            _logger.exception('[AStockFundamental] summary build failed')
            if cached:
                stale = dict(cached)
                stale['status'] = 'partial'
                stale['as_of'] = cached.get('as_of')
                stale['source_status'] = dict(cached.get('source_status') or {})
                for state in stale['source_status'].values():
                    if isinstance(state, dict):
                        state['stale'] = True
                        state['status'] = 'partial'
                        state['error'] = str(exc)[:240]
                return _public_summary(stale)
            now = _iso(_now())
            return {
                'schema_version': _CACHE_SCHEMA_VERSION,
                'status': 'unavailable',
                'as_of': now,
                'last_updated': now,
                'universe_count': 0,
                'coverage': {'valuation': 0, 'financial': 0},
                'source_status': {
                    'valuation': _source_state('eastmoney_spot', 'unavailable', now, error=str(exc)[:240]),
                    'financial': _source_state('eastmoney_yjbb', 'unavailable', now, error=str(exc)[:240]),
                },
                'report_periods': [],
                'rows': [],
            }


def _detail_cache_path(code: str) -> Path:
    return _DETAIL_CACHE_DIR / f'{str(code).zfill(6)}.json'


def _fetch_pe_history(code: str) -> tuple[list[dict], dict]:
    fetched_at = _iso(_now())
    try:
        from core.astock_data_store import ak
        frame = ak.stock_zh_valuation_baidu(
            symbol=code, indicator='市盈率(TTM)', period='近三年'
        )
        history: list[dict] = []
        if frame is not None and not frame.empty:
            for _, raw in frame.iterrows():
                value = _to_float(raw.get('value'))
                # PE <= 0 means current earnings are not a usable positive denominator.
                history.append({'date': _to_date(raw.get('date')), 'value': value if value and value > 0 else None})
        history = [row for row in history if row.get('date')]
        status = 'ok' if history else 'unavailable'
        return history, _source_state('baidu_valuation', status, fetched_at, coverage=len(history), total=1)
    except Exception as exc:  # noqa: BLE001
        _logger.warning('[AStockFundamental] PE history %s failed: %s', code, exc)
        return [], _source_state('baidu_valuation', 'unavailable', fetched_at, total=1, error=str(exc)[:240])


def _percentile(value: float | None, history: list[dict]) -> float | None:
    if value is None or value <= 0:
        return None
    values = [v['value'] for v in history if isinstance(v.get('value'), (int, float)) and v['value'] > 0]
    if not values:
        return None
    return round(sum(v <= value for v in values) / len(values), 4)


def get_fundamental_detail(code: str, force: bool = False, pe_period: str = '3y') -> dict:
    """Return one stock's summary, financial periods and historical PE."""
    code = str(code).strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        raise ValueError('A 股代码必须是 6 位数字')
    # Validate against the approved theme universe before doing per-stock work.
    summary = get_fundamentals(force=False)
    row = next((r for r in summary.get('rows', []) if r.get('symbol') == code), None)
    if row is None:
        raise ValueError(f'{code} 不在当前 A 股主题股票池中')
    cache_path = _detail_cache_path(code)
    cached = _read_json(cache_path)
    if (
        not force
        and cached
        and cached.get('schema_version') == _CACHE_SCHEMA_VERSION
        and _cache_is_fresh(cached.get('updated'), hours=_DETAIL_TTL_HOURS)
    ):
        return cached.get('payload', cached)

    stored_summary = _read_json(_SUMMARY_CACHE) or {}
    records = (stored_summary.get('_financials') or {}).get(code, [])
    periods = _enrich_periods(records)
    pe_history, pe_state = _fetch_pe_history(code)
    payload = {
        'status': 'ok' if row else 'unavailable',
        'as_of': _iso(_now()),
        'symbol': code,
        'summary': row,
        'periods': periods,
        'pe_history': pe_history,
        'pe_percentile_3y': _percentile(row.get('pe_ttm'), pe_history),
        'source_status': {
            'summary': summary.get('source_status', {}),
            'pe_history': pe_state,
        },
        'pe_period': pe_period,
    }
    try:
        _write_json_atomic(cache_path, {'schema_version': _CACHE_SCHEMA_VERSION, 'updated': _iso(_now()), 'payload': _clean_floats(payload)})
    except Exception as exc:  # noqa: BLE001
        _logger.warning('[AStockFundamental] detail cache write %s failed: %s', code, exc)
    return _clean_floats(payload)


if __name__ == '__main__':
    import argparse
    import pprint

    parser = argparse.ArgumentParser(description='A 股主题股票池基本面摘要')
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--code')
    args = parser.parse_args()
    pprint.pp(get_fundamental_detail(args.code, force=args.force) if args.code else get_fundamentals(force=args.force))
