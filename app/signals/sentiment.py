"""情绪信号模块

实现设计文档 4.5 节的三类情绪信号：
- 板块热度排名上升（hot_rank_surge）
- 研报数量突增（research_report_surge）+ 时间衰减
- 股吧讨论量异常（discussion_anomaly）—— Phase 2 桩实现
"""

from __future__ import annotations

import math
from datetime import date, datetime

import numpy as np

from app.data.models import HotRankRecord, ResearchReportRecord
from app.signals.base import SignalResult

# 研报情绪衰减系数：lambda=0.10，半衰期约7天
# 含义：一次研报爆发的情绪影响在7天后剩余50%，30天后剩余5%
_RESEARCH_DECAY_LAMBDA = 0.10


def _research_decay(count: float, record_date: date | datetime | None) -> float:
    """对单日研报数量施加时间衰减。

    Args:
        count: 当日研报数量
        record_date: 研报记录日期

    Returns:
        衰减后的有效研报数
    """
    if record_date is None:
        return count
    if isinstance(record_date, datetime):
        record_date = record_date.date()
    days = max(0, (date.today() - record_date).days)
    return count * math.exp(-_RESEARCH_DECAY_LAMBDA * days)


def hot_rank_surge(rank_history: list[HotRankRecord]) -> SignalResult:
    """板块热度排名上升信号

    触发条件（满足任一）：
    - 当前排名进入前5
    - 排名较近5日均值上升10位以上（rank 数值变小）
    """
    if not rank_history:
        return SignalResult(reason="无热度排名数据")

    today_rank = rank_history[-1].rank
    past_ranks = [r.rank for r in rank_history[:-1]]

    avg_rank = float(np.mean(past_ranks)) if past_ranks else today_rank
    rank_improvement = avg_rank - today_rank  # 正值=排名上升

    triggered = today_rank <= 5 or rank_improvement >= 10
    score = 0.0
    if triggered:
        if today_rank <= 5:
            score = min(10.0, 8.0 + (5 - today_rank))
        else:
            score = min(10.0, rank_improvement / 2)

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_rank": today_rank,
            "avg_rank": round(avg_rank, 1),
            "rank_improvement": round(rank_improvement, 1),
        },
        reason=(
            f"热度排名第{today_rank}（较均值上升{rank_improvement:.0f}位）"
            if triggered
            else f"热度排名第{today_rank}，无明显上升"
        ),
    )


def research_report_surge(
    report_history: list[ResearchReportRecord],
    threshold_ratio: float = 3.0,
) -> SignalResult:
    """研报数量突增信号（含时间衰减）

    将每日研报数量经时间衰减后加权，衰减后累积研报密度 /
    历史均值 > threshold_ratio 触发。

    时间衰减：lambda=0.10，半衰期约7天。
    含义：今天的研报爆发权重=1.0，7天前=0.5，30天前=0.05，
    避免3周前的研报热度今天仍触发信号。
    """
    if len(report_history) < 5:
        return SignalResult(reason="研报数据不足")

    # 计算每条记录的衰减后有效研报数
    decayed_counts = []
    for r in report_history:
        record_date = getattr(r, "record_date", None) or getattr(r, "date", None)
        decayed_counts.append(_research_decay(float(r.count), record_date))

    today_decayed = decayed_counts[-1]
    past_decayed = decayed_counts[:-1]
    avg = float(np.mean(past_decayed)) if past_decayed else 0.0

    ratio = today_decayed / avg if avg > 0 else 0.0
    triggered = ratio > threshold_ratio and report_history[-1].count >= 3

    score = min(10.0, ratio * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_count": report_history[-1].count,
            "today_decayed": round(today_decayed, 2),
            "avg_decayed": round(avg, 2),
            "ratio": round(ratio, 2),
            "decay_lambda": _RESEARCH_DECAY_LAMBDA,
        },
        reason=f"研报突增（衰减后{today_decayed:.1f}篇，均值{avg:.1f}，{ratio:.1f}倍）" if triggered
               else f"研报数量正常（衰减后{today_decayed:.1f}篇，均值{avg:.1f}）",
    )


