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

