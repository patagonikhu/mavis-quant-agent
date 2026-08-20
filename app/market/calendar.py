"""交易日历

判断交易日和市场状态。

数据源策略：
- 优先用 akshare.tool_trade_date_hist_sina() 拿到 1990-2026 的全部 A 股交易日（8797+ 天）
- 一次性加载 + 内存缓存（永不过期，节假日表一年就更新一次）
- 加载失败时降级到"仅排除周末"（老逻辑）
"""

from __future__ import annotations

import datetime
import logging

import akshare as ak
import pandas as pd

logger = logging.getLogger(__name__)

# 缓存：从 akshare 加载的全部 A 股交易日
_TRADING_DAYS_CACHE: set[datetime.date] = set()
_TRADING_DAYS_LOAD_ATTEMPTED: bool = False


def is_weekend(dt: datetime.date | None = None) -> bool:
    """是否为周末"""
    if dt is None:
        dt = datetime.date.today()
    return dt.weekday() >= 5


def _load_trading_days() -> set[datetime.date]:
    """从 akshare 加载完整 A 股交易日历（一次性 + 永久缓存）

    Returns:
        set[date]: 全部已知交易日（含历史 + 未来预排的 1-2 年）

    失败时返回空 set —— 调用方应回退到 is_weekend() 判断。
    """
    global _TRADING_DAYS_CACHE, _TRADING_DAYS_LOAD_ATTEMPTED
    if _TRADING_DAYS_LOAD_ATTEMPTED:
        return _TRADING_DAYS_CACHE

    _TRADING_DAYS_LOAD_ATTEMPTED = True
    try:
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            logger.warning("akshare.tool_trade_date_hist_sina 返回空")
            return set()
        days: set[datetime.date] = set(pd.to_datetime(df["trade_date"]).dt.date.tolist())
        _TRADING_DAYS_CACHE = days
        if days:
            logger.info("加载 A 股交易日历: %d 天 (范围 %s ~ %s)",
                        len(days), min(days), max(days))
        return days
    except Exception as e:
        logger.warning("加载交易日历失败: %s, 降级到 weekend-only 模式", e)
        return set()


def reload_trading_days() -> set[datetime.date]:
    """强制重新加载交易日历（测试 / 节假日调整时用）"""
    global _TRADING_DAYS_CACHE, _TRADING_DAYS_LOAD_ATTEMPTED
    _TRADING_DAYS_CACHE = set()
    _TRADING_DAYS_LOAD_ATTEMPTED = False
    return _load_trading_days()


def is_trading_day(dt: datetime.date | None = None) -> bool:
    """判断是否为 A 股交易日（排除周末 + 排除节假日）

    节假日判断依赖 akshare 交易日历：
    - 加载成功：用真实日历（端午/国庆/春节都识别）
    - 加载失败：降级到 weekend-only（之前的老逻辑）

    Args:
        dt: 要判断的日期，默认今天

    Returns:
        True = 交易日
        False = 周末 或 节假日
    """
    if dt is None:
        dt = datetime.date.today()

    # 先排除周末（快路径）
    if is_weekend(dt):
        return False

    # 查交易日表
    trading_days = _load_trading_days()
    if not trading_days:
        # 加载失败，降级：weekday 默认认为是交易日
        return True

    return dt in trading_days


def get_recent_trading_days(count: int = 5, end_date: datetime.date | None = None) -> list[datetime.date]:
    """获取最近 N 个交易日 (含节假日判断)"""
    if end_date is None:
        end_date = datetime.date.today()

    days = []
    current = end_date
    while len(days) < count:
        if is_trading_day(current):
            days.append(current)
        current -= datetime.timedelta(days=1)
    return list(reversed(days))


def get_next_trading_day(dt: datetime.date | None = None) -> datetime.date:
    """获取下一个交易日"""
    if dt is None:
        dt = datetime.date.today()

    next_day = dt + datetime.timedelta(days=1)
    # 最多往前找 30 天（防极端情况死循环，比如数据源返回空）
    for _ in range(30):
        if is_trading_day(next_day):
            return next_day
        next_day += datetime.timedelta(days=1)
    raise RuntimeError(f"30 天内找不到 {dt} 之后的交易日，交易日历可能为空")


def get_prev_trading_day(dt: datetime.date | None = None) -> datetime.date:
    """获取上一个交易日（用于 sync 默认"昨天"时跳过节假日）"""
    if dt is None:
        dt = datetime.date.today()

    prev = dt - datetime.timedelta(days=1)
    for _ in range(30):
        if is_trading_day(prev):
            return prev
        prev -= datetime.timedelta(days=1)
    raise RuntimeError(f"30 天内找不到 {dt} 之前的交易日，交易日历可能为空")


def get_market_status(dt: datetime.datetime | None = None) -> str:
    """获取市场状态文本

    Returns:
        "交易中" / "午间休市" / "已收盘" / "休市" / "盘前"
    """
    from app.market.rules import get_market_session

    if dt is None:
        dt = datetime.datetime.now()

    if not is_trading_day(dt.date()):
        return "休市 (非交易日)"

    session = get_market_session(dt)
    status_map = {
        "morning": "🟢 交易中 (上午盘)",
        "afternoon": "🟢 交易中 (下午盘)",
        "call_auction": "🟡 集合竞价",
        "pre_market": "🟡 盘前",
        "lunch_break": "🟠 午间休市",
        "post_market": "🔴 已收盘",
        "closed": "🔴 休市",
    }
    return status_map.get(session, "未知")


