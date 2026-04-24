"""
前瞻偏差（Lookahead Bias）静态检测器
=====================================
使用 AST 解析 + 正则扫描，检测量化因子/策略代码中常见的前瞻偏差模式。

用法：
    python scripts/detect_lookahead.py
    python scripts/detect_lookahead.py --paths strategies/factors strategies/
    python scripts/detect_lookahead.py --strict
    python scripts/detect_lookahead.py --report scripts/lookahead_report.md

退出码：
    0  无 CRITICAL（宽松模式）/ 无任何问题（严格模式）
    1  有 CRITICAL（宽松模式）/ 有 WARNING 或 CRITICAL（严格模式）
"""

from __future__ import annotations

import ast
import argparse
import re
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────────────────

CRITICAL = "CRITICAL"
WARNING  = "WARNING"


@dataclass
class Issue:
    level:   str   # CRITICAL / WARNING
    file:    str   # 相对路径
    line:    int
    code:    str   # 触发该问题的代码片段（去除首尾空格）
    pattern: str   # 检测到的模式说明


# ─────────────────────────────────────────────────────────────────────────────
# AST 工具：提取 shift() 调用并检查参数
# ─────────────────────────────────────────────────────────────────────────────

def _ast_shift_issues(source: str, rel_path: str) -> list[Issue]:
    """
    用 AST 精确解析所有 .shift(...) 调用：
      CRITICAL: shift(-N)  ——向未来移位
      WARNING:  shift(0)   ——显式 shift(0)，等同于不移位
    """
    issues: list[Issue] = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return issues

    lines = source.splitlines()

    for node in ast.walk(tree):
        # 匹配形如 <expr>.shift(<args>) 的 Call 节点
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "shift"):
            continue

        # 取第一个位置参数（shift 的 periods 参数）
        if not node.args:
            continue
        arg = node.args[0]

        lineno = node.lineno
        # 安全取行文本
        code_line = lines[lineno - 1].strip() if lineno <= len(lines) else "<unknown>"

        # ── 负数字面量: shift(-N) ──────────────────────────────────
        # AST 中 -N 表示为 UnaryOp(USub, Constant(N))
        if (
            isinstance(arg, ast.UnaryOp)
            and isinstance(arg.op, ast.USub)
            and isinstance(arg.operand, ast.Constant)
            and isinstance(arg.operand.value, (int, float))
            and arg.operand.value > 0
        ):
            issues.append(Issue(
                level=CRITICAL,
                file=rel_path,
                line=lineno,
                code=code_line,
                pattern="shift(-N): 向未来移位，直接引入前瞻偏差",
            ))
            continue

        # ── shift(0)：显式无移位 ────────────────────────────────────
        if (
            isinstance(arg, ast.Constant)
            and arg.value == 0
        ):
            issues.append(Issue(
                level=WARNING,
                file=rel_path,
                line=lineno,
                code=code_line,
                pattern="shift(0): 显式 shift(0) 等同于不移位，请确认是否故意使用",
            ))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# 正则检测规则
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RegexRule:
    level:       str
    pattern:     re.Pattern
    description: str
    # 排除某些误报行：匹配此模式的行跳过
    exclude:     re.Pattern | None = None


