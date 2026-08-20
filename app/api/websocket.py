"""SSE 实时推送

/v1/stream/signals — Server-Sent Events 端点，
调度器检测到信号时推送 JSON 事件给订阅客户端。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter()


async def _event_generator(
    min_score: float,
    timeout: int,
) -> AsyncGenerator[dict, None]:
    """从信号队列读取事件并 yield 给 SSE"""
    from app.scheduler import get_signal_queue
    queue = get_signal_queue()
    elapsed = 0
    interval = 1

    while elapsed < timeout:
        try:
            event = queue.get_nowait()
            if event.get("total_score", 0) >= min_score:
                yield {
                    "event": "signal",
                    "data": json.dumps(event, ensure_ascii=False),
                }
        except asyncio.QueueEmpty:
            # 每30秒发一次心跳
            if elapsed % 30 == 0:
                yield {"event": "heartbeat", "data": "ping"}

        await asyncio.sleep(interval)
        elapsed += interval


@router.get("/stream/signals")
async def stream_signals(
    min_score: float = 50.0,
    timeout: int = 3600,
):
    """SSE 实时信号流

    客户端连接后持续接收板块信号事件，直到超时断开。

    Args:
        min_score: 最低评分阈值（默认50，即中等信号以上才推送）
        timeout: 连接超时秒数（默认3600=1小时）
    """
    return EventSourceResponse(
        _event_generator(min_score, timeout),
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
