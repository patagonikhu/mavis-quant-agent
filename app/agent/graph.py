"""ReAct Agent

简化对话入口：用户输入 → LLM 看工具列表 → 自己决定调哪些工具 → 循环到完成。
"""
from __future__ import annotations

import logging
from typing import Optional

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools.market_data import (
    get_fund_flow,
    get_kline_data,
    get_market_overview,
    get_realtime_quote,
    search_stock,
)
from app.agent.tools.screening import screen_stocks
from app.agent.tools.signal import generate_stock_signal
from app.agent.tools.technical import (
    analyze_fundamental,
    calculate_technical_indicators,
    detect_candlestick_patterns,
)

logger = logging.getLogger(__name__)

ALL_TOOLS = [
    search_stock,
    get_realtime_quote,
    get_kline_data,
    get_fund_flow,
    get_market_overview,
    calculate_technical_indicators,
    analyze_fundamental,
    detect_candlestick_patterns,
    generate_stock_signal,
    screen_stocks,
]


async def chat(user_message: str, history: Optional[list[dict]] = None) -> str:
    """ReAct 对话入口：用户消息直接进 ReAct agent"""
    try:
        from langchain.agents import AgentExecutor, create_react_agent
        from langchain_core.prompts import PromptTemplate

        from app.llm.client import get_llm

        prompt_template = PromptTemplate.from_template(
            SYSTEM_PROMPT + "\n\n可用工具:\n{tools}\n工具名称: {tool_names}\n\n"
            "{agent_scratchpad}\n\nHuman: {input}\nAssistant:"
        )
        llm = get_llm()
        agent = create_react_agent(llm, ALL_TOOLS, prompt_template)
        executor = AgentExecutor(
            agent=agent, tools=ALL_TOOLS, verbose=False, handle_parsing_errors=True
        )

        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: executor.invoke({"input": user_message})
        )
        return result.get("output", "抱歉，暂时无法回答。")
    except Exception as e:
        logger.error("chat 失败: %s", e)
        return f"执行出错: {str(e)[:200]}"


def reset_agent() -> None:
    """兼容旧调用，重置无副作用（ReAct agent 无全局状态）"""
    pass