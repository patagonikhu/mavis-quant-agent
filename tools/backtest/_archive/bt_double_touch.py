"""
/tmp/bt_double_touch.py — 5 天内 2 次触底 + 三重全中回测

新条件: 在 5 天窗口内, 出现 2 次布林% < 15% 的触底, 且每次都是三重全中 (✅✅✅)
- 1次触底 = 单日布林<15% + 缠论 + MACD 底背驰 同时满足
- 5d 2次 = 持续超卖 + 主力持续吸筹
- 这是更严的"强吸筹"信号

回测:
  - 18 只样本 (watchlist 内)
  - 5783 只全市场
  - 持仓 5d/10d/20d 收益
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
HOLD_DAYS = [5, 10, 20]
BOLL_THRESHOLDS = [10, 15]
MA120_MIN = -5
MA120_MAX = 15
WINDOW_DAYS = 5  # 5d 内 2 次触底
REQUIRED_TRIPLE = '✅✅✅'  # 三重全中 (布林+缠论+MACD)


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


def find_double_touch_signals(args):
    """ProcessPool worker: 找 5d 内 2 次触底 (三重全中) 信号"""
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

        # 第一步: 找所有触底行 (布林<阈值 + MA120 在范围 + 三重全中)
        touch_rows = []
        for i, r in enumerate(rows):
            bpct = r.get('boll_pct')
            ma120 = r.get('ma120_dev')
            if bpct is None or ma120 is None:
                continue
            if not (0 <= bpct <= boll_threshold):
                continue
            if not (MA120_MIN <= ma120 <= MA120_MAX):
                continue

            has_c = has_chan_bot(rows, i)
            has_m = has_macd_bot(rows, i)
            if has_c and has_m:
                touch_rows.append((i, r))

        # 第二步: 找 5d 内 2 次触底的 (相邻两次触底 < 5d)
        signals = []
        for j in range(len(touch_rows) - 1):
            idx1, r1 = touch_rows[j]
            idx2, r2 = touch_rows[j + 1]
            # 检查 idx1 和 idx2 是否在 5d 窗口内
            date1 = r1.get('date')
            date2 = r2.get('date')
            if date1 not in closes_dict or date2 not in closes_dict:
                continue
            try:
                d1_idx = sorted_dates.index(date1)
                d2_idx = sorted_dates.index(date2)
            except ValueError:
                continue
            if d2_idx - d1_idx > WINDOW_DAYS:
                continue  # 间隔超过 5d, 不是同一窗口

            # 2 次触底都在! 用第 2 次的日期作为入场信号
            trigger_date = date2
            rets = {}
            for hd in HOLD_DAYS:
                r_v = future_ret(closes_dict, sorted_dates, trigger_date, hd)
                rets[f'ret_{hd}d'] = r_v
            if rets['ret_5d'] is None:
                continue

            signals.append({
                'code': code,
                'trigger_date': trigger_date,
                'first_touch': date1,
                'days_between': d2_idx - d1_idx,
                'boll_1': r1.get('boll_pct'),
                'boll_2': r2.get('boll_pct'),
                **rets,
            })
        return signals
    except Exception as e:
        return []


def stats(signals, key, min_n=3):
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


def run_sample(codes, label):
    """跑一组样本"""
    t0 = time.time()
    print(f"\n{'=' * 80}")
    print(f"  跑 {label}: {len(codes)} 只")
    print(f"{'=' * 80}")

    by_bt = {bt: [] for bt in BOLL_THRESHOLDS}
    tasks = [(c, bt) for c in codes for bt in BOLL_THRESHOLDS]
    done = 0

    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(find_double_touch_signals, t): t for t in tasks}
        for fut in as_completed(futs):
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - t0
                eta = elapsed / done * (len(tasks) - done)
                print(f"  进度: {done}/{len(tasks)} ({done/len(tasks)*100:.0f}%)  耗时 {elapsed:.0f}s  预计剩 {eta:.0f}s")
            r = fut.result()
            if r:
                code, bt = futs[fut]
                by_bt[bt].extend(r)

    elapsed = time.time() - t0
    print(f"\n⏱️  完成, 耗时 {elapsed:.0f}s\n")

    for bt in BOLL_THRESHOLDS:
        signals = by_bt[bt]
        if not signals:
            print(f"布林 ≤ {bt}%: 0 信号 (5d 内 2 次触底 + 三重全中 太严苛)")
            continue
        print(f"{'=' * 80}")
        print(f"  布林阈值 ≤ {bt}%  共触发 {len(signals)} 个信号")
        print(f"  (条件: 5d 内 2 次触底 + 三重全中 ✅✅✅)")
        print(f"{'=' * 80}")

        # 触底间隔分布
        gaps = [s['days_between'] for s in signals]
        avg_gap = statistics.mean(gaps)
        print(f"\n  触底间隔: 平均 {avg_gap:.1f}d, 范围 [{min(gaps)}d, {max(gaps)}d]")

        for hd in HOLD_DAYS:
            key = f'ret_{hd}d'
            s = stats(signals, key, min_n=3)
            if s:
                sharpe = s['avg'] / s['stdev'] if s['stdev'] > 0 else 0
                bar = '█' * int(s['win_rate'] / 5)
                print(f"  {hd}d 持仓: 胜率 {s['win_rate']:5.1f}% ({s['n']}个)  "
                      f"均收益 {s['avg']:+6.2f}%  中位 {s['median']:+6.2f}%  "
                      f"夏普 {sharpe:+.2f}  区间 [{s['min']:+6.1f}%, {s['max']:+6.1f}%]  {bar}")

    return by_bt


def main():
    print("=" * 80)
    print(f"  /t-am-divergence 加严版: 5d 内 2 次触底 + 三重全中")
    print(f"  实战意义: 持续超卖 + 主力真在买 (强吸筹信号)")
    print(f"  样本: 18 只 (watchlist 优等生) + 5783 只 (全市场)")
    print("=" * 80)

    # 1. 18 只样本
    by_bt_sample = run_sample(SAMPLE_CODES, "18 只 watchlist")

    # 2. 全市场
    from tools.data_store import DataStore
    all_codes = DataStore.list_codes()
    by_bt_all = run_sample(all_codes, f"全市场 {len(all_codes)} 只")

    # 对比
    print(f"\n{'=' * 80}")
    print(f"  对比: 18 只 vs 全市场")
    print(f"{'=' * 80}")
    print(f"{'条件':<35}{'样本':<10}{'信号数':<8}{'20d胜率':<10}{'20d均收益':<12}")
    for bt in BOLL_THRESHOLDS:
        for label, by_bt in [('18 只', by_bt_sample), ('全市场', by_bt_all)]:
            signals = by_bt.get(bt, [])
            s = stats(signals, 'ret_20d', min_n=3)
            if s:
                cond = f"布林≤{bt}% + 5d 2次 + 三重"
                print(f"{cond:<35}{label:<10}{s['n']:<8}{s['win_rate']:>6.1f}%  {s['avg']:>+8.2f}%")

    # 实战建议
    print(f"\n{'=' * 80}")
    print(f"  实战建议")
    print(f"{'=' * 80}")
    # 找最佳配置
    best = None
    for label, by_bt in [('18 只', by_bt_sample), ('全市场', by_bt_all)]:
        for bt in BOLL_THRESHOLDS:
            signals = by_bt.get(bt, [])
            for hd in HOLD_DAYS:
                s = stats(signals, f'ret_{hd}d', min_n=3)
                if s:
                    score = s['win_rate'] * 0.5 + s['avg'] * 1.0
                    if best is None or score > best[0]:
                        best = (score, label, bt, hd, s)
    if best:
        _, label, bt, hd, s = best
        print(f"  最佳: {label} + 布林≤{bt}% + 5d 2次 + 三重 + {hd}d 持仓")
        print(f"         胜率 {s['win_rate']:.1f}% ({s['n']} 个样本)")
        print(f"         均收益 {s['avg']:+.2f}%, 最佳 {s['max']:+.1f}%, 最差 {s['min']:+.1f}%")


if __name__ == "__main__":
    main()
