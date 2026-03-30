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

import warnings
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

warnings.filterwarnings('ignore')


class DataStore:
    """
    Parquet-based 本地 OHLCV 数据存储。

    - stocks/{SYMBOL}.parquet  : 个股全历史，index=date（DatetimeIndex）

    核心方法：
      get(symbols, start, end)       → dict[str, DataFrame]   # 主入口，自动增量更新
      update(symbols, start, end)                              # 仅下载缺失部分
      load_multi(symbols, start, end) → DataFrame             # date×symbol 横截面矩阵
    """

    def __init__(self, data_dir: str | Path = 'data'):
        self.root       = Path(data_dir)
        self.stocks_dir = self.root / 'stocks'
        self.stocks_dir.mkdir(parents=True, exist_ok=True)

    # ── 主入口 ────────────────────────────────────────────────

    def get(
        self,
        symbols:    list[str],
        start:      str,
        end:        str  = None,
        min_rows:   int  = 40,
        auto_update: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        确保数据是最新的，然后返回 {symbol: DataFrame}。
        与 MarketCache.get() 接口兼容，无需修改调用方代码。
        """
        end = self._cap_end(end)
        if auto_update:
            self.update(symbols, start, end)
        return self._load(symbols, start, end, min_rows)

    def update(self, symbols: list[str], start: str, end: str = None) -> bool:
        """
        增量下载：只补充本地没有的日期。
        返回 True 表示有新数据被写入。
        """
        end = self._cap_end(end)

        groups: dict[str, list[str]] = {}
        for sym in symbols:
            dl_from = self._dl_from(sym, start, end)
            if dl_from is not None:
                groups.setdefault(dl_from, []).append(sym)

        if not groups:
            print('  [DataStore] 数据已是最新，无需下载')
            return False

        total = sum(len(v) for v in groups.values())
        print(f'  [DataStore] 需更新 {total} 只，分 {len(groups)} 批下载...')
        written = 0
        for dl_from, syms in sorted(groups.items()):
            written += self._download_and_save(syms, dl_from, end)
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

    def _dl_from(self, symbol: str, requested_start: str, end: str) -> str | None:
        """
        计算该 symbol 的下载起始日期，返回 None 表示无需下载。

        规则：
          1. 本地无文件              → 从 requested_start 全量下载
          2. 本地最早日期 > 请求起点 → 从 requested_start 补历史
          3. 本地最新日期 < end-7天  → 从 latest+1 增量补充
          4. 已经足够新              → None（跳过）
        """
        last_trading = self._last_trading_day()
        end_date     = date.fromisoformat(end)
        start_date   = date.fromisoformat(requested_start)

        latest, earliest = self._date_range(symbol)

        if latest is None:
            dl_from = requested_start                            # 完全没有数据
        elif earliest is not None and earliest > start_date:
            dl_from = requested_start                            # 历史不够，向前补
        elif latest >= min(end_date, last_trading):
            return None                                          # 已经足够新
        else:
            next_day = (latest + timedelta(days=1)).strftime('%Y-%m-%d')
            if next_day > end:
                return None                                      # 超过请求终点
            dl_from = next_day

        return dl_from if dl_from <= end else None

    # ── 内部：下载 & 存储 ─────────────────────────────────────

    def _download_and_save(self, symbols: list[str], start: str, end: str) -> int:
        """批量下载并 append-save 到个股 parquet 文件。"""
        try:
            raw = yf.download(
                symbols,
                start=start,
                end=end,
                auto_adjust=True,
                progress=True,
                group_by='ticker',
                threads=True,
            )
        except Exception as e:
            print(f'  [DataStore] 下载失败：{e}')
            return 0

        saved = 0
        for sym in symbols:
            df = self._extract(raw, sym, len(symbols))
            if df is None or df.empty:
                continue
            path = self.stocks_dir / f'{sym}.parquet'
            if path.exists():
                old      = pd.read_parquet(path)
                new_rows = df[~df.index.isin(old.index)]
                if new_rows.empty:
                    continue                          # 无新数据，跳过写入
                df = pd.concat([old, df])
                df = df[~df.index.duplicated(keep='last')].sort_index()
            df.index.name = 'date'
            df.to_parquet(path)
            saved += 1
        print(f'  [DataStore] 写入 {saved}/{len(symbols)} 只')
        return saved

    # ── 内部：读取 ───────────────────────────────────────────

    def _load(
        self,
        symbols:  list[str],
        start:    str,
        end:      str,
        min_rows: int,
    ) -> dict[str, pd.DataFrame]:
        result = {}
        ts_start = pd.Timestamp(start)
        ts_end   = pd.Timestamp(end)
        for sym in symbols:
            path = self.stocks_dir / f'{sym}.parquet'
            if not path.exists():
                continue
            df = pd.read_parquet(path)
            df = df[(df.index >= ts_start) & (df.index <= ts_end)]
            if len(df) >= min_rows:
                result[sym] = df
        return result

    # ── 内部：工具函数 ────────────────────────────────────────

    def _date_range(self, symbol: str) -> tuple[date | None, date | None]:
        """返回 (最新日期, 最早日期)，文件不存在时返回 (None, None)。"""
        path = self.stocks_dir / f'{symbol}.parquet'
        if not path.exists():
            return None, None
        # 只读 close 列（parquet 列式存储，比读全部快很多）
        idx = pd.read_parquet(path, columns=['close']).index
        if idx.empty:
            return None, None
        return idx.max().date(), idx.min().date()

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
    parser.add_argument('--universe', default='sp500',
                        help='股票池：sp500 / nasdaq100 / russell2000')
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
