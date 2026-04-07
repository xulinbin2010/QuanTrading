"""
因子模块 —— 每个因子是纯函数，签名统一为 (df, **params) -> df。

技术因子签名：(df: pd.DataFrame, **params) -> pd.DataFrame
基本面因子签名：(info: dict) -> dict

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
from .volume_profile   import compute_obv_trend
from .drawdown         import compute_drawdown_filter
from .atr              import compute_atr
from .trend            import compute_trend_filter
from .volatility       import compute_volatility_filter
from .momentum_quality import compute_momentum_quality
from .sector_rs        import compute_sector_rs, SECTOR_ETFS, ALL_SECTOR_ETFS
from .fundamental import (
    compute_revenue_growth, compute_earnings_growth,
    compute_roe, compute_debt_to_equity, compute_fcf_yield,
    compute_pe_ratio, compute_pb_ratio,
)
from .registry    import FactorMeta, get_registry

__all__ = [
    # 技术因子
    'compute_rs_score',
    'compute_breakout',
    'compute_volume_ma',
    'compute_volume_surge',
    'compute_volume_divergence',
    'compute_obv_trend',
    'compute_drawdown_filter',
    'compute_atr',
    'compute_trend_filter',
    'compute_volatility_filter',
    'compute_momentum_quality',
    'compute_sector_rs',
    'SECTOR_ETFS',
    'ALL_SECTOR_ETFS',
    # 基本面因子
    'compute_revenue_growth',
    'compute_earnings_growth',
    'compute_roe',
    'compute_debt_to_equity',
    'compute_fcf_yield',
    'compute_pe_ratio',
    'compute_pb_ratio',
    # 注册表
    'FactorMeta',
    'get_registry',
]
