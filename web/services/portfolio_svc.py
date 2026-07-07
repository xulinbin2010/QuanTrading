"""持仓总览服务层：封装 Database / IB Account 调用"""
from __future__ import annotations
import sys
import os
import time
import threading
from datetime import datetime, date
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

import concurrent.futures as _cf

_conn = None        # IBConnection 对象
_ib   = None        # _conn.ib 的引用
_ib_lock = threading.Lock()
_last_attempt: float = 0.0
_COOLDOWN = 30.0    # 两次连接尝试之间最少间隔（秒）

# ib_insync 同步方法（qualifyContracts / reqTickers / reqHistoricalData 等）内部
# 调用 asyncio.get_event_loop()；AnyIO worker thread 没有 event loop 会报错。
# 解决方案：连接时保存 event loop，并建一个单线程 executor，其 worker 线程绑定
# 同一 loop，所有 ib_insync 同步调用都通过 _run_ib_sync() 转发到这个线程执行。
_ib_event_loop = None
_ib_executor: _cf.ThreadPoolExecutor | None = None


def _run_ib_sync(fn, *args, timeout: float = 30, **kwargs):
    """从任意线程安全地调用 ib_insync 同步方法（解决 AnyIO worker thread 无 event loop 问题）。"""
    global _ib_executor
    if _ib_executor is None:
        raise RuntimeError("IB not connected")
    return _ib_executor.submit(fn, *args, **kwargs).result(timeout=timeout)


def _ensure_connected():
    """首次（或冷却后）建立 IB 连接；之后依赖 IBConnection 自动重连。"""
    global _conn, _ib, _last_attempt, _ib_event_loop, _ib_executor
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
            # Web 专用固定 client_id（默认 IB_CLIENT_ID+10=11，可用 .env IB_WEB_CLIENT_ID 覆盖）。
            # 必须与 Gateway「Configuration→API→Master API client ID」一致 → Web 成为主控
            # client，才能撤销/管理任意 client（含 auto_trader clientId=1）下的订单。固定 id 是
            # master 的前提；万一旧连接未释放报 Error 326，由 IBConnection 重试兜底。
            cid = getattr(config, 'IB_WEB_CLIENT_ID', config.IB_CLIENT_ID + 10)
            _conn = IBConnection(
                host=config.IB_HOST,
                port=config.IB_PORT,
                client_id=cid,
                timeout=config.IB_TIMEOUT,
            )
            _ib = _conn.connect()
            # ib_insync 单账户时连接后自动订阅账户数据，无需手动 reqAccountUpdates。
            # 绝不能在非 ib_insync event loop 线程调 _ib.sleep() / reqAccountUpdates，
            # 否则会破坏后台 loop 导致立刻断线并触发 Error 326 死循环。
            time.sleep(3.0)  # 等 ib_insync 后台 loop 把账户数据（accountValues）推送完

            # 保存 ib_insync 使用的 event loop，建专用 executor 供后续跨线程调用
            _ib_event_loop = loop
            def _worker_init():
                asyncio.set_event_loop(_ib_event_loop)
            _ib_executor = _cf.ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix='ib_api',
                initializer=_worker_init,
            )
        except Exception as e:
            _conn = None
            _ib = None
            # 把连接失败原因存起来，供诊断接口展示
            _last_connect_error = str(e)
            import sys
            setattr(sys.modules[__name__], '_last_connect_error', _last_connect_error)


def get_ib_status() -> dict:
    _ensure_connected()
    connected = bool(_ib and _ib.isConnected())
    accounts: list[str] = []
    if connected:
        try:
            accounts = list(_ib.managedAccounts())
        except Exception:
            pass
    import config
    return {
        "connected": connected,
        "accounts": accounts,
        "account": accounts[0] if accounts else None,   # 向后兼容
        "port": config.IB_PORT,
        "is_live": config.IB_PORT == 4001,
    }


_last_snapshot_date: date | None = None
_snapshot_lock = threading.Lock()


def _auto_save_snapshot(balance: dict) -> None:
    """每天首次成功获取 balance 时，自动写一条账户快照（用于复盘）。"""
    global _last_snapshot_date
    today = date.today()
    with _snapshot_lock:
        if _last_snapshot_date == today:
            return
        try:
            db = get_db()
            db.save_account_snapshot(
                net_liq       = balance['net_liquidation'],
                total_cash    = balance['total_cash'],
                unrealized_pnl= balance['unrealized_pnl'],
                realized_pnl  = balance['realized_pnl'],
                buying_power  = balance['buying_power'],
            )
            _last_snapshot_date = today
            # 清除复盘缓存，使新数据立即生效
            try:
                from web.services import performance_svc
                performance_svc.invalidate_cache()
            except Exception:
                pass
        except Exception:
            pass


