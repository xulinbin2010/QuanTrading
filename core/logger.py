"""
全局日志模块。

用法：
    from core.logger import get_logger
    logger = get_logger(__name__)
    logger.info("买入 NVDA 100 股 @ $184.11")
    logger.warning("止损触发：AAPL 浮亏 -15.3%")
    logger.error("下单失败：TSLA 连接超时")

日志文件：logs/trading.log（每天午夜自动切割，保留 30 天）
格式：2026-03-23 09:01:23 | INFO  | auto_trader | 买入 NVDA ...

扩展：以后要加 Telegram / 企业微信，只需在此文件 addHandler，调用方不用改。
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

_loggers: dict[str, logging.Logger] = {}

LOG_DIR  = os.path.join(os.path.dirname(__file__), '..', 'logs')
LOG_FILE = os.path.join(LOG_DIR, 'trading.log')
FMT      = '%(asctime)s | %(levelname)-5s | %(name)s | %(message)s'
DATE_FMT = '%Y-%m-%d %H:%M:%S'


def get_logger(name: str) -> logging.Logger:
    """返回具名 logger，同名多次调用返回同一实例。"""
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False          # 不向 root logger 传播，避免重复输出

    formatter = logging.Formatter(FMT, datefmt=DATE_FMT)

    # ── 控制台（INFO 及以上）────────────────────────────────
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # ── 文件（DEBUG 及以上，每天切割，保留 30 天）──────────
    os.makedirs(LOG_DIR, exist_ok=True)
    fh = TimedRotatingFileHandler(
        LOG_FILE, when='midnight', backupCount=30, encoding='utf-8'
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    _loggers[name] = logger
    return logger
