"""A股市场交易规则

实现 T+1、涨跌停、交易时段等规则校验。
"""

from __future__ import annotations

import datetime
import logging

from app.market.constants import (
    AFTERNOON_CLOSE,
    AFTERNOON_OPEN,
    CALL_AUCTION_END,
    MIN_LOT_SIZE,
    MORNING_CLOSE,
    MORNING_OPEN,
    PRE_MARKET_OPEN,
    PRICE_LIMIT_CHI_NEXT,
    PRICE_LIMIT_MAIN,
    PRICE_LIMIT_ST,
    PRICE_LIMIT_STAR,
    T_PLUS_DAYS,
)

logger = logging.getLogger(__name__)


def get_price_limit(symbol: str, pre_close: float, is_st: bool = False) -> tuple[float, float]:
    """计算涨跌停价格

    Args:
        symbol: 股票代码
        pre_close: 昨收价
        is_st: 是否为 ST 股票

    Returns:
        (涨停价, 跌停价)
    """
    if is_st:
        limit = PRICE_LIMIT_ST
    elif symbol.startswith("30"):
        limit = PRICE_LIMIT_CHI_NEXT
    elif symbol.startswith("68"):
        limit = PRICE_LIMIT_STAR
    else:
        limit = PRICE_LIMIT_MAIN

    upper = round(pre_close * (1 + limit), 2)
    lower = round(pre_close * (1 - limit), 2)
    return upper, lower


def is_price_limit_hit(symbol: str, price: float, pre_close: float, is_st: bool = False) -> str:
    """判断是否涨跌停

    Returns:
        "up_limit" / "down_limit" / "" (未涨跌停)
    """
    upper, lower = get_price_limit(symbol, pre_close, is_st)
    if price >= upper:
        return "up_limit"
    if price <= lower:
        return "down_limit"
    return ""


def get_market_code(symbol: str) -> str:
    """根据股票代码判断市场板块"""
    if symbol.startswith("60"):
        return "沪市主板"
    if symbol.startswith("00"):
        return "深市主板"
    if symbol.startswith("30"):
        return "创业板"
    if symbol.startswith("68"):
        return "科创板"
    if symbol.startswith("8") or symbol.startswith("4"):
        return "北交所"
    return "未知"


def validate_lot_size(shares: int) -> bool:
    """验证交易数量是否为整手 (100股的整数倍)"""
    return shares > 0 and shares % MIN_LOT_SIZE == 0


def calc_lot_count(shares: int) -> int:
    """计算手数"""
    return shares // MIN_LOT_SIZE


def get_change_pct(price: float, pre_close: float) -> float:
    """计算涨跌幅 (%)"""
    if pre_close <= 0:
        return 0.0
    return round((price - pre_close) / pre_close * 100, 2)


# ---- 交易时段判断 ----

def _parse_time(time_str: str) -> datetime.time:
    """解析 HH:MM 格式时间"""
    h, m = time_str.split(":")
    return datetime.time(int(h), int(m))


def get_market_session(now: datetime.datetime | None = None) -> str:
    """判断当前市场时段

    Returns:
        "closed" / "pre_market" / "call_auction" / "morning" /
        "lunch_break" / "afternoon" / "post_market"
    """
    if now is None:
        now = datetime.datetime.now()

    # 周末休市
    if now.weekday() >= 5:
        return "closed"

    t = now.time()

    pre_open = _parse_time(PRE_MARKET_OPEN)
    call_end = _parse_time(CALL_AUCTION_END)
    morning_open = _parse_time(MORNING_OPEN)
    morning_close = _parse_time(MORNING_CLOSE)
    afternoon_open = _parse_time(AFTERNOON_OPEN)
    afternoon_close = _parse_time(AFTERNOON_CLOSE)

    if t < pre_open:
        return "closed"
    if t < call_end:
        return "call_auction"
    if t < morning_open:
        return "pre_market"
    if t <= morning_close:
        return "morning"
    if t < afternoon_open:
        return "lunch_break"
    if t <= afternoon_close:
        return "afternoon"
    return "post_market"


def is_trading_time(now: datetime.datetime | None = None) -> bool:
    """判断当前是否在交易时间"""
    session = get_market_session(now)
    return session in ("morning", "afternoon", "call_auction")
