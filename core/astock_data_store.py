"""A 股 Parquet 本地数据存储层（akshare 数据源）。

与美股 core/data_store.py 的 DataStore 并行，接口保持一致：
  store = AStockDataStore()
  data = store.get(['600519', '000001'], start='2024-01-01')
  # → {code: DataFrame(open/high/low/close/volume), index=DatetimeIndex}

目录：data/stocks_a/{code}.parquet

注意（代理）：akshare 抓国内数据源（东方财富/新浪/申万），若本机开了科学上网代理
会连不上。模块 import 时把这些国内域名追加到 no_proxy，使 akshare 直连，
同时不影响美股 yfinance 走代理（只追加国内域名，不全局禁用）。
"""
from __future__ import annotations

import os
import logging
import threading
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

warnings.filterwarnings('ignore')

_logger = logging.getLogger(__name__)

# sina 源（stock_zh_a_daily）内部用 py_mini_racer 解密，V8 上下文非线程安全，
# 多线程并发会段错误崩溃，故所有 sina 调用串行化。
_sina_lock = threading.Lock()


# ── 代理处理：国内数据域名直连，不影响美股走代理 ──────────────
_CN_DIRECT_DOMAINS = [
    'eastmoney.com', 'push2.eastmoney.com', 'push2his.eastmoney.com',
    'datacenter-web.eastmoney.com', 'datacenter.eastmoney.com',
    'sinajs.cn', 'sina.com.cn', 'finance.sina.com.cn',
    'swhysc.com', 'swsresearch.com', 'legulegu.com',
]


def _ensure_cn_direct():
    """把国内数据域名追加进 no_proxy/NO_PROXY（保留已有值），让 akshare 直连。"""
    for key in ('no_proxy', 'NO_PROXY'):
        existing = os.environ.get(key, '')
        parts = [p.strip() for p in existing.split(',') if p.strip()]
        changed = False
        for d in _CN_DIRECT_DOMAINS:
            if d not in parts:
                parts.append(d)
                changed = True
        if changed:
            os.environ[key] = ','.join(parts)


_ensure_cn_direct()

import akshare as ak  # noqa: E402  （必须在 _ensure_cn_direct 之后）


# ── 基准指数代码映射（akshare stock_zh_index_daily 用 sh/sz 前缀）──
INDEX_SYMBOLS = {
    'HS300': 'sh000300',   # 沪深300（默认大盘基准）
    'SH':    'sh000001',   # 上证指数
    'ZZ500': 'sh000905',   # 中证500
    'CYB':   'sz399006',   # 创业板指
}


def _tx_symbol(code: str) -> str:
    """6 位代码 → 腾讯源前缀格式：6→sh, 0/3→sz, 8/4→bj。"""
    if code.startswith('6'):
        return 'sh' + code
    if code.startswith(('0', '3')):
        return 'sz' + code
    return 'bj' + code


def _normalize_hist(df: pd.DataFrame) -> pd.DataFrame:
    """akshare stock_zh_a_hist 中文列 → 标准 OHLCV，index=DatetimeIndex。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    col_map = {'日期': 'date', '开盘': 'open', '收盘': 'close',
               '最高': 'high', '最低': 'low', '成交量': 'volume'}
    out = df.rename(columns=col_map)[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    out['date'] = pd.to_datetime(out['date'])
    out = out.set_index('date').sort_index()
    for c in ['open', 'high', 'low', 'close', 'volume']:
        out[c] = pd.to_numeric(out[c], errors='coerce')
    return out.dropna()


def _normalize_hist_tx(df: pd.DataFrame) -> pd.DataFrame:
    """腾讯源 stock_zh_a_hist_tx 列（date/open/close/high/low/amount[/volume]）→ 标准 OHLCV。

    腾讯源常只给 amount（成交额，元）不给 volume（成交量）。动能算法只用 volume 的
    相对比值（量比/OBV/上涨量占比），单位可抵消，故缺 volume 时用 amount 代理填充。
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if 'date' not in out.columns:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    if 'volume' not in out.columns and 'amount' in out.columns:
        out['volume'] = out['amount']
    keep = ['date', 'open', 'high', 'low', 'close', 'volume']
    out = out[[c for c in keep if c in out.columns]].copy()
    out['date'] = pd.to_datetime(out['date'])
    out = out.set_index('date').sort_index()
    for c in ['open', 'high', 'low', 'close', 'volume']:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')
    return out.dropna()


def _normalize_hist_sina(df: pd.DataFrame) -> pd.DataFrame:
    """sina 源 stock_zh_a_daily 列（date/open/high/low/close/volume/amount/outstanding_share/...）
    → 标准 OHLCV + shares（流通股本，用于算流通市值）。"""
    if df is None or df.empty:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    out = df.copy()
    out.columns = [str(c).lower() for c in out.columns]
    if 'date' not in out.columns:
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
    keep = ['date', 'open', 'high', 'low', 'close', 'volume', 'outstanding_share']
    out = out[[c for c in keep if c in out.columns]].copy()
    out['date'] = pd.to_datetime(out['date'])
    out = out.set_index('date').sort_index()
    for c in ['open', 'high', 'low', 'close', 'volume', 'outstanding_share']:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors='coerce')
    out = out.rename(columns={'outstanding_share': 'shares'})
    return out.dropna(subset=['open', 'high', 'low', 'close', 'volume'])


