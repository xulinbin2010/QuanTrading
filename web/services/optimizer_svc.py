"""
因子组合优化器服务层

核心逻辑：
  1. 枚举所有技术因子组合（mandatory 必选 + optional 子集）
  2. 将总回测区间按 train_ratio 切分为训练期 / 测试期
  3. 每个组合分别在训练期和测试期各跑一次回测
  4. 按测试期指标（默认 Sharpe）排序，计算过拟合分数
  5. 过滤测试期交易笔数 < 5 的组合（样本不足，指标不可信）
  6. 相同 Sharpe 时优先因子数少的组合（复杂度惩罚）

使用 ThreadPoolExecutor 并行（4 线程），预估耗时约 3-5 分钟。
"""
from __future__ import annotations
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import uuid
import time
import threading
from datetime import date, timedelta
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional


# ── 内存任务存储 ───────────────────────────────────────────
_tasks: dict[str, dict] = {}
_lock = threading.Lock()


# ── 工具函数 ───────────────────────────────────────────────

def _period_to_dates(period: str) -> tuple[str, str]:
    """将 '1y' / '3y' 等转换为 (start, end) 日期字符串"""
    today = date.today()
    mapping = {
        '6mo': 183, '1y': 365, '2y': 730,
        '3y': 1095, '5y': 1825, '10y': 3650,
    }
    days = mapping.get(period, 365)
    start = today - timedelta(days=days)
    return start.strftime('%Y-%m-%d'), today.strftime('%Y-%m-%d')


def _split_dates(start: str, end: str, train_ratio: float) -> tuple[str, str, str, str]:
    """将 [start, end] 按 train_ratio 切分为 (train_start, train_end, test_start, test_end)"""
    d0 = date.fromisoformat(start)
    d1 = date.fromisoformat(end)
    total_days = (d1 - d0).days
    split_days = int(total_days * train_ratio)
    split = d0 + timedelta(days=split_days)
    return (
        d0.strftime('%Y-%m-%d'),
        split.strftime('%Y-%m-%d'),
        (split + timedelta(days=1)).strftime('%Y-%m-%d'),
        d1.strftime('%Y-%m-%d'),
    )


def _get_tech_factor_keys() -> list[str]:
    """从注册表获取所有技术因子 key（注册顺序）"""
    from strategies.factors.registry import get_registry
    registry = get_registry()
    return [k for k, m in registry.items() if m.data_type == 'technical']


def _run_one(combo: list[str], start: str, end: str, universe: str,
             top_n: int, min_cap_b: float | None, max_cap_b: float | None) -> dict:
    """跑单次回测，返回 summary 或空 dict（出错时）"""
    from tests.backtest_rs import run_backtest
    try:
        result = run_backtest(
            start=start, end=end, period=None,
            universe=universe, top_n=top_n,
            min_cap_b=min_cap_b, max_cap_b=max_cap_b,
            factors=combo,
        )
        return result.get('summary', {})
    except Exception as e:
        return {'_error': str(e)}


# ── 主优化流程 ─────────────────────────────────────────────

