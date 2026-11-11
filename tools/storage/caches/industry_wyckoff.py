"""
tools/storage/caches/industry_wyckoff.py — 行业威科夫判定 cache (2026-11-11 加)

为什么需要 cache:
  行业威科夫用的是**个股同一套** WyckoffStrategy.analyze_history (纯 OHLCV, 零改动复用),
  实测 1 个行业全历史 (1696 日) 只要 0.24s —— 但因子历史走势是**按个股**渲染的:
  5,000 只票 → 每个行业被重复算 5,000/134 ≈ 37 次 → 纯浪费 ~20 分钟。
  所以: 算一次, 存下来, 以后直接读。

落盘:
  data/cache/industry_wyckoff/{YYYY}.parquet    ← 按年份, 不按行业
  key = (ts_code, trade_date) → 行业威科夫判定结果 (stage/strength/channel/sub_event)

⚠️ 生命周期: **只增不删** (用户要求)。每天 +134 行 (134 行业 × 1 天), 一年才 5 万行。
   旧行永不变 —— 因为判定是**因果**的 (build_kline_features 全是回看型滚动窗口,
   scan_sub_events 自带 as_of_idx 语义): 某日结果只依赖该日之前的数据。
   例外: tushare 修订历史 bar 时该结论可能失效 → 手动删对应年份文件重算 (极少见)。

并发安全:
  批量分析是 4 worker 多进程 (mp.spawn) → 多个进程会同时写同一年文件。
  写盘走 **文件锁 + 读改写 + 原子替换** (临时文件 + os.replace),
  避免 half-written 文件 (项目有前车之鉴: EPS parquet 并发写损坏 bug)。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# cache 根目录 (派生缓存, 不是同步数据 → 跟 data/analysis_cache.db 同级)
INDUSTRY_WYCKOFF_DIR = Path("data/cache/industry_wyckoff")

# cache 行 schema (字段跟个股 factor history 的威科夫字段一一对应)
CACHE_FIELDS = (
    "ts_code", "trade_date",
    "stage_daily", "stage_weekly",
    "strength_daily", "strength_weekly",
    "channel_daily", "channel_weekly",
    "sub_event_daily", "sub_event_weekly",
)

# 进程内索引: (ts_code|trade_date) → dict。批量跑时避免反复读盘。
_INDEX: dict[str, dict] | None = None


def _key(ts_code: str, trade_date: str) -> str:
    return f"{ts_code}|{trade_date}"


def _load_index() -> dict[str, dict]:
    """把 cache 全部载入内存 (只读 ts_code/trade_date 外的字段全要)"""
    global _INDEX
    if _INDEX is not None:
        return _INDEX

    import pandas as pd

    index: dict[str, dict] = {}
    if INDUSTRY_WYCKOFF_DIR.exists():
        for f in sorted(INDUSTRY_WYCKOFF_DIR.glob("*.parquet")):
            try:
                df = pd.read_parquet(f)
            except Exception:
                continue
            if df.empty:
                continue
            for rec in df.to_dict("records"):
                index[_key(str(rec.get("ts_code", "")), str(rec.get("trade_date", "")))] = rec
    _INDEX = index
    return _INDEX


def get_cached(ts_code: str, trade_date: str) -> dict | None:
    """查 cache (进程内索引), 没有返 None"""
    return _load_index().get(_key(ts_code, trade_date))


def cached_dates(ts_code: str) -> set[str]:
    """某行业已缓存的日期集合"""
    idx = _load_index()
    prefix = f"{ts_code}|"
    return {k[len(prefix):] for k in idx if k.startswith(prefix)}


def _atomic_write(path: Path, df) -> None:
    """临时文件 + os.replace 原子落盘 (多进程下不会读到半个文件)"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.close(fd)
        df.to_parquet(tmp, index=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_rows(rows: list[dict]) -> int:
    """写入 cache (合并到对应年份文件, 读改写 + 文件锁 + 原子替换)

    Args:
        rows: CACHE_FIELDS 的 dict 列表

    Returns:
        实际写入的行数
    """
    if not rows:
        return 0

    import pandas as pd

    df = pd.DataFrame(rows)
    for col in CACHE_FIELDS:
        if col not in df.columns:
            df[col] = None
    df = df[list(CACHE_FIELDS)]
    df["trade_date"] = df["trade_date"].astype(str)
    df["_year"] = df["trade_date"].str[:4]

    written = 0
    for year, group in df.groupby("_year"):
        path = INDUSTRY_WYCKOFF_DIR / f"{year}.parquet"
        group = group.drop(columns=["_year"], errors="ignore")
        with _file_lock(path):
            if path.exists():
                try:
                    old = pd.read_parquet(path)
                    merged = pd.concat([old, group], ignore_index=True)
                    merged = merged.drop_duplicates(
                        subset=["ts_code", "trade_date"], keep="last")
                    merged = merged.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
                except Exception:
                    merged = group.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
            else:
                merged = group.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
            _atomic_write(path, merged)
        written += len(group)
        # 内存索引同步更新 (否则本进程再查还是 miss)
        idx = _load_index()
        for rec in group.to_dict("records"):
            idx[_key(str(rec["ts_code"]), str(rec["trade_date"]))] = rec
    return written


class _file_lock:
    """跨进程文件锁 (fcntl.flock) —— 多 worker 同时写同一年文件时串行化"""

    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(str(self.path) + ".lock", "a+")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        if self._fh:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._fh.close()
                self._fh = None
        return False


def cache_stats() -> dict:
    """cache 概况 (行数 / 行业数 / 年份文件数) —— 给报告或排查用"""
    idx = _load_index()
    codes = {k.split("|", 1)[0] for k in idx}
    files = sorted(INDUSTRY_WYCKOFF_DIR.glob("*.parquet")) if INDUSTRY_WYCKOFF_DIR.exists() else []
    return {
        "rows": len(idx),
        "industries": len(codes),
        "year_files": [f.stem for f in files],
        "dir": str(INDUSTRY_WYCKOFF_DIR),
    }


def reset_index() -> None:
    """清空进程内索引 (测试用)"""
    global _INDEX
    _INDEX = None
