"""
Parquet-based 本地数据存储层（替代 MySQL + MarketCache）。

目录结构：
  data/
  └── stocks/
      ├── AAPL.parquet          ← 个股 OHLCV，index=date（DatetimeIndex）
      └── ...

用法：
  from core.data_store import DataStore
  store = DataStore()

  # 获取指定时间段的数据（自动增量更新）
  data = store.get(['AAPL', 'SPY'], start='2024-01-01', end='2024-12-31')
  # → {symbol: DataFrame(open/high/low/close/volume)}

  # 获取 date × symbol 收盘价矩阵（横截面分析）
  close = store.load_multi(['AAPL', 'SPY'], '2024-01-01', '2024-12-31')
  spy = close['SPY']

CLI（独立运行，下载 / 更新数据）：
  python -m core.data_store --universe sp500 --start 2022-01-01
"""

import logging
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')

_logger = logging.getLogger(__name__)


class DataStore:
    """
    Parquet-based 本地 OHLCV 数据存储。

    - stocks/{SYMBOL}.parquet  : 个股全历史，index=date（DatetimeIndex）

    核心方法：
      get(symbols, start, end)       → dict[str, DataFrame]   # 主入口，自动增量更新
      update(symbols, start, end)                              # 仅下载缺失部分
      load_multi(symbols, start, end) → DataFrame             # date×symbol 横截面矩阵
    """

    def __init__(self, data_dir: str | Path = 'data', ibkr_store=None):
        self.root       = Path(data_dir)
        self.stocks_dir = self.root / 'stocks'
        self.stocks_dir.mkdir(parents=True, exist_ok=True)
        # update() 期间发现 yfinance 无数据（同批其他有数据）的 symbol，_load() 直接过滤
        self._no_data_syms: set[str] = set()
        # update()→_date_range() 期间缓存各 symbol 的最新日期，_load() 复用避免重复读文件
        self._last_date_cache: dict[str, 'date | None'] = {}
        # 可选：IBKRDataStore 实例，用于 yfinance 复权跳变时的数据修复
        self._ibkr_store = ibkr_store

    # ── 主入口 ────────────────────────────────────────────────

    def get(
        self,
        symbols:    list[str],
        start:      str,
        end:        str  = None,
        min_rows:   int  = 40,
        auto_update: bool = True,
        force_refresh_recent_days: int = 0,
    ) -> dict[str, pd.DataFrame]:
        """
        确保数据是最新的，然后返回 {symbol: DataFrame}。
        与 MarketCache.get() 接口兼容，无需修改调用方代码。

        force_refresh_recent_days: 强制重拉最近 N 个交易日(覆盖 yfinance 在
        美东收盘前/后两次拉取产生的"stale-but-corrupted"数据,典型场景如
        MRVL 5/29 volume 13.75M→33.95M 的校正)。N=0 时为现有 incremental 行为。
        """
        end = self._cap_end(end)
        if auto_update:
            self.update(symbols, start, end, force_refresh_recent_days=force_refresh_recent_days)
        return self._load(symbols, start, end, min_rows)

    def update(self, symbols: list[str], start: str, end: str = None,
               force_refresh_recent_days: int = 0) -> bool:
        """
        增量下载：只补充本地没有的日期。
        force_refresh_recent_days > 0 时,强制从 last_trading - N×2 日(留余量给周末)起重拉,
        合并时由 dedup(keep='last') 自动用 fresh 数据覆盖旧值。
        返回 True 表示有新数据被写入。
        """
        end = self._cap_end(end)
        self._no_data_syms = set()   # 每次 update 重置

        groups: dict[str, list[str]] = {}
        for sym in symbols:
            dl_from = self._dl_from(sym, start, end, force_refresh_recent_days)
            if dl_from is not None:
                groups.setdefault(dl_from, []).append(sym)

        if not groups:
            _logger.info('[DataStore] 数据已是最新，无需下载')
            return False

        total = sum(len(v) for v in groups.values())
        _logger.info(f'[DataStore] 需更新 {total} 只，分 {len(groups)} 批下载...')
        written     = 0
        all_got     : set[str] = set()   # yfinance 有返回数据（含重复）的 symbol
        all_no_data : set[str] = set()   # yfinance 完全无数据的 symbol
        for dl_from, syms in sorted(groups.items()):
            saved, got, no_data = self._download_and_save(syms, dl_from, end)
            written     += saved
            all_got     |= got
            all_no_data |= no_data
        # 仅在本次运行内存中标记疑似退市（不写文件、不删 parquet）
        # 条件：其他 symbol 有数据，说明不是网络问题，该 symbol 才被视为疑似退市
        if all_got and all_no_data:
            self._no_data_syms = all_no_data
        return written > 0

    def load_multi(
        self,
        symbols: list[str],
        start:   str,
        end:     str,
        column:  str = 'close',
    ) -> pd.DataFrame:
        """
        返回 date × symbol 矩阵（单列），用于横截面分析。

        例：
          close = store.load_multi(syms, '2024-01-01', '2024-12-31', 'close')
          spy   = close['SPY']
          corr  = close.corr()
        """
        ts_start = pd.Timestamp(start)
        ts_end   = pd.Timestamp(end)
        frames: dict[str, pd.Series] = {}
        for sym in symbols:
            path = self.stocks_dir / f'{sym}.parquet'
            if not path.exists():
                continue
            df = pd.read_parquet(path, columns=[column])
            df = df[(df.index >= ts_start) & (df.index <= ts_end)]
            if not df.empty:
                frames[sym] = df[column]
        return pd.DataFrame(frames)

    # ── 内部：增量判断 ────────────────────────────────────────

    def _dl_from(self, symbol: str, requested_start: str, end: str,
                 force_refresh_recent_days: int = 0) -> str | None:
        """
        计算该 symbol 的下载起始日期，返回 None 表示无需下载。

        规则（按优先级顺序）：
          1. 本地无文件                       → 从 requested_start 全量下载
          2. 本地最早日期 > 请求起点          → 从 requested_start 补历史
          3. force_refresh_recent_days > 0    → 从 last_trading - N×2 日重拉
                                                (覆盖 yfinance 校正过的最近 K 线)
          4. 已经足够新且无历史缺口           → None（跳过）
          5. 本地最新日期 < end               → 从 latest+1 增量补充
        """
        last_trading = self._last_trading_day()
        end_date     = date.fromisoformat(end)
        start_date   = date.fromisoformat(requested_start)

        latest, earliest = self._date_range(symbol)

        if latest is None:
            return requested_start                               # 完全没有数据

        # 先检查历史是否完整（不受 latest 是否最新影响）
        if earliest is not None and earliest > start_date:
            return requested_start                               # 历史不够，向前补

        # 强制刷新最近 N 个交易日(×2 日历日留余量给周末/节假日)
        if force_refresh_recent_days > 0:
            refresh_from = last_trading - timedelta(days=force_refresh_recent_days * 2)
            return refresh_from.strftime('%Y-%m-%d') if refresh_from <= end_date else None

        # 历史完整再判断是否最新
        if latest >= min(end_date, last_trading):
            return None                                          # 数据已最新，无需下载

        next_day = (latest + timedelta(days=1)).strftime('%Y-%m-%d')
        return next_day if next_day <= end else None             # 增量补充

    # ── 内部：下载 & 存储 ─────────────────────────────────────

    def _download_and_save(
        self, symbols: list[str], start: str, end: str
    ) -> tuple[int, set[str], set[str]]:
        """批量下载并 append-save 到个股 parquet 文件。
        返回 (written, got_data_syms, no_data_syms)：
          got_data_syms  — yfinance 有返回数据（含重复/无新行）的 symbol
          no_data_syms   — yfinance 完全无数据的 symbol
        """
        end_exclusive = (date.fromisoformat(end) + timedelta(days=1)).strftime('%Y-%m-%d')
        try:
            raw = yf.download(
                symbols,
                start=start,
                end=end_exclusive,
                auto_adjust=True,
                progress=True,
                group_by='ticker',
                threads=True,
            )
        except Exception as e:
            print(f'  [DataStore] 下载失败：{e}')
            return 0, set(), set()   # 整批失败，不标记任何 symbol（网络问题，非退市）

        saved    = 0
        got_syms : set[str] = set()
        bad_syms : set[str] = set()
        suspect_syms: set[str] = set()   # 批量数据中涨跌幅异常，需单只重试
        for sym in symbols:
            df = self._extract(raw, sym, len(symbols))
            if df is None or df.empty:
                bad_syms.add(sym)
                continue
            got_syms.add(sym)
            path = self.stocks_dir / f'{sym}.parquet'
            if path.exists():
                old      = pd.read_parquet(path)
                new_rows = df[~df.index.isin(old.index)]
                if new_rows.empty:
                    continue                          # 无新数据，跳过写入
                # 校验衔接处涨跌幅：区分回补（新数据比现有更旧）和追加（新数据比现有更新）
                if not old.empty and 'close' in old.columns and 'close' in new_rows.columns:
                    is_backfill = new_rows.index.max() < old.index[0]   # 历史回补
                    is_append   = new_rows.index.min() > old.index[-1]  # 向前追加
                    if is_append:
                        # 追加：检查 new_rows 首行 vs old 末行的衔接
                        ref_old = old['close'].iloc[-1]
                        ref_new = new_rows['close'].iloc[0]
                        if ref_old > 0 and abs(ref_new / ref_old - 1) > 0.20:
                            _logger.warning(
                                f'[DataStore] {sym} 复权跳变（追加）'
                                f'({ref_old:.2f}→{ref_new:.2f})，触发全量重下'
                            )
                            suspect_syms.add(sym)
                            continue
                    elif is_backfill:
                        # 回补：检查 new_rows 末行 vs old 首行的衔接
                        ref_new = new_rows['close'].iloc[-1]
                        ref_old = old['close'].iloc[0]
                        if ref_old > 0 and abs(ref_new / ref_old - 1) > 0.20:
                            _logger.warning(
                                f'[DataStore] {sym} 复权跳变（回补）'
                                f'({ref_new:.2f}→{ref_old:.2f})，触发全量重下'
                            )
                            suspect_syms.add(sym)
                            continue
                    # 跨范围混合（既有回补又有追加）：直接合并，dedup 处理
                df = pd.concat([old, df])
                df = df[~df.index.duplicated(keep='last')].sort_index()
            df.index.name = 'date'
            df.to_parquet(path)
            saved += 1

        # 复权跳变的 symbol：优先从 IBKR 修复，不可用时 yfinance 全量重下
        if suspect_syms:
            _logger.warning(f'[DataStore] 复权跳变 {len(suspect_syms)} 只，开始修复：{sorted(suspect_syms)}')
            full_start = (date.today() - timedelta(days=365 * 8)).strftime('%Y-%m-%d')
            for sym in sorted(suspect_syms):
                path = self.stocks_dir / f'{sym}.parquet'
                repaired = False
                # ── 优先：IBKR ────────────────────────────────
                if self._ibkr_store is not None:
                    df_ibkr = self._ibkr_store.fetch_df(sym)
                    if df_ibkr is not None and not df_ibkr.empty:
                        df_ibkr.index.name = 'date'
                        df_ibkr.to_parquet(path)
                        saved += 1
                        got_syms.add(sym)
                        _logger.info(
                            f'[DataStore] {sym} 已从 IBKR 修复，'
                            f'{len(df_ibkr)} 行，close={df_ibkr["close"].iloc[-1]:.2f}'
                        )
                        repaired = True
                # ── 退路：yfinance 全量重下 ────────────────────
                if not repaired:
                    try:
                        r = yf.download(sym, start=full_start, end=end_exclusive,
                                        auto_adjust=True, progress=False)
                        df2 = self._extract(r, sym, 1)
                        if df2 is not None and not df2.empty:
                            df2.index.name = 'date'
                            df2.to_parquet(path)
                            saved += 1
                            _logger.info(
                                f'[DataStore] {sym} yfinance 全量重下完成，'
                                f'{len(df2)} 行，close={df2["close"].iloc[-1]:.2f}'
                            )
                        else:
                            bad_syms.add(sym)
                    except Exception as e:
                        _logger.error(f'[DataStore] {sym} 全量重下失败：{e}')
                        bad_syms.add(sym)

        # 批量下载中空数据的 symbol 并行重试（yfinance 批量偶发空列问题）
        if bad_syms:
            def _retry_one(sym: str):
                try:
                    r = yf.download(sym, start=start, end=end_exclusive,
                                    auto_adjust=True, progress=False)
                    return sym, self._extract(r, sym, 1)
                except Exception:
                    return sym, None

            retry_bad: set[str] = set()
            with ThreadPoolExecutor(max_workers=8) as ex:
                futs = {ex.submit(_retry_one, s): s for s in sorted(bad_syms)}
                for fut in as_completed(futs, timeout=90):
                    sym, df2 = fut.result()
                    if df2 is None or df2.empty:
                        retry_bad.add(sym)
                        continue
                    got_syms.add(sym)
                    path = self.stocks_dir / f'{sym}.parquet'
                    if path.exists():
                        old      = pd.read_parquet(path)
                        new_rows = df2[~df2.index.isin(old.index)]
                        if not new_rows.empty:
                            df2 = pd.concat([old, df2])
                            df2 = df2[~df2.index.duplicated(keep='last')].sort_index()
                        else:
                            continue
                    df2.index.name = 'date'
                    df2.to_parquet(path)
                    saved += 1
            bad_syms = retry_bad

        if bad_syms:
            _logger.warning(f'[DataStore] yfinance 无数据（疑似退市）：{sorted(bad_syms)}')
        _logger.info(f'[DataStore] 写入 {saved}/{len(symbols)} 只')
        return saved, got_syms, bad_syms

    # ── 内部：读取 ───────────────────────────────────────────

    def _load(
        self,
        symbols:   list[str],
        start:     str,
        end:       str,
        min_rows:  int,
        max_stale: int = 4,   # 最后一条 K 线比 SPY 最新日早超过 N 日历天，视为退市/停牌
    ) -> dict[str, pd.DataFrame]:
        result = {}
        ts_start = pd.Timestamp(start)
        ts_end   = pd.Timestamp(end)
        stale_syms = []

        # 用 SPY 的实际最后 K 线日期作为参考基准（比 _last_trading_day() 更精确，自动处理节假日）
        spy_path = self.stocks_dir / 'SPY.parquet'
        if spy_path.exists():
            spy_idx = pd.read_parquet(spy_path, columns=['close']).index
            ref_date = spy_idx.max() if not spy_idx.empty else pd.Timestamp(self._last_trading_day())
        else:
            ref_date = pd.Timestamp(self._last_trading_day())
        stale_limit = ref_date - pd.Timedelta(days=max_stale)
        # 只有"请求的 end 本身接近今天"时才做 stale 过滤（选股/实盘场景）。
        # 历史回测的 end 早于 stale_limit，退市股的历史数据应当保留参与计算。
        check_stale = ts_end >= stale_limit
        for sym in symbols:
            # update() 中 yfinance 对此 symbol 无数据（同批其他有）→ 疑似退市，直接过滤
            # 但仅在实盘/选股场景下过滤；历史回测同样放行
            if check_stale and sym in self._no_data_syms:
                stale_syms.append(sym)
                continue
            path = self.stocks_dir / f'{sym}.parquet'
            if not path.exists():
                continue
            # stale 检查：优先用 update()→_date_range() 期间缓存的最新日期；
            # cache miss（如 auto_update=False）时做轻量读，避免为确认 stale 而全量加载
            if check_stale:
                last_dt = self._last_date_cache.get(sym)
                if last_dt is None and sym not in self._last_date_cache:
                    idx = pd.read_parquet(path, columns=['close']).index
                    last_dt = idx[-1].date() if not idx.empty else None
                if last_dt is not None and pd.Timestamp(last_dt) < stale_limit:
                    stale_syms.append(sym)
                    continue
            df_full = pd.read_parquet(path)
            df = df_full[(df_full.index >= ts_start) & (df_full.index <= ts_end)]
            if len(df) < min_rows:
                continue
            result[sym] = df
        if stale_syms:
            _logger.warning(f'[DataStore] 数据过期跳过 {len(stale_syms)} 只（疑似退市/停牌）：{stale_syms}')
        return result

    # ── 内部：工具函数 ────────────────────────────────────────

    def _date_range(self, symbol: str) -> tuple[date | None, date | None]:
        """返回 (最新日期, 最早日期)，文件不存在时返回 (None, None)。"""
        path = self.stocks_dir / f'{symbol}.parquet'
        if not path.exists():
            self._last_date_cache[symbol] = None
            return None, None
        # 只读 close 列（parquet 列式存储，比读全部快很多）
        idx = pd.read_parquet(path, columns=['close']).index
        if idx.empty:
            self._last_date_cache[symbol] = None
            return None, None
        latest = idx.max().date()
        self._last_date_cache[symbol] = latest   # 供 _load() stale 检查复用，避免重复读文件
        return latest, idx.min().date()

    @staticmethod
    def _last_trading_day() -> date:
        today  = date.today()
        offset = {0: 3, 6: 2}.get(today.weekday(), 1)   # 周一→3, 周日→2, 其余→1
        return today - timedelta(days=offset)

    @staticmethod
    def _cap_end(end: str = None) -> str:
        """将 end 限制到最近交易日，防止请求未来数据。"""
        last = date.today() - timedelta(
            days={0: 3, 6: 2}.get(date.today().weekday(), 1)
        )
        cap = last.strftime('%Y-%m-%d')
        return cap if end is None else min(end, cap)

    @staticmethod
    def _extract(raw: pd.DataFrame, sym: str, n: int) -> pd.DataFrame | None:
        """从 yfinance 多 ticker 下载结果中提取单只股票的 OHLCV。"""
        try:
            if n == 1:
                df = raw.copy()
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = [c[0].lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]
            else:
                df = raw[sym].copy()
                df.columns = [c.lower() for c in df.columns]

            df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)
            return df if not df.empty else None
        except Exception:
            return None


# ── CLI：独立运行更新数据 ─────────────────────────────────────

if __name__ == '__main__':
    import argparse
    from core.universe import get_tickers

    parser = argparse.ArgumentParser(description='Parquet 数据更新工具')
    parser.add_argument('--universe', default='sp500+ndx',
                        help='股票池（默认 sp500+ndx）')
    parser.add_argument('--start',   default='2022-01-01',
                        help='历史起始日期（默认 2022-01-01）')
    parser.add_argument('--end',     default=None,
                        help='结束日期（默认今天）')
    args = parser.parse_args()

    tickers = list(set(get_tickers(args.universe) + ['SPY']))
    print(f'\n更新 {len(tickers)} 只股票（{args.start} → {args.end or "今天"}）...')

    store = DataStore()
    store.update(tickers, args.start, args.end)
    print('\n完成。')
