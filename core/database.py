import sqlite3
import os
from datetime import datetime, timedelta
import config
from core.fmt import lj, rj

sqlite3.register_converter("TIMESTAMP", lambda b: datetime.fromisoformat(b.decode()) if b else None)


class Database:
    def __init__(self):
        self.conn = None
        self.cursor = None

    def connect(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
            self.conn = sqlite3.connect(
                config.DB_PATH,
                check_same_thread=False,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self.conn.isolation_level = None  # autocommit
            self.conn.execute('PRAGMA journal_mode=WAL')
            self.conn.execute('PRAGMA foreign_keys=ON')
            self.cursor = self.conn.cursor()
            self._init_db()
            print("数据库连接成功")
        except Exception as e:
            print(f"数据库连接失败：{e}")
            print("程序可以继续运行，但订单不会存库")
            self.conn = None
            self.cursor = None

    def _ensure_conn(self):
        if self.conn:
            try:
                self.conn.execute('SELECT 1')
                return True
            except Exception:
                self.conn = None
                self.cursor = None
        try:
            self.connect()
            return self.conn is not None
        except Exception:
            return False

    def _init_db(self):
        self.cursor.executescript("""
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

    # ---------- orders ----------

    def save_order(self, symbol, action, order_type, quantity,
                   price=None, filled_price=None, status='', order_id=None):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO orders (symbol, action, order_type, quantity, price, filled_price, status, order_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, action, order_type, quantity, price, filled_price, status, order_id))

    def update_order_fill(self, order_id: int, filled_price: float,
                          filled_qty: float, status: str):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            UPDATE orders
               SET filled_price = ?,
                   quantity     = ?,
                   status       = ?
             WHERE order_id = ?
        """, (filled_price, filled_qty, status, order_id))

    def get_pending_orders(self, trade_date: str) -> list:
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT id, symbol, action, order_type, quantity, price, order_id
              FROM orders
             WHERE DATE(created_at) = ?
               AND filled_price IS NULL
               AND order_id IS NOT NULL
               AND status NOT IN ('Cancelled', 'ApiCancelled', 'Inactive')
             ORDER BY created_at ASC
        """, (trade_date,))
        return self.cursor.fetchall()

    def get_orders(self, symbol=None, limit=50):
        if not self._ensure_conn():
            return []
        sql = "SELECT * FROM orders"
        params = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def print_orders(self, symbol=None, limit=20):
        rows = self.get_orders(symbol=symbol, limit=limit)
        if not rows:
            print("\n无交易记录")
            return
        print(f"\n===== 交易记录{f' ({symbol})' if symbol else ''} =====")
        print(f"{lj('时间',22)}{lj('股票',8)}{lj('方向',6)}{lj('类型',6)}{rj('数量',6)}{rj('价格',10)}{rj('状态',12)}")
        print("-" * 72)
        for r in rows:
            # id, symbol, action, order_type, qty, price, filled, status, oid, created
            ts = r[9]
            created  = ts.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ts, datetime) else (str(ts)[:19] if ts else '-')
            symbol_  = r[1]
            action   = r[2]
            otype    = r[3]
            qty      = r[4]
            price    = f"{r[5]:.2f}" if r[5] else "市价"
            status   = r[7]
            print(f"{created:<22}{symbol_:<8}{action:<6}{otype:<6}{qty:>6}{price:>10}{status:>12}")

    # ---------- account snapshots ----------

    def save_account_snapshot(self, net_liq, total_cash, unrealized_pnl, realized_pnl, buying_power):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO account_snapshots
                (net_liquidation, total_cash, unrealized_pnl, realized_pnl, buying_power)
            VALUES (?, ?, ?, ?, ?)
        """, (net_liq, total_cash, unrealized_pnl, realized_pnl, buying_power))

    def get_account_history(self, limit=30):
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT snapshot_at, net_liquidation, total_cash, unrealized_pnl, realized_pnl, buying_power
            FROM account_snapshots
            ORDER BY snapshot_at DESC LIMIT ?
        """, (limit,))
        return self.cursor.fetchall()

    def print_account_history(self, limit=10):
        rows = self.get_account_history(limit=limit)
        if not rows:
            print("\n无账户快照记录")
            return
        print("\n===== 账户净值历史 =====")
        print(f"{lj('时间',22)}{rj('净值',14)}{rj('现金',14)}{rj('浮盈',12)}{rj('实盈',12)}")
        print("-" * 76)
        for r in rows:
            ts = r[0]
            ts_str = ts.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ts, datetime) else (str(ts)[:19] if ts else '-')
            print(f"{ts_str:<22}{r[1]:>14.2f}{r[2]:>14.2f}{r[3]:>12.2f}{r[4]:>12.2f}")

    # ---------- signals ----------

    def save_signals(self, scan_date, signals_dict: dict):
        if not self._ensure_conn():
            return
        rows = []
        for s in signals_dict.get('buy', []):
            rows.append((scan_date, s['symbol'], 1,
                         s.get('rs_score'), s.get('vol_ratio'), s.get('close'), None))
        for s in signals_dict.get('sell', []):
            rows.append((scan_date, s['symbol'], -1,
                         None, None, s.get('close'), s.get('reason')))
        if not rows:
            return
        self.cursor.executemany("""
            INSERT OR IGNORE INTO signals
                (scan_date, symbol, signal, rs_score, vol_ratio, close, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        print(f"  [DB] 信号已存库：买入 {len(signals_dict.get('buy',[]))} 只，"
              f"卖出报警 {len(signals_dict.get('sell',[]))} 只")

    # ---------- scheduled_tasks ----------

    def upsert_task(self, task_id: str, name: str, command: str,
                    cron_expr: str, enabled: bool = True):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO scheduled_tasks (task_id, name, command, cron_expr, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(task_id) DO UPDATE SET
                name      = excluded.name,
                command   = excluded.command,
                cron_expr = excluded.cron_expr,
                enabled   = excluded.enabled,
                updated_at = datetime('now')
        """, (task_id, name, command, cron_expr, int(enabled)))

    def get_tasks(self) -> list:
        if not self._ensure_conn():
            return []
        self.cursor.execute(
            "SELECT task_id, name, command, cron_expr, enabled, created_at, updated_at "
            "FROM scheduled_tasks ORDER BY created_at"
        )
        return self.cursor.fetchall()

    def delete_task(self, task_id: str):
        if not self._ensure_conn():
            return
        self.cursor.execute("DELETE FROM scheduled_tasks WHERE task_id = ?", (task_id,))

    # ---------- task_runs ----------

    def start_task_run(self, task_id: str) -> int:
        if not self._ensure_conn():
            return -1
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute(
            "INSERT INTO task_runs (task_id, started_at, status) VALUES (?, ?, 'running')",
            (task_id, now)
        )
        return self.cursor.lastrowid

    def append_run_log(self, run_id: int, chunk: str):
        """运行中增量追加日志，供实时查看。"""
        if not self._ensure_conn() or not chunk:
            return
        self.cursor.execute(
            "UPDATE task_runs SET log_text = COALESCE(log_text, '') || ? WHERE id = ?",
            (chunk, run_id)
        )

    def finish_task_run(self, run_id: int, exit_code: int, log_text: str):
        if not self._ensure_conn():
            return
        status = 'success' if exit_code == 0 else 'failed'
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        self.cursor.execute("""
            UPDATE task_runs
               SET finished_at = ?,
                   status      = ?,
                   exit_code   = ?,
                   log_text    = ?
             WHERE id = ?
        """, (now, status, exit_code, log_text, run_id))

    def reap_zombie_runs(self, timeout_minutes: int = 5) -> int:
        if not self._ensure_conn():
            return 0
        cutoff = (datetime.utcnow() - timedelta(minutes=timeout_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        log_suffix = f'\n[TIMEOUT] 超过 {timeout_minutes} 分钟未完成，自动标记为失败'
        self.cursor.execute("""
            UPDATE task_runs
               SET finished_at = ?,
                   status      = 'failed',
                   exit_code   = -9,
                   log_text    = COALESCE(log_text, '') || ?
             WHERE status = 'running'
               AND started_at < ?
        """, (now, log_suffix, cutoff))
        return self.cursor.rowcount

    def get_task_runs(self, task_id: str = None, limit: int = 50) -> list:
        if not self._ensure_conn():
            return []
        sql = """
            SELECT r.id, r.task_id, t.name, r.started_at, r.finished_at,
                   r.status, r.exit_code,
                   CAST((julianday(r.finished_at) - julianday(r.started_at)) * 86400 AS INTEGER) AS duration_s
              FROM task_runs r
              LEFT JOIN scheduled_tasks t ON t.task_id = r.task_id
        """
        params = []
        if task_id:
            sql += " WHERE r.task_id = ?"
            params.append(task_id)
        sql += " ORDER BY r.started_at DESC LIMIT ?"
        params.append(limit)
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def delete_task_run(self, run_id: int):
        if not self._ensure_conn():
            return
        self.cursor.execute("DELETE FROM task_runs WHERE id = ?", (run_id,))

    def get_last_runs_per_task(self) -> dict:
        if not self._ensure_conn():
            return {}
        self.cursor.execute("""
            SELECT r.task_id, r.id, r.started_at, r.status
              FROM task_runs r
             INNER JOIN (
                 SELECT task_id, MAX(id) AS max_id
                   FROM task_runs
                  GROUP BY task_id
             ) latest ON r.task_id = latest.task_id AND r.id = latest.max_id
        """)
        return {row[0]: row for row in self.cursor.fetchall()}

    def get_run_log(self, run_id: int) -> str:
        if not self._ensure_conn():
            return ''
        self.cursor.execute("SELECT log_text FROM task_runs WHERE id = ?", (run_id,))
        row = self.cursor.fetchone()
        return row[0] if row else ''

    # ---------- config_store ----------

    def get_config(self, key: str):
        if not self._ensure_conn():
            return None
        self.cursor.execute("SELECT value FROM config_store WHERE key = ?", (key,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def set_config(self, key: str, value: str, typ: str = 'str',
                   category: str = '', description: str = ''):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO config_store (key, value, type, category, description, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value      = excluded.value,
                updated_at = datetime('now')
        """, (key, value, typ, category, description))

    def get_all_config(self) -> list:
        if not self._ensure_conn():
            return []
        self.cursor.execute(
            "SELECT key, value, type, category, description, updated_at "
            "FROM config_store ORDER BY category, key"
        )
        return self.cursor.fetchall()

    # ---------- close ----------

    def close(self):
        if self.conn:
            self.conn.close()
            print("数据库连接已关闭")
