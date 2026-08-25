"""
/tmp/bt_dual_30d.py — 双信号 + 5d 2次 + 30d 窗口回测

条件:
  1. 布林% ≤ 15%
  2. MA120 偏离 ∈ [-5%, +15%]
  3. 双信号 (✅✅⬜): 缠论 OR MACD 底背驰 至少一个
  4. 5d 内 2 次触底

胜率: 30d 窗口内 max_upside > X%

跑 18 只 + 全市场
"""

import sys
import time
import statistics
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

ROOT = Path('/Users/I514959/workspace/mavis-quant-agent')
sys.path.insert(0, str(ROOT))

from tools.data_store import DataStore
from tools.analysis.factor_history import compute_factor_history
from tools.analysis.analysis_engine import ChanStrategy, MacdDivergenceStrategy

SAMPLE_CODES = [
    '688012', '688361', '688072', '688082', '688037',
    '688041', '688256', '002371', '300604', '603290',
    '002028', '600089', '300274', '300308', '300502',
    '300223', '300285', '601138',
]

LOOKBACK_DAYS = 180
BOLL_THRESHOLDS = [10, 15]
MA120_MIN = -5
MA120_MAX = 15
WINDOW_DAYS = 5
WIN_DAYS = 30  # 30 天窗口
WIN_THRESHOLDS = [0, 5, 10, 15, 20, 30]


def has_chan_bot(rows, trigger_idx, lookback=30):
    start = max(0, trigger_idx - lookback)
    for r in rows[start:trigger_idx + 1]:
        bc = r.get("daily_beichi") or {}
        if isinstance(bc, dict):
            if bc.get("direction") == "bot" and bc.get("strength") in ("strong", "weak"):
                return True
        elif isinstance(bc, str) and "底背" in bc:
            return True
    return False


def has_macd_bot(rows, trigger_idx, lookback=5):
    start = max(0, trigger_idx - lookback)
    return any(r.get("macd_div_bot") for r in rows[start:trigger_idx + 1])


def get_closes_dict(ctx):
    return {k['trade_date']: k['close'] for k in ctx.kline}


def future_window_metrics(closes_dict, sorted_dates, trigger_date, win_days):
    """返回 30d 窗口内 max_upside 和 final_return"""
    if trigger_date not in closes_dict:
        return None
    try:
        idx = sorted_dates.index(trigger_date)
    except ValueError:
        return None
    if idx + win_days >= len(sorted_dates):
        return None
    trigger_close = closes_dict[trigger_date]
    window_closes = [closes_dict[sorted_dates[i]] for i in range(idx + 1, idx + win_days + 1)]
    if not window_closes:
        return None
    return {
        'max_upside': (max(window_closes) - trigger_close) / trigger_close * 100,
        'final_return': (window_closes[-1] - trigger_close) / trigger_close * 100,
        'max_close_date': sorted_dates[idx + 1 + window_closes.index(max(window_closes))],
    }


def check_dual_touch(rows, i, boll_threshold):
    """检查双信号触底: 布林+MA120+缠论/MACD 至少一个"""
    r = rows[i]
    bpct = r.get('boll_pct')
    ma120 = r.get('ma120_dev')
    if bpct is None or ma120 is None:
        return False
    if not (0 <= bpct <= boll_threshold):
        return False
    if not (MA120_MIN <= ma120 <= MA120_MAX):
        return False
    has_c = has_chan_bot(rows, i)
    has_m = has_macd_bot(rows, i)
    return has_c or has_m  # 双信号: 至少一个


def scan_one(args):
    code, boll_threshold = args
    try:
        ctx = DataStore.get_ctx(code, kline_only=True, limit=300)
        if len(ctx.kline) < 60:
            return []

        closes_dict = get_closes_dict(ctx)
        sorted_dates = sorted(closes_dict.keys())
        rows = compute_factor_history(ctx, step=1, lookback=LOOKBACK_DAYS,
                                      strategies=[ChanStrategy, MacdDivergenceStrategy])
        if not rows or len(rows) < 30:
            return []

        signals = []
        last_trigger_idx = -30  # 30d 去重

        for i, r in enumerate(rows):
            if i - last_trigger_idx < 30:
                continue

            if not check_dual_touch(rows, i, boll_threshold):
                continue

            # 检查 5d 内是否有另一次双信号触底
            has_double = any(
                check_dual_touch(rows, j, boll_threshold)
                for j in range(max(0, i - WINDOW_DAYS), i)
            )
            if not has_double:
                continue

            trigger_date = r['date']
            if trigger_date not in closes_dict:
                continue

            metrics = future_window_metrics(closes_dict, sorted_dates, trigger_date, WIN_DAYS)
            if metrics is None:
                continue

            signals.append({
                'code': code,
                'trigger_date': trigger_date,
                'max_upside': metrics['max_upside'],
                'final_return': metrics['final_return'],
                'max_at': metrics['max_close_date'],
            })
            last_trigger_idx = i

        return signals
    except Exception:
        return []


