#!/usr/bin/env python3
"""
生成回测基准（golden baseline）。

运行后会实际执行回测并把真实数值写入 scripts/golden_baseline.json。
每个年度单独跑一次并更新 yearly 字段；全段（2020-01-01 ~ 2024-12-31）也跑一次。

用法：
  python scripts/generate_baseline.py
  python scripts/generate_baseline.py --universe nasdaq100
  python scripts/generate_baseline.py --years 2022 2023 2024   # 只更新指定年度
  python scripts/generate_baseline.py --full-only               # 只更新全段，不跑年度
  python scripts/generate_baseline.py --yearly-only             # 只更新年度，不跑全段
"""
import argparse
import json
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

BASELINE_PATH = _ROOT / "scripts" / "golden_baseline.json"

# 默认全段日期
DEFAULT_START = "2020-01-01"
DEFAULT_END   = "2024-12-31"
DEFAULT_UNIVERSE = "sp500"

YEARLY_RANGES = {
    "2020": ("2020-01-01", "2020-12-31"),
    "2021": ("2021-01-01", "2021-12-31"),
    "2022": ("2022-01-01", "2022-12-31"),
    "2023": ("2023-01-01", "2023-12-31"),
    "2024": ("2024-01-01", "2024-12-31"),
}


def _extract_metrics(result: dict) -> dict:
    """从 run_backtest() 返回值中提取需要持久化的指标。"""
    s = result["summary"]
    return {
        "sharpe":           s.get("sharpe"),
        "total_return":     s.get("total_return"),
        "annualized_return": s.get("annual_return"),
        "max_drawdown":     s.get("max_drawdown"),
        "win_rate":         s.get("win_rate"),
        "computed":         True,
    }


def run_one(start: str, end: str, universe: str, label: str) -> dict:
    """运行单次回测并返回指标 dict。"""
    from tests.backtest_rs import run_backtest
    print(f"  正在跑回测：{label}  {start} → {end}  universe={universe}")
    result = run_backtest(start=start, end=end, universe=universe)
    metrics = _extract_metrics(result)
    s = result["summary"]
    print(f"    total_return={s['total_return']:+.2%}  sharpe={s['sharpe']:.2f}"
          f"  max_drawdown={s['max_drawdown']:.2%}  win_rate={s['win_rate']:.2%}")
    return metrics


def load_baseline() -> dict:
    with open(BASELINE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_baseline(data: dict) -> None:
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n已写入 {BASELINE_PATH}")


def main():
    parser = argparse.ArgumentParser(description="生成/更新 golden_baseline.json")
    parser.add_argument("--universe",    default=DEFAULT_UNIVERSE,
                        help=f"股票池（默认 {DEFAULT_UNIVERSE}）")
    parser.add_argument("--start",       default=DEFAULT_START,
                        help=f"全段回测起始日期（默认 {DEFAULT_START}）")
    parser.add_argument("--end",         default=DEFAULT_END,
                        help=f"全段回测结束日期（默认 {DEFAULT_END}）")
    parser.add_argument("--years",       nargs="+", default=list(YEARLY_RANGES.keys()),
                        help="要更新的年度列表（默认全部）")
    parser.add_argument("--full-only",   action="store_true",
                        help="只更新全段，不跑年度")
    parser.add_argument("--yearly-only", action="store_true",
                        help="只更新年度，不跑全段")
    args = parser.parse_args()

    baseline = load_baseline()

    # 更新全段
    if not args.yearly_only:
        print("\n[1/2] 全段回测")
        full_metrics = run_one(args.start, args.end, args.universe, "全段")
        baseline["period"]   = {"start": args.start, "end": args.end}
        baseline["universe"] = args.universe
        baseline["computed"] = True
        baseline["metrics"]  = {k: full_metrics[k] for k in
                                 ("sharpe", "total_return", "annualized_return",
                                  "max_drawdown", "win_rate")}

    # 更新年度
    if not args.full_only:
        print(f"\n[2/2] 年度回测（{', '.join(args.years)}）")
        for year in args.years:
            if year not in YEARLY_RANGES:
                print(f"  跳过未知年度：{year}")
                continue
            y_start, y_end = YEARLY_RANGES[year]
            y_metrics = run_one(y_start, y_end, args.universe, f"{year}年")
            if year not in baseline.get("yearly", {}):
                baseline.setdefault("yearly", {})[year] = {}
            baseline["yearly"][year].update(y_metrics)

    save_baseline(baseline)
    print("\n完成。")


if __name__ == "__main__":
    main()
