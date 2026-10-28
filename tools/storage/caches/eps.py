"""
tools/storage/caches/eps.py — EPS 机构一致预期本地缓存 (v6.2.5 改: 单文件)

主路径 (parquet):
  daily_basic   → data/history/stk_factor/YYYYQN.parquet
  stock_basic   → data/history/stock_basic/stock_basic.parquet
  financials    → data/history/financials/YYYYQN.parquet
  eps           → data/history/eps/eps_consensus.parquet  (v6.2.5 改: 1 个全表, 不再 117 拆文件)

v6.2.5 改造: 1 只票 = 1 parquet (4 期 A/E) → 117 票 = 117 文件
              跟 financials/stk_factor 1 季 1 文件不平行, 浪费 117 个 schema
              改成: 全表 = 1 个 parquet, 约 500 行 (117 票 × 4 期 + header)

对外接口:
  get_eps(code)            → list[dict]  (eps_table 格式, 跟 analysis 消费一致)
  write_eps(code, table)   → 内部用, 增量更新 1 只票的 4 期
  EPS_DIR / EPS_FILE       → 路径常量
"""
import json
import os
import tempfile
import threading
import time
from pathlib import Path

import duckdb
import pandas as pd

# v6.2.5 改: 单文件路径, 不再按 code 拆
EPS_DIR = Path("data/history/eps")
EPS_DIR.mkdir(parents=True, exist_ok=True)
EPS_FILE = EPS_DIR / "eps_consensus.parquet"

_TTL_EPS = 30 * 24 * 3600  # 30 天 (机构预期每月更新)

# 2026-10-28 加: 写锁。
# write_eps 是 read-modify-write (读全表 → 改 1 只 → 写全表),
# 多进程 (mp.spawn workers=8) 并发时必然互相覆盖: A 读完 → B 读完 → A 写 → B 写 (B 的版本丢了 A 的数据).
# 叠加 to_parquet 直接写目标文件 (非原子), 另一个进程在写一半时读会拿到半个文件 →
#   duckdb 报 "Could not deserialize thrift: don't know what type" (parquet 损坏).
# 修法: 进程内 threading.Lock + 跨进程文件锁 (fcntl), 写临时文件后 os.replace 原子替换.
_WRITE_LOCK = threading.Lock()


def _watchlist_codes() -> set:
    try:
        # 走 DataStore（统一读 config/watchlist.json），不硬编码 data/watchlist.json
        from tools.storage.store import DataStore
        return {s["code"] for s in DataStore.load_watchlist().get("stocks", [])}
    except Exception:
        return set()


def _in_watchlist(code: str) -> bool:
    return code in _watchlist_codes()


def _is_stale(path: Path, ttl: int) -> bool:
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > ttl


# ============================================================
# EPS — 机构一致预期, 每月刷
# ============================================================

# 8 列 schema (跟 stk_factor 风格统一, 元数据列放最后)
EPS_COLUMNS = [
    "code",          # 6 位 code (主键, 跟 financials 风格一致)
    "year",          # 年份 (2025A / 2026E)
    "year_mark",     # A / E
    "eps",           # EPS (元)
    "net_profit_yi", # 净利润 (亿)
    "revenue_yi",    # 营收 (亿)
    "roe",           # ROE (%)
    "fetched_at",    # unix timestamp (秒), 跟 stk_factor 落盘 metadata 风格一致
]


def _read_all() -> pd.DataFrame:
    """读全表 (返 DataFrame, code 列保留)"""
    if not EPS_FILE.exists():
        return pd.DataFrame(columns=EPS_COLUMNS)
    try:
        return pd.read_parquet(EPS_FILE)
    except Exception as e:
        # 2026-10-28: parquet 损坏 (并发写导致的历史遗留) → 备份坏文件 + 当空表处理
        # 不直接崩, 让 write_eps 能重建
        print(f"  ⚠️ EPS parquet 读取失败 ({type(e).__name__}), 当空表处理: {e}")
        try:
            bad = EPS_FILE.with_suffix(f".parquet.corrupt-{int(time.time())}")
            EPS_FILE.rename(bad)
            print(f"     已备份坏文件: {bad}")
        except Exception:
            pass
        return pd.DataFrame(columns=EPS_COLUMNS)


