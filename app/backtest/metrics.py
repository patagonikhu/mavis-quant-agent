"""回测指标计算

计算胜率、平均涨幅、夏普比率、最大回撤等，并按板块拆解。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class BacktestRecord:
    """单条信号回测记录"""
    date: str
    sector: str
    score: float
    rating: str
    # 持有期内最大涨幅
    max_return: float = 0.0
    # 10日后收盘涨幅
    close_return_10d: float = 0.0
    is_winner: bool = False  # max_return >= 10%


@dataclass
class BacktestMetrics:
    """回测指标汇总"""
    total_signals: int = 0
    strong_signals: int = 0
    medium_signals: int = 0
    win_rate: float = 0.0
    win_rate_strong: float = 0.0
    win_rate_medium: float = 0.0
    avg_max_return: float = 0.0
    max_single_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    by_sector: dict[str, dict[str, Any]] = field(default_factory=dict)

    def report_text(self) -> str:
        lines = [
            "=" * 42,
            "回测结果",
            "=" * 42,
            f"触发信号总数:     {self.total_signals}",
            f"强信号数:         {self.strong_signals}",
            f"中等信号数:       {self.medium_signals}",
            f"胜率（总体）:     {self.win_rate*100:.1f}%",
            f"胜率（强信号）:   {self.win_rate_strong*100:.1f}%",
            f"胜率（中等）:     {self.win_rate_medium*100:.1f}%",
            f"平均最大涨幅:     {self.avg_max_return*100:.1f}%",
            f"最大单次涨幅:     {self.max_single_return*100:.1f}%",
            f"夏普比率:         {self.sharpe_ratio:.2f}",
            f"最大回撤:         {self.max_drawdown*100:.1f}%",
            "",
            "【按板块拆解（Top 5）】",
        ]
        sorted_sectors = sorted(
            self.by_sector.items(),
            key=lambda x: x[1].get("win_rate", 0),
            reverse=True,
        )
        for sector, m in sorted_sectors[:5]:
            lines.append(
                f"  {sector}: 胜率{m['win_rate']*100:.0f}%,"
                f" 平均涨幅{m['avg_return']*100:.1f}%,"
                f" 信号数{m['count']}"
            )
        return "\n".join(lines)


def calculate_metrics(records: list[BacktestRecord]) -> BacktestMetrics:
    """从回测记录计算汇总指标"""
    if not records:
        return BacktestMetrics()

    total = len(records)
    winners = [r for r in records if r.is_winner]
    strong = [r for r in records if "强信号" in r.rating]
    medium = [r for r in records if "中等信号" in r.rating]

    returns = [r.max_return for r in records]
    avg_return = float(np.mean(returns)) if returns else 0.0
    max_return = float(np.max(returns)) if returns else 0.0

    # 夏普（以 10 日持有期收益为基础，无风险利率=0）
    close_returns = [r.close_return_10d for r in records]
    sharpe = 0.0
    if len(close_returns) > 1:
        std = float(np.std(close_returns))
        if std > 0:
            sharpe = round(float(np.mean(close_returns)) / std * math.sqrt(252 / 10), 2)

    # 最大回撤（基于10日收盘收益累积净值序列）
    max_drawdown = _calc_max_drawdown(close_returns)

    # 按板块拆解
    by_sector: dict[str, list[BacktestRecord]] = {}
    for r in records:
        by_sector.setdefault(r.sector, []).append(r)

    sector_metrics = {}
    for sector, recs in by_sector.items():
        w = sum(1 for r in recs if r.is_winner)
        sector_metrics[sector] = {
            "count": len(recs),
            "win_rate": w / len(recs) if recs else 0.0,
            "avg_return": float(np.mean([r.max_return for r in recs])),
        }

    return BacktestMetrics(
        total_signals=total,
        strong_signals=len(strong),
        medium_signals=len(medium),
        win_rate=len(winners) / total if total else 0.0,
        win_rate_strong=sum(1 for r in strong if r.is_winner) / len(strong) if strong else 0.0,
        win_rate_medium=sum(1 for r in medium if r.is_winner) / len(medium) if medium else 0.0,
        avg_max_return=avg_return,
        max_single_return=max_return,
        sharpe_ratio=sharpe,
        max_drawdown=max_drawdown,
        by_sector=sector_metrics,
    )


def _calc_max_drawdown(returns: list[float]) -> float:
    """计算最大回撤（从净值曲线）"""
    if not returns:
        return 0.0
    nav = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        nav *= (1 + r)
        peak = max(peak, nav)
        dd = (peak - nav) / peak
        max_dd = max(max_dd, dd)
    return round(max_dd, 4)
