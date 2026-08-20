"""龙头信号模块

实现设计文档 4.3 节的三类龙头信号：
- 识别龙头股（identify_leaders）
- 龙头启动（leader_launching）
- 板块扩散（sector_diffusion）
"""

from __future__ import annotations

import datetime

import numpy as np

from app.data.models import KlineBar, SectorConstituentStock, SectorKlineBar
from app.signals.base import SignalResult


def identify_leaders(
    constituents: list[SectorConstituentStock],
    top_n: int = 3,
) -> list[SectorConstituentStock]:
    """识别板块龙头股

    规则：流通市值 top_n 即为龙头（Phase 1 简化版，不需要历史涨停数据）。
    后续可叠加「年涨幅排名」和「历史涨停领涨次数」。
    """
    if not constituents:
        return []
    sorted_by_mv = sorted(
        [c for c in constituents if c.circ_mv > 0],
        key=lambda c: c.circ_mv,
        reverse=True,
    )
    return sorted_by_mv[:top_n]


def leader_launching(
    leader_klines: dict[str, list[KlineBar]],
    lookback_consecutive: int = 2,
    return_threshold: float = 0.15,
    volume_ratio_threshold: float = 2.0,
) -> SignalResult:
    """龙头启动信号

    判定条件（满足任一）：
    1. 连续涨停 >= 2 日
    2. 5日涨幅 > 15% 且 5日平均量比 > 2

    Args:
        leader_klines: {symbol: [KlineBar]} 龙头股K线（至少20根）
    """
    if not leader_klines:
        return SignalResult(reason="无龙头股数据")

    best_sym = ""
    best_consecutive = 0
    best_return_5d = 0.0
    best_vol_ratio = 0.0

    for sym, bars in leader_klines.items():
        if len(bars) < 6:
            continue

        # 连续涨停天数（收盘涨幅 >= 9.9%）
        consecutive = 0
        for b in reversed(bars[-5:]):
            if b.change_pct >= 9.9:
                consecutive += 1
            else:
                break

        # 5日涨幅
        return_5d = (bars[-1].close_price - bars[-6].close_price) / bars[-6].close_price

        # 5日平均量比（相对过去20日均量）
        if len(bars) >= 25:
            avg_vol_20 = np.mean([b.volume for b in bars[-25:-5]])
            avg_vol_5 = np.mean([b.volume for b in bars[-5:]])
            vol_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 0.0
        else:
            vol_ratio = 0.0

        if consecutive > best_consecutive or return_5d > best_return_5d:
            best_sym = sym
            best_consecutive = max(best_consecutive, consecutive)
            best_return_5d = max(best_return_5d, return_5d)
            best_vol_ratio = max(best_vol_ratio, vol_ratio)

    triggered = (
        best_consecutive >= lookback_consecutive
        or (best_return_5d > return_threshold and best_vol_ratio > volume_ratio_threshold)
    )

    if triggered:
        if best_consecutive >= lookback_consecutive:
            score = min(10.0, 5.0 + best_consecutive * 2.0)
            reason = f"龙头{best_sym}连续{best_consecutive}日涨停"
        else:
            score = min(10.0, best_return_5d * 50)
            reason = f"龙头{best_sym} 5日涨幅{best_return_5d*100:.1f}%，量比{best_vol_ratio:.1f}"
    else:
        score = 0.0
        reason = "龙头股未见明显启动"

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "leader_symbol": best_sym,
            "consecutive_limit_up": best_consecutive,
            "return_5d_pct": round(best_return_5d * 100, 1),
            "volume_ratio_5d": round(best_vol_ratio, 2),
        },
        reason=reason,
    )


def sector_diffusion(
    sector_bars: list[SectorKlineBar],
    leader_launch_date: datetime.date | None,
    max_days_after: int = 5,
) -> SignalResult:
    """板块扩散信号

    龙头启动后 1-5 日内，板块涨停家数 > 启动前均值 * 2 且 >= 3 家。
    """
    if not sector_bars or leader_launch_date is None:
        return SignalResult(reason="无法判断扩散（无龙头启动日期）")

    today = sector_bars[-1]
    days_since = (today.trade_date - leader_launch_date).days

    if days_since < 1 or days_since > max_days_after:
        return SignalResult(
            reason=f"龙头启动后第{days_since}日，不在扩散观察窗口(1-{max_days_after}日)",
        )

    # 启动前5日的涨停家数均值
    pre_idx = None
    for i, b in enumerate(sector_bars):
        if b.trade_date == leader_launch_date:
            pre_idx = i
            break

    if pre_idx is None or pre_idx < 5:
        return SignalResult(reason="找不到龙头启动日前的历史数据")

    pre_avg = float(np.mean([b.limit_up_count for b in sector_bars[pre_idx - 5:pre_idx]]))
    today_count = today.limit_up_count

    triggered = today_count > pre_avg * 2 and today_count >= 3
    score = min(10.0, today_count / max(pre_avg, 1) * 3) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_limit_up": today_count,
            "pre_launch_avg": round(pre_avg, 1),
            "days_since_leader": days_since,
        },
        reason=(
            f"龙头启动后第{days_since}日，板块涨停{today_count}家"
            f"（启动前均值{pre_avg:.1f}）"
        ),
    )