def discussion_anomaly(discussion_counts: list[int]) -> SignalResult:
    """股吧讨论量异常信号（Z-score 检测）

    Args:
        discussion_counts: 近 N 日讨论量（整数列表，最新在末尾）
    """
    if len(discussion_counts) < 5:
        return SignalResult(reason="讨论量数据不足")

    today = discussion_counts[-1]
    history = discussion_counts[:-1]

    mean = float(np.mean(history))
    std = float(np.std(history))
    z_score = (today - mean) / std if std > 0 else 0.0

    triggered = z_score > 2.0 and today > mean
    score = min(10.0, z_score * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_count": today,
            "mean": round(mean, 1),
            "z_score": round(z_score, 2),
        },
        reason=f"讨论量异常（今日{today}，均值{mean:.0f}，Z={z_score:.1f}）" if triggered
               else f"讨论量正常（Z={z_score:.1f}）",
    )



def hot_rank_surge(rank_history: list[HotRankRecord]) -> SignalResult:
    """板块热度排名上升信号

    触发条件（满足任一）：
    - 当前排名进入前5
    - 排名较近5日均值上升10位以上（rank 数值变小）
    """
    if not rank_history:
        return SignalResult(reason="无热度排名数据")

    today_rank = rank_history[-1].rank
    past_ranks = [r.rank for r in rank_history[:-1]]

    avg_rank = float(np.mean(past_ranks)) if past_ranks else today_rank
    rank_improvement = avg_rank - today_rank  # 正值=排名上升

    triggered = today_rank <= 5 or rank_improvement >= 10
    score = 0.0
    if triggered:
        if today_rank <= 5:
            score = min(10.0, 8.0 + (5 - today_rank))
        else:
            score = min(10.0, rank_improvement / 2)

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_rank": today_rank,
            "avg_rank": round(avg_rank, 1),
            "rank_improvement": round(rank_improvement, 1),
        },
        reason=(
            f"热度排名第{today_rank}（较均值上升{rank_improvement:.0f}位）"
            if triggered
            else f"热度排名第{today_rank}，无明显上升"
        ),
    )


def research_report_surge(
    report_history: list[ResearchReportRecord],
    threshold_ratio: float = 3.0,
) -> SignalResult:
    """研报数量突增信号

    今日研报数 / 历史均值 > threshold_ratio 触发。
    """
    if len(report_history) < 5:
        return SignalResult(reason="研报数据不足")

    today_count = report_history[-1].count
    past_counts = [r.count for r in report_history[:-1]]
    avg = float(np.mean(past_counts))

    ratio = today_count / avg if avg > 0 else 0.0
    triggered = ratio > threshold_ratio and today_count >= 3

    score = min(10.0, ratio * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_count": today_count,
            "avg_30d": round(avg, 1),
            "ratio": round(ratio, 2),
        },
        reason=f"今日研报{today_count}篇（30日均值{avg:.1f}，{ratio:.1f}倍）" if triggered
               else f"研报数量正常（今日{today_count}篇，均值{avg:.1f}）",
    )


def discussion_anomaly(discussion_counts: list[int]) -> SignalResult:
    """股吧讨论量异常信号（Z-score 检测）

    Args:
        discussion_counts: 近 N 日讨论量（整数列表，最新在末尾）
    """
    if len(discussion_counts) < 5:
        return SignalResult(reason="讨论量数据不足")

    today = discussion_counts[-1]
    history = discussion_counts[:-1]

    mean = float(np.mean(history))
    std = float(np.std(history))
    z_score = (today - mean) / std if std > 0 else 0.0

    triggered = z_score > 2.0 and today > mean
    score = min(10.0, z_score * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_count": today,
            "mean": round(mean, 1),
            "z_score": round(z_score, 2),
        },
        reason=f"讨论量异常（今日{today}，均值{mean:.0f}，Z={z_score:.1f}）" if triggered
               else f"讨论量正常（Z={z_score:.1f}）",
    )
