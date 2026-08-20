"""A股量化智能投顾 Agent - FastAPI 入口"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    settings = get_settings()

    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    logger.info("🚀 A股量化智能投顾 Agent 启动中...")
    logger.info("   LLM: %s (%s)", settings.get_llm_model(), settings.llm_provider)
    logger.info("   数据源: %s", settings.data_provider)
    logger.info("   调试模式: %s", settings.debug)

    # 启动调度器（仅在配置开启时）
    if settings.scheduler_enabled:
        from app.scheduler import start_scheduler
        start_scheduler()
        logger.info("   调度器: 已启动（盘中扫描/收盘分析/每周回测）")
    else:
        logger.info("   调度器: 已禁用（设置 SCHEDULER_ENABLED=true 开启）")

    yield

    # 停止调度器
    if settings.scheduler_enabled:
        from app.scheduler import stop_scheduler
        stop_scheduler()

    logger.info("👋 A股量化智能投顾 Agent 已关闭")


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例"""
    settings = get_settings()

    app = FastAPI(
        title="A股量化智能投顾 Agent",
        description="基于 AI 的 A 股量化分析助手, 提供行情查询、技术分析、信号生成等服务",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 注册路由
    from app.api.routes import router
    from app.api.websocket import router as ws_router

    app.include_router(router, prefix="/v1")
    app.include_router(ws_router, prefix="/v1")

    # 健康检查
    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": "ashare-quant-agent"}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
