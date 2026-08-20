"""
factors/timeseries/ma.py - 时序移动平均 (Day 1 示例)

ts_mean / ts_std 的标准实现
"""
import pandas as pd
from tools.factors.base import Factor


class TSMean(Factor):
    """时序移动平均 (参数化窗口)"""

    name = "ts_mean"  # ⚠️ 这是模板, 实际用 ts_mean_5, ts_mean_20
    category = "timeseries"
    dependencies = ["close"]
    description = "滚动平均, 用法: TSMean(window=5)(df)"

    def compute(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        window = kwargs.get('window', 5)
        return df['close'].rolling(window, min_periods=1).mean().rename(
            f"ts_mean_{window}"
        )


class TSStd(Factor):
    """时序标准差"""

    name = "ts_std"
    category = "timeseries"
    dependencies = ["close"]
    description = "滚动标准差"

    def compute(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        window = kwargs.get('window', 20)
        return df['close'].rolling(window, min_periods=1).std().rename(
            f"ts_std_{window}"
        )
