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

# 调度器单实例锁：跨进程互斥，保证同一时刻只有一个进程真正启动 APScheduler。
# 防止 uvicorn --reload 残留的孤儿 worker / 多开实例各起一个调度器导致定时任务重复执行
# （尤其 auto_trader 重复下单）。抢不到锁的进程只服务 API、不跑调度。
_SCHED_LOCK_PATH = os.path.join(ROOT, 'data', '.scheduler.lock')

# 预设任务（混合时区）：
#   美股开盘时段任务（见 NY_TASKS）cron 按【美东时间】书写，trigger 用 America/New_York，自动夏/冬令时；
#   A 股 / 收盘后批处理 / 维护任务 cron 按【北京时间】书写，trigger 用 Asia/Shanghai。
DEFAULT_TASKS = [
    {
        'task_id':  'dry_run',
        'name':     '模拟预览（Dry-run，不下单）',
        'command':  f'{PYTHON} auto_trader.py --dry-run',
        'cron_expr': '30 8 * * 1-5',   # 美东 8:30（纽约时区），开盘前预览，比 auto_trader 早 30 分钟
        'enabled':  False,
    },
    {
        'task_id':  'auto_trader',
        'name':     '自动交易（OPG 下单）',
        'command':  f'{PYTHON} auto_trader.py --run',
        'cron_expr': '0 9 * * 1-5',    # 美东 9:00（纽约时区，自动夏/冬令时），开盘前 30 分钟提交 OPG
        'enabled':  False,
    },
    {
        'task_id':  'stop_exits',
        'name':     '止损出场（开盘后 DAY 单）',
        'command':  f'{PYTHON} auto_trader.py --run --exits-only',
        'cron_expr': '35 9 * * 1-5',   # 美东 9:35（纽约时区），开盘后 5 分钟
        'enabled':  True,
        'description': '开盘后用 DAY 市价单执行止损/卖出出场（不买入）。盘前 OPG 出场单在模拟盘不撮合、易漏卖，此任务确保止损真正成交。',
    },
    {
        'task_id':  'confirm_fills',
        'name':     '成交确认（OPG 回报）',
        'command':  f'{PYTHON} confirm_fills.py',
        'cron_expr': '35 10 * * 1-5',  # 美东 10:35（纽约时区），开盘约 1 小时后对账
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
        'task_id':  'market_scan',
        'name':     '市场扫描预热(因子裸值表)',
        'command':  f'{PYTHON} -m web.services.factor_svc --universe ai --top 50',
        'cron_expr': '45 8 * * 1-5',   # 美东 8:45（纽约时区），开盘前预热市场扫描缓存
        'enabled':  False,
        'description': '全池逐股算因子裸值+覆盖率,写文件缓存供市场扫描页秒开,避免用户点开时现场跑',
    },
    {
        'task_id':  'ai_scan_intraday',
        'name':     '美股AI追踪盘中刷新（实时价覆盖）',
        'command':  f'{PYTHON} -m web.services.ai_tracker_svc --intraday',
        'cron_expr': '*/5 9-16 * * 1-5',   # 美东 9:00~16:55 每 5 分钟（纽约时区，自动夏/冬令时）
        'enabled':  False,
        'description': '交易时段每 5 分钟用 yfinance 实时价覆盖 AI 池的 price/动量列，'
                       '让「产业图谱」热力图盘中跟着动（重活基本面/评分沿用缓存不重算，单次约 5 秒）。'
                       '函数内含 9:30–16:00 时段闸门，盘前/盘后/午间多余的 cron tick 自动跳过。',
    },
    {
        'task_id':  'core_intel_cards',
        'name':     '核心票每日情报卡（AI+联网检索）',
        'command':  f'{PYTHON} -m web.services.intel_svc --core-cards',
        'cron_expr': '15 8 * * 1-5',   # 美东 8:15（纽约时区），开盘前生成，「盘前扫描 → 情报卡」查看
        'enabled':  False,
        'description': '对盘前清单 core 组每只票做 AI 联网检索（隔夜要闻/产业链同行/华尔街/催化剂 + 论点检查）。'
                       '默认走本机 Codex CLI 登录态（无 API 费用），失败再尝试 Claude CLI；不自动调用付费 API。'
                       '默认关闭：页面手动点「生成情报卡」即可；想每天盘前自动生成再开启此任务。',
    },
    {
        'task_id':  'ai_discover',
        'name':     'AI池吐故纳新（发现新标的 + 汰旧建议）',
        'command':  f'{PYTHON} -m web.services.ai_tracker_svc --discover',
        'cron_expr': '0 8 1 * *',      # 北京 每月 1 日 08:00（美股收盘后、A股开盘前的低活跃时段）
        'enabled':  False,
        'description': '进新：扫描 sp500+ndx+russell2000 中 $2B–$500B 的 AI 产业链新标的（按行业关键词），'
                       '写入待审核列表，在「美股AI追踪 → 清单管理」人工批准/忽略（新成员默认仅观察，不进实盘优先池）。'
                       '汰旧：对池内成员做持续弱势检查（近 2 个月 RS 池内后 20% 且破 EMA21），生成移出建议，'
                       '同样人工确认——绝不自动删。',
    },
    {
        'task_id':  'ai_ipo_discover',
        'name':     'AI池新股发现（Claude+联网检索）',
        'command':  f'{PYTHON} -m web.services.intel_svc --ipo-discover',
        'cron_expr': '30 8 1 * *',     # 北京 每月 1 日 08:30（指数扫描后 30 分钟）
        'enabled':  False,
        'description': '用 Claude 联网检索近 6-12 个月上市/热门的 AI 硬件产业链新票（补指数扫描的 IPO 盲区：'
                       'ARM/ALAB/CRWV/NBIS 这类上市初期不在任何指数里），结果同样写入待审核列表人工把关。'
                       '默认走本机 claude CLI 订阅额度（无 API 费用）。',
    },
    {
        'task_id':  'social_buzz',
        'name':     '社区热度采集（Reddit/StockTwits）',
        'command':  f'{PYTHON} -m web.services.social_svc --collect',
        'cron_expr': '0 8,11,14,17 * * 1-5',   # 美东 盘前 8:00 / 盘中 11:00、14:00 / 盘后 17:00
        'enabled':  False,
        'description': '采集 AI 池 + 持仓的 Reddit 提及热度（ApeWisdom 聚合）与 StockTwits 多空情绪，'
                       '入库并按 7 日基线算 z-score 异动，「情报中心 → 社区热度」查看。'
                       '信号是相对自身基线的异动而非绝对热度，开启后需积累约一周基线才有 z-score。'
                       '可选增强：在 .env 配 REDDIT_CLIENT_ID/SECRET（reddit.com/prefs/apps 免费建 script 应用）'
                       '可额外抓热帖标题样本；不配置只是没有标题列，提及数不受影响。纯观察层，不参与交易信号。',
    },
    {
        'task_id':  'astock_update',
        'name':     'A股盘中实时刷新 + 扫描(主题板块)',
        'command':  f'{PYTHON} -m web.services.astock_momentum_svc --mode theme',
        'cron_expr': '*/10 9-11,13-15 * * 1-5',  # 北京 交易时段每 10 分钟（含开盘 9:30 起 / 上午~11:30 / 下午13:00~15:00；盘前/午休/盘后空转由 topup 新鲜度校验自动跳过）
        'enabled':  False,
        'description': 'A股交易时段每 10 分钟用实时快照覆盖当日 bar 并重建主题扫描缓存；含开盘段(9:30起)；15:00 收盘那次即收盘价，次日 astock_refresh 用正式日线复核。单次扫描约 20 秒',
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
        'name':     '数据清理（日志3天+订单30天）',
        'command':  f'{PYTHON} -m tools.clean_logs --days 3',
        'cron_expr': '0 5 * * *',       # 北京 每天 05:00（低活跃时段）
        'enabled':  False,
        'description': '删除 logs/ 下修改时间超过 3 天的日志文件及 task_runs 执行记录（保护活跃日志和 running 任务）；同时清理 orders 表中 30 天前的订单，删除前自动备份到 data/order_cleanup_backups/，可审计、可恢复。--days N 只控制日志和 task_runs，订单固定保留 30 天。',
    },
]

