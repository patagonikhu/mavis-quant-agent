"""
/tmp/bt_v3.py — 极简快速回测

每只票: 1 次读 K 线, 1 次算 factor_history
"""
import sys, time, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

SAMPLE = ['688012', '688361', '688072', '688082', '688037', '688041', '688256',
          '002371', '300604', '603290', '002028', '600089', '300274', '300308',
          '300502', '300223', '300285', '601138']

MA120_MIN, MA120_MAX = -5, 15
WINDOW_DAYS = 5
WIN_DAYS = 30


def scan_one(args):
    code, boll = args
    try:
        from tools.history_sync import read_kline
        from tools.data_store import _to_ts_code
        rows = read_kline(_to_ts_code(code), limit=300)
        closes = [r['close'] for r in rows if r.get('close', 0) > 0]
        if len(closes) < 130:
            return []

        # 阶段 1: K 线快速算 BOLL+MA120, 找触底
        touchs = []
        for i in range(120, len(closes)):
            win = closes[i - 19:i + 1]
            mid = sum(win) / 20
            std = (sum((x - mid) ** 2 for x in win) / 20) ** 0.5
            if std <= 0:
                continue
            bp = (closes[i] - (mid - 2 * std)) / (4 * std) * 100
            ma120 = sum(closes[i - 119:i + 1]) / 120
            dev = (closes[i] / ma120 - 1) * 100
            if 0 <= bp <= boll and MA120_MIN <= dev <= MA120_MAX:
                touchs.append(i)
        if not touchs:
            return []

        # 阶段 2: 算缠论+MACD (只算 1 次)
        from tools.data_store import DataStore
        from tools.analysis.factor_history import compute_factor_history
        from tools.analysis.analysis_engine import ChanStrategy, MacdDivergenceStrategy
        ctx = DataStore.get_ctx(code, kline_only=True, limit=300)
        fh_rows = compute_factor_history(ctx, step=1, lookback=180,
                                          strategies=[ChanStrategy, MacdDivergenceStrategy])
        if not fh_rows or len(fh_rows) < 130:
            return []

        # 算每行 daily_beichi 状态 (缓存, 避免每次重算)
        beichi_bot_cache = [False] * len(fh_rows)
        macd_bot_cache = [False] * len(fh_rows)

        for i in range(len(fh_rows)):
            # 缠论 30d 内
            start = max(0, i - 30)
            for r in fh_rows[start:i + 1]:
                bc = r.get("daily_beichi") or {}
                if isinstance(bc, dict):
                    if bc.get("direction") == "bot" and bc.get("strength") in ("strong", "weak"):
                        beichi_bot_cache[i] = True
                        break
            # MACD 5d 内
            start = max(0, i - 5)
            macd_bot_cache[i] = any(r.get("macd_div_bot") for r in fh_rows[start:i + 1])

        # 阶段 3: 找 5d 2 次触底, 验证信号, 算 30d 窗口
        signals = []
        last_trigger = -30
        # 排序触底行 (按时间)
        touchs.sort()
        # 找 5d 内 2 次触底的组 (用 sliding window)
        i = 0
        while i < len(touchs):
            t1 = touchs[i]
            if t1 - last_trigger < 30:
                i += 1
                continue
            # 5d 内 5 个内 (window=5)
            if i + 1 < len(touchs) and touchs[i + 1] - t1 <= WINDOW_DAYS:
                # 第二次触底在 5d 内, 用第二次
                t2 = touchs[i + 1]
                # 跳过中间所有触底 (30d 去重)
                if t2 - last_trigger < 30:
                    i += 1
                    continue
                fh_i = t2  # 假设对齐
                if fh_i >= len(fh_rows):
                    break
                if not beichi_bot_cache[fh_i] and not macd_bot_cache[fh_i]:
                    i += 1
                    continue
                if t2 + 30 >= len(closes):
                    break
                window = closes[t2 + 1:t2 + 31]
                c0 = closes[t2]
                max_up = (max(window) - c0) / c0 * 100
                fin = (window[-1] - c0) / c0 * 100
                signals.append({'i': t2, 'max': max_up, 'fin': fin})
                last_trigger = t2
                i += 2  # 跳过已用
            else:
                i += 1

        return [(code, s) for s in signals]
    except Exception:
        return []


def main():
    print("=" * 90)
    print(f"  极简快速回测 (1 票 1 次 factor_history, ThreadPool)")
    print("=" * 90)

    from tools.data_store import DataStore
    all_codes = DataStore.list_codes()

    # 4 个配置: 布林 × {1次触底, 5d 2次}
    # 简化: 1次 vs 2次区别在 scan_one 内部, 跑 2 次就好
    for boll in [15]:
        print(f"\n--- 布林≤{boll}% + 5d 2 次触底 (双信号) ---")
        # 18 只
        t0 = time.time()
        results = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(scan_one, (c, boll)): c for c in SAMPLE}
            for fut in as_completed(futs):
                r = fut.result()
                if r: results.extend(r)
        elapsed = time.time() - t0
        if results:
            n = len(results)
            max_vals = [s[1]['max'] for s in results]
            print(f"  18 只: n={n} 耗时 {elapsed:.0f}s")
            for thr in [0, 5, 10, 15, 20, 30]:
                wins = sum(1 for m in max_vals if m > thr)
                print(f"    >{thr}%: {wins/n*100:.1f}%")
            print(f"    均max: {statistics.mean(max_vals):+.1f}%")
            print(f"    均fin: {statistics.mean([s[1]['fin'] for s in results]):+.1f}%")

        # 全市场
        t0 = time.time()
        all_results = []
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = {ex.submit(scan_one, (c, boll)): c for c in all_codes}
            done = 0
            for fut in as_completed(futs):
                r = fut.result()
                if r: all_results.extend(r)
                done += 1
                if done % 1000 == 0:
                    print(f"  全市场: {done}/{len(all_codes)} ({done/len(all_codes)*100:.0f}%)  {time.time()-t0:.0f}s")
        elapsed = time.time() - t0
        if all_results:
            n = len(all_results)
            max_vals = [s[1]['max'] for s in all_results]
            print(f"\n  全市场: n={n} 耗时 {elapsed:.0f}s")
            for thr in [0, 5, 10, 15, 20, 30]:
                wins = sum(1 for m in max_vals if m > thr)
                print(f"    >{thr}%: {wins/n*100:.1f}%")
            print(f"    均max: {statistics.mean(max_vals):+.1f}%")
            print(f"    均fin: {statistics.mean([s[1]['fin'] for s in all_results]):+.1f}%")


if __name__ == "__main__":
    main()
