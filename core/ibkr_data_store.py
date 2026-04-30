"""
IBKRDataStore — 基于 IBKR TWS API 的 Parquet 本地数据存储。

接口与 DataStore（yfinance）完全一致，可直接用于数据比对。
数据存入 data/stocks_ibkr/{SYMBOL}.parquet（与 yfinance 的 data/stocks/ 平行）。

用法：
  from core.ibkr_data_store import IBKRDataStore
  store = IBKRDataStore()
  store.connect()                           # 连接 IB Gateway
  data = store.get(['AAPL', 'NVDA'], start='2024-01-01')
  store.disconnect()

离线读取已缓存的 Parquet（不需要 IB 连接）：
  store = IBKRDataStore()
  data = store.get(['AAPL'], start='2024-01-01', auto_update=False)

注意：
  - IBKR 限速 60 请求/10 分钟，REQUEST_INTERVAL = 12 秒
  - 全量 S&P500（~500 只）拉取约需 100 分钟，建议作为隔夜批处理任务
  - useRTH=True，只拉正常交易时段 K 线（与 yfinance 默认行为一致）
"""
from __future__ import annotations

import time
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

REQUEST_INTERVAL = 12   # 秒（IBKR：60 次 / 10 分钟）
BAR_SIZE = '1 day'
DURATION = '3 Y'        # 初次拉取最近 3 年日线（与 yfinance 回测窗口对齐）


