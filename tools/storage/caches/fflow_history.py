"""
tools/storage/caches/fflow_history.py — 主力资金历史本地缓存 (v6.2.5 加)

跟 stk_factor 平行:
  stk_factor    → data/history/stk_factor/{YYYYQN}.parquet
  fflow_history → data/history/fflow_history/fflow_{YYYYQN}.parquet

按天拉全市场, 按季存 parquet. 9 字段精简版.

v6.2.5 优化: done 进度塞进 parquet metadata, 不再单独维护 .json 进度文件
- parquet metadata key: b'done_dates'  (JSON 数组 of YYYYMMDD)
- read 跟 write 互不干扰 (duckdb 读数据 + pyarrow 读 metadata)

对外接口:
  FFLOW_HISTORY_DIR          → 目录常量
  read_fflow_history(code)   → 单只票所有历史天 (按 trade_date 升序)
  read_all_done_dates()      → 跨 5 季 parquet 读出所有已 done 的 trade_date (set)
  write_fflow_quarter(quarter, rows, done_dates=None)  → 落盘 (内部用, sync.py 调)
    done_dates=None  → 不写 metadata
    done_dates=list  → 塞进 parquet schema.metadata (JSON-encoded)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

FFLOW_HISTORY_DIR = Path("data/history/fflow_history")
FFLOW_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 9 字段, 跟 tushare.py::FFLOW_HISTORY_FIELDS 一致
FFLOW_HISTORY_FIELDS = (
    "ts_code,trade_date,"
    "buy_sm_amount,sell_sm_amount,"
    "buy_lg_amount,sell_lg_amount,"
    "buy_elg_amount,sell_elg_amount,"
    "net_mf_amount"
)

# parquet metadata key (bytes)
_META_DONE_DATES = b"done_dates"


def _is_stale(path: Path, ttl: int) -> bool:
    """30 天 TTL: fflow 是日频, 30 天内的都算 fresh"""
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > ttl


def _quarter_of(date_str: str) -> str:
    """YYYYMMDD → YYYYQN (1-3=Q1, 4-6=Q2, 7-9=Q3, 10-12=Q4)"""
    y, m = int(date_str[:4]), int(date_str[4:6])
    q = (m - 1) // 3 + 1
    return f"{y}Q{q}"


def write_fflow_quarter(quarter: str, rows: list[dict], done_dates: list[str] | None = None) -> Path | None:
    """单只票 1 季度 1 文件, 落盘 (v6.2.5 改造: done 进度塞 parquet metadata)

    Args:
        quarter: '2026Q2' 格式
        rows: [{ts_code, trade_date, ...9 字段}, ...]
        done_dates: 当季已完成的 trade_date 列表 (YYYYMMDD). None=不写 metadata
                   跟 rows 长度可能不同 (rows 是 trade_date->rows 累加, done_dates 是 trade_date 集合)
    Returns: 写入路径 (空 rows 返 None)
    """
    import pandas as pd
    if not rows:
        return None
    df = pd.DataFrame(rows)
    # 强制 9 列 schema
    for col in FFLOW_HISTORY_FIELDS.split(","):
        if col not in df.columns:
            df[col] = None
    df = df[FFLOW_HISTORY_FIELDS.split(",")]
    df = df.drop_duplicates(subset=["ts_code", "trade_date"])
    out = FFLOW_HISTORY_DIR / f"fflow_{quarter}.parquet"
    # pyarrow 写 + metadata (兼容 duckdb 读)
    table = pa.Table.from_pandas(df, preserve_index=False)
    if done_dates is not None:
        md = {_META_DONE_DATES: json.dumps(sorted(set(done_dates))).encode()}
        table = table.replace_schema_metadata(md)
    pq.write_table(table, out)
    return out


def read_quarter_done_dates(quarter: str) -> set[str]:
    """读某季 parquet metadata 里的 done dates (返 set, 无数据返空 set)"""
    p = FFLOW_HISTORY_DIR / f"fflow_{quarter}.parquet"
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
    if not FFLOW_HISTORY_DIR.exists():
        return out
    for f in sorted(FFLOW_HISTORY_DIR.glob("fflow_*.parquet")):
        try:
            md = pq.read_metadata(str(f)).metadata or {}
            if _META_DONE_DATES in md:
                out.update(json.loads(md[_META_DONE_DATES].decode()))
        except Exception:
            continue
    return out


def read_fflow_history(code: str) -> list[dict]:
    """读单只票所有历史 fflow (跨所有季度 parquet)

    兼容两种入参:
      - 6 位 code: '600519'        → 匹配 ts_code = '600519.SH' / '600519.SZ' 任一
      - 带后缀:    '600519.SH'     → 直接匹配
    """
    out: list[dict] = []
    if not FFLOW_HISTORY_DIR.exists():
        return out
    if "." in code:
        # ts_code 直接匹配
        where = f"ts_code = '{code}'"
    else:
        # 6 位纯 code, 用 split('.')[0] 匹配
        where = f"split_part(ts_code, '.', 1) = '{code}'"
    for f in sorted(FFLOW_HISTORY_DIR.glob("fflow_*.parquet")):
        try:
            df = duckdb.execute(f"""
                SELECT * FROM read_parquet('{f}')
                WHERE {where}
                ORDER BY trade_date
            """).df()
            out.extend(df.to_dict("records"))
        except Exception:
            continue
    return out
