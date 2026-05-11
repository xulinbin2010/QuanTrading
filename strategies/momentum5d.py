"""
5日动量策略（Momentum5D）

设计理念：
- 追短周期强势股：个股 5 日涨幅持续跑赢 SPY → 买入
- 动量衰减即出场：5 日滚动 RS ≤ 0（不再跑赢 SPY）→ 次日 OPG 卖
- 成交量降权：仅作辅助参考，不作硬门槛
- 行业/AI 过滤：由外部调用方（回测/交易脚本）负责

与 RSMomentum 的区别：
- rs_period = 5（而非 63）
- 无突破、无量价背离信号
- signal=-1 表示 RS 转负（出场），而非量价背离
"""
import pandas as pd


class Momentum5D:
    """
    5日相对强度动量策略。

    信号定义：
      signal =  1  → 5日RS > 0，可作为买入候选（按 rs_5d 降序排名）
      signal = -1  → 5日RS ≤ 0，持仓应于次日 OPG 出场
      signal =  0  → 数据不足，忽略

    输出列：
      - rs_5d     : 5日RS
      - vol_ratio : 量比（参考）
      - ema_stop  : EMA 短期均线（默认 8 日），破位作为止损线（period=0 时不计算）
      - signal    : 1 / -1 / 0

    使用前必须调用 set_spy(spy_close) 传入 SPY 日线收盘价。
    """

    rs_period = 5   # 相对强度计算窗口（交易日）

    def __init__(self, rs_period: int = 5, ema_stop_period: int = 8):
        self.rs_period       = rs_period
        self.ema_stop_period = ema_stop_period   # 0 = 禁用 EMA 破位止损
        self._spy: pd.Series | None = None

    def set_spy(self, spy_close: pd.Series):
        self._spy = spy_close

    @staticmethod
    def _norm_idx(s: pd.Series) -> pd.Series:
        """统一 DatetimeIndex 单位为 'us'，避免 pandas 2.x 'Cannot losslessly convert units'"""
        if hasattr(s.index, 'as_unit'):
            return s.copy().set_axis(s.index.as_unit('us'))
        return s

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        输入：单只股票的日线 OHLCV DataFrame（index=date）
        输出：添加以下列后的 DataFrame：
          - rs_5d     : 5日RS（个股5日涨幅 - SPY5日涨幅）
          - vol_ratio : 当日量/20日均量（仅展示，不作门槛）
          - signal    : 1=买入候选 / -1=出场 / 0=无效
        """
        if self._spy is None:
            raise RuntimeError("请先调用 set_spy() 传入 SPY 收盘价")

        df = df.copy()
        # 统一 index 单位，避免 pandas 2.x 跨 unit reindex 报错
        if hasattr(df.index, 'as_unit'):
            df.index = df.index.as_unit('us')

        # 5日RS = 个股5日涨幅 - SPY5日涨幅
        spy_norm  = self._norm_idx(self._spy)
        spy_ret5  = spy_norm.pct_change(self.rs_period).reindex(df.index)
        stk_ret5  = df['close'].pct_change(self.rs_period)
        df['rs_5d'] = stk_ret5 - spy_ret5

        # 成交量参考（降权，不作硬门槛）
        vol_ma = df['volume'].rolling(20).mean()
        df['vol_ma20'] = vol_ma
        df['vol_ratio'] = df['volume'] / vol_ma.replace(0, float('nan'))

        # EMA 破位止损线（强牛市里比 SMA 更敏感）
        if self.ema_stop_period and self.ema_stop_period > 0:
            df['ema_stop'] = df['close'].ewm(
                span=self.ema_stop_period, adjust=False
            ).mean()

        # 信号
        df['signal'] = 0
        has_rs = df['rs_5d'].notna()
        df.loc[has_rs & (df['rs_5d'] > 0),  'signal'] =  1
        df.loc[has_rs & (df['rs_5d'] <= 0), 'signal'] = -1

        return df
