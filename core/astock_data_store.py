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
        """增量更新单只：补 (a) earliest > start 时向前回补历史 (b) 最新日期之后增量，无本地则全量。"""
        local = self._load_local(code)
        today = date.today()
        # 自愈：旧数据缺 volume 列（早期腾讯源遗留）则丢弃本地，强制全量重拉
        if local is not None and not local.empty and 'volume' not in local.columns:
            local = None
        if local is not None and not local.empty:
            # (a) 历史不够：earliest > start 时从 start 重拉，merge dedup(keep='last') 覆盖重叠
            earliest_local = local.index[0].date()
            req_start = date.fromisoformat(start) if isinstance(start, str) else start
            if earliest_local > req_start:
                back_fill = self._download(code, start)
                if not back_fill.empty:
                    local = pd.concat([local, back_fill])
                    local = local[~local.index.duplicated(keep='last')].sort_index()
                    local.to_parquet(self._path(code))
            # (b) 最新之后增量
            last = local.index[-1].date()
            if last >= today:
                return local  # 已含当日数据，不重拉
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

    # ── 当日补齐：sina 历史日K接口对当天有延迟，盘后用实时快照补当天 bar ──
    def _fetch_spot(self, retries: int = 3):
        """新浪全市场实时快照，带重试（该接口高频调用会被限流）。"""
        import time
        for i in range(retries):
            try:
                df = ak.stock_zh_a_spot()
                if df is not None and not df.empty:
                    return df
            except Exception:
                time.sleep(1.0 * (i + 1))
        return None

    def topup_today_from_spot(self, codes: list[str]) -> int:
        """用实时快照把"当天"日K补进本地 Parquet（仅交易日）。返回补齐只数。
        快照含 今开/最高/最低/最新价/成交量/昨收，单位与 sina 日线一致（股）。
        盘中可重复调用：当天 bar 用最新快照覆盖（最新价/最高/最低/量滚动更新），
        以"快照昨收≈昨日收盘"做连续性校验，防止错位。次日 refresh_recent 用正式日线覆盖。"""
        today = date.today()
        if today.isoweekday() > 5:   # 周末无新数据
            return 0
        spot = self._fetch_spot()
        if spot is None:
            _logger.warning('[AStock] 实时快照不可用，跳过当日补齐')
            return 0
        spot = spot.copy()
        spot['_c6'] = spot['代码'].astype(str).str[-6:]
        smap = {r['_c6']: r for _, r in spot.iterrows()}
        ts = pd.Timestamp(today)
        n = 0
        for code in dict.fromkeys(str(c).zfill(6) for c in codes if c not in INDEX_SYMBOLS):
            local = self._load_local(code)
            if local is None or local.empty or 'volume' not in local.columns:
                continue
            if local.index[-1].date() > today:
                continue   # 本地存在未来日期 bar（异常），跳过
            r = smap.get(code)
            if r is None:
                continue
            try:
                o, hi, lo = float(r['今开']), float(r['最高']), float(r['最低'])
                close, vol, prev = float(r['最新价']), float(r['成交量']), float(r['昨收'])
            except Exception:
                continue
            if not (close > 0 and vol > 0 and o > 0):
                continue
            # 连续性基准取「昨日(<today)」最后一根，避免盘中已补的当天实时价干扰校验
            prior = local[local.index.date < today]
            if prior.empty:
                continue
            # 缺口自愈：本地最后一根距今 > 4 天（超出正常周末 Fri→Mon=3 天间隔），说明中间
            # 缺交易日。若此时直接把当日快照续到陈旧 bar 上，会导致：①「今日涨幅」被算成跨多日
            # 涨幅（如 601133 06-17→06-29 算出假的 +27%）②本地最后一根变成今日，后续 get()
            # 的 _update_one 因 last>=today 短路，永远不再回补缺口。故先用历史日线补缺口再贴当日。
            # 长假场景无害：假期内 _download 返回空，prior 不变，前后两根本就是连续交易日。
            last_prior = prior.index[-1].date()
            if (today - last_prior).days > 4:
                gap = self._download(code, (last_prior + timedelta(days=1)).strftime('%Y-%m-%d'))
                if not gap.empty:
                    local = pd.concat([local, gap])
                    local = local[~local.index.duplicated(keep='last')].sort_index()
                    local.to_parquet(self._path(code))
                    prior = local[local.index.date < today]
                    if prior.empty:
                        continue
            last_close = float(prior['close'].iloc[-1])
            if last_close <= 0 or abs(prev - last_close) / last_close > 0.2:
                continue   # 连续性校验失败（疑似代码错位/复权跳变），跳过
            # 数据新鲜度：节假日(工作日休市)spot 返回上一交易日陈旧快照，最新价+成交量
            # 与本地昨日完全相同 → 跳过，不补重复假 bar（isoweekday>5 仅排周末，节假日漏网）
            last_vol = float(prior['volume'].iloc[-1]) if 'volume' in prior.columns else None
            if close == last_close and last_vol is not None and vol == last_vol:
                continue
            row = {'open': o, 'high': hi, 'low': lo, 'close': close, 'volume': vol}
            if 'shares' in prior.columns:
                row['shares'] = prior['shares'].iloc[-1]   # 盘中流通股本不变，用昨日值
            merged = pd.concat([local, pd.DataFrame([row], index=[ts])])
            merged = merged[~merged.index.duplicated(keep='last')].sort_index()
            merged.to_parquet(self._path(code))
            n += 1
        return n

    def topup_index_today(self, key: str) -> bool:
        """用指数实时快照补当天基准 bar（如 HS300）。"""
        today = date.today()
        if today.isoweekday() > 5 or key not in INDEX_SYMBOLS:
            return False
        cache_path = self.stocks_dir / f'_idx_{key}.parquet'
        if not cache_path.exists():
            return False
        try:
            local = pd.read_parquet(cache_path)
        except Exception:
            return False
        if local.empty or local.index[-1].date() > today:
            return False   # 盘中可重复覆盖当天 bar（>today 才跳过）
        sym = INDEX_SYMBOLS[key]
        num = sym[2:] if sym[:2] in ('sh', 'sz') else sym
        import time
        idx = None
        for i in range(3):
            try:
                idx = ak.stock_zh_index_spot_sina()
                if idx is not None and not idx.empty:
                    break
            except Exception:
                time.sleep(1.0 * (i + 1))
        if idx is None or idx.empty:
            return False
        try:
            ccol = '代码' if '代码' in idx.columns else idx.columns[0]
            row = idx[idx[ccol].astype(str).str.contains(num)]
            if row.empty:
                return False
            r = row.iloc[0]
            o, hi, lo, close = float(r['今开']), float(r['最高']), float(r['最低']), float(r['最新价'])
            if close <= 0:
                return False
            # 数据新鲜度：节假日指数 spot 返回陈旧值，与本地昨日 close 相同 → 不补假 bar
            prior_idx = local[local.index.date < today]
            if not prior_idx.empty and close == float(prior_idx['close'].iloc[-1]):
                return False
            new = pd.DataFrame([{'open': o, 'high': hi, 'low': lo, 'close': close, 'volume': 0.0}],
                               index=[pd.Timestamp(today)])
            merged = pd.concat([local, new])
            merged = merged[~merged.index.duplicated(keep='last')].sort_index()
            merged.to_parquet(cache_path)
            return True
        except Exception:
            return False

    def refresh_recent(self, codes: list[str], days: int = 15) -> int:
        """重拉最近 N 天正式前复权日线并覆盖本地（次日复核用：把前一日的快照原始价 bar
        换成 sina 正式 qfq 数据）。返回处理只数。"""
        start = (date.today() - timedelta(days=days)).strftime('%Y-%m-%d')
        n = 0
        for code in dict.fromkeys(str(c).zfill(6) for c in codes if c not in INDEX_SYMBOLS):
            fresh = self._download(code, start)
            if fresh.empty:
                continue
            local = self._load_local(code)
            if local is None or local.empty or 'volume' not in local.columns:
                fresh.to_parquet(self._path(code))
            else:
                merged = pd.concat([local, fresh])
                merged = merged[~merged.index.duplicated(keep='last')].sort_index()  # fresh 覆盖重叠
                merged.to_parquet(self._path(code))
            n += 1
        return n

    def refresh_index(self, key: str) -> bool:
        """重拉指数日线覆盖缓存（次日复核基准）。"""
        if key not in INDEX_SYMBOLS:
            return False
        try:
            raw = ak.stock_zh_index_daily(symbol=INDEX_SYMBOLS[key])
            raw['date'] = pd.to_datetime(raw['date'])
            df = raw.set_index('date').sort_index()[['open', 'high', 'low', 'close', 'volume']]
            df.to_parquet(self.stocks_dir / f'_idx_{key}.parquet')
            return True
        except Exception as e:
            _logger.warning(f'[AStock] 指数 {key} 复核重拉失败：{e}')
            return False

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
