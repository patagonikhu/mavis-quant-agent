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
from pathlib import Path


def _load_project_cfg() -> dict:
    try:
        import yaml
        p = Path(__file__).parent.parent / "config" / "project.yaml"
        return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


_PROJECT_CFG = _load_project_cfg()


_INDEX_SUFFIX = {
    "000001": "SH", "000300": "SH", "000905": "SH", "000016": "SH",
    "000688": "SH",
    "399001": "SZ", "399006": "SZ", "399808": "SZ",
}

_INDEX_NAMES = {
    "000001": "上证指数",
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "000688": "科创50",
    "399001": "深证成指",
    "399006": "创业板指",
    "399808": "中证新能源",
}


def _to_ts_code(code: str) -> str:
    if "." in code:
        return code
    c = code.strip()
    if c in _INDEX_SUFFIX:
        return f"{c}.{_INDEX_SUFFIX[c]}"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    return f"{c}.SZ"


class DataStore:
    """统一数据访问入口，所有方法均为 classmethod，无需实例化。"""

    @classmethod
    def get_kline(cls, code: str, limit: int = 0) -> list[dict]:
        """日线 K线，升序。limit=0 表示全量（默认取 config 里的 kline_days）。
        统一字段名：vol → volume（parquet 存的是 Tushare 原始 vol，计算层期望 volume）。
        """
        from tools.history_sync import read_kline
        if limit == 0:
            limit = _PROJECT_CFG.get("data", {}).get("kline_days", 1250)
        ts_code = _to_ts_code(code)
        rows = read_kline(ts_code, limit=limit)
        # 统一 vol → volume，保留 vol 做兼容
        for r in rows:
            if 'vol' in r and 'volume' not in r:
                r['volume'] = r['vol']
        return rows

    @classmethod
    def get_weekly(cls, code: str, limit: int = 0) -> list[dict]:
        """周线，从日线聚合，升序。"""
        from tools.fetch.data_fetcher import _synthesize_weekly
        kline = cls.get_kline(code, limit=limit * 5 if limit else 0)
        return _synthesize_weekly(kline)

    @classmethod
    def get_daily_basic(cls, code: str) -> dict:
        """PE/PB/市值等估值快照，从 parquet 读（全市场，近1年）。"""
        from tools.history_sync import read_daily_basic
        return read_daily_basic(_to_ts_code(code))

    @classmethod
    def get_stock_basic(cls, code: str) -> dict:
        """行业/名称/上市日期等静态信息，从 parquet 读（全市场）。"""
        from tools.history_sync import read_stock_basic
        return read_stock_basic(code)

    @classmethod
    def get_eps(cls, code: str) -> list[dict]:
        """EPS 机构一致预期表（每月更新）。"""
        from tools.static_cache import get_eps
        return get_eps(code)

    @classmethod
    def get_ctx(cls, code: str, kline_only: bool = False, limit: int = 0):
        """返回 RawContext（L1 数据层唯一入口）。

        kline_only=True: 只读 K线，跳过 stock_basic/daily_basic/eps。
        limit: K线条数上限，0=使用 config 默认值（kline_days）。
        """
        from tools.analysis.analysis_engine import RawContext
        from tools.fetch.data_fetcher import _synthesize_weekly

        kline  = cls.get_kline(code, limit=limit)
        weekly = _synthesize_weekly(kline)
        close  = kline[-1]["close"] if kline else 0.0

        if kline_only:
            return RawContext(
                kline=kline, weekly=weekly,
                eps_table=[], fflow={}, moneyflow=[],
                current_price=close, market_cap_yi=0.0,
                industry="", code=code, name="",
            )

        sb  = cls.get_stock_basic(code)
        db  = cls.get_daily_basic(code)
        eps = cls.get_eps(code)

        return RawContext(
            kline=kline, weekly=weekly,
            eps_table=eps, fflow={}, moneyflow=[],
            current_price=db.get("close") or close,
            market_cap_yi=db.get("total_mv") or 0.0,
            industry=sb.get("industry", ""),
            code=code, name=_INDEX_NAMES.get(code) or sb.get("name", ""),
        )

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
