"""Walk-forward 验证

滚动窗口验证，模拟真实使用场景：
  - 用 train_window 天训练（参数优化）
  - 用 test_window 天验证（out-of-sample）
  - 窗口按 test_window 步长滚动

对应设计文档 7.3 节。
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Optional

from app.backtest.metrics import BacktestMetrics, calculate_metrics
from app.data.models import SECTOR_LIST

logger = logging.getLogger(__name__)


@dataclass
class WalkForwardResult:
    """单个滚动窗口的结果"""
    train_start: datetime.date
    train_end: datetime.date
    test_start: datetime.date
    test_end: datetime.date
    best_weights: dict[str, float]
    test_metrics: BacktestMetrics


@dataclass
class WalkForwardReport:
    """Walk-forward 汇总报告"""
    windows: list[WalkForwardResult] = field(default_factory=list)

    @property
    def avg_win_rate(self) -> float:
        rates = [w.test_metrics.win_rate for w in self.windows if w.test_metrics.total_signals > 0]
        return sum(rates) / len(rates) if rates else 0.0

    @property
    def avg_return(self) -> float:
        rets = [w.test_metrics.avg_max_return for w in self.windows if w.test_metrics.total_signals > 0]
        return sum(rets) / len(rets) if rets else 0.0

    @property
    def stability(self) -> float:
        """胜率稳定性：1 - (std / mean)，越接近1越稳定"""
        rates = [w.test_metrics.win_rate for w in self.windows if w.test_metrics.total_signals > 0]
        if len(rates) < 2:
            return 0.0
        import numpy as np
        mean = float(np.mean(rates))
        std = float(np.std(rates))
        return round(1 - std / mean, 3) if mean > 0 else 0.0

    def report_text(self) -> str:
        lines = [
            "=" * 48,
            f"Walk-Forward 验证报告（{len(self.windows)} 个窗口）",
            "=" * 48,
            f"平均样本外胜率:   {self.avg_win_rate*100:.1f}%",
            f"平均样本外涨幅:   {self.avg_return*100:.1f}%",
            f"胜率稳定性:       {self.stability:.3f}（越接近1越稳定）",
            "",
            "【各窗口明细】",
        ]
        for i, w in enumerate(self.windows, 1):
            m = w.test_metrics
            lines.append(
                f"  窗口{i}: 测试 {w.test_start}~{w.test_end} "
                f"信号{m.total_signals}条 "
                f"胜率{m.win_rate*100:.1f}% "
                f"涨幅{m.avg_max_return*100:.1f}%"
            )
        return "\n".join(lines)


async def walk_forward_validation(
    start: datetime.date,
    end: datetime.date,
    sectors: Optional[list[str]] = None,
    train_window: int = 180,   # 训练窗口天数（设计文档建议730，但受限于数据可用性，默认180）
    test_window: int = 45,     # 测试窗口天数（设计文档建议90）
) -> WalkForwardReport:
    """滚动窗口 Walk-Forward 验证

    Args:
        start: 整体回测开始日期
        end: 整体回测结束日期
        sectors: 要验证的板块列表
        train_window: 训练窗口天数
        test_window: 测试窗口天数（每次滚动步长）
    """
    from app.backtest.engine import BacktestEngine
    from app.backtest.optimizer import optimize_weights

    targets = sectors or SECTOR_LIST[:5]
    report = WalkForwardReport()

    current = start
    window_count = 0

    while True:
        train_start = current
        train_end = current + datetime.timedelta(days=train_window)
        test_start = train_end + datetime.timedelta(days=1)
        test_end = test_start + datetime.timedelta(days=test_window)

        if test_end > end:
            break

        window_count += 1
        logger.info(
            "Walk-forward 窗口%d: 训练 %s~%s, 测试 %s~%s",
            window_count, train_start, train_end, test_start, test_end,
        )

        # 训练集：参数优化
        try:
            opt_result = await optimize_weights(train_start, train_end, targets)
            best_weights = opt_result.get("best_weights", {})
        except Exception as e:
            logger.warning("窗口%d 参数优化失败，使用默认权重: %s", window_count, e)
            best_weights = {}

        # 测试集：验证
        try:
            engine = BacktestEngine(test_start, test_end)
            records = await engine.run(targets)
            test_metrics = calculate_metrics(records)
        except Exception as e:
            logger.warning("窗口%d 测试验证失败: %s", window_count, e)
            from app.backtest.metrics import BacktestMetrics
            test_metrics = BacktestMetrics()

        report.windows.append(WalkForwardResult(
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            best_weights=best_weights,
            test_metrics=test_metrics,
        ))

        current += datetime.timedelta(days=test_window)

    logger.info("Walk-forward 完成，共 %d 个窗口", len(report.windows))
    return report
