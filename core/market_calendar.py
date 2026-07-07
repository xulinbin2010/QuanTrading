"""美股（NYSE/Nasdaq）交易日历：整日休市判断。

背景（2026-07-03 事故）：独立日补休当天代码按「周五盘中」下了 MKT/DAY 卖单，
IB 把休市日的 DAY 单顺延到下一交易日（7/6 周一）开盘成交，绕过了当天用户
「保留持仓」的人工决策。任何真实下单前的时段判断必须先过 is_us_trading_day()。

维护说明：
  - 只维护整日休市；提前收盘日（感恩节次日 / 平安夜等 13:00 收盘）不在此列，
    对本系统影响仅限盘中单收不到成交，无顺延风险
  - 日历表覆盖至 _COVERAGE_END；超出范围按普通工作日放行（fail-open）并打
    WARNING 日志提醒补表，避免未来某年系统整体罢工
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from core.logger import get_logger

logger = get_logger('market_calendar')

# NYSE 整日休市日（元旦 / MLK / 总统日 / 耶稣受难日 / 阵亡将士 / 六月节 /
# 独立日 / 劳动节 / 感恩节 / 圣诞，含周末补休规则：周六→前一周五，周日→后一周一）
US_MARKET_HOLIDAYS = {
    # 2025
    date(2025, 1, 1),  date(2025, 1, 20),  date(2025, 2, 17),
    date(2025, 4, 18), date(2025, 5, 26),  date(2025, 6, 19),
    date(2025, 7, 4),  date(2025, 9, 1),   date(2025, 11, 27),
    date(2025, 12, 25),
    # 2026
    date(2026, 1, 1),  date(2026, 1, 19),  date(2026, 2, 16),
    date(2026, 4, 3),  date(2026, 5, 25),  date(2026, 6, 19),
    date(2026, 7, 3),   # 独立日补休（7/4 为周六）← 本次事故触发日
    date(2026, 9, 7),  date(2026, 11, 26), date(2026, 12, 25),
    # 2027
    date(2027, 1, 1),  date(2027, 1, 18),  date(2027, 2, 15),
    date(2027, 3, 26), date(2027, 5, 31),  date(2027, 6, 18),
    date(2027, 7, 5),   # 独立日补休（7/4 为周日）
    date(2027, 9, 6),  date(2027, 11, 25), date(2027, 12, 24),
}

_COVERAGE_END = date(2027, 12, 31)
_warned_out_of_range = False

_ET = ZoneInfo('America/New_York')


def is_us_trading_day(d: date | None = None) -> bool:
    """判断 d（默认＝美东今天）是否为美股交易日。周末与 US_MARKET_HOLIDAYS 为 False。"""
    global _warned_out_of_range
    if d is None:
        d = datetime.now(_ET).date()
    if d.weekday() >= 5:
        return False
    if d > _COVERAGE_END:
        if not _warned_out_of_range:
            logger.warning(f'{d} 超出交易日历维护范围（至 {_COVERAGE_END}），'
                           f'按普通工作日放行 — 请更新 core/market_calendar.py 假日表')
            _warned_out_of_range = True
        return True
    return d not in US_MARKET_HOLIDAYS
