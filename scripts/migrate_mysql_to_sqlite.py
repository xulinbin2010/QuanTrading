"""
一次性迁移脚本：MySQL quantrading → SQLite data/quantrading.db

用法：
    .venv/bin/python scripts/migrate_mysql_to_sqlite.py
"""
import os
import sys
import sqlite3
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql

# ── 源：MySQL ──────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), '.env'))

MYSQL_CFG = dict(
    host=os.getenv('DB_HOST', '127.0.0.1'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'root'),
    password=os.getenv('DB_PASSWORD', ''),
    database=os.getenv('DB_NAME', 'quantrading'),
    charset='utf8mb4',
)

# ── 目标：SQLite ───────────────────────────────────────────
SQLITE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'quantrading.db')
os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)


def ts(v):
    """datetime → ISO字符串，None 保持 None"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=' ')
    return str(v)


def migrate():
    print(f"连接 MySQL {MYSQL_CFG['host']}:{MYSQL_CFG['port']} / {MYSQL_CFG['database']} ...")
    my = pymysql.connect(**MYSQL_CFG, cursorclass=pymysql.cursors.DictCursor)

    print(f"创建/打开 SQLite {SQLITE_PATH} ...")
    sq = sqlite3.connect(SQLITE_PATH)
    sq.execute('PRAGMA journal_mode=WAL')
    sq.execute('PRAGMA foreign_keys=ON')

    # ── 建表 ──────────────────────────────────────────────
    sq.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT NOT NULL,
            action       TEXT NOT NULL,
            order_type   TEXT NOT NULL,
            quantity     REAL NOT NULL,
            price        REAL,
            filled_price REAL,
            status       TEXT NOT NULL,
            order_id     INTEGER,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS account_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            net_liquidation REAL,
            total_cash      REAL,
            unrealized_pnl  REAL,
            realized_pnl    REAL,
            buying_power    REAL,
            snapshot_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS signals (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date  TEXT    NOT NULL,
            symbol     TEXT    NOT NULL,
            signal     INTEGER NOT NULL,
            rs_score   REAL,
            vol_ratio  REAL,
            close      REAL,
            reason     TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(scan_date, symbol, signal)
        );
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            task_id    TEXT PRIMARY KEY,
            name       TEXT NOT NULL,
            command    TEXT NOT NULL,
            cron_expr  TEXT,
            enabled    INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS task_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id     TEXT NOT NULL,
            started_at  TIMESTAMP NOT NULL,
            finished_at TIMESTAMP,
            status      TEXT DEFAULT 'running',
            exit_code   INTEGER,
            log_text    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_task ON task_runs(task_id, started_at);
        CREATE TABLE IF NOT EXISTS config_store (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            type        TEXT DEFAULT 'str',
            category    TEXT,
            description TEXT,
            updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    sq.commit()

    cur = my.cursor()

    # ── orders ────────────────────────────────────────────
    cur.execute("SELECT * FROM orders ORDER BY id")
    rows = cur.fetchall()
    for r in rows:
        sq.execute("""
            INSERT OR IGNORE INTO orders
            (id, symbol, action, order_type, quantity, price, filled_price, status, order_id, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (r['id'], r['symbol'], r['action'], r['order_type'],
              float(r['quantity']), float(r['price']) if r['price'] is not None else None,
              float(r['filled_price']) if r['filled_price'] is not None else None,
              r['status'], r['order_id'], ts(r['created_at'])))
    sq.commit()
    print(f"  orders:            {len(rows)} 行")

    # ── account_snapshots ─────────────────────────────────
    cur.execute("SELECT * FROM account_snapshots ORDER BY id")
    rows = cur.fetchall()
    for r in rows:
        sq.execute("""
            INSERT OR IGNORE INTO account_snapshots
            (id, net_liquidation, total_cash, unrealized_pnl, realized_pnl, buying_power, snapshot_at)
            VALUES (?,?,?,?,?,?,?)
        """, (r['id'],
              float(r['net_liquidation']) if r['net_liquidation'] is not None else None,
              float(r['total_cash'])      if r['total_cash']      is not None else None,
              float(r['unrealized_pnl'])  if r['unrealized_pnl']  is not None else None,
              float(r['realized_pnl'])    if r['realized_pnl']    is not None else None,
              float(r['buying_power'])    if r['buying_power']    is not None else None,
              ts(r['snapshot_at'])))
    sq.commit()
    print(f"  account_snapshots: {len(rows)} 行")

    # ── signals ───────────────────────────────────────────
    cur.execute("SELECT * FROM signals ORDER BY id")
    rows = cur.fetchall()
    for r in rows:
        sq.execute("""
            INSERT OR IGNORE INTO signals
            (id, scan_date, symbol, signal, rs_score, vol_ratio, close, reason, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (r['id'], str(r['scan_date']), r['symbol'], int(r['signal']),
              float(r['rs_score'])  if r['rs_score']  is not None else None,
              float(r['vol_ratio']) if r['vol_ratio']  is not None else None,
              float(r['close'])     if r['close']      is not None else None,
              r.get('reason'), ts(r['created_at'])))
    sq.commit()
    print(f"  signals:           {len(rows)} 行")

    # ── scheduled_tasks ───────────────────────────────────
    cur.execute("SELECT * FROM scheduled_tasks")
    rows = cur.fetchall()
    for r in rows:
        sq.execute("""
            INSERT OR REPLACE INTO scheduled_tasks
            (task_id, name, command, cron_expr, enabled, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?)
        """, (r['task_id'], r['name'], r['command'], r['cron_expr'],
              int(r['enabled']), ts(r['created_at']), ts(r['updated_at'])))
    sq.commit()
    print(f"  scheduled_tasks:   {len(rows)} 行")

    # ── task_runs ─────────────────────────────────────────
    cur.execute("SELECT * FROM task_runs ORDER BY id")
    rows = cur.fetchall()
    for r in rows:
        sq.execute("""
            INSERT OR IGNORE INTO task_runs
            (id, task_id, started_at, finished_at, status, exit_code, log_text)
            VALUES (?,?,?,?,?,?,?)
        """, (r['id'], r['task_id'], ts(r['started_at']), ts(r['finished_at']),
              r['status'], r['exit_code'], r.get('log_text')))
    sq.commit()
    print(f"  task_runs:         {len(rows)} 行")

    # ── config_store ──────────────────────────────────────
    cur.execute("SELECT * FROM config_store")
    rows = cur.fetchall()
    for r in rows:
        sq.execute("""
            INSERT OR REPLACE INTO config_store (key, value, type, category, description, updated_at)
            VALUES (?,?,?,?,?,?)
        """, (r['key'], r['value'], r.get('type', 'str'),
              r.get('category'), r.get('description'),
              ts(r.get('updated_at'))))
    sq.commit()
    print(f"  config_store:      {len(rows)} 行")

    my.close()
    sq.close()
    print(f"\n迁移完成 → {SQLITE_PATH}")


if __name__ == '__main__':
    migrate()
