"""backfill_obv.py — backfill OBV columns in analysis_cache.db

算法: O(n) per stock
  1. OBV 数组: 一次遍历
  2. 4 个 15 天滑窗的 OBV 净增量: 用前缀和 O(1) 查
  3. 段背离: 对每根 K 线, 直接看 4 个窗口的 (价变化, OBV 净增)

用法:
  bash tools/with_venv.sh python3 tools/batch/backfill_obv.py
"""
import sys, time, sqlite3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kline_store import DataStore

DB = ROOT / "data" / "analysis_cache.db"
ds = DataStore()

WIN = 15  # 段背离窗口大小


def compute_obv_per_date(closes, vols, win=WIN, lookback=60):
    """O(n): 算 OBV 数组 + 4 个 15d 窗口的 60d 段背离次数
    段背离: 60d 内不重叠 4 个 15d 窗口
    """
    n = len(closes)
    if n < win * 2:
        return [0.0] * n, [0] * n, [0] * n

    # 1) OBV 数组 (前缀和)
    obv_arr = [0.0] * n
    for i in range(1, n):
        if closes[i] > closes[i-1]:   obv_arr[i] = obv_arr[i-1] + vols[i]
        elif closes[i] < closes[i-1]: obv_arr[i] = obv_arr[i-1] - vols[i]
        else:                          obv_arr[i] = obv_arr[i-1]

    # 2) 对每根 K 线 i 算 [end-window, end] 窗口的 OBV 净增量
    #    obv_net[end] = obv_arr[end-1] - obv_arr[start-1]  (从 start 到 end-1 的累计变化)
    #    vol_sum_window[end] = sum(vols[start+1 : end+1])  (窗口内的成交量, 排除起点)
    # 用前缀和 O(1) 查
    obv_prefix = [0.0] * (n + 1)
    vol_prefix = [0.0] * (n + 1)
    for i in range(n):
        obv_prefix[i+1] = obv_prefix[i] + (obv_arr[i] - obv_arr[i-1] if i > 0 else 0)
        vol_prefix[i+1] = vol_prefix[i] + vols[i]

    # 3) 段背离
    div_bot_arr = [0] * n
    div_top_arr = [0] * n
    th_p = -0.02  # 价跌 2%
    th_o = 0.03   # OBV 净增 3%
    for i in range(win * 2 - 1, n):  # 至少 win*2 根
        count_bot = 0
        count_top = 0
        # 4 个 15d 窗口: 末/15/30/45/60 日前
        for w in range(4):
            end = i - w * win
            start = end - win
            if start < 0: break
            # 窗口 [start, end]
            p_chg = closes[end] / closes[start] - 1
            # 窗口内 OBV 净增量 (从 start+1 到 end 的 vol 涨跌累计)
            net = 0
            for j in range(start + 1, end + 1):
                if closes[j] > closes[j-1]:   net += vols[j]
                elif closes[j] < closes[j-1]: net -= vols[j]
            # 窗口内总成交 (排除起点日)
            tv = sum(vols[start+1:end+1]) if end + 1 > start + 1 else 0
            o_pct = net / tv * 100 if tv > 0 else 0
            if p_chg < th_p and o_pct > th_o:
                count_bot += 1
            elif p_chg > -th_p and o_pct < -th_o:
                count_top += 1
        div_bot_arr[i] = count_bot
        div_top_arr[i] = count_top

    return obv_arr, div_bot_arr, div_top_arr


def backfill_one(code: str) -> int:
    try:
        ctx = ds.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 30:
            return 0
        closes = [k.get('close', 0) for k in ctx.kline]
        vols = [k.get('volume', 0) for k in ctx.kline]
        dates = [k.get('trade_date', '').replace('-', '')[:8] for k in ctx.kline]
        n = len(closes)

        obv_arr, div_bot_arr, div_top_arr = compute_obv_per_date(closes, vols)

        # verdict (基于最终 obv_factor 状态)
        from tools.factors.volume.price_fflow import obv_factor
        res = obv_factor(closes=closes, vols=vols, dates=dates)
        verdict = res.get('verdict', '')

        # 批量 UPDATE
        conn = sqlite3.connect(str(DB))
        cur = conn.cursor()
        updates = list(zip(
            obv_arr, div_bot_arr, div_top_arr,
            [verdict] * n,
            [code] * n, dates
        ))
        cur.executemany(
            "UPDATE analysis_cache SET obv=?, obv_div_bot=?, obv_div_top=?, obv_verdict=? "
            "WHERE code=? AND date_str=?",
            updates
        )
        conn.commit()
        conn.close()
        return n
    except Exception as e:
        return 0


def main():
    conn = sqlite3.connect(str(DB))
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM analysis_cache").fetchall()]
    conn.close()
    print(f"=== Backfill OBV: {len(codes)} 只 ===")
    t0 = time.time()
    total = 0
    done = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(backfill_one, code): code for code in codes}
        for fut in as_completed(futs):
            n = fut.result()
            total += n
            done += 1
            if done % 200 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed
                eta = (len(codes) - done) / rate
                print(f"  [{done}/{len(codes)}] updated={total:,} | {elapsed:.0f}s, {rate:.0f}只/s, ETA={eta:.0f}s", flush=True)
    print(f"\n=== 完成 ({(time.time()-t0):.0f}s) ===")
    print(f"updated: {total:,} rows")


if __name__ == "__main__":
    main()
