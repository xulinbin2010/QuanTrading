"""
HistoricalData — IBKR 历史K线拉取工具。

存储层已改为 IBKRDataStore（Parquet），不再依赖 MySQL。
旧的 `db` 参数保留但忽略，保持向后兼容的构造签名。
"""
from __future__ import annotations
import time
from core.ibkr_data_store import IBKRDataStore

# IBKR 对 bar_size 允许拉取的最大时间跨度
DURATION_MAP = {
    '1 min':   '7 D',
    '5 mins':  '1 M',
    '15 mins': '1 M',
    '30 mins': '1 M',
    '1 hour':  '1 Y',
    '1 day':   '5 Y',
}

REQUEST_INTERVAL = 12  # 秒，保守设置（IBKR 限制：10分钟内不超过60次请求）


class HistoricalData:
    def __init__(self, ib, db=None):
        """
        ib : ib_insync.IB 实例（已连接）
        db : 已废弃，保留参数只为向后兼容，不再使用
        """
        self._store = IBKRDataStore()
        self._store.connect(ib=ib)

    def fetch(self, symbol: str, bar_size: str = '1 day', duration: str = None):
        """初始化或增量拉取单只股票K线，存入 Parquet。"""
        if bar_size not in DURATION_MAP:
            print(f"不支持的 bar_size: {bar_size}，可选：{list(DURATION_MAP.keys())}")
            return 0
        # IBKRDataStore._fetch_and_save 内置增量逻辑
        saved = self._store._fetch_and_save(symbol)
        if saved:
            print(f"  {symbol} [{bar_size}] 新增 {saved} 根K线")
        else:
            print(f"  {symbol} [{bar_size}] 已是最新，无需更新")
        return saved

    def update(self, symbol: str, bar_size: str = '1 day'):
        """增量更新单只股票（已有数据则只拉最新日期之后的部分）。"""
        return self.fetch(symbol, bar_size)

    def fetch_all(self, symbols: list, bar_sizes: list = None, interval: int = REQUEST_INTERVAL):
        """批量拉取多只股票，自动控制请求频率。"""
        if bar_sizes is None:
            bar_sizes = ['1 day']
        total = len(symbols) * len(bar_sizes)
        done = 0
        for symbol in symbols:
            for bar_size in bar_sizes:
                done += 1
                print(f"\n[{done}/{total}] ", end='')
                self.fetch(symbol, bar_size)
                if done < total:
                    time.sleep(interval)
        print(f"\n批量拉取完成，共处理 {total} 个任务")

    def print_klines(self, symbol: str, bar_size: str = '1 day', limit: int = 10):
        """打印最近 N 根K线（从 Parquet 读取）。"""
        import pandas as pd
        df = self._store._load(symbol)
        if df is None or df.empty:
            print(f"\n{symbol} [{bar_size}] 无本地数据")
            return
        rows = df.tail(limit)
        print(f"\n===== {symbol} [{bar_size}]（共 {len(df)} 根，显示最近 {len(rows)} 根）=====")
        print(f"{'日期':<14}{'开盘':>10}{'最高':>10}{'最低':>10}{'收盘':>10}{'成交量':>14}")
        print("-" * 68)
        for dt, row in rows.iterrows():
            print(f"{str(dt)[:10]:<14}{row['open']:>10.2f}{row['high']:>10.2f}"
                  f"{row['low']:>10.2f}{row['close']:>10.2f}{row['volume']:>14.0f}")
