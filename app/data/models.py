"""数据模型定义

个股 + 板块相关的所有数据模型。
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---- 枚举 ----

class Period(str, Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


# ---- 个股模型 ----

class StockInfo(BaseModel):
    symbol: str
    name: str
    market: str = ""
    industry: str = ""


class StockQuote(BaseModel):
    symbol: str
    name: str
    price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    pre_close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    turnover_rate: float = 0.0
    pe_ratio: float = 0.0
    pb_ratio: float = 0.0
    total_mv: float = 0.0
    circ_mv: float = 0.0


class KlineBar(BaseModel):
    trade_date: datetime.date
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    close_price: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    change_pct: float = 0.0
    turnover_rate: float = 0.0


class FinancialData(BaseModel):
    symbol: str
    name: str = ""
    pe_ttm: float = 0.0
    pb: float = 0.0
    roe: float = 0.0
    gross_margin: float = 0.0
    net_margin: float = 0.0
    revenue: float = 0.0
    revenue_yoy: float = 0.0
    net_profit: float = 0.0
    net_profit_yoy: float = 0.0
    debt_ratio: float = 0.0
    current_ratio: float = 0.0
    total_assets: float = 0.0


class FundFlow(BaseModel):
    symbol: str
    name: str = ""
    trade_date: datetime.date = Field(default_factory=datetime.date.today)
    main_net_inflow: float = 0.0
    main_net_pct: float = 0.0
    super_large_inflow: float = 0.0
    large_inflow: float = 0.0
    medium_inflow: float = 0.0
    small_inflow: float = 0.0


class IndexData(BaseModel):
    symbol: str
    name: str
    price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0


# ---- 板块模型 ----

class SectorKlineBar(BaseModel):
    """板块日K线"""
    trade_date: datetime.date
    open_price: float = 0.0
    high_price: float = 0.0
    low_price: float = 0.0
    close_price: float = 0.0
    volume: float = 0.0
    amount: float = 0.0
    change_pct: float = 0.0
    # 涨停家数（当日板块内涨停股票数量）
    limit_up_count: int = 0


class SectorInfo(BaseModel):
    """板块基本信息"""
    code: str
    name: str
    # 板块内股票数量
    stock_count: int = 0


class SectorConstituentStock(BaseModel):
    """板块成分股"""
    symbol: str
    name: str
    # 相对板块涨跌（今日）
    change_pct: float = 0.0
    # 今日是否涨停
    is_limit_up: bool = False
    # 流通市值（亿元）
    circ_mv: float = 0.0


class SectorFundFlow(BaseModel):
    """板块资金流向"""
    name: str
    trade_date: datetime.date = Field(default_factory=datetime.date.today)
    # 主力净流入（万元）
    main_net_inflow: float = 0.0
    main_net_pct: float = 0.0
    # 超大单净流入（万元）
    super_large_inflow: float = 0.0
    # 大单净流入（万元）
    large_inflow: float = 0.0


# ---- Phase 2 扩展模型 ----

class NorthFlowRecord(BaseModel):
    """北向资金（陆股通）按板块净流入"""
    sector_name: str
    trade_date: datetime.date = Field(default_factory=datetime.date.today)
    net_inflow: float = 0.0   # 万元


class LhbRecord(BaseModel):
    """龙虎榜记录"""
    trade_date: datetime.date
    symbol: str
    name: str = ""
    # 买一~买五席位中机构数量
    institutional_buy_count: int = 0
    # 买入总额（万元）
    buy_amount: float = 0.0


class HotRankRecord(BaseModel):
    """板块热度排名"""
    sector_name: str
    rank: int           # 排名（越小越热）
    record_time: datetime.datetime = Field(default_factory=datetime.datetime.now)


class ResearchReportRecord(BaseModel):
    """研报记录（简化）"""
    sector_name: str
    trade_date: datetime.date
    count: int = 0      # 当日研报篇数


class NewsItem(BaseModel):
    """新闻条目"""
    title: str
    content: str = ""
    source: str = ""
    published_at: datetime.datetime = Field(default_factory=datetime.datetime.now)


# ---- 常量 ----

MAJOR_INDICES = {
    "000001": "上证指数",
    "399001": "深证成指",
    "399006": "创业板指",
    "000300": "沪深300",
    "000016": "上证50",
    "000905": "中证500",
}

# 申万二级行业板块列表（精简版，Phase 1 重点关注）
SECTOR_LIST = [
    "半导体",
    "消费电子",
    "新能源",
    "新能源车",
    "光伏设备",
    "储能",
    "军工",
    "医药生物",
    "创新药",
    "AI",
    "云计算",
    "数字经济",
    "房地产",
    "银行",
    "券商",
    "白酒",
    "机器人",
    "低空经济",
]