"""信号层入口

用法:
    from app.signals import evaluate_sector
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

from app.data.models import (
    HotRankRecord,
    KlineBar,
    LhbRecord,
    NewsItem,
    NorthFlowRecord,
    ResearchReportRecord,
    SectorConstituentStock,
    SectorFundFlow,
    SectorKlineBar,
)
from app.signals.base import SignalResult
from app.signals.capital import (
    etf_subscription_surge,
    institutional_concentration,
    main_capital_consecutive_inflow,
    north_capital_anomaly,
)
from app.signals.leader import identify_leaders, leader_launching, sector_diffusion
from app.signals.policy import llm_policy_evaluation, policy_keyword_hit
from app.signals.scorer import SectorSignalReport, calculate_score
from app.signals.sentiment import discussion_anomaly, hot_rank_surge, research_report_surge
from app.signals.volume_price import (
    breakout_resistance,
    limit_up_surge,
    volume_breakout,
    volume_price_uptrend,
)

logger = logging.getLogger(__name__)


async def evaluate_sector(
    sector_name: str,
    sector_bars: list[SectorKlineBar],
    constituents: list[SectorConstituentStock],
    leader_klines: dict[str, list[KlineBar]],
    leader_launch_date: Optional[datetime.date] = None,
    evaluate_date: Optional[datetime.date] = None,
    # Phase 2 可选数据
    fund_flow_history: Optional[list[SectorFundFlow]] = None,
    north_flow_history: Optional[list[NorthFlowRecord]] = None,
    lhb_records: Optional[list[LhbRecord]] = None,
    news_list: Optional[list[NewsItem]] = None,
    hot_rank_history: Optional[list[HotRankRecord]] = None,
    research_history: Optional[list[ResearchReportRecord]] = None,
    discussion_counts: Optional[list[int]] = None,
    enable_llm_policy: bool = False,
) -> SectorSignalReport:
    """对单个板块运行全部 5 类信号，返回评分报告

    Phase 2 数据参数均为可选，未传则跳过对应信号。
    """
    results: dict[str, SignalResult] = {}

    # ---- 量价信号 ----
    for name, fn, args in [
        ("volume_breakout",       volume_breakout,       (sector_bars,)),
        ("limit_up_surge",        limit_up_surge,        (sector_bars,)),
        ("volume_price_uptrend",  volume_price_uptrend,  (sector_bars,)),
        ("breakout_resistance",   breakout_resistance,   (sector_bars,)),
    ]:
        try:
            results[name] = fn(*args)
        except Exception as e:
            logger.warning("%s 异常: %s", name, e)
            results[name] = SignalResult(reason=f"计算异常: {e}")

    # ---- 龙头信号 ----
    try:
        results["leader_launching"] = leader_launching(leader_klines)
    except Exception as e:
        logger.warning("leader_launching 异常: %s", e)
        results["leader_launching"] = SignalResult(reason=f"计算异常: {e}")

    try:
        results["sector_diffusion"] = sector_diffusion(sector_bars, leader_launch_date)
    except Exception as e:
        logger.warning("sector_diffusion 异常: %s", e)
        results["sector_diffusion"] = SignalResult(reason=f"计算异常: {e}")

    # ---- 资金流信号 ----
    if fund_flow_history:
        try:
            results["main_capital_inflow"] = main_capital_consecutive_inflow(fund_flow_history)
        except Exception as e:
            logger.warning("main_capital_inflow 异常: %s", e)
            results["main_capital_inflow"] = SignalResult(reason=f"计算异常: {e}")

    if north_flow_history:
        try:
            results["north_capital_anomaly"] = north_capital_anomaly(north_flow_history)
        except Exception as e:
            logger.warning("north_capital_anomaly 异常: %s", e)
            results["north_capital_anomaly"] = SignalResult(reason=f"计算异常: {e}")

    if lhb_records is not None:
        try:
            sector_symbols = {c.symbol for c in constituents}
            results["institutional_concentration"] = institutional_concentration(
                lhb_records, sector_symbols
            )
        except Exception as e:
            logger.warning("institutional_concentration 异常: %s", e)
            results["institutional_concentration"] = SignalResult(reason=f"计算异常: {e}")

    # ---- 政策/事件信号 ----
    if news_list is not None:
        try:
            results["policy_keyword_hit"] = policy_keyword_hit(news_list, sector_name)
        except Exception as e:
            logger.warning("policy_keyword_hit 异常: %s", e)
            results["policy_keyword_hit"] = SignalResult(reason=f"计算异常: {e}")

        if enable_llm_policy:
            try:
                results["llm_policy_score"] = await llm_policy_evaluation(sector_name, news_list)
            except Exception as e:
                logger.warning("llm_policy_evaluation 异常: %s", e)
                results["llm_policy_score"] = SignalResult(reason=f"LLM评估异常: {e}")

    # ---- 情绪信号 ----
    if hot_rank_history:
        try:
            results["hot_rank_surge"] = hot_rank_surge(hot_rank_history)
        except Exception as e:
            logger.warning("hot_rank_surge 异常: %s", e)
            results["hot_rank_surge"] = SignalResult(reason=f"计算异常: {e}")

    if research_history:
        try:
            results["research_surge"] = research_report_surge(research_history)
        except Exception as e:
            logger.warning("research_surge 异常: %s", e)
            results["research_surge"] = SignalResult(reason=f"计算异常: {e}")

    if discussion_counts:
        try:
            results["discussion_anomaly"] = discussion_anomaly(discussion_counts)
        except Exception as e:
            logger.warning("discussion_anomaly 异常: %s", e)
            results["discussion_anomaly"] = SignalResult(reason=f"计算异常: {e}")

    return calculate_score(sector_name, results, evaluate_date, sector_bars)
