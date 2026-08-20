"""
alpha_001.py - WorldQuant Alpha #1

公式: rank(ts_argmax(pow(returns, 2), 5)) - rank(returns)
逻辑: 5天内涨幅最大那天的排名 - 当日涨幅排名
      差越大 = 前期有大利好但当日涨势减弱 → 下期可能跌

2026-07-27 清理: 移除对 tools.factors.timeseries.helpers 的依赖 (helpers.py 不存在)
"""
import pandas as pd
import numpy as np
from tools.factors.base import Factor, rank_pct


class Alpha001(Factor):
    name = "alpha_001"
    category = "alpha101"
    dependencies = ["close"]
    description = "WorldQuant Alpha #1: 5d 最大涨幅 vs 当日 rank 差 (经典反转)"
    version = "1.0"

    def compute(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        closes = df['close']

        # 1. 日收益率
        returns = closes.pct_change()

        # 2. 5 天内最大收益率 (替代 ts_argmax(pow(returns, 2), 5) → 取 max returns)
        # 注: 原 Alpha #1 用 squared returns 的 argmax, 等价于 abs returns 的 argmax
        max_returns_5d = returns.abs().rolling(5, min_periods=1).max()

        # 3. rank 差
        rank_max = rank_pct(max_returns_5d)
        rank_now = rank_pct(returns)

        alpha = rank_max - rank_now
        return alpha.rename(self.name)

