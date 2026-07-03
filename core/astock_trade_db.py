"""A 股半自动交易台账（独立 SQLite，与美股 core/database.py 完全隔离）。

半自动场景下系统不接券商，无法自动获知持仓，故本地维护一份台账：
  - astock_positions       当前持仓（手动录入 + 回填成交维护）
  - astock_orders          每周调仓指令清单（pending → filled/skipped/canceled）
  - astock_trade_settings  总资金 / 持仓数 / 策略等配置（kv）

存储文件 data/astock_trade.db，不复用 config.DB_PATH（那是美股 orders/account_snapshots）。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / 'data' / 'astock_trade.db'

_DEFAULT_SETTINGS = {
    'capital': '70000',          # 总资金（元）
    'top_n': '5',                # 目标持仓数
    'strategy': 'sector_rotation',
    'mode': 'theme',             # 板块轮动走主题板块
}


@contextmanager
def _conn():
    """每次操作开一个连接（A 股调仓低频，SQLite 轻量，autocommit + WAL）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.isolation_level = None  # autocommit
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS astock_positions (
                code       TEXT PRIMARY KEY,
                name       TEXT,
                qty        INTEGER NOT NULL,
                avg_cost   REAL NOT NULL,
                open_date  TEXT,
                update_date TEXT
            );
            CREATE TABLE IF NOT EXISTS astock_orders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_date   TEXT NOT NULL,
                code        TEXT NOT NULL,
                name        TEXT,
                side        TEXT NOT NULL,          -- BUY / SELL
                target_qty  INTEGER NOT NULL,
                ref_price   REAL,                   -- 生成时参考价（最新收盘）
                budget      REAL,                   -- 预算金额
                reason      TEXT,                   -- 新进目标 / 掉出目标 / 预算不足1手
                filled_qty  INTEGER,
                filled_price REAL,
                status      TEXT NOT NULL DEFAULT 'pending',  -- pending/filled/skipped/canceled
                create_ts   TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_orders_plan ON astock_orders(plan_date);
            CREATE TABLE IF NOT EXISTS astock_trade_settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        # 初始化默认设置（仅填补缺失项，不覆盖用户改过的）
        for k, v in _DEFAULT_SETTINGS.items():
            c.execute(
                'INSERT OR IGNORE INTO astock_trade_settings(key, value) VALUES(?, ?)',
                (k, v),
            )


# ── 设置 ──────────────────────────────────────────────

def get_settings() -> dict:
    init_db()
    with _conn() as c:
        rows = c.execute('SELECT key, value FROM astock_trade_settings').fetchall()
    raw = {r['key']: r['value'] for r in rows}
    return {
        'capital': float(raw.get('capital', 70000)),
        'top_n': int(raw.get('top_n', 5)),
        'strategy': raw.get('strategy', 'sector_rotation'),
        'mode': raw.get('mode', 'theme'),
    }


def update_settings(patch: dict) -> dict:
    init_db()
    with _conn() as c:
        for k in ('capital', 'top_n', 'strategy', 'mode'):
            if k in patch and patch[k] is not None:
                c.execute(
                    'INSERT INTO astock_trade_settings(key, value) VALUES(?, ?) '
                    'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                    (k, str(patch[k])),
                )
    return get_settings()


# ── 持仓 ──────────────────────────────────────────────

def get_positions() -> list[dict]:
    init_db()
    with _conn() as c:
        rows = c.execute(
            'SELECT code, name, qty, avg_cost, open_date, update_date '
            'FROM astock_positions WHERE qty > 0 ORDER BY code'
        ).fetchall()
    return [dict(r) for r in rows]


def get_position(code: str) -> dict | None:
    with _conn() as c:
        r = c.execute(
            'SELECT code, name, qty, avg_cost, open_date, update_date '
            'FROM astock_positions WHERE code = ?', (code,)
        ).fetchone()
    return dict(r) if r else None


def upsert_position(code: str, name: str | None, qty: int, avg_cost: float,
                    open_date: str | None = None) -> None:
    """覆盖式写入一只持仓（qty<=0 视为清仓删除）。"""
    init_db()
    today = date.today().isoformat()
    if qty <= 0:
        remove_position(code)
        return
    with _conn() as c:
        existing = c.execute('SELECT open_date FROM astock_positions WHERE code=?', (code,)).fetchone()
        od = open_date or (existing['open_date'] if existing else today)
        c.execute(
            'INSERT INTO astock_positions(code, name, qty, avg_cost, open_date, update_date) '
            'VALUES(?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(code) DO UPDATE SET name=excluded.name, qty=excluded.qty, '
            'avg_cost=excluded.avg_cost, open_date=excluded.open_date, update_date=excluded.update_date',
            (code, name, int(qty), round(float(avg_cost), 4), od, today),
        )


def remove_position(code: str) -> None:
    with _conn() as c:
        c.execute('DELETE FROM astock_positions WHERE code = ?', (code,))


# ── 调仓单 ────────────────────────────────────────────

def replace_plan(plan_date: str, orders: list[dict]) -> None:
    """用新清单替换某日的调仓单：重新生成会清掉该日所有未成交单（pending/skipped/
    canceled），只保留已 filled 的（本次调仓已执行的部分，不重复、不丢轨迹）。
    多次点「生成」即幂等重算，不累加。"""
    init_db()
    now = _now_ts()
    with _conn() as c:
        c.execute(
            "DELETE FROM astock_orders WHERE plan_date=? AND status != 'filled'", (plan_date,)
        )
        for o in orders:
            c.execute(
                'INSERT INTO astock_orders(plan_date, code, name, side, target_qty, ref_price, '
                'budget, reason, status, create_ts) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (plan_date, o['code'], o.get('name'), o['side'], int(o['target_qty']),
                 o.get('ref_price'), o.get('budget'), o.get('reason'),
                 o.get('status', 'pending'), now),
            )


def get_orders(plan_date: str | None = None, status: str | None = None) -> list[dict]:
    init_db()
    sql = ('SELECT id, plan_date, code, name, side, target_qty, ref_price, budget, reason, '
           'filled_qty, filled_price, status, create_ts FROM astock_orders WHERE 1=1')
    args: list = []
    if plan_date:
        sql += ' AND plan_date = ?'; args.append(plan_date)
    if status:
        sql += ' AND status = ?'; args.append(status)
    sql += ' ORDER BY CASE side WHEN "SELL" THEN 0 ELSE 1 END, id'
    with _conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def get_order(order_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute(
            'SELECT id, plan_date, code, name, side, target_qty, ref_price, budget, reason, '
            'filled_qty, filled_price, status, create_ts FROM astock_orders WHERE id=?',
            (order_id,)
        ).fetchone()
    return dict(r) if r else None


def update_order(order_id: int, filled_qty: int | None, filled_price: float | None,
                 status: str) -> None:
    with _conn() as c:
        c.execute(
            'UPDATE astock_orders SET filled_qty=?, filled_price=?, status=? WHERE id=?',
            (filled_qty, filled_price, status, order_id),
        )


def latest_plan_date() -> str | None:
    init_db()
    with _conn() as c:
        r = c.execute('SELECT MAX(plan_date) AS d FROM astock_orders').fetchone()
    return r['d'] if r and r['d'] else None


def _now_ts() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec='seconds')
