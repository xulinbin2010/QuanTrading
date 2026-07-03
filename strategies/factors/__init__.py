"""
因子模块 —— 每个因子是纯函数，签名统一为 (df, **params) -> df。

技术因子签名：(df: pd.DataFrame, **params) -> pd.DataFrame

用法示例：
  from strategies.factors import compute_rs_score, compute_breakout
  df = compute_rs_score(df, spy_close, period=63)
  df = compute_breakout(df, period=50)

  from strategies.factors import get_registry
  registry = get_registry()  # dict[str, FactorMeta]
"""

from .rs_score         import compute_rs_score
from .breakout         import compute_breakout
from .volume           import compute_volume_ma, compute_volume_surge, compute_volume_divergence
from .drawdown         import compute_drawdown_filter
from .atr              import compute_atr
from .trend            import compute_trend_filter
from .registry    import FactorMeta, get_registry

__all__ = [
    # 技术因子
    'compute_rs_score',
    'compute_breakout',
    'compute_volume_ma',
    'compute_volume_surge',
    'compute_volume_divergence',
    'compute_drawdown_filter',
    'compute_atr',
    'compute_trend_filter',
    # 注册表
    'FactorMeta',
    'get_registry',
]