def _fetch_balance_from_ib() -> dict:
    """从已连接的 _ib 对象读取账户余额，不做连接检查。"""
    from core.account import Account
    acc = Account(_ib)
    balance = {
        "net_liquidation": acc._get_value("NetLiquidation"),
        "total_cash":      acc._get_value("TotalCashValue"),
        "unrealized_pnl":  acc._get_value("UnrealizedPnL"),
        "realized_pnl":    acc._get_value("RealizedPnL"),
        "buying_power":    acc._get_value("BuyingPower"),
    }
    # accountValues() 首次连接可能还未推送完，遇到全 0 视为数据未就绪
    if balance["net_liquidation"] == 0.0 and balance["total_cash"] == 0.0:
        raise RuntimeError("IB 账户数据尚未就绪，请稍后重试")
    return balance


def get_balance() -> dict:
    """返回账户余额（需 IB Gateway），失败抛 RuntimeError"""
    _ensure_connected()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    balance = _fetch_balance_from_ib()
    _auto_save_snapshot(balance)
    return balance


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
    """清除 IB 连接单例，允许下次请求用最新参数重新建立连接。

    注意：ib_insync 事件循环运行在后台线程，直接从 FastAPI 线程调用
    ib.disconnect() 可能跨线程失效，导致旧 client_id 占用（Error 326）。
    此处只阻止旧对象自动重连、然后直接丢弃引用，让 GC + socket 超时自然回收。
    client_id 固定（master 需固定 id），重连仍用同一 id，Error 326 由 IBConnection 重试兜底。
    """
    global _conn, _ib, _last_attempt, _ib_event_loop, _ib_executor
    with _ib_lock:
        if _conn is not None:
            try:
                _conn._should_reconnect = False   # 阻止旧连接触发自动重连
                _conn.ib.disconnectedEvent.clear() # 摘除所有断线回调
            except Exception:
                pass
            try:
                _conn.ib.disconnect()             # 尽力断开（可能失败，无妨）
            except Exception:
                pass
        if _ib_executor is not None:
            try:
                _ib_executor.shutdown(wait=False)
            except Exception:
                pass
        _conn = None
        _ib = None
        _ib_event_loop = None
        _ib_executor = None
        # 固定 master client_id，不再换号（master 需固定 id）；Error 326 由 IBConnection 重试兜底
        _last_attempt = 0.0


def _format_symbol(contract) -> str:
    """将 IB contract 转成可读标识：股票直接用 symbol，期权拼成 GOOGL C260 261218 格式"""
    sec_type = getattr(contract, 'secType', 'STK')
    if sec_type != 'OPT':
        return contract.symbol
    right  = getattr(contract, 'right', '')
    strike = getattr(contract, 'strike', '')
    expiry = getattr(contract, 'lastTradeDateOrContractMonth', '')
    # 20261218 → 261218
    expiry_short = expiry[2:] if len(expiry) == 8 else expiry
    # 去掉多余的 .0（strike=260.0 → 260）
    strike_str = str(int(strike)) if isinstance(strike, float) and strike == int(strike) else str(strike)
    return f"{contract.symbol} {right}{strike_str} {expiry_short}"


