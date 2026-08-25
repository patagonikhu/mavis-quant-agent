"""
/tmp/bt_full.py — /t-am-divergence 全市场 5000 只回测 (A 选项)

设计:
  - 5000 只全 A 股
  - lookback 60d (5d 触底 + 30d 缠论 + 5d MACD + 余量)
  - 8 worker 并行 (multiprocessing)
  - 输出回测最优参数 (布林≤15% + ✅✅⬜ + 20d) 胜率
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

# === 回测最优参数 ===
BOLL_THRESHOLDS = [10, 15, 20]
MA120_MIN = -5
MA120_MAX = 15
LOOKBACK_DAYS = 60  # 5d 触底 + 30d 缠论 + 5d MACD + 余量
HOLD_DAYS = [5, 10, 20]
MIN_GAP_DAYS = 30
MIN_SAMPLES_FOR_STATS = 10  # 至少 10 个样本才统计


def get_closes_dict(ctx):
    return {k['trade_date']: k['close'] for k in ctx.kline}


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


def future_ret(closes_dict, sorted_dates, trigger_date, hold):
    if trigger_date not in closes_dict:
        return None
    try:
        idx = sorted_dates.index(trigger_date)
    except ValueError:
        return None
    if idx + hold >= len(sorted_dates):
        return None
    return (closes_dict[sorted_dates[idx + hold]] - closes_dict[trigger_date]) / closes_dict[trigger_date] * 100


def scan_one(args):
    """ProcessPool worker: 单只票回测"""
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
        last_trigger = -MIN_GAP_DAYS - 1

        for i, r in enumerate(rows):
            if i - last_trigger < MIN_GAP_DAYS:
                continue
            bpct = r.get('boll_pct')
            ma120 = r.get('ma120_dev')
            if bpct is None or ma120 is None:
                continue
            if not (0 <= bpct <= boll_threshold):
                continue
            if not (MA120_MIN <= ma120 <= MA120_MAX):
                continue

            trigger_date = r['date']
            if trigger_date not in closes_dict:
                continue

            has_c = has_chan_bot(rows, i)
            has_m = has_macd_bot(rows, i)
            if has_c and has_m:
                triple = '✅✅✅'
            elif has_c or has_m:
                triple = '✅✅⬜'
            else:
                triple = '✅⬜⬜'

            rets = {f'ret_{hd}d': future_ret(closes_dict, sorted_dates, trigger_date, hd) for hd in HOLD_DAYS}
            if rets['ret_5d'] is None:
                continue

            signals.append({
                'code': code,
                'trigger_date': trigger_date,
                'triple': triple,
                **rets,
            })
            last_trigger = i
        return signals
    except Exception as e:
        return []


def stats(signals, key, min_n=MIN_SAMPLES_FOR_STATS):
    valid = [s[key] for s in signals if s.get(key) is not None]
    if len(valid) < min_n:
        return None
    wins = sum(1 for r in valid if r > 0)
    return {
        'n': len(valid),
        'win_rate': wins / len(valid) * 100,
        'avg': statistics.mean(valid),
        'median': statistics.median(valid),
        'max': max(valid),
        'min': min(valid),
        'stdev': statistics.stdev(valid) if len(valid) > 1 else 0,
    }


def main():
    t0 = time.time()
    codes = DataStore.list_codes()
    print(f"📊 全市场 {len(codes)} 只, 8 worker 并行回测")
    print(f"   参数: 布林≤15/20%, MA120 [-5%,+15%], 30d 去重, 60d lookback")
    print(f"   评估: 5d/10d/20d 持仓\n")

    tasks = [(c, bt) for c in codes for bt in BOLL_THRESHOLDS]
    print(f"   任务数: {len(tasks)} (≈ {len(codes)} × {len(BOLL_THRESHOLDS)} = {len(tasks)})")

    by_bt = {bt: [] for bt in BOLL_THRESHOLDS}
    done = 0

    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(scan_one, t): t for t in tasks}
        for fut in as_completed(futs):
            done += 1
            if done % 1000 == 0:
                elapsed = time.time() - t0
                eta = elapsed / done * (len(tasks) - done)
                print(f"   进度: {done}/{len(tasks)} ({done/len(tasks)*100:.0f}%)  耗时 {elapsed:.0f}s  预计剩 {eta:.0f}s")
            r = fut.result()
            if r:
                code, bt = futs[fut]
                by_bt[bt].extend(r)

    elapsed = time.time() - t0
    print(f"\n⏱️  全部完成, 耗时 {elapsed:.0f}s\n")

    for bt in BOLL_THRESHOLDS:
        signals = by_bt[bt]
        if not signals:
            continue
        print("=" * 80)
        print(f"  布林阈值 = {bt}%  共触发 {len(signals)} 个信号 (全市场 {len(codes)} 只 × 60d, 30d 去重)")
        print("=" * 80)

        for label, filt in [('✅✅✅ 三重全中', lambda s: s['triple'] == '✅✅✅'),
                             ('✅✅⬜ 双信号 (推荐)', lambda s: s['triple'] == '✅✅⬜'),
                             ('✅⬜⬜ 仅布林', lambda s: s['triple'] == '✅⬜⬜'),
                             ('ALL 全部', lambda s: True)]:
            subset = [s for s in signals if filt(s)]
            if len(subset) < MIN_SAMPLES_FOR_STATS:
                continue
            print(f"\n  [{label}] n={len(subset)}")
            print(f"    {'持仓':<8}{'胜率':>8}{'均收益':>10}{'中位':>10}{'最好':>10}{'最差':>10}{'夏普':>10}")
            for hd in HOLD_DAYS:
                key = f'ret_{hd}d'
                s = stats(subset, key)
                if s:
                    sharpe = s['avg'] / s['stdev'] if s['stdev'] > 0 else 0
                    print(f"    {hd}d{'':<5}  {s['win_rate']:>6.1f}%  {s['avg']:>+8.2f}%  "
                          f"{s['median']:>+8.2f}%  {s['max']:>+8.1f}%  {s['min']:>+8.1f}%  {sharpe:>+8.2f}")

    # 最佳
    print()
    print("=" * 80)
    print("  最佳配置 (按 胜率 × 均收益)")
    print("=" * 80)
    candidates = []
    for bt in BOLL_THRESHOLDS:
        signals = by_bt[bt]
        for triple_filter in ['✅✅✅', '✅✅⬜', '✅⬜⬜', 'ALL']:
            subset = [s for s in signals if (triple_filter == 'ALL') or (s['triple'] == triple_filter)]
            for hd in HOLD_DAYS:
                s = stats(subset, f'ret_{hd}d')
                if s and s['n'] >= MIN_SAMPLES_FOR_STATS:
                    score = s['win_rate'] * 0.5 + s['avg'] * 1.0
                    candidates.append((score, bt, triple_filter, hd, s))
    candidates.sort(reverse=True)
    for score, bt, t, hd, s in candidates[:15]:
        sharpe = s['avg'] / s['stdev'] if s['stdev'] > 0 else 0
        print(f"  布林≤{bt}% + {t} + {hd}d: n={s['n']:>4} 胜率{s['win_rate']:>5.1f}% 均收益{s['avg']:>+6.2f}% 夏普{sharpe:>5.2f}")


if __name__ == "__main__":
    main()
