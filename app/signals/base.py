"""信号层基础模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalResult:
    """单个信号的检测结果"""
    triggered: bool = False
    score: float = 0.0          # 0-10
    detail: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