# 编译所有正则规则（只编译一次）
_REGEX_RULES: list[RegexRule] = [

    # ── CRITICAL ─────────────────────────────────────────────────────────────

    # shift(-N) 文本形式兜底（AST 已精确处理，这里捕获动态变量如 shift(-n)）
    RegexRule(
        level=CRITICAL,
        pattern=re.compile(r'\.shift\(\s*-\s*[a-zA-Z0-9_]+'),
        description="shift(-var): 向未来移位（动态负数变量）",
        exclude=re.compile(r'^\s*#'),  # 跳过注释行
    ),

    # iloc[-1] / .tail(N) 在赋值给 signal 相关变量时标记
    RegexRule(
        level=CRITICAL,
        pattern=re.compile(r'\.iloc\[\s*-1\s*\]'),
        description="df.iloc[-1]: 取最后一行，可能泄露最新（未来）数据到信号列",
        exclude=re.compile(r'^\s*#'),
    ),
    RegexRule(
        level=CRITICAL,
        pattern=re.compile(r'\.tail\(\s*1\s*\)'),
        description="df.tail(1): 取最后一行，可能泄露最新数据",
        exclude=re.compile(r'^\s*#'),
    ),

    # 未来相关变量名出现在赋值右侧或条件中
    RegexRule(
        level=CRITICAL,
        pattern=re.compile(
            r'\b(future_|next_day|tomorrow|next_close|fwd_ret|forward_ret)\w*\s*[=\[]',
            re.IGNORECASE,
        ),
        description="未来数据变量名（future_*/next_day/tomorrow/fwd_ret...）出现在赋值/索引中",
        exclude=re.compile(r'^\s*#'),
    ),

    # resample 后直接用（未 shift）
    # 捕获 .resample(...).<aggr>() 紧跟在同一行，无随后的 .shift
    RegexRule(
        level=CRITICAL,
        pattern=re.compile(
            r'\.resample\([^\)]+\)\s*\.\s*(last|first|mean|sum|ohlc|max|min)\(\)'
            r'(?!\s*\.shift)'  # 未紧跟 .shift
        ),
        description="resample().agg() 后未接 .shift()，可能使用当周期收盘数据",
        exclude=re.compile(r'^\s*#'),
    ),

    # ── WARNING ──────────────────────────────────────────────────────────────

    # pd.merge / df.join 未检查 on/left_on/right_on 是否对齐
    # 只要出现 pd.merge 或 .merge( 或 .join( 就提示
    RegexRule(
        level=WARNING,
        pattern=re.compile(r'(?:pd\.merge|\.merge|\.join)\s*\('),
        description="pd.merge/join: 跨 DataFrame 合并时需确认日期键对齐，防止未来财报等数据混入",
        exclude=re.compile(r'^\s*#'),
    ),

    # bfill —— 用未来值填充
    RegexRule(
        level=WARNING,
        pattern=re.compile(
            r'(?:\.bfill\(\)|fillna\([^)]*method\s*=\s*[\'"]bfill[\'"]\s*[,)])'
        ),
        description="bfill()/fillna(method='bfill'): 用未来值向后填充，可能引入前瞻偏差",
        exclude=re.compile(r'^\s*#'),
    ),

    # pct_change() 后未跟 .shift(1) 就结束语句（可能直接用作当日特征）
    # 只匹配行尾是 .pct_change() 且后面没有 .shift( 的情况
    RegexRule(
        level=WARNING,
        pattern=re.compile(
            r'\.pct_change\(\s*\)'
            r'(?!\s*\.shift\s*\()'  # 其后没有紧跟 .shift
            r'\s*(?:#.*)?$'         # 行尾（可有注释）
        ),
        description="pct_change() 后未接 .shift(1) 直接结束，若作为预测特征需先移位",
        exclude=re.compile(r'^\s*#'),
    ),
]


def _regex_issues(source: str, rel_path: str) -> list[Issue]:
    """按行扫描正则规则，返回所有命中的 Issue 列表。"""
    issues: list[Issue] = []
    lines = source.splitlines()

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        # 跳过空行
        if not stripped:
            continue

        for rule in _REGEX_RULES:
            # 如果有排除规则且该行命中排除规则，跳过
            if rule.exclude and rule.exclude.search(raw_line):
                continue
            if rule.pattern.search(raw_line):
                issues.append(Issue(
                    level=rule.level,
                    file=rel_path,
                    line=lineno,
                    code=stripped,
                    pattern=rule.description,
                ))

    return issues


# ─────────────────────────────────────────────────────────────────────────────
# 去重：同一 (file, line, level) 可能被 AST + 正则同时命中
# ─────────────────────────────────────────────────────────────────────────────

def _dedup(issues: list[Issue]) -> list[Issue]:
    """
    若同一文件同一行同一 level 被多条规则命中，保留第一个（通常来自 AST，更精确）。
    """
    seen: set[tuple[str, int, str]] = set()
    out: list[Issue] = []
    for iss in issues:
        key = (iss.file, iss.line, iss.level)
        if key in seen:
            continue
        seen.add(key)
        out.append(iss)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 扫描单个文件
