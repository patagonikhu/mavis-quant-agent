"""
factors/price/position.py — 价格位置因子 (从 WyckoffTradingAgent 移植)

输入: K 线 (含 close/high/low/open)
输出: dict 含 4 个 Series:
  - close_pos_day    日内位置 (今天收盘在今天 K 线区间的位置, 0-1)
  - close_pos_20     20 日区间位置 (今天收盘在 20 日区间的位置, 0-1)
  - upper_shadow_pct 当日上影线百分比
  - upper_shadow_5d_avg 近 5 日上影线均值

asof 支持: asof_date / as_of_date kwarg, 切片到当日及之前

SOS / 放量上影 / 缩量回踩 形态识别的必备基础指标。
"""
from __future__ import annotations
import pandas as pd
from tools.factors.base import Factor
from tools.factors.utils import asof_slice, df_from_bars


def _upper_shadow_pct(df: pd.DataFrame) -> pd.Series:
    """上影线百分比: (high - max(close, open)) / close × 100%"""
    body_top = pd.concat([df["close"], df["open"]], axis=1).max(axis=1)
    return ((df["high"] - body_top) / df["close"] * 100.0).clip(lower=0)


def _day_close_pos(df: pd.DataFrame) -> pd.Series:
    """日内位置: (close - low) / (high - low), clamp [0, 1]"""
    span = (df["high"] - df["low"]).where((df["high"] - df["low"]) != 0)
    return ((df["close"] - df["low"]) / span).clip(0, 1).fillna(0.5)


def _n_day_close_pos(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """N 日区间位置: (close - N日low) / (N日high - N日low), clamp [0, 1]"""
    hi = df["high"].rolling(window).max()
    lo = df["low"].rolling(window).min()
    return ((df["close"] - lo) / (hi - lo)).clip(0, 1).fillna(0.5)


class PricePositionFactor(Factor):
    """价格位置因子 — 日内位置 + N 日位置 + 上影线"""

    name = "position"
    category = "price"
    dependencies = ["close", "high", "low", "open"]
    description = "价格位置三件套: 日内位置 + 20 日区间位置 + 上影线"
    output_type = "dict"  # dict[4 Series]

    def compute(self, df, **kwargs):
        asof = kwargs.get("asof_date") or kwargs.get("as_of_date")
        window = int(kwargs.get("window", 20))

        df = asof_slice(df_from_bars(df), asof)
        if df is None or len(df) < 2:
            return {
                "close_pos_day": pd.Series(dtype=float),
                "close_pos_20": pd.Series(dtype=float),
                "upper_shadow_pct": pd.Series(dtype=float),
                "upper_shadow_5d_avg": pd.Series(dtype=float),
            }

        us_pct = _upper_shadow_pct(df)
        return {
            "close_pos_day": _day_close_pos(df),
            "close_pos_20": _n_day_close_pos(df, window),
            "upper_shadow_pct": us_pct,
            "upper_shadow_5d_avg": us_pct.rolling(5, min_periods=1).mean(),
        }