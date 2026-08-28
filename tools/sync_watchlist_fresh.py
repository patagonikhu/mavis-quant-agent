#!/usr/bin/env python3
"""
tools/sync_watchlist_fresh.py — 智能判断并刷新 JSON 数据 (2026-07-22)

设计目标:
  - skill 跑前调它, 自动决定哪些票需要 dump
  - 已存在且 < 1 小时 → 跳过
  - 不存在 或 > 1 小时 → dump
  - 静默执行, 不需要用户介入

用法:
  python3 tools/sync_watchlist_fresh.py 300274                    # 单只
  python3 tools/sync_watchlist_fresh.py 300274 000725 002273      # 多只
  python3 tools/sync_watchlist_fresh.py --sector 半导体设备         # 板块成分股
  python3 tools/sync_watchlist_fresh.py --watchlist                # 70 只
  python3 tools/sync_watchlist_fresh.py --max-age 1800 300274     # 自定义过期阈值 (秒)
  python3 tools/sync_watchlist_fresh.py --force 300274            # 强制刷, 不看 age
"""
import sys
import os

# 自动检查依赖 (2026-07-24 固化, 解决 "No module named tushare" 反复忘装问题)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from check_deps import ensure as _ensure_deps
    _ensure_deps(verbose=False)
except Exception:
    pass
import json
import time
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "data" / "watchlist.json"
SECTORS = ROOT / "data" / "sectors.json"


def dump_one(code: str, force: bool = False, max_age: int = 3600) -> bool:
    """
    单只 sync.
    Return True if synced (or fresh), False if failed.
    """
    if not force:
        try:
            from tools.kline_store import DataStore
            from datetime import datetime
            ctx = DataStore.get_ctx(code)
            if ctx.kline:
                last_date = ctx.kline[-1].get("trade_date", "")
                if last_date:
                    as_of_dt = datetime.strptime(last_date, "%Y%m%d")
                    age = (datetime.now() - as_of_dt).total_seconds()
                    if age < max_age:
                        print(f"  ⏩ {code} (age {age:.0f}s, 新鲜, 跳过)")
                        return True
                    else:
                        print(f"  🔄 {code} (age {age:.0f}s, 过期, 刷新中...)")
        except Exception as e:
            print(f"  ⚠️  {code} (读取失败: {e}, 强制刷)")
    else:
        print(f"  🔄 {code} (强制刷)")

    # 实际 sync
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "tools.sync_stock", code],
        capture_output=True, text=True, env=env
    )
    if result.returncode == 0:
        return True
    else:
        print(f"     ❌ sync 失败: {result.stderr.strip()[:200]}")
        return False


def get_sector_codes(sector_name: str) -> list:
    """从 sectors.json 找板块成分股 codes"""
    if not SECTORS.exists():
        return []
    with open(SECTORS) as f:
        sectors = json.load(f)
    # 板块名匹配 (模糊)
    for k, v in sectors.items():
        if sector_name in k or k in sector_name:
            if isinstance(v, dict):
                return v.get("codes", v.get("stocks", []))
            elif isinstance(v, list):
                return v
    return []


def get_watchlist_codes() -> list:
    """从 watchlist.json 读全部 code"""
    if not WATCHLIST.exists():
        return []
    with open(WATCHLIST) as f:
        wl = json.load(f)
    return [s["code"] for s in wl.get("stocks", [])]


def main():
    parser = argparse.ArgumentParser(description="智能刷新 JSON 数据 (skill 前置)")
    parser.add_argument("codes", nargs="*", help="股票代码列表")
    parser.add_argument("--sector", help="板块名, 自动找成分股")
    parser.add_argument("--watchlist", action="store_true", help="刷新全部 watchlist")
    parser.add_argument("--force", action="store_true", help="强制刷新, 不看 age")
    parser.add_argument("--max-age", type=int, default=3600, help="过期阈值 (秒), 默认 1 小时")
    parser.add_argument("--quiet", action="store_true", help="静默, 不打印每只")
    args = parser.parse_args()

    if args.sector:
        codes = get_sector_codes(args.sector)
        if not codes:
            print(f"❌ 板块 '{args.sector}' 没找到, 看 data/sectors.json")
            sys.exit(1)
        print(f"🔍 板块 '{args.sector}' 成分股: {len(codes)} 只")
    elif args.watchlist:
        codes = get_watchlist_codes()
        print(f"🔍 watchlist 全部: {len(codes)} 只")
    else:
        codes = args.codes
        if not codes:
            print("❌ 用法: ensure_fresh.py 300274 | --sector 半导体设备 | --watchlist")
            sys.exit(1)

    # 并发 dump (4 个进程, Tushare 全接口 80/分 + 单接口 100/分 双约束下安全)
    from concurrent.futures import ThreadPoolExecutor, as_completed
    print(f"⏱️  并发 4 进程 dump {len(codes)} 只, 最大 age {args.max_age}s")
    start = time.time()
    ok = 0; fail = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(dump_one, c, args.force, args.max_age): c for c in codes}
        for f in as_completed(futures):
            if f.result():
                ok += 1
            else:
                fail += 1
    elapsed = time.time() - start
    print(f"")
    print(f"✅ 完成: {ok} 成功 / {fail} 失败, 耗时 {elapsed:.1f} 秒")
    sys.exit(0 if fail == 0 else 1)


if __name__ == "__main__":
    main()
