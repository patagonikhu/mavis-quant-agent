"""Tushare 数据提供者

基于 Tushare (需 Token) 实现 A 股数据获取。
Tushare 文档: https://tushare.pro/document/2
"""

from __future__ import annotations

import asyncio
import logging
import datetime as dt
from functools import partial
from typing import Optional

import pandas as pd

from app.data.cache import (
    DataCache,
    cache,
    cache_key_financial,
    cache_key_fund_flow,
    cache_key_index,
    cache_key_kline,
    cache_key_quote,
    cache_key_search,
)
from app.data.models import (
    FinancialData,
    FundFlow,
    IndexData,
    KlineBar,
    MAJOR_INDICES,
    Period,
    StockInfo,
    StockQuote,
)
from app.data.provider import DataProvider

logger = logging.getLogger(__name__)


def _get_tushare_api(token: str):
    """获取 Tushare Pro API 实例"""
    import tushare as ts

    ts.set_token(token)
    return ts.pro_api()


class TushareProvider(DataProvider):
    """Tushare 数据提供者"""

    def __init__(self, token: str, cache: Optional[DataCache] = None):
        self._token = token
        self._cache = cache or globals()["cache"]
        self._api = _get_tushare_api(token)

    async def _call(self, method: str, **kwargs):
        """异步调用 Tushare API"""
        loop = asyncio.get_event_loop()
        func = getattr(self._api, method)
        return await loop.run_in_executor(None, partial(func, **kwargs))

    def _to_ts_code(self, symbol: str) -> str:
        """转换股票代码为 Tushare 格式 (如 600519 → 600519.SH)"""
        if "." in symbol:
            return symbol
        if symbol.startswith("6"):
            return f"{symbol}.SH"
        return f"{symbol}.SZ"

    def _from_ts_code(self, ts_code: str) -> str:
        """Tushare 格式转回纯数字代码"""
        return ts_code.split(".")[0] if "." in ts_code else ts_code

    async def get_realtime_quote(self, symbol: str) -> StockQuote:
        key = cache_key_quote(symbol)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        try:
            ts_code = self._to_ts_code(symbol)

            # Tushare 实时行情使用 daily 接口获取最新交易日数据
            df = await self._call("daily", ts_code=ts_code, limit=1)

            if df.empty:
                raise ValueError(f"未找到股票: {symbol}")

            row = df.iloc[0]

            # 获取基本信息
            basic_df = await self._call("stock_basic", ts_code=ts_code, fields="ts_code,name")
            name = basic_df.iloc[0]["name"] if not basic_df.empty else ""

            quote = StockQuote(
                symbol=symbol,
                name=name,
                price=float(row.get("close", 0)),
                change=float(row.get("change", 0)),
                change_pct=float(row.get("pct_chg", 0)),
                open_price=float(row.get("open", 0)),
                high_price=float(row.get("high", 0)),
                low_price=float(row.get("low", 0)),
                pre_close=float(row.get("pre_close", 0)),
                volume=float(row.get("vol", 0)) * 100,  # Tushare vol 单位是手
                amount=float(row.get("amount", 0)) * 1000,  # Tushare amount 单位是千元
                timestamp=dt.datetime.now(),
            )

            await self._cache.set(key, quote, DataCache.TTL_REALTIME)
            return quote

        except Exception as e:
            logger.error("获取实时行情失败 [%s]: %s", symbol, e)
            raise

    async def get_kline(
        self,
        symbol: str,
        period: Period = Period.DAILY,
        count: int = 120,
    ) -> list[KlineBar]:
        key = cache_key_kline(symbol, period.value, count)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        ts_code = self._to_ts_code(symbol)

        period_map = {
            Period.DAILY: "D",
            Period.WEEKLY: "W",
            Period.MONTHLY: "M",
        }

        try:
            end_date = dt.datetime.now().strftime("%Y%m%d")
            days_back = count * 3 if period == Period.DAILY else count * 30
            start_date = (dt.datetime.now() - dt.timedelta(days=days_back)).strftime("%Y%m%d")

            df = await self._call(
                "pro_bar",
                ts_code=ts_code,
                freq=period_map[period],
                start_date=start_date,
                end_date=end_date,
                adj="qfq",
            )

            if df is None or df.empty:
                raise ValueError(f"未找到K线数据: {symbol}")

            # Tushare 返回的是倒序, 需要翻转
            df = df.sort_values("trade_date").tail(count)

            bars = []
            for _, row in df.iterrows():
                bar = KlineBar(
                    trade_date=pd.to_datetime(row["trade_date"]).date(),
                    open_price=float(row.get("open", 0)),
                    high_price=float(row.get("high", 0)),
                    low_price=float(row.get("low", 0)),
                    close_price=float(row.get("close", 0)),
                    volume=float(row.get("vol", 0)),
                    amount=float(row.get("amount", 0)) * 1000,
                    change_pct=float(row.get("pct_chg", 0) or 0),
                    turnover_rate=float(row.get("turnover_rate", 0) or 0),
                )
                bars.append(bar)

            await self._cache.set(key, bars, DataCache.TTL_KLINE)
            return bars

        except Exception as e:
            logger.error("获取K线数据失败 [%s]: %s", symbol, e)
            raise

    async def get_financial(self, symbol: str) -> FinancialData:
        key = cache_key_financial(symbol)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        ts_code = self._to_ts_code(symbol)

        try:
            # 获取财务指标
            fina_df = await self._call(
                "fina_indicator",
                ts_code=ts_code,
                fields="ts_code,ann_date,end_date,eps,bps,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets",
            )

            data = FinancialData(symbol=symbol)

            if fina_df is not None and not fina_df.empty:
                latest = fina_df.iloc[0]
                data.report_date = pd.to_datetime(latest.get("end_date")).date() if "end_date" in latest else None
                data.eps = float(latest.get("eps", 0) or 0)
                data.bps = float(latest.get("bps", 0) or 0)
                data.roe = float(latest.get("roe", 0) or 0)
                data.roa = float(latest.get("roa", 0) or 0)
                data.gross_margin = float(latest.get("grossprofit_margin", 0) or 0)
                data.net_margin = float(latest.get("netprofit_margin", 0) or 0)
                data.debt_ratio = float(latest.get("debt_to_assets", 0) or 0)

            # 获取 PE/PB
            try:
                daily_basic = await self._call(
                    "daily_basic",
                    ts_code=ts_code,
                    fields="ts_code,pe_ttm,pb",
                )
                if daily_basic is not None and not daily_basic.empty:
                    row = daily_basic.iloc[0]
                    data.pe_ttm = float(row.get("pe_ttm", 0) or 0)
                    data.pb = float(row.get("pb", 0) or 0)
            except Exception:
                pass

            await self._cache.set(key, data, DataCache.TTL_FINANCIAL)
            return data

        except Exception as e:
            logger.error("获取财务数据失败 [%s]: %s", symbol, e)
            raise

    async def get_fund_flow(self, symbol: str) -> FundFlow:
        key = cache_key_fund_flow(symbol)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        ts_code = self._to_ts_code(symbol)

        try:
            df = await self._call(
                "moneyflow",
                ts_code=ts_code,
                fields="ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount",
            )

            if df is None or df.empty:
                raise ValueError(f"未找到资金流向: {symbol}")

            latest = df.iloc[0]
            flow = FundFlow(
                symbol=symbol,
                trade_date=pd.to_datetime(latest["trade_date"]).date(),
                super_large_inflow=float(latest.get("buy_elg_amount", 0) or 0) - float(latest.get("sell_elg_amount", 0) or 0),
                large_inflow=float(latest.get("buy_lg_amount", 0) or 0) - float(latest.get("sell_lg_amount", 0) or 0),
                medium_inflow=float(latest.get("buy_md_amount", 0) or 0) - float(latest.get("sell_md_amount", 0) or 0),
                small_inflow=float(latest.get("buy_sm_amount", 0) or 0) - float(latest.get("sell_sm_amount", 0) or 0),
            )
            flow.main_net_inflow = flow.super_large_inflow + flow.large_inflow

            await self._cache.set(key, flow, DataCache.TTL_FUND_FLOW)
            return flow

        except Exception as e:
            logger.error("获取资金流向失败 [%s]: %s", symbol, e)
            raise

    async def get_index_quote(self, symbol: str) -> IndexData:
        key = cache_key_index(symbol)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        # Tushare 指数代码格式: 000001.SH
        ts_code = f"{symbol}.SH" if symbol.startswith("0") or symbol.startswith("3") else f"{symbol}.SZ"
        if symbol in ("000001", "000300", "000905", "000852"):
            ts_code = f"{symbol}.SH"

        try:
            df = await self._call("index_daily", ts_code=ts_code, limit=1)

            if df is None or df.empty:
                raise ValueError(f"未找到指数: {symbol}")

            row = df.iloc[0]
            index_data = IndexData(
                symbol=symbol,
                name=MAJOR_INDICES.get(symbol, symbol),
                price=float(row.get("close", 0)),
                change=float(row.get("change", 0)),
                change_pct=float(row.get("pct_chg", 0)),
                open_price=float(row.get("open", 0)),
                high_price=float(row.get("high", 0)),
                low_price=float(row.get("low", 0)),
                pre_close=float(row.get("pre_close", 0)),
                volume=float(row.get("vol", 0)),
                amount=float(row.get("amount", 0)),
                timestamp=dt.datetime.now(),
            )

            await self._cache.set(key, index_data, DataCache.TTL_INDEX)
            return index_data

        except Exception as e:
            logger.error("获取指数行情失败 [%s]: %s", symbol, e)
            raise

    async def get_major_indices(self) -> list[IndexData]:
        results = []
        for code in MAJOR_INDICES:
            try:
                data = await self.get_index_quote(code)
                results.append(data)
            except Exception as e:
                logger.warning("获取指数 %s 失败: %s", code, e)
        return results

    async def search_stock(self, keyword: str) -> list[StockInfo]:
        key = cache_key_search(keyword)
        cached = await self._cache.get(key)
        if cached is not None:
            return cached

        try:
            df = await self._call(
                "stock_basic",
                exchange="",
                list_status="L",
                fields="ts_code,name,market,list_date,industry",
            )

            if df is None or df.empty:
                return []

            # 按代码或名称模糊匹配
            mask = (
                df["ts_code"].str.contains(keyword, na=False)
                | df["name"].str.contains(keyword, na=False)
            )
            matched = df[mask].head(20)

            results = []
            for _, row in matched.iterrows():
                results.append(
                    StockInfo(
                        symbol=self._from_ts_code(row["ts_code"]),
                        name=str(row.get("name", "")),
                        market=str(row.get("market", "")),
                        industry=str(row.get("industry", "")),
                        list_date=pd.to_datetime(row["list_date"]).date() if "list_date" in row and row["list_date"] else None,
                    )
                )

            await self._cache.set(key, results, DataCache.TTL_SEARCH)
            return results

        except Exception as e:
            logger.error("搜索股票失败 [%s]: %s", keyword, e)
            raise
