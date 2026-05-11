"""回测服务层：异步运行 run_backtest()，管理任务状态，结果持久化到磁盘"""
from __future__ import annotations
import sys
import os
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

_logger = logging.getLogger(__name__)

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

_COMBOS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'factor_combos.json'
)

# 内置预设：RSMomentum 5条件完整版
_BUILTIN_COMBOS: list[dict] = [
    {
        'id':           'rs_momentum_default',
        'name':         'RSMomentum 默认',
        'description':  'RS动量策略5个核心条件：相对强度+突破+放量+不崩+上升趋势',
        'builtin':      True,
        'factors':      ['rs_score', 'breakout', 'volume_surge', 'volume_divergence',
                         'trend_filter', 'drawdown_filter'],
        'factor_params': {},
    },
]


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
    # 后台线程默认没有 event loop，yfinance 等库可能需要，提前初始化
    import asyncio
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    with _lock:
        _tasks[task_id]['status'] = 'running'
    short_id = task_id[:8]
    _logger.info(f'[回测 {short_id}] 开始 params={params}')
    try:
        strategy = params.get('strategy', 'rs_momentum')
        if strategy == 'momentum5d':
            from tests.backtest_momentum5d import run_backtest as _run_m5d
            result = _run_m5d(
                period    = params.get('period', '3mo'),
                start     = params.get('start'),
                end       = params.get('end'),
                max_pos   = params.get('top', 4),
                pos_pct   = params.get('pos_pct', 0.22),
                hard_stop = params.get('hard_stop', -0.08),
                ema_stop  = params.get('ema_stop', 8),
                daily     = True,
            )
        else:
            from tests.backtest_rs import run_backtest
            # 过滤掉 momentum5d 专用字段，避免 run_backtest 收到意外参数
            rs_params = {k: v for k, v in params.items()
                         if k not in ('strategy', 'hard_stop', 'pos_pct', 'ema_stop')}
            result = run_backtest(**{**rs_params, 'daily': True})

        # 把每日持仓明细写入 server.log
        daily = result.get('daily_holdings') or []
        if daily:
            _logger.info(f'[回测 {short_id}] 每日持仓明细（共 {len(daily)} 天）:')
            for snap in daily:
                holdings = snap.get('holdings') or []
                syms = ' '.join(h['symbol'] for h in holdings) if holdings else '空仓'
                _logger.info(
                    f'  {snap["date"]}  持仓{len(holdings)}只  '
                    f'净值${snap.get("equity", 0):>10,.0f}  [{syms}]'
                )

        s = result.get('summary', {})
        _logger.info(
            f'[回测 {short_id}] 完成  '
            f'收益{s.get("total_return", 0):.1%}  '
            f'Sharpe={s.get("sharpe_ratio", 0):.2f}  '
            f'交易{s.get("num_trades", 0)}次'
        )
        with _lock:
            _tasks[task_id]['result']   = result
            _tasks[task_id]['status']   = 'completed'
            _tasks[task_id]['progress'] = 1.0
        _save_task(task_id, _tasks[task_id])
    except Exception as e:
        _logger.exception(f'[回测 {short_id}] 失败：{e}')
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


def submit_walk_forward(params: dict) -> str:
    task_id = str(uuid.uuid4())
    with _lock:
        _tasks[task_id] = {
            'status':     'pending',
            'progress':   0.0,
            'result':     None,
            'error':      None,
            'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
            'params':     params,
            'task_type':  'walk_forward',
        }
    t = threading.Thread(target=_run_walk_forward, args=(task_id, params), daemon=True)
    t.start()
    return task_id


def _run_walk_forward(task_id: str, params: dict):
    with _lock:
        _tasks[task_id]['status'] = 'running'
    try:
        from tests.walk_forward import walk_forward
        result = walk_forward(**params)
        with _lock:
            _tasks[task_id]['result']   = result
            _tasks[task_id]['status']   = 'completed'
            _tasks[task_id]['progress'] = 1.0
    except Exception as e:
        with _lock:
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['error']  = str(e)


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


# ── 因子组合 CRUD ──────────────────────────────────────────────

def _load_user_combos() -> list[dict]:
    if not os.path.exists(_COMBOS_FILE):
        return []
    try:
        with open(_COMBOS_FILE, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return []


def _save_user_combos(combos: list[dict]):
    os.makedirs(os.path.dirname(_COMBOS_FILE), exist_ok=True)
    with open(_COMBOS_FILE, 'w', encoding='utf-8') as f:
        json.dump(combos, f, ensure_ascii=False, indent=2)


def list_combos() -> list[dict]:
    """返回内置预设 + 用户保存的组合。"""
    return _BUILTIN_COMBOS + _load_user_combos()


def save_combo(name: str, factors: list[str], factor_params: dict = None) -> dict:
    """保存新因子组合，返回含 id 的完整 combo dict。"""
    combo = {
        'id':           str(uuid.uuid4()),
        'name':         name.strip(),
        'builtin':      False,
        'factors':      factors,
        'factor_params': factor_params or {},
        'created_at':   time.strftime('%Y-%m-%dT%H:%M:%S'),
    }
    combos = _load_user_combos()
    combos.append(combo)
    _save_user_combos(combos)
    return combo


def delete_combo(combo_id: str) -> bool:
    """删除用户组合（内置组合不可删除）。返回是否删除成功。"""
    if any(c['id'] == combo_id for c in _BUILTIN_COMBOS):
        return False   # 内置不可删
    combos = _load_user_combos()
    new_combos = [c for c in combos if c['id'] != combo_id]
    if len(new_combos) == len(combos):
        return False   # 未找到
    _save_user_combos(new_combos)
    return True