# ─────────────────────────────────────────────────────────────────────────────

def scan_file(path: Path, base_dir: Path) -> list[Issue]:
    """读取 path，运行 AST + 正则检测，返回去重后的 Issue 列表。"""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"[WARN] 无法读取 {path}: {exc}", file=sys.stderr)
        return []

    rel = str(path.relative_to(base_dir))

    issues: list[Issue] = []
    issues.extend(_ast_shift_issues(source, rel))
    issues.extend(_regex_issues(source, rel))

    return _dedup(issues)


# ─────────────────────────────────────────────────────────────────────────────
# 收集待扫描文件
# ─────────────────────────────────────────────────────────────────────────────

def collect_python_files(paths: list[str], base_dir: Path) -> list[Path]:
    """
    将 --paths 参数展开为 .py 文件列表（递归目录，跳过 __pycache__）。
    路径相对于 base_dir 解释。
    """
    files: list[Path] = []
    seen:  set[Path]  = set()

    for raw in paths:
        p = Path(raw) if Path(raw).is_absolute() else base_dir / raw
        if p.is_file() and p.suffix == ".py":
            if p not in seen:
                seen.add(p)
                files.append(p)
        elif p.is_dir():
            for py in sorted(p.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                if py not in seen:
                    seen.add(py)
                    files.append(py)
        else:
            print(f"[WARN] 路径不存在或不是 .py 文件：{raw}", file=sys.stderr)

    return files


# ─────────────────────────────────────────────────────────────────────────────
# 报告渲染
# ─────────────────────────────────────────────────────────────────────────────

def _md_escape(text: str) -> str:
    """转义 Markdown 表格中的竖线字符，避免破坏表格结构。"""
    return text.replace("|", "\\|")


def _truncate(text: str, max_len: int = 100) -> str:
    """代码片段过长时截断，保持表格可读。"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def render_report(
    all_issues:     list[Issue],
    files_scanned:  int,
    strict:         bool,
) -> str:
    """返回完整的 Markdown 报告字符串（同时用于 stdout 和写文件）。"""

    criticals = [i for i in all_issues if i.level == CRITICAL]
    warnings  = [i for i in all_issues if i.level == WARNING]

    exit_code = 0
    if criticals:
        exit_code = 1
    elif strict and warnings:
        exit_code = 1

    lines: list[str] = []
    lines.append("# Lookahead Bias Detection Report\n")
    lines.append(
        f"> Scanned {files_scanned} file(s) | "
        f"CRITICAL: {len(criticals)} | WARNING: {len(warnings)} | "
        f"Strict: {'ON' if strict else 'OFF'}\n"
    )

    # ── CRITICAL 表格 ─────────────────────────────────────────────────────
    lines.append("## CRITICAL Issues\n")
    if criticals:
        lines.append("| File | Line | Code | Pattern |")
        lines.append("|------|------|------|---------|")
        for iss in criticals:
            code = _md_escape(_truncate(iss.code))
            pat  = _md_escape(iss.pattern)
            lines.append(f"| `{iss.file}` | {iss.line} | `{code}` | {pat} |")
    else:
        lines.append("_No CRITICAL issues found._")

    lines.append("")

    # ── WARNING 表格 ──────────────────────────────────────────────────────
    lines.append("## WARNING Issues\n")
    if warnings:
        lines.append("| File | Line | Code | Pattern |")
        lines.append("|------|------|------|---------|")
        for iss in warnings:
            code = _md_escape(_truncate(iss.code))
            pat  = _md_escape(iss.pattern)
            lines.append(f"| `{iss.file}` | {iss.line} | `{code}` | {pat} |")
    else:
        lines.append("_No WARNING issues found._")

    lines.append("")

    # ── Summary ───────────────────────────────────────────────────────────
    lines.append("## Summary\n")
    lines.append(f"- Files scanned: {files_scanned}")
    lines.append(f"- CRITICAL: {len(criticals)}")
    lines.append(f"- WARNING: {len(warnings)}")
    lines.append(
        f"- Exit code: {exit_code} "
        f"({'clean' if exit_code == 0 else 'issues found'})"
    )

    return "\n".join(lines) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# stdout 彩色概要（非 Markdown，方便终端阅读）
# ─────────────────────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"


def _color(text: str, code: str) -> str:
    """只有 tty 时才输出颜色转义。"""
    if sys.stdout.isatty():
        return f"{code}{text}{_RESET}"
    return text


def print_console_report(all_issues: list[Issue], files_scanned: int, strict: bool) -> None:
    """向 stdout 输出带颜色的终端报告。"""
    criticals = [i for i in all_issues if i.level == CRITICAL]
    warnings  = [i for i in all_issues if i.level == WARNING]

    print()
    print(_color("=" * 60, _BOLD))
    print(_color("  Lookahead Bias Detection Report", _BOLD))
    print(_color("=" * 60, _BOLD))
    print(f"  Files scanned : {files_scanned}")
    print(f"  CRITICAL      : {_color(str(len(criticals)), _RED if criticals else _GREEN)}")
    print(f"  WARNING       : {_color(str(len(warnings)),  _YELLOW if warnings else _GREEN)}")
    print(f"  Strict mode   : {'ON' if strict else 'OFF'}")
    print()

    if criticals:
        print(_color(f"── CRITICAL ({len(criticals)}) ──────────────────────────────────", _RED + _BOLD))
        for iss in criticals:
            print(_color(f"  [{iss.file}:{iss.line}]", _CYAN))
            print(f"    Code    : {_truncate(iss.code, 90)}")
            print(_color(f"    Pattern : {iss.pattern}", _RED))
            print()

    if warnings:
        print(_color(f"── WARNING ({len(warnings)}) ──────────────────────────────────", _YELLOW + _BOLD))
        for iss in warnings:
            print(_color(f"  [{iss.file}:{iss.line}]", _CYAN))
            print(f"    Code    : {_truncate(iss.code, 90)}")
            print(_color(f"    Pattern : {iss.pattern}", _YELLOW))
            print()

    if not all_issues:
        print(_color("  No issues detected. All clear!", _GREEN + _BOLD))
        print()


# ─────────────────────────────────────────────────────────────────────────────
# CLI 入口
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="前瞻偏差静态检测器（AST + 正则）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例：
              python scripts/detect_lookahead.py
              python scripts/detect_lookahead.py --paths strategies/factors strategies/
              python scripts/detect_lookahead.py --strict
              python scripts/detect_lookahead.py --report scripts/lookahead_report.md
        """),
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=["strategies/factors", "strategies"],
        metavar="PATH",
        help="要扫描的文件或目录（可多个，相对于工作目录）。默认：strategies/factors strategies/",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help="严格模式：WARNING 也使退出码返回 1",
    )
    parser.add_argument(
        "--report",
        default="scripts/lookahead_report.md",
        metavar="FILE",
        help="Markdown 报告输出路径（默认：scripts/lookahead_report.md）",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 始终以项目根目录（本脚本的上一级）为基准解析路径
    script_dir = Path(__file__).resolve().parent
    base_dir   = script_dir.parent  # QuanTrading/

    # 收集文件
    py_files = collect_python_files(args.paths, base_dir)
    if not py_files:
        print("[ERROR] 未找到任何 .py 文件，请检查 --paths 参数。", file=sys.stderr)
        sys.exit(1)

    # 扫描
    all_issues: list[Issue] = []
    for path in py_files:
        all_issues.extend(scan_file(path, base_dir))

    # 按文件名 + 行号排序，同文件先 CRITICAL 后 WARNING
    _level_order = {CRITICAL: 0, WARNING: 1}
    all_issues.sort(key=lambda i: (i.file, i.line, _level_order[i.level]))

    # 终端输出
    print_console_report(all_issues, len(py_files), args.strict)

    # 写 Markdown 报告
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = base_dir / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    md_content = render_report(all_issues, len(py_files), args.strict)
    report_path.write_text(md_content, encoding="utf-8")
    print(f"Markdown 报告已写入：{report_path}")

    # 退出码
    criticals = [i for i in all_issues if i.level == CRITICAL]
    warnings  = [i for i in all_issues if i.level == WARNING]

    exit_code = 0
    if criticals:
        exit_code = 1
    elif args.strict and warnings:
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
