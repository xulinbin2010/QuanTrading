"""
scripts/preflight.py

IBKR Live Trading Preflight Check
----------------------------------
并行运行5个测试文件，聚合结果，输出 go/no-go 裁决 + markdown postmortem。

用法：
    .venv/bin/python scripts/preflight.py
    .venv/bin/python scripts/preflight.py --timeout 60
    .venv/bin/python scripts/preflight.py --report scripts/preflight_report.md
    .venv/bin/python scripts/preflight.py --python .venv/bin/python

退出码：0 = GO，1 = NO-GO
"""

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────
# 配置：测试文件列表（test_class 名 → 文件路径）
# ─────────────────────────────────────────────

TEST_FILES = [
    ("connection_drops",  "tests/test_connection.py"),
    ("partial_fills",     "tests/test_partial_fills.py"),
    ("sector_limits",     "tests/test_sector_limits.py"),
    ("position_sizing",   "tests/test_position_sizing.py"),
    ("zombie_orders",     "tests/test_zombie_orders.py"),
]

# ─────────────────────────────────────────────
# 子进程运行单个测试
# ─────────────────────────────────────────────

def _run_one(args_tuple: tuple) -> dict:
    """
    在子进程中运行一个测试文件，返回结构化结果 dict。
    因为 ProcessPoolExecutor 不支持 lambda，用顶层函数。
    """
    test_class, test_file, python_exe, timeout_sec, project_root = args_tuple

    # 文件不存在 → SKIPPED
    full_path = Path(project_root) / test_file
    if not full_path.exists():
        return {
            "test_class": test_class,
            "file": test_file,
            "status": "SKIPPED",
            "passed": 0,
            "failed": 0,
            "total": 0,
            "stdout": "",
            "stderr": f"File not found: {test_file}",
            "exit_code": None,
            "results": [],
            "elapsed": 0.0,
        }

    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            [python_exe, test_file],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=project_root,
        )
        elapsed = time.monotonic() - t0
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()
        exit_code = proc.returncode

        # 解析 stdout 最后一行 JSON
        report = None
        if stdout:
            lines = stdout.splitlines()
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("{"):
                    try:
                        report = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue

        if report is not None:
            passed = report.get("passed", 0)
            failed = report.get("failed", 0)
            total  = passed + failed
            results = report.get("results", [])
            status = "PASS" if failed == 0 and exit_code == 0 else "FAIL"
        else:
            # 无法解析 JSON → 按退出码判断
            passed = 0
            failed = 1
            total  = 1
            results = []
            status = "FAIL"
            stderr = (stderr or "") + "\n[preflight] Could not parse JSON report from stdout."

        return {
            "test_class": test_class,
            "file": test_file,
            "status": status,
            "passed": passed,
            "failed": failed,
            "total": total,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "results": results,
            "elapsed": round(elapsed, 2),
        }

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        return {
            "test_class": test_class,
            "file": test_file,
            "status": "TIMEOUT",
            "passed": 0,
            "failed": 1,
            "total": 1,
            "stdout": "",
            "stderr": f"Test timed out after {timeout_sec}s",
            "exit_code": None,
            "results": [],
            "elapsed": round(elapsed, 2),
        }
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return {
            "test_class": test_class,
            "file": test_file,
            "status": "FAIL",
            "passed": 0,
            "failed": 1,
            "total": 1,
            "stdout": "",
            "stderr": f"[preflight] Exception launching test: {exc}",
            "exit_code": None,
            "results": [],
            "elapsed": round(elapsed, 2),
        }


# ─────────────────────────────────────────────
# 终端表格输出
# ─────────────────────────────────────────────

# ANSI color codes（只在终端输出用，不写入 markdown）
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _status_icon(status: str, is_tty: bool) -> str:
    if status == "PASS":
        return (GREEN + "✓" + RESET) if is_tty else "✓"
    if status == "SKIPPED":
        return (CYAN + "–" + RESET) if is_tty else "-"
    if status == "TIMEOUT":
        return (RED + "T" + RESET) if is_tty else "T"
    # FAIL
    return (RED + "✗" + RESET) if is_tty else "✗"


def _verdict_icon(go: bool, is_tty: bool) -> str:
    if go:
        return (GREEN + BOLD + "✓ GO" + RESET) if is_tty else "✓ GO"
    return (RED + BOLD + "✗ NO-GO" + RESET) if is_tty else "✗ NO-GO"


