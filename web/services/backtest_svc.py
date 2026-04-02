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


def run_vix_analysis(threshold: float = 30, start: str = '2010-01-01',
                     end: str | None = None, symbol: str = 'SPY',
                     mode: str = 'spike') -> dict:
    """VIX恐慌指数回测：分析VIX触发信号后各持有周期的胜率与平均收益。

    mode='spike': VIX > threshold 当天即为信号
    mode='peak' : VIX > threshold 且当日低于昨日（峰值回落，恐慌缓解买点）
    """
    import yfinance as yf
    import pandas as pd
    import numpy as np

    if end is None:
        end = pd.Timestamp.today().strftime('%Y-%m-%d')

    vix_raw = yf.download('^VIX', start=start, end=end, auto_adjust=True, progress=False)
    spy_raw = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)

    vix_close = vix_raw['Close'].squeeze()
    spy_close = spy_raw['Close'].squeeze()

    df = pd.DataFrame({'vix': vix_close, 'spy': spy_close}).dropna()
    if df.empty:
        return {'error': '数据为空，请检查日期范围'}

    HORIZONS = [1, 3, 5, 10, 20, 30, 60]
    BUCKETS = [(20, 25), (25, 30), (30, 35), (35, 40), (40, 50), (50, float('inf'))]
    BUCKET_LABELS = ['20-25', '25-30', '30-35', '35-40', '40-50', '50+']

    for h in HORIZONS:
        df[f'ret_{h}d'] = (df['spy'].shift(-h) / df['spy'] - 1)

    if mode == 'spike':
        signal_mask = df['vix'] > threshold
    else:
        # peak: VIX > threshold 且当日开始从高点回落
        signal_mask = (df['vix'] > threshold) & (df['vix'] < df['vix'].shift(1))

    events_df = df[signal_mask].copy()

    events = []
    for date, row in events_df.iterrows():
        ev = {'date': str(date.date()), 'vix': round(float(row['vix']), 1)}
        for h in HORIZONS:
            v = row.get(f'ret_{h}d')
            ev[f'ret_{h}d'] = round(float(v) * 100, 2) if pd.notna(v) else None
        events.append(ev)
    events.sort(key=lambda x: x['date'], reverse=True)

    heatmap_win_rate: list = []
    heatmap_avg_ret: list = []
    heatmap_count: list = []

    for (lo, hi) in BUCKETS:
        hi_cond = df['vix'] < hi if hi != float('inf') else pd.Series(True, index=df.index)
        bucket_mask = signal_mask & (df['vix'] >= lo) & hi_cond
        bucket_df = df[bucket_mask]
        win_rates, avg_rets, counts = [], [], []
        for h in HORIZONS:
            valid = bucket_df[f'ret_{h}d'].dropna()
            if len(valid) == 0:
                win_rates.append(None)
                avg_rets.append(None)
                counts.append(0)
            else:
                win_rates.append(round(float((valid > 0).mean()) * 100, 1))
                avg_rets.append(round(float(valid.mean()) * 100, 2))
                counts.append(int(len(valid)))
        heatmap_win_rate.append(win_rates)
        heatmap_avg_ret.append(avg_rets)
        heatmap_count.append(counts)

    # VIX走势（用于图表）
    vix_series = [
        {'date': str(d.date()), 'vix': round(float(v), 2)}
        for d, v in df['vix'].items()
    ]
    # 标记信号日（用于图表上的散点）
    signal_dates = {str(d.date()) for d in df[signal_mask].index}

    return {
        'heatmap': {
            'buckets':    BUCKET_LABELS,
            'horizons':   HORIZONS,
            'win_rate':   heatmap_win_rate,
            'avg_return': heatmap_avg_ret,
            'count':      heatmap_count,
        },
        'events':        events[:300],
        'total_events':  len(events),
        'vix_series':    vix_series,
        'signal_dates':  list(signal_dates),
        'symbol':        symbol,
        'mode':          mode,
        'threshold':     threshold,
    }


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
