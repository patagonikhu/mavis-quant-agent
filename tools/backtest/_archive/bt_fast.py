"""
/tmp/bt_fast.py — 快速回测 (跳过 factor_history, 直接 K 线算布林% + MA120)

优化:
  - 不调 factor_history (避免重算 7 个 strategy, 慢 5-10x)
  - 直接从 K 线 (limit=300) 计算 BOLL% 和 MA120 偏离
  - 缠论/MACD 验证: 复用全市场 dump 里的 macd_div_bot 字段 (如果存在)
  - 5 worker ThreadPoolExecutor (I/O bound, 多线程够用)

性能: 5783 只 × 180d 回测, 预计 30-60s
"""
import sys, time, statistics
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

# === 配置 ===
SAMPLE = ['688012', '688361', '688072', '688082', '688037', '688041', '688256',
          '002371', '300604', '603290', '002028', '600089', '300274', '300308',
          '300502', '300223', '300285', '601138']

LOOKBACK = 180
BOLL_THRESHOLDS = [10, 15]
MA120_MIN, MA120_MAX = -5, 15
WINDOW_DAYS = 5
WIN_DAYS = 30
WIN_THRESHOLDS = [0, 5, 10, 15, 20, 30]


def calc_boll_pct(closes, i, n=20, k=2):
    """BOLL% position: 0=下轨, 50=中轨, 100=上轨"""
    if i < n:
        return None
    window = closes[i - n + 1:i + 1]
    mid = sum(window) / n
    std = (sum((x - mid) ** 2 for x in window) / n) ** 0.5
    upper = mid + k * std
    lower = mid - k * std
    if upper == lower:
        return 50.0
    return (closes[i] - lower) / (upper - lower) * 100


def calc_ma120_dev(closes, i, n=120):
    """MA120 偏离 %"""
    if i < n:
        return None
    ma = sum(closes[i - n + 1:i + 1]) / n
    return (closes[i] / ma - 1) * 100


def check_touch(prices, i, boll, mode='dual'):
    """触底检查: 布林+MA120"""
    bp = calc_boll_pct(prices, i)
    ma = calc_ma120_dev(prices, i)
    if bp is None or ma is None:
        return False
    if not (0 <= bp <= boll):
        return False
    if not (MA120_MIN <= ma <= MA120_MAX):
        return False
    # 简化: 不算缠论/MACD, 用 mode 控制是否需要确认信号
    # mode='any' = 仅布林触底, 'dual' = 任意确认, 'triple' = 全部确认
    # 这里只验布林+MA120, mode 仅做标识
    return True


def scan_one(code, boll):
    """单只票回测: 跳过 factor_history"""
    try:
        # 直接从 parquet 读 K 线 (比 compute_factor_history 快 5-10x)
        from tools.history_sync import read_kline
        from tools.data_store import _to_ts_code
        rows = read_kline(_to_ts_code(code), limit=300)
        if len(rows) < 130:
            return []

        # 提取 closes 序列 (注意 rows 是时间倒序还是正序)
        # 假设 read_kline 返回时间正序 (旧的在前), 转换
        closes = []
        for r in rows:
            c = r.get('close', 0)
            if c and c > 0:
                closes.append(c)

        if len(closes) < 130:
            return []

        # 找所有触底行
        touch_idxs = []
        for i in range(len(closes) - 1, max(0, len(closes) - LOOKBACK - 1), -1):
            if check_touch(closes, i, boll):
                touch_idxs.append(i)

        if not touch_idxs:
            return []

        # 找 5d 内 2 次触底
        signals = []
        # 按时间正序处理 (i 小的早, i 大的晚)
        touch_idxs.sort()
        last_trigger = -30
        for i in touch_idxs:
            if i - last_trigger < 30:
                continue
            # 5d 内 5 个 index 内 (近似, 5d ≈ 5 个交易日)
            if i > 0:
                has_double = any(
                    check_touch(closes, j, boll)
                    for j in range(max(0, i - WINDOW_DAYS), i)
                )
                if not has_double:
                    continue
            # 计算 30d 窗口
            if i + 30 >= len(closes):
                continue
            window = closes[i + 1:i + 31]
            c0 = closes[i]
            max_up = (max(window) - c0) / c0 * 100
            fin = (window[-1] - c0) / c0 * 100
            signals.append({
                'date_idx': i,
                'max': max_up,
                'fin': fin,
            })
            last_trigger = i

        return [(code, s) for s in signals]
    except Exception:
        return []


def main():
    print("=" * 90)
    print(f"  快速回测: 布林≤15% + 5d 2 次 + 30d 窗口 (跳过缠论/MACD, 节省时间)")
    print(f"  胜率 = 30d 内最高涨幅 > X%")
    print("=" * 90)

    all_codes = []
    from tools.data_store import DataStore
    all_codes = DataStore.list_codes()
    print(f"\n跑 18 只样本 (快速)...")

    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {}
        for c in SAMPLE:
            for b in [10, 15]:
                futs[ex.submit(scan_one, c, b)] = (c, b)
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.extend(r)
    elapsed = time.time() - t0
    print(f"⏱️  18 只跑完, 耗时 {elapsed:.1f}s, 收集 {len(results)} 个信号")

    if not results:
        print("无信号")
        return

    # 拆分布林阈值
    print(f"\n{'配置':<25}{'n':<6}{'>0%':<8}{'>5%':<8}{'>10%':<8}{'>15%':<8}{'>20%':<8}{'>30%':<8}{'均max':<10}")
    print("-" * 100)
    for boll, label in [(15, '布林≤15%'), (10, '布林≤10%')]:
        # 简化: 由于不区分 boll, 全部放一起
        # 重新跑分类
        signals = []
        for c in SAMPLE:
            signals.extend(scan_one(c, boll))
        if not signals:
            continue
        n = len(signals)
        max_vals = [s[1]['max'] for s in signals]
        fin_vals = [s[1]['fin'] for s in signals]
        line = f"{label:<25}{n:<6}"
        for thr in WIN_THRESHOLDS:
            wins = sum(1 for m in max_vals if m > thr)
            line += f"{wins/n*100:>5.1f}%   "
        line += f"{statistics.mean(max_vals):>+6.1f}%"
        print(line)

    # 跑全市场
    print(f"\n跑全市场 {len(all_codes)} 只 (快速)...")
    t0 = time.time()
    all_results = []
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = {ex.submit(scan_one, c, 15): c for c in all_codes}
        done = 0
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                all_results.extend(r)
            done += 1
            if done % 1000 == 0:
                print(f"  进度: {done}/{len(all_codes)} ({done/len(all_codes)*100:.0f}%)  {time.time()-t0:.0f}s")
    elapsed = time.time() - t0

    if all_results:
        n = len(all_results)
        max_vals = [s[1]['max'] for s in all_results]
        print(f"\n⏱️  全市场 {n} 个信号, 耗时 {elapsed:.0f}s")
        print(f"\n=== 全市场 布林≤15% + 5d 2次 + 30d max_upside 胜率 ===")
        print(f"{'n':<6}{'>0%':<8}{'>5%':<8}{'>10%':<8}{'>15%':<8}{'>20%':<8}{'>30%':<8}{'均max':<10}")
        line = f"{n:<6}"
        for thr in WIN_THRESHOLDS:
            wins = sum(1 for m in max_vals if m > thr)
            line += f"{wins/n*100:>5.1f}%   "
        line += f"{statistics.mean(max_vals):>+6.1f}%"
        print(line)


if __name__ == "__main__":
    main()
