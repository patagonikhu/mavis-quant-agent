"""告警发送器

支持钉钉机器人、飞书机器人、通用 Webhook。
格式化输出设计文档 9.1 节样式的告警消息。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from app.signals.scorer import SectorSignalReport

logger = logging.getLogger(__name__)


def _format_alert_text(report: SectorSignalReport, leaders: list[dict]) -> str:
    """格式化告警消息（文本版）"""
    triggered = report.triggered_signals
    triggered_lines = "\n".join(
        f"✓ {ts['name']}: {ts['reason']}"
        for ts in triggered
    )
    leader_lines = "\n".join(
        f"{i+1}. {l.get('name', '')}({l.get('symbol', '')})"
        for i, l in enumerate(leaders[:3])
    )
    return (
        f"🚨 板块启动信号告警\n"
        f"板块: {report.sector_name}\n"
        f"评分: {report.total_score:.0f}/100  {report.rating}\n"
        f"日期: {report.evaluate_date}\n\n"
        f"【触发信号】\n{triggered_lines or '  无'}\n\n"
        f"【建议关注龙头】\n{leader_lines or '  暂无'}\n\n"
        f"⚠️ 仅供研究参考，不构成投资建议"
    )


async def send_dingtalk(
    webhook_url: str,
    report: SectorSignalReport,
    leaders: list[dict],
    secret: Optional[str] = None,
) -> bool:
    """发送钉钉机器人消息"""
    text = _format_alert_text(report, leaders)
    payload: dict[str, Any] = {
        "msgtype": "text",
        "text": {"content": text},
    }

    url = webhook_url
    if secret:
        import hashlib, hmac, base64, time, urllib.parse
        timestamp = str(round(time.time() * 1000))
        sign_str = f"{timestamp}\n{secret}"
        sign = base64.b64encode(
            hmac.new(secret.encode(), sign_str.encode(), digestmod=hashlib.sha256).digest()
        ).decode()
        url = f"{webhook_url}&timestamp={timestamp}&sign={urllib.parse.quote(sign)}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        logger.info("钉钉告警发送成功: %s", report.sector_name)
        return True
    except Exception as e:
        logger.error("钉钉告警发送失败: %s", e)
        return False


async def send_feishu(
    webhook_url: str,
    report: SectorSignalReport,
    leaders: list[dict],
) -> bool:
    """发送飞书机器人消息"""
    text = _format_alert_text(report, leaders)
    payload = {
        "msg_type": "text",
        "content": {"text": text},
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        logger.info("飞书告警发送成功: %s", report.sector_name)
        return True
    except Exception as e:
        logger.error("飞书告警发送失败: %s", e)
        return False


async def send_webhook(
    webhook_url: str,
    report: SectorSignalReport,
    leaders: list[dict],
) -> bool:
    """发送通用 Webhook（POST JSON）"""
    payload = {
        "sector_name": report.sector_name,
        "total_score": report.total_score,
        "rating": report.rating,
        "evaluate_date": str(report.evaluate_date),
        "triggered_signals": report.triggered_signals,
        "leaders": leaders,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        logger.info("Webhook 告警发送成功: %s", report.sector_name)
        return True
    except Exception as e:
        logger.error("Webhook 告警发送失败: %s", e)
        return False


async def dispatch_alert(
    report: SectorSignalReport,
    leaders: list[dict],
    min_score: float = 50.0,
) -> None:
    """按配置自动分发告警"""
    if report.total_score < min_score:
        return

    from app.config import get_settings
    settings = get_settings()

    webhook_url = settings.alert_webhook_url
    webhook_type = settings.alert_webhook_type

    if not webhook_url:
        logger.debug("未配置告警 Webhook，跳过发送")
        return

    if webhook_type == "dingtalk":
        await send_dingtalk(webhook_url, report, leaders, secret=settings.alert_webhook_secret)
    elif webhook_type == "feishu":
        await send_feishu(webhook_url, report, leaders)
    else:
        await send_webhook(webhook_url, report, leaders)
