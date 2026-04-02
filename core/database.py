import pymysql
import config
from core.fmt import lj, rj


class Database:
    def __init__(self):
        self.conn = None
        self.cursor = None

    def connect(self):
        try:
            self.conn = pymysql.connect(
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWORD,
                charset='utf8mb4',
                autocommit=True,
                connect_timeout=3,
            )
            self.cursor = self.conn.cursor()
            self._init_db()
            print("数据库连接成功")
        except Exception as e:
            print(f"数据库连接失败：{e}")
            print("程序可以继续运行，但订单不会存库")
            self.conn = None
            self.cursor = None

    def _ensure_conn(self):
        """检查连接是否有效，断了就带超时重连"""
        if not self.conn:
            return False
        try:
            self.conn.ping(reconnect=False)
            self.cursor = self.conn.cursor()
            self.cursor.execute(f"USE `{config.DB_NAME}`")
            return True
        except Exception:
            pass
        # ping 失败 → 手动重连（带 3s 超时）
        try:
            self.conn = pymysql.connect(
                host=config.DB_HOST,
                port=config.DB_PORT,
                user=config.DB_USER,
                password=config.DB_PASSWORD,
                charset='utf8mb4',
                autocommit=True,
                connect_timeout=3,
            )
            self.cursor = self.conn.cursor()
            self.cursor.execute(f"USE `{config.DB_NAME}`")
            return True
        except Exception:
            self.conn = None
            self.cursor = None
            return False

    def _init_db(self):
        self.cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{config.DB_NAME}` DEFAULT CHARSET utf8mb4")
        self.cursor.execute(f"USE `{config.DB_NAME}`")

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                symbol      VARCHAR(20)  NOT NULL,
                action      VARCHAR(10)  NOT NULL,
                order_type  VARCHAR(10)  NOT NULL,
                quantity    DECIMAL(12,2) NOT NULL,
                price       DECIMAL(12,4),
                filled_price DECIMAL(12,4),
                status      VARCHAR(20)  NOT NULL,
                order_id    INT,
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id                INT AUTO_INCREMENT PRIMARY KEY,
                net_liquidation   DECIMAL(16,2),
                total_cash        DECIMAL(16,2),
                unrealized_pnl    DECIMAL(16,2),
                realized_pnl      DECIMAL(16,2),
                buying_power      DECIMAL(16,2),
                snapshot_at       DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id         INT AUTO_INCREMENT PRIMARY KEY,
                scan_date  DATE         NOT NULL,
                symbol     VARCHAR(20)  NOT NULL,
                `signal`   TINYINT      NOT NULL,
                rs_score   DECIMAL(8,4),
                vol_ratio  DECIMAL(8,4),
                close      DECIMAL(12,4),
                reason     VARCHAR(50),
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_signal (scan_date, symbol, `signal`)
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS klines (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                symbol      VARCHAR(20)  NOT NULL,
                bar_size    VARCHAR(20)  NOT NULL,
                dt          DATETIME     NOT NULL,
                open        DECIMAL(14,4) NOT NULL,
                high        DECIMAL(14,4) NOT NULL,
                low         DECIMAL(14,4) NOT NULL,
                close       DECIMAL(14,4) NOT NULL,
                volume      BIGINT        NOT NULL,
                UNIQUE KEY uq_kline (symbol, bar_size, dt)
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_id     VARCHAR(50)   PRIMARY KEY,
                name        VARCHAR(100)  NOT NULL,
                command     VARCHAR(500)  NOT NULL,
                cron_expr   VARCHAR(50),
                enabled     TINYINT(1)    DEFAULT 1,
                created_at  DATETIME      DEFAULT CURRENT_TIMESTAMP,
                updated_at  DATETIME      DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS task_runs (
                id          INT AUTO_INCREMENT PRIMARY KEY,
                task_id     VARCHAR(50)   NOT NULL,
                started_at  DATETIME      NOT NULL,
                finished_at DATETIME,
                status      VARCHAR(20)   DEFAULT 'running',
                exit_code   INT,
                log_text    MEDIUMTEXT,
                INDEX idx_task (task_id, started_at)
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS config_store (
                `key`       VARCHAR(50)  PRIMARY KEY,
                value       VARCHAR(500) NOT NULL,
                type        VARCHAR(10)  DEFAULT 'str',
                category    VARCHAR(20),
                description VARCHAR(200),
                updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP
            )
        """)

    # ---------- orders ----------

    def save_order(self, symbol, action, order_type, quantity,
                   price=None, filled_price=None, status='', order_id=None):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO orders (symbol, action, order_type, quantity, price, filled_price, status, order_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (symbol, action, order_type, quantity, price, filled_price, status, order_id))

    def update_order_fill(self, order_id: int, filled_price: float,
                          filled_qty: float, status: str):
        """成交回报回写：更新 filled_price、filled_qty、status。"""
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            UPDATE orders
               SET filled_price = %s,
                   quantity     = %s,
                   status       = %s
             WHERE order_id = %s
        """, (filled_price, filled_qty, status, order_id))

    def get_pending_orders(self, trade_date: str) -> list:
        """返回指定日期 filled_price 仍为 NULL 的订单（待确认）。"""
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT id, symbol, action, order_type, quantity, price, order_id
              FROM orders
             WHERE DATE(created_at) = %s
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
            sql += " WHERE symbol = %s"
            params.append(symbol)
        sql += " ORDER BY created_at DESC LIMIT %s"
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
            created  = r[9].strftime('%Y-%m-%d %H:%M:%S') if r[9] else '-'
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
            VALUES (%s, %s, %s, %s, %s)
        """, (net_liq, total_cash, unrealized_pnl, realized_pnl, buying_power))

    def get_account_history(self, limit=30):
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT snapshot_at, net_liquidation, total_cash, unrealized_pnl, realized_pnl, buying_power
            FROM account_snapshots
            ORDER BY snapshot_at DESC LIMIT %s
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
            ts = r[0].strftime('%Y-%m-%d %H:%M:%S') if r[0] else '-'
            print(f"{ts:<22}{r[1]:>14.2f}{r[2]:>14.2f}{r[3]:>12.2f}{r[4]:>12.2f}")

    # ---------- signals ----------

    def save_signals(self, scan_date, signals_dict: dict):
        """保存扫描信号（买入候选 + 卖出报警），重复的自动忽略"""
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
            INSERT IGNORE INTO signals
                (scan_date, symbol, `signal`, rs_score, vol_ratio, close, reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, rows)
        print(f"  [DB] 信号已存库：买入 {len(signals_dict.get('buy',[]))} 只，"
              f"卖出报警 {len(signals_dict.get('sell',[]))} 只")

    # ---------- klines ----------

    def save_klines(self, symbol: str, bar_size: str, bars: list):
        """批量保存K线，重复的自动忽略"""
        if not self._ensure_conn() or not bars:
            return 0
        sql = """
            INSERT IGNORE INTO klines (symbol, bar_size, dt, open, high, low, close, volume)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        rows = [
            (symbol, bar_size, b.date, b.open, b.high, b.low, b.close, b.volume)
            for b in bars
        ]
        self.cursor.executemany(sql, rows)
        return self.cursor.rowcount

    def get_latest_dt(self, symbol: str, bar_size: str):
        """返回该 symbol+bar_size 最新一条K线的时间，没有则返回 None"""
        if not self._ensure_conn():
            return None
        self.cursor.execute("""
            SELECT MAX(dt) FROM klines WHERE symbol = %s AND bar_size = %s
        """, (symbol, bar_size))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def get_klines(self, symbol: str, bar_size: str, limit: int = 200):
        """查询K线，按时间升序"""
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT dt, open, high, low, close, volume
            FROM klines
            WHERE symbol = %s AND bar_size = %s
            ORDER BY dt DESC LIMIT %s
        """, (symbol, bar_size, limit))
        rows = self.cursor.fetchall()
        return list(reversed(rows))  # 改为升序返回

    def get_klines_count(self, symbol: str, bar_size: str):
        if not self._ensure_conn():
            return 0
        self.cursor.execute(
            "SELECT COUNT(*) FROM klines WHERE symbol = %s AND bar_size = %s",
            (symbol, bar_size)
        )
        return self.cursor.fetchone()[0]

    # ---------- scheduled_tasks ----------

    def upsert_task(self, task_id: str, name: str, command: str,
                    cron_expr: str, enabled: bool = True):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO scheduled_tasks (task_id, name, command, cron_expr, enabled)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name       = VALUES(name),
                command    = VALUES(command),
                cron_expr  = VALUES(cron_expr),
                enabled    = VALUES(enabled),
                updated_at = CURRENT_TIMESTAMP
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
        self.cursor.execute("DELETE FROM scheduled_tasks WHERE task_id = %s", (task_id,))

    # ---------- task_runs ----------

    def start_task_run(self, task_id: str) -> int:
        """记录任务开始，返回 run id"""
        if not self._ensure_conn():
            return -1
        self.cursor.execute("""
            INSERT INTO task_runs (task_id, started_at, status)
            VALUES (%s, NOW(), 'running')
        """, (task_id,))
        return self.cursor.lastrowid

    def finish_task_run(self, run_id: int, exit_code: int, log_text: str):
        if not self._ensure_conn():
            return
        status = 'success' if exit_code == 0 else 'failed'
        self.cursor.execute("""
            UPDATE task_runs
               SET finished_at = NOW(),
                   status      = %s,
                   exit_code   = %s,
                   log_text    = %s
             WHERE id = %s
        """, (status, exit_code, log_text, run_id))

    def reap_zombie_runs(self, timeout_minutes: int = 5) -> int:
        """将超时仍处于 running 的僵尸记录标记为 failed，返回清理条数"""
        if not self._ensure_conn():
            return 0
        self.cursor.execute("""
            UPDATE task_runs
               SET finished_at = NOW(),
                   status      = 'failed',
                   exit_code   = -9,
                   log_text    = CONCAT(COALESCE(log_text, ''), '\n[TIMEOUT] 超过 %s 分钟未完成，自动标记为失败')
             WHERE status = 'running'
               AND started_at < DATE_SUB(NOW(), INTERVAL %s MINUTE)
        """, (timeout_minutes, timeout_minutes))
        return self.cursor.rowcount

    def get_task_runs(self, task_id: str = None, limit: int = 50) -> list:
        if not self._ensure_conn():
            return []
        sql = """
            SELECT r.id, r.task_id, t.name, r.started_at, r.finished_at,
                   r.status, r.exit_code,
                   TIMESTAMPDIFF(SECOND, r.started_at, r.finished_at) AS duration_s
              FROM task_runs r
              LEFT JOIN scheduled_tasks t ON t.task_id = r.task_id
        """
        params = []
        if task_id:
            sql += " WHERE r.task_id = %s"
            params.append(task_id)
        sql += " ORDER BY r.started_at DESC LIMIT %s"
        params.append(limit)
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def delete_task_run(self, run_id: int):
        if not self._ensure_conn():
            return
        self.cursor.execute("DELETE FROM task_runs WHERE id = %s", (run_id,))

    def get_last_runs_per_task(self) -> dict:
        """批量获取每个 task_id 的最近一次执行记录，返回 {task_id: (id, started_at, status)}"""
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
        self.cursor.execute("SELECT log_text FROM task_runs WHERE id = %s", (run_id,))
        row = self.cursor.fetchone()
        return row[0] if row else ''

    # ---------- config_store ----------

    def get_config(self, key: str):
        """返回单个配置值（字符串），不存在返回 None"""
        if not self._ensure_conn():
            return None
        self.cursor.execute("SELECT value FROM config_store WHERE `key` = %s", (key,))
        row = self.cursor.fetchone()
        return row[0] if row else None

    def set_config(self, key: str, value: str, typ: str = 'str',
                   category: str = '', description: str = ''):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            INSERT INTO config_store (`key`, value, type, category, description)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                updated_at = NOW()
        """, (key, value, typ, category, description))

    def get_all_config(self) -> list:
        """返回所有配置项 (key, value, type, category, description, updated_at)"""
        if not self._ensure_conn():
            return []
        self.cursor.execute(
            "SELECT `key`, value, type, category, description, updated_at "
            "FROM config_store ORDER BY category, `key`"
        )
        return self.cursor.fetchall()

    # ---------- close ----------

    def close(self):
        if self.conn:
            self.conn.close()
            print("数据库连接已关闭")
