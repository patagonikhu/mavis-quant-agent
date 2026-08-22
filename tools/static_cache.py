"""
tools/static_cache.py — 低频数据本地缓存

管理三类低频数据，按 mtime 判断是否需要重拉：

  daily_basic  → data/cache/daily_basic.json   每周刷一次 (7天)
  stock_basic  → data/cache/stock_basic.json   每月刷一次 (30天)
  eps          → data/cache/eps/{code}.json    每月刷一次 (30天)

对外接口:
  get_daily_basic(code)  → {"total_mv": ..., "pe_ttm": ..., "pb": ..., "circ_mv": ...}
  get_stock_basic(code)  → {"name": ..., "industry": ..., "list_date": ..., "total_share": ...}
  get_eps(code)          → list[dict]  (eps_table 格式，跟 analysis 消费的一致)
  refresh_all(codes)     → 强制刷新所有缓存
"""

import json
import time
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path("data/cache")
EPS_DIR   = CACHE_DIR / "eps"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
EPS_DIR.mkdir(parents=True, exist_ok=True)

DAILY_BASIC_PATH = CACHE_DIR / "daily_basic.json"
STOCK_BASIC_PATH = CACHE_DIR / "stock_basic.json"

# 过期阈值（秒）
_TTL_DAILY_BASIC = 7  * 24 * 3600   # 7天
_TTL_STOCK_BASIC = 30 * 24 * 3600   # 30天
_TTL_EPS         = 30 * 24 * 3600   # 30天


def _watchlist_codes() -> set:
    """返回 watchlist.json 里的股票代码集合，用于判断是否需要缓存。"""
    try:
        return {s["code"] for s in json.loads(
            Path("data/watchlist.json").read_text(encoding="utf-8")
        ).get("stocks", [])}
    except Exception:
        return set()


def _in_watchlist(code: str) -> bool:
    return code in _watchlist_codes()


# ============================================================
# 1. 工具函数
# ============================================================

def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_stale(path: Path, ttl: int) -> bool:
    """文件不存在或超过 ttl 秒视为过期。"""
    if not path.exists():
        return True
    age = time.time() - path.stat().st_mtime
    return age > ttl


def _now_str() -> str:
    return datetime.now().strftime("%Y%m%d")


# ============================================================
# 2. daily_basic — PE/市值，每周刷
# ============================================================

def get_daily_basic(code: str, force: bool = False) -> dict:
    """返回单只股票最新的 daily_basic 快照。只对 watchlist 股票缓存/拉取。"""
    if not _in_watchlist(code):
        return {}

    cache = _load_json(DAILY_BASIC_PATH)
    entry = cache.get(code)

    if not force and entry and not _is_stale(DAILY_BASIC_PATH, _TTL_DAILY_BASIC):
        return entry

    # 需要刷新整个文件（按只刷太零散，统一刷 watchlist）
    # 单只调用时，如果文件整体没过期但这只没有记录，也拉一次
    if not force and entry:
        return entry

    # 拉单只
    data = _fetch_daily_basic_one(code)
    if data:
        cache[code] = {**data, "updated": _now_str()}
        _save_json(DAILY_BASIC_PATH, cache)
        return cache[code]

    return entry or {}


def _fetch_daily_basic_one(code: str) -> dict | None:
    try:
        from tools.fetch.tushare_fetcher import get_daily_basic
        rows, status = get_daily_basic(code, limit=1)
        if not rows:
            return None
        row = rows[-1]  # 最新一条
        return {
            "total_mv":      row.get("total_mv"),
            "circ_mv":       row.get("circ_mv"),
            "pe_ttm":        row.get("pe_ttm"),
            "pb":            row.get("pb"),
            "turnover_rate": row.get("turnover_rate"),
            "volume_ratio":  row.get("volume_ratio"),
            "close":         row.get("close"),
        }
    except Exception as e:
        print(f"  ⚠️ fetch daily_basic {code}: {e}")
        return None