def _run(task_id: str, params: dict):
    """后台线程：枚举组合 → 并行回测 → 排序结果"""
    try:
        with _lock:
            _tasks[task_id]['status'] = 'running'

        universe    = params.get('universe', 'sp500')
        period      = params.get('period', '3y')
        start       = params.get('start') or _period_to_dates(period)[0]
        end         = params.get('end')   or _period_to_dates(period)[1]
        mandatory   = params.get('mandatory_factors', ['rs_score'])
        min_size    = params.get('min_combo_size', 2)
        max_size    = params.get('max_combo_size', 6)
        train_ratio = params.get('train_ratio', 0.7)
        metric      = params.get('metric', 'sharpe')
        top_n       = params.get('top_n_results', 20)
        min_cap_b   = params.get('min_cap_b', None)
        max_cap_b   = params.get('max_cap_b', None)
        bt_top_n    = params.get('bt_top_n', 6)     # 回测持仓候选数

        # 训练 / 测试日期
        train_start, train_end, test_start, test_end = _split_dates(start, end, train_ratio)

        # 可选因子 = 全部技术因子 - mandatory
        all_tech = _get_tech_factor_keys()
        optional = [k for k in all_tech if k not in mandatory]

        # 枚举 optional 的子集（大小 = min_size-len(mandatory) ~ max_size-len(mandatory)）
        opt_min = max(0, min_size - len(mandatory))
        opt_max = max_size - len(mandatory)

        combos: list[list[str]] = []
        for size in range(opt_min, opt_max + 1):
            for subset in combinations(optional, size):
                combos.append(mandatory + list(subset))

        total = len(combos)
        with _lock:
            _tasks[task_id]['total'] = total
            _tasks[task_id]['current'] = 0

        # ── 并行回测 ─────────────────────────────────────────
        results = []

        def _submit(combo):
            train_s = _run_one(combo, train_start, train_end, universe, bt_top_n, min_cap_b, max_cap_b)
            test_s  = _run_one(combo, test_start,  test_end,  universe, bt_top_n, min_cap_b, max_cap_b)
            return combo, train_s, test_s

        done = 0
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_submit, c): c for c in combos}
            for future in as_completed(futures):
                combo, train_s, test_s = future.result()
                done += 1
                with _lock:
                    _tasks[task_id]['current']  = done
                    _tasks[task_id]['progress'] = done / (total * 2)
                    _tasks[task_id]['current_combo'] = combo

                # 过滤：有报错 / 测试期交易不足 5 笔
                if '_error' in train_s or '_error' in test_s:
                    continue
                if test_s.get('total_trades', 0) < 5:
                    continue

                train_sharpe = float(train_s.get('sharpe', 0) or 0)
                test_sharpe  = float(test_s.get('sharpe', 0) or 0)
                overfit      = round(train_sharpe - test_sharpe, 3)

                # 排序得分：测试期指标 - 复杂度惩罚
                if metric == 'sharpe':
                    base_score = test_sharpe
                elif metric == 'total_return':
                    base_score = float(test_s.get('total_return', 0) or 0)
                elif metric == 'excess_return':
                    base_score = float(test_s.get('excess_return', 0) or 0)
                else:
                    base_score = test_sharpe

                score = base_score - 0.02 * len(combo)

                results.append({
                    'factors':        combo,
                    'factor_count':   len(combo),
                    'score':          round(score, 4),
                    'overfit_score':  overfit,
                    'train': {
                        'return':     round(float(train_s.get('total_return', 0) or 0), 4),
                        'annual':     round(float(train_s.get('annual_return', 0) or 0), 4),
                        'sharpe':     round(train_sharpe, 3),
                        'max_dd':     round(float(train_s.get('max_drawdown', 0) or 0), 4),
                        'win_rate':   round(float(train_s.get('win_rate', 0) or 0), 4),
                        'trades':     int(train_s.get('total_trades', 0) or 0),
                    },
                    'test': {
                        'return':     round(float(test_s.get('total_return', 0) or 0), 4),
                        'annual':     round(float(test_s.get('annual_return', 0) or 0), 4),
                        'sharpe':     round(test_sharpe, 3),
                        'max_dd':     round(float(test_s.get('max_drawdown', 0) or 0), 4),
                        'win_rate':   round(float(test_s.get('win_rate', 0) or 0), 4),
                        'trades':     int(test_s.get('total_trades', 0) or 0),
                        'spy_return': round(float(test_s.get('spy_return', 0) or 0), 4),
                    },
                })

        # 排序 + 截取 top_n
        results.sort(key=lambda r: r['score'], reverse=True)
        top_results = results[:top_n]

        with _lock:
            _tasks[task_id]['result']   = {
                'results':      top_results,
                'total_tested': len(results),
                'total_combos': total,
                'train_period': f'{train_start} ~ {train_end}',
                'test_period':  f'{test_start} ~ {test_end}',
                'metric':       metric,
            }
            _tasks[task_id]['status']   = 'completed'
            _tasks[task_id]['progress'] = 1.0

    except Exception as e:
        with _lock:
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['error']  = str(e)


# ── 公开 API ───────────────────────────────────────────────

def submit_optimization(params: dict) -> str:
    """提交优化任务，立即返回 task_id"""
    task_id = str(uuid.uuid4())
    with _lock:
        _tasks[task_id] = {
            'status':       'pending',
            'progress':     0.0,
            'current':      0,
            'total':        0,
            'current_combo': [],
            'result':       None,
            'error':        None,
            'created_at':   time.strftime('%Y-%m-%d %H:%M:%S'),
            'params':       params,
        }
    t = threading.Thread(target=_run, args=(task_id, params), daemon=True)
    t.start()
    return task_id


def get_status(task_id: str) -> Optional[dict]:
    task = _tasks.get(task_id)
    if task is None:
        return None
    with _lock:
        return {
            'task_id':       task_id,
            'status':        task['status'],
            'progress':      task['progress'],
            'current':       task['current'],
            'total':         task['total'],
            'current_combo': task['current_combo'],
            'error':         task.get('error'),
        }


def get_result(task_id: str) -> Optional[dict]:
    task = _tasks.get(task_id)
    if task is None or task['status'] != 'completed':
        return None
    with _lock:
        return task['result']


def get_history() -> list[dict]:
    with _lock:
        items = []
        for tid, task in _tasks.items():
            r = task.get('result') or {}
            items.append({
                'task_id':       tid,
                'status':        task['status'],
                'created_at':    task['created_at'],
                'total_combos':  r.get('total_combos', 0),
                'total_tested':  r.get('total_tested', 0),
                'train_period':  r.get('train_period', ''),
                'test_period':   r.get('test_period', ''),
                'metric':        r.get('metric', ''),
                'best_factors':  r['results'][0]['factors'] if r.get('results') else [],
                'best_score':    r['results'][0]['score'] if r.get('results') else None,
            })
        items.sort(key=lambda x: x['created_at'], reverse=True)
        return items
