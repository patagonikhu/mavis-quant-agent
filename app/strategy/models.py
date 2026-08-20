"""信号模型定义"""

import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Direction(str, Enum):
    """信号方向"""
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class Signal(BaseModel):
    """单条交易信号"""

    symbol: str = Field(..., description="股票代码")
    name: str = Field(default="", description="股票名称")
    direction: Direction = Field(..., description="信号方向")
    confidence: float = Field(..., ge=0, le=1, description="置信度 0-1")
    strategy: str = Field(..., description="产生信号的策略名称")
    reason: str = Field(..., description="信号原因说明")
    indicators: dict = Field(default_factory=dict, description="相关指标值")
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)


class SignalReport(BaseModel):
    """信号报告 (多策略综合)"""

    symbol: str = Field(..., description="股票代码")
    name: str = Field(default="", description="股票名称")

    # 综合信号
    direction: Direction = Field(default=Direction.HOLD)
    confidence: float = Field(default=0.0, ge=0, le=1)

    # 各策略信号
    signals: list[Signal] = Field(default_factory=list)

    # 市场概况
    market_summary: str = Field(default="")

    # 风险等级
    risk_level: str = Field(default="medium", description="low/medium/high")

    # 风险提示 (必须)
    disclaimer: str = Field(
        default="⚠️ 以上分析仅供参考，不构成投资建议。股市有风险，投资需谨慎。",
    )

    def buy_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == Direction.BUY]

    def sell_signals(self) -> list[Signal]:
        return [s for s in self.signals if s.direction == Direction.SELL]

    def summary_text(self) -> str:
        """生成摘要文本"""
        buy_count = len(self.buy_signals())
        sell_count = len(self.sell_signals())
        total = len(self.signals)

        lines = [
            f"📊 {self.name}({self.symbol}) 信号报告",
            f"综合方向: {self.direction.value.upper()} (置信度: {self.confidence:.0%})",
            f"买入信号: {buy_count}/{total}  卖出信号: {sell_count}/{total}",
        ]

        if self.market_summary:
            lines.append(f"\n📝 {self.market_summary}")

        for s in self.signals:
            icon = "🟢" if s.direction == Direction.BUY else "🔴" if s.direction == Direction.SELL else "⚪"
            lines.append(f"  {icon} [{s.strategy}] {s.reason} (置信度{s.confidence:.0%})")

        lines.append(f"\n{self.disclaimer}")
        return "\n".join(lines)
