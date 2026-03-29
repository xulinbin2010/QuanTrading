import pandas as pd
from .base import Strategy


class MACrossover(Strategy):
    """
    均线交叉策略。

    逻辑：
      - 快线上穿慢线（金叉）→ 买入信号 +1
      - 快线下穿慢线（死叉）→ 卖出信号 -1
      - 其他             → 持有     0

    参数：
      fast  : 快线周期，默认 10 日
      slow  : 慢线周期，默认 30 日
    """

    def __init__(self, fast: int = 10, slow: int = 30):
        if fast >= slow:
            raise ValueError(f"fast({fast}) 必须小于 slow({slow})")
        self.fast = fast
        self.slow = slow

    @property
    def name(self) -> str:
        return f"MA_Crossover(fast={self.fast}, slow={self.slow})"

    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        df['ma_fast'] = df['close'].rolling(self.fast).mean()
        df['ma_slow'] = df['close'].rolling(self.slow).mean()

        # 当前快线与慢线的位置关系
        df['_above'] = df['ma_fast'] > df['ma_slow']

        # 金叉：今天快线在慢线上方，昨天不是
        # 死叉：今天快线在慢线下方，昨天不是
        prev_above = df['_above'].shift(1).infer_objects(copy=False).fillna(False)
        df['signal'] = 0
        df.loc[ df['_above'] & ~prev_above, 'signal'] = 1   # 金叉
        df.loc[~df['_above'] &  prev_above, 'signal'] = -1  # 死叉

        df.drop(columns=['_above'], inplace=True)
        return df
