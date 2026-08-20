"""Agent 工具: 选股筛选 (占位, 后续完善)"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def screen_stocks(condition: str) -> str:
    """按条件筛选股票 (功能开发中)

    Args:
        condition: 筛选条件描述, 如 "市盈率<20且ROE>15%"
    """
    return f"选股筛选功能正在开发中，暂不支持条件: {condition}\n请使用 search_stock 搜索特定股票进行分析。"
