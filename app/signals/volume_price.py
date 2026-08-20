"""量价信号模块

实现设计文档 4.1 节的四类量价信号：
- 放量上涨（volume_breakout）
- 涨停家数突变（limit_up_surge）
- 量价齐升（volume_price_uptrend）
- 突破压力位（breakout_resistance）
"""

from __future__ import annotations

import math

import numpy as np

from app.data.models import SectorKlineBar
from app.signals.base import SignalResult


def volume_breakout(bars: list[SectorKlineBar], lookback: int = 20) -> SignalResult:
    """放量上涨信号

    volume_ratio = today_volume / MA(volume, lookback)
    触发条件: volume_ratio > 1.5 AND price_change > 2%
    """
    if len(bars) < lookback + 1:
        return SignalResult(reason="数据不足")

    today = bars[-1]
    past = bars[-(lookback + 1):-1]

    avg_vol = np.mean([b.volume for b in past])
    if avg_vol <= 0:
        return SignalResult(reason="成交量均值为0")

    volume_ratio = today.volume / avg_vol
    price_change = today.change_pct / 100.0

    triggered = volume_ratio > 1.5 and price_change > 0.02

    # 强度评分 0-10
    if volume_ratio > 3 and price_change > 0.05:
        score = 10.0
    elif volume_ratio > 2 and price_change > 0.03:
        score = 7.0
    elif volume_ratio > 1.5 and price_change > 0.02:
        score = 5.0
    else:
        score = 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "volume_ratio": round(volume_ratio, 2),
            "price_change_pct": round(today.change_pct, 2),
            "avg_volume_20d": round(avg_vol, 0),
        },
        reason=f"量比={volume_ratio:.1f}，涨幅={today.change_pct:.1f}%",
    )


def limit_up_surge(bars: list[SectorKlineBar], lookback: int = 20) -> SignalResult:
    """涨停家数突变信号（核心信号）

    使用 Z-score 异常检测：
    - 绝对数量门槛：今日涨停 >= 3
    - 统计异常：z_score >= 2
    - 倍数门槛：今日 >= 历史均值 * 3
    """
    if len(bars) < lookback + 1:
        return SignalResult(reason="数据不足")

    today_count = bars[-1].limit_up_count
    history = [b.limit_up_count for b in bars[-(lookback + 1):-1]]

    mean = float(np.mean(history))
    std = float(np.std(history))

    z_score = (today_count - mean) / std if std > 0 else 0.0

    triggered = (
        today_count >= 3
        and z_score >= 2.0
        and (mean == 0 or today_count >= mean * 3)
    )

    score = min(10.0, z_score * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_count": today_count,
            "mean_20d": round(mean, 1),
            "z_score": round(z_score, 2),
        },
        reason=f"涨停{today_count}家（均值{mean:.1f}，Z={z_score:.1f}）",
    )


def volume_price_uptrend(bars: list[SectorKlineBar], days: int = 3) -> SignalResult:
    """量价齐升（持续性信号）

    连续 N 日收盘价和成交量同步上升。
    """
    if len(bars) < days + 1:
        return SignalResult(reason="数据不足")

    recent = bars[-days:]
    price_up = all(
        recent[i].close_price > recent[i - 1].close_price
        for i in range(1, days)
    )
    volume_up = all(
        recent[i].volume > recent[i - 1].volume
        for i in range(1, days)
    )

    triggered = price_up and volume_up
    score = 5.0 if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "days": days,
            "price_up": price_up,
            "volume_up": volume_up,
            "recent_closes": [round(b.close_price, 2) for b in recent],
        },
        reason=f"连续{days}日量价齐升" if triggered else f"未满足连续{days}日量价齐升",
    )


def breakout_resistance(bars: list[SectorKlineBar], lookback: int = 60) -> SignalResult:
    """突破压力位信号

    检测突破：60日新高、MA20、MA60
    """
    if len(bars) < max(lookback, 60) + 1:
        return SignalResult(reason="数据不足")

    today_close = bars[-1].close_price
    yesterday_close = bars[-2].close_price

    closes = [b.close_price for b in bars[-lookback:]]
    # 排除今日，取前60日高点
    high_60d = max(closes[:-1])
    ma20 = float(np.mean([b.close_price for b in bars[-21:-1]]))
    ma60 = float(np.mean([b.close_price for b in bars[-61:-1]]))

    breaks = []
    if today_close > high_60d:
        breaks.append("60日新高")
    if today_close > ma20 and yesterday_close <= ma20:
        breaks.append("突破MA20")
    if today_close > ma60 and yesterday_close <= ma60:
        breaks.append("突破MA60")

    triggered = len(breaks) > 0
    score = min(10.0, len(breaks) * 4.0)

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "breaks": breaks,
            "today_close": today_close,
            "high_60d": round(high_60d, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
        },
        reason="突破: " + "、".join(breaks) if breaks else "未突破任何压力位",
    )
