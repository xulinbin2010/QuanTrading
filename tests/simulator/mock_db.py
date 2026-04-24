"""
内存版 Database Mock — 与 core/database.py Database 接口兼容。

不依赖 MySQL，所有数据存储在 list 中。
实现了 auto_trader、confirm_fills、Trading、Account 实际调用的最小接口集。

字段顺序与真实 DB 的 orders 表一致：
  (id, symbol, action, order_type, quantity, price, filled_price, status, order_id, created_at)
  索引:  0      1       2          3           4        5      6              7       8          9
"""

from __future__ import annotations

from datetime import datetime


class _OrderProxy:
    """
    将 orders 表的 tuple 行包装为支持属性访问的对象，用于测试断言。
    字段顺序：
      (id, symbol, action, order_type, quantity, price, filled_price,
       status, order_id, created_at)
       0    1       2      3            4         5      6
       7       8          9
    """

    __slots__ = ('_row',)

    def __init__(self, row: tuple) -> None:
        self._row = row

    @property
    def id(self)           -> int:            return self._row[0]
    @property
    def symbol(self)       -> str:            return self._row[1]
    @property
    def action(self)       -> str:            return self._row[2]
    @property
    def order_type(self)   -> str:            return self._row[3]
    @property
    def quantity(self)     -> float:          return self._row[4]
    @property
    def price(self)        -> float | None:   return self._row[5]
    @property
    def filled_price(self) -> float | None:   return self._row[6]
    @property
    def status(self)       -> str:            return self._row[7]
    @property
    def order_id(self)     -> int | None:     return self._row[8]
    @property
    def created_at(self)   -> datetime:       return self._row[9]

    def __repr__(self) -> str:
        return (
            f'_OrderProxy(id={self.id}, symbol={self.symbol!r}, '
            f'order_id={self.order_id}, filled_price={self.filled_price})'
        )


