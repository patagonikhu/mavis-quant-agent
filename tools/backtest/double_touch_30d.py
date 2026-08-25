"""
tools/backtest/double_touch_30d.py — 干净快速回测

设计原则:
  - 简单: 不调 factor_history (重), 只用 K 线 + EMA/MA
  - 过滤: 成交额 < 3 亿 的小票跳过
  - 高速: 不用 ProcessPool (macOS 慢), 用 ThreadPool

回测条件:
  布林 ≤ 15% + MA120 ∈ [-5%, +15%] + 5d 内 2 次触底

胜率: 30d 内 max_upside > 阈值

用法:
  bash tools/with_venv.sh python3 tools/backtest/double_touch_30d.py
"""
import sys, time, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

# === 配置 ===
MIN_AMOUNT_YI = 3.0  # 20d 均成交额 ≥ 3 亿
MA120_MIN, MA120_MAX = -5, 15
WINDOW_DAYS = 5
WIN_DAYS = 30
BOLL = 15  # 布林阈值


def scan_one(code):
    """单只票: 1 次读 K 线 + 滑动窗口计算 (O(1)/step)"""
    try:
        from tools.history_sync import read_kline
        from tools.data_store import _to_ts_code
        rows = read_kline(_to_ts_code(code), limit=300)
        if len(rows) < 130:
            return []

        # 提取 K 线 (正序), 一次性过滤无效行
        valid = [(r['close'], r.get('amount', 0)) for r in rows if r.get('close', 0) > 0]
        if len(valid) < 130:
            return []
        closes   = [v[0] for v in valid]
        amounts  = [v[1] for v in valid]

        # 过滤 1: 成交额 (20d 均 ≥ 3 亿)
        # amount 字段单位 = 千元, /1e5 转亿
        avg_amt = sum(amounts[-20:]) / min(20, len(amounts)) / 1e5
        if avg_amt < MIN_AMOUNT_YI:
            return []

        n = len(closes)

        # 预热滑动窗口到 i=120 的前一刻
        # BOLL 窗口: [i-19..i], 初始化为 closes[100:120] (i=120 时老值=closes[100])
        boll_s  = sum(closes[100:120])
        boll_s2 = sum(x * x for x in closes[100:120])
        # MA120 窗口: [i-119..i], 初始化为 closes[0:120] (i=120 时老值=closes[0])
        ma120_s = sum(closes[:120])

        touch_idxs = []
        for i in range(120, n):
            # --- 滑动更新 BOLL (窗口 [i-19 .. i]) ---
            old_b = closes[i - 20]
            new_b = closes[i]
            boll_s  += new_b - old_b
            boll_s2 += new_b * new_b - old_b * old_b
            mid = boll_s / 20
            var = boll_s2 / 20 - mid * mid
            if var <= 0:
                # 滑动更新 MA120
                ma120_s += closes[i] - closes[i - 120]
                continue
            std = var ** 0.5
            bp = (closes[i] - (mid - 2 * std)) / (4 * std) * 100

            # --- 滑动更新 MA120 (窗口 [i-119 .. i]) ---
            ma120_s += closes[i] - closes[i - 120]
            dev = (closes[i] / (ma120_s / 120) - 1) * 100

            if 0 <= bp <= BOLL and MA120_MIN <= dev <= MA120_MAX:
                touch_idxs.append(i)

        if len(touch_idxs) < 2:
            return []

        # 找 5d 内 2 次触底 + 30d 窗口
        signals = []
        last_trigger = -30
        for i in range(len(touch_idxs) - 1):
            t1, t2 = touch_idxs[i], touch_idxs[i + 1]
            if t2 - t1 > WINDOW_DAYS:
                continue  # 不是 5d 内 2 次
            if t2 - last_trigger < 30:
                continue
            if t2 + 30 >= len(closes):
                break
            window = closes[t2 + 1:t2 + 31]
            c0 = closes[t2]
            max_up = (max(window) - c0) / c0 * 100
            fin = (window[-1] - c0) / c0 * 100
            signals.append({'i': t2, 'max': max_up, 'fin': fin})
            last_trigger = t2
            i += 1  # 跳过 t1 避免重复

        return [(code, s) for s in signals]
    except Exception:
        return []


def run(codes, label, workers=16):
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(scan_one, c): c for c in codes}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.extend(r)
            done += 1
            if done % 1000 == 0 and len(codes) > 1000:
                print(f"  {label}: {done}/{len(codes)} ({done/len(codes)*100:.0f}%)  {time.time()-t0:.0f}s")
    elapsed = time.time() - t0
    return results, elapsed


def main():
    print("=" * 80)
    print(f"  布林≤{BOLL}% + 5d 2次 + 30d max_upside (过滤成交额<{MIN_AMOUNT_YI}亿)")
    print("=" * 80)

    SAMPLE = ['688012', '688361', '688072', '688082', '688037', '688041', '688256',
              '002371', '300604', '603290', '002028', '600089', '300274', '300308',
              '300502', '300223', '300285', '601138']

    # 18 只
    results, elapsed = run(SAMPLE, "18只")
    if results:
        n = len(results)
        max_vals = [s[1]['max'] for s in results]
        fin_vals = [s[1]['fin'] for s in results]
        print(f"\n18只样本: n={n} 耗时{elapsed:.1f}s")
        for thr in [0, 5, 10, 15, 20, 30]:
            w = sum(1 for m in max_vals if m > thr)
            print(f"  >{thr:>3}% 胜率 {w/n*100:5.1f}%")
        print(f"  均max: {statistics.mean(max_vals):+.1f}%")
        print(f"  均fin: {statistics.mean(fin_vals):+.1f}%")

    # 全市场
    from tools.data_store import DataStore
    all_codes = DataStore.list_codes()
    print(f"\n全市场 {len(all_codes)} 只...")
    results, elapsed = run(all_codes, "全市场", workers=32)
    if results:
        n = len(results)
        max_vals = [s[1]['max'] for s in results]
        fin_vals = [s[1]['fin'] for s in results]
        print(f"\n全市场: n={n} 耗时{elapsed:.0f}s")
        for thr in [0, 5, 10, 15, 20, 30]:
            w = sum(1 for m in max_vals if m > thr)
            print(f"  >{thr:>3}% 胜率 {w/n*100:5.1f}%")
        print(f"  均max: {statistics.mean(max_vals):+.1f}%")
        print(f"  均fin: {statistics.mean(fin_vals):+.1f}%")


if __name__ == "__main__":
    main()
