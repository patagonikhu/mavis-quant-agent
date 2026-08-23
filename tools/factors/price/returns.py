"""
factors/price/returns.py - 日收益率因子 (Day 1 示例)

最简单的因子: 算日收益率
不依赖任何外部 (不调 原 dump_data), 纯 pandas

Day 1 用来测试 factor 框架能不能跑通
"""
import pandas as pd
import numpy as np
from tools.factors.base import Factor


class Returns(Factor):
    """日收益率 (close-to-close)"""

    name = "returns"
    category = "price"
    dependencies = ["close"]
    description = "日收益率 = (close_t - close_{t-1}) / close_{t-1}"

    def compute(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        return df['close'].pct_change().rename(self.name)


class LogReturns(Factor):
    """对数收益率"""

    name = "log_returns"
    category = "price"
    dependencies = ["close"]
    description = "对数日收益率 = log(close_t / close_{t-1})"

    def compute(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        return np.log(df['close'] / df['close'].shift(1)).rename(self.name)
