"""
tools/storage/caches/stk_factor_history.py — stk_factor 历史本地缓存 (v6.2.5 加)

跟 fflow_history 平行, 同样的 parquet metadata 模式:
  stk_factor    → data/history/stk_factor/{YYYYQN}.parquet
  done 进度塞进 parquet schema.metadata, key=b'done_dates', JSON-encoded

v6.2.5 改造: 干掉 .stk_factor_progress.json 独立进度文件
- 之前用 json 文件, 崩了会丢 (parquet 还没写但 progress 已记 done)
- 现在 done 直接跟 parquet 走, 0 外部文件

对外接口:
  STK_FACTOR_FIELDS         → 16 列 schema 常量
  write_stk_factor_quarter(quarter, rows, done_dates=None)  → 落盘
  read_quarter_done_dates(quarter)        → 单季 done dates (set)
  read_all_done_dates()                  → 跨 5 季 union
"""
from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

# 复用 store.py 已有的 STK_FACTOR_DIR (避免重复定义)
from ..store import STK_FACTOR_DIR

# 16 列, 跟 tushare.py::get_stk_factor_by_date 返回的 fields 一致
# (从 sync.py::STK_FACTOR_FIELDS 搬过来, 跟 fflow_history 平行)
STK_FACTOR_FIELDS = (
    "ts_code,trade_date,close,"
    "pe,pe_ttm,pb,ps,ps_ttm,"
    "dv_ratio,dv_ttm,"
    "total_mv,circ_mv,"
    "turnover_rate,turnover_rate_f,volume_ratio,"
    "total_share,float_share"
).rstrip(",")

# parquet metadata key (bytes)
_META_DONE_DATES = b"done_dates"


def _quarter_of(date_str: str) -> str:
    """YYYYMMDD → YYYYQN (1-3=Q1, 4-6=Q2, 7-9=Q3, 10-12=Q4)"""
    y, m = int(date_str[:4]), int(date_str[4:6])
    q = (m - 1) // 3 + 1
    return f"{y}Q{q}"


def write_stk_factor_quarter(quarter: str, rows: list[dict], done_dates: list[str] | None = None) -> Path | None:
    """单季 stk_factor 落盘 (v6.2.5 改造: done 进度塞 parquet metadata)

    Args:
        quarter: '2026Q2' 格式
        rows: [{ts_code, trade_date, ...16 字段}, ...]
        done_dates: 当季已完成的 trade_date (YYYYMMDD). None=不写 metadata
    Returns: 写入路径 (空 rows 返 None)
    """
    import pandas as pd
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # 强制 16 列 schema
    for col in STK_FACTOR_FIELDS.split(","):
        if col not in df.columns:
            df[col] = None
    df = df[STK_FACTOR_FIELDS.split(",")]
    df = df.drop_duplicates(subset=["ts_code", "trade_date"])
    out = STK_FACTOR_DIR / f"{quarter}.parquet"
    # pyarrow 写 + metadata (兼容 duckdb 读)
    table = pa.Table.from_pandas(df, preserve_index=False)
    if done_dates is not None:
        md = {_META_DONE_DATES: json.dumps(sorted(set(done_dates))).encode()}
        table = table.replace_schema_metadata(md)
    pq.write_table(table, out)
    return out


def read_quarter_done_dates(quarter: str) -> set[str]:
    """读某季 parquet metadata 里的 done dates (返 set, 无数据返空 set)"""
    p = STK_FACTOR_DIR / f"{quarter}.parquet"
    if not p.exists():
        return set()
    try:
        md = pq.read_metadata(str(p)).metadata or {}
        if _META_DONE_DATES in md:
            return set(json.loads(md[_META_DONE_DATES].decode()))
    except Exception:
        pass
    return set()


def read_all_done_dates() -> set[str]:
    """跨 5 季 parquet metadata 读出所有 done dates (union)"""
    out: set[str] = set()
    if not STK_FACTOR_DIR.exists():
        return out
    for f in sorted(STK_FACTOR_DIR.glob("*.parquet")):
        try:
            md = pq.read_metadata(str(f)).metadata or {}
            if _META_DONE_DATES in md:
                out.update(json.loads(md[_META_DONE_DATES].decode()))
        except Exception:
            continue
    return out
