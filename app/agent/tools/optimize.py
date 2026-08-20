"""Agent 工具: Walk-forward 验证 + 参数优化"""

from __future__ import annotations

import logging

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
async def run_walk_forward(days: int = 270, sector_name: str = "") -> str:
    """运行 Walk-Forward 滚动窗口验证，评估信号参数的稳定性

    将历史数据切分为多个训练/测试窗口，检验信号在样本外（未来）的表现，
    避免过拟合。输出各窗口胜率和稳定性指标。

    Args:
        days: 总验证天数（默认270，约9个月）
        sector_name: 指定板块，空则取前5个主要板块
    """
    import datetime as dt
    from app.backtest.walk_forward import walk_forward_validation
    from app.data.models import SECTOR_LIST

    try:
        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        sectors = [sector_name] if sector_name else SECTOR_LIST[:5]

        report = await walk_forward_validation(start, end, sectors)
        return report.report_text()
    except Exception as e:
        logger.error("walk_forward 失败: %s", e)
        return f"Walk-Forward 执行失败: {e}"


@tool
async def optimize_signal_weights(sector_name: str = "", days: int = 60) -> str:
    """自动优化信号权重参数，找到使历史胜率最高的权重组合

    对量价/龙头/资金/政策/情绪五大类信号做网格搜索，
    最优权重自动保存并在下次运行时加载。

    Args:
        sector_name: 指定板块，空则取前3个主要板块
        days: 优化用历史天数（默认60）
    """
    import datetime as dt
    from app.backtest.optimizer import optimize_weights
    from app.data.models import SECTOR_LIST

    try:
        end = dt.date.today()
        start = end - dt.timedelta(days=days)
        sectors = [sector_name] if sector_name else SECTOR_LIST[:3]

        result = await optimize_weights(start, end, sectors)

        lines = [
            f"参数优化完成（测试 {result['combos_tested']} 种组合）",
            f"最优胜率: {result['best_win_rate']*100:.1f}%",
            f"最优平均涨幅: {result['best_avg_return']*100:.1f}%",
            "",
            "Top 3 权重配置:",
        ]
        for i, r in enumerate(result["top5"][:3], 1):
            lines.append(
                f"  {i}. 胜率={r['win_rate']*100:.1f}%  "
                f"涨幅={r['avg_return']*100:.1f}%  "
                f"scales={r['scales']}"
            )
        if result.get("saved"):
            lines.append("\n✓ 最优权重已保存，下次自动加载。")

        return "\n".join(lines)
    except Exception as e:
        logger.error("optimize_weights 失败: %s", e)
        return f"参数优化失败: {e}"
