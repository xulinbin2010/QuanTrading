"""持仓总览服务层：封装 Database / IB Account 调用"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from core.database import Database

# 懒加载单例 DB
_db: Database | None = None


def get_db() -> Database:
    global _db
    if _db is None:
        _db = Database()
        _db.connect()
    return _db


# ── IB 连接单例（可选，需 IB Gateway 运行）─────────────────

_ib = None


def _connect_ib():
    """尝试连接 IB Gateway，失败返回 None。
    ib_insync 内部用 asyncio，FastAPI worker thread 没有默认 event loop，需手动创建。
    """
    global _ib
    import asyncio
    try:
        # FastAPI 的 AnyIO worker thread 没有 event loop，ib_insync 需要一个
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        import config
        from core.connection import IBConnection
        conn = IBConnection(
            host=config.IB_HOST,
            port=config.IB_PORT,
            client_id=config.IB_CLIENT_ID + 10,  # Web 用不同 client_id，避免与 CLI 冲突
            timeout=config.IB_TIMEOUT,
        )
        _ib = conn.connect()
        return _ib
    except Exception:
        _ib = None
        return None


def get_ib_status() -> dict:
    global _ib
    if _ib is None:
        _connect_ib()
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
    global _ib
    if _ib is None or not _ib.isConnected():
        _connect_ib()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    # 主动请求最新账户数据
    _ib.reqAccountUpdates(True, '')
    _ib.sleep(0.5)
    from core.account import Account
    acc = Account(_ib)

    def val(tag):
        return acc._get_value(tag)

    return {
        "net_liquidation": val("NetLiquidation"),
        "total_cash": val("TotalCashValue"),
        "unrealized_pnl": val("UnrealizedPnL"),
        "realized_pnl": val("RealizedPnL"),
        "buying_power": val("BuyingPower"),
    }


def get_positions() -> list[dict]:
    """返回当前持仓（需 IB Gateway）"""
    global _ib
    if _ib is None or not _ib.isConnected():
        _connect_ib()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    # 主动向 IB 请求最新持仓，而不是读本地缓存
    _ib.reqPositions()
    _ib.sleep(0.5)
    positions = []
    for item in _ib.portfolio():
        avg_cost = float(item.averageCost)
        market_price = float(item.marketPrice)
        unrealized_pnl_pct = (market_price - avg_cost) / avg_cost if avg_cost else 0
        positions.append({
            "symbol": item.contract.symbol,
            "qty": float(item.position),
            "avg_cost": avg_cost,
            "market_price": market_price,
            "market_value": float(item.marketValue),
            "unrealized_pnl": float(item.unrealizedPNL),
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "realized_pnl": float(item.realizedPNL),
        })
    return positions


def get_orders(symbol: str | None = None, limit: int = 50) -> list[dict]:
    db = get_db()
    rows = db.get_orders(symbol=symbol, limit=limit)
    result = []
    for r in rows:
        # id, symbol, action, order_type, qty, price, filled, status, order_id, created_at
        result.append({
            "id": r[0],
            "symbol": r[1],
            "action": r[2],
            "order_type": r[3],
            "quantity": float(r[4]),
            "price": float(r[5]) if r[5] is not None else None,
            "filled_price": float(r[6]) if r[6] is not None else None,
            "status": r[7],
            "order_id": r[8],
            "created_at": r[9].strftime('%Y-%m-%d %H:%M:%S') if r[9] else '',
        })
    return result


def get_account_history(limit: int = 90) -> list[dict]:
    db = get_db()
    rows = db.get_account_history(limit=limit)
    result = []
    for r in rows:
        # snapshot_at, net_liq, cash, unrealized, realized, buying_power
        result.append({
            "snapshot_at": r[0].strftime('%Y-%m-%d %H:%M:%S') if r[0] else '',
            "net_liquidation": float(r[1]) if r[1] is not None else 0,
            "total_cash": float(r[2]) if r[2] is not None else 0,
            "unrealized_pnl": float(r[3]) if r[3] is not None else 0,
            "realized_pnl": float(r[4]) if r[4] is not None else 0,
            "buying_power": float(r[5]) if r[5] is not None else 0,
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
            "symbol": s['symbol'],
            "rs_score": round(float(s.get('rs_score', 0)), 4),
            "close": float(s.get('close', 0)),
            "vol_ratio": round(float(s.get('vol_ratio', 0)), 2),
            "market_cap_b": s.get('market_cap_b'),
            "industry": s.get('industry'),
            "sector": s.get('sector'),
            "insider_score": s.get('insider_score'),
        }
        for s in signals.get('buy', [])
    ]
    sell = [
        {"symbol": s['symbol'], "close": float(s.get('close', 0)), "reason": s.get('reason', '')}
        for s in signals.get('sell', [])
    ]
    return {"buy": buy, "sell": sell, "spy_brake": signals.get('spy_brake', False)}
