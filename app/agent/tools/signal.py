"""Agent 工具: 信号生成"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

from app.data import get_data_provider
from app.data.models import Period

logger = logging.getLogger(__name__)


@tool
async def generate_stock_signal(symbol: str) -> str:
    """生成个股买卖信号 (多策略综合判断)

    运行 MACD交叉、KDJ超买超卖、均线趋势、布林带、放量突破 5 个策略，
    汇总生成综合买卖信号。

    Args:
        symbol: 股票代码, 如 "600519"
    """
    try:
        provider = get_data_provider()

        # 获取行情 (拿名称)
        quote = await provider.get_realtime_quote(symbol)
        name = quote.name

        # 获取K线
        bars = await provider.get_kline(symbol, Period.DAILY, 120)
        if len(bars) < 20:
            return f"K线数据不足 (仅{len(bars)}条), 无法生成信号"

        # 生成信号
        from app.strategy.engine import generate_signal

        report = generate_signal(symbol, name, bars)

        return report.summary_text()

    except Exception as e:
        logger.error("生成信号失败: %s", e)
        return f"生成信号失败: {e}"
