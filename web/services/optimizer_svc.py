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
import json
import threading
from datetime import date, timedelta
from itertools import combinations
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional


# ── 内存任务存储 ───────────────────────────────────────────
_tasks: dict[str, dict] = {}
_lock = threading.Lock()
_history_loaded = False

_RESULTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'optimizer_results'
)


# ── 文件持久化 ─────────────────────────────────────────────

def _save_task(task_id: str, task: dict):
    """将已完成的任务写入磁盘"""
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
    """首次调用时从磁盘加载历史记录到内存"""
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
                        'status':        data['status'],
                        'progress':      1.0,
                        'current':       0,
                        'total':         0,
                        'current_combo': [],
                        'result':        data['result'],
                        'error':         None,
                        'created_at':    data['created_at'],
                        'params':        data.get('params', {}),
                    }
        except Exception:
            pass


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


def _add_months(d: date, months: int) -> date:
    """日期加 N 个月，处理月末边界"""
    import calendar
    month = d.month - 1 + months
    year  = d.year + month // 12
    month = month % 12 + 1
    day   = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _generate_wf_windows(start: str, end: str,
                          train_months: int, test_months: int,
                          step_months: int) -> list[dict]:
    """生成 Walk-Forward 滚动窗口列表（非重叠测试期）"""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    windows, ts = [], d0
    while True:
        te = _add_months(ts, train_months) - timedelta(days=1)
        vs = te + timedelta(days=1)
        ve = _add_months(vs, test_months) - timedelta(days=1)
        if ve > d1:
            break
        windows.append({
            'train_start': ts.strftime('%Y-%m-%d'),
            'train_end':   te.strftime('%Y-%m-%d'),
            'test_start':  vs.strftime('%Y-%m-%d'),
            'test_end':    ve.strftime('%Y-%m-%d'),
        })
        ts = _add_months(ts, step_months)
    return windows


def _get_tech_factor_keys() -> list[str]:
    """从注册表获取所有技术因子 key（注册顺序）"""
    from strategies.factors.registry import get_registry
    registry = get_registry()
    return [k for k, m in registry.items() if m.data_type == 'technical' and not m.is_dependency]


