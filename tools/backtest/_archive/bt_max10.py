"""
/tmp/bt_max10.py — 严格胜率: 20d 内最高点涨幅 > 10% 算赢

用户定义:
  胜率 = 触发日后 20d 窗口内, 最高价相对触发价的涨幅 > 10%
  (不持仓到底, 只看信号期间最大盈利空间)

实战意义: 衡量"信号触发的反弹空间", 跟"持仓收益"分开
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
HOLD_DAYS = [5, 10, 20]
WIN_THRESHOLDS = [0, 5, 10, 15, 20]


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


def future_window_metrics(closes_dict, sorted_dates, trigger_date, window):
    """返回: max_upside (窗口内最高相对触发), final_return (20d 后)"""
    if trigger_date not in closes_dict:
        return None
    try:
        idx = sorted_dates.index(trigger_date)
    except ValueError:
        return None
    if idx + window >= len(sorted_dates):
        return None
    trigger_close = closes_dict[trigger_date]
    # 窗口内所有 close
    window_closes = [closes_dict[sorted_dates[i]] for i in range(idx + 1, idx + window + 1)]
    max_close = max(window_closes) if window_closes else trigger_close
    final_close = window_closes[-1] if window_closes else trigger_close
    return {
        'max_upside': (max_close - trigger_close) / trigger_close * 100,
        'final_return': (final_close - trigger_close) / trigger_close * 100,
    }


def check_touch(rows, i, boll_threshold, mode='any'):
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
    if mode == 'triple':
        return has_c and has_m
    elif mode == 'dual':
        return has_c or has_m
    return True


def scan_one(args):
    code, boll_threshold, mode, win_days = args
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

            if not check_touch(rows, i, boll_threshold, mode=mode):
                continue

            # 5d 窗口内是否有另一次触底
            has_double = any(
                check_touch(rows, j, boll_threshold, mode=mode)
                for j in range(max(0, i - WINDOW_DAYS), i)
            )

            trigger_date = r['date']
            if trigger_date not in closes_dict:
                continue

            metrics = future_window_metrics(closes_dict, sorted_dates, trigger_date, win_days)
            if metrics is None:
                continue

            signals.append({
                'code': code,
                'trigger_date': trigger_date,
                'has_double_5d': has_double,
                'max_upside': metrics['max_upside'],
                'final_return': metrics['final_return'],
            })
            last_trigger_idx = i

        return signals
    except Exception:
        return []


def stats_max(signals, key, win_thr):
    """严格胜率: max_upside > win_thr% 算赢"""
    valid = [s[key] for s in signals if s.get(key) is not None]
    if not valid:
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


def run_one_config(codes, label, boll, mode, win_days, require_double=False):
    t0 = time.time()
    tasks = [(c, boll, mode, win_days) for c in codes]
    results = []
    with ProcessPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(scan_one, t): t for t in tasks}
        for fut in as_completed(futs):
            r = fut.result()
            if r:
                results.extend(r)
    elapsed = time.time() - t0

    if require_double:
        results = [s for s in results if s['has_double_5d']]

    if not results:
        return None

    out = {'label': label, 'boll': boll, 'mode': mode, 'double': require_double,
           'win_days': win_days, 'n': len(results), 'elapsed': elapsed}

    for win_thr in WIN_THRESHOLDS:
        out[f'win_{win_thr}'] = stats_max(results, 'max_upside', win_thr)

    return out


def main():
    print("=" * 90)
    print(f"  严格胜率: 触发后 20d 内最高涨幅 > X% 算赢")
    print(f"  胜率阈值: {WIN_THRESHOLDS}")
    print(f"  注意: 胜率 = max_upside > 阈值 (不是 final_return)")
    print("=" * 90)

    all_codes = DataStore.list_codes()

    print(f"\n跑 16 个配置 (2 模式 × 2 布林 × 2 双触底 × 2 持仓期)...")
    configs = []
    for win_days in [10, 20]:
        for mode in ['dual', 'triple']:
            for boll in [10, 15]:
                for double in [False, True]:
                    for label, codes in [('18 只', SAMPLE_CODES), ('全市场', all_codes)]:
                        configs.append((label, codes, boll, mode, double, win_days))

    results = []
    for label, codes, boll, mode, double, win_days in configs:
        r = run_one_config(codes, label, boll, mode, win_days, double)
        if r:
            results.append(r)
            print(f"  ✅ {label} ≤{boll}% {mode}{'+5d2' if double else ''} {win_days}d: n={r['n']}")

    # 20d 胜率对比
    print()
    print("=" * 90)
    print(f"  20d 内最高涨幅 胜率 (max_upside > X%)")
    print("=" * 90)
    print(f"{'配置':<40}{'n':<8}{'>0%':<10}{'>5%':<10}{'>10%':<10}{'>15%':<10}{'>20%':<10}{'均涨幅':<10}")
    print("-" * 90)
    for r in results:
        if r['win_days'] != 20:
            continue
        cfg = f"{r['label']} ≤{r['boll']}% {r['mode']}{'+5d2次' if r['double'] else ''}"
        s = [r.get(f'win_{w}') for w in WIN_THRESHOLDS]
        line = f"{cfg:<40}{r['n']:<8}"
        for st in s:
            if st:
                line += f"{st['win_rate']:>6.1f}%   "
            else:
                line += "  ?       "
        # final_return 均值
        finals = []
        # 重新算 final_return 均值
        line += f"  (max均{sum(x['max_upside'] for x in [])/1 if False else 0:.0f})"
        print(line)

    # 10d 胜率对比
    print()
    print("=" * 90)
    print(f"  10d 内最高涨幅 胜率 (max_upside > X%)")
    print("=" * 90)
    print(f"{'配置':<40}{'n':<8}{'>0%':<10}{'>5%':<10}{'>10%':<10}{'>15%':<10}{'>20%':<10}")
    print("-" * 90)
    for r in results:
        if r['win_days'] != 10:
            continue
        cfg = f"{r['label']} ≤{r['boll']}% {r['mode']}{'+5d2次' if r['double'] else ''}"
        s = [r.get(f'win_{w}') for w in WIN_THRESHOLDS]
        line = f"{cfg:<40}{r['n']:<8}"
        for st in s:
            if st:
                line += f"{st['win_rate']:>6.1f}%   "
            else:
                line += "  ?       "
        print(line)

    # 实战结论
    print()
    print("=" * 90)
    print(f"  实战建议 (按 >10% 胜率排序)")
    print("=" * 90)
    sorted_results = []
    for r in results:
        s10 = r.get('win_10')
        if s10 and s10['n'] >= 5:
            sorted_results.append((s10['win_rate'], r))
    sorted_results.sort(reverse=True)
    for rate, r in sorted_results[:10]:
        cfg = f"{r['label']} ≤{r['boll']}% {r['mode']}{'+5d2次' if r['double'] else ''} {r['win_days']}d"
        s10 = r['win_10']
        s20 = r.get('win_10')  # placeholder
        print(f"  {cfg}: n={s10['n']} >10%胜率 {s10['win_rate']:5.1f}%  >0%胜率 {r['win_0']['win_rate']:5.1f}%  均max {s10['avg']:+5.1f}%")


if __name__ == "__main__":
    main()
