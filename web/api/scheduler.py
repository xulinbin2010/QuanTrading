"""任务调度 API 路由"""
import re
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from web.services.scheduler_svc import get_scheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo


def _posix_dow_to_aps(dw: str) -> str:
    """POSIX cron 星期编号（0/7=周日, 1=周一..6=周六）→ APScheduler（0=周一..6=周日）"""
    if dw == '*':
        return dw
    return re.sub(r'\d+', lambda m: str((int(m.group()) - 1) % 7), dw)

router = APIRouter(prefix='/api/scheduler', tags=['scheduler'])


class TaskUpsert(BaseModel):
    task_id: str
    name: str
    command: str
    cron_expr: str
    enabled: bool = True


@router.get('/tasks')
def list_tasks():
    try:
        return get_scheduler().get_tasks()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"调度器获取任务失败：{e}")


@router.post('/tasks')
def upsert_task(body: TaskUpsert):
    return get_scheduler().upsert_task(
        task_id=body.task_id,
        name=body.name,
        command=body.command,
        cron_expr=body.cron_expr,
        enabled=body.enabled,
    )


@router.delete('/tasks/{task_id}')
def delete_task(task_id: str):
    get_scheduler().delete_task(task_id)
    return {'status': 'ok'}


@router.post('/tasks/{task_id}/run-now')
def run_now(task_id: str):
    try:
        return get_scheduler().run_now(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get('/runs')
def get_runs(task_id: str = Query(None), limit: int = Query(50, le=200)):
    return get_scheduler().get_runs(task_id=task_id, limit=limit)


@router.get('/runs/{run_id}/log')
def get_log(run_id: int):
    log = get_scheduler().get_run_log(run_id)
    return {'run_id': run_id, 'log': log}


@router.get('/cron-preview')
def cron_preview(expr: str = Query(...), count: int = Query(5, le=10)):
    """返回 cron 表达式（北京时间）未来 N 次执行时间"""
    try:
        parts = expr.strip().split()
        if len(parts) != 5:
            return {'times': [], 'error': '需要5个字段的 cron 表达式'}
        mn, hr, dm, mo, dw = parts
        trigger = CronTrigger(
            minute=mn, hour=hr, day=dm, month=mo,
            day_of_week=_posix_dow_to_aps(dw),
            timezone='Asia/Shanghai',
        )
        cst = ZoneInfo('Asia/Shanghai')
        t = datetime.now(tz=timezone.utc)
        times = []
        for _ in range(count):
            t = trigger.get_next_fire_time(None, t)
            if t is None:
                break
            times.append(t.astimezone(cst).strftime('%Y-%m-%d %H:%M'))
            t = t + timedelta(minutes=1)
        return {'times': times}
    except Exception as e:
        return {'times': [], 'error': str(e)}
