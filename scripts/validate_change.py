#!/usr/bin/env python3
"""
回测退步验证脚本（validate_change.py）。

读取 golden_baseline.json，并行运行多个回测（全段 + 各年度），
对比每个 metric，若退步超过 threshold 标记为 FAIL，
输出 markdown 报告到文件并同时 print 到 stdout。

用法：
  python scripts/validate_change.py
  python scripts/validate_change.py --threshold 0.10
  python scripts/validate_change.py --report scripts/my_report.md
  python scripts/validate_change.py --baseline scripts/golden_baseline.json

退出码：
  0  全部通过
  1  有 FAIL 项
"""
import argparse
import json
import sys
import os
import multiprocessing
from pathlib import Path
from datetime import datetime

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# ── 颜色/图标符号 ────────────────────────────────────────────────
ICON_PASS  = "🟩"   # 通过
ICON_WARN  = "🟨"   # 边界（退步但未超过阈值）
ICON_FAIL  = "🟥"   # 失败
ICON_SKIP  = "⬜"   # 跳过（无基准数据）

# warn 区间：退步 0 ~ threshold*0.5 时显示 WARN（而非直接 FAIL）
WARN_FACTOR = 0.5


# ── 指标元数据 ───────────────────────────────────────────────────
# (metric_key, baseline_field, direction)
# direction:
#   "higher_is_better"  → current < baseline*(1-thr) 为 FAIL
#   "lower_is_better"   → current < baseline*(1+thr) 时更差（绝对值更大 = 更差）→ FAIL
METRICS_DEF = [
    ("sharpe",           "sharpe",           "higher_is_better"),
    ("total_return",     "total_return",      "higher_is_better"),
    ("annualized_return","annualized_return", "higher_is_better"),
    ("max_drawdown",     "max_drawdown",      "lower_is_better"),
    ("win_rate",         "win_rate",          "higher_is_better"),
]


# ── Worker 函数（必须在模块顶层，multiprocessing 才能 pickle）────
def _run_task(task: dict) -> dict:
    """在子进程中运行一次 run_backtest，返回 {task_id, metrics, error}。"""
    try:
        from tests.backtest_rs import run_backtest
        result = run_backtest(
            start=task["start"],
            end=task["end"],
            universe=task["universe"],
        )
        s = result["summary"]
        metrics = {
            "sharpe":           s.get("sharpe"),
            "total_return":     s.get("total_return"),
            "annualized_return": s.get("annual_return"),
            "max_drawdown":     s.get("max_drawdown"),
            "win_rate":         s.get("win_rate"),
        }
        return {"task_id": task["task_id"], "metrics": metrics, "error": None}
    except Exception as exc:
        return {"task_id": task["task_id"], "metrics": None, "error": str(exc)}


# ── 退步判断 ─────────────────────────────────────────────────────
def _compare_metric(baseline_val, current_val, direction: str, threshold: float):
    """
    返回 (status, diff_pct)。
    status: "PASS" | "WARN" | "FAIL" | "SKIP"
    diff_pct: (current - baseline) / abs(baseline)，正 = 改善，负 = 退步
    """
    if baseline_val is None or current_val is None:
        return "SKIP", None

    # 避免除以 0
    if abs(baseline_val) < 1e-12:
        # baseline 几乎为 0，只要 current 不差就算 PASS
        return ("PASS" if current_val >= 0 else "FAIL"), None

    diff_pct = (current_val - baseline_val) / abs(baseline_val)

    if direction == "higher_is_better":
        # 退步 = diff_pct 是负的（current 更小）
        degradation = -diff_pct if diff_pct < 0 else 0.0
        if degradation >= threshold:
            return "FAIL", diff_pct
        elif degradation >= threshold * WARN_FACTOR:
            return "WARN", diff_pct
        else:
            return "PASS", diff_pct

    else:  # lower_is_better（max_drawdown 是负数，绝对值更大 = 更差）
        # max_drawdown 为负数，current 更负 = 更差 = current < baseline
        # 退步判断：current < baseline * (1 + threshold)
        # 即 current/baseline - 1 > threshold（因为两者都是负数，比值 >1 表示绝对值更大）
        if baseline_val < 0:
            ratio = current_val / baseline_val   # >1 表示 current 绝对值更大（更差）
            degradation = (ratio - 1.0) if ratio > 1.0 else 0.0
        else:
            # baseline 为正数的 lower_is_better 场景（理论上不存在，防御性处理）
            degradation = diff_pct if diff_pct > 0 else 0.0

        if degradation >= threshold:
            return "FAIL", diff_pct
        elif degradation >= threshold * WARN_FACTOR:
            return "WARN", diff_pct
        else:
            return "PASS", diff_pct


# ── 格式化 ───────────────────────────────────────────────────────
def _fmt_val(val, metric_key: str) -> str:
    if val is None:
        return "N/A"
    if metric_key in ("sharpe",):
        return f"{val:.4f}"
    if metric_key in ("total_return", "annualized_return", "max_drawdown", "win_rate"):
        return f"{val:+.2%}"
    return str(val)


