"""API 路由定义"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.graph import chat as agent_chat
from app.data import get_data_provider
from app.data.models import Period

logger = logging.getLogger(__name__)

router = APIRouter()


# ---- 请求/响应模型 ----

class ChatRequest(BaseModel):
    message: str = Field(..., description="用户消息", examples=["帮我分析一下贵州茅台"])
    history: list[dict] = Field(default_factory=list, description="历史消息")


class ChatResponse(BaseModel):
    reply: str
    status: str = "ok"


# ---- API 端点 ----

@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """AI 对话接口 — 与量化 Agent 交互"""
    reply = await agent_chat(req.message, req.history or None)
    return ChatResponse(reply=reply)


@router.get("/market/overview")
async def market_overview():
    """大盘概览 — 上证/深证/创业板等主要指数"""
    provider = get_data_provider()
    indices = await provider.get_major_indices()
    return [idx.model_dump() for idx in indices]


@router.get("/market/{symbol}/quote")
async def get_quote(symbol: str):
    """获取个股实时行情"""
    provider = get_data_provider()
    quote = await provider.get_realtime_quote(symbol)
    return quote.model_dump()


@router.get("/market/{symbol}/kline")
async def get_kline(
    symbol: str,
    period: str = Query("daily", description="daily/weekly/monthly"),
    count: int = Query(60, ge=1, le=500),
):
    """获取历史K线数据"""
    period_map = {"daily": Period.DAILY, "weekly": Period.WEEKLY, "monthly": Period.MONTHLY}
    provider = get_data_provider()
    bars = await provider.get_kline(symbol, period_map.get(period, Period.DAILY), count)
    return [bar.model_dump() for bar in bars]


@router.get("/market/{symbol}/signal")
async def get_signal(symbol: str):
    """生成个股买卖信号"""
    from app.strategy.engine import generate_signal

    provider = get_data_provider()
    quote = await provider.get_realtime_quote(symbol)
    bars = await provider.get_kline(symbol, Period.DAILY, 120)

    report = generate_signal(symbol, quote.name, bars)
    return {
        "direction": report.direction.value,
        "confidence": report.confidence,
        "risk_level": report.risk_level,
        "signals": [s.model_dump() for s in report.signals],
        "market_summary": report.market_summary,
        "disclaimer": report.disclaimer,
        "summary": report.summary_text(),
    }


@router.get("/search")
async def search(keyword: str = Query(..., description="搜索关键词")):
    """搜索股票"""
    provider = get_data_provider()
    results = await provider.search_stock(keyword)
    return [r.model_dump() for r in results]


# ---- 板块信号端点 ----

@router.get("/sector/list")
async def sector_list():
    """获取板块列表"""
    provider = get_data_provider()
    sectors = await provider.get_sector_list()
    if sectors:
        return [s.model_dump() for s in sectors]
    from app.data.models import SECTOR_LIST
    return [{"name": n} for n in SECTOR_LIST]


@router.get("/sector/{sector_name}/signal")
async def sector_signal(sector_name: str):
    """获取单个板块的启动信号评分"""
    provider = get_data_provider()
    sector_bars = await provider.get_sector_kline(sector_name, count=60)
    if len(sector_bars) < 20:
        return {"error": f"板块 {sector_name} 数据不足，无法分析"}

    constituents = await provider.get_sector_constituents(sector_name)

    from app.signals.leader import identify_leaders
    leaders = identify_leaders(constituents, top_n=3)
    leader_klines: dict = {}
    for leader in leaders:
        bars = await provider.get_kline(leader.symbol, Period.DAILY, 60)
        if bars:
            leader_klines[leader.symbol] = bars

    from app.signals import evaluate_sector
    report = await evaluate_sector(
        sector_name=sector_name,
        sector_bars=sector_bars,
        constituents=constituents,
        leader_klines=leader_klines,
    )

    return {
        "sector_name": report.sector_name,
        "evaluate_date": str(report.evaluate_date),
        "total_score": report.total_score,
        "rating": report.rating,
        "triggered_signals": report.triggered_signals,
        "leaders": [{"symbol": c.symbol, "name": c.name} for c in leaders],
        "summary": report.summary_text(),
    }


@router.get("/sector/scan")
async def sector_scan():
    """扫描所有板块，返回评分 Top 10"""
    from app.data.models import SECTOR_LIST
    from app.signals import evaluate_sector
    from app.signals.leader import identify_leaders

    provider = get_data_provider()
    results = []

    for sector_name in SECTOR_LIST:
        try:
            sector_bars = await provider.get_sector_kline(sector_name, count=60)
            if len(sector_bars) < 20:
                continue
            constituents = await provider.get_sector_constituents(sector_name)
            leaders = identify_leaders(constituents, top_n=3)
            leader_klines: dict = {}
            for leader in leaders:
                bars = await provider.get_kline(leader.symbol, Period.DAILY, 60)
                if bars:
                    leader_klines[leader.symbol] = bars

            report = await evaluate_sector(
                sector_name=sector_name,
                sector_bars=sector_bars,
                constituents=constituents,
                leader_klines=leader_klines,
            )
            results.append({
                "sector_name": report.sector_name,
                "total_score": report.total_score,
                "rating": report.rating,
                "triggered_count": len(report.triggered_signals),
                "triggered_signals": [ts["name"] for ts in report.triggered_signals],
            })
        except Exception:
            continue

    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results[:10]


# ---- 回测端点 ----

class BacktestRequest(BaseModel):
    sectors: list[str] = Field(default_factory=list, description="回测板块列表，空则用全部默认板块")
    start_date: str = Field(..., description="回测开始日期 YYYY-MM-DD")
    end_date: str = Field(..., description="回测结束日期 YYYY-MM-DD")
    min_score: float = Field(default=30.0, description="触发记录的最低评分")


@router.post("/backtest/run")
async def backtest_run(req: BacktestRequest):
    """运行板块信号回测

    对指定时间段逐日运行信号评估，统计胜率、涨幅等关键指标。
    注意：回测调用网络数据，耗时较长，建议在非盘中时段调用。
    """
    import datetime as dt
    from app.backtest.engine import BacktestEngine
    from app.data.models import SECTOR_LIST

    try:
        start = dt.date.fromisoformat(req.start_date)
        end = dt.date.fromisoformat(req.end_date)
    except ValueError as e:
        return {"error": f"日期格式错误: {e}"}

    if (end - start).days > 365:
        return {"error": "回测区间不能超过1年，请分段运行"}

    sectors = req.sectors or SECTOR_LIST
    engine = BacktestEngine(start, end, min_score_to_record=req.min_score)
    metrics = await engine.run_and_report(sectors)

    return {
        "period": f"{start} ~ {end}",
        "sectors": sectors,
        "total_signals": metrics.total_signals,
        "strong_signals": metrics.strong_signals,
        "win_rate": round(metrics.win_rate, 3),
        "win_rate_strong": round(metrics.win_rate_strong, 3),
        "avg_max_return": round(metrics.avg_max_return, 4),
        "max_single_return": round(metrics.max_single_return, 4),
        "sharpe_ratio": metrics.sharpe_ratio,
        "max_drawdown": round(metrics.max_drawdown, 4),
        "by_sector": metrics.by_sector,
        "report": metrics.report_text(),
    }


@router.get("/backtest/quick")
async def backtest_quick(
    days: int = Query(30, description="回测天数（默认30）"),
    sector: str = Query("", description="指定板块，空则取前5个"),
):
    """快速回测（近N日，单板块或前5板块）"""
    import datetime as dt
    from app.backtest.engine import BacktestEngine
    from app.data.models import SECTOR_LIST

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    sectors = [sector] if sector else SECTOR_LIST[:5]

    engine = BacktestEngine(start, end)
    metrics = await engine.run_and_report(sectors)
    return {
        "period": f"{start} ~ {end}",
        "total_signals": metrics.total_signals,
        "win_rate": round(metrics.win_rate, 3),
        "avg_max_return": round(metrics.avg_max_return, 4),
        "sharpe_ratio": metrics.sharpe_ratio,
        "report": metrics.report_text(),
    }


# ---- Walk-Forward + 参数优化端点 ----

@router.get("/backtest/walk-forward")
async def backtest_walk_forward(
    days: int = Query(270, description="总验证天数（默认270）"),
    sector: str = Query("", description="指定板块，空则取前5个"),
):
    """Walk-Forward 滚动窗口验证"""
    import datetime as dt
    from app.backtest.walk_forward import walk_forward_validation
    from app.data.models import SECTOR_LIST

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    sectors = [sector] if sector else SECTOR_LIST[:5]

    report = await walk_forward_validation(start, end, sectors)
    return {
        "windows": len(report.windows),
        "avg_win_rate": round(report.avg_win_rate, 3),
        "avg_return": round(report.avg_return, 4),
        "stability": report.stability,
        "report": report.report_text(),
        "details": [
            {
                "test_start": str(w.test_start),
                "test_end": str(w.test_end),
                "win_rate": round(w.test_metrics.win_rate, 3),
                "avg_return": round(w.test_metrics.avg_max_return, 4),
                "total_signals": w.test_metrics.total_signals,
            }
            for w in report.windows
        ],
    }


@router.post("/params/optimize")
async def params_optimize(
    days: int = Query(60, description="优化用历史天数"),
    sector: str = Query("", description="指定板块，空则取前3个"),
):
    """信号权重参数自动优化"""
    import datetime as dt
    from app.backtest.optimizer import optimize_weights
    from app.data.models import SECTOR_LIST

    end = dt.date.today()
    start = end - dt.timedelta(days=days)
    sectors = [sector] if sector else SECTOR_LIST[:3]

    result = await optimize_weights(start, end, sectors)
    return {
        "combos_tested": result["combos_tested"],
        "best_win_rate": round(result["best_win_rate"], 3),
        "best_avg_return": round(result["best_avg_return"], 4),
        "saved": result["saved"],
        "top5": result["top5"],
    }


# ---- 用户反馈端点 ----

class FeedbackRequest(BaseModel):
    sector_name: str = Field(..., description="板块名称")
    score: float = Field(..., description="信号评分")
    rating: str = Field(..., description="评价: good / bad / neutral")
    comment: str = Field(default="", description="评论（可选）")
    triggered_signals: list[str] = Field(default_factory=list)


@router.post("/feedback")
async def add_feedback(req: FeedbackRequest):
    """提交对信号的评价"""
    from app.feedback import FeedbackStore
    store = FeedbackStore()
    fid = await store.add(
        sector_name=req.sector_name,
        score=req.score,
        rating=req.rating,  # type: ignore[arg-type]
        comment=req.comment,
        triggered_signals=req.triggered_signals,
    )
    return {"id": fid, "status": "ok"}


@router.get("/feedback")
async def list_feedback(limit: int = Query(20, description="返回条数")):
    """查看最近反馈记录"""
    from app.feedback import FeedbackStore
    store = FeedbackStore()
    return await store.list_recent(limit=limit)


@router.get("/feedback/stats")
async def feedback_stats():
    """反馈统计（good/bad/neutral 占比 + 好信号实际涨幅）"""
    from app.feedback import FeedbackStore
    store = FeedbackStore()
    return await store.get_stats()

