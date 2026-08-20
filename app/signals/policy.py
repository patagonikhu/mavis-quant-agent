"""政策/事件信号模块

实现设计文档 4.4 节的两类政策信号：
- 政策关键词命中（规则匹配）+ 时间衰减
- LLM 政策影响评估
"""

from __future__ import annotations

import json
import logging
import math
from datetime import date, datetime

from app.data.models import NewsItem
from app.signals.base import SignalResult

logger = logging.getLogger(__name__)


# ---- 板块政策关键词映射 ----

SECTOR_POLICY_KEYWORDS: dict[str, list[str]] = {
    "半导体": ["芯片", "半导体", "光刻", "国产替代", "大基金", "集成电路"],
    "消费电子": ["手机", "可穿戴", "智能硬件", "消费电子"],
    "新能源": ["新能源", "碳中和", "双碳", "清洁能源"],
    "新能源车": ["新能源车", "电动车", "动力电池", "充电桩", "补贴", "购置税"],
    "光伏设备": ["光伏", "太阳能", "硅料", "组件", "装机量"],
    "储能": ["储能", "电化学", "液流电池", "抽水蓄能"],
    "军工": ["国防", "军费", "装备", "演习", "国防预算", "军工"],
    "医药生物": ["医保", "集采", "创新药", "生物医药", "医疗"],
    "创新药": ["创新药", "NDA", "IND", "临床", "新药"],
    "AI": ["人工智能", "大模型", "算力", "GPU", "AI算力", "智算"],
    "云计算": ["云计算", "数据中心", "IDC", "算力", "云服务"],
    "数字经济": ["数字经济", "数据要素", "工业互联网", "智能制造"],
    "房地产": ["房地产", "楼市", "限购", "首付", "房贷利率", "收储"],
    "银行": ["银行", "降息", "LPR", "存款", "贷款"],
    "券商": ["证券", "IPO", "注册制", "资本市场", "并购"],
    "白酒": ["白酒", "茅台", "消费税", "消费复苏"],
    "机器人": ["机器人", "人形机器人", "工业机器人", "自动化"],
    "低空经济": ["低空经济", "eVTOL", "无人机", "飞行汽车", "通用航空"],
    "半导体设备": ["半导体设备", "刻蚀机", "CVD", "PVD", "CMP", "国产替代", "大基金", "设备国产化"],
}

OFFICIAL_SOURCES = ["国务院", "发改委", "工信部", "央行", "证监会", "财政部", "商务部", "科技部"]

# 不同事件类型的衰减系数（lambda值越大，衰减越快）
# 半衰期 ≈ ln(2) / lambda
# policy(产业政策): lambda=0.05 → 半衰期~14天，影响持续约60天
# earnings(财报): lambda=0.15 → 半衰期~5天，影响持续约20天
# sentiment(情绪): lambda=0.30 → 半衰期~2天，影响持续约7天
DECAY_LAMBDA: dict[str, float] = {
    "policy":    0.05,   # 产业政策/规划/补贴：衰减慢，长期影响
    "earnings":  0.15,   # 财报/订单公告：中速衰减
    "sentiment": 0.30,   # 情绪/传言/概念：衰减快
    "default":   0.10,   # 未分类事件
}


def _days_since(news_date: date | datetime | str | None) -> int:
    """计算新闻距今天数，返回整数（无日期信息返回0，视为今天）。"""
    if news_date is None:
        return 0
    if isinstance(news_date, str):
        try:
            news_date = datetime.fromisoformat(news_date).date()
        except Exception:
            return 0
    if isinstance(news_date, datetime):
        news_date = news_date.date()
    return max(0, (date.today() - news_date).days)


def _time_decay(score: float, days: int, event_type: str = "default") -> float:
    """指数衰减：score × exp(-lambda × days)。

    Args:
        score: 原始得分
        days: 距今天数
        event_type: 事件类型（policy/earnings/sentiment/default）

    Returns:
        衰减后得分
    """
    lam = DECAY_LAMBDA.get(event_type, DECAY_LAMBDA["default"])
    return score * math.exp(-lam * days)


