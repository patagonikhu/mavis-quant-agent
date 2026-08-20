"""
alpha_ga_001.py - GA 挖掘的 Alpha 因子 (2026-07-27 入库)

公式: (close_pct_rank - vol_5_20 + ma20_dist + rsi) * (vol_5_20 + 0.711 - vol_price_corr - obv)

来源: gplearn Genetic Programming, 在 4 张票 (300274/688012/002371/300308) 上 Top 1 公式完全一致
性能: |IC| 0.077-0.671 (跨票), 300274 上 0.453 (强信号)
方向: 多数票反向 (高 factor 值→5d 跌), 002371 正向 (5d 涨)
使用: factor > 阈值 → 5d 跌信号; 实际使用需带 sign(测试IC) 调整

输入: df (pd.DataFrame) 含 open/high/low/close/volume
输出: pd.Series, 因子值 (高/低 视方向而定)
"""
import pandas as pd
import numpy as np
from tools.factors.base import Factor


class AlphaGA001(Factor):
    """GA 挖掘的 alpha_ga_001 因子

    6 个原子特征:
      - close_pct_rank: 60日 价格分位 (0-1)
      - vol_5_20: 5日均量 / 20日均量
      - ma20_dist: (close - MA20) / MA20
      - rsi: 14日 RSI
      - vol_price_corr: 5日 量价相关系数
      - obv: 累积 OBV (标准化后)

    公式:
      factor = (close_pct_rank - vol_5_20 + ma20_dist + rsi)
             * (vol_5_20 + 0.711 - vol_price_corr - obv_norm)
    """

    name = "alpha_ga_001"
    category = "alpha101"
    dependencies = ["close", "volume"]
    description = "GA 挖掘的 5d 涨跌预测因子 (gplearn, 2026-07-27)"
    version = "1.0"

    def compute(self, df: pd.DataFrame, **kwargs) -> pd.Series:
        closes = df['close']
        volumes = df['volume']

        # X0 = obv (标准化)
        obv_raw = (np.sign(closes.diff()) * volumes).cumsum()
        # 用滚动 60 日标准化
        obv_norm = (obv_raw - obv_raw.rolling(60, min_periods=20).mean()) / \
                   (obv_raw.rolling(60, min_periods=20).std() + 1e-9)

        # X3 = vol_5_20
        vol_5_20 = volumes.rolling(5).mean() / (volumes.rolling(20).mean() + 1e-9)

        # X4 = rsi (14)
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - 100 / (1 + gain / (loss + 1e-9))

        # X7 = close_pct_rank (60日滚动)
        close_pct_rank = closes.rolling(60, min_periods=20).apply(
            lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
        )

        # X8 = vol_price_corr (5日)
        vol_price_corr = closes.rolling(5).corr(volumes)

        # X9 = ma20_dist
        ma20 = closes.rolling(20).mean()
        ma20_dist = (closes - ma20) / (ma20 + 1e-9)

        # 公式: (X7 - X3 + X9 + X4) * (X3 + 0.711 - X8 - X0)
        part1 = close_pct_rank - vol_5_20 + ma20_dist + rsi / 100  # rsi 缩放到 0-1 跟 pct_rank 同量纲
        part2 = vol_5_20 + 0.711 - vol_price_corr - obv_norm
        factor = part1 * part2

        return factor.rename(self.name)
