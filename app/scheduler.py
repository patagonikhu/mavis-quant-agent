"""实时监控调度器

盘中每5分钟扫描一次全市场板块，触发信号时推送告警。
收盘后15:30全量分析，每周日20:00运行回测验证。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.data import get_data_provider
from app.data.models import SECTOR_LIST, Period

logger = logging.getLogger(__name__)

# 全局调度器单例
_scheduler: Optional[AsyncIOScheduler] = None

# 信号广播队列（供 SSE 推送消费）
_signal_queue: asyncio.Queue = asyncio.Queue(maxsize=200)


def get_signal_queue() -> asyncio.Queue:
    return _signal_queue


async def _scan_sector(sector_name: str, alert_min_score: float = 50.0):
    """扫描单个板块并在达标时告警"""
    try:
        from app.signals import evaluate_sector
        from app.signals.leader import identify_leaders
        from app.alert import dispatch_alert

        provider = get_data_provider()
        bars = await provider.get_sector_kline(sector_name, count=60)
        if len(bars) < 20:
            return

        constituents = await provider.get_sector_constituents(sector_name)
        leaders = identify_leaders(constituents, top_n=3)
        leader_klines: dict = {}
        for ldr in leaders:
            lbars = await provider.get_kline(ldr.symbol, Period.DAILY, 60)
            if lbars:
                leader_klines[ldr.symbol] = lbars

        news_list = None
        try:
            news_list = await provider.get_news_realtime()
        except Exception:
            pass

        report = await evaluate_sector(
            sector_name=sector_name,
            sector_bars=bars,
            constituents=constituents,
            leader_klines=leader_klines,
            news_list=news_list,
        )

        if report.total_score >= alert_min_score:
            leaders_data = [{"symbol": c.symbol, "name": c.name} for c in leaders]
            # 推入广播队列（SSE 消费）
            try:
                _signal_queue.put_nowait({
                    "sector_name": report.sector_name,
                    "total_score": report.total_score,
                    "rating": report.rating,
                    "triggered_signals": report.triggered_signals,
                    "leaders": leaders_data,
                })
            except asyncio.QueueFull:
                pass

            # 发送外部告警
            await dispatch_alert(report, leaders_data, min_score=alert_min_score)

    except Exception as e:
        logger.error("扫描 %s 失败: %s", sector_name, e)


async def intraday_scan():
    """盘中扫描：并发扫描所有板块"""
    logger.info("盘中板块信号扫描开始")
    tasks = [_scan_sector(s) for s in SECTOR_LIST]
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("盘中板块信号扫描完成")


async def daily_full_analysis():
    """收盘后全量分析，结果存日志"""
    logger.info("收盘全量分析开始")
    await intraday_scan()
    logger.info("收盘全量分析完成")


async def weekly_backtest():
    """每周回测验证，输出关键指标到日志"""
    import datetime
    logger.info("每周回测开始")
    try:
        from app.backtest.engine import BacktestEngine
        end = datetime.date.today()
        start = end - datetime.timedelta(days=30)
        engine = BacktestEngine(start, end)
        metrics = await engine.run_and_report(SECTOR_LIST[:5])  # 仅取前5板块做快速验证
        logger.info("每周回测完成:\n%s", metrics.report_text())
    except Exception as e:
        logger.error("每周回测失败: %s", e)


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")

        # 盘中每5分钟扫描（9:30-11:30, 13:00-15:00）
        _scheduler.add_job(
            intraday_scan,
            CronTrigger(
                hour="9-11,13-14",
                minute="*/5",
                day_of_week="mon-fri",
                timezone="Asia/Shanghai",
            ),
            id="intraday_scan",
            replace_existing=True,
            max_instances=1,
        )

        # 收盘后15:30全量分析
        _scheduler.add_job(
            daily_full_analysis,
            CronTrigger(
                hour=15,
                minute=30,
                day_of_week="mon-fri",
                timezone="Asia/Shanghai",
            ),
            id="daily_full_analysis",
            replace_existing=True,
        )

        # 每周日20:00回测验证
        _scheduler.add_job(
            weekly_backtest,
            CronTrigger(
                day_of_week="sun",
                hour=20,
                minute=0,
                timezone="Asia/Shanghai",
            ),
            id="weekly_backtest",
            replace_existing=True,
        )

    return _scheduler


def start_scheduler():
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("调度器已启动（盘中扫描/收盘分析/每周回测）")


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("调度器已停止")
    _scheduler = None
