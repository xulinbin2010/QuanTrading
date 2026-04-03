"""持仓总览服务层：封装 Database / IB Account 调用"""
from __future__ import annotations
import sys
import os
import time
import threading
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from core.database import Database

# ── DB：每个线程独立连接，避免 pymysql 多线程 packet 冲突 ──────
_db_local = threading.local()


def get_db() -> Database:
    db = getattr(_db_local, 'db', None)
    if db is None:
        db = Database()
        db.connect()
        _db_local.db = db
    return db


# ── IB 连接单例（可选，需 IB Gateway 运行）─────────────────────
# 全局只建一个 IBConnection；失败后加冷却，防止无限轰炸 IB Gateway。

_conn = None        # IBConnection 对象
_ib   = None        # _conn.ib 的引用
_ib_lock = threading.Lock()
_last_attempt: float = 0.0
_COOLDOWN = 30.0    # 两次连接尝试之间最少间隔（秒）


def _ensure_connected():
    """首次（或冷却后）建立 IB 连接；之后依赖 IBConnection 自动重连。"""
    global _conn, _ib, _last_attempt
    import asyncio
    with _ib_lock:
        # 已有连接管理器 → 由 IBConnection._on_disconnected 负责重连，不干预
        if _conn is not None:
            return
        # 冷却期内不重试，避免短时间内反复轰炸 IB Gateway
        if time.monotonic() - _last_attempt < _COOLDOWN:
            return
        _last_attempt = time.monotonic()
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            import config
            from core.connection import IBConnection
            _conn = IBConnection(
                host=config.IB_HOST,
                port=config.IB_PORT,
                client_id=config.IB_CLIENT_ID + 10,  # Web 用不同 client_id
                timeout=config.IB_TIMEOUT,
            )
            _ib = _conn.connect()
            # ib_insync 单账户时连接后自动订阅账户数据，无需手动 reqAccountUpdates。
            # 绝不能在非 ib_insync event loop 线程调 _ib.sleep() / reqAccountUpdates，
            # 否则会破坏后台 loop 导致立刻断线并触发 Error 326 死循环。
            time.sleep(1.5)  # 等 ib_insync 后台 loop 把初始数据跑完
        except Exception:
            _conn = None
            _ib = None


def get_ib_status() -> dict:
    _ensure_connected()
    connected = bool(_ib and _ib.isConnected())
    account = None
    if connected:
        try:
            account = _ib.managedAccounts()[0] if _ib.managedAccounts() else None
        except Exception:
            pass
    return {"connected": connected, "account": account}


def get_balance() -> dict:
    """返回账户余额（需 IB Gateway），失败抛 RuntimeError"""
    _ensure_connected()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    from core.account import Account
    acc = Account(_ib)
    return {
        "net_liquidation": acc._get_value("NetLiquidation"),
        "total_cash":      acc._get_value("TotalCashValue"),
        "unrealized_pnl":  acc._get_value("UnrealizedPnL"),
        "realized_pnl":    acc._get_value("RealizedPnL"),
        "buying_power":    acc._get_value("BuyingPower"),
    }


def _get_entry_date(symbol: str, db) -> str | None:
    """从 orders 表查该持仓最近一笔已成交 BUY 单的日期（只取日期部分）"""
    try:
        rows = db.get_orders(symbol=symbol, limit=50)
        for r in rows:
            if r[2] == 'BUY' and r[6] is not None:   # action=BUY, filled_price 有值
                dt = r[9]
                if dt is None:
                    continue
                if hasattr(dt, 'date'):
                    return dt.date().strftime('%Y-%m-%d')
                return str(dt)[:10]
    except Exception:
        pass
    return None


def _reset_connection():
    """断开并清除 IB 连接单例，供强制刷新时使用。"""
    global _conn, _ib, _last_attempt
    with _ib_lock:
        if _conn is not None:
            try:
                _conn.disconnect()
            except Exception:
                pass
        _conn = None
        _ib = None
        _last_attempt = 0.0  # 清除冷却计时，允许立即重连


def get_positions(force_refresh: bool = False) -> list[dict]:
    """返回当前持仓（需 IB Gateway）。force_refresh=True 时先断线重连以获取最新数据。"""
    if force_refresh:
        _reset_connection()
    _ensure_connected()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    db = get_db()
    positions = []
    for item in _ib.portfolio():
        avg_cost = float(item.averageCost)
        market_price = float(item.marketPrice)
        unrealized_pnl_pct = (market_price - avg_cost) / avg_cost if avg_cost else 0
        symbol = item.contract.symbol
        positions.append({
            "symbol":             symbol,
            "qty":                float(item.position),
            "avg_cost":           avg_cost,
            "market_price":       market_price,
            "market_value":       float(item.marketValue),
            "unrealized_pnl":     float(item.unrealizedPNL),
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "realized_pnl":       float(item.realizedPNL),
            "entry_date":         _get_entry_date(symbol, db),
        })
    return positions


def _fmt_dt(v) -> str:
    """将 datetime / int(unix ts) / None 统一转成字符串"""
    if not v:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v).strftime('%Y-%m-%d %H:%M:%S')
    return str(v)


def get_orders(symbol: str | None = None, limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.get_orders(symbol=symbol, limit=limit)
    result = []
    for r in rows:
        # id, symbol, action, order_type, qty, price, filled, status, order_id, created_at
        result.append({
            "id":           r[0],
            "symbol":       r[1],
            "action":       r[2],
            "order_type":   r[3],
            "quantity":     float(r[4]),
            "price":        float(r[5]) if r[5] is not None else None,
            "filled_price": float(r[6]) if r[6] is not None else None,
            "status":       r[7],
            "order_id":     r[8],
            "created_at":   _fmt_dt(r[9]),
        })
    return result


def get_account_history(limit: int = 90) -> list[dict]:
    db = get_db()
    rows = db.get_account_history(limit=limit)
    result = []
    for r in rows:
        # snapshot_at, net_liq, cash, unrealized, realized, buying_power
        result.append({
            "snapshot_at":      _fmt_dt(r[0]),
            "net_liquidation":  float(r[1]) if r[1] is not None else 0,
            "total_cash":       float(r[2]) if r[2] is not None else 0,
            "unrealized_pnl":   float(r[3]) if r[3] is not None else 0,
            "realized_pnl":     float(r[4]) if r[4] is not None else 0,
            "buying_power":     float(r[5]) if r[5] is not None else 0,
        })
    return list(reversed(result))  # 升序，方便图表


def get_signals(universe: str = 'sp500') -> dict:
    """调用 scan_signals() dry-run，不需要 IB"""
    from auto_trader import scan_signals
    signals = scan_signals(
        held_symbols=[],
        universe=universe,
    )
    buy = [
        {
            "symbol":       s['symbol'],
            "rs_score":     round(float(s.get('rs_score', 0)), 4),
            "close":        float(s.get('close', 0)),
            "vol_ratio":    round(float(s.get('vol_ratio', 0)), 2),
            "market_cap_b": s.get('market_cap_b'),
            "industry":     s.get('industry'),
            "sector":       s.get('sector'),
            "insider_score": s.get('insider_score'),
        }
        for s in signals.get('buy', [])
    ]
    sell = [
        {"symbol": s['symbol'], "close": float(s.get('close', 0)), "reason": s.get('reason', '')}
        for s in signals.get('sell', [])
    ]
    return {"buy": buy, "sell": sell, "spy_brake": signals.get('spy_brake', False)}
