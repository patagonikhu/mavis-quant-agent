"""数据提供者抽象基类"""

from __future__ import annotations

import datetime
from abc import ABC, abstractmethod

from app.data.models import (
    FinancialData,
    FundFlow,
    HotRankRecord,
    IndexData,
    KlineBar,
    LhbRecord,
    NewsItem,
    NorthFlowRecord,
    Period,
    ResearchReportRecord,
    SectorConstituentStock,
    SectorFundFlow,
    SectorInfo,
    SectorKlineBar,
    StockInfo,
    StockQuote,
)


class DataProvider(ABC):
    """数据提供者基类，定义所有数据获取接口"""

    # ---- 个股接口 ----

    @abstractmethod
    async def search_stock(self, keyword: str) -> list[StockInfo]:
        """搜索股票"""

    @abstractmethod
    async def get_realtime_quote(self, symbol: str) -> StockQuote:
        """获取实时行情"""

    @abstractmethod
    async def get_kline(self, symbol: str, period: Period, count: int) -> list[KlineBar]:
        """获取K线数据"""

    @abstractmethod
    async def get_financial(self, symbol: str) -> FinancialData:
        """获取财务数据"""

    @abstractmethod
    async def get_fund_flow(self, symbol: str) -> FundFlow:
        """获取个股资金流向"""

    @abstractmethod
    async def get_major_indices(self) -> list[IndexData]:
        """获取主要指数"""

    # ---- 板块接口 ----

    @abstractmethod
    async def get_sector_list(self) -> list[SectorInfo]:
        """获取板块列表"""

    @abstractmethod
    async def get_sector_kline(
        self,
        sector_name: str,
        count: int = 60,
    ) -> list[SectorKlineBar]:
        """获取板块日K线（含涨停家数）"""

    @abstractmethod
    async def get_sector_constituents(
        self,
        sector_name: str,
    ) -> list[SectorConstituentStock]:
        """获取板块成分股及今日涨跌情况"""

    @abstractmethod
    async def get_sector_fund_flow(self) -> list[SectorFundFlow]:
        """获取所有板块资金流向排名"""

    # ---- Phase 2 接口 ----

    @abstractmethod
    async def get_north_flow_by_sector(self, sector_name: str, lookback: int = 20) -> list[NorthFlowRecord]:
        """获取板块北向资金历史净流入（近 lookback 日）"""

    @abstractmethod
    async def get_lhb_data(self, trade_date: "datetime.date") -> list[LhbRecord]:
        """获取龙虎榜数据"""

    @abstractmethod
    async def get_hot_rank_history(self, sector_name: str, lookback: int = 5) -> list[HotRankRecord]:
        """获取板块热度排名历史"""

    @abstractmethod
    async def get_research_count(self, sector_name: str, lookback: int = 30) -> list[ResearchReportRecord]:
        """获取板块研报数量历史"""

    @abstractmethod
    async def get_news_realtime(self) -> list[NewsItem]:
        """获取实时财经新闻"""