def print_banner(test_results: list[dict], overall_go: bool, is_tty: bool) -> None:
    """Print the box-drawing summary table to stdout."""
    WIDTH = 44
    border_h = "═" * WIDTH
    sep_h    = "═" * WIDTH

    lines = []
    lines.append(f"╔{border_h}╗")
    lines.append(f"║  {'IBKR Live Trading Preflight':<{WIDTH-2}}║")
    lines.append(f"╠{sep_h}╣")

    for r in test_results:
        name    = r["test_class"]
        status  = r["status"]
        passed  = r["passed"]
        total   = r["total"]
        icon    = _status_icon(status, is_tty)

        if status == "SKIPPED":
            score_str = "SKIPPED"
        elif status == "TIMEOUT":
            score_str = f"TIMEOUT ({r['elapsed']}s)"
        else:
            score_str = f"{passed}/{total} {'PASS' if status == 'PASS' else 'FAIL'}"

        # Build row: icon + name + score
        row_inner = f"  {icon}  {name:<22} {score_str}"

        # Strip ANSI for length calculation
        import re
        ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
        plain_inner = ansi_escape.sub("", row_inner)
        padding = WIDTH - len(plain_inner)
        if padding < 0:
            padding = 0

        lines.append(f"║{row_inner}{' ' * padding}║")

    lines.append(f"╠{sep_h}╣")

    verdict_text  = _verdict_icon(overall_go, is_tty)
    import re
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    plain_verdict = ansi_escape.sub("", verdict_text)
    verdict_row   = f"  RESULT:  {verdict_text}"
    plain_row     = f"  RESULT:  {plain_verdict}"
    padding       = WIDTH - len(plain_row)
    if padding < 0:
        padding = 0
    lines.append(f"║{verdict_row}{' ' * padding}║")
    lines.append(f"╚{border_h}╝")

    print("\n".join(lines))


# ─────────────────────────────────────────────
# Markdown レポート生成
# ─────────────────────────────────────────────

def _md_status_badge(status: str) -> str:
    if status == "PASS":
        return "✅ PASS"
    if status == "SKIPPED":
        return "⏭️ SKIPPED"
    if status == "TIMEOUT":
        return "⏱️ TIMEOUT"
    return "❌ FAIL"


def write_markdown_report(
    test_results: list[dict],
    overall_go: bool,
    total_elapsed: float,
    report_path: str,
) -> None:
    """Write a markdown postmortem to report_path."""
    now_utc = datetime.now(timezone.utc)
    now_str  = now_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

    lines = []
    lines.append("# IBKR Live Trading Preflight Report")
    lines.append("")
    lines.append(f"**Date:** {now_str}  ")
    lines.append(f"**Total runtime:** {total_elapsed:.1f}s  ")
    total_passed = sum(r["passed"] for r in test_results)
    total_failed = sum(r["failed"] for r in test_results if r["status"] != "SKIPPED")
    skipped_count = sum(1 for r in test_results if r["status"] == "SKIPPED")
    lines.append(f"**Tests passed:** {total_passed}  ")
    lines.append(f"**Tests failed:** {total_failed}  ")
    if skipped_count:
        lines.append(f"**Test files skipped (not yet created):** {skipped_count}  ")
    lines.append("")

    # Summary table
    lines.append("## Summary")
    lines.append("")
    lines.append("| Test Class | File | Passed | Failed | Time | Status |")
    lines.append("|------------|------|-------:|-------:|-----:|--------|")
    for r in test_results:
        lines.append(
            f"| `{r['test_class']}` "
            f"| `{r['file']}` "
            f"| {r['passed']} "
            f"| {r['failed']} "
            f"| {r['elapsed']}s "
            f"| {_md_status_badge(r['status'])} |"
        )
    lines.append("")

    # Failure details
    failed_results = [r for r in test_results if r["status"] in ("FAIL", "TIMEOUT")]
    if failed_results:
        lines.append("## Failure Details")
        lines.append("")
        for r in failed_results:
            lines.append(f"### {r['test_class']} — {_md_status_badge(r['status'])}")
            lines.append("")
            lines.append(f"**File:** `{r['file']}`  ")
            lines.append(f"**Exit code:** {r['exit_code']}  ")
            lines.append(f"**Elapsed:** {r['elapsed']}s  ")
            lines.append("")

            if r["status"] == "TIMEOUT":
                lines.append(f"> Test timed out: {r['stderr']}")
                lines.append("")
                continue

            # Scenario-level failures
            scenario_failures = [s for s in r.get("results", []) if s.get("status") == "FAIL"]
            if scenario_failures:
                lines.append("**Failed scenarios:**")
                lines.append("")
                for s in scenario_failures:
                    scenario_name = s.get("scenario", s.get("test", "unknown"))
                    lines.append(f"#### Scenario: `{scenario_name}`")
                    lines.append("")
                    details = s.get("details", "")
                    if details:
                        lines.append(f"**Details:** {details}  ")
                    errors = s.get("errors", [])
                    if errors:
                        lines.append("")
                        lines.append("**Errors:**")
                        lines.append("```")
                        for err in errors:
                            lines.append(str(err))
                        lines.append("```")
                    lines.append("")

            # stderr if any
            if r.get("stderr"):
                lines.append("**stderr:**")
                lines.append("```")
                lines.append(r["stderr"])
                lines.append("```")
                lines.append("")

    # Skipped details
    skipped_results = [r for r in test_results if r["status"] == "SKIPPED"]
    if skipped_results:
        lines.append("## Skipped Test Files")
        lines.append("")
        lines.append("The following test files were not found. They may not have been created yet.")
        lines.append("")
        for r in skipped_results:
            lines.append(f"- `{r['file']}` (`{r['test_class']}`)")
        lines.append("")

    # Verdict
    lines.append("---")
    lines.append("")
    if overall_go:
        lines.append("## Verdict: ✅ GO")
        lines.append("")
        lines.append("All tests passed. Safe to proceed with live trading deployment.")
    else:
        lines.append("## Verdict: ❌ NO-GO — Do not deploy to live trading")
        lines.append("")
        lines.append(
            "One or more preflight checks failed or timed out. "
            "Resolve all failures before deploying."
        )
    lines.append("")

    report_text = "\n".join(lines)
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(report_text, encoding="utf-8")


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="IBKR Live Trading Preflight — parallel test runner"
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=120,
        metavar="SECONDS",
        help="Per-test timeout in seconds (default: 120)",
    )
    p.add_argument(
        "--report",
        default="scripts/preflight_report.md",
        metavar="PATH",
        help="Output markdown report path (default: scripts/preflight_report.md)",
    )
    p.add_argument(
        "--python",
        default=".venv/bin/python",
        metavar="PATH",
        help="Python interpreter to use (default: .venv/bin/python)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    # Resolve project root (scripts/preflight.py lives in scripts/)
    project_root = str(Path(__file__).resolve().parent.parent)

    # Resolve python executable
    python_exe = args.python
    if not Path(python_exe).is_absolute():
        python_exe = str(Path(project_root) / python_exe)

    if not Path(python_exe).exists():
        print(f"[preflight] WARNING: Python not found at {python_exe}, falling back to sys.executable", file=sys.stderr)
        python_exe = sys.executable

    is_tty = sys.stdout.isatty()

    print(f"\n[preflight] Python: {python_exe}")
    print(f"[preflight] Timeout: {args.timeout}s per test")
    print(f"[preflight] Running {len(TEST_FILES)} test suites in parallel...\n")

    # Build args for each worker
    worker_args = [
        (test_class, test_file, python_exe, args.timeout, project_root)
        for test_class, test_file in TEST_FILES
    ]

    t_start = time.monotonic()

    # Run in parallel using ProcessPoolExecutor
    # max_workers=5 to match the number of test files
    test_results_map: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=5) as executor:
        future_to_class = {
            executor.submit(_run_one, arg): arg[0]
            for arg in worker_args
        }
        for future in as_completed(future_to_class):
            test_class = future_to_class[future]
            try:
                result = future.result(timeout=args.timeout + 5)
            except FuturesTimeoutError:
                result = {
                    "test_class": test_class,
                    "file": next(f for tc, f in TEST_FILES if tc == test_class),
                    "status": "TIMEOUT",
                    "passed": 0,
                    "failed": 1,
                    "total": 1,
                    "stdout": "",
                    "stderr": f"Future timed out after {args.timeout + 5}s",
                    "exit_code": None,
                    "results": [],
                    "elapsed": float(args.timeout),
                }
            except Exception as exc:
                result = {
                    "test_class": test_class,
                    "file": next(f for tc, f in TEST_FILES if tc == test_class),
                    "status": "FAIL",
                    "passed": 0,
                    "failed": 1,
                    "total": 1,
                    "stdout": "",
                    "stderr": f"[preflight] Unexpected error: {exc}",
                    "exit_code": None,
                    "results": [],
                    "elapsed": 0.0,
                }
            test_results_map[test_class] = result
            # Print progress
            icon = {"PASS": "✓", "FAIL": "✗", "SKIPPED": "-", "TIMEOUT": "T"}.get(result["status"], "?")
            print(f"  [{icon}] {result['test_class']:<22} {result['status']}  ({result['elapsed']}s)")

    total_elapsed = round(time.monotonic() - t_start, 2)

    # Reconstruct ordered list (preserve TEST_FILES order)
    test_results = [test_results_map[tc] for tc, _ in TEST_FILES]

    # Determine overall verdict
    # SKIPPED does NOT count as FAIL
    any_fail = any(
        r["status"] in ("FAIL", "TIMEOUT")
        for r in test_results
    )
    overall_go = not any_fail

    print()
    print_banner(test_results, overall_go, is_tty)
    print()

    # Write markdown report
    report_path = args.report
    if not Path(report_path).is_absolute():
        report_path = str(Path(project_root) / report_path)

    write_markdown_report(test_results, overall_go, total_elapsed, report_path)
    print(f"[preflight] Report written to: {report_path}")
    print()

    return 0 if overall_go else 1


if __name__ == "__main__":
    sys.exit(main())