def get_positions(force_refresh: bool = False, account: str | None = None) -> list[dict]:
    """返回当前持仓（需 IB Gateway）。force_refresh=True 时先断线重连以获取最新数据。"""
    if force_refresh:
        _reset_connection()
    _ensure_connected()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    db = get_db()
    positions = []

    # 优先用 portfolio()（含实时市值/盈亏）；
    # 多账户 FA 结构下 portfolio() 可能为空，改用 positions()
    portfolio_items = list(_run_ib_sync(_ib.portfolio))
    if portfolio_items:
        for item in portfolio_items:
            if float(item.position) == 0:
                continue
            contract   = item.contract
            sec_type   = getattr(contract, 'secType', 'STK')
            multiplier = float(getattr(contract, 'multiplier', 1) or 1)
            qty        = float(item.position)

            # IB averageCost：股票=每股，期权=每合约（含乘数），统一转成"每份单价"显示
            avg_cost_raw   = float(item.averageCost)
            avg_cost       = avg_cost_raw / multiplier if sec_type == 'OPT' else avg_cost_raw
            market_price   = float(item.marketPrice)   # 期权/股票都是每份单价
            market_value   = float(item.marketValue)   # IB 已算好总市值
            unrealized_pnl = float(item.unrealizedPNL) # IB 已算好盈亏
            # P&L% = 盈亏 / 成本绝对值（正确处理空头）
            cost_basis_abs = abs(qty) * avg_cost_raw   # abs 处理空头
            unrealized_pnl_pct = unrealized_pnl / cost_basis_abs if cost_basis_abs else 0

            symbol = _format_symbol(contract)
            positions.append({
                "symbol":             symbol,
                "qty":                qty,
                "avg_cost":           avg_cost,
                "market_price":       market_price,
                "market_value":       market_value,
                "unrealized_pnl":     unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "realized_pnl":       float(item.realizedPNL),
                "entry_date":         _get_entry_date(contract.symbol, db),
            })
    else:
        # 多账户 FA 回退：positions() 跨所有子账户，无实时市值需自行计算
        raw_list = [p for p in _run_ib_sync(_ib.positions) if float(p.position) != 0
                    and (account is None or p.account == account)]
        if raw_list:
            # ── 通过 IB API 获取实时/昨收价 ─────────────────────────
            # 股票：reqTickers snapshot 在 FA 账户下可能无行情权限 → 改用 reqHistoricalData 取昨收
            # 期权：reqTickers 的 close 字段可靠，直接使用
            import math
            ib_price: dict[int, float] = {}   # conId → 价格

            all_contracts = [p.contract for p in raw_list]
            try:
                qualified_all = _run_ib_sync(_ib.qualifyContracts, *all_contracts)
            except Exception:
                qualified_all = all_contracts

            # 股票和期权统一用 reqHistoricalData 取昨收（不依赖行情订阅权限，最可靠）
            # 期权依次尝试：TRADES → MIDPOINT → BID_ASK 中间价
            for contract in qualified_all:
                sec_type = getattr(contract, 'secType', 'STK')
                show_modes = ['TRADES', 'MIDPOINT', 'BID_ASK'] if sec_type == 'OPT' else ['TRADES']
                for what in show_modes:
                    try:
                        bars = _run_ib_sync(
                            _ib.reqHistoricalData,
                            contract,
                            endDateTime='',
                            durationStr='5 D',
                            barSizeSetting='1 day',
                            whatToShow=what,
                            useRTH=True,
                            formatDate=1,
                        )
                        if bars:
                            raw = bars[-1].close
                            import math as _m
                            if raw is not None and not _m.isnan(float(raw)) and float(raw) > 0:
                                px = float(raw)
                                ib_price[contract.conId] = px
                                if sec_type == 'STK':
                                    ib_price[contract.symbol] = px
                                break  # 拿到有效价格才跳出
                    except Exception:
                        continue

            # ── 逐仓构建 ─────────────────────────────────────────────
            for pos in raw_list:
                contract   = pos.contract
                sec_type   = getattr(contract, 'secType', 'STK')
                underlying = contract.symbol
                qty        = float(pos.position)
                multiplier = float(getattr(contract, 'multiplier', 1) or 1)

                # avgCost：股票=每股，期权=每合约（含乘数）→ 统一转每份单价
                avg_cost_raw = float(pos.avgCost)
                avg_cost     = avg_cost_raw / multiplier if sec_type == 'OPT' else avg_cost_raw

                # 现价：优先用 conId 精确匹配（期权不同行权价同 symbol），兜底 avg_cost
                market_price = ib_price.get(contract.conId, 0.0)
                if market_price <= 0:
                    market_price = ib_price.get(underlying, 0.0)
                if market_price <= 0:
                    market_price = avg_cost  # 无行情时占位

                # 市值 & 盈亏（统一公式，空头 qty 为负自动处理符号）
                market_value   = qty * market_price * multiplier
                cost_basis     = qty * avg_cost_raw          # qty×每合约成本
                unrealized_pnl = market_value - cost_basis
                cost_basis_abs = abs(qty) * avg_cost_raw
                unrealized_pnl_pct = unrealized_pnl / cost_basis_abs if cost_basis_abs else 0

                display_symbol = _format_symbol(contract)
                positions.append({
                    "symbol":             display_symbol,
                    "qty":                qty,
                    "avg_cost":           avg_cost,
                    "market_price":       market_price,
                    "market_value":       market_value,
                    "unrealized_pnl":     unrealized_pnl,
                    "unrealized_pnl_pct": unrealized_pnl_pct,
                    "realized_pnl":       0.0,
                    "entry_date":         _get_entry_date(underlying, db),
                })

    # IB 已连接时，顺手尝试保存今日快照（防止 balance API 因时序问题未能触发保存）
    if _last_snapshot_date != date.today():
        try:
            balance = _fetch_balance_from_ib()
            _auto_save_snapshot(balance)
        except Exception:
            pass  # 数据未就绪时静默忽略，下次重试

    # 批量补充行业信息（7 天缓存，速度快）
    # 期权以底层股票 symbol 查询，股票直接用自身 symbol
    if positions:
        from core.universe import get_stock_info
        underlying_syms = []
        for p in positions:
            parts = p['symbol'].split()
            underlying_syms.append(parts[0])   # 股票 → 自身，期权 → 底层股票
        stock_info = get_stock_info(list(set(underlying_syms)))
        for p in positions:
            underlying = p['symbol'].split()[0]
            info = stock_info.get(underlying, {})
            p['industry'] = info.get('industry')
            p['sector']   = info.get('sector')

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


