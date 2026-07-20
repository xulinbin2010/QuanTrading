import sqlite3
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_CST = ZoneInfo('Asia/Shanghai')

def _now_cst() -> str:
    return datetime.now(_CST).strftime('%Y-%m-%d %H:%M:%S')
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

            CREATE TABLE IF NOT EXISTS pending_exits (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT NOT NULL,
                qty           INTEGER NOT NULL,
                avg_cost      REAL,
                trigger_price REAL,
                ret           REAL,
                rule          TEXT,
                reason        TEXT,
                status        TEXT DEFAULT 'pending',
                intel_json    TEXT,
                intel_at      TIMESTAMP,
                decided_at    TIMESTAMP,
                triggered_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_pending_exits_status
                ON pending_exits(status, symbol);

            CREATE TABLE IF NOT EXISTS social_mentions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT NOT NULL,
                source     TEXT NOT NULL,      -- apewisdom / stocktwits / reddit_posts
                trade_date TEXT NOT NULL,      -- 美东日期 YYYY-MM-DD（z-score 按日聚合用）
                mentions   INTEGER,            -- 提及数（apewisdom=24h 全 Reddit / reddit_posts=热帖命中数）
                rank       INTEGER,            -- apewisdom 全站排名（其他源 NULL）
                upvotes    INTEGER,
                bull_cnt   INTEGER,            -- stocktwits 近页帖子中带 Bullish 标签数
                bear_cnt   INTEGER,
                extra      TEXT,               -- JSON 附加（热帖标题样本等，仅展示用）
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_social_mentions_sym
                ON social_mentions(symbol, source, trade_date);
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
               AND status NOT IN ('Cancelled', 'ApiCancelled', 'Inactive', 'Expired')
             ORDER BY created_at ASC
        """, (trade_date,))
        return self.cursor.fetchall()

    def get_stale_orders(self, before_date: str) -> list:
        """取 before_date（不含）之前仍处于非终态且无成交价的订单。
        OPG 只对下一开盘有效、DAY 当日失效——前一交易日之前的非终态单要么已死、
        要么已成交但漏对账、要么是仍活在 IB 的僵尸单（休市日顺延，2026-07-03 事故），
        必须逐笔与 IB 对账后再定终态，不能直接标 Expired。"""
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT id, symbol, action, order_type, quantity, order_id, created_at
              FROM orders
             WHERE status IN ('PreSubmitted', 'Submitted', 'PendingSubmit')
               AND filled_price IS NULL
               AND DATE(created_at) < ?
             ORDER BY created_at ASC
        """, (before_date,))
        return self.cursor.fetchall()

    def set_order_result(self, db_id: int, status: str,
                         filled_price: float | None = None) -> None:
        """按 DB 行 id 精确回写订单终态（order_id 是 IB 侧 clientId 内自增，跨日可重号）。"""
        if not self._ensure_conn():
            return
        if filled_price is not None:
            self.cursor.execute(
                "UPDATE orders SET status = ?, filled_price = ? WHERE id = ?",
                (status, filled_price, db_id))
        else:
            self.cursor.execute(
                "UPDATE orders SET status = ? WHERE id = ?", (status, db_id))
        self.conn.commit()

    # ---------- pending_exits（半自动出场：触发→待人工确认）----------

    def _pe_rows(self) -> list:
        cols = [d[0] for d in self.cursor.description]
        return [dict(zip(cols, r)) for r in self.cursor.fetchall()]

    def upsert_pending_exit(self, symbol: str, qty: int, avg_cost: float,
                            trigger_price: float, ret: float,
                            rule: str, reason: str):
        """登记/刷新一条待确认出场。返回 (id, created)；当日已被人工「保留」则跳过返回 (None, False)。
        - 已有 pending 记录：刷新价格/浮亏/规则（保留已生成的情报），支持隔日重提醒
        - 当日已决策为 kept：不再重复提醒（次日触发条件仍成立会重新建新记录）
        - 9:00 OPG 与 9:35 exits-only 双跑天然去重"""
        if not self._ensure_conn():
            return None, False
        self.cursor.execute(
            "SELECT id FROM pending_exits WHERE symbol = ? AND status = 'pending'", (symbol,))
        row = self.cursor.fetchone()
        if row:
            self.cursor.execute("""
                UPDATE pending_exits
                   SET qty = ?, avg_cost = ?, trigger_price = ?, ret = ?,
                       rule = ?, reason = ?, updated_at = datetime('now', 'localtime')
                 WHERE id = ?
            """, (qty, avg_cost, trigger_price, ret, rule, reason, row[0]))
            return row[0], False
        self.cursor.execute("""
            SELECT id FROM pending_exits
             WHERE symbol = ? AND status = 'kept'
               AND DATE(decided_at) = DATE('now', 'localtime')
        """, (symbol,))
        if self.cursor.fetchone():
            return None, False
        self.cursor.execute("""
            INSERT INTO pending_exits (symbol, qty, avg_cost, trigger_price, ret, rule, reason,
                                       triggered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))
        """, (symbol, qty, avg_cost, trigger_price, ret, rule, reason))
        return self.cursor.lastrowid, True

    def record_auto_exit(self, symbol: str, qty: int, avg_cost: float,
                         trigger_price: float, ret: float, rule: str, reason: str):
        """灾难硬止损等全自动卖出的审计记录（status=auto_sold，不需要确认）。
        同时把该股尚存的 pending 记录标记为已自动处理。"""
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            UPDATE pending_exits SET status = 'auto_sold',
                   decided_at = datetime('now','localtime'),
                   updated_at = datetime('now','localtime')
             WHERE symbol = ? AND status = 'pending'
        """, (symbol,))
        self.cursor.execute("""
            INSERT INTO pending_exits (symbol, qty, avg_cost, trigger_price, ret, rule, reason,
                                       status, decided_at, triggered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'auto_sold',
                    datetime('now','localtime'), datetime('now','localtime'), datetime('now','localtime'))
        """, (symbol, qty, avg_cost, trigger_price, ret, rule, reason))

    def get_pending_exits(self, status: str = None, limit: int = 100) -> list:
        """按状态查待确认出场（status=None 返回全部），新→旧。返回 dict 列表。"""
        if not self._ensure_conn():
            return []
        if status:
            self.cursor.execute("""
                SELECT * FROM pending_exits WHERE status = ?
                 ORDER BY updated_at DESC LIMIT ?""", (status, limit))
        else:
            self.cursor.execute(
                "SELECT * FROM pending_exits ORDER BY updated_at DESC LIMIT ?", (limit,))
        return self._pe_rows()

    def set_pending_exit_intel(self, pe_id: int, intel_json: str):
        if not self._ensure_conn():
            return
        self.cursor.execute("""
            UPDATE pending_exits
               SET intel_json = ?, intel_at = datetime('now','localtime'),
                   updated_at = datetime('now','localtime')
             WHERE id = ?
        """, (intel_json, pe_id))

    def decide_pending_exit(self, pe_id: int, decision: str):
        """人工决策：decision ∈ {'sold','kept'}。返回该记录 dict（不存在/非 pending 返回 None）。"""
        if not self._ensure_conn():
            return None
        self.cursor.execute(
            "SELECT * FROM pending_exits WHERE id = ? AND status = 'pending'", (pe_id,))
        rows = self._pe_rows()
        if not rows:
            return None
        self.cursor.execute("""
            UPDATE pending_exits
               SET status = ?, decided_at = datetime('now','localtime'),
                   updated_at = datetime('now','localtime')
             WHERE id = ?
        """, (decision, pe_id))
        rows[0]['status'] = decision
        return rows[0]

    def expire_stale_pending_exits(self, active_symbols: list) -> int:
        """出场扫描后调用：触发条件已不再成立（反弹回来了/持仓已卖出）的 pending 记录自动作废。
        active_symbols 为本轮扫描仍触发出场的股票列表。返回作废条数。"""
        if not self._ensure_conn():
            return 0
        act = [s.upper() for s in active_symbols]
        ph = ','.join('?' * len(act)) if act else "''"
        self.cursor.execute(f"""
            UPDATE pending_exits
               SET status = 'expired', updated_at = datetime('now','localtime')
             WHERE status = 'pending' AND UPPER(symbol) NOT IN ({ph})
        """, act)
        return self.cursor.rowcount

    def get_orders(self, symbol=None, limit=50, since_days: int | None = None):
        if not self._ensure_conn():
            return []
        sql = "SELECT * FROM orders"
        params = []
        conditions = []
        if symbol:
            conditions.append("UPPER(symbol) = UPPER(?)")
            params.append(symbol)
        if since_days is not None:
            conditions.append("created_at >= datetime('now', ?)")
            params.append(f'-{int(since_days)} days')
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def get_old_orders(self, days: int = 30) -> list:
        """返回超过 days 天的订单，供清理任务先备份/预览后再精确删除。"""
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT *
              FROM orders
             WHERE created_at < datetime('now', ?)
             ORDER BY created_at ASC
        """, (f'-{int(days)} days',))
        return self.cursor.fetchall()

    def delete_orders_by_ids(self, order_ids: list[int]) -> int:
        """按 DB 主键删除已备份的订单，避免清理预览与实际删除的范围漂移。"""
        if not self._ensure_conn() or not order_ids:
            return 0
        placeholders = ','.join('?' for _ in order_ids)
        self.cursor.execute(
            f"DELETE FROM orders WHERE id IN ({placeholders})",
            [int(order_id) for order_id in order_ids],
        )
        return self.cursor.rowcount

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
        now = _now_cst()
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
        now = _now_cst()
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
        cutoff = (datetime.now(_CST) - timedelta(minutes=timeout_minutes)).strftime('%Y-%m-%d %H:%M:%S')
        now = _now_cst()
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

    def delete_old_task_runs(self, days: int = 3, dry_run: bool = False) -> int:
        """删除 started_at 早于 now-days 的执行记录（保护 running 状态，避免误删在途任务）。
        started_at 存为 '%Y-%m-%d %H:%M:%S' 字符串，字典序即时间序，可直接比较。
        返回（将）删除的条数。"""
        if not self._ensure_conn():
            return 0
        cutoff = (datetime.now(_CST) - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')
        where = "started_at < ? AND status != 'running'"
        self.cursor.execute(f"SELECT COUNT(*) FROM task_runs WHERE {where}", (cutoff,))
        n = self.cursor.fetchone()[0]
        if not dry_run and n:
            self.cursor.execute(f"DELETE FROM task_runs WHERE {where}", (cutoff,))
        return n

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

    # ---------- social_mentions（社区热度：Reddit/StockTwits 采集样本）----------

    def add_social_mentions(self, rows: list[dict]) -> int:
        """批量写入社区热度样本。rows 字段对齐表结构，extra 传 dict 会自动 JSON 序列化。"""
        if not rows or not self._ensure_conn():
            return 0
        import json as _json
        payload = []
        for r in rows:
            extra = r.get('extra')
            if isinstance(extra, (dict, list)):
                extra = _json.dumps(extra, ensure_ascii=False)
            payload.append((
                r['symbol'].upper(), r['source'], r['trade_date'],
                r.get('mentions'), r.get('rank'), r.get('upvotes'),
                r.get('bull_cnt'), r.get('bear_cnt'), extra,
            ))
        self.cursor.executemany("""
            INSERT INTO social_mentions
                (symbol, source, trade_date, mentions, rank, upvotes, bull_cnt, bear_cnt, extra)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, payload)
        self.conn.commit()
        return len(payload)

    def get_social_daily(self, source: str, days: int = 14) -> list:
        """按 (symbol, trade_date) 聚合的日度序列（盘中多次采样取当日最大提及数），供 z-score 基线。"""
        if not self._ensure_conn():
            return []
        self.cursor.execute("""
            SELECT symbol, trade_date,
                   MAX(mentions)  AS mentions,
                   MIN(rank)      AS best_rank,
                   MAX(upvotes)   AS upvotes,
                   MAX(bull_cnt)  AS bull_cnt,
                   MAX(bear_cnt)  AS bear_cnt
              FROM social_mentions
             WHERE source = ?
               AND trade_date >= DATE('now', ?)
             GROUP BY symbol, trade_date
             ORDER BY symbol, trade_date
        """, (source, f'-{int(days)} days'))
        return self.cursor.fetchall()

    def prune_social_mentions(self, keep_days: int = 90) -> int:
        """清理 keep_days 之前的原始采样（日度聚合足够做基线，原始样本无需长留）。"""
        if not self._ensure_conn():
            return 0
        self.cursor.execute(
            "DELETE FROM social_mentions WHERE trade_date < DATE('now', ?)",
            (f'-{int(keep_days)} days',))
        n = self.cursor.rowcount
        self.conn.commit()
        return n

    # ---------- close ----------

    def close(self):
        if self.conn:
            self.conn.close()
            print("数据库连接已关闭")
