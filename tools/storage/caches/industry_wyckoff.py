"""
tools/storage/caches/industry_wyckoff.py — 行业威科夫判定 cache (2026-11-11 加, 2026-11-12 改只读)

⚠️ **写权限只有 /t-sync-data** (2026-11-12 用户定调):
  - **写** → `tools/storage/sync.py` 的 `_refresh_industry_wyckoff_cache()` (唯一合法写者)
  - **读** → 任何 skill / 脚本, **只读不写**

为什么这么定: 原来是「查 cache → 算 → 回写」的懒惰写盘, 批量分析时 4 worker 多进程
(mp.spawn) 会同时写同一年 parquet → 必须上文件锁 (fcntl) + 读改写 + 原子替换。
把写路径收归 sync 之后:
  - 写永远单进程 → **不用锁**
  - 读路径纯查表 → 逻辑简单很多
  - **查不到就不显示** (渲染成「—」) → 空列本身就是"cache 没同步"的信号,
    用户看到就知道该跑 /t-sync-data; 不悄悄自己算 (那会把"缺数据"藏起来)

机器强制: 守门员第 5 项 `cache-writer` (tools/batch/guardrail.py) 扫全仓,
除 sync.py 外任何文件碰写入口都报错; 写入口另有运行时守卫 (双保险)。

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
"""
from __future__ import annotations

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


# ============================================================
# 读接口 (任何 skill 都只能调这几个)
# ============================================================

def get_cached(ts_code: str, trade_date: str) -> dict | None:
    """查 cache (进程内索引), 没有返 None"""
    return _load_index().get(_key(ts_code, trade_date))


def cached_dates(ts_code: str) -> set[str]:
    """某行业已缓存的日期集合"""
    idx = _load_index()
    prefix = f"{ts_code}|"
    return {k[len(prefix):] for k in idx if k.startswith(prefix)}


def cache_max_date() -> str:
    """cache 里最新的交易日 (YYYYMMDD), 空返 ""

    给 /t-sync-data 的 stale 检测用 (比 cached_dates_by_code() 轻得多: 只读 trade_date 列)。
    """
    if not INDUSTRY_WYCKOFF_DIR.exists():
        return ""
    best = ""
    import pandas as pd
    for f in sorted(INDUSTRY_WYCKOFF_DIR.glob("*.parquet")):
        try:
            df = pd.read_parquet(f, columns=["trade_date"])
        except Exception:
            continue
        if df.empty:
            continue
        d = str(df["trade_date"].astype(str).max())
        if d > best:
            best = d
    return best


def cached_dates_by_code() -> dict[str, set[str]]:
    """{ts_code: 已缓存日期集合} —— 批量版 (sync 补 cache 时用)

    比逐个行业调 cached_dates() 快一个量级 (后者每次都全表扫前缀)。
    """
    out: dict[str, set[str]] = {}
    for k in _load_index():
        code, _, date = k.partition("|")
        out.setdefault(code, set()).add(date)
    return out


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


# ============================================================
# 写接口 (唯一合法写者 = tools/storage/sync.py)
# ============================================================

def _assert_sync_caller() -> None:
    """运行时守卫: 写入口只允许 tools/storage/sync.py 调用

    双保险: 除了守门员的静态检查 (cache-writer), 运行时再拦一道。
    """
    import inspect
    for fr in inspect.stack()[1:]:
        p = Path(fr.filename)
        if p.name == "industry_wyckoff.py":
            continue                      # 本模块内部调用
        if p.name == "sync.py" and p.parent.name == "storage":
            return
        raise PermissionError(
            "行业威科夫 cache 只能由 tools/storage/sync.py 写 (唯一合法写者 = /t-sync-data); "
            f"当前调用方 {fr.filename}:{fr.lineno}"
        )


def _atomic_write(path: Path, df) -> None:
    """临时文件 + os.replace 原子落盘 (读方不会看到半个文件)

    ⚠️ 权限要手动改成 644: tempfile.mkstemp 建的文件是 600,
    跟项目其他数据文件 (data/history/**) 不一致。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        os.close(fd)
        df.to_parquet(tmp, index=False)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def cleanup_stale_locks() -> int:
    """清掉旧文件锁机制的残留 .lock 文件 (2026-11-12 锁机制已删)

    老版本 write_rows 用 fcntl 文件锁 → 目录下留了一堆 0 字节的 *.lock。
    现在写永远单进程, 锁文件是纯垃圾。

    删法走 safe_delete (项目铁律: 永不 unlink, 一律移回收站可恢复)。

    Returns:
        清掉的个数
    """
    if not INDUSTRY_WYCKOFF_DIR.exists():
        return 0
    from tools.storage.safe_fs import safe_delete
    n = 0
    for f in sorted(INDUSTRY_WYCKOFF_DIR.glob("*.lock")):
        if safe_delete(f, reason="2026-11-12 行业威科夫 cache 去掉文件锁, 残留 .lock 是垃圾"):
            n += 1
    return n


def write_rows_for_sync(rows: list[dict]) -> int:
    """写入 cache (**唯一合法写者 = tools/storage/sync.py**)

    2026-11-12: 原来这里要上 fcntl 文件锁 —— 因为分析时多进程会同时写同一年文件。
    现在写路径收归 sync (单进程) → **不用锁**, 逻辑简单很多。

    Args:
        rows: CACHE_FIELDS 的 dict 列表

    Returns:
        实际写入的行数
    """
    _assert_sync_caller()
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


def reset_index() -> None:
    """清空进程内索引 (测试用)"""
    global _INDEX
    _INDEX = None