class MockDatabase:
    """
    core/database.Database 的内存替代品，用于测试。

    主要覆盖 auto_trader._execute_inner / Trading._save 调用的接口：
      - save_order()
      - get_orders()
      - update_order_fill()
      - get_pending_orders()
      - save_account_snapshot()
      - close()

    不实现的接口（与交易执行无关）：
      - save_signals / get_tasks / task_runs / config_store 等

    若测试需要这些接口，可按需扩展。
    """

    def __init__(self) -> None:
        # 主存储：每条记录是一个 tuple，模拟 cursor.fetchall() 返回格式
        # (id, symbol, action, order_type, quantity, price, filled_price, status, order_id, created_at)
        self._orders: list[tuple] = []
        self._order_id: int = 1
        self._snapshots: list[tuple] = []
        self._update_log: list[dict] = []   # 记录每次 update_order_fill 调用

    # ──────────────────────────────────────────────────────
    #  orders 表
    # ──────────────────────────────────────────────────────

    def save_order(
        self,
        symbol: str,
        action: str,
        order_type: str,
        quantity: float,
        price: float | None = None,
        filled_price: float | None = None,
        status: str = '',
        order_id: int | None = None,
    ) -> int:
        """
        保存一条订单记录，返回自增 id。

        字段顺序与真实 orders 表一致（id 字段自增生成）：
          (id, symbol, action, order_type, quantity, price, filled_price,
           status, order_id, created_at)
        """
        row_id = self._order_id
        self._order_id += 1
        now = datetime.now()
        self._orders.append((
            row_id,       # 0: id
            symbol,       # 1: symbol
            action,       # 2: action
            order_type,   # 3: order_type
            quantity,     # 4: quantity
            price,        # 5: price
            filled_price, # 6: filled_price
            status,       # 7: status
            order_id,     # 8: order_id
            now,          # 9: created_at
        ))
        return row_id

    def update_order_fill(
        self,
        order_id: int,
        filled_price: float,
        filled_qty: float,
        status: str,
    ) -> None:
        """成交回报回写：更新 filled_price、quantity（实际成交量）、status。"""
        self._update_log.append({
            'order_id':     order_id,
            'filled_price': filled_price,
            'filled_qty':   filled_qty,
            'status':       status,
        })
        updated = []
        for row in self._orders:
            if row[8] == order_id:  # row[8] = order_id 字段
                row = (
                    row[0],        # id
                    row[1],        # symbol
                    row[2],        # action
                    row[3],        # order_type
                    filled_qty,    # quantity（更新为实际成交量）
                    row[5],        # price（委托价不变）
                    filled_price,  # filled_price（更新）
                    status,        # status（更新）
                    row[8],        # order_id
                    row[9],        # created_at
                )
            updated.append(row)
        self._orders = updated

    def get_orders(self, symbol: str | None = None, limit: int = 100) -> list[tuple]:
        """
        查询订单记录，按 created_at 倒序返回。

        返回格式与真实 DB 一致（tuple 列表），字段顺序：
          (id, symbol, action, order_type, quantity, price, filled_price,
           status, order_id, created_at)

        auto_trader._entry_date() 读取：
          r[2] = action, r[6] = filled_price, r[9] = created_at
        database.print_orders() 读取同样索引，字段顺序须与此保持一致。
        """
        rows = list(reversed(self._orders))  # 倒序（最新在前）
        if symbol:
            rows = [r for r in rows if r[1] == symbol]
        return rows[:limit]

    def get_pending_orders(self, trade_date: str) -> list[tuple]:
        """
        返回指定日期 filled_price 为 None 的待确认订单。

        confirm_fills.py 用此方法查询需回写的 OPG 单：
          返回字段：(id, symbol, action, order_type, quantity, price, order_id)
        """
        result = []
        for row in self._orders:
            row_date = row[9].strftime('%Y-%m-%d') if row[9] else ''
            filled_price = row[6]
            order_id = row[8]
            status = row[7]
            if (row_date == trade_date
                    and filled_price is None
                    and order_id is not None
                    and status not in ('Cancelled', 'ApiCancelled', 'Inactive')):
                # 返回字段子集（与真实 DB 一致）
                result.append((
                    row[0],  # id
                    row[1],  # symbol
                    row[2],  # action
                    row[3],  # order_type
                    row[4],  # quantity
                    row[5],  # price
                    row[8],  # order_id
                ))
        return result

    # ──────────────────────────────────────────────────────
    #  account_snapshots 表
    # ──────────────────────────────────────────────────────

    def save_account_snapshot(
        self,
        net_liq: float,
        total_cash: float,
        unrealized_pnl: float,
        realized_pnl: float,
        buying_power: float,
    ) -> None:
        """保存账户快照（account.py 在 print_balance() 中调用）。"""
        self._snapshots.append((
            datetime.now(),
            net_liq,
            total_cash,
            unrealized_pnl,
            realized_pnl,
            buying_power,
        ))

    def get_account_history(self, limit: int = 30) -> list[tuple]:
        """返回账户快照历史（倒序）。"""
        return list(reversed(self._snapshots))[:limit]

    # ──────────────────────────────────────────────────────
    #  辅助方法（测试断言用）
    # ──────────────────────────────────────────────────────

    def count_orders(self, symbol: str | None = None, action: str | None = None) -> int:
        """统计订单数量，可按 symbol / action 过滤。"""
        rows = self._orders
        if symbol:
            rows = [r for r in rows if r[1] == symbol]
        if action:
            rows = [r for r in rows if r[2] == action]
        return len(rows)

    def last_order(self, symbol: str | None = None) -> tuple | None:
        """返回最近一条订单记录（可按 symbol 过滤）。"""
        rows = list(reversed(self._orders))
        if symbol:
            rows = [r for r in rows if r[1] == symbol]
        return rows[0] if rows else None

    def inject_orders(self, orders: list[dict]) -> None:
        """
        批量预置测试订单，接受 dict 列表。
        每个 dict 的 key 与 save_order 参数名一致，
        额外支持 'created_at'（datetime）字段控制日期归属。

        示例::
            db.inject_orders([
                {'symbol': 'NVDA', 'action': 'BUY', 'order_type': 'LMT',
                 'quantity': 50, 'price': 480.0, 'filled_price': None,
                 'status': 'Submitted', 'order_id': 1001,
                 'created_at': datetime(2026, 4, 22, 10, 0, 0)},
            ])
        """
        from datetime import datetime as _dt
        for d in orders:
            row_id = self._order_id
            self._order_id += 1
            created_at = d.get('created_at', _dt.now())
            self._orders.append((
                row_id,                        # 0: id
                d['symbol'],                   # 1: symbol
                d.get('action', 'BUY'),        # 2: action
                d.get('order_type', 'LMT'),    # 3: order_type
                d.get('quantity', 0),          # 4: quantity
                d.get('price'),                # 5: price
                d.get('filled_price'),         # 6: filled_price
                d.get('status', 'Submitted'),  # 7: status
                d.get('order_id'),             # 8: order_id
                created_at,                    # 9: created_at
            ))

    def get_order_by_id(self, order_id: int) -> '_OrderProxy | None':
        """按 order_id（字段 index 8）查找订单行（测试断言用）。"""
        for row in self._orders:
            if row[8] == order_id:
                return _OrderProxy(row)
        return None

    def update_call_count(self) -> int:
        """返回 update_order_fill 被调用的总次数（幂等性断言用）。"""
        return len(self._update_log)

    def get_update_log(self) -> list[dict]:
        """返回所有 update_order_fill 调用记录的副本。"""
        return list(self._update_log)

    def reset(self) -> None:
        """清空所有数据（每个测试用例 setUp 时调用）。"""
        self._orders.clear()
        self._snapshots.clear()
        self._update_log.clear()
        self._order_id = 1

    # ──────────────────────────────────────────────────────
    #  生命周期（与真实 Database 接口兼容）
    # ──────────────────────────────────────────────────────

    def close(self) -> None:
        """no-op，保持与 Database.close() 接口一致。"""
        pass
