"""Agent 工具: 回测分析"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def run_quick_backtest(sector_name: str = "", days: int = 30) -> str:
    """对板块信号运行快速回测，评估历史信号有效性

    统计近 N 日内信号触发后 10 日的胜率和平均涨幅，
    帮助判断当前信号体系的有效性。

    Args:
        sector_name: 板块名称，如 "半导体"；空则取前5个主要板块
        days: 回测天数（默认30，建议不超过90）
    """
    import datetime as dt
    from app.backtest.engine import BacktestEngine
    from app.data.models import SECTOR_LIST

    try:
        end = dt.date.today()
        start = end - dt.timedelta(days=min(days, 90))
        sectors = [sector_name] if sector_name else SECTOR_LIST[:5]

        engine = BacktestEngine(start, end)
        metrics = await engine.run_and_report(sectors)

        if metrics.total_signals == 0:
            return f"回测期间（{start}~{end}）无信号触发，无法评估"

        return metrics.report_text()

    except Exception as e:
        logger.error("回测失败: %s", e)
        return f"回测执行失败: {e}"
