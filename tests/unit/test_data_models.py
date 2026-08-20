"""数据层单元测试"""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.cache import DataCache, cache_key_quote, cache_key_kline
from app.data.models import (
    FinancialData,
    FundFlow,
    IndexData,
    KlineBar,
    Period,
    StockInfo,
    StockQuote,
    MAJOR_INDICES,
)


# ---- 数据模型测试 ----

class TestStockQuote:
    def test_create_basic(self):
        quote = StockQuote(symbol="600519", name="贵州茅台", price=1800.0)
        assert quote.symbol == "600519"
        assert quote.name == "贵州茅台"
        assert quote.price == 1800.0
        assert quote.change == 0.0
        assert quote.change_pct == 0.0

    def test_create_full(self):
        quote = StockQuote(
            symbol="600519",
            name="贵州茅台",
            price=1800.0,
            change=50.0,
            change_pct=2.86,
            open_price=1760.0,
            high_price=1810.0,
            low_price=1755.0,
            pre_close=1750.0,
            volume=50000,
            amount=9000000000,
            turnover_rate=0.4,
            pe_ratio=35.0,
            pb_ratio=12.0,
            total_mv=2260000000000,
            circ_mv=2260000000000,
        )
        assert quote.pe_ratio == 35.0
        assert quote.total_mv == 2260000000000


class TestKlineBar:
    def test_create(self):
        bar = KlineBar(
            trade_date=datetime.date(2026, 6, 12),
            open_price=100.0,
            high_price=105.0,
            low_price=99.0,
            close_price=103.0,
            volume=100000,
        )
        assert bar.trade_date == datetime.date(2026, 6, 12)
        assert bar.close_price == 103.0


class TestFinancialData:
    def test_defaults(self):
        data = FinancialData(symbol="600519")
        assert data.pe_ttm == 0.0
        assert data.roe == 0.0
        assert data.revenue == 0.0


class TestFundFlow:
    def test_defaults(self):
        flow = FundFlow(symbol="600519")
        assert flow.main_net_inflow == 0.0
        assert flow.trade_date == datetime.date.today()


class TestIndexData:
    def test_create(self):
        idx = IndexData(symbol="000001", name="上证指数", price=3300.0)
        assert idx.symbol == "000001"
        assert idx.price == 3300.0


class TestStockInfo:
    def test_create(self):
        info = StockInfo(symbol="600519", name="贵州茅台", market="沪市主板")
        assert info.market == "沪市主板"


class TestMajorIndices:
    def test_indices_defined(self):
        assert "000001" in MAJOR_INDICES
        assert "399001" in MAJOR_INDICES
        assert "399006" in MAJOR_INDICES
        assert "000300" in MAJOR_INDICES


class TestPeriod:
    def test_values(self):
        assert Period.DAILY.value == "daily"
        assert Period.WEEKLY.value == "weekly"
        assert Period.MONTHLY.value == "monthly"


# ---- 缓存测试 ----

class TestDataCache:
    @pytest.fixture
    def cache(self):
        return DataCache()

    @pytest.mark.asyncio
    async def test_set_and_get(self, cache: DataCache):
        await cache.set("key1", "value1", ttl=60)
        result = await cache.get("key1")
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, cache: DataCache):
        result = await cache.get("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_expired_entry(self, cache: DataCache):
        await cache.set("key1", "value1", ttl=0.01)
        await asyncio.sleep(0.02)
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self, cache: DataCache):
        await cache.set("key1", "value1", ttl=60)
        await cache.delete("key1")
        result = await cache.get("key1")
        assert result is None

    @pytest.mark.asyncio
    async def test_clear(self, cache: DataCache):
        await cache.set("key1", "value1", ttl=60)
        await cache.set("key2", "value2", ttl=60)
        await cache.clear()
        assert await cache.get("key1") is None
        assert await cache.get("key2") is None
        assert cache.size == 0

    @pytest.mark.asyncio
    async def test_eviction_on_max_size(self):
        small_cache = DataCache(max_size=3)
        await small_cache.set("key1", "v1", ttl=0.01)
        await small_cache.set("key2", "v2", ttl=0.01)
        await small_cache.set("key3", "v3", ttl=0.01)
        await asyncio.sleep(0.02)
        # 触发淘汰
        await small_cache.set("key4", "v4", ttl=60)
        assert await small_cache.get("key4") == "v4"


class TestCacheKeys:
    def test_quote_key(self):
        assert cache_key_quote("600519") == "quote:600519"

    def test_kline_key(self):
        assert cache_key_kline("600519", "daily", 120) == "kline:600519:daily:120"


# ---- AKShare Provider 辅助函数测试 ----

class TestHelpers:
    def test_safe_float(self):
        from app.data.akshare_provider import _safe_float

        assert _safe_float(100) == 100.0
        assert _safe_float("100.5") == 100.5
        assert _safe_float("12.3%") == 12.3
        assert _safe_float(None) == 0.0
        assert _safe_float("") == 0.0
        assert _safe_float("-") == 0.0
        assert _safe_float("--") == 0.0
        assert _safe_float("nan") == 0.0

    def test_detect_market(self):
        from app.data.akshare_provider import _detect_market

        assert _detect_market("600519") == "沪市主板"
        assert _detect_market("000001") == "深市主板"
        assert _detect_market("300750") == "创业板"
        assert _detect_market("688981") == "科创板"
        assert _detect_market("830799") == "北交所"
        assert _detect_market("999999") == "未知"


# ---- Provider ABC 测试 ----

class TestDataProvider:
    def test_is_abstract(self):
        from app.data.provider import DataProvider

        with pytest.raises(TypeError):
            DataProvider()
