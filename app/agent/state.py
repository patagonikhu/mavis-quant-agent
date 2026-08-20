"""Agent 状态定义

SectorSignalState 用于 LangGraph 板块信号分析流程。
AgentState 保留用于简单对话模式。
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

from langchain_core.messages import BaseMessage


# ---- LangGraph 状态（板块信号流程）----

class SectorSignalState(TypedDict, total=False):
    """板块信号 LangGraph 状态"""
    # 用户输入
    user_query: str
    # 路由结果：sector_scan | sector_detail | stock_analysis | general
    intent: str
    # 要分析的板块列表
    sectors_to_analyze: list[str]
    # 原始信号报告（JSON 可序列化）
    raw_signals: dict[str, Any]
    # LLM 综合分析结果
    llm_analysis: dict[str, Any]
    # 最终报告文本
    final_report: str
    # 对话历史
    messages: list[dict]
    # 错误信息
    error: Optional[str]


# ---- 简单对话状态（兼容旧模式）----

class AgentState:
    """简单 React Agent 状态容器"""

    def __init__(self):
        self.messages: list[BaseMessage] = []
        self.current_symbol: Optional[str] = None
        self.current_name: Optional[str] = None
        self.analysis_context: dict[str, Any] = {}

    def add_message(self, message: BaseMessage) -> None:
        self.messages.append(message)

    def set_current_stock(self, symbol: str, name: str = "") -> None:
        self.current_symbol = symbol
        self.current_name = name

    def set_context(self, key: str, value: Any) -> None:
        self.analysis_context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        return self.analysis_context.get(key, default)

    def clear(self) -> None:
        self.messages.clear()
        self.current_symbol = None
        self.current_name = None
        self.analysis_context.clear()
