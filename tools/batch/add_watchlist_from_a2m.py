#!/usr/bin/env python
"""把 /t-wyckoff-a2m 命中票加入 watchlist (自选)

2026-10-28: 用户要求把 A→M 命中票加到 watchlist 再做分析。
用 '自选' 类型 + 'a2m' tag, 不污染持仓/blowout 分类。

⚠️ **一次性脚本** (不挂任何 skill): HITS 是硬编码的, 不会自动更新。
要加新票先改下面的 HITS 列表。

用法:
    bash tools/with_venv.sh python -m tools.batch.add_watchlist_from_a2m [--dry-run]

2026-11-02 改:
  - 原子写 + 写前备份 (原来直接 write_text 覆盖, 中途异常会写出半截 JSON)
  - last_updated 用真实日期 (原来硬编码 "2026-10-28", 每次跑都把日期改回去)
  - 加 --dry-run (默认 dry-run, 要真写加 --no-dry-run)
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import duckdb  # noqa: E402

from tools.storage.safe_fs import atomic_write_json  # noqa: E402

WL = Path("config/watchlist.json")

# /t-wyckoff-a2m 命中 (2026-10-28, 收紧过滤后 5 只) — 一次性快照, 不自动更新
HITS = ["300832", "301101", "603387", "688410", "301109"]


def main():
    ap = argparse.ArgumentParser(description="把 A→M 命中票加入 watchlist (自选)")
    ap.add_argument("--no-dry-run", action="store_true",
                    help="真写文件 (默认 dry-run, 只打印计划)")
    args = ap.parse_args()
    dry_run = not args.no_dry_run

    d = json.loads(WL.read_text(encoding="utf-8"))
    stocks = d["stocks"]
    existing = {s["code"] for s in stocks}

    # 行业从 stock_basic 拿 (单一事实源)
    in_clause = ",".join(f"'{c}.SZ','{c}.SH','{c}.BJ'" for c in HITS)
    df = duckdb.execute(f"""
        SELECT ts_code, name, industry FROM read_parquet('data/history/stock_basic/stock_basic.parquet')
        WHERE ts_code IN ({in_clause})
    """).fetchdf()
    info = {r["ts_code"].split(".")[0]: {"name": r["name"], "sector": r["industry"]} for _, r in df.iterrows()}

    added, skipped = [], []
    for code in HITS:
        if code in existing:
            skipped.append(code)
            continue
        meta = info.get(code, {"name": code, "sector": ""})
        stocks.append({
            "code": code,
            "name": meta["name"],
            "sector": meta["sector"],
            "list_type": "自选",
            "tags": ["a2m", "wyckoff-a2m"],
        })
        added.append(code)

    # 2026-11-02 修: 用真实日期 (原来硬编码 "2026-10-28", 每次跑都把日期改回去)
    d["last_updated"] = datetime.date.today().isoformat()

    print(f"📋 计划新增 {len(added)} 只: {', '.join(added) or '(无)'}")
    if skipped:
        print(f"⏭ 已存在跳过: {', '.join(skipped)}")
    print(f"   watchlist 总数: {len(stocks)}")

    if dry_run:
        print("\n🔍 DRY-RUN: 没写文件 (加 --no-dry-run 才真写)")
        return

    if not atomic_write_json(WL, d):
        print("\n❌ watchlist.json 写入失败 (原文件未改动)")
        return
    print("\n✅ watchlist.json 已写 (原子写 + 备份)")


if __name__ == "__main__":
    main()
