"""数据提供者抽象基类"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.data.models import (
    FinancialData,
    FundFlow,
    IndexData,
    KlineBar,
    Period,
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