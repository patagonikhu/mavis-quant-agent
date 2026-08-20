"""AKShare 数据提供者实现

通过 akshare 获取 A 股行情、财务、资金流、板块数据。
所有网络调用都在线程池中执行（akshare 是同步库）。
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

import pandas as pd

from app.data.cache import (
    DataCache,
    cache_key_kline,
    cache_key_quote,
    cache_key_sector_constituents,
    cache_key_sector_fund_flow,
    cache_key_sector_kline,
)
from app.data.models import (
    FinancialData,
    FundFlow,
    HotRankRecord,
    IndexData,
    KlineBar,
    LhbRecord,
    NewsItem,
    NorthFlowRecord,
    MAJOR_INDICES,
    Period,
    ResearchReportRecord,
    SectorConstituentStock,
    SectorFundFlow,
    SectorInfo,
    SectorKlineBar,
    StockInfo,
    StockQuote,
)
from app.data.provider import DataProvider

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="akshare")


async def _run(fn, *args, **kwargs) -> Any:
    """在线程池中运行同步函数"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, partial(fn, *args, **kwargs))


# ---- 辅助工具 ----

def _safe_float(val: Any, default: float = 0.0) -> float:
    """安全转换为 float，处理各种脏数据格式"""
    if val is None:
        return default
    if isinstance(val, float):
        import math
        if math.isnan(val) or math.isinf(val):
            return default
        return val
    if isinstance(val, (int,)):
        return float(val)
    s = str(val).strip().replace(",", "").replace("%", "").replace("--", "").replace("-", "")
    if not s or s.lower() in ("nan", "none", "null", ""):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    return int(_safe_float(val, default))


def _detect_market(symbol: str) -> str:
    if symbol.startswith("688") or symbol.startswith("689"):
        return "科创板"
    if symbol.startswith("6"):
        return "沪市主板"
    if symbol.startswith("300") or symbol.startswith("301"):
        return "创业板"
    if symbol.startswith("000") or symbol.startswith("001"):
        return "深市主板"
    if symbol.startswith("8") or symbol.startswith("4"):
        return "北交所"
    return "未知"


def _parse_date(val: Any) -> datetime.date:
    if isinstance(val, datetime.date):
        return val
    if isinstance(val, datetime.datetime):
        return val.date()
    s = str(val).strip().replace("-", "").replace("/", "")
    try:
        return datetime.datetime.strptime(s[:8], "%Y%m%d").date()
    except Exception:
        return datetime.date.today()


# ---- AKShare Provider ----