def _icon(status: str) -> str:
    return {"PASS": ICON_PASS, "WARN": ICON_WARN, "FAIL": ICON_FAIL, "SKIP": ICON_SKIP}.get(status, ICON_SKIP)


# ── 报告生成 ─────────────────────────────────────────────────────
def _build_report(
    tasks_baseline: dict,   # task_id -> baseline metrics dict
    tasks_current:  dict,   # task_id -> current  metrics dict (or None on error)
    tasks_errors:   dict,   # task_id -> error string (or None)
    tasks_labels:   dict,   # task_id -> human-readable label
    threshold: float,
) -> tuple[str, bool]:
    """
    构建 markdown 报告字符串，返回 (report_text, has_fail)。
    """
    lines = []
    has_fail = False
    all_statuses = []   # 用于 heatmap

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines.append(f"# 回测退步验证报告")
    lines.append(f"")
    lines.append(f"生成时间：{now_str}  |  阈值：{threshold:.0%}")
    lines.append(f"")

    # ── 汇总表（先占位，后面填充）────────────────────────────────
    summary_placeholder = len(lines)
    lines.append("_SUMMARY_PLACEHOLDER_")
    lines.append(f"")

    # ── 各段详细对比 ─────────────────────────────────────────────
    lines.append("## 逐段指标对比")
    lines.append(f"")

    task_ids = sorted(tasks_labels.keys())

    for task_id in task_ids:
        label    = tasks_labels[task_id]
        baseline = tasks_baseline.get(task_id, {})
        current  = tasks_current.get(task_id)
        error    = tasks_errors.get(task_id)

        lines.append(f"### {label}")
        lines.append(f"")

        if error:
            lines.append(f"> **ERROR**: 回测执行失败：{error}")
            lines.append(f"")
            has_fail = True
            all_statuses.append((label, "FAIL", {}))
            continue

        if current is None:
            lines.append(f"> **SKIP**: 无法获取当前回测结果")
            lines.append(f"")
            all_statuses.append((label, "SKIP", {}))
            continue

        # 表头
        lines.append(f"| 指标 | 基准值 | 当前值 | 变化% | 状态 |")
        lines.append(f"|------|--------|--------|-------|------|")

        segment_statuses = {}
        for (metric_key, _, direction) in METRICS_DEF:
            b_val = baseline.get(metric_key)
            c_val = current.get(metric_key)
            status, diff_pct = _compare_metric(b_val, c_val, direction, threshold)

            segment_statuses[metric_key] = status
            if status == "FAIL":
                has_fail = True

            diff_str = (f"{diff_pct:+.2%}" if diff_pct is not None else "N/A")
            lines.append(
                f"| {metric_key} "
                f"| {_fmt_val(b_val, metric_key)} "
                f"| {_fmt_val(c_val, metric_key)} "
                f"| {diff_str} "
                f"| {_icon(status)} {status} |"
            )

        lines.append(f"")
        all_statuses.append((label, segment_statuses))

    # ── ASCII Heatmap ─────────────────────────────────────────────
    lines.append("## 状态 Heatmap")
    lines.append(f"")

    metric_keys = [m[0] for m in METRICS_DEF]
    # 表头
    hdr = "| 段落 | " + " | ".join(metric_keys) + " |"
    sep = "|------|" + "|".join(["------"] * len(metric_keys)) + "|"
    lines.append(hdr)
    lines.append(sep)

    fail_count = 0
    warn_count = 0
    pass_count = 0
    skip_count = 0

    for item in all_statuses:
        label = item[0]
        seg_statuses = item[1] if isinstance(item[1], dict) else {}

        if isinstance(item[1], str):
            # error/skip 行
            overall = item[1]
            icons = " | ".join([_icon(overall)] * len(metric_keys))
            lines.append(f"| {label} | {icons} |")
            if overall == "FAIL":
                fail_count += 1
            else:
                skip_count += 1
        else:
            icons = []
            for mk in metric_keys:
                st = seg_statuses.get(mk, "SKIP")
                icons.append(_icon(st))
                if st == "FAIL":
                    fail_count += 1
                elif st == "WARN":
                    warn_count += 1
                elif st == "PASS":
                    pass_count += 1
                else:
                    skip_count += 1
            lines.append(f"| {label} | " + " | ".join(icons) + " |")

    lines.append(f"")
    lines.append(f"图例：{ICON_PASS} PASS（通过）  {ICON_WARN} WARN（边界，退步未超阈值）  {ICON_FAIL} FAIL（退步超阈值）  {ICON_SKIP} SKIP（无基准）")
    lines.append(f"")

    # ── 汇总（回填 placeholder）────────────────────────────────
    total_checks = fail_count + warn_count + pass_count
    overall_status = "FAIL" if has_fail else ("WARN" if warn_count > 0 else "PASS")
    summary_lines = [
        f"## 验证汇总",
        f"",
        f"| 结果 | PASS | WARN | FAIL | SKIP | 总计 |",
        f"|------|------|------|------|------|------|",
        f"| {_icon(overall_status)} **{overall_status}** "
        f"| {pass_count} | {warn_count} | {fail_count} | {skip_count} | {total_checks} |",
        f"",
    ]
    lines[summary_placeholder] = "\n".join(summary_lines)

    report_text = "\n".join(lines)
    return report_text, has_fail