class IBKRDataStore:
    """
    IBKR 日线数据 Parquet 存储，接口与 DataStore 一致。

    构造参数：
      data_dir : 项目根目录（Parquet 存入 data_dir/stocks_ibkr/）
    """

    def __init__(self, data_dir: str | Path = 'data'):
        self.root = Path(data_dir)
        self.stocks_dir = self.root / 'stocks_ibkr'
        self.stocks_dir.mkdir(parents=True, exist_ok=True)
        self._ib = None

    # ── 连接管理 ──────────────────────────────────────────────

    def connect(self, ib=None, host: str = '127.0.0.1', port: int = 4002, client_id: int = 10):
        """
        连接 IB Gateway。

        可传入已有的 ib_insync.IB 实例，也可由内部自动新建连接。
        client_id 默认 10（避免与主程序的 client_id=1 冲突）。
        """
        if ib is not None:
            self._ib = ib
            return

        try:
            from ib_insync import IB
        except ImportError:
            raise RuntimeError("ib_insync 未安装，请执行：pip install ib_insync")

        self._ib = IB()
        self._ib.connect(host, port, clientId=client_id, timeout=30)
        print(f"  [IBKRDataStore] 已连接 {host}:{port} (clientId={client_id})")

    def disconnect(self):
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            print("  [IBKRDataStore] 已断开连接")

    # ── 公共接口（与 DataStore.get 签名一致）─────────────────

    def get(
        self,
        symbols: list[str],
        start: str,
        end: str | None = None,
        min_rows: int = 40,
        auto_update: bool = True,
    ) -> dict[str, pd.DataFrame]:
        """
        返回 {symbol: DataFrame} 字典，DataFrame 列：open/high/low/close/volume，DatetimeIndex。

        auto_update=True 且已连接时，自动拉取缺失或过期数据。
        auto_update=False 或未连接时，直接读 Parquet 缓存（离线模式）。
        """
        if auto_update and self._ib is not None and self._ib.isConnected():
            self.update(symbols, start, end)

        result: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            df = self._load(sym)
            if df is None or len(df) < min_rows:
                continue
            # 按日期过滤
            if start:
                df = df[df.index >= pd.Timestamp(start)]
            if end:
                df = df[df.index <= pd.Timestamp(end)]
            if len(df) >= min_rows:
                result[sym] = df
        return result

    def update(
        self,
        symbols: list[str],
        start: str | None = None,
        end: str | None = None,
    ) -> None:
        """
        批量更新 IBKR K 线数据（增量：只拉取本地最新日期之后的数据）。
        每只之间自动 sleep REQUEST_INTERVAL 秒，遵守 IBKR 限速。
        """
        if self._ib is None or not self._ib.isConnected():
            warnings.warn("[IBKRDataStore] 未连接 IB Gateway，跳过更新")
            return

        for i, sym in enumerate(symbols):
            try:
                self._fetch_and_save(sym)
            except Exception as e:
                print(f"  [IBKRDataStore] {sym} 拉取失败：{e}")
            if i < len(symbols) - 1:
                time.sleep(REQUEST_INTERVAL)

    # ── 私有方法 ──────────────────────────────────────────────

    def _parquet_path(self, symbol: str) -> Path:
        return self.stocks_dir / f"{symbol.upper()}.parquet"

    def _load(self, symbol: str) -> pd.DataFrame | None:
        """从 Parquet 读取，返回 DataFrame 或 None。"""
        p = self._parquet_path(symbol)
        if not p.exists():
            return None
        try:
            df = pd.read_parquet(p)
            df.index = pd.to_datetime(df.index)
            df.index.name = 'date'
            return df.sort_index()
        except Exception:
            return None

    def _save(self, symbol: str, df: pd.DataFrame) -> None:
        """保存到 Parquet，与已有数据合并（去重保留最新）。"""
        p = self._parquet_path(symbol)
        existing = self._load(symbol)
        if existing is not None:
            df = pd.concat([existing, df])
            df = df[~df.index.duplicated(keep='last')]
        df = df.sort_index()
        df.to_parquet(p)

    def _fetch_and_save(self, symbol: str) -> int:
        """
        拉取单只股票的日线数据并保存。
        增量逻辑：已有数据则只拉最新日期之后的部分。

        返回新增 K 线数量。
        """
        from ib_insync import Stock

        existing = self._load(symbol)
        if existing is not None and len(existing) > 0:
            last_date = existing.index[-1].date()
            today = date.today()
            days_diff = (today - last_date).days + 2
            if days_diff <= 1:
                return 0  # 已是最新
            duration = f"{min(days_diff, 365)} D"
        else:
            duration = DURATION

        contract = Stock(symbol, 'SMART', 'USD')
        self._ib.qualifyContracts(contract)
        bars = self._ib.reqHistoricalData(
            contract,
            endDateTime='',
            durationStr=duration,
            barSizeSetting=BAR_SIZE,
            whatToShow='TRADES',
            useRTH=True,
            formatDate=1,
        )

        if not bars:
            return 0

        df = self._bars_to_df(bars)
        self._save(symbol, df)
        return len(df)

    @staticmethod
    def _bars_to_df(bars) -> pd.DataFrame:
        """将 ib_insync BarData 列表转为标准 DataFrame（与 DataStore 格式一致）。"""
        records = []
        for b in bars:
            # b.date 可能是 datetime 或 date 字符串
            dt = b.date
            if isinstance(dt, str):
                dt = datetime.strptime(dt, '%Y%m%d').date()
            elif hasattr(dt, 'date'):
                dt = dt.date()
            records.append({
                'date':   dt,
                'open':   float(b.open),
                'high':   float(b.high),
                'low':    float(b.low),
                'close':  float(b.close),
                'volume': float(b.volume),
            })

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        return df

    def fetch_df(self, symbol: str) -> 'pd.DataFrame | None':
        """
        从 IBKR 拉取日线数据，返回 DataFrame，不写入本地文件。
        供 DataStore 在 yfinance 复权跳变时用作修复数据源。

        优先 ADJUSTED_LAST（分红+拆股调整，与 yfinance auto_adjust 等价），
        不支持时退回 TRADES（仅拆股调整，无分红调整）。
        """
        if self._ib is None or not self._ib.isConnected():
            return None
        try:
            from ib_insync import Stock
            contract = Stock(symbol, 'SMART', 'USD')
            self._ib.qualifyContracts(contract)
            for what, duration in [('ADJUSTED_LAST', '3 Y'), ('TRADES', '5 Y')]:
                try:
                    bars = self._ib.reqHistoricalData(
                        contract,
                        endDateTime='',
                        durationStr=duration,
                        barSizeSetting=BAR_SIZE,
                        whatToShow=what,
                        useRTH=True,
                        formatDate=1,
                    )
                    if bars:
                        df = self._bars_to_df(bars)
                        if not df.empty:
                            print(f'  [IBKRDataStore] {symbol} {what} {len(df)} 行')
                            return df
                except Exception:
                    continue
        except Exception as e:
            print(f'  [IBKRDataStore] {symbol} fetch_df 失败：{e}')
        return None

    # ── CLI 入口 ──────────────────────────────────────────────

    @classmethod
    def cli_update(cls, symbols: list[str], host: str = '127.0.0.1', port: int = 4002):
        """命令行批量更新入口。"""
        store = cls()
        store.connect(host=host, port=port)
        try:
            store.update(symbols)
            print(f"\n[IBKRDataStore] 更新完成，共 {len(symbols)} 只")
        finally:
            store.disconnect()


if __name__ == '__main__':
    import argparse
    from core.universe import get_tickers

    parser = argparse.ArgumentParser(description='IBKR 历史数据更新工具')
    parser.add_argument('--symbols', nargs='+', help='股票代码列表')
    parser.add_argument('--universe', default='sp500', help='股票池（sp500/nasdaq100）')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=4002)
    args = parser.parse_args()

    syms = args.symbols if args.symbols else get_tickers(args.universe)
    IBKRDataStore.cli_update(syms, host=args.host, port=args.port)