def _write_all(df: pd.DataFrame) -> None:
    """写全表 (内部用) — 原子写: 先写临时文件, 再 os.replace 覆盖

    2026-10-28 加: 原先 df.to_parquet(EPS_FILE) 直接写目标文件, 写到一半被
    另一个进程读会拿到半个文件 → parquet 损坏. 改成临时文件 + os.replace (原子).
    """
    EPS_DIR.mkdir(parents=True, exist_ok=True)
    # 强制 schema, 补缺失列
    for col in EPS_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[EPS_COLUMNS]
    fd, tmp_path = tempfile.mkstemp(dir=str(EPS_DIR), suffix=".tmp.parquet")
    os.close(fd)
    try:
        df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, EPS_FILE)  # 原子替换
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def get_eps(code: str, force: bool = False) -> list[dict]:
    """返回单只股票的 EPS 预期表 (4 期: 1A + 3E)

    兼容 watchlist 过滤 (历史行为): 非 watchlist 返 []
    force=True 强制重拉 (跳过 stale 检查)

    v6.2.7 改: 不在 watchlist 也尝试读 parquet, 命中即返 (避免误杀)
    2026-09-15 改: 加 _in_watchlist gate, 非 watchlist 直接返 [] (修历史 bug)
    2026-10-08 改: parquet 损坏时直接返 [] (不重试, 避免卡死)
    """
    # 2026-09-15: watchlist gate (历史行为恢复)
    if not force and not _in_watchlist(code):
        return []

    # 2026-10-08: parquet 损坏时, _read_all() 抛 TProtocolException, 直接返 [] 不重试
    # 避免 _is_stale 后 _read_all() 卡住, 让 factor_5y 等能跑过
    if not force and not _is_stale(EPS_FILE, _TTL_EPS):
        try:
            df = _read_all()
            sub = df[df["code"] == code]
            if not sub.empty:
                return _df_to_table(sub)
        except Exception as e:
            # parquet 损坏: 2026-10-08 改, 直接返 [], 不重复重试
            if 'TProtocolException' in str(e) or 'Invalid data' in str(e):
                return []
            # 其他异常继续重试
            pass

    data = _fetch_eps_one(code)
    if data is not None and data:
        write_eps(code, data)
        return data

    # 拉取失败时 fallback 读旧的全表
    try:
        df = _read_all()
        sub = df[df["code"] == code]
        if not sub.empty:
            return _df_to_table(sub)
    except Exception:
        pass
    return []


def _df_to_table(df: pd.DataFrame) -> list[dict]:
    """DataFrame → list[dict] (剔 code/fetched_at 元字段, 跟历史接口兼容)"""
    drop_cols = [c for c in ("code", "fetched_at") if c in df.columns]
    return df.drop(columns=drop_cols).to_dict(orient="records")


def write_eps(code: str, table: list[dict]) -> None:
    """写 1 只票的 4 期 EPS 到全表 (upsert: 删旧 + 加新)

    内部用, sync.py 拉完 1 只票就调一次

    2026-10-28 加锁: read-modify-write 模式在多进程并发下会丢数据 + 写坏 parquet.
    进程内用 threading.Lock, 跨进程用 fcntl.flock (同一把文件锁).
    """
    if not table:
        return
    EPS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = EPS_DIR / ".eps_consensus.lock"
    with _WRITE_LOCK:
        with open(lock_path, "w") as lf:
            try:
                import fcntl
                fcntl.flock(lf, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass  # 非 POSIX 或不支持, 退化成只有进程内锁
            try:
                df_all = _read_all()
                # 删旧 (该 code 的所有行)
                df_all = df_all[df_all["code"] != code]
                # 加新
                new_df = pd.DataFrame(table)
                new_df.insert(0, "code", code)
                new_df["fetched_at"] = time.time()
                df_all = pd.concat([df_all, new_df], ignore_index=True)
                _write_all(df_all)
            finally:
                try:
                    import fcntl
                    fcntl.flock(lf, fcntl.LOCK_UN)
                except (ImportError, OSError):
                    pass


def _fetch_eps_one(code: str) -> list[dict] | None:
    try:
        from ..sources.eastmoney import _build_eps_table
        table, source = _build_eps_table(code)
        if table:
            print(f"    EPS {code}: {len(table)} 条 (source={source})")
        return table or []
    except Exception as e:
        print(f"  ⚠️ fetch eps {code}: {e}")
        return None


def refresh_eps(codes: list[str]):
    """批量刷新 EPS 缓存。"""
    updated = 0
    for code in codes:
        data = _fetch_eps_one(code)
        if data is not None and data:
            write_eps(code, data)
            updated += 1
        time.sleep(0.3)
    print(f"  ✅ EPS 刷新 {updated}/{len(codes)} 只 (单文件 {EPS_FILE})")


def refresh_all(codes: list[str], force: bool = False):
    """刷新所有缓存 (当前只有 EPS)。"""
    print(f"🔄 刷新缓存: {len(codes)} 只")
    if force or _is_stale(EPS_FILE, _TTL_EPS):
        print(f"  📥 EPS (全表更新)")
        refresh_eps(codes)
    else:
        print(f"  ⏭️  EPS 全部跳过 (未过期)")


# ============================================================
# CLI
# ============================================================

def _load_watchlist_codes() -> list[str]:
    try:
        # 走 DataStore（统一读 config/watchlist.json）
        from tools.storage.store import DataStore
        return [s["code"] for s in DataStore.load_watchlist().get("stocks", [])]
    except Exception:
        return []


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="静态缓存刷新")
    parser.add_argument("--force", action="store_true", help="强制全部重拉")
    parser.add_argument("--codes", nargs="*", help="指定股票代码，默认用 watchlist")
    args = parser.parse_args()

    codes = args.codes or _load_watchlist_codes()
    if not codes:
        print("❌ 没有找到股票代码")
        raise SystemExit(1)

    print(f"股票: {codes}")
    refresh_all(codes, force=args.force)
