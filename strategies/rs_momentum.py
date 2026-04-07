import pandas as pd
from .base import Strategy
from .factors import (
    compute_rs_score,
    compute_breakout,
    compute_volume_ma,
    compute_volume_surge,
    compute_volume_divergence,
    compute_drawdown_filter,
    compute_atr,
    compute_trend_filter,
)


class RSMomentum(Strategy):
    """
    相对强度动量策略。

    买入信号（同时满足）：
      1. RS 得分 > 0 —— 个股跑赢 SPY（过去 rs_period 天）
      2. 价格突破近 breakout_period 天高点
      3. 突破当天成交量 > vol_ma 日均量 × vol_multiplier（量价配合）
      4. 距52周高点回撤不超过 max_drawdown_from_high
      5. MA50 > MA200（黄金交叉趋势过滤）

    卖出报警（持仓监控）：
      - 价格创近 breakout_period 天新高，但成交量低于均量（量价背离 → 顶部信号）

    使用前需调用 set_spy(spy_close) 传入 SPY 收盘价。
    """

    def __init__(
        self,
        rs_period: int = 63,          # RS 计算窗口（3个月≈63交易日）
        breakout_period: int = 50,     # 突破判断窗口（近50日高点）
        vol_ma: int = 20,             # 成交量均线窗口
        vol_multiplier: float = 1.5,  # 放量倍数阈值
        max_drawdown_from_high: float = -0.30,  # 从52周最高跌超此比例不买入
        vol_shrink_ratio: float = 0.7,  # 量价背离判定：成交量需低于均量×此比例
        extra_filters: list[str] | None = None,  # 额外注册表因子键（filter 类型）
    ):
        self.rs_period = rs_period
        self.breakout_period = breakout_period
        self.vol_ma = vol_ma
        self.vol_multiplier = vol_multiplier
        self.max_drawdown_from_high = max_drawdown_from_high
        self.vol_shrink_ratio = vol_shrink_ratio
        self.extra_filters = list(extra_filters) if extra_filters else []
        self._spy_close: pd.Series = None

    @property
    def name(self) -> str:
        return (f"RS_Momentum("
                f"rs={self.rs_period}d, "
                f"breakout={self.breakout_period}d, "
                f"vol×{self.vol_multiplier})")

    def set_spy(self, spy_close: pd.Series):
        """传入 SPY 收盘价，用于计算相对强度"""
        self._spy_close = spy_close

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # ── 计算各因子 ────────────────────────────────────────────
        df = compute_rs_score(df, self._spy_close, self.rs_period)
        df = compute_volume_ma(df, self.vol_ma)
        df = compute_breakout(df, self.breakout_period)
        df = compute_volume_surge(df, self.vol_multiplier)
        df = compute_volume_divergence(df, self.breakout_period, self.vol_shrink_ratio)
        df = compute_drawdown_filter(df, self.max_drawdown_from_high)
        df = compute_atr(df, period=14)
        df = compute_trend_filter(df)

        # ── 生成信号 ──────────────────────────────────────────────
        df['signal'] = 0

        # 买入：RS 跑赢 + 突破 + 放量 + 没有从高点崩跌 + 上升趋势
        buy = (
            (df['rs_score'] > 0) &
            df['breakout'] &
            df['vol_surge'] &
            df['not_crashed'] &
            df['uptrend']
        )
        df.loc[buy, 'signal'] = 1

        # 卖出报警：价格创新高但成交量萎缩（优先级低于买入信号）
        sell_alert = df['at_new_high'] & df['vol_shrink'] & (df['signal'] == 0)
        df.loc[sell_alert, 'signal'] = -1

        # ── 额外注册表 filter 因子（实验验证后从 extra_filters 推入生产）──
        if self.extra_filters:
            from .factors.registry import get_registry
            registry = get_registry()
            for key in self.extra_filters:
                meta = registry.get(key)
                if meta is None or meta.data_type != 'technical' or meta.signal_type != 'filter':
                    continue
                # 先确保因子已计算
                if meta.signal_column not in df.columns:
                    params = {p: v[0] for p, v in meta.params.items()}
                    df = meta.compute_fn(df, **params)
                # 将不满足额外过滤条件的买入信号清零
                col = meta.signal_column
                df.loc[(df['signal'] == 1) & ~df[col].fillna(False).astype(bool), 'signal'] = 0

        # 清理中间列（只删 prev_high，at_new_high 保留供 scanner 使用）
        df.drop(columns=['prev_high'], inplace=True)

        return df
