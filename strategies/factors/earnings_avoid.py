"""
财报回避因子

注意：此因子 display_only=True。
只能判断当前时刻是否临近财报，无法对历史 K 线做逐日判断（未来财报日期在过去无法知道）。
实际消费由 factor_svc.py 的 scan_factors() 在行层面调用 has_upcoming_earnings()。
此模块提供配置默认值和注册表元数据。
"""
from __future__ import annotations


def compute_earnings_avoid_placeholder(info: dict) -> dict:
    """
    占位计算函数（基本面快照类型）。
    真正的财报回避逻辑在 factor_svc.scan_factors() 中处理（需要 symbol 参数）。
    此函数仅供注册表保持接口一致性。
    """
    return {"earnings_safe": info.get("earnings_safe")}
