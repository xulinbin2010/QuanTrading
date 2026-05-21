"""
动态因子策略：允许从注册表中选择任意技术因子组合，动态生成买卖信号。

用于 Web 回测 UI 的因子实验，不替代 RSMomentum（CLI 仍用 RSMomentum）。

信号逻辑：
  买入 = AND(所有 enabled filter 因子 == True)
          AND (所有 enabled score 因子 >= score_threshold)
  卖出报警 = volume_divergence 启用时的量价背离信号（at_new_high & vol_shrink）

注意：
  - volume_surge 和 volume_divergence 依赖 volume_ma，会自动先计算 volume_ma
  - rs_score 需要 spy_close，通过 set_spy() 传入
  - 基本面因子（fundamental）不参与时序回测，动态策略中忽略
"""
from __future__ import annotations
import pandas as pd
from .base import Strategy
from .factors.registry import get_registry

# volume_surge / volume_divergence 依赖 volume_ma，需先计算
_VOLUME_DEPS = {'volume_surge', 'volume_divergence'}


class DynamicFactorStrategy(Strategy):
    """
    基于因子注册表的动态因子组合策略。

    参数：
      enabled_factors : 要启用的因子 key 列表（只含技术因子）
      factor_params   : 每个因子的参数覆盖，如 {"rs_score": {"period": 126}}
      score_threshold : score 类因子的买入阈值（默认 0，即 rs_score > 0）
    """

    def __init__(
        self,
        enabled_factors: list[str],
        factor_params: dict[str, dict] | None = None,
        score_threshold: float = 0.0,
    ):
        self.enabled_factors = list(enabled_factors)
        self.factor_params = factor_params or {}
        self.score_threshold = score_threshold
        self._spy_close: pd.Series | None = None
        self._sector_etf_close: pd.Series | None = None

    @property
    def name(self) -> str:
        return f"DynamicFactor({', '.join(self.enabled_factors)})"

    def set_spy(self, spy_close: pd.Series):
        self._spy_close = spy_close

    def set_sector_etf(self, sector: str | None, sector_etf_close: pd.Series | None):
        """为 sector_rs 因子提供行业 ETF 数据。sector 用于记录日志，sector_etf_close 是价格序列。"""
        self._sector_etf_close = sector_etf_close

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        registry = get_registry()
        df = df.copy()

        # 如果有依赖 volume_ma 的因子，先确保 volume_ma 已计算
        needs_vol_ma = any(k in self.enabled_factors for k in _VOLUME_DEPS)
        vol_ma_done = False

        # 按注册顺序计算（保证 volume_ma → volume_surge/divergence 的顺序）
        for key in registry:
            if key not in self.enabled_factors:
                continue
            meta = registry[key]
            if meta.data_type != 'technical':
                continue

            # 自动补算 volume_ma（如果还没算）
            if key in _VOLUME_DEPS and not vol_ma_done:
                if 'volume_ma' not in self.enabled_factors:
                    vol_meta = registry['volume_ma']
                    df = vol_meta.compute_fn(df)
                vol_ma_done = True
            if key == 'volume_ma':
                vol_ma_done = True

            params = dict(self.factor_params.get(key, {}))
            # rs_score 需要 spy_close 作为位置参数
            if key == 'rs_score':
                period  = params.get('period',  meta.params['period'][0])
                weights = params.get('weights', meta.params.get('weights', ('',))[0])
                df = meta.compute_fn(df, self._spy_close, period=period, weights=weights)
            # sector_rs 需要 sector_etf_close + spy_close
            elif key == 'sector_rs':
                period = params.get('period', meta.params['period'][0])
                df = meta.compute_fn(df,
                                     sector_etf_close=self._sector_etf_close,
                                     spy_close=self._spy_close,
                                     period=period)
            else:
                # 使用注册表默认值填充缺失参数
                for pname, (pdefault, _ptype, _pdesc) in meta.params.items():
                    params.setdefault(pname, pdefault)
                df = meta.compute_fn(df, **params)

        # ── 构建买入信号 ──────────────────────────────────────
        df['signal'] = 0
        buy_mask = pd.Series(True, index=df.index)

        for key in self.enabled_factors:
            if key not in registry:
                continue
            meta = registry[key]
            if meta.data_type != 'technical':
                continue
            col = meta.signal_column
            if col not in df.columns:
                continue

            if meta.signal_type == 'filter':
                # 排除量价背离（它是卖出信号，不参与买入条件）
                if key == 'volume_divergence':
                    continue
                buy_mask &= df[col].fillna(False).astype(bool)
            elif meta.signal_type == 'score':
                buy_mask &= df[col].fillna(-999) > self.score_threshold

        df.loc[buy_mask, 'signal'] = 1

        # ── 量价背离卖出报警 ──────────────────────────────────
        if 'volume_divergence' in self.enabled_factors:
            if 'at_new_high' in df.columns and 'vol_shrink' in df.columns:
                sell_alert = df['at_new_high'] & df['vol_shrink'] & (df['signal'] == 0)
                df.loc[sell_alert, 'signal'] = -1

        # 清理中间列
        if 'prev_high' in df.columns:
            df.drop(columns=['prev_high'], inplace=True)

        return df