def policy_keyword_hit(
    news_list: list[NewsItem],
    sector_name: str,
    lookback_days: int = 60,
) -> SignalResult:
    """政策关键词命中（规则）+ 时间衰减

    官方来源命中权重=3，其他=1，经时间衰减后累积>=3分触发。

    Args:
        news_list: 新闻列表（NewsItem 需有 published_at 日期字段）
        sector_name: 板块名称
        lookback_days: 最大回看天数，超过此天数的新闻忽略（默认60天）
    """
    keywords = SECTOR_POLICY_KEYWORDS.get(sector_name, [])
    if not keywords:
        return SignalResult(reason=f"板块 {sector_name} 无关键词配置")

    score_decayed = 0.0
    hits: list[dict] = []

    for news in news_list:
        # 获取新闻日期（兼容 published_at / date / pub_date 等字段名）
        news_date = (
            getattr(news, "published_at", None)
            or getattr(news, "date", None)
            or getattr(news, "pub_date", None)
        )
        days = _days_since(news_date)

        # 超过回看窗口的新闻直接跳过
        if days > lookback_days:
            continue

        text = news.title + " " + news.content
        is_official = any(s in news.source for s in OFFICIAL_SOURCES)
        weight = 3.0 if is_official else 1.0

        # 判断事件类型（官方来源 = policy，其他 = default）
        event_type = "policy" if is_official else "default"

        for kw in keywords:
            if kw in text:
                raw = weight
                decayed = _time_decay(raw, days, event_type)
                score_decayed += decayed
                hits.append({
                    "keyword": kw,
                    "title": news.title[:60],
                    "source": news.source,
                    "days_ago": days,
                    "raw_weight": raw,
                    "decayed_weight": round(decayed, 3),
                })
                break  # 同一条新闻不重复计分

    triggered = score_decayed >= 3.0
    score = min(10.0, score_decayed * 1.5) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "decayed_score": round(score_decayed, 3),
            "hit_count": len(hits),
            "hits": hits[:5],
            "lookback_days": lookback_days,
        },
        reason=f"政策关键词命中{len(hits)}条（衰减后得分{score_decayed:.2f}）" if triggered
               else f"政策关键词衰减后得分{score_decayed:.2f}（未达阈值3.0）",
    )


POLICY_EVAL_PROMPT = """你是金融分析师。评估今日政策/事件对 {sector} 板块的影响。

今日相关新闻（最多10条）：
{news_list}

请输出 JSON（只输出JSON，不要有其他文字）：
{{
  "impact_direction": "利好|利空|中性",
  "impact_strength": 1-10,
  "impact_timeframe": "短期|中期|长期",
  "key_drivers": ["核心驱动因素1", "驱动因素2"],
  "risks": ["风险点1"],
  "reasoning": "简要推理"
}}"""


async def llm_policy_evaluation(
    sector_name: str,
    news_list: list[NewsItem],
) -> SignalResult:
    """LLM 政策影响评估

    调用 LLM 对今日相关新闻进行综合判断，返回影响方向和强度。
    """
    if not news_list:
        return SignalResult(reason="无新闻数据，跳过LLM评估")

    # 只取相关新闻（关键词过滤）
    keywords = SECTOR_POLICY_KEYWORDS.get(sector_name, [])
    relevant = [
        n for n in news_list
        if any(kw in n.title + n.content for kw in keywords)
    ][:10]

    if not relevant:
        return SignalResult(reason=f"无 {sector_name} 板块相关新闻")

    news_text = "\n".join(
        f"{i+1}. [{n.source}] {n.title}" for i, n in enumerate(relevant)
    )
    prompt = POLICY_EVAL_PROMPT.format(sector=sector_name, news_list=news_text)

    try:
        from app.llm.client import get_llm
        llm = get_llm()
        # 同步调用在线程中执行
        import asyncio
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(None, lambda: llm.invoke(prompt))
        text = response.content if hasattr(response, "content") else str(response)

        # 提取 JSON
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("LLM 未返回 JSON")
        data = json.loads(text[start:end])

        direction = data.get("impact_direction", "中性")
        strength = int(data.get("impact_strength", 0))
        triggered = direction == "利好" and strength >= 6

        return SignalResult(
            triggered=triggered,
            score=float(strength) if triggered else 0.0,
            detail={
                "impact_direction": direction,
                "impact_strength": strength,
                "impact_timeframe": data.get("impact_timeframe", ""),
                "key_drivers": data.get("key_drivers", []),
                "risks": data.get("risks", []),
                "reasoning": data.get("reasoning", ""),
            },
            reason=f"LLM评估：{direction}，强度{strength}/10",
        )

    except Exception as e:
        logger.warning("llm_policy_evaluation %s 失败: %s", sector_name, e)
        return SignalResult(reason=f"LLM评估失败: {e}")