# 依赖美股交易时段的任务：cron 按美东时间书写，trigger 用 America/New_York（自动夏/冬令时）。
# 其余任务（A 股 / 收盘后批处理 / 维护）用 Asia/Shanghai。
NY_TASKS = {'dry_run', 'auto_trader', 'stop_exits', 'confirm_fills', 'market_scan',
            'core_intel_cards', 'ai_scan_intraday', 'social_buzz'}

# 默认任务 cron 调整（非 UTC 迁移）：task_id → 需被替换的旧默认 cron。
# DB 里命中此旧值时升级到 DEFAULT_TASKS 当前 cron；用户手改过的自定义 cron 不受影响。
_REPLACE_OLD_CRON = {
    'astock_update': '0,30 10-11,13-15 * * 1-5',  # 每 30 分钟 → 每 10 分钟(且补开盘 9:30 段)
}

# 历史 cron 自动迁移：{task_id: {旧值: 新值}}，**按 task_id 限定**避免跨任务误伤
# （旧版用全局 cron 字符串匹配，把 auto_trader 的目标值 '0 22 * * 1-5' 误当 UTC 迁成
#  '0 6 * * 2-6'，导致 OPG 单跑到收盘后——此处按 task_id 隔离根治）。
# 仅当 DB 现值精确命中下列旧值才迁移；用户自定义过的 cron 不受影响。
_MIGRATE = {
    # 美股任务：旧北京时间 / 旧 UTC / 被旧 bug 改坏值 → 新美东时间（纽约时区）
    'auto_trader':        {'0 22 * * 1-5': '0 9 * * 1-5',
                           '0 6 * * 2-6':  '0 9 * * 1-5',
                           '0 14 * * 1-5': '0 9 * * 1-5'},
    'confirm_fills':      {'35 22 * * 1-5': '35 10 * * 1-5',
                           '35 14 * * 1-5': '35 10 * * 1-5'},
    'stop_exits':         {'35 21 * * 1-5': '35 9 * * 1-5'},
    'dry_run':            {'30 21 * * 1-5': '30 8 * * 1-5'},
    'market_scan':        {'45 21 * * 1-5': '45 8 * * 1-5'},
    # 收盘后批处理：保持北京时间，仅迁移更老的 UTC 残留值
    'sp500_scanner':      {'0 22 * * 1-5': '0 6 * * 2-6'},
    'data_update':        {'0 23 * * 1-5': '0 7 * * 2-6'},
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
        self._lock_fp = None             # 单实例文件锁的 fd（持有期间不关闭）

    def _acquire_singleton_lock(self) -> bool:
        """抢占跨进程单实例锁（flock 非阻塞）。抢到返回 True 并持有 fd 直到进程退出。"""
        import fcntl
        try:
            fp = open(_SCHED_LOCK_PATH, 'w')
            fcntl.flock(fp.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fp.write(str(os.getpid()))
            fp.flush()
            self._lock_fp = fp          # 保持引用，进程存活期间不释放
            return True
        except (OSError, BlockingIOError):
            try:
                fp.close()
            except Exception:
                pass
            return False

    def _get_db(self) -> Database:
        if not hasattr(self._local, 'db'):
            self._local.db = Database()
            self._local.db.connect()
        return self._local.db

    def start(self):
        """启动调度器，加载 DB 中的任务（受单实例锁保护，抢不到则只服务 API 不调度）"""
        import logging
        if not self._acquire_singleton_lock():
            logging.getLogger(__name__).warning(
                "[scheduler] 调度锁被其它进程持有（多开/--reload 残留 worker？），"
                "本进程仅服务 API，不启动调度器，避免定时任务重复执行")
            return
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
        try:
            if self.scheduler.running:
                self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        # 释放单实例锁
        if self._lock_fp is not None:
            try:
                self._lock_fp.close()   # 关闭即释放 flock
            except Exception:
                pass
            self._lock_fp = None

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
                migrate = _MIGRATE.get(t['task_id'], {})
                old_name = existing[t['task_id']][1]
                old_command = existing[t['task_id']][2]
                if (t['task_id'] == 'log_cleanup'
                        and old_name == '日志清理（删除3天前日志+执行记录）'
                        and old_command == t['command']
                        and old_cron == t['cron_expr']):
                    # 仅刷新未被用户自定义过的旧默认名称；enabled 状态保持不变。
                    db.upsert_task(
                        task_id=t['task_id'],
                        name=t['name'],
                        command=t['command'],
                        cron_expr=t['cron_expr'],
                        enabled=existing[t['task_id']][4],
                    )
                elif old_cron in migrate:
                    db.upsert_task(
                        task_id=t['task_id'],
                        name=t['name'],
                        command=t['command'],
                        cron_expr=migrate[old_cron],
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
        tz = 'America/New_York' if task_id in NY_TASKS else 'Asia/Shanghai'
        trigger = CronTrigger(
            minute=mn, hour=hr, day=dm, month=mo,
            day_of_week=re.sub(r'\d+', lambda m: str((int(m.group()) - 1) % 7), dw) if dw != '*' else dw,
            timezone=tz,
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
                'tz':         'America/New_York' if task_id in NY_TASKS else 'Asia/Shanghai',
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
