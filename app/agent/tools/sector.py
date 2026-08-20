"""Agent 工具: 板块信号扫描"""

from __future__ import annotations

import json
import logging

from langchain_core.tools import tool

from app.data import get_data_provider
from app.data.models import Period

logger = logging.getLogger(__name__)


async def _evaluate_one(sector_name: str, enable_llm_policy: bool = False):
    """内部复用：拉取数据并评估单个板块"""
    provider = get_data_provider()

    sector_bars = await provider.get_sector_kline(sector_name, count=60)
    if len(sector_bars) < 20:
        return None

    constituents = await provider.get_sector_constituents(sector_name)

    from app.signals.leader import identify_leaders
    leaders = identify_leaders(constituents, top_n=3)
    leader_klines: dict = {}
    for leader in leaders:
        bars = await provider.get_kline(leader.symbol, Period.DAILY, 60)
        if bars:
            leader_klines[leader.symbol] = bars

    # 尽力获取北向/新闻/热度数据
    north_flow = None
    news_list = None
    hot_rank = None
    fund_flow_history = None

    try:
        nf = await provider.get_north_flow_by_sector(sector_name, lookback=20)
        if nf:
            north_flow = nf
    except Exception:
        pass

    try:
        news_list = await provider.get_news_realtime()
    except Exception:
        pass

    try:
        hot = await provider.get_hot_rank_history(sector_name, lookback=5)
        if hot:
            hot_rank = hot
    except Exception:
        pass

    try:
        flows = await provider.get_sector_fund_flow()
        # 过滤本板块
        matching = [f for f in flows if sector_name in f.name or f.name in sector_name]
        if matching:
            fund_flow_history = matching
    except Exception:
        pass

    from app.signals import evaluate_sector
    return await evaluate_sector(
        sector_name=sector_name,
        sector_bars=sector_bars,
        constituents=constituents,
        leader_klines=leader_klines,
        fund_flow_history=fund_flow_history,
        north_flow_history=north_flow,
        news_list=news_list,
        hot_rank_history=hot_rank,
        enable_llm_policy=enable_llm_policy,
    ), leaders


@tool
async def scan_sector_signal(sector_name: str) -> str:
    """扫描单个板块的启动信号，输出多维度评分

    综合量价、龙头、资金流、政策/事件、情绪五大类信号，
    计算板块启动概率评分（0-100）并给出等级判断。

    Args:
        sector_name: 板块名称，如 "半导体"、"新能源车"、"AI"、"机器人"
    """
    try:
        result = await _evaluate_one(sector_name, enable_llm_policy=True)
        if result is None:
            return f"板块 {sector_name} 数据不足（K线<20条），无法分析"

        report, leaders = result

        output = {
            "板块": sector_name,
            "评分": f"{report.total_score:.0f}/100",
            "等级": report.rating,
            "触发信号数": len(report.triggered_signals),
            "触发信号": [
                {
                    "信号": ts["name"],
                    "得分": ts["actual_score"],
                    "描述": ts["reason"],
                }
                for ts in report.triggered_signals
            ],
            "龙头股": [c.name + f"({c.symbol})" for c in leaders],
            "今日涨跌幅": "N/A",
        }

        # 取今日涨跌幅和涨停家数
        try:
            provider = get_data_provider()
            sb = await provider.get_sector_kline(sector_name, count=2)
            if sb:
                output["今日涨跌幅"] = f"{sb[-1].change_pct:.2f}%"
                output["今日涨停家数"] = sb[-1].limit_up_count
        except Exception:
            pass

        return json.dumps(output, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error("scan_sector_signal %s 失败: %s", sector_name, e)
        return f"板块信号扫描失败: {e}"


@tool
async def scan_all_sectors() -> str:
    """扫描所有主要板块，找出当前评分最高的启动信号

    返回评分 Top 5 的板块及其信号摘要，帮助快速发现热点板块。
    """
    from app.data.models import SECTOR_LIST

    try:
        results = []
        for sector_name in SECTOR_LIST:
            try:
                ret = await _evaluate_one(sector_name)
                if ret is None:
                    continue
                report, leaders = ret
                results.append((report, leaders))
            except Exception as e:
                logger.warning("扫描 %s 失败: %s", sector_name, e)
                continue

        if not results:
            return "暂无板块数据，请稍后重试"

        results.sort(key=lambda x: x[0].total_score, reverse=True)
        top5 = results[:5]

        lines = ["🔍 板块启动信号扫描结果（Top 5）:", ""]
        for i, (r, leaders) in enumerate(top5, 1):
            icon = "🚨" if r.total_score >= 70 else "⚡" if r.total_score >= 50 else "📊"
            lines.append(
                f"{i}. {icon} {r.sector_name}  "
                f"评分: {r.total_score:.0f}/100  {r.rating}"
            )
            if r.triggered_signals:
                sigs = [ts["name"] for ts in r.triggered_signals]
                lines.append(f"   触发: {', '.join(sigs)}")
            if leaders:
                leader_names = [c.name for c in leaders[:2]]
                lines.append(f"   龙头: {', '.join(leader_names)}")
            lines.append("")

        return "\n".join(lines)

    except Exception as e:
        logger.error("scan_all_sectors 失败: %s", e)
        return f"全市场扫描失败: {e}"


@tool
async def get_sector_list() -> str:
    """获取可分析的板块列表"""
    from app.data.models import SECTOR_LIST
    try:
        provider = get_data_provider()
        sector_infos = await provider.get_sector_list()
        if sector_infos:
            names = [s.name for s in sector_infos[:30]]
        else:
            names = SECTOR_LIST
        return "支持分析的板块:\n" + "\n".join(f"- {n}" for n in names)
    except Exception:
        return "支持分析的板块:\n" + "\n".join(f"- {n}" for n in SECTOR_LIST)
