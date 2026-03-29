from abc import ABC, abstractmethod
import pandas as pd


class Strategy(ABC):
    """
    策略基类。所有策略继承此类，实现 generate_signals()。

    信号约定：
      1  = 买入
     -1  = 卖出
      0  = 持有/无操作
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        ...

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        输入标准 OHLCV DataFrame（列名：open/high/low/close/volume），
        返回新增 'signal' 列的 DataFrame。
        """
        ...

    def on_buy(self, price: float, dt):
        """买入信号触发时的回调（用于接入实盘下单）"""
        pass

    def on_sell(self, price: float, dt):
        """卖出信号触发时的回调（用于接入实盘下单）"""
        pass

    def __repr__(self):
        return f"<Strategy: {self.name}>"
