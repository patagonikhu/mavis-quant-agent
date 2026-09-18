#!/usr/bin/env python3
"""红转绿 分年回测: 策略 A vs 策略 B 按年份拆开胜率

策略 A: 红柱 ≥ N 天 + 最近 2 根翻绿
策略 B: 红柱 ≥ N 天 + bar_diff 刚转正

用法:
    python -m tools.batch.red_to_green_yearly --min-red 20 --hold-days 20
"""
import argparse
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore


def get_kline(code, limit=1250):
    rows = DataStore.get_kline(code, limit=limit)
    if not rows: return None
    df = pd.DataFrame(rows)
    df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '').str[:8]
    return df.sort_values('trade_date').reset_index(drop=True)


def _macd_bar(closes):
    n = len(closes)
    if n < 26: return [0.0] * n
    ema12 = sum(closes[:12]) / 12.0
    ema26 = sum(closes[:26]) / 26.0
    dif = [0.0] * n
    dea = [0.0] * n
    bar = [0.0] * n
    dif[25] = ema12 - ema26
    dea[25] = dif[25]
    alpha12, alpha26, alpha9 = 2/13, 2/27, 2/10
    for i in range(26, n):
        ema12 = closes[i] * alpha12 + ema12 * (1 - alpha12)
        ema26 = closes[i] * alpha26 + ema26 * (1 - alpha26)
        dif[i] = ema12 - ema26
        dea[i] = dif[i] * alpha9 + dea[i-1] * (1 - alpha9)
        bar[i] = (dif[i] - dea[i]) * 2
    return bar


def check_a(i, bars, min_red):
    if i < 25: return None
    if not (bars[i] > 0 and bars[i-1] > 0): return None
    red = 0
    for j in range(i-2, -1, -1):
        if bars[j] < 0: red += 1
        else: break
    if red < min_red: return None
    return red


def check_b(i, bars, min_red):
    if i < 25: return None
    bar_diff = [0.0]
    for j in range(1, i+1):
        bar_diff.append(bars[j] - bars[j-1])
    if bar_diff[-1] <= 0: return None
    has = False; run = 0
    for j in range(i+1):
        if bars[j] < 0:
            run += 1
            if run >= min_red: has = True
        else:
            run = 0
    if not has: return None
    return 0  # B 不记录红柱天数


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-red', type=int, default=20)
    parser.add_argument('--hold-days', type=int, default=20)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()

    basic = pd.read_parquet('data/history/stock_basic/stock_basic.parquet')
    all_codes = basic['ts_code'].str[:6].tolist()
    if args.limit:
        all_codes = all_codes[:args.limit]
    print(f'回测 {len(all_codes)} 只, 红柱 ≥ {args.min_red} 天, 持有 {args.hold_days} 天')

    # 按年分桶
    by_year_a = {}
    by_year_b = {}

    t0 = time.time()
    for code in all_codes:
        df = get_kline(code)
        if df is None or len(df) < 200: continue
        closes = df['close'].values
        dates = df['trade_date'].values
        bars = _macd_bar(closes)

        for i in range(60, len(closes) - args.hold_days):
            year = dates[i][:4]  # '20260914' -> '2026'
            buy = closes[i + 1] if i + 1 < len(closes) else closes[i]
            sell_idx = i + args.hold_days
            if sell_idx >= len(closes): break
            future_high = max(closes[i+1:sell_idx+1])
            future_low = min(closes[i+1:sell_idx+1])
            ret = (closes[sell_idx] - buy) / buy * 100
            max_gain = (future_high - buy) / buy * 100
            max_dd = (future_low - buy) / buy * 100
            if year not in by_year_a: by_year_a[year] = []
            if year not in by_year_b: by_year_b[year] = []

            if check_a(i, bars, args.min_red) is not None:
                by_year_a[year].append({'max_gain': max_gain, 'max_dd': max_dd, 'ret': ret})
            if check_b(i, bars, args.min_red) is not None:
                by_year_b[year].append({'max_gain': max_gain, 'max_dd': max_dd, 'ret': ret})

    elapsed = time.time() - t0
    print(f'耗时 {elapsed:.0f}s\n')

    # === 按年统计 ===
    def stat_year(year_data):
        out = {}
        for y in sorted(year_data):
            trades = year_data[y]
            if not trades: continue
            n = len(trades)
            wins5 = sum(1 for t in trades if t['max_gain'] >= 5)
            losses5 = sum(1 for t in trades if t['max_dd'] <= -5)
            total5 = wins5 + losses5
            wr5 = wins5 / total5 * 100 if total5 > 0 else 0
            expected = (wins5 * 5 + losses5 * -5) / n if n > 0 else 0
            avg_ret = sum(t['ret'] for t in trades) / n
            out[y] = {'n': n, 'wins5': wins5, 'losses5': losses5, 'wr5': wr5, 'expected': expected, 'avg_ret': avg_ret}
        return out

    a_stats = stat_year(by_year_a)
    b_stats = stat_year(by_year_b)

    print(f'{"年份":>6}{"n":>6}{"涨≥5%":>8}{"胜率":>8}{"期望":>8}{"持有终":>8}  | {"n":>6}{"涨≥5%":>8}{"胜率":>8}{"期望":>8}{"持有终":>8}')
    print('-' * 95)
    print(' 策略 A: 红柱 ≥ ' + str(args.min_red) + ' 天 + 最近2根翻绿      |  策略 B: 红柱 ≥ ' + str(args.min_red) + ' 天 + bar_diff 刚转正')
    for y in sorted(set(list(a_stats.keys()) + list(b_stats.keys()))):
        a = a_stats.get(y, {})
        b = b_stats.get(y, {})
        a_str = f"{a.get('n',0):>6}{a.get('wins5',0):>8}{a.get('wr5',0):>7.1f}%{a.get('expected',0):>+7.2f}%{a.get('avg_ret',0):>+7.2f}%"
        b_str = f"{b.get('n',0):>6}{b.get('wins5',0):>8}{b.get('wr5',0):>7.1f}%{b.get('expected',0):>+7.2f}%{b.get('avg_ret',0):>+7.2f}%"
        print(f'{y:>6}{a_str}  | {b_str}')

    # === 累计 ===
    total_a = sum(a_stats[y]['n'] for y in a_stats)
    total_b = sum(b_stats[y]['n'] for y in b_stats)
    print('\n=== 累计 5 年 ===')
    print(f'策略 A: {total_a} 笔, 涨≥5% {a_stats and sum(a_stats[y]["wins5"] for y in a_stats)/total_a*100:.1f}% 期望 {a_stats and sum(a_stats[y]["expected"]*a_stats[y]["n"] for y in a_stats)/total_a:+.2f}%')
    print(f'策略 B: {total_b} 笔, 涨≥5% {b_stats and sum(b_stats[y]["wins5"] for y in b_stats)/total_b*100:.1f}% 期望 {b_stats and sum(b_stats[y]["expected"]*b_stats[y]["n"] for y in b_stats)/total_b:+.2f}%')


if __name__ == '__main__':
    main()