class AKShareProvider(DataProvider):

    def __init__(self):
        self._cache = DataCache()

    # ---- 个股接口 ----

    async def search_stock(self, keyword: str) -> list[StockInfo]:
        import akshare as ak
        try:
            df = await _run(ak.stock_info_a_code_name)
            if df is None or df.empty:
                return []
            mask = (
                df["code"].str.contains(keyword, na=False) |
                df["name"].str.contains(keyword, na=False)
            )
            rows = df[mask].head(10)
            return [
                StockInfo(
                    symbol=row["code"],
                    name=row["name"],
                    market=_detect_market(row["code"]),
                )
                for _, row in rows.iterrows()
            ]
        except Exception as e:
            logger.warning("search_stock 失败: %s", e)
            return []

    async def get_realtime_quote(self, symbol: str) -> StockQuote:
        cached = await self._cache.get(cache_key_quote(symbol))
        if cached:
            return cached

        import akshare as ak
        try:
            df = await _run(ak.stock_zh_a_spot_em)
            if df is None or df.empty:
                return StockQuote(symbol=symbol, name=symbol)
            row = df[df["代码"] == symbol]
            if row.empty:
                return StockQuote(symbol=symbol, name=symbol)
            r = row.iloc[0]
            quote = StockQuote(
                symbol=symbol,
                name=str(r.get("名称", "")),
                price=_safe_float(r.get("最新价")),
                change=_safe_float(r.get("涨跌额")),
                change_pct=_safe_float(r.get("涨跌幅")),
                open_price=_safe_float(r.get("今开")),
                high_price=_safe_float(r.get("最高")),
                low_price=_safe_float(r.get("最低")),
                pre_close=_safe_float(r.get("昨收")),
                volume=_safe_float(r.get("成交量")) * 100,
                amount=_safe_float(r.get("成交额")),
                turnover_rate=_safe_float(r.get("换手率")),
                pe_ratio=_safe_float(r.get("市盈率-动态")),
                pb_ratio=_safe_float(r.get("市净率")),
                total_mv=_safe_float(r.get("总市值")),
                circ_mv=_safe_float(r.get("流通市值")),
            )
            await self._cache.set(cache_key_quote(symbol), quote, ttl=30)
            return quote
        except Exception as e:
            logger.warning("get_realtime_quote %s 失败: %s", symbol, e)
            return StockQuote(symbol=symbol, name=symbol)

    async def get_kline(self, symbol: str, period: Period, count: int) -> list[KlineBar]:
        key = cache_key_kline(symbol, period.value, count)
        cached = await self._cache.get(key)
        if cached:
            return cached

        import akshare as ak
        period_map = {
            Period.DAILY: "daily",
            Period.WEEKLY: "weekly",
            Period.MONTHLY: "monthly",
        }
        try:
            df = await _run(
                ak.stock_zh_a_hist,
                symbol=symbol,
                period=period_map[period],
                adjust="qfq",
            )
            if df is None or df.empty:
                return []
            df = df.tail(count)
            bars = []
            for _, r in df.iterrows():
                bars.append(KlineBar(
                    trade_date=_parse_date(r.get("日期", r.get("date", ""))),
                    open_price=_safe_float(r.get("开盘")),
                    high_price=_safe_float(r.get("最高")),
                    low_price=_safe_float(r.get("最低")),
                    close_price=_safe_float(r.get("收盘")),
                    volume=_safe_float(r.get("成交量")) * 100,
                    amount=_safe_float(r.get("成交额")),
                    change_pct=_safe_float(r.get("涨跌幅")),
                    turnover_rate=_safe_float(r.get("换手率")),
                ))
            await self._cache.set(key, bars, ttl=300)
            return bars
        except Exception as e:
            logger.warning("get_kline %s 失败: %s", symbol, e)
            return []

    async def get_financial(self, symbol: str) -> FinancialData:
        import akshare as ak
        try:
            df = await _run(ak.stock_a_indicator_lg, symbol=symbol)
            if df is None or df.empty:
                return FinancialData(symbol=symbol)
            r = df.iloc[-1]
            return FinancialData(
                symbol=symbol,
                pe_ttm=_safe_float(r.get("pe")),
                pb=_safe_float(r.get("pb")),
                roe=_safe_float(r.get("roe")),
            )
        except Exception as e:
            logger.warning("get_financial %s 失败: %s", symbol, e)
            return FinancialData(symbol=symbol)

    async def get_fund_flow(self, symbol: str) -> FundFlow:
        import akshare as ak
        try:
            # 先尝试沪市，失败（深圳股票）时静默 fallback 到深市
            try:
                df = await _run(ak.stock_individual_fund_flow, stock=symbol, market="sh")
            except Exception:
                df = None
            if df is None or df.empty:
                df = await _run(ak.stock_individual_fund_flow, stock=symbol, market="sz")
            if df is None or df.empty:
                return FundFlow(symbol=symbol)
            r = df.iloc[-1]
            return FundFlow(
                symbol=symbol,
                trade_date=_parse_date(r.get("日期", "")),
                main_net_inflow=_safe_float(r.get("主力净流入-净额")) * 10000,
                main_net_pct=_safe_float(r.get("主力净流入-净占比")),
                super_large_inflow=_safe_float(r.get("超大单净流入-净额")) * 10000,
                large_inflow=_safe_float(r.get("大单净流入-净额")) * 10000,
                medium_inflow=_safe_float(r.get("中单净流入-净额")) * 10000,
                small_inflow=_safe_float(r.get("小单净流入-净额")) * 10000,
            )
        except Exception as e:
            logger.warning("get_fund_flow %s 失败: %s", symbol, e)
            return FundFlow(symbol=symbol)

    async def get_major_indices(self) -> list[IndexData]:
        import akshare as ak
        try:
            df = await _run(ak.stock_zh_index_spot_em)
            if df is None or df.empty:
                return []
            result = []
            for code, name in MAJOR_INDICES.items():
                row = df[df["代码"] == code]
                if row.empty:
                    continue
                r = row.iloc[0]
                result.append(IndexData(
                    symbol=code,
                    name=name,
                    price=_safe_float(r.get("最新价")),
                    change=_safe_float(r.get("涨跌额")),
                    change_pct=_safe_float(r.get("涨跌幅")),
                ))
            return result
        except Exception as e:
            logger.warning("get_major_indices 失败: %s", e)
            return []

    # ---- 板块接口 ----

    async def get_sector_list(self) -> list[SectorInfo]:
        import akshare as ak
        try:
            df = await _run(ak.stock_board_industry_name_em)
            if df is None or df.empty:
                return []
            return [
                SectorInfo(
                    code=str(r.get("板块代码", "")),
                    name=str(r.get("板块名称", "")),
                    stock_count=_safe_int(r.get("公司家数")),
                )
                for _, r in df.iterrows()
            ]
        except Exception as e:
            logger.warning("get_sector_list 失败: %s", e)
            return []

    async def get_sector_kline(
        self,
        sector_name: str,
        count: int = 60,
    ) -> list[SectorKlineBar]:
        key = cache_key_sector_kline(sector_name, count)
        cached = await self._cache.get(key)
        if cached:
            return cached

        import akshare as ak
        try:
            today = datetime.date.today()
            start = (today - datetime.timedelta(days=count * 2)).strftime("%Y%m%d")
            end = today.strftime("%Y%m%d")
            df = await _run(
                ak.stock_board_industry_hist_em,
                symbol=sector_name,
                start_date=start,
                end_date=end,
                period="日k",
                adjust="",
            )
            if df is None or df.empty:
                return []
            df = df.tail(count)

            # 获取当日涨停家数（尽力而为）
            limit_up_map: dict[str, int] = {}
            try:
                cons = await _run(ak.stock_board_industry_cons_em, symbol=sector_name)
                if cons is not None and not cons.empty:
                    # 最新一天的成分股中涨停的数量
                    latest_date_str = str(df.iloc[-1].get("日期", "")).replace("-", "")
                    spot = await _run(ak.stock_zh_a_spot_em)
                    if spot is not None and not spot.empty:
                        symbols = set(cons["代码"].astype(str).tolist())
                        spot_filtered = spot[spot["代码"].isin(symbols)]
                        limit_up_count = int(
                            (spot_filtered["涨跌幅"] >= 9.9).sum()
                        )
                        limit_up_map[latest_date_str] = limit_up_count
            except Exception:
                pass

            bars = []
            dates_seen = set()
            for _, r in df.iterrows():
                d = _parse_date(r.get("日期", ""))
                if d in dates_seen:
                    continue
                dates_seen.add(d)
                date_str = d.strftime("%Y%m%d")
                bars.append(SectorKlineBar(
                    trade_date=d,
                    open_price=_safe_float(r.get("开盘")),
                    high_price=_safe_float(r.get("最高")),
                    low_price=_safe_float(r.get("最低")),
                    close_price=_safe_float(r.get("收盘")),
                    volume=_safe_float(r.get("成交量")),
                    amount=_safe_float(r.get("成交额")),
                    change_pct=_safe_float(r.get("涨跌幅")),
                    limit_up_count=limit_up_map.get(date_str, 0),
                ))
            bars.sort(key=lambda b: b.trade_date)
            await self._cache.set(key, bars, ttl=300)
            return bars
        except Exception as e:
            logger.warning("get_sector_kline %s 失败: %s", sector_name, e)
            return []

    async def get_sector_constituents(
        self,
        sector_name: str,
    ) -> list[SectorConstituentStock]:
        key = cache_key_sector_constituents(sector_name)
        cached = await self._cache.get(key)
        if cached:
            return cached

        import akshare as ak
        try:
            cons_df = await _run(ak.stock_board_industry_cons_em, symbol=sector_name)
            if cons_df is None or cons_df.empty:
                return []

            symbols = set(cons_df["代码"].astype(str).tolist())
            spot_df = await _run(ak.stock_zh_a_spot_em)

            results = []
            for _, row in cons_df.iterrows():
                sym = str(row.get("代码", ""))
                name = str(row.get("名称", ""))
                change_pct = 0.0
                is_limit_up = False
                circ_mv = 0.0

                if spot_df is not None and not spot_df.empty:
                    spot_row = spot_df[spot_df["代码"] == sym]
                    if not spot_row.empty:
                        sr = spot_row.iloc[0]
                        change_pct = _safe_float(sr.get("涨跌幅"))
                        circ_mv = _safe_float(sr.get("流通市值")) / 1e8
                        is_limit_up = change_pct >= 9.9

                results.append(SectorConstituentStock(
                    symbol=sym,
                    name=name,
                    change_pct=change_pct,
                    is_limit_up=is_limit_up,
                    circ_mv=circ_mv,
                ))

            await self._cache.set(key, results, ttl=60)
            return results
        except Exception as e:
            logger.warning("get_sector_constituents %s 失败: %s", sector_name, e)
            return []

    async def get_sector_fund_flow(self) -> list[SectorFundFlow]:
        key = cache_key_sector_fund_flow()
        cached = await self._cache.get(key)
        if cached:
            return cached

        import akshare as ak
        try:
            df = await _run(ak.stock_sector_fund_flow_rank, indicator="今日")
            if df is None or df.empty:
                return []
            results = []
            for _, r in df.iterrows():
                results.append(SectorFundFlow(
                    name=str(r.get("名称", "")),
                    main_net_inflow=_safe_float(r.get("主力净流入-净额")),
                    main_net_pct=_safe_float(r.get("主力净流入-净占比")),
                    super_large_inflow=_safe_float(r.get("超大单净流入-净额")),
                    large_inflow=_safe_float(r.get("大单净流入-净额")),
                ))
            await self._cache.set(key, results, ttl=60)
            return results
        except Exception as e:
            logger.warning("get_sector_fund_flow 失败: %s", e)
            return []

    # ---- Phase 2 接口 ----

    async def get_north_flow_by_sector(
        self,
        sector_name: str,
        lookback: int = 20,
    ) -> list[NorthFlowRecord]:
        import akshare as ak
        try:
            df = await _run(ak.stock_hsgt_board_rank_em, indicator="今日")
            if df is None or df.empty:
                return []
            row = df[df["名称"].str.contains(sector_name, na=False)]
            if row.empty:
                return []
            r = row.iloc[0]
            return [NorthFlowRecord(
                sector_name=sector_name,
                trade_date=datetime.date.today(),
                net_inflow=_safe_float(r.get("今日净买入", r.get("净买入", 0))),
            )]
        except Exception as e:
            logger.warning("get_north_flow_by_sector %s 失败: %s", sector_name, e)
            return []

    async def get_lhb_data(self, trade_date: datetime.date) -> list[LhbRecord]:
        import akshare as ak
        import re
        date_str = trade_date.strftime("%Y%m%d")
        try:
            # akshare 1.14+ 改了签名：date= → start_date= + end_date=
            df = await _run(ak.stock_lhb_detail_em, start_date=date_str, end_date=date_str)
            if df is None or df.empty:
                return []
            results = []
            # 新版 akshare 砍了 "买方机构" 字段，改从 "解读" 字段正则提取 "X家机构...买入"
            inst_buy_pat = re.compile(r"(\d+)家机构.*?买入")
            for _, r in df.iterrows():
                sym = str(r.get("代码", ""))
                comment = str(r.get("解读", "") or "")
                m = inst_buy_pat.search(comment)
                institutional_count = int(m.group(1)) if m else 0
                results.append(LhbRecord(
                    trade_date=trade_date,
                    symbol=sym,
                    name=str(r.get("名称", "")),
                    institutional_buy_count=institutional_count,
                    # akshare 返回单位是"元"，表 schema 注释是"万元"，统一除 10000
                    buy_amount=_safe_float(r.get("龙虎榜买入额", 0)) / 10000,
                ))
            return results
        except Exception as e:
            logger.warning("get_lhb_data %s 失败: %s", date_str, e)
            return []

    async def get_hot_rank_history(
        self,
        sector_name: str,
        lookback: int = 5,
    ) -> list[HotRankRecord]:
        import akshare as ak
        try:
            df = await _run(ak.stock_board_industry_spot_em)
            if df is None or df.empty:
                return []
            # 按涨跌幅降序作为热度代理（akshare 无专属热度接口）
            df_sorted = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
            matches = df_sorted[df_sorted["板块名称"].str.contains(sector_name, na=False)]
            if matches.empty:
                return []
            rank = int(matches.index[0]) + 1
            now = datetime.datetime.now()
            return [HotRankRecord(sector_name=sector_name, rank=rank, record_time=now)]
        except Exception as e:
            logger.warning("get_hot_rank_history %s 失败: %s", sector_name, e)
            return []

    async def get_research_count(
        self,
        sector_name: str,
        lookback: int = 30,
    ) -> list[ResearchReportRecord]:
        # akshare 无直接研报数量接口，返回空列表（后续可接东财研报）
        return []

    async def get_news_realtime(self) -> list[NewsItem]:
        import akshare as ak
        try:
            df = await _run(ak.stock_news_em)
            if df is None or df.empty:
                return []
            results = []
            for _, r in df.head(50).iterrows():
                results.append(NewsItem(
                    title=str(r.get("新闻标题", r.get("title", ""))),
                    content=str(r.get("新闻内容", r.get("content", ""))),
                    source=str(r.get("文章来源", r.get("source", ""))),
                ))
            return results
        except Exception as e:
            logger.warning("get_news_realtime 失败: %s", e)
            return []