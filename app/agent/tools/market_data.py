"""Agent 工具: 行情数据查询"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from app.data import get_data_provider

logger = logging.getLogger(__name__)


@tool
async def search_stock(keyword: str) -> str:
    """搜索股票 (按代码/名称/拼音首字母)

    Args:
        keyword: 搜索关键词, 如 "贵州茅台"、"600519"、"gzmt"
    """
    try:
        provider = get_data_provider()
        results = await provider.search_stock(keyword)

        if not results:
            return f"未找到与 '{keyword}' 相关的股票"

        items = []
        for r in results[:10]:
            items.append(f"- {r.symbol} {r.name} ({r.market})")
        return f"搜索结果:\n" + "\n".join(items)

    except Exception as e:
        logger.error("搜索股票失败: %s", e)
        return f"搜索失败: {e}"


@tool
async def get_realtime_quote(symbol: str) -> str:
    """获取个股实时行情

    Args:
        symbol: 股票代码, 如 "600519"
    """
    try:
        provider = get_data_provider()
        quote = await provider.get_realtime_quote(symbol)

        change_icon = "🔴" if quote.change_pct < 0 else "🟢" if quote.change_pct > 0 else "⚪"

        return json.dumps({
            "股票": f"{quote.name}({quote.symbol})",
            "最新价": f"{quote.price:.2f}",
            "涨跌": f"{change_icon} {quote.change:+.2f} ({quote.change_pct:+.2f}%)",
            "今开": f"{quote.open_price:.2f}",
            "最高": f"{quote.high_price:.2f}",
            "最低": f"{quote.low_price:.2f}",
            "昨收": f"{quote.pre_close:.2f}",
            "成交量": f"{quote.volume:.0f}手",
            "成交额": f"{quote.amount / 1e8:.2f}亿",
            "换手率": f"{quote.turnover_rate:.2f}%",
            "市盈率": f"{quote.pe_ratio:.1f}" if quote.pe_ratio > 0 else "N/A",
            "市净率": f"{quote.pb_ratio:.2f}" if quote.pb_ratio > 0 else "N/A",
            "总市值": f"{quote.total_mv / 1e8:.0f}亿" if quote.total_mv > 0 else "N/A",
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("获取行情失败: %s", e)
        return f"获取行情失败: {e}"


@tool
async def get_kline_data(symbol: str, period: str = "daily", count: int = 60) -> str:
    """获取历史K线数据

    Args:
        symbol: 股票代码, 如 "600519"
        period: K线周期: "daily"(日K) / "weekly"(周K) / "monthly"(月K)
        count: 获取条数, 默认 60
    """
    from app.data.models import Period

    period_map = {"daily": Period.DAILY, "weekly": Period.WEEKLY, "monthly": Period.MONTHLY}
    p = period_map.get(period, Period.DAILY)

    try:
        provider = get_data_provider()
        bars = await provider.get_kline(symbol, p, count)

        if not bars:
            return f"未获取到 {symbol} 的K线数据"

        # 返回摘要信息
        latest = bars[-1]
        oldest = bars[0]
        price_change = (latest.close_price - oldest.close_price) / oldest.close_price * 100

        high = max(b.high_price for b in bars)
        low = min(b.low_price for b in bars)

        return json.dumps({
            "股票代码": symbol,
            "周期": period,
            "数据条数": len(bars),
            "时间范围": f"{oldest.trade_date} ~ {latest.trade_date}",
            "最新收盘": f"{latest.close_price:.2f}",
            "区间涨跌": f"{price_change:+.2f}%",
            "区间最高": f"{high:.2f}",
            "区间最低": f"{low:.2f}",
            "最近5日收盘": [f"{b.close_price:.2f}" for b in bars[-5:]],
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("获取K线失败: %s", e)
        return f"获取K线数据失败: {e}"


@tool
async def get_fund_flow(symbol: str) -> str:
    """获取个股资金流向

    Args:
        symbol: 股票代码, 如 "600519"
    """
    try:
        provider = get_data_provider()
        flow = await provider.get_fund_flow(symbol)

        return json.dumps({
            "股票": f"{flow.name}({flow.symbol})" if flow.name else flow.symbol,
            "日期": str(flow.trade_date),
            "主力净流入": f"{flow.main_net_inflow / 1e4:.2f}万 ({flow.main_net_pct:+.2f}%)",
            "超大单净流入": f"{flow.super_large_inflow / 1e4:.2f}万",
            "大单净流入": f"{flow.large_inflow / 1e4:.2f}万",
            "中单净流入": f"{flow.medium_inflow / 1e4:.2f}万",
            "小单净流入": f"{flow.small_inflow / 1e4:.2f}万",
        }, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("获取资金流向失败: %s", e)
        return f"获取资金流向失败: {e}"


@tool
async def get_market_overview() -> str:
    """获取大盘概览 (上证指数、深证成指、创业板指等主要指数)"""
    try:
        provider = get_data_provider()
        indices = await provider.get_major_indices()

        if not indices:
            return "无法获取指数数据"

        lines = ["📊 A股大盘概览:"]
        for idx in indices:
            icon = "🔴" if idx.change_pct < 0 else "🟢" if idx.change_pct > 0 else "⚪"
            lines.append(
                f"  {icon} {idx.name}: {idx.price:.2f} "
                f"({idx.change:+.2f}, {idx.change_pct:+.2f}%)"
            )
        return "\n".join(lines)

    except Exception as e:
        logger.error("获取大盘概览失败: %s", e)
        return f"获取大盘数据失败: {e}"
