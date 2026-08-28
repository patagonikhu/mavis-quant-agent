"""
tools/backtest/double_touch_30d.py — 干净快速回测

回测条件:
  布林 ≤ 15%（不要求双触底）

胜率: 30d 内 max_upside > 10%

用法:
  bash tools/with_venv.sh python3 tools/backtest/double_touch_30d.py
"""
import sys, time, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

# === 配置 ===
MIN_AMOUNT_YI = 3.0  # 20d 均成交额 ≥ 3 亿
WIN_DAYS = 30
BOLL = 15  # 布林阈值
WIN_THRESHOLD = 10  # 胜率阈值：30d 内最大涨幅 > 10%
COOLDOWN = 30  # 同一只票最少间隔天数，避免重复计数


def scan_one(code):
    """单只票: 优先读 signal_cache（boll_bpct），没有缓存才回退到 K 线计算"""
    try:
        from tools.kline_history_backfill import read_kline
        from tools.kline_store import _to_ts_code
        rows = read_kline(_to_ts_code(code), limit=300)
        if len(rows) < 130:
            return []

        valid = [(r['close'], r.get('amount', 0), r.get('trade_date', '')) for r in rows if r.get('close', 0) > 0]
        if len(valid) < 130:
            return []
        closes  = [v[0] for v in valid]
        amounts = [v[1] for v in valid]
        dates   = [v[2].replace('-','')[:8] for v in valid]

        # 过滤: 成交额 (20d 均 ≥ 3 亿), amount 单位=千元
        avg_amt = sum(amounts[-20:]) / min(20, len(amounts)) / 1e5
        if avg_amt < MIN_AMOUNT_YI:
            return []

        n = len(closes)

        # 尝试从 signal_cache 读 boll_bpct
        bp_map = {}
        try:
            from tools.analysis.signal_cache import _conn
            conn = _conn()
            placeholders = ','.join(['?'] * len(dates))
            rows_db = conn.execute(
                f"SELECT date_str, boll_bpct FROM analysis_cache WHERE code=? AND date_str IN ({placeholders})",
                [code] + dates
            ).fetchall()
            conn.close()
            bp_map = {r[0]: r[1] for r in rows_db if r[1] is not None}
        except Exception:
            pass

        # 预热滑动 BOLL 窗口（cache 缺失时用）
        boll_s  = sum(closes[100:120])
        boll_s2 = sum(x * x for x in closes[100:120])

        signals = []
        last_trigger = -COOLDOWN

        for i in range(120, n):
            d = dates[i]
            if d in bp_map:
                bp = bp_map[d]
            else:
                # 回退：滑动计算 Boll
                old_b = closes[i - 20]
                new_b = closes[i]
                boll_s  += new_b - old_b
                boll_s2 += new_b * new_b - old_b * old_b
                mid = boll_s / 20
                var = boll_s2 / 20 - mid * mid
                if var <= 0:
                    continue
                std = var ** 0.5
                bp = (closes[i] - (mid - 2 * std)) / (4 * std) * 100
            # 更新滑动窗口（即使用了cache，也要保持滑动窗口正确）
            if d not in bp_map:
                pass  # 已在上面更新
            else:
                old_b = closes[i - 20]
                new_b = closes[i]
                boll_s  += new_b - old_b
                boll_s2 += new_b * new_b - old_b * old_b

            if bp is None or bp > BOLL:
                continue
            if i - last_trigger < COOLDOWN:
                continue
            if i + WIN_DAYS >= n:
                break

            window = closes[i + 1:i + WIN_DAYS + 1]
            c0 = closes[i]
            max_up = (max(window) - c0) / c0 * 100
            fin    = (window[-1] - c0) / c0 * 100
            signals.append({'i': i, 'max': max_up, 'fin': fin, 'bp': bp})
            last_trigger = i

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
    return results, time.time() - t0


def print_stats(label, results):
    if not results:
        print(f"{label}: 无信号")
        return
    n = len(results)
    max_vals = [s[1]['max'] for s in results]
    fin_vals = [s[1]['fin'] for s in results]
    w = sum(1 for m in max_vals if m > WIN_THRESHOLD)
    print(f"\n{label}: n={n} 信号")
    print(f"  >{WIN_THRESHOLD}% 胜率: {w/n*100:.1f}%  ({w}/{n})")
    for thr in [5, 10, 15, 20, 30]:
        ww = sum(1 for m in max_vals if m > thr)
        print(f"  >{thr:>2}% 胜率 {ww/n*100:5.1f}%")
    print(f"  均max: {statistics.mean(max_vals):+.1f}%  均fin: {statistics.mean(fin_vals):+.1f}%")


