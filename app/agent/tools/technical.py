"""Agent 工具: 技术分析和基本面分析"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from app.data import get_data_provider
from app.data.models import Period

logger = logging.getLogger(__name__)


@tool
async def calculate_technical_indicators(symbol: str) -> str:
    """计算个股技术指标 (MA/MACD/KDJ/RSI/BOLL等)

    Args:
        symbol: 股票代码, 如 "600519"
    """
    try:
        provider = get_data_provider()
        bars = await provider.get_kline(symbol, Period.DAILY, 120)

        if len(bars) < 20:
            return f"K线数据不足 (仅{len(bars)}条), 无法计算技术指标"

        from app.analysis.technical import compute_all_indicators, interpret_indicators

        indicators = compute_all_indicators(bars)
        latest = indicators.latest

        # 格式化关键指标
        result = {
            "股票代码": symbol,
            "最新收盘": f"{latest.get('close', 0):.2f}",
            "均线": {
                "MA5": f"{latest.get('ma5', 0):.2f}",
                "MA10": f"{latest.get('ma10', 0):.2f}",
                "MA20": f"{latest.get('ma20', 0):.2f}",
            },
            "MACD": {
                "DIF": f"{latest.get('dif', 0):.4f}",
                "DEA": f"{latest.get('dea', 0):.4f}",
                "MACD柱": f"{latest.get('macd_hist', 0):.4f}",
            },
            "KDJ": {
                "K": f"{latest.get('k', 0):.2f}",
                "D": f"{latest.get('d', 0):.2f}",
                "J": f"{latest.get('j', 0):.2f}",
            },
            "RSI": {
                "RSI6": f"{latest.get('rsi6', 0):.2f}",
                "RSI12": f"{latest.get('rsi12', 0):.2f}",
            },
            "布林带": {
                "上轨": f"{latest.get('boll_upper', 0):.2f}",
                "中轨": f"{latest.get('boll_mid', 0):.2f}",
                "下轨": f"{latest.get('boll_lower', 0):.2f}",
            },
            "量比": f"{latest.get('volume_ratio', 0):.2f}",
            "ATR": f"{latest.get('atr', 0):.4f}",
        }

        # 添加解读
        signals = interpret_indicators(latest)
        if signals:
            result["技术面解读"] = signals

        return json.dumps(result, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("计算技术指标失败: %s", e)
        return f"计算技术指标失败: {e}"


@tool
async def analyze_fundamental(symbol: str) -> str:
    """分析个股基本面 (估值/盈利/成长/安全性评分)

    Args:
        symbol: 股票代码, 如 "600519"
    """
    try:
        provider = get_data_provider()
        financial = await provider.get_financial(symbol)

        from app.analysis.fundamental import analyze_fundamentals

        score = analyze_fundamentals(financial)

        return json.dumps({
            "股票": f"{score.name}({score.symbol})" if score.name else score.symbol,
            "综合评分": f"{score.total_score:.1f}分",
            "综合结论": score.summary,
            "估值评估": {
                "评分": f"{score.valuation_score:.1f}",
                "分析": score.valuation_comment,
            },
            "盈利能力": {
                "评分": f"{score.profitability_score:.1f}",
                "分析": score.profitability_comment,
            },
            "成长性": {
                "评分": f"{score.growth_score:.1f}",
                "分析": score.growth_comment,
            },
            "安全性": {
                "评分": f"{score.safety_score:.1f}",
                "分析": score.safety_comment,
            },
            "关键指标": {
                "PE(TTM)": financial.pe_ttm,
                "PB": financial.pb,
                "ROE(%)": financial.roe,
                "毛利率(%)": financial.gross_margin,
                "净利率(%)": financial.net_margin,
                "营收增长(%)": financial.revenue_yoy,
                "净利增长(%)": financial.net_profit_yoy,
                "负债率(%)": financial.debt_ratio,
            },
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("基本面分析失败: %s", e)
        return f"基本面分析失败: {e}"


@tool
async def detect_candlestick_patterns(symbol: str) -> str:
    """识别K线形态 (十字星、锤子线、吞没形态等)

    Args:
        symbol: 股票代码, 如 "600519"
    """
    try:
        provider = get_data_provider()
        bars = await provider.get_kline(symbol, Period.DAILY, 30)

        if len(bars) < 3:
            return "K线数据不足，无法识别形态"

        from app.analysis.pattern import detect_patterns, judge_trend

        patterns = detect_patterns(bars)
        trend = judge_trend(bars)

        trend_text = {
            "uptrend": "📈 上升趋势",
            "downtrend": "📉 下降趋势",
            "sideways": "↔️ 横盘震荡",
        }.get(trend, "未知")

        if not patterns:
            return f"{symbol} 近期无明显K线形态信号\n当前趋势: {trend_text}"

        lines = [f"📊 {symbol} K线形态识别:", f"当前趋势: {trend_text}", ""]
        for p in patterns:
            icon = "🟢" if p.direction == "bullish" else "🔴" if p.direction == "bearish" else "⚪"
            lines.append(f"  {icon} **{p.name}** (强度:{p.strength}) - {p.description}")

        return "\n".join(lines)

    except Exception as e:
        logger.error("K线形态识别失败: %s", e)
        return f"K线形态识别失败: {e}"
