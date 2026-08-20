"""Agent + LLM 层单元测试"""

import json

import pytest

from app.agent.prompts import SYSTEM_PROMPT, build_sector_analysis_prompt
from app.agent.state import AgentState, SectorSignalState
from app.llm.structured import extract_json_from_text, parse_structured_output
from app.strategy.models import Signal, Direction
from pydantic import BaseModel, Field


# ---- 提示词测试 ----

class TestPrompts:
    def test_system_prompt_content(self):
        assert "A股" in SYSTEM_PROMPT
        assert "风险提示" in SYSTEM_PROMPT
        assert "T+1" in SYSTEM_PROMPT
        assert "涨跌停" in SYSTEM_PROMPT

    def test_build_sector_analysis_prompt(self):
        prompt = build_sector_analysis_prompt(
            sector="半导体",
            volume_signals="放量上涨: 触发",
            capital_signals="主力净流入: 触发",
            leader_signals="龙头启动: 触发",
            policy_signals="政策利好: 触发",
            sentiment_signals="热度上升: 触发",
            rule_score=75.0,
            rating="强信号 ⭐⭐⭐⭐⭐",
        )
        assert "半导体" in prompt
        assert "75.0" in prompt
        assert "is_launching" in prompt


# ---- Agent 状态测试 ----

class TestAgentState:
    def test_sector_signal_state_keys(self):
        state: SectorSignalState = {
            "user_query": "扫描板块信号",
            "intent": "sector_scan",
            "sectors_to_analyze": ["半导体"],
            "raw_signals": {},
            "llm_analysis": {},
            "final_report": "",
            "messages": [],
            "error": None,
        }
        assert state["user_query"] == "扫描板块信号"
        assert state["intent"] == "sector_scan"

    def test_agent_state_class(self):
        state = AgentState()
        state.set_current_stock("600519", "贵州茅台")
        assert state.current_symbol == "600519"
        assert state.current_name == "贵州茅台"


# ---- 结构化输出测试 ----

class SimpleModel(BaseModel):
    name: str = Field(...)
    score: float = Field(default=0.0)


class TestParseStructuredOutput:
    def test_direct_json(self):
        result = parse_structured_output('{"name": "test", "score": 85.0}', SimpleModel)
        assert result is not None
        assert result.name == "test"
        assert result.score == 85.0

    def test_markdown_code_block(self):
        text = '一些说明文字\n```json\n{"name": "test", "score": 90.0}\n```\n更多内容'
        result = parse_structured_output(text, SimpleModel)
        assert result is not None
        assert result.name == "test"
        assert result.score == 90.0

    def test_embedded_json(self):
        text = '根据分析结果：{"name": "贵州茅台", "score": 75.5} 建议持有'
        result = parse_structured_output(text, SimpleModel)
        assert result is not None
        assert result.name == "贵州茅台"

    def test_invalid_text(self):
        result = parse_structured_output("没有JSON内容", SimpleModel)
        assert result is None

    def test_empty_text(self):
        result = parse_structured_output("", SimpleModel)
        assert result is None


class TestExtractJson:
    def test_direct(self):
        result = extract_json_from_text('{"key": "value"}')
        assert result == {"key": "value"}

    def test_embedded(self):
        result = extract_json_from_text('前缀 {"a": 1, "b": 2} 后缀')
        assert result == {"a": 1, "b": 2}

    def test_no_json(self):
        result = extract_json_from_text("纯文本没有JSON")
        assert result is None

    def test_empty(self):
        result = extract_json_from_text("")
        assert result is None


# ---- 工具注册测试 ----

class TestToolImports:
    def test_market_data_tools(self):
        from app.agent.tools.market_data import (
            search_stock,
            get_realtime_quote,
            get_kline_data,
            get_fund_flow,
            get_market_overview,
        )
        assert search_stock.name == "search_stock"
        assert get_realtime_quote.name == "get_realtime_quote"
        assert get_kline_data.name == "get_kline_data"
        assert get_fund_flow.name == "get_fund_flow"
        assert get_market_overview.name == "get_market_overview"

    def test_technical_tools(self):
        from app.agent.tools.technical import (
            calculate_technical_indicators,
            analyze_fundamental,
            detect_candlestick_patterns,
        )
        assert calculate_technical_indicators.name == "calculate_technical_indicators"
        assert analyze_fundamental.name == "analyze_fundamental"
        assert detect_candlestick_patterns.name == "detect_candlestick_patterns"

    def test_signal_tools(self):
        from app.agent.tools.signal import generate_stock_signal
        assert generate_stock_signal.name == "generate_stock_signal"

    def test_screening_tools(self):
        from app.agent.tools.screening import screen_stocks
        assert screen_stocks.name == "screen_stocks"

    def test_sector_tools(self):
        from app.agent.tools.sector import scan_sector_signal, scan_all_sectors, get_sector_list
        assert scan_sector_signal.name == "scan_sector_signal"
        assert scan_all_sectors.name == "scan_all_sectors"
        assert get_sector_list.name == "get_sector_list"

    def test_all_tools_count(self):
        from app.agent.graph import ALL_TOOLS
        assert len(ALL_TOOLS) == 16
