"""LangGraph Agent

板块信号分析流程图：
  intent_router → signal_collector → llm_analyzer → explanation_generator → END

对话（个股/通用查询）走简化 ReAct 路径：
  intent_router → react_chat → END
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, StateGraph

from app.agent.prompts import SYSTEM_PROMPT, build_sector_analysis_prompt, build_signals_text
from app.agent.state import SectorSignalState
from app.agent.tools.market_data import (
    get_fund_flow,
    get_kline_data,
    get_market_overview,
    get_realtime_quote,
    search_stock,
)
from app.agent.tools.screening import screen_stocks
from app.agent.tools.sector import get_sector_list, scan_all_sectors, scan_sector_signal
from app.agent.tools.signal import generate_stock_signal
from app.agent.tools.backtest import run_quick_backtest
from app.agent.tools.optimize import run_walk_forward, optimize_signal_weights
from app.agent.tools.technical import (
    analyze_fundamental,
    calculate_technical_indicators,
    detect_candlestick_patterns,
)
from app.data.models import SECTOR_LIST

logger = logging.getLogger(__name__)

# 所有工具（供 ReAct 路径使用）
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
    scan_sector_signal,
    scan_all_sectors,
    get_sector_list,
    run_quick_backtest,
    run_walk_forward,
    optimize_signal_weights,
]

# 板块意图关键词
_SECTOR_SCAN_KEYWORDS = ["板块", "行业", "启动", "热点", "扫描", "信号", "哪个板块", "哪些板块"]
_SECTOR_NAMES_ALL = set(SECTOR_LIST)


def _detect_intent(query: str) -> tuple[str, list[str]]:
    """路由意图检测

    Returns:
        (intent, sectors)
        intent: "sector_scan" | "sector_detail" | "general"
    """
    # 检测是否提到特定板块
    mentioned = [s for s in _SECTOR_NAMES_ALL if s in query]
    if mentioned:
        return "sector_detail", mentioned

    if any(kw in query for kw in _SECTOR_SCAN_KEYWORDS):
        return "sector_scan", []

    return "general", []


# ---- LangGraph 节点 ----

async def intent_router(state: SectorSignalState) -> SectorSignalState:
    """路由节点：判断用户意图"""
    query = state.get("user_query", "")
    intent, sectors = _detect_intent(query)
    return {**state, "intent": intent, "sectors_to_analyze": sectors}


async def signal_collector(state: SectorSignalState) -> SectorSignalState:
    """信号采集节点：调用信号评估逻辑"""
    intent = state.get("intent", "general")
    sectors = state.get("sectors_to_analyze", [])
    query = state.get("user_query", "")

    if intent == "sector_scan" or (intent == "general" and not sectors):
        # 全市场扫描：取 SECTOR_LIST
        sectors = SECTOR_LIST

    raw_signals: dict = {}
    from app.data import get_data_provider
    from app.data.models import Period
    from app.signals import evaluate_sector
    from app.signals.leader import identify_leaders

    provider = get_data_provider()
    for sector_name in sectors[:10]:  # 最多处理10个板块
        try:
            sector_bars = await provider.get_sector_kline(sector_name, count=60)
            if len(sector_bars) < 20:
                continue
            constituents = await provider.get_sector_constituents(sector_name)
            leaders = identify_leaders(constituents, top_n=3)
            leader_klines: dict = {}
            for ldr in leaders:
                bars = await provider.get_kline(ldr.symbol, Period.DAILY, 60)
                if bars:
                    leader_klines[ldr.symbol] = bars

            news_list = None
            try:
                news_list = await provider.get_news_realtime()
            except Exception:
                pass

            report = await evaluate_sector(
                sector_name=sector_name,
                sector_bars=sector_bars,
                constituents=constituents,
                leader_klines=leader_klines,
                news_list=news_list,
            )
            raw_signals[sector_name] = {
                "total_score": report.total_score,
                "rating": report.rating,
                "triggered_signals": report.triggered_signals,
                "signal_details_text": build_signals_text(report),
                "leaders": [{"symbol": c.symbol, "name": c.name} for c in leaders],
                "_report": report,
            }
        except Exception as e:
            logger.warning("signal_collector %s 失败: %s", sector_name, e)

    return {**state, "raw_signals": raw_signals}


async def llm_analyzer(state: SectorSignalState) -> SectorSignalState:
    """LLM 分析节点：对评分最高的板块做 LLM 综合分析"""
    raw_signals = state.get("raw_signals", {})
    if not raw_signals:
        return {**state, "llm_analysis": {}}

    # 取评分最高的板块做 LLM 分析
    top = sorted(raw_signals.items(), key=lambda x: x[1]["total_score"], reverse=True)[:3]
    llm_results: dict = {}

    try:
        from app.llm.client import get_llm
        llm = get_llm()

        for sector_name, sig in top:
            if sig["total_score"] < 30:
                continue
            texts = sig["signal_details_text"]
            prompt = build_sector_analysis_prompt(
                sector=sector_name,
                volume_signals=texts["volume"],
                capital_signals=texts["capital"],
                leader_signals=texts["leader"],
                policy_signals=texts["policy"],
                sentiment_signals=texts["sentiment"],
                rule_score=sig["total_score"],
                rating=sig["rating"],
            )
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(None, lambda p=prompt: llm.invoke(p))
                text = response.content if hasattr(response, "content") else str(response)
                start, end = text.find("{"), text.rfind("}") + 1
                if start != -1 and end > 0:
                    llm_results[sector_name] = json.loads(text[start:end])
            except Exception as e:
                logger.warning("LLM 分析 %s 失败: %s", sector_name, e)
    except Exception as e:
        logger.warning("LLM 初始化失败，跳过 LLM 分析: %s", e)

    return {**state, "llm_analysis": llm_results}


async def explanation_generator(state: SectorSignalState) -> SectorSignalState:
    """报告生成节点：输出最终可读报告"""
    raw_signals = state.get("raw_signals", {})
    llm_analysis = state.get("llm_analysis", {})
    intent = state.get("intent", "general")

    if not raw_signals:
        final = "暂无板块数据，请检查数据源连接或稍后重试。"
        return {**state, "final_report": final}

    sorted_sectors = sorted(
        raw_signals.items(), key=lambda x: x[1]["total_score"], reverse=True
    )

    lines = ["📊 **A股板块启动信号分析报告**", ""]

    for sector_name, sig in sorted_sectors[:5]:
        score = sig["total_score"]
        rating = sig["rating"]
        icon = "🚨" if score >= 70 else "⚡" if score >= 50 else "📊" if score >= 30 else "⬜"
        lines.append(f"### {icon} {sector_name}  {score:.0f}/100  {rating}")

        if sig["triggered_signals"]:
            lines.append("**触发信号:**")
            for ts in sig["triggered_signals"]:
                lines.append(f"- ✓ {ts['name']}: {ts['reason']}")

        leaders = sig.get("leaders", [])
        if leaders:
            names = [f"{l['name']}({l['symbol']})" for l in leaders[:3]]
            lines.append(f"**龙头股:** {', '.join(names)}")

        # LLM 补充分析
        la = llm_analysis.get(sector_name)
        if la:
            is_launching = la.get("is_launching", False)
            stage = la.get("stage", "")
            advice = la.get("operation_advice", "")
            drivers = la.get("key_drivers", [])
            risks = la.get("risks", [])
            lines.append(f"**AI分析:** {'启动中 🟢' if is_launching else '观望 ⚪'} | 阶段: {stage}")
            if drivers:
                lines.append(f"**核心驱动:** {', '.join(drivers[:3])}")
            if risks:
                lines.append(f"**主要风险:** {', '.join(risks[:2])}")
            if advice:
                lines.append(f"**操作建议:** {advice}")

        lines.append("")

    lines.append("⚠️ 以上信号仅供研究参考，不构成投资建议。股市有风险，投资需谨慎。")
    final = "\n".join(lines)
    return {**state, "final_report": final}


async def react_chat(state: SectorSignalState) -> SectorSignalState:
    """通用对话节点：处理非板块扫描请求（个股、大盘等）"""
    query = state.get("user_query", "")
    history = state.get("messages", [])

    try:
        from langchain.agents import create_react_agent, AgentExecutor
        from langchain_core.prompts import PromptTemplate

        # ReAct prompt 必须包含 {tools}, {tool_names}, {agent_scratchpad}, {input}
        prompt_template = PromptTemplate.from_template(
            SYSTEM_PROMPT + "\n\n可用工具:\n{tools}\n工具名称: {tool_names}\n\n"
            "{agent_scratchpad}\n\nHuman: {input}\nAssistant:"
        )
        from app.llm.client import get_llm
        llm = get_llm()
        agent = create_react_agent(llm, ALL_TOOLS, prompt_template)
        executor = AgentExecutor(agent=agent, tools=ALL_TOOLS, verbose=False, handle_parsing_errors=True)

        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: executor.invoke({"input": query}))
        reply = result.get("output", "抱歉，暂时无法回答。")
    except Exception as e:
        logger.error("react_chat 失败: %s", e)
        reply = f"执行出错: {str(e)[:200]}"

    return {**state, "final_report": reply}


def _route_after_intent(state: SectorSignalState) -> str:
    intent = state.get("intent", "general")
    if intent in ("sector_scan", "sector_detail"):
        return "signal_collector"
    return "react_chat"


# ---- 构建图 ----

def build_graph():
    graph = StateGraph(SectorSignalState)

    graph.add_node("intent_router", intent_router)
    graph.add_node("signal_collector", signal_collector)
    graph.add_node("llm_analyzer", llm_analyzer)
    graph.add_node("explanation_generator", explanation_generator)
    graph.add_node("react_chat", react_chat)

    graph.set_entry_point("intent_router")
    graph.add_conditional_edges("intent_router", _route_after_intent)
    graph.add_edge("signal_collector", "llm_analyzer")
    graph.add_edge("llm_analyzer", "explanation_generator")
    graph.add_edge("explanation_generator", END)
    graph.add_edge("react_chat", END)

    return graph.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


def reset_agent() -> None:
    global _graph
    _graph = None


async def chat(user_message: str, history: Optional[list[dict]] = None) -> str:
    """主对话入口"""
    graph = get_graph()
    initial_state: SectorSignalState = {
        "user_query": user_message,
        "messages": history or [],
        "intent": "",
        "sectors_to_analyze": [],
        "raw_signals": {},
        "llm_analysis": {},
        "final_report": "",
        "error": None,
    }
    try:
        result = await graph.ainvoke(initial_state)
        return result.get("final_report") or "抱歉，暂时无法回答。"
    except Exception as e:
        logger.error("graph 执行失败: %s", e)
        return f"执行出错: {str(e)[:200]}"
