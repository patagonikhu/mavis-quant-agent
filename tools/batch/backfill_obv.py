"""backfill_obv.py — backfill OBV columns in analysis_cache.db

算法: O(n) per stock
  1. OBV 数组: 一次遍历 (累加 vol 当 close 涨, 减 vol 当 close 跌)
  2. obv5: 5 日价跌 + OBV 涨 (实战吸筹信号)
  3. obv_trend: OBV > MA20 (资金净流入)

用法:
  bash tools/with_venv.sh python3 tools/batch/backfill_obv.py
  bash tools/with_venv.sh python3 tools/batch/backfill_obv.py --year 2024
"""
import sys
import time
import sqlite3
import argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kline_store import DataStore
from tools.factors.kline_arrays import sliding_ma

DB = ROOT / "data" / "analysis_cache.db"
ds = DataStore()


def compute_obv_signals(closes, vols):
    """O(n): 算 OBV 数组 + obv5 + obv_trend

    Returns:
        (obv_arr, obv5_arr, obv_trend_arr) 三个等长 list
    """
    n = len(closes)
    obv_arr = [0.0] * n
    for i in range(1, n):
        if closes[i] > closes[i-1]:
            obv_arr[i] = obv_arr[i-1] + vols[i]
        elif closes[i] < closes[i-1]:
            obv_arr[i] = obv_arr[i-1] - vols[i]
        else:
            obv_arr[i] = obv_arr[i-1]

    # obv5: 5 日价跌 + OBV 涨
    obv5_arr = [0] * n
    for i in range(5, n):
        if closes[i] < closes[i-5] and obv_arr[i] > obv_arr[i-5]:
            obv5_arr[i] = 1

    # obv_trend: OBV > MA20
    obv_ma20 = sliding_ma(obv_arr, 20)
    obv_trend_arr = [0] * n
    for i in range(n):
        if obv_ma20[i] and obv_arr[i] > obv_ma20[i]:
            obv_trend_arr[i] = 1

    return obv_arr, obv5_arr, obv_trend_arr


def backfill_one(code: str, year_filter: str = None) -> int:
    try:
        ctx = ds.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 30:
            return 0
        closes = [k.get('close', 0) for k in ctx.kline]
        vols = [k.get('volume', 0) for k in ctx.kline]
        dates = [k.get('trade_date', '').replace('-', '')[:8] for k in ctx.kline]

        obv_arr, obv5_arr, obv_trend_arr = compute_obv_signals(closes, vols)

        if year_filter:
            updates = [
                (obv_arr[i], obv5_arr[i], obv_trend_arr[i], code, dates[i])
                for i in range(len(dates)) if dates[i].startswith(year_filter)
            ]
        else:
            updates = list(zip(obv_arr, obv5_arr, obv_trend_arr, [code] * len(dates), dates))

        if not updates:
            return 0

        conn = sqlite3.connect(str(DB))
        cur = conn.cursor()
        cur.executemany(
            "UPDATE analysis_cache SET obv=?, obv5=?, obv_trend=? "
            "WHERE code=? AND date_str=?",
            updates,
        )
        conn.commit()
        conn.close()
        return len(updates)
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", default=None, help="按年过滤, 例 2024")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    codes = ds.list_codes()
    print(f"开始 backfill: {len(codes)} 只, year={args.year or 'all'}")

    total = 0
    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(backfill_one, code, args.year): code for code in codes}
        for fut in as_completed(futs):
            done += 1
            total += fut.result()
            if done % 200 == 0:
                print(f"  [{done}/{len(codes)}] 累计 {total} 行")

    print(f"完成: {total} 行, 耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