def main():
    print("=" * 60)
    print(f"  布林≤{BOLL}%  30d max_upside>{WIN_THRESHOLD}% 胜率（仅 signal_cache 覆盖的股票）")
    print("=" * 60)

    from tools.analysis.signal_cache import _conn
    from tools.kline_store import DataStore

    # 一次性从 cache 读所有 boll_bpct
    conn = _conn()
    rows = conn.execute(
        "SELECT code, date_str, boll_bpct FROM analysis_cache WHERE boll_bpct IS NOT NULL ORDER BY code, date_str"
    ).fetchall()
    conn.close()

    # 按 code 分组
    from collections import defaultdict
    cache_by_code = defaultdict(list)
    for code, date_str, boll_bpct in rows:
        cache_by_code[code].append((date_str, boll_bpct))

    print(f"cache 覆盖 {len(cache_by_code)} 只股票，共 {len(rows)} 条记录")

    # 读 K 线（只用 close + amount，不算 boll）
    def scan_one_cache(code):
        try:
            from tools.kline_history_backfill import read_kline
            from tools.kline_store import _to_ts_code
            krows = read_kline(_to_ts_code(code), limit=300)
            if len(krows) < 130:
                return []
            valid = [(r['close'], r.get('amount', 0), r.get('trade_date','').replace('-','')[:8])
                     for r in krows if r.get('close', 0) > 0]
            if len(valid) < 130:
                return []
            closes  = [v[0] for v in valid]
            amounts = [v[1] for v in valid]
            dates   = [v[2] for v in valid]

            avg_amt = sum(amounts[-20:]) / min(20, len(amounts)) / 1e5
            if avg_amt < MIN_AMOUNT_YI:
                return []

            n = len(closes)
            date_to_idx = {d: i for i, d in enumerate(dates)}
            bp_map = {d: bp for d, bp in cache_by_code[code]}

            signals = []
            last_trigger = -COOLDOWN

            for d, bp in cache_by_code[code]:
                if bp > BOLL:
                    continue
                i = date_to_idx.get(d)
                if i is None or i < 20:
                    continue
                if i - last_trigger < COOLDOWN:
                    continue
                if i + WIN_DAYS >= n:
                    continue
                window = closes[i + 1:i + WIN_DAYS + 1]
                c0 = closes[i]
                max_up = (max(window) - c0) / c0 * 100
                fin    = (window[-1] - c0) / c0 * 100
                signals.append({'i': i, 'max': max_up, 'fin': fin, 'bp': bp})
                last_trigger = i
            return [(code, s) for s in signals]
        except Exception:
            return []

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = {ex.submit(scan_one_cache, c): c for c in cache_by_code}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.extend(r)
            done += 1
            if done % 500 == 0:
                print(f"  {done}/{len(cache_by_code)}")
    print(f"耗时 {time.time()-t0:.0f}s")
    print_stats("cache覆盖股票（布林≤15%）", results)

    # === 缠论买点胜率 ===
    print("\n" + "=" * 60)
    print("  缠论买点（chan_1buy/2buy/3buy）30d max_upside 胜率")
    print("=" * 60)

    conn2 = _conn()
    bsp_rows = conn2.execute(
        "SELECT code, date_str, chan_1buy, chan_2buy, chan_3buy FROM analysis_cache "
        "WHERE chan_1buy=1 OR chan_2buy=1 OR chan_3buy=1 ORDER BY code, date_str"
    ).fetchall()
    conn2.close()

    from collections import defaultdict
    bsp_by_code = defaultdict(list)
    for code, date_str, b1, b2, b3 in bsp_rows:
        bsp_by_code[code].append((date_str, bool(b1), bool(b2), bool(b3)))

    print(f"触发买点记录: {len(bsp_rows)} 条，涉及 {len(bsp_by_code)} 只")

    def scan_bsp(code):
        try:
            from tools.kline_history_backfill import read_kline
            from tools.kline_store import _to_ts_code
            krows = read_kline(_to_ts_code(code), limit=300)
            if len(krows) < 30:
                return []
            valid = [(r['close'], r.get('trade_date','').replace('-','')[:8])
                     for r in krows if r.get('close', 0) > 0]
            closes = [v[0] for v in valid]
            dates  = [v[1] for v in valid]
            n = len(closes)
            date_to_idx = {d: i for i, d in enumerate(dates)}

            signals = []
            # 只取每段连续买点的第一天（新触发），避免同一段行情重复计入
            prev_b1, prev_b2, prev_b3 = False, False, False
            for date_str, b1, b2, b3 in bsp_by_code[code]:
                new_b1 = b1 and not prev_b1
                new_b2 = b2 and not prev_b2
                new_b3 = b3 and not prev_b3
                prev_b1, prev_b2, prev_b3 = b1, b2, b3

                for bsp_type, is_new in [('1买', new_b1), ('2买', new_b2), ('3买', new_b3)]:
                    if not is_new:
                        continue
                    i = date_to_idx.get(date_str)
                    if i is None or i + WIN_DAYS >= n:
                        continue
                    window = closes[i + 1:i + WIN_DAYS + 1]
                    c0 = closes[i]
                    max_up = (max(window) - c0) / c0 * 100
                    fin    = (window[-1] - c0) / c0 * 100
                    signals.append({'max': max_up, 'fin': fin, 'type': bsp_type})
            return [(code, s) for s in signals]
        except Exception:
            return []

    t1 = time.time()
    bsp_results = []
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs2 = {ex.submit(scan_bsp, c): c for c in bsp_by_code}
        for fut in as_completed(futs2):
            r = fut.result()
            if r:
                bsp_results.extend(r)
    print(f"耗时 {time.time()-t1:.0f}s")

    # 按买点类型分别统计
    for bsp_type in ['1买', '2买', '3买']:
        subset = [s for _, s in bsp_results if s['type'] == bsp_type]
        print_stats(f"缠论{bsp_type}", [(None, s) for s in subset])
    print_stats("缠论买点合计", bsp_results)


if __name__ == "__main__":
    main()