def _run_one(combo: list[str], start: str, end: str, universe: str,
             bt_top: int, min_cap_b: float | None, max_cap_b: float | None,
             preloaded_data: dict = None, preloaded_info: dict = None,
             precomputed_cache=None) -> dict:
    """跑单次回测，返回 summary 或 {'_error': ...}（出错时）"""
    from tests.backtest_rs import run_backtest
    try:
        result = run_backtest(
            start=start, end=end, period=None,
            universe=universe, top=bt_top,
            min_cap_b=min_cap_b, max_cap_b=max_cap_b,
            factors=combo,
            preloaded_data=preloaded_data,
            preloaded_info=preloaded_info,
            precomputed_cache=precomputed_cache,
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
        bt_top_n    = params.get('bt_top_n', 6)
        mode        = params.get('mode', 'single')   # 'single' | 'walkforward'

        # 可选因子 = 全部技术因子 - mandatory
        all_tech = _get_tech_factor_keys()
        optional = [k for k in all_tech if k not in mandatory]
        opt_min  = max(0, min_size - len(mandatory))
        opt_max  = max_size - len(mandatory)
        combos: list[list[str]] = []
        for size in range(opt_min, opt_max + 1):
            for subset in combinations(optional, size):
                combos.append(mandatory + list(subset))

        total = len(combos)
        with _lock:
            _tasks[task_id]['total']   = total
            _tasks[task_id]['current'] = 0

        # ── 预加载数据（整个优化只加载一次，所有组合共用）────────
        import pandas as pd
        from datetime import timedelta
        from core.data_store import DataStore
        from core.universe import get_tickers, get_stock_info

        tickers  = get_tickers(universe)
        all_syms = list(set(tickers + ['SPY', '^VIX']))

        # 向前多留 140 天用于指标预热（与 run_backtest 保持一致）
        dl_start = (pd.Timestamp(start) - timedelta(days=140)).strftime('%Y-%m-%d')
        dl_end   = end

        _store = DataStore()
        _store.update(all_syms, dl_start, dl_end)          # 增量更新只做一次
        preloaded_data = _store.get(all_syms, dl_start, dl_end,
                                    min_rows=0, auto_update=False)
        preloaded_info = get_stock_info(tickers)            # 基本面/行业只拉一次

        # ── 因子信号预计算（整个优化只算一次，所有 combo × window 共用）─
        from strategies.precompute import precompute_all_factors
        spy_close = preloaded_data['SPY']['close'] if 'SPY' in preloaded_data else pd.Series(dtype=float)
        _stock_data_for_cache = {s: preloaded_data[s] for s in tickers if s in preloaded_data}
        precomputed_cache = precompute_all_factors(_stock_data_for_cache, spy_close)
        # ──────────────────────────────────────────────────────────────────

        results: list[dict] = []

        # ── 单次切分模式 ───────────────────────────────────────
        if mode == 'single':
            train_start, train_end, test_start, test_end = _split_dates(start, end, train_ratio)

            def _submit_single(combo):
                ts = _run_one(combo, train_start, train_end, universe, bt_top_n, min_cap_b, max_cap_b,
                              preloaded_data, preloaded_info, precomputed_cache)
                vs = _run_one(combo, test_start,  test_end,  universe, bt_top_n, min_cap_b, max_cap_b,
                              preloaded_data, preloaded_info, precomputed_cache)
                return combo, ts, vs

            done = 0
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(_submit_single, c): c for c in combos}
                for future in as_completed(futures):
                    combo, train_s, test_s = future.result()
                    done += 1
                    with _lock:
                        _tasks[task_id]['current']      = done
                        _tasks[task_id]['progress']     = done / total
                        _tasks[task_id]['current_combo'] = combo

                    if '_error' in train_s or '_error' in test_s:
                        err = train_s.get('_error') or test_s.get('_error', '')
                        with _lock:
                            _tasks[task_id].setdefault('last_error', err)
                        continue
                    if test_s.get('total_trades', 0) < 5:
                        continue

                    train_sharpe = float(train_s.get('sharpe', 0) or 0)
                    test_sharpe  = float(test_s.get('sharpe', 0) or 0)

                    if metric == 'sharpe':
                        base_score = test_sharpe
                    elif metric == 'total_return':
                        base_score = float(test_s.get('total_return', 0) or 0)
                    elif metric == 'excess_return':
                        base_score = float(test_s.get('excess_return', 0) or 0)
                    else:
                        base_score = test_sharpe

                    results.append({
                        'factors':       combo,
                        'factor_count':  len(combo),
                        'score':         round(base_score - 0.02 * len(combo), 4),
                        'overfit_score': round(train_sharpe - test_sharpe, 3),
                        'train': {
                            'return':   round(float(train_s.get('total_return', 0) or 0), 4),
                            'annual':   round(float(train_s.get('annual_return', 0) or 0), 4),
                            'sharpe':   round(train_sharpe, 3),
                            'max_dd':   round(float(train_s.get('max_drawdown', 0) or 0), 4),
                            'win_rate': round(float(train_s.get('win_rate', 0) or 0), 4),
                            'trades':   int(train_s.get('total_trades', 0) or 0),
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

            results.sort(key=lambda r: r['score'], reverse=True)
            result_payload = {
                'mode':         'single',
                'results':      results[:top_n],
                'total_tested': len(results),
                'total_combos': total,
                'train_period': f'{train_start} ~ {train_end}',
                'test_period':  f'{test_start} ~ {test_end}',
                'metric':       metric,
            }

        # ── Walk-Forward 滚动窗口模式 ──────────────────────────
        else:
            wf_train  = params.get('wf_train_months', 12)
            wf_test   = params.get('wf_test_months',  3)
            wf_step   = params.get('wf_step_months',  3)
            windows   = _generate_wf_windows(start, end, wf_train, wf_test, wf_step)

            if not windows:
                raise ValueError(
                    f'区间太短，无法生成 Walk-Forward 窗口（需要至少 {wf_train + wf_test} 个月）'
                )

            def _submit_wf(combo):
                pairs = []
                for w in windows:
                    ts = _run_one(combo, w['train_start'], w['train_end'], universe, bt_top_n, min_cap_b, max_cap_b,
                                  preloaded_data, preloaded_info, precomputed_cache)
                    vs = _run_one(combo, w['test_start'],  w['test_end'],  universe, bt_top_n, min_cap_b, max_cap_b,
                                  preloaded_data, preloaded_info, precomputed_cache)
                    pairs.append((w, ts, vs))
                return combo, pairs

            done = 0
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(_submit_wf, c): c for c in combos}
                for future in as_completed(futures):
                    combo, pairs = future.result()
                    done += 1
                    with _lock:
                        _tasks[task_id]['current']       = done
                        _tasks[task_id]['progress']      = done / total
                        _tasks[task_id]['current_combo'] = combo

                    # 仅保留无报错、测试期交易 >= 3 的窗口
                    valid = [(w, ts, vs) for w, ts, vs in pairs
                             if '_error' not in ts and '_error' not in vs
                             and vs.get('total_trades', 0) >= 3]

                    # 有效窗口须占总窗口数的一半以上
                    if len(valid) < max(1, len(windows) // 2):
                        continue

                    train_sharpes  = [float(ts.get('sharpe', 0) or 0) for _, ts, _ in valid]
                    test_sharpes   = [float(vs.get('sharpe', 0) or 0) for _, _, vs in valid]
                    test_returns   = [float(vs.get('total_return', 0) or 0) for _, _, vs in valid]
                    test_excesses  = [float(vs.get('excess_return', 0) or 0) for _, _, vs in valid]
                    test_dds       = [float(vs.get('max_drawdown', 0) or 0) for _, _, vs in valid]
                    test_wrs       = [float(vs.get('win_rate', 0) or 0) for _, _, vs in valid]
                    test_trades    = [int(vs.get('total_trades', 0) or 0) for _, _, vs in valid]

                    n = len(valid)
                    avg_train_sharpe  = sum(train_sharpes) / n
                    avg_test_sharpe   = sum(test_sharpes)  / n
                    std_test_sharpe   = (sum((x - avg_test_sharpe) ** 2 for x in test_sharpes) / n) ** 0.5
                    avg_overfit       = avg_train_sharpe - avg_test_sharpe
                    avg_excess_return = sum(test_excesses) / n
                    # stability = 全部窗口（含失效）中测试 Sharpe > 0 的比例
                    stability         = sum(1 for s in test_sharpes if s > 0) / len(windows)
                    # 链式累计总收益率：只用互不重叠的测试窗口连乘
                    # 当 step < test 时贪心选取，避免同一市场区间被重复计入导致虚高
                    non_overlap_returns: list[float] = []
                    nonoverlap_first_start: str | None = None
                    last_test_end: str | None = None
                    for w, _ts, vs in valid:
                        if last_test_end is None or w['test_start'] > last_test_end:
                            non_overlap_returns.append(float(vs.get('total_return', 0) or 0))
                            if nonoverlap_first_start is None:
                                nonoverlap_first_start = w['test_start']
                            last_test_end = w['test_end']
                    chain_total_return = 1.0
                    for r in non_overlap_returns:
                        chain_total_return *= (1 + r)
                    chain_total_return -= 1
                    windows_overlapping = wf_step < wf_test

                    # 年化链式收益率（主要展示指标，消除窗口数量对幅度的影响）
                    avg_window_return = (
                        sum(non_overlap_returns) / len(non_overlap_returns)
                        if non_overlap_returns else 0.0
                    )
                    if non_overlap_returns and nonoverlap_first_start and last_test_end:
                        from datetime import date as _date
                        _chain_days = (
                            _date.fromisoformat(last_test_end)
                            - _date.fromisoformat(nonoverlap_first_start)
                        ).days
                        chain_annual_return = (
                            (1 + chain_total_return) ** (365 / max(_chain_days, 1)) - 1
                            if chain_total_return > -1 else -1.0
                        )
                    else:
                        chain_annual_return = 0.0

                    if metric == 'sharpe':
                        base_score = avg_test_sharpe
                    elif metric == 'total_return':
                        base_score = sum(test_returns) / n
                    elif metric == 'excess_return':
                        base_score = avg_excess_return
                    else:
                        base_score = avg_test_sharpe

                    # 得分：均值 - 波动惩罚 - 复杂度惩罚
                    score = base_score - 0.3 * std_test_sharpe - 0.02 * len(combo)

                    per_window = [
                        {
                            'train_period': f"{w['train_start']} ~ {w['train_end']}",
                            'test_period':  f"{w['test_start']} ~ {w['test_end']}",
                            'train_sharpe': round(float(ts.get('sharpe', 0) or 0), 3),
                            'test_sharpe':  round(float(vs.get('sharpe', 0) or 0), 3),
                            'test_return':  round(float(vs.get('total_return', 0) or 0), 4),
                            'test_trades':  int(vs.get('total_trades', 0) or 0),
                        }
                        for w, ts, vs in valid
                    ]

                    results.append({
                        'factors':      combo,
                        'factor_count': len(combo),
                        'score':        round(score, 4),
                        'stability':    round(stability, 3),
                        'avg_overfit':  round(avg_overfit, 3),
                        'avg_train': {
                            'sharpe':  round(avg_train_sharpe, 3),
                        },
                        'avg_test': {
                            'sharpe':             round(avg_test_sharpe, 3),
                            'std_sharpe':         round(std_test_sharpe, 3),
                            'return':             round(sum(test_returns) / n, 4),
                            'avg_window_return':  round(avg_window_return, 4),    # 均窗口收益（主要）
                            'chain_annual_return':round(chain_annual_return, 4),  # 年化链式（主要）
                            'total_return':       round(chain_total_return, 4),   # 原始链式（次要）
                            'excess_return':      round(avg_excess_return, 4),
                            'max_dd':             round(sum(test_dds) / n, 4),
                            'win_rate':           round(sum(test_wrs) / n, 4),
                            'trades':             round(sum(test_trades) / n, 1),
                        },
                        'window_count':        n,
                        'non_overlap_windows': len(non_overlap_returns),
                        'windows_overlapping': windows_overlapping,
                        'windows':             per_window,
                    })

            results.sort(key=lambda r: r['score'], reverse=True)
            result_payload = {
                'mode':         'walkforward',
                'results':      results[:top_n],
                'total_tested': len(results),
                'total_combos': total,
                'window_count': len(windows),
                'wf_params':           {'train_months': wf_train, 'test_months': wf_test, 'step_months': wf_step},
                'windows_overlapping': wf_step < wf_test,  # step<test 时测试期有重叠，链式收益率已自动修正
                # 供历史表格展示用的整体区间
                'train_period': f'{windows[0]["train_start"]} ~ {windows[-1]["train_end"]}',
                'test_period':  f'{windows[0]["test_start"]}  ~ {windows[-1]["test_end"]}',
                'metric':       metric,
            }

        with _lock:
            _tasks[task_id]['result']   = result_payload
            _tasks[task_id]['status']   = 'completed'
            _tasks[task_id]['progress'] = 1.0
            _save_task(task_id, _tasks[task_id])

    except Exception as e:
        import traceback
        with _lock:
            _tasks[task_id]['status'] = 'failed'
            _tasks[task_id]['error']  = str(e)
            _tasks[task_id]['trace']  = traceback.format_exc()


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
            'last_error':    task.get('last_error'),   # 首个组合级别的报错（调试用）
        }


def get_result(task_id: str) -> Optional[dict]:
    _load_history_from_disk()
    task = _tasks.get(task_id)
    if task is None or task['status'] != 'completed':
        return None
    with _lock:
        return task['result']


def get_history() -> list[dict]:
    _load_history_from_disk()
    with _lock:
        items = []
        for tid, task in _tasks.items():
            r = task.get('result') or {}
            items.append({
                'task_id':       tid,
                'status':        task['status'],
                'created_at':    task['created_at'],
                'mode':          r.get('mode', 'single'),
                'total_combos':  r.get('total_combos', 0),
                'total_tested':  r.get('total_tested', 0),
                'train_period':  r.get('train_period', ''),
                'test_period':   r.get('test_period', ''),
                'window_count':  r.get('window_count', 0),
                'wf_params':     r.get('wf_params', {}),
                'metric':        r.get('metric', ''),
                'best_factors':  r['results'][0]['factors'] if r.get('results') else [],
                'best_score':    r['results'][0]['score'] if r.get('results') else None,
            })
        items.sort(key=lambda x: x['created_at'], reverse=True)
        return items