def place_sell_order(symbol: str, qty: int, order_type: str,
                     limit_price: float | None = None, tif: str = 'DAY') -> dict:
    """通过 IB 提交卖出订单（市价单或限价单）。order_type: 'MKT' | 'LMT'"""
    _ensure_connected()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")

    from core.trading import Trading
    db = get_db()
    trading = Trading(_ib, db)

    def _do():
        # 防重复/超卖：已挂未成交卖单 + 本次数量 不得超过持仓（跨 clientId 全量查询，
        # 防止盘前重复点击卖出把仓位卖空。reqAllOpenOrders 拉全部 client 的挂单含
        # auto_trader 的止损单）。如确需追加卖出，请先撤销原卖单。
        _ib.reqAllOpenOrders()
        pending = sum(
            float(t.order.totalQuantity)
            for t in _ib.openTrades()
            if t.contract.symbol == symbol and t.order.action == 'SELL'
            and t.orderStatus.status not in ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')
        )
        held = sum(
            abs(float(p.position)) for p in _ib.positions()
            if p.contract.symbol == symbol and getattr(p.contract, 'secType', 'STK') == 'STK'
        )
        if pending + qty > held:
            raise ValueError(
                f"{symbol} 已有 {int(pending)} 股待成交卖单，再卖 {qty} 股将超过持仓 "
                f"{int(held)} 股（会卖空）。如需调整请先撤销原卖单（持仓页订单区或 TWS）。"
            )
        if order_type == 'LMT':
            if limit_price is None:
                raise ValueError("限价单需要提供限价")
            return trading.limit_sell(symbol, qty, limit_price, tif=tif)
        else:
            return trading.market_sell(symbol, qty, tif=tif)

    trade = _run_ib_sync(_do, timeout=30)
    if trade is None:
        raise RuntimeError("下单失败，请检查 IB Gateway 日志")

    return {
        "symbol":     symbol,
        "qty":        qty,
        "order_type": order_type,
        "price":      limit_price,
        "tif":        tif,
        "status":     trade.orderStatus.status,
        "order_id":   trade.order.orderId,
    }


def cancel_open_orders(symbol: str, action: str | None = None) -> dict:
    """撤销指定股票的未成交挂单。action='SELL'/'BUY' 只撤单边（None=全撤）。
    需 Web 连接为 master client（IB_WEB_CLIENT_ID 与 Gateway Master API client ID 一致），
    才能撤其他 client（如 auto_trader）下的单；否则只能撤本连接自己的单。
    按 symbol 级撤销，规避不同 client 的 orderId 重号问题。"""
    _ensure_connected()
    if not _ib or not _ib.isConnected():
        raise RuntimeError("IB Gateway 未连接")
    symu = symbol.upper()

    def _do():
        _ib.reqAllOpenOrders()
        targets = [
            t for t in _ib.openTrades()
            if t.contract.symbol.upper() == symu
            and (action is None or t.order.action == action)
            and t.orderStatus.status not in ('Filled', 'Cancelled', 'ApiCancelled', 'Inactive')
        ]
        for t in targets:
            _ib.cancelOrder(t.order)
        return [
            {'symbol': t.contract.symbol, 'orderId': t.order.orderId,
             'action': t.order.action, 'orderType': t.order.orderType,
             'qty': float(t.order.totalQuantity)}
            for t in targets
        ]

    cancelled = _run_ib_sync(_do, timeout=30)
    # 同步 DB（独立连接，线程安全）
    if cancelled:
        try:
            import sqlite3 as _sq
            import config as _cfg
            con = _sq.connect(_cfg.DB_PATH, timeout=5)
            sql = ("UPDATE orders SET status='Cancelled' WHERE UPPER(symbol)=? "
                   "AND status IN ('PreSubmitted','Submitted','PendingSubmit','PendingCancel')")
            params: tuple = (symu,)
            if action:
                sql += " AND action=?"
                params = (symu, action)
            con.execute(sql, params)
            con.commit(); con.close()
        except Exception:
            pass
    return {"symbol": symu, "cancelled": cancelled, "count": len(cancelled)}


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