class AStockDataStore:
    """A 股本地 OHLCV 存储（akshare 前复权日线）。"""

    def __init__(self, data_dir: str | Path = 'data'):
        self.root = Path(data_dir)
        self.stocks_dir = self.root / 'stocks_a'
        self.stocks_dir.mkdir(parents=True, exist_ok=True)
        self._no_data: set[str] = set()

    # ── 路径 ──
    def _path(self, code: str) -> Path:
        return self.stocks_dir / f'{code}.parquet'

    def _load_local(self, code: str) -> pd.DataFrame | None:
        p = self._path(code)
        if not p.exists():
            return None
        try:
            return pd.read_parquet(p)
        except Exception:
            return None

    # ── 单只下载（增量，带重试 + 备用源兜底）──
    def _download(self, code: str, start: str, retries: int = 3) -> pd.DataFrame:
        """拉单只前复权日线。主源 sina（含真实成交量，国内直连稳定），
        断连则退避重试；仍失败依次切东方财富、腾讯备用源。"""
        import time
        start_ymd = start.replace('-', '')
        sym = _tx_symbol(code)
        # 主源：sina（stock_zh_a_daily，返回 open/high/low/close/volume/amount）
        for attempt in range(retries):
            try:
                with _sina_lock:
                    raw = ak.stock_zh_a_daily(symbol=sym, start_date=start_ymd, adjust='qfq')
                df = _normalize_hist_sina(raw)
                if not df.empty:
                    return df
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        # 备用源1：东方财富（网络恢复时可用）
        try:
            raw = ak.stock_zh_a_hist(symbol=code, period='daily',
                                     start_date=start_ymd, adjust='qfq')
            df = _normalize_hist(raw)
            if not df.empty:
                return df
        except Exception:
            pass
        # 备用源2：腾讯（无 volume 时以 amount 代理）
        try:
            raw = ak.stock_zh_a_hist_tx(symbol=sym, adjust='qfq', start_date=start_ymd)
            return _normalize_hist_tx(raw)
        except Exception as e:
            _logger.warning(f'[AStock] {code} 三源均失败：{e}')
        return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

    def _update_one(self, code: str, start: str) -> pd.DataFrame:
        """增量更新单只：本地有则只补最新日期之后，无则全量。"""
        local = self._load_local(code)
        today = date.today()
        # 自愈：旧数据缺 volume 列（早期腾讯源遗留）则丢弃本地，强制全量重拉
        if local is not None and not local.empty and 'volume' not in local.columns:
            local = None
        if local is not None and not local.empty:
            last = local.index[-1].date()
            if last >= today - timedelta(days=1):
                return local  # 已是最新（或昨日），不重拉
            fetch_start = (last + timedelta(days=1)).strftime('%Y-%m-%d')
            fresh = self._download(code, fetch_start)
            if not fresh.empty:
                merged = pd.concat([local, fresh])
                merged = merged[~merged.index.duplicated(keep='last')].sort_index()
                merged.to_parquet(self._path(code))
                return merged
            return local
        # 无本地数据，全量
        full = self._download(code, start)
        if not full.empty:
            full.to_parquet(self._path(code))
        else:
            self._no_data.add(code)
        return full

    # ── 主入口 ──
    def get(self, symbols: list[str], start: str = '2023-01-01',
            end: str | None = None, auto_update: bool = True,
            max_workers: int = 5) -> dict[str, pd.DataFrame]:
        """返回 {code: DataFrame}。code 为 6 位数字；'HS300' 等基准自动转指数。"""
        result: dict[str, pd.DataFrame] = {}
        codes = list(dict.fromkeys(symbols))

        # 基准指数单独处理
        index_codes = [c for c in codes if c in INDEX_SYMBOLS]
        stock_codes = [c for c in codes if c not in INDEX_SYMBOLS]

        for ic in index_codes:
            df = self._get_index(ic, start, auto_update)
            if df is not None and not df.empty:
                result[ic] = df

        if auto_update:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futs = {ex.submit(self._update_one, c, start): c for c in stock_codes}
                for fut in as_completed(futs):
                    c = futs[fut]
                    try:
                        df = fut.result()
                        if df is not None and not df.empty:
                            result[c] = df
                    except Exception as e:
                        _logger.warning(f'[AStock] {c} 失败：{e}')
        else:
            for c in stock_codes:
                df = self._load_local(c)
                if df is not None and not df.empty:
                    result[c] = df

        # 按 end 截断
        if end:
            end_ts = pd.to_datetime(end)
            result = {k: v[v.index <= end_ts] for k, v in result.items()}
        # 按 start 截断（本地可能含更早数据）
        start_ts = pd.to_datetime(start)
        result = {k: v[v.index >= start_ts] for k, v in result.items()}
        return result

    def _get_index(self, key: str, start: str, auto_update: bool) -> pd.DataFrame | None:
        """指数（沪深300 等）日线，含本地缓存。"""
        cache_path = self.stocks_dir / f'_idx_{key}.parquet'
        if not auto_update and cache_path.exists():
            try:
                return pd.read_parquet(cache_path)
            except Exception:
                pass
        # 缓存命中（当天）直接用
        if cache_path.exists():
            try:
                cached = pd.read_parquet(cache_path)
                if not cached.empty and cached.index[-1].date() >= date.today() - timedelta(days=1):
                    return cached
            except Exception:
                pass
        try:
            raw = ak.stock_zh_index_daily(symbol=INDEX_SYMBOLS[key])
            raw['date'] = pd.to_datetime(raw['date'])
            df = raw.set_index('date').sort_index()
            df = df[['open', 'high', 'low', 'close', 'volume']]
            df.to_parquet(cache_path)
            return df
        except Exception as e:
            _logger.warning(f'[AStock] 指数 {key} 下载失败：{e}')
            if cache_path.exists():
                try:
                    return pd.read_parquet(cache_path)
                except Exception:
                    pass
            return None
