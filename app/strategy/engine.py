"""信号引擎

运行所有策略，汇总信号，计算综合置信度，生成信号报告。
"""

from __future__ import annotations

import logging
from typing import Optional

from app.analysis.technical import TechnicalIndicators, compute_all_indicators, interpret_indicators
from app.data.models import KlineBar
from app.strategy.builtin import BUILTIN_STRATEGIES, BaseStrategy
from app.strategy.models import Direction, Signal, SignalReport

logger = logging.getLogger(__name__)


class SignalEngine:
    """信号引擎"""

    def __init__(self, strategies: Optional[list[BaseStrategy]] = None):
        self.strategies = strategies or BUILTIN_STRATEGIES

    def evaluate(
        self,
        symbol: str,
        name: str,
        bars: list[KlineBar],
        indicators: Optional[TechnicalIndicators] = None,
    ) -> SignalReport:
        """对单只股票运行所有策略，生成综合报告

        Args:
            symbol: 股票代码
            name: 股票名称
            bars: K线数据
            indicators: 预计算的指标 (可选, 不传则自动计算)
        """
        if not bars:
            return SignalReport(
                symbol=symbol, name=name,
                market_summary="K线数据不足，无法生成信号",
            )

        # 计算技术指标
        if indicators is None:
            indicators = compute_all_indicators(bars)

        # 运行所有策略
        signals: list[Signal] = []
        for strategy in self.strategies:
            try:
                signal = strategy.evaluate(symbol, name, indicators)
                if signal is not None:
                    signals.append(signal)
            except Exception as e:
                logger.warning("策略 %s 执行异常: %s", strategy.name, e)

        # 计算综合方向和置信度
        direction, confidence = self._aggregate(signals)

        # 生成市场概况
        summary_parts = interpret_indicators(indicators.latest)
        market_summary = "；".join(summary_parts[:5]) if summary_parts else ""

        # 风险评估
        risk_level = self._assess_risk(indicators, signals)

        return SignalReport(
            symbol=symbol,
            name=name,
            direction=direction,
            confidence=confidence,
            signals=signals,
            market_summary=market_summary,
            risk_level=risk_level,
        )

    def _aggregate(self, signals: list[Signal]) -> tuple[Direction, float]:
        """汇总多策略信号

        规则:
        - 按方向加权投票 (置信度作为权重)
        - 买入权重和 > 卖出权重和 → BUY
        - 卖出权重和 > 买入权重和 → SELL
        - 综合置信度 = 胜出方权重和 / 总权重和
        """
        if not signals:
            return Direction.HOLD, 0.0

        buy_weight = sum(s.confidence for s in signals if s.direction == Direction.BUY)
        sell_weight = sum(s.confidence for s in signals if s.direction == Direction.SELL)
        total = buy_weight + sell_weight

        if total == 0:
            return Direction.HOLD, 0.0

        if buy_weight > sell_weight:
            return Direction.BUY, round(buy_weight / (total + len(signals) * 0.1), 2)
        elif sell_weight > buy_weight:
            return Direction.SELL, round(sell_weight / (total + len(signals) * 0.1), 2)
        else:
            return Direction.HOLD, 0.3

    def _assess_risk(self, indicators: TechnicalIndicators, signals: list[Signal]) -> str:
        """风险评估"""
        latest = indicators.latest
        risk_score = 0

        # RSI 超买超卖
        rsi6 = latest.get("rsi6", 50)
        if rsi6 > 80 or rsi6 < 20:
            risk_score += 2
        elif rsi6 > 70 or rsi6 < 30:
            risk_score += 1

        # KDJ J 值极端
        j = latest.get("j", 50)
        if j > 100 or j < 0:
            risk_score += 1

        # 信号方向冲突 (多空分歧大)
        buy_count = len([s for s in signals if s.direction == Direction.BUY])
        sell_count = len([s for s in signals if s.direction == Direction.SELL])
        if buy_count > 0 and sell_count > 0:
            risk_score += 2

        if risk_score >= 4:
            return "high"
        elif risk_score >= 2:
            return "medium"
        return "low"


# 全局引擎实例
engine = SignalEngine()


def generate_signal(
    symbol: str,
    name: str,
    bars: list[KlineBar],
) -> SignalReport:
    """便捷函数: 生成信号报告"""
    return engine.evaluate(symbol, name, bars)
