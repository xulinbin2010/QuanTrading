"""回测服务层：异步运行 run_backtest()，管理任务状态"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import uuid
import threading
import time
from typing import Optional

# 内存存储（key: task_id, value: {status, progress, result, error, created_at}）
_tasks: dict[str, dict] = {}


def submit_backtest(params: dict) -> str:
    """提交回测任务，返回 task_id，在后台线程执行"""
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        'status': 'pending',
        'progress': 0.0,
        'result': None,
        'error': None,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'params': params,
    }
    t = threading.Thread(target=_run, args=(task_id, params), daemon=True)
    t.start()
    return task_id


def _run(task_id: str, params: dict):
    _tasks[task_id]['status'] = 'running'
    try:
        from tests.backtest_rs import run_backtest
        result = run_backtest(**params)
        _tasks[task_id]['result'] = result
        _tasks[task_id]['status'] = 'completed'
        _tasks[task_id]['progress'] = 1.0
    except Exception as e:
        _tasks[task_id]['status'] = 'failed'
        _tasks[task_id]['error'] = str(e)


def get_status(task_id: str) -> Optional[dict]:
    task = _tasks.get(task_id)
    if task is None:
        return None
    return {
        'task_id': task_id,
        'status': task['status'],
        'progress': task['progress'],
        'error': task.get('error'),
    }


def get_result(task_id: str) -> Optional[dict]:
    task = _tasks.get(task_id)
    if task is None or task['status'] != 'completed':
        return None
    return task['result']


def get_history() -> list[dict]:
    """返回历史回测摘要列表（按创建时间降序）"""
    items = []
    for tid, task in _tasks.items():
        summary = task.get('result', {}).get('summary', {}) if task['result'] else {}
        factors = task['params'].get('factors') or []
        items.append({
            'task_id': tid,
            'status': task['status'],
            'created_at': task['created_at'],
            'universe': summary.get('universe', task['params'].get('universe', '')),
            'factors': factors,          # 空列表 = 默认 RSMomentum
            'total_return': summary.get('total_return'),
            'sharpe': summary.get('sharpe'),
            'bt_start': summary.get('bt_start'),
            'bt_end': summary.get('bt_end'),
        })
    items.sort(key=lambda x: x['created_at'], reverse=True)
    return items
