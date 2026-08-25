"""
/tmp/bt_10pct.py — 严格胜率回测: 20d 涨幅 > 10% 算赢

用户定义: 胜率 = 20d 持仓涨幅 > 10% (不是 > 0)
- 大涨 = 真信号
- 微涨 (< 10%) = 噪声, 算输

对比 4 个条件 (18 只 + 全市场):
  1. 布林≤15% + 双信号 (✅✅⬜) + 20d
  2. 布林≤15% + 三重全中 (✅✅✅) + 20d
  3. 布林≤15% + 5d 2次 + 三重 + 20d
  4. 布林≤15% + 5d 2次 + 双信号 + 20d  (新!)
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
WIN_THRESHOLDS = [0, 5, 10, 15]  # 胜率阈值: >0% / >5% / >10% / >15%


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


def check_touch(rows, i, boll_threshold, mode='any'):
    """检查第 i 行是否为触底 (布林+MA120), mode='any'/'triple'/'dual'"""
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
    elif mode == 'any':
        return True
    return False


def scan_one(args):
    code, boll_threshold, mode = args
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

            # 检查 5d 窗口内是否有另一次触底 (如果开启)
            # 这里简化: 5d 内是否另有 触底 (任意 mode)
            has_double = False
            for j in range(max(0, i - WINDOW_DAYS), i):
                if check_touch(rows, j, boll_threshold, mode=mode):
                    has_double = True
                    break

            trigger_date = r['date']
            if trigger_date not in closes_dict:
                continue

            rets = {f'ret_{hd}d': future_ret(closes_dict, sorted_dates, trigger_date, hd) for hd in HOLD_DAYS}
            if rets['ret_20d'] is None:
                continue

            signals.append({
                'code': code,
                'trigger_date': trigger_date,
                'has_double_5d': has_double,
                **rets,
            })
            last_trigger_idx = i

        return signals
    except Exception as e:
        return []


def stats_strict(signals, key, win_thr):
    """严格胜率: 20d 涨幅 > win_thr% 算赢"""
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
        'stdev': statistics.stdev(valid) if len(valid) > 1 else 0,
        'wins': wins,
    }


def run_one_config(codes, label, boll, mode, require_double=False):
    """跑一种配置"""
    t0 = time.time()
    tasks = [(c, boll, mode) for c in codes]
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
           'n': len(results), 'elapsed': elapsed}

    for win_thr in WIN_THRESHOLDS:
        s20 = stats_strict(results, 'ret_20d', win_thr)
        s10 = stats_strict(results, 'ret_10d', win_thr)
        s5  = stats_strict(results, 'ret_5d',  win_thr)
        out[f'win_{win_thr}'] = {'20d': s20, '10d': s10, '5d': s5}

    return out


def main():
    print("=" * 90)
    print(f"  严格胜率回测: 20d 涨幅 > X% 算赢")
    print(f"  胜率阈值: {WIN_THRESHOLDS}")
    print("=" * 90)

    all_codes = DataStore.list_codes()

    # 8 个配置: 2 模式 × 2 布林 × 2 是否要求双触底
    configs = [
        ('18 只', SAMPLE_CODES, 15, 'dual',  False),
        ('18 只', SAMPLE_CODES, 15, 'dual',  True),  # 双信号 + 5d 2次
        ('18 只', SAMPLE_CODES, 15, 'triple', False),
        ('18 只', SAMPLE_CODES, 15, 'triple', True),  # 三重 + 5d 2次
        ('全市场', all_codes,    15, 'dual',  False),
        ('全市场', all_codes,    15, 'dual',  True),
        ('全市场', all_codes,    15, 'triple', False),
        ('全市场', all_codes,    15, 'triple', True),
    ]

    print(f"\n跑 8 个配置 (18 只 + 全市场)...")
    results = []
    for label, codes, boll, mode, double in configs:
        r = run_one_config(codes, label, boll, mode, double)
        if r:
            results.append(r)
            print(f"  ✅ {label} 布林≤{boll}% + {mode} + 5d2次={double}: {r['n']} 信号")

    # 输出对比表
    print()
    print("=" * 90)
    print(f"  20d 持仓胜率 (不同阈值)")
    print("=" * 90)
    print(f"{'配置':<35}{'n':<8}{'>0%':<10}{'>5%':<10}{'>10%':<10}{'>15%':<10}{'均收益':<10}")
    print("-" * 90)
    for r in results:
        cfg = f"{r['label']} ≤{r['boll']}% {r['mode']}{'+5d2' if r['double'] else ''}"
        s0  = r['win_0'].get('20d')
        s5  = r['win_5'].get('20d')
        s10 = r['win_10'].get('20d')
        s15 = r['win_15'].get('20d')
        avg = s0['avg'] if s0 else 0
        print(f"{cfg:<35}{r['n']:<8}"
              f"{s0['win_rate']:>6.1f}%   " if s0 else "  ?       "
              f"{s5['win_rate']:>6.1f}%   " if s5 else "  ?       "
              f"{s10['win_rate']:>6.1f}%   " if s10 else "  ?       "
              f"{s15['win_rate']:>6.1f}%   " if s15 else "  ?       "
              f"{avg:+6.2f}%")

    # 10d 对比
    print()
    print("=" * 90)
    print(f"  10d 持仓胜率 (不同阈值)")
    print("=" * 90)
    print(f"{'配置':<35}{'n':<8}{'>0%':<10}{'>5%':<10}{'>10%':<10}{'>15%':<10}{'均收益':<10}")
    print("-" * 90)
    for r in results:
        cfg = f"{r['label']} ≤{r['boll']}% {r['mode']}{'+5d2' if r['double'] else ''}"
        s0  = r['win_0'].get('10d')
        s5  = r['win_5'].get('10d')
        s10 = r['win_10'].get('10d')
        s15 = r['win_15'].get('10d')
        avg = s0['avg'] if s0 else 0
        print(f"{cfg:<35}{r['n']:<8}"
              f"{s0['win_rate']:>6.1f}%   " if s0 else "  ?       "
              f"{s5['win_rate']:>6.1f}%   " if s5 else "  ?       "
              f"{s10['win_rate']:>6.1f}%   " if s10 else "  ?       "
              f"{s15['win_rate']:>6.1f}%   " if s15 else "  ?       "
              f"{avg:+6.2f}%")

    # 实战结论
    print()
    print("=" * 90)
    print(f"  实战建议 (按 >10% 胜率排序, 20d 持仓)")
    print("=" * 90)
    # 找 >10% 胜率最高的配置
    sorted_results = sorted(results, key=lambda r: r['win_10']['20d']['win_rate'] if r['win_10']['20d'] else 0, reverse=True)
    for r in sorted_results[:8]:
        cfg = f"{r['label']} ≤{r['boll']}% {r['mode']}{'+5d2次' if r['double'] else ''}"
        s10 = r['win_10']['20d']
        if s10:
            print(f"  {cfg}: n={s10['n']} 胜率{s10['win_rate']:5.1f}% 均收益{s10['avg']:+.2f}% 胜{s10['wins']}/{s10['n']}")


if __name__ == "__main__":
    main()
