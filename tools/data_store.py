"""
tools/data_store.py — 统一数据访问层

所有代码通过这里访问数据，不直接读文件。

用法:
    from tools.data_store import DataStore

    raw = DataStore.get_raw("002371")       # 替代 json.load(dump)
    ctx = DataStore.get_ctx("002371")       # 替代 RawContext.from_dump(dump)
    kline = DataStore.get_kline("002371")   # 单独取 K线

内部依赖:
    tools/history_sync.py  — K线历史库 (data/history/daily/*.parquet)
    tools/static_cache.py  — 低频缓存 (data/cache/)
"""

from __future__ import annotations


def _to_ts_code(code: str) -> str:
    if "." in code:
        return code
    c = code.strip()
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    return f"{c}.SZ"


class DataStore:
    """统一数据访问入口，所有方法均为 classmethod，无需实例化。"""

    @classmethod
    def get_kline(cls, code: str, limit: int = 0) -> list[dict]:
        """日线 K线，升序。limit=0 表示全量（默认取 config 里的 kline_days）。"""
        from tools.history_sync import read_kline
        from tools.dump_data import _PROJECT_CFG
        if limit == 0:
            limit = _PROJECT_CFG.get("data", {}).get("kline_days", 1250)
        ts_code = _to_ts_code(code)
        return read_kline(ts_code, limit=limit)

    @classmethod
    def get_weekly(cls, code: str, limit: int = 0) -> list[dict]:
        """周线，从日线聚合，升序。"""
        from tools.fetch.data_fetcher import _synthesize_weekly
        kline = cls.get_kline(code, limit=limit * 5 if limit else 0)
        return _synthesize_weekly(kline)

    @classmethod
    def get_daily_basic(cls, code: str) -> dict:
        """PE/PB/市值等估值快照（每周更新）。"""
        from tools.static_cache import get_daily_basic
        return get_daily_basic(code)

    @classmethod
    def get_stock_basic(cls, code: str) -> dict:
        """行业/名称/上市日期等静态信息（每月更新）。"""
        from tools.static_cache import get_stock_basic
        return get_stock_basic(code)

    @classmethod
    def get_eps(cls, code: str) -> list[dict]:
        """EPS 机构一致预期表（每月更新）。"""
        from tools.static_cache import get_eps
        return get_eps(code)

    @classmethod
    def get_raw(cls, code: str) -> dict:
        """组装完整 raw dict，格式与原 dump json 兼容。

        替代:
            json.load(open(f"data/dump/{code}.json"))
            load_dump(code)
        """
        kline   = cls.get_kline(code)
        weekly  = cls.get_weekly(code)
        sb      = cls.get_stock_basic(code)
        db      = cls.get_daily_basic(code)
        eps     = cls.get_eps(code)

        close = kline[-1]["close"] if kline else (db.get("close") or 0)

        return {
            "code":          code,
            "name":          sb.get("name", ""),
            "industry":      sb.get("industry", ""),
            "list_date":     sb.get("list_date", ""),
            "close":         close,
            "pe_ttm":        db.get("pe_ttm"),
            "pb":            db.get("pb"),
            "total_mv":      db.get("total_mv"),
            "circ_mv":       db.get("circ_mv"),
            "total_share":   sb.get("total_share", 0),
            "turnover_rate": db.get("turnover_rate"),
            "volume_ratio":  db.get("volume_ratio"),
            "kline":         kline,
            "weekly":        weekly,
            "eps_table":     eps,
            "fflow":         {},
            "tushare": {
                "stock_basic": {
                    "ts_code":   _to_ts_code(code),
                    "name":      sb.get("name", ""),
                    "industry":  sb.get("industry", ""),
                    "list_date": sb.get("list_date", ""),
                    "market":    sb.get("market", ""),
                },
                "money_flow": [],
                "weekly":     weekly,
            },
        }

    @classmethod
    def get_ctx(cls, code: str):
        """直接返回 RawContext，替代 RawContext.from_dump(dump)。

        替代:
            dump = json.load(open(...))
            ctx = RawContext.from_dump(dump)
        """
        from tools.analysis.analysis_engine import RawContext
        raw = cls.get_raw(code)
        return RawContext.from_dump(raw)

    @classmethod
    def list_codes(cls) -> list[str]:
        """返回本地历史库里所有有数据的股票代码（6位，不带交易所后缀）。"""
        try:
            import duckdb
            from pathlib import Path
            files = list(Path("data/history/daily").glob("*.parquet"))
            if not files:
                return []
            result = duckdb.execute(
                "SELECT DISTINCT ts_code FROM read_parquet('data/history/daily/*.parquet')"
            ).fetchall()
            codes = []
            for (ts_code,) in result:
                code = ts_code.split(".")[0]
                codes.append(code)
            return sorted(codes)
        except Exception:
            return []

    @classmethod
    def watchlist_codes(cls) -> list[str]:
        """返回 watchlist.json 里的股票代码列表。"""
        import json
        from pathlib import Path
        try:
            d = json.loads(Path("data/watchlist.json").read_text(encoding="utf-8"))
            return [s["code"] for s in d.get("stocks", [])]
        except Exception:
            return []
