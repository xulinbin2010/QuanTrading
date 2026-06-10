"""任务调度服务层：APScheduler + subprocess 执行器"""
from __future__ import annotations
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import subprocess
import threading
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from core.database import Database

# 项目根目录
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
PYTHON = os.path.join(ROOT, '.venv', 'bin', 'python')
if not os.path.exists(PYTHON):
    PYTHON = sys.executable

# 预设任务（北京时间）
DEFAULT_TASKS = [
    {
        'task_id':  'dry_run',
        'name':     '模拟预览（Dry-run，不下单）',
        'command':  f'{PYTHON} auto_trader.py --dry-run',
        'cron_expr': '30 21 * * 1-5',  # 北京 21:30，比正式下单早 30 分钟
        'enabled':  False,
    },
    {
        'task_id':  'auto_trader',
        'name':     '自动交易（OPG 下单）',
        'command':  f'{PYTHON} auto_trader.py --run',
        'cron_expr': '0 22 * * 1-5',   # 北京 22:00 = 美东 9:00（非夏令时）/ 21:00（夏令时）
        'enabled':  False,
    },
    {
        'task_id':  'stop_exits',
        'name':     '止损出场（开盘后 DAY 单）',
        'command':  f'{PYTHON} auto_trader.py --run --exits-only',
        'cron_expr': '35 21 * * 1-5',  # 北京 21:35 = 美东 9:35（夏令时）开盘后5分钟
        'enabled':  True,
        'description': '开盘后用 DAY 市价单执行止损/卖出出场（不买入）。盘前 OPG 出场单在模拟盘不撮合、易漏卖，此任务确保止损真正成交。注：cron 按美东夏令时设定，冬令时需后移1小时。',
    },
    {
        'task_id':  'confirm_fills',
        'name':     '成交确认（OPG 回报）',
        'command':  f'{PYTHON} confirm_fills.py',
        'cron_expr': '35 22 * * 1-5',  # 北京 22:35 = 美东 10:35（夏令时），开盘1小时后对账
        'enabled':  True,              # 与 stop_exits 配套：买单+DAY出场单成交后回写 DB，避免订单状态长期失真
    },
    {
        'task_id':  'sp500_scanner',
        'name':     '选股扫描（收盘后）',
        'command':  f'{PYTHON} sp500_scanner.py --top 20',
        'cron_expr': '0 6 * * 2-6',    # 北京 周二至六 06:00（= 美东周一至五 22:00 UTC 次日）
        'enabled':  False,
    },
    {
        'task_id':  'data_update',
        'name':     '历史数据更新',
        'command':  f'{PYTHON} -m core.data_store --universe sp500',
        'cron_expr': '0 7 * * 2-6',    # 北京 周二至六 07:00
        'enabled':  False,
    },
    {
        'task_id':  'data_health_check',
        'name':     '数据健康检查与修复',
        'command':  f'{PYTHON} -m tools.data_health --fix --no-volume',
        'cron_expr': '30 7 * * 2-6',   # 北京 周二至六 07:30（数据更新后 30 分钟）
        'enabled':  False,
        'description': '扫描所有缓存数据，自动修复历史缺失/价格偏移/退市等问题',
    },
    {
        'task_id':  'production_signals',
        'name':     '生产信号扫描(因子看板 top10)',
        'command':  f'{PYTHON} -m web.services.production_signal_svc',
        'cron_expr': '50 21 * * 1-5',  # 北京 周一至五 21:50(美东 08:50,开盘前 40 分钟)
        'enabled':  False,
        'description': '复用 auto_trader.scan_signals 全套(双路扫描+过滤+排名),写 top10 缓存供因子看板「生产信号」展示',
    },
    {
        'task_id':  'astock_update',
        'name':     'A股盘中实时刷新 + 扫描(主题板块)',
        'command':  f'{PYTHON} -m web.services.astock_momentum_svc --mode theme',
        'cron_expr': '0,30 10-11,13-15 * * 1-5',  # 北京 交易时段每 30 分钟（10:00~11:30 / 13:00~15:30）
        'enabled':  False,
        'description': 'A股交易时段每 30 分钟用实时快照覆盖当日 bar 并重建主题扫描缓存；15:30 那次即收盘价，次日 astock_refresh 用正式日线复核',
    },
    {
        'task_id':  'astock_refresh',
        'name':     'A股次日复核（正式日线覆盖）',
        'command':  f'{PYTHON} -m web.services.astock_momentum_svc --mode theme --refresh',
        'cron_expr': '30 7 * * 2-6',   # 北京 周二至六 07:30，复核前一交易日数据
        'enabled':  False,
        'description': '次日早重拉 sina 正式前复权日线，覆盖前一日盘后快照补的原始价 bar，保证数据最终准确',
    },
    {
        'task_id':  'log_cleanup',
        'name':     '日志清理（删除3天前日志）',
        'command':  f'{PYTHON} -m tools.clean_logs --days 3',
        'cron_expr': '0 5 * * *',       # 北京 每天 05:00（低活跃时段）
        'enabled':  False,
        'description': '删除 logs/ 下修改时间超过 3 天的日志文件（data_health_*.json、trading.log 轮转文件等），保护正在写入的 trading.log。要改保留天数，编辑 command 里的 --days N。',
    },
]

# 默认任务 cron 调整（非 UTC 迁移）：task_id → 需被替换的旧默认 cron。
# DB 里命中此旧值时升级到 DEFAULT_TASKS 当前 cron；用户手改过的自定义 cron 不受影响。
_REPLACE_OLD_CRON = {
    'astock_update': '30 16 * * 1-5',  # 盘后单次 → 盘中每 30 分钟实时刷新
}

# 旧 UTC cron → 新北京时间 cron（自动迁移）
_UTC_TO_CST = {
    '0 14 * * 1-5':  '0 22 * * 1-5',
    '35 14 * * 1-5': '35 22 * * 1-5',
    '0 22 * * 1-5':  '0 6 * * 2-6',
    '0 23 * * 1-5':  '0 7 * * 2-6',
}


def _fmt_ts(val) -> str | None:
    """将 datetime 或 ISO 字符串统一格式化为 'YYYY-MM-DD HH:MM:SS'，None 返回 None。"""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d %H:%M:%S')
    return str(val)[:19]


class SchedulerService:
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone='Asia/Shanghai')
        self._local = threading.local()  # 每个线程独立 DB 连接，避免 sqlite3 并发 segfault
        self._lock = threading.Lock()

    def _get_db(self) -> Database:
        if not hasattr(self._local, 'db'):
            self._local.db = Database()
            self._local.db.connect()
        return self._local.db

    def start(self):
        """启动调度器，加载 DB 中的任务"""
        self._seed_defaults()
        # 清理上次服务崩溃遗留的僵尸 runs
        n = self._get_db().reap_zombie_runs(timeout_minutes=5)
        if n:
            import logging
            logging.getLogger(__name__).warning(f"[scheduler] 启动时清理 {n} 条僵尸 running 记录")
        self._reload_jobs()
        # 每分钟定期清理超时任务
        self.scheduler.add_job(
            func=self._reap_zombies,
            trigger='interval',
            minutes=1,
            id='__reap_zombies__',
            replace_existing=True,
        )
        self.scheduler.start()

    def _reap_zombies(self):
        self._get_db().reap_zombie_runs(timeout_minutes=5)

    def stop(self):
        self.scheduler.shutdown(wait=False)

    def _seed_defaults(self):
        """写入预设任务；已存在时若 cron 为旧 UTC 格式则自动迁移到北京时间"""
        db = self._get_db()
        existing = {row[0]: row for row in db.get_tasks()}
        for t in DEFAULT_TASKS:
            if t['task_id'] not in existing:
                db.upsert_task(
                    task_id=t['task_id'],
                    name=t['name'],
                    command=t['command'],
                    cron_expr=t['cron_expr'],
                    enabled=t['enabled'],
                )
            else:
                old_cron = existing[t['task_id']][3]
                if old_cron in _UTC_TO_CST:
                    db.upsert_task(
                        task_id=t['task_id'],
                        name=t['name'],
                        command=t['command'],
                        cron_expr=_UTC_TO_CST[old_cron],
                        enabled=existing[t['task_id']][4],
                    )
                elif _REPLACE_OLD_CRON.get(t['task_id']) == old_cron:
                    # 命中需替换的旧默认 cron → 升级到当前默认（同时刷新 name/description）
                    db.upsert_task(
                        task_id=t['task_id'],
                        name=t['name'],
                        command=t['command'],
                        cron_expr=t['cron_expr'],
                        enabled=existing[t['task_id']][4],
                    )

    def _reload_jobs(self):
        """从 DB 重新加载所有启用的 job"""
        self.scheduler.remove_all_jobs()
        db = self._get_db()
        for row in db.get_tasks():
            # task_id, name, command, cron_expr, enabled, created_at, updated_at
            task_id, name, command, cron_expr, enabled = row[:5]
            if enabled:
                self._add_job(task_id, command, cron_expr)

    def _add_job(self, task_id: str, command: str, cron_expr: str):
        parts = cron_expr.split()
        if len(parts) != 5:
            return
        mn, hr, dm, mo, dw = parts
        trigger = CronTrigger(
            minute=mn, hour=hr, day=dm, month=mo,
            day_of_week=re.sub(r'\d+', lambda m: str((int(m.group()) - 1) % 7), dw) if dw != '*' else dw,
            timezone='Asia/Shanghai',
        )
        self.scheduler.add_job(
            func=self._execute_task,
            trigger=trigger,
            args=[task_id, command],
            id=task_id,
            replace_existing=True,
        )

    def _execute_task(self, task_id: str, command: str, run_id: int = None):
        """在子进程执行任务，记录日志到 DB。run_id 由调用方预先创建时传入。
        注意：后台线程使用独立的 DB 连接，避免与主线程 cursor 竞争。"""
        db = Database()
        db.connect()
        if run_id is None:
            run_id = db.start_task_run(task_id)
        log_lines = []
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            pending: list[str] = []
            for line in proc.stdout:
                log_lines.append(line)
                pending.append(line)
                if len(pending) >= 10:          # 每 10 行实时写一次 DB
                    db.append_run_log(run_id, ''.join(pending))
                    pending.clear()
            if pending:                          # 剩余不足 10 行
                db.append_run_log(run_id, ''.join(pending))
            proc.wait(timeout=3600)
            exit_code = proc.returncode
        except Exception as e:
            log_lines.append(f"[SCHEDULER ERROR] {e}\n")
            exit_code = -1
        finally:
            db.finish_task_run(run_id, exit_code, ''.join(log_lines))

    # ── 公开 API ────────────────────────────────────────────

    def get_tasks(self) -> list[dict]:
        from zoneinfo import ZoneInfo
        db = self._get_db()
        rows = db.get_tasks()
        last_runs = db.get_last_runs_per_task()  # 单次批量查询，替代 N+1
        cst = ZoneInfo('Asia/Shanghai')
        result = []
        for row in rows:
            task_id, name, command, cron_expr, enabled, created_at, updated_at = row
            job = self.scheduler.get_job(task_id)
            next_run = None
            if job and job.next_run_time:
                next_run = job.next_run_time.astimezone(cst).strftime('%Y-%m-%d %H:%M 北京')
            last_run = None
            r = last_runs.get(task_id)
            if r:
                last_run = {
                    'id': r[1],
                    'started_at': _fmt_ts(r[2]),
                    'status': r[3],
                }
            result.append({
                'task_id':    task_id,
                'name':       name,
                'command':    command,
                'cron_expr':  cron_expr,
                'enabled':    bool(enabled),
                'next_run':   next_run,
                'last_run':   last_run,
            })
        return result

    def upsert_task(self, task_id: str, name: str, command: str,
                    cron_expr: str, enabled: bool) -> dict:
        db = self._get_db()
        db.upsert_task(task_id, name, command, cron_expr, enabled)
        if enabled:
            self._add_job(task_id, command, cron_expr)
        else:
            try:
                self.scheduler.remove_job(task_id)
            except Exception:
                pass
        return {'task_id': task_id, 'status': 'ok'}

    def delete_task(self, task_id: str):
        db = self._get_db()
        db.delete_task(task_id)
        try:
            self.scheduler.remove_job(task_id)
        except Exception:
            pass

    def run_now(self, task_id: str) -> dict:
        """立即在后台线程执行任务。在主线程预先写入 running 记录，保证前端刷新时立即可见状态。"""
        db = self._get_db()
        rows = db.get_tasks()
        task = next((r for r in rows if r[0] == task_id), None)
        if task is None:
            raise ValueError(f"任务 {task_id} 不存在")
        command = task[2]
        # 在主线程预先创建 run 记录（status=running），前端刷新时立即可见
        run_id = db.start_task_run(task_id)
        t = threading.Thread(
            target=self._execute_task,
            args=[task_id, command, run_id],
            daemon=True,
        )
        t.start()
        return {'task_id': task_id, 'status': 'triggered', 'run_id': run_id}

    def get_runs(self, task_id: str = None, limit: int = 50) -> list[dict]:
        db = self._get_db()
        rows = db.get_task_runs(task_id=task_id, limit=limit)
        result = []
        for r in rows:
            # id, task_id, name, started_at, finished_at, status, exit_code, duration_s
            result.append({
                'id':          r[0],
                'task_id':     r[1],
                'task_name':   r[2] or r[1],
                'started_at':  _fmt_ts(r[3]),
                'finished_at': _fmt_ts(r[4]),
                'status':      r[5],
                'exit_code':   r[6],
                'duration_s':  r[7],
            })
        return result

    def delete_run(self, run_id: int):
        self._get_db().delete_task_run(run_id)

    def get_run_log(self, run_id: int) -> str:
        return self._get_db().get_run_log(run_id)


# 全局单例
_svc: SchedulerService | None = None


def get_scheduler() -> SchedulerService:
    global _svc
    if _svc is None:
        _svc = SchedulerService()
    return _svc
