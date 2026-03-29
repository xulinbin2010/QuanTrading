"""
终端表格对齐工具。

问题：中文字符显示宽度为2，但 Python len() 计为1，
导致 f"{s:<8}" 对含中文的字符串缩进错位。

解决：lj(s, w) / rj(s, w) 按显示宽度对齐。
"""


def _cjk_extra(s: str) -> int:
    """计算字符串中 CJK 字符多占的显示宽度（每个 CJK 字符多占 1）"""
    extra = 0
    for c in s:
        cp = ord(c)
        if (0x1100 <= cp <= 0x115F   # Hangul Jamo
                or 0x2E80 <= cp <= 0x303F   # CJK Radicals / Symbols
                or 0x3040 <= cp <= 0x33FF   # Japanese / CJK
                or 0x3400 <= cp <= 0x4DBF   # CJK Ext-A
                or 0x4E00 <= cp <= 0x9FFF   # CJK Unified
                or 0xAC00 <= cp <= 0xD7AF   # Hangul Syllables
                or 0xF900 <= cp <= 0xFAFF   # CJK Compat
                or 0xFE10 <= cp <= 0xFE6F   # Compat Forms / Small Forms
                or 0xFF00 <= cp <= 0xFF60   # Fullwidth
                or 0xFFE0 <= cp <= 0xFFE6): # Fullwidth Signs
            extra += 1
    return extra


def lj(s: str, width: int) -> str:
    """左对齐，按终端显示宽度填充空格"""
    return s.ljust(width - _cjk_extra(s))


def rj(s: str, width: int) -> str:
    """右对齐，按终端显示宽度填充空格"""
    return s.rjust(width - _cjk_extra(s))
