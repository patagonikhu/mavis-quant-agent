"""K线形态识别

识别常见K线形态，辅助技术面判断。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from app.analysis.technical import kline_to_df
from app.data.models import KlineBar

logger = logging.getLogger(__name__)


@dataclass
class CandlePattern:
    """K线形态结果"""

    name: str           # 形态名称
    direction: str      # "bullish" / "bearish" / "neutral"
    strength: int       # 信号强度 1-3
    description: str    # 形态描述


def _body(row) -> float:
    """实体长度"""
    return abs(row["close"] - row["open"])


def _upper_shadow(row) -> float:
    """上影线长度"""
    return row["high"] - max(row["close"], row["open"])


def _lower_shadow(row) -> float:
    """下影线长度"""
    return min(row["close"], row["open"]) - row["low"]


def _is_bullish(row) -> bool:
    """阳线"""
    return row["close"] > row["open"]


def _is_bearish(row) -> bool:
    """阴线"""
    return row["close"] < row["open"]


def _is_doji(row, threshold: float = 0.05) -> bool:
    """十字星: 实体占比很小"""
    total = row["high"] - row["low"]
    if total == 0:
        return True
    return _body(row) / total < threshold


# ---- 单根K线形态 ----

def detect_doji(row) -> CandlePattern | None:
    """十字星"""
    if _is_doji(row, threshold=0.1):
        return CandlePattern(
            name="十字星",
            direction="neutral",
            strength=1,
            description="开盘价≈收盘价，多空力量均衡，可能变盘",
        )
    return None


def detect_hammer(row) -> CandlePattern | None:
    """锤子线 (底部看涨)"""
    body = _body(row)
    lower = _lower_shadow(row)
    upper = _upper_shadow(row)

    if lower >= body * 2 and upper < body * 0.5 and body > 0:
        return CandlePattern(
            name="锤子线",
            direction="bullish",
            strength=2,
            description="下影线长，实体短，出现在下跌末端为看涨信号",
        )
    return None


def detect_inverted_hammer(row) -> CandlePattern | None:
    """倒锤子线"""
    body = _body(row)
    upper = _upper_shadow(row)
    lower = _lower_shadow(row)

    if upper >= body * 2 and lower < body * 0.5 and body > 0:
        return CandlePattern(
            name="倒锤子线",
            direction="bullish",
            strength=1,
            description="上影线长，实体短，若后续放量确认则看涨",
        )
    return None


def detect_shooting_star(row) -> CandlePattern | None:
    """射击之星 (顶部看跌)"""
    body = _body(row)
    upper = _upper_shadow(row)
    lower = _lower_shadow(row)

    if upper >= body * 2 and lower < body * 0.3 and _is_bullish(row):
        return CandlePattern(
            name="射击之星",
            direction="bearish",
            strength=2,
            description="长上影线阳线，出现在上涨末端为看跌信号",
        )
    return None


def detect_big_yang(row) -> CandlePattern | None:
    """大阳线"""
    total = row["high"] - row["low"]
    if total == 0:
        return None
    body_ratio = _body(row) / total

    if _is_bullish(row) and body_ratio > 0.8 and row.get("change_pct", 0) > 5:
        return CandlePattern(
            name="大阳线",
            direction="bullish",
            strength=2,
            description=f"涨幅 {row['change_pct']:.1f}%，多方强势控盘",
        )
    return None


def detect_big_yin(row) -> CandlePattern | None:
    """大阴线"""
    total = row["high"] - row["low"]
    if total == 0:
        return None
    body_ratio = _body(row) / total

    if _is_bearish(row) and body_ratio > 0.8 and row.get("change_pct", 0) < -5:
        return CandlePattern(
            name="大阴线",
            direction="bearish",
            strength=2,
            description=f"跌幅 {abs(row['change_pct']):.1f}%，空方强势打压",
        )
    return None


# ---- 组合K线形态 ----

def detect_engulfing(curr, prev) -> CandlePattern | None:
    """吞没形态"""
    # 看涨吞没: 前阴后阳，当前实体完全包含前一根
    if (_is_bearish(prev) and _is_bullish(curr)
            and curr["open"] <= prev["close"]
            and curr["close"] >= prev["open"]):
        return CandlePattern(
            name="看涨吞没",
            direction="bullish",
            strength=2,
            description="阳线实体完全吞没前一根阴线，看涨反转信号",
        )

    # 看跌吞没: 前阳后阴
    if (_is_bullish(prev) and _is_bearish(curr)
            and curr["open"] >= prev["close"]
            and curr["close"] <= prev["open"]):
        return CandlePattern(
            name="看跌吞没",
            direction="bearish",
            strength=2,
            description="阴线实体完全吞没前一根阳线，看跌反转信号",
        )

    return None


def detect_morning_star(rows: list) -> CandlePattern | None:
    """启明星 (三根K线看涨反转)

    条件: 大阴线 + 小实体(星线) + 大阳线
    """
    if len(rows) < 3:
        return None

    r1, r2, r3 = rows[-3], rows[-2], rows[-1]

    r1_big_yin = _is_bearish(r1) and _body(r1) > (r1["high"] - r1["low"]) * 0.6
    r2_small = _body(r2) < (r2["high"] - r2["low"] + 1e-10) * 0.3
    r3_big_yang = _is_bullish(r3) and _body(r3) > (r3["high"] - r3["low"]) * 0.6

    if r1_big_yin and r2_small and r3_big_yang:
        return CandlePattern(
            name="启明星",
            direction="bullish",
            strength=3,
            description="大阴线+十字星+大阳线的三根K线组合，强看涨反转信号",
        )
    return None


def detect_evening_star(rows: list) -> CandlePattern | None:
    """黄昏星 (三根K线看跌反转)

    条件: 大阳线 + 小实体(星线) + 大阴线
    """
    if len(rows) < 3:
        return None

    r1, r2, r3 = rows[-3], rows[-2], rows[-1]

    r1_big_yang = _is_bullish(r1) and _body(r1) > (r1["high"] - r1["low"]) * 0.6
    r2_small = _body(r2) < (r2["high"] - r2["low"] + 1e-10) * 0.3
    r3_big_yin = _is_bearish(r3) and _body(r3) > (r3["high"] - r3["low"]) * 0.6

    if r1_big_yang and r2_small and r3_big_yin:
        return CandlePattern(
            name="黄昏星",
            direction="bearish",
            strength=3,
            description="大阳线+十字星+大阴线的三根K线组合，强看跌反转信号",
        )
    return None


# ---- 趋势判断 ----

def judge_trend(bars: list[KlineBar], period: int = 20) -> str:
    """判断近期趋势

    Returns:
        "uptrend" / "downtrend" / "sideways"
    """
    if len(bars) < period:
        return "unknown"

    df = kline_to_df(bars)
    recent = df.tail(period)

    ma5 = recent["close"].rolling(5).mean()
    ma10 = recent["close"].rolling(10).mean()

    latest_ma5 = ma5.iloc[-1]
    latest_ma10 = ma10.iloc[-1]

    # 涨幅判断
    price_change = (recent["close"].iloc[-1] - recent["close"].iloc[0]) / recent["close"].iloc[0] * 100

    if latest_ma5 > latest_ma10 and price_change > 3:
        return "uptrend"
    elif latest_ma5 < latest_ma10 and price_change < -3:
        return "downtrend"
    else:
        return "sideways"


# ---- 综合形态识别接口 ----

def detect_patterns(bars: list[KlineBar]) -> list[CandlePattern]:
    """识别最近K线的所有形态

    Args:
        bars: K线数据列表 (至少 3 条)

    Returns:
        识别到的形态列表
    """
    if len(bars) < 2:
        return []

    df = kline_to_df(bars)
    patterns = []

    # 最新一根K线的单根形态
    latest = df.iloc[-1]
    for detector in [detect_doji, detect_hammer, detect_inverted_hammer,
                     detect_shooting_star, detect_big_yang, detect_big_yin]:
        result = detector(latest)
        if result:
            patterns.append(result)

    # 两根K线组合形态
    if len(df) >= 2:
        prev = df.iloc[-2]
        result = detect_engulfing(latest, prev)
        if result:
            patterns.append(result)

    # 三根K线组合形态
    if len(df) >= 3:
        rows = [df.iloc[-3], df.iloc[-2], df.iloc[-1]]
        for detector in [detect_morning_star, detect_evening_star]:
            result = detector(rows)
            if result:
                patterns.append(result)

    return patterns
