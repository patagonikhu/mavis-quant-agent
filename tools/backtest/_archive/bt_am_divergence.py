"""
/tmp/bt_am_divergence.py — /t-am-divergence 历史回测 (v2, 正确版)

设计:
  1. 选 18 只样本股
  2. 跑 factor_history(lookback=180d) 拿到 daily 行
  3. 在历史 180 天里**所有**触底信号, 评估 5d/10d/20d 后的实际收益
  4. 按三重级别 + 布林阈值分类, 输出胜率

修正: 不是只看最近 5 天的信号, 而是在 180 天里所有触底, 然后看持仓 5/10/20d 收益
"""

import sys
import statistics
from pathlib import Path
from datetime import datetime
from collections import defaultdict

ROOT = Path('/Users/I514959/workspace/mavis-quant-agent')
sys.path.insert(0, str(ROOT))

from tools.data_store import DataStore
from tools.analysis.factor_history import compute_factor_history
from tools.analysis.analysis_engine import ChanStrategy, MacdDivergenceStrategy

SAMPLE_CODES = [
    '688012', '688361', '688072', '688082', '688037',
    '688041', '688256',
    '002371', '300604', '603290',
    '002028', '600089', '300274',
    '300308', '300502', '300223',
    '300285', '601138',
]

LOOKBACK_DAYS = 180
HOLD_DAYS = [5, 10, 20]
BOLL_THRESHOLDS = [10, 15, 20]
MA120_MIN = -5
MA120_MAX = 15
# 防止同一段重复算: 触底后 30d 内不重复
MIN_GAP_DAYS = 30


def get_closes_dict(ctx):
    return {k['trade_date']: k['close'] for k in ctx.kline}


def get_dates_sorted(closes_dict):
    return sorted(closes_dict.keys())


def find_future_return(closes_dict, sorted_dates, trigger_date, hold_days):
    """trigger 日后 hold_days 天的收益"""
    if trigger_date not in closes_dict:
        return None
    idx = sorted_dates.index(trigger_date)
    target_idx = idx + hold_days
    if target_idx >= len(sorted_dates):
        return None
    return (closes_dict[sorted_dates[target_idx]] - closes_dict[trigger_date]) / closes_dict[trigger_date] * 100


def has_chan_bot_within(rows, trigger_idx, lookback=30):
    start = max(0, trigger_idx - lookback)
    for r in rows[start:trigger_idx + 1]:
        bc = r.get("daily_beichi") or {}
        if isinstance(bc, dict):
            d = bc.get("direction", "")
            s = bc.get("strength", "")
            if d == "bot" and s in ("strong", "weak"):
                return True
        elif isinstance(bc, str) and "底背" in bc:
            return True
    return False


def has_macd_bot_within(rows, trigger_idx, lookback=5):
    start = max(0, trigger_idx - lookback)
    return any(r.get("macd_div_bot") for r in rows[start:trigger_idx + 1])


def simulate_one_code(code, boll_threshold):
    """对单只票按指定布林阈值模拟回测"""
    ctx = DataStore.get_ctx(code, kline_only=True, limit=400)
    if len(ctx.kline) < 60:
        return []

    closes_dict = get_closes_dict(ctx)
    sorted_dates = get_dates_sorted(closes_dict)
    rows = compute_factor_history(ctx, step=1, lookback=LOOKBACK_DAYS,
                                  strategies=[ChanStrategy, MacdDivergenceStrategy])
    if not rows or len(rows) < 30:
        return []

    signals = []
    last_trigger_idx = -MIN_GAP_DAYS - 1  # 30d 内不重复

    for i, r in enumerate(rows):
        # 防重复 (同段触底)
        if i - last_trigger_idx < MIN_GAP_DAYS:
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
        # 找 5d 持仓的未来日期, 必须存在
        if trigger_date not in closes_dict:
            continue
        trigger_idx_in_closes = sorted_dates.index(trigger_date)

        has_chan = has_chan_bot_within(rows, i)
        has_macd = has_macd_bot_within(rows, i)

        if has_chan and has_macd:
            triple = "✅✅✅"
        elif has_chan or has_macd:
            triple = "✅✅⬜"
        else:
            triple = "✅⬜⬜"

        rets = {}
        for hd in HOLD_DAYS:
            r_v = find_future_return(closes_dict, sorted_dates, trigger_date, hd)
            rets[f'ret_{hd}d'] = r_v

        # 至少 5d 收益有效
        if rets['ret_5d'] is None:
            continue

        signals.append({
            'code': code,
            'trigger_date': trigger_date,
            'trigger_price': r.get('close', 0),
            'boll_pct': bpct,
            'ma120_dev': ma120,
            'has_chan': has_chan,
            'has_macd': has_macd,
            'triple': triple,
            **rets,
        })
        last_trigger_idx = i

    return signals


def stats(signals, key):
    valid = [s[key] for s in signals if s.get(key) is not None]
    if not valid:
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
    print("=" * 80)
    print(f"  /t-am-divergence 胜率回测 (180d 历史, 18 只样本)")
    print(f"  评估: {HOLD_DAYS} 天持仓  布林阈值: {BOLL_THRESHOLDS}")
    print("=" * 80)

    all_signals = {bt: [] for bt in BOLL_THRESHOLDS}
    for code in SAMPLE_CODES:
        for bt in BOLL_THRESHOLDS:
            sigs = simulate_one_code(code, bt)
            all_signals[bt].extend(sigs)
        sigs_15 = simulate_one_code(code, 15)
        print(f"  {code}: 布林≤15% 共 {len(sigs_15)} 信号")

    # 报告
    for bt in BOLL_THRESHOLDS:
        signals = all_signals[bt]
        if not signals:
            continue
        print()
        print("=" * 80)
        print(f"  布林阈值 = {bt}%  共触发 {len(signals)} 个信号 (18 只 × 180d, 30d 去重)")
        print("=" * 80)

        for label, filt in [('✅✅✅ 三重全中', lambda s: s['triple'] == '✅✅✅'),
                             ('✅✅⬜ 双信号', lambda s: s['triple'] == '✅✅⬜'),
                             ('✅⬜⬜ 仅布林', lambda s: s['triple'] == '✅⬜⬜'),
                             ('ALL 全部', lambda s: True)]:
            subset = [s for s in signals if filt(s)]
            if not subset:
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

    # 总结: 找最佳配置
    print()
    print("=" * 80)
    print("  最佳配置排序 (按胜率 × 均收益)")
    print("=" * 80)
    candidates = []
    for bt in BOLL_THRESHOLDS:
        signals = all_signals[bt]
        for triple_filter in ['✅✅✅', '✅✅⬜', '✅⬜⬜', 'ALL']:
            subset = [s for s in signals if (triple_filter == 'ALL') or (s['triple'] == triple_filter)]
            for hd in HOLD_DAYS:
                s = stats(subset, f'ret_{hd}d')
                if s and s['n'] >= 5:
                    score = s['win_rate'] * 0.5 + s['avg'] * 1.0  # 综合分
                    candidates.append((score, bt, triple_filter, hd, s))
    candidates.sort(reverse=True)
    for score, bt, t, hd, s in candidates[:10]:
        print(f"  布林≤{bt}% + {t} + {hd}d 持仓: n={s['n']} 胜率{s['win_rate']:.1f}% 均收益{s['avg']:+.2f}%  夏普{s['avg']/s['stdev']:.2f}" if s['stdev'] else f"  布林≤{bt}% + {t} + {hd}d 持仓: n={s['n']} 胜率{s['win_rate']:.1f}% 均收益{s['avg']:+.2f}%")


if __name__ == "__main__":
    main()
