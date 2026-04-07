import numpy as np
import pandas as pd


def compute_obv_trend(
    df: pd.DataFrame,
    period: int = 20,
) -> pd.DataFrame:
    """
    添加 obv_trend 列（OBV 趋势强度，归一化）。

    OBV（On-Balance Volume）= 累计（成交量 × sign(当日涨跌)）。
    obv_trend = OBV 在过去 period 天的线性斜率 / 同期日均成交量（归一化）。

      - 正值 = OBV 上升，资金净流入（吸筹）
      - 负值 = OBV 下降，资金净流出（派发）
      - 绝对值越大 = 资金流向越强烈

    用于在价格突破时验证资金面支撑，补充量价配合信号。
    """
    direction = np.sign(df['close'].diff().fillna(0))
    obv = (direction * df['volume']).cumsum()

    avg_vol = df['volume'].rolling(period).mean()

    def _slope(vals: np.ndarray) -> float:
        if len(vals) < period or np.isnan(vals).any():
            return np.nan
        x = np.arange(len(vals), dtype=float)
        return np.polyfit(x, vals, 1)[0]

    obv_slope = obv.rolling(period).apply(_slope, raw=True)
    # 除以平均成交量归一化，使不同价位/成交量的股票可横向比较
    df['obv_trend'] = obv_slope / avg_vol.replace(0, np.nan)
    return df
