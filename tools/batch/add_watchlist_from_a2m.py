#!/usr/bin/env python
"""把 /t-wyckoff-a2m 命中票加入 watchlist (自选)

2026-10-28: 用户要求把 5 只 A→M 命中票加到 watchlist 再做分析。
用 '自选' 类型 + 'a2m' tag, 不污染持仓/blowout 分类。

用法:
    bash tools/with_venv.sh python -m tools.batch.add_watchlist_from_a2m
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import duckdb  # noqa: E402

WL = Path("config/watchlist.json")

# /t-wyckoff-a2m 命中 (2026-10-28, 收紧过滤后 5 只)
HITS = ["300832", "301101", "603387", "688410", "301109"]


def main():
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

    d["last_updated"] = "2026-10-28"
    WL.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"✅ 新增 {len(added)} 只: {', '.join(added)}")
    if skipped:
        print(f"⏭ 已存在跳过: {', '.join(skipped)}")
    print(f"   watchlist 总数: {len(stocks)}")


if __name__ == "__main__":
    main()
