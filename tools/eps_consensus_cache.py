"""
tools/eps_consensus_cache.py — 低频数据本地缓存（EPS 机构预期）

主路径（parquet）:
  daily_basic  → data/history/daily_basic/YYYYQN.parquet  (DataStore via history_sync)
  stock_basic  → data/history/ (DataStore via history_sync)
  eps          → data/cache/eps/{code}.json                (每月刷，仅 watchlist)

对外接口:
  get_eps(code)       → list[dict]  (eps_table 格式，跟 analysis 消费的一致)
  refresh_eps(codes)  → 批量刷新 EPS
  refresh_all(codes)  → 刷新所有缓存（当前只有 EPS）
"""

import json
import time
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path("data/cache")
EPS_DIR   = CACHE_DIR / "eps"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
EPS_DIR.mkdir(parents=True, exist_ok=True)

_TTL_EPS = 30 * 24 * 3600  # 30天（机构预期每月更新）


def _watchlist_codes() -> set:
    try:
        return {s["code"] for s in json.loads(
            Path("data/watchlist.json").read_text(encoding="utf-8")
        ).get("stocks", [])}
    except Exception:
        return set()


def _in_watchlist(code: str) -> bool:
    return code in _watchlist_codes()


def _is_stale(path: Path, ttl: int) -> bool:
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime > ttl


# ============================================================
# EPS — 机构一致预期，每月刷
# ============================================================

def get_eps(code: str, force: bool = False) -> list[dict]:
    """返回单只股票的 EPS 预期表。只对 watchlist 股票缓存/拉取。"""
    if not _in_watchlist(code):
        return []

    path = EPS_DIR / f"{code}.json"

    if not force and not _is_stale(path, _TTL_EPS):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    data = _fetch_eps_one(code)
    if data is not None:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def _fetch_eps_one(code: str) -> list[dict] | None:
    try:
        from tools.fetch.data_fetcher import _build_eps_table
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
        if data is not None:
            path = EPS_DIR / f"{code}.json"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            updated += 1
        time.sleep(0.3)
    print(f"  ✅ EPS 刷新 {updated}/{len(codes)} 只")


def refresh_all(codes: list[str], force: bool = False):
    """刷新所有缓存（当前只有 EPS）。"""
    print(f"🔄 刷新缓存: {len(codes)} 只")
    stale_eps = [c for c in codes if force or _is_stale(EPS_DIR / f"{c}.json", _TTL_EPS)]
    if stale_eps:
        print(f"  📥 EPS ({len(stale_eps)} 只需更新)")
        refresh_eps(stale_eps)
    else:
        print(f"  ⏭️  EPS 全部跳过 (未过期)")


# ============================================================
# CLI
# ============================================================

def _load_watchlist_codes() -> list[str]:
    try:
        d = json.loads(Path("data/watchlist.json").read_text(encoding="utf-8"))
        return [s["code"] for s in d.get("stocks", [])]
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
