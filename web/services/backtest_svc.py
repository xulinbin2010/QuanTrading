"""回测服务层：异步运行 run_backtest()，管理任务状态，结果持久化到磁盘"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import uuid
import threading
import time
import json
from typing import Optional

_tasks: dict[str, dict] = {}
_lock = threading.Lock()
_history_loaded = False

_RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'backtest_results'
)


# ── 文件持久化 ─────────────────────────────────────────────

def _save_task(task_id: str, task: dict):
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    path = os.path.join(_RESULTS_DIR, f'{task_id}.json')
    payload = {
        'task_id':    task_id,
        'status':     task['status'],
        'created_at': task['created_at'],
        'params':     task['params'],
        'result':     task['result'],
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _load_history_from_disk():
    global _history_loaded
    with _lock:
        if _history_loaded:
            return
        _history_loaded = True
    if not os.path.exists(_RESULTS_DIR):
        return
    for fname in sorted(os.listdir(_RESULTS_DIR)):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(_RESULTS_DIR, fname), encoding='utf-8') as f:
                data = json.load(f)
            task_id = data['task_id']
            with _lock:
                if task_id not in _tasks:
                    _tasks[task_id] = {
                        'status':     data['status'],
                        'progress':   1.0,
                        'result':     data['result'],
                        'error':      None,
                        'created_at': data['created_at'],
                        'params':     data.get('params', {}),
                    }
        except Exception:
            pass


# ── 任务执行 ───────────────────────────────────────────────

def submit_backtest(params: dict) -> str:
    task_id = str(uuid.uuid4())
    with _lock:
        _tasks[task_id] = {
            'status':     'pending',
            'progress':   0.0,
            'result':     None,
            'error':      None,
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'params':     params,
        }
    t = threading.Thread(target=_run, args=(task_id, params), daemon=True)
    t.start()
    return task_id


def _run(task_id: str, params: dict):
    with _lock:
        _tasks[task_id]['status'] = 'running'
    try:
        from tests.backtest_rs import run_backtest
        result = run_backtest(**params)
        with _lock:
            _tasks[task_id]['result']   = result
            _tasks[task_id]['status']   = 'completed'
            _tasks[task_id]['progress'] = 1.0
        _save_task(task_id, _tasks[task_id])
    except Exception as e:
        with _lock:
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['error']  = str(e)


def get_status(task_id: str) -> Optional[dict]:
    _load_history_from_disk()
    task = _tasks.get(task_id)
    if task is None:
        return None
    return {
        'task_id':  task_id,
        'status':   task['status'],
        'progress': task['progress'],
        'error':    task.get('error'),
    }


def get_result(task_id: str) -> Optional[dict]:
    _load_history_from_disk()
    task = _tasks.get(task_id)
    if task is None or task['status'] != 'completed':
        return None
    return task['result']


def get_history() -> list[dict]:
    _load_history_from_disk()
    with _lock:
        items = []
        for tid, task in _tasks.items():
            summary = task.get('result', {}).get('summary', {}) if task['result'] else {}
            factors = task['params'].get('factors') or []
            trades = task.get('result', {}).get('trades', []) if task['result'] else []
            days_list = [t['days_held'] for t in trades if isinstance(t.get('days_held'), (int, float))]
            avg_days = round(sum(days_list) / len(days_list)) if days_list else None
            items.append({
                'task_id':      tid,
                'status':       task['status'],
                'created_at':   task['created_at'],
                'universe':     summary.get('universe', task['params'].get('universe', '')),
                'factors':      factors,
                'total_return': summary.get('total_return'),
                'sharpe':       summary.get('sharpe'),
                'bt_start':     summary.get('bt_start'),
                'bt_end':       summary.get('bt_end'),
                'avg_days':     avg_days,
            })
        items.sort(key=lambda x: x['created_at'], reverse=True)
        return items