def refresh_daily_basic(codes: list[str]):
    """批量刷新 daily_basic，适合每周跑一次。"""
    cache = _load_json(DAILY_BASIC_PATH)
    updated = 0
    for code in codes:
        data = _fetch_daily_basic_one(code)
        if data:
            cache[code] = {**data, "updated": _now_str()}
            updated += 1
            time.sleep(0.2)
    _save_json(DAILY_BASIC_PATH, cache)
    print(f"  ✅ daily_basic 刷新 {updated}/{len(codes)} 只")


# ============================================================
# 3. stock_basic — 行业/名称，每月刷
# ============================================================

def get_stock_basic(code: str, force: bool = False) -> dict:
    """返回单只股票的静态基础信息。只对 watchlist 股票缓存/拉取。"""
    if not _in_watchlist(code):
        return {}

    cache = _load_json(STOCK_BASIC_PATH)
    entry = cache.get(code)

    if not force and entry and not _is_stale(STOCK_BASIC_PATH, _TTL_STOCK_BASIC):
        return entry

    if not force and entry:
        return entry

    data = _fetch_stock_basic_one(code)
    if data:
        cache[code] = {**data, "updated": _now_str()}
        _save_json(STOCK_BASIC_PATH, cache)
        return cache[code]

    return entry or {}


def _fetch_stock_basic_one(code: str) -> dict | None:
    try:
        from tools.fetch.tushare_fetcher import get_stock_basic
        row, status = get_stock_basic(code)
        if not row:
            return None
        return {
            "name":         row.get("name", ""),
            "industry":     row.get("industry", ""),
            "list_date":    row.get("list_date", ""),
            "total_share":  row.get("total_share", 0),
            "float_share":  row.get("float_share", 0),
            "market":       row.get("market", ""),
        }
    except Exception as e:
        print(f"  ⚠️ fetch stock_basic {code}: {e}")
        return None


def refresh_stock_basic(codes: list[str]):
    """批量刷新 stock_basic。"""
    cache = _load_json(STOCK_BASIC_PATH)
    updated = 0
    for code in codes:
        data = _fetch_stock_basic_one(code)
        if data:
            cache[code] = {**data, "updated": _now_str()}
            updated += 1
            time.sleep(0.2)
    _save_json(STOCK_BASIC_PATH, cache)
    print(f"  ✅ stock_basic 刷新 {updated}/{len(codes)} 只")


# ============================================================
# 4. EPS — 机构一致预期，每月刷
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

    # 回退：返回已有缓存（即使过期）
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


# ============================================================
# 5. 统一刷新入口
# ============================================================

def refresh_all(codes: list[str], force: bool = False):
    """刷新所有缓存，按过期策略决定是否真正拉取。

    force=True 强制全部重拉（忽略 mtime）。
    """
    print(f"🔄 刷新缓存: {len(codes)} 只")

    # daily_basic: 文件整体过期才刷
    if force or _is_stale(DAILY_BASIC_PATH, _TTL_DAILY_BASIC):
        print("  📥 daily_basic (每周)")
        refresh_daily_basic(codes)
    else:
        age_days = (time.time() - DAILY_BASIC_PATH.stat().st_mtime) / 86400
        print(f"  ⏭️  daily_basic 跳过 (上次更新 {age_days:.1f} 天前)")

    # stock_basic: 文件整体过期才刷
    if force or _is_stale(STOCK_BASIC_PATH, _TTL_STOCK_BASIC):
        print("  📥 stock_basic (每月)")
        refresh_stock_basic(codes)
    else:
        age_days = (time.time() - STOCK_BASIC_PATH.stat().st_mtime) / 86400
        print(f"  ⏭️  stock_basic 跳过 (上次更新 {age_days:.1f} 天前)")

    # EPS: 按只判断
    stale_eps = [c for c in codes if force or _is_stale(EPS_DIR / f"{c}.json", _TTL_EPS)]
    if stale_eps:
        print(f"  📥 EPS ({len(stale_eps)} 只需更新)")
        refresh_eps(stale_eps)
    else:
        print(f"  ⏭️  EPS 全部跳过 (未过期)")


# ============================================================
# 6. CLI
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
