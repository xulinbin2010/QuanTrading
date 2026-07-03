"""单股回测服务层：异步运行 EMA21 补仓策略 + 对照（RSMomentum / Buy&Hold / SPY）。"""
from __future__ import annotations

import sys
import os
import json
import time
import uuid
import logging
import threading
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_logger = logging.getLogger(__name__)

_tasks: dict[str, dict] = {}
_lock = threading.Lock()
_history_loaded = False

_RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'single_backtest_results',
)


# ── 持久化 ────────────────────────────────────────────────────

def _save_task(task_id: str, task: dict):
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    path = os.path.join(_RESULTS_DIR, f'{task_id}.json')
    payload = {
        'task_id':    task_id,
        'status':     task['status'],
        'created_at': task['created_at'],
        'params':     task['params'],
        'result':     task['result'],
        'error':      task.get('error'),
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
                        'progress':   1.0 if data['status'] == 'completed' else 0.0,
                        'result':     data.get('result'),
                        'error':      data.get('error'),
                        'created_at': data['created_at'],
                        'params':     data.get('params', {}),
                    }
        except Exception:
            pass


# ── 任务执行 ──────────────────────────────────────────────────

def submit(params: dict) -> str:
    """提交一次单股回测，返回 task_id"""
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
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    with _lock:
        _tasks[task_id]['status'] = 'running'

    short_id = task_id[:8]
    sym = params.get('symbol', '?')
    _logger.info(f'[单股回测 {short_id}] 启动 {sym} {params}')

    try:
        from strategies.ema_pullback import run_ema_pullback_backtest
        result = run_ema_pullback_backtest(
            symbol             = params['symbol'].upper(),
            start              = params['start'],
            end                = params['end'],
            initial_cash       = float(params.get('initial_cash', 60_000)),
            base_pct           = float(params.get('base_pct', 0.50)),
            add_size_mult      = float(params.get('add_size_mult', 0.50)),
            max_adds           = int(params.get('max_adds', 2)),
            touch_tol          = float(params.get('touch_tol', 0.01)),
            sell_atr_mult      = float(params.get('sell_atr_mult', 2.5)),
            stop_ema_period    = int(params.get('stop_ema_period', 50)),
            ema_fast           = int(params.get('ema_fast', 21)),
            entry_mode         = str(params.get('entry_mode', 'rs_momentum')),
            allow_margin       = bool(params.get('allow_margin', False)),
            max_leverage       = float(params.get('max_leverage', 1.0)),
            margin_rate        = float(params.get('margin_rate', 0.06)),
            retrace_levels       = params.get('retrace_levels'),
            retrace_max_leverage = float(params.get('retrace_max_leverage', 2.0)),
            retrace_rsi_boost    = bool(params.get('retrace_rsi_boost', False)),
        )

        s = result['summaries']['ema_pullback']
        _logger.info(
            f'[单股回测 {short_id}] 完成 {sym} '
            f'EMA21补仓 ret={s["total_return"]:+.1%} '
            f'maxDD={s["max_drawdown"]:+.1%} trades={s["num_trades"]}'
        )

        with _lock:
            _tasks[task_id]['result']   = result
            _tasks[task_id]['status']   = 'completed'
            _tasks[task_id]['progress'] = 1.0
        _save_task(task_id, _tasks[task_id])
    except Exception as e:
        _logger.exception(f'[单股回测 {short_id}] 失败：{e}')
        with _lock:
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['error']  = str(e)
        try:
            _save_task(task_id, _tasks[task_id])
        except Exception:
            pass


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
    if task is None:
        return None
    if task['status'] != 'completed':
        return None
    return task['result']


def get_history(limit: int = 30) -> list[dict]:
    """最近 N 次任务的摘要列表（用于 UI 历史栏）。"""
    _load_history_from_disk()
    items = []
    with _lock:
        for tid, task in _tasks.items():
            r = task.get('result') or {}
            s = (r.get('summaries') or {}).get('ema_pullback') or {}
            items.append({
                'task_id':      tid,
                'status':       task['status'],
                'created_at':   task['created_at'],
                'symbol':       (task['params'] or {}).get('symbol'),
                'start':        (task['params'] or {}).get('start'),
                'end':          (task['params'] or {}).get('end'),
                'total_return': s.get('total_return'),
                'sharpe':       s.get('sharpe'),
            })
    items.sort(key=lambda x: x['created_at'], reverse=True)
    return items[:limit]
