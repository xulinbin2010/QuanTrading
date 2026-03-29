import time
from datetime import datetime, timezone
from ib_insync import Stock


# IBKR 对 bar_size 允许拉取的最大时间跨度
# 文档：https://interactivebrokers.github.io/tws-api/historical_limitations.html
DURATION_MAP = {
    '1 min':   '7 D',
    '5 mins':  '1 M',
    '15 mins': '1 M',
    '30 mins': '1 M',
    '1 hour':  '1 Y',
    '1 day':   '5 Y',
}

# 每次请求之间的间隔（IBKR 限制：10分钟内不超过60次请求）
REQUEST_INTERVAL = 12  # 秒，保守设置


class HistoricalData:
    def __init__(self, ib, db):
        self.ib = ib
        self.db = db

    def _fetch_bars(self, symbol: str, bar_size: str, duration: str, end_dt: str = ''):
        """从 IBKR 拉取一段历史K线"""
        contract = Stock(symbol, 'SMART', 'USD')
        self.ib.qualifyContracts(contract)
        bars = self.ib.reqHistoricalData(
            contract,
            endDateTime=end_dt,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=True,       # 只要正常交易时段
            formatDate=1,
        )
        return bars

    def fetch(self, symbol: str, bar_size: str = '1 day', duration: str = None):
        """
        初始化拉取：拉满 DURATION_MAP 里允许的最大历史。
        已有数据则改为增量更新。
        """
        if bar_size not in DURATION_MAP:
            print(f"不支持的 bar_size: {bar_size}，可选：{list(DURATION_MAP.keys())}")
            return 0

        # 如果本地已有数据，改为增量更新
        latest = self.db.get_latest_dt(symbol, bar_size)
        if latest:
            print(f"{symbol} [{bar_size}] 本地已有数据，执行增量更新（最新：{latest}）")
            return self.update(symbol, bar_size)

        duration = duration or DURATION_MAP[bar_size]
        print(f"初始化拉取 {symbol} [{bar_size}]，时间跨度：{duration} ...")
        bars = self._fetch_bars(symbol, bar_size, duration)
        if not bars:
            print(f"  未拿到数据（周末/非交易日正常）")
            return 0

        saved = self.db.save_klines(symbol, bar_size, bars)
        print(f"  共 {len(bars)} 根K线，新增 {saved} 根存入数据库")
        return saved

    def update(self, symbol: str, bar_size: str = '1 day'):
        """
        增量更新：只拉取本地最新时间之后的数据。
        """
        latest = self.db.get_latest_dt(symbol, bar_size)
        if not latest:
            print(f"{symbol} [{bar_size}] 本地无数据，请先执行 fetch()")
            return 0

        # 计算距今天数，动态设置 duration
        now = datetime.now()
        days_diff = (now - latest).days + 2  # 多拉2天保险
        if days_diff <= 0:
            print(f"{symbol} [{bar_size}] 已是最新，无需更新")
            return 0

        # 根据 bar_size 选合适的 duration 单位
        if bar_size in ('1 min', '5 mins', '15 mins', '30 mins'):
            duration = f"{min(days_diff, 30)} D"
        else:
            duration = f"{min(days_diff, 365)} D"

        print(f"增量更新 {symbol} [{bar_size}]，从 {latest} 至今（{duration}）...")
        bars = self._fetch_bars(symbol, bar_size, duration)
        if not bars:
            print(f"  无新数据")
            return 0

        saved = self.db.save_klines(symbol, bar_size, bars)
        print(f"  共 {len(bars)} 根K线，新增 {saved} 根")
        return saved

    def fetch_all(self, symbols: list, bar_sizes: list = None, interval: int = REQUEST_INTERVAL):
        """
        批量拉取多只股票、多个周期，自动控制请求频率。
        """
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
        """打印最近 N 根K线"""
        rows = self.db.get_klines(symbol, bar_size, limit)
        count = self.db.get_klines_count(symbol, bar_size)
        if not rows:
            print(f"\n{symbol} [{bar_size}] 无本地数据")
            return
        print(f"\n===== {symbol} [{bar_size}]（共 {count} 根，显示最近 {len(rows)} 根）=====")
        print(f"{'时间':<22}{'开盘':>10}{'最高':>10}{'最低':>10}{'收盘':>10}{'成交量':>14}")
        print("-" * 78)
        for r in rows:
            dt = r[0].strftime('%Y-%m-%d %H:%M') if hasattr(r[0], 'strftime') else str(r[0])
            print(f"{dt:<22}{r[1]:>10.2f}{r[2]:>10.2f}{r[3]:>10.2f}{r[4]:>10.2f}{r[5]:>14}")