# ── 主逻辑 ───────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="回测退步验证")
    parser.add_argument("--threshold", type=float, default=0.05,
                        help="退步阈值（默认 0.05 = 5%%）")
    parser.add_argument("--report",    default="scripts/validation_report.md",
                        help="报告输出路径（默认 scripts/validation_report.md）")
    parser.add_argument("--baseline",  default="scripts/golden_baseline.json",
                        help="基准文件路径（默认 scripts/golden_baseline.json）")
    args = parser.parse_args()

    # 路径处理：支持相对/绝对路径
    baseline_path = Path(args.baseline)
    if not baseline_path.is_absolute():
        baseline_path = _ROOT / baseline_path
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = _ROOT / report_path

    # 加载基准
    if not baseline_path.exists():
        print(f"ERROR: 基准文件不存在：{baseline_path}", file=sys.stderr)
        sys.exit(1)

    with open(baseline_path, "r", encoding="utf-8") as f:
        baseline_data = json.load(f)

    universe  = baseline_data.get("universe", "sp500")
    threshold = args.threshold
    yearly    = baseline_data.get("yearly", {})
    full_computed = baseline_data.get("computed", False)

    # ── 构建任务列表 ─────────────────────────────────────────────
    tasks   = []    # 传给 worker 的参数列表
    labels  = {}    # task_id -> label
    bl_data = {}    # task_id -> baseline metrics dict

    # 全段任务（仅当 baseline computed=True 时才加入）
    if full_computed:
        task_id = "full"
        tasks.append({
            "task_id":  task_id,
            "start":    baseline_data["period"]["start"],
            "end":      baseline_data["period"]["end"],
            "universe": universe,
        })
        labels[task_id] = f"全段（{baseline_data['period']['start']} ~ {baseline_data['period']['end']}）"
        bl_data[task_id] = baseline_data["metrics"]
    else:
        print("注意：baseline computed=false，跳过全段对比，只对比有 yearly 数据的年度。")

    # 年度任务（只加入有数据的年度）
    for year, y_metrics in yearly.items():
        # 只要 sharpe 或 total_return 有值就纳入对比
        has_data = any(
            y_metrics.get(k) is not None
            for k in ("sharpe", "total_return", "annualized_return", "max_drawdown", "win_rate")
        )
        if not has_data:
            print(f"跳过 {year} 年：无基准数据")
            continue
        task_id = f"year_{year}"
        tasks.append({
            "task_id":  task_id,
            "start":    f"{year}-01-01",
            "end":      f"{year}-12-31",
            "universe": universe,
        })
        labels[task_id]  = f"{year} 年"
        bl_data[task_id] = {
            "sharpe":           y_metrics.get("sharpe"),
            "total_return":     y_metrics.get("total_return"),
            "annualized_return": y_metrics.get("annualized_return"),
            "max_drawdown":     y_metrics.get("max_drawdown"),
            "win_rate":         y_metrics.get("win_rate"),
        }

    if not tasks:
        print("ERROR: 没有可运行的任务（基准文件中没有有效数据）", file=sys.stderr)
        sys.exit(1)

    print(f"\n共 {len(tasks)} 个回测任务，使用 multiprocessing 并行运行...")
    for t in tasks:
        print(f"  - {labels[t['task_id']]}  {t['start']} ~ {t['end']}")

    # ── 并行运行 ─────────────────────────────────────────────────
    ctx = multiprocessing.get_context("fork")   # macOS/Linux 均支持 fork
    with ctx.Pool(processes=min(len(tasks), os.cpu_count() or 1)) as pool:
        raw_results = pool.map(_run_task, tasks)

    # 整理结果
    current_metrics = {}
    error_map       = {}
    for r in raw_results:
        tid = r["task_id"]
        if r["error"]:
            current_metrics[tid] = None
            error_map[tid]       = r["error"]
            print(f"  [ERROR] {labels[tid]}：{r['error']}")
        else:
            current_metrics[tid] = r["metrics"]
            error_map[tid]       = None
            s = r["metrics"]
            print(f"  [OK]    {labels[tid]}："
                  f"total_return={s['total_return']:+.2%}  "
                  f"sharpe={s['sharpe']:.4f}")

    # ── 生成报告 ─────────────────────────────────────────────────
    report_text, has_fail = _build_report(
        tasks_baseline=bl_data,
        tasks_current=current_metrics,
        tasks_errors=error_map,
        tasks_labels=labels,
        threshold=threshold,
    )

    # 写文件 + 打印 stdout
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(f"\n{'='*60}")
    print(report_text)
    print(f"{'='*60}")
    print(f"报告已写入：{report_path}")

    sys.exit(1 if has_fail else 0)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