def stats_max(signals, key, win_thr, min_n=3):
    valid = [s[key] for s in signals if s.get(key) is not None]
    if len(valid) < min_n:
        return None
    wins = sum(1 for r in valid if r > win_thr)
    return {
        'n': len(valid),
        'win_rate': wins / len(valid) * 100,
        'avg': statistics.mean(valid),
        'median': statistics.median(valid),
        'max': max(valid),
        'min': min(valid),
        'wins': wins,
    }


def run(codes, label, boll):
    t0 = time.time()
    tasks = [(c, boll) for c in codes]
    results = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(scan_one, t): t for t in tasks}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.extend(r)
    elapsed = time.time() - t0

    if not results:
        return None

    out = {'label': label, 'boll': boll, 'n': len(results), 'elapsed': elapsed}

    for win_thr in WIN_THRESHOLDS:
        s = stats_max(results, 'max_upside', win_thr, min_n=3)
        out[f'win_{win_thr}'] = s

    # final_return 统计
    finals = [s['final_return'] for s in results]
    out['final_avg'] = statistics.mean(finals) if finals else 0
    out['final_median'] = statistics.median(finals) if finals else 0
    out['max_avg'] = statistics.mean([s['max_upside'] for s in results]) if results else 0

    return out, results


def main():
    print("=" * 90)
    print(f"  条件: 布林≤15% + 双信号 (缠论 OR MACD) + 5d 内 2 次触底")
    print(f"  窗口: 30d 内最高涨幅 (max_upside)")
    print(f"  胜率阈值: >0% / >5% / >10% / >15% / >20% / >30%")
    print("=" * 90)

    all_codes = DataStore.list_codes()

    print(f"\n跑 4 个配置 (18 只 + 全市场 × 布林 10/15%)...")
    all_results = {}
    for label, codes in [('18 只', SAMPLE_CODES), ('全市场', all_codes)]:
        for boll in [10, 15]:
            r = run(codes, label, boll)
            if r:
                out, results = r
                all_results[(label, boll)] = (out, results)
                print(f"  ✅ {label} 布林≤{boll}%: n={out['n']} ({out['elapsed']:.0f}s)")

    # 输出对比表
    print()
    print("=" * 90)
    print(f"  30d 窗口 max_upside 胜率")
    print("=" * 90)
    print(f"{'配置':<30}{'n':<8}{'>0%':<10}{'>5%':<10}{'>10%':<10}{'>15%':<10}{'>20%':<10}{'>30%':<10}{'均max':<10}{'均final':<10}")
    print("-" * 110)
    for (label, boll), (out, _) in all_results.items():
        cfg = f"{label} 布林≤{boll}%"
        s = [out.get(f'win_{w}') for w in WIN_THRESHOLDS]
        line = f"{cfg:<30}{out['n']:<8}"
        for st in s:
            if st:
                line += f"{st['win_rate']:>6.1f}%   "
            else:
                line += "  ?       "
        line += f"{out['max_avg']:>+6.1f}%   {out['final_avg']:>+6.1f}%"
        print(line)

    # 信号样本展示
    print()
    print("=" * 90)
    print(f"  18 只样本 (布林≤15%) 信号列表 (前 20 个)")
    print("=" * 90)
    if (18, 15) in all_results:
        _, results = all_results[(18, 15)]
        for r in results[:20]:
            print(f"  {r['trigger_date']} {r['code']}: max_upside={r['max_upside']:+.1f}% (最高@{r['max_at']}) final={r['final_return']:+.1f}%")

    # 最佳配置
    print()
    print("=" * 90)
    print(f"  最佳配置 (按 >10% 胜率, 30d max_upside)")
    print("=" * 90)
    best = []
    for (label, boll), (out, _) in all_results.items():
        s = out.get('win_10')
        if s and s['n'] >= 5:
            best.append((s['win_rate'], label, boll, s, out))
    best.sort(reverse=True)
    for rate, label, boll, s, out in best:
        print(f"  {label} 布林≤{boll}%: n={s['n']} 胜率 {s['win_rate']:5.1f}%  均max {s['avg']:+.1f}%  均final {out['final_avg']:+.1f}%")


if __name__ == "__main__":
    main()
