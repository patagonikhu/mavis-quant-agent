#!/usr/bin/env python3
"""红转绿 5 年回测对比:
- 策略 A: 红柱 ≥ 20 天 + 最近 2 根 K 线翻绿 (你的策略)
- 策略 B: 红柱 ≥ 20 天 + bar_diff 刚转正 (我的对照)
"""
import argparse
import sys
import time
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.storage.store import read_kline, _to_ts_code


def get_kline(code, limit=1250):
    rows = read_kline(_to_ts_code(code), limit=limit)
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


def check_strategy_a(i, bars):
    """策略 A: 红柱 ≥ 20 天 + 最近 2 根翻绿 (触发日 = 翻绿第 2 天)"""
    if i < 25: return None
    if not (bars[i] > 0 and bars[i-1] > 0): return None  # 最近 2 根翻绿
    # 前面红柱天数
    red_days = 0
    for j in range(i-2, -1, -1):
        if bars[j] < 0:
            red_days += 1
        else:
            break
    if red_days < 20: return None
    return {'red_days': red_days, 'strategy': 'A_2day_green'}


def check_strategy_b(i, bars):
    """策略 B: 红柱 ≥ 20 天 + bar_diff 刚转正 (红柱持续 ≥ 20 天 + bar_diff[i] > 0)"""
    if i < 25: return None
    bar_diff = [0.0]
    for j in range(1, i+1):
        bar_diff.append(bars[j] - bars[j-1])
    if bar_diff[-1] <= 0: return None  # diff 必须转正
    # 之前是否有 ≥ 20 天红柱 (任一时刻即可)
    has_20_red = False
    run = 0
    for j in range(i+1):
        if bars[j] < 0:
            run += 1
            if run >= 20:
                has_20_red = True
        else:
            run = 0
    if not has_20_red: return None
    return {'red_days': -1, 'strategy': 'B_diff_pos'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hold-days', type=int, default=20)
    parser.add_argument('--limit', type=int, default=0)
    args = parser.parse_args()

    basic = pd.read_parquet('data/history/stock_basic/stock_basic.parquet')
    all_codes = basic['ts_code'].str[:6].tolist()
    if args.limit:
        all_codes = all_codes[:args.limit]
    print(f'回测 {len(all_codes)} 只, 持有 {args.hold_days} 天')

    trades_a = []
    trades_b = []
    t0 = time.time()

    for code in all_codes:
        df = get_kline(code)
        if df is None or len(df) < 200: continue
        closes = df['close'].values
        bars = _macd_bar(closes)
        dates = df['trade_date'].values

        for i in range(60, len(closes) - args.hold_days):
            sig_a = check_strategy_a(i, bars)
            if sig_a:
                buy = closes[i + 1] if i + 1 < len(closes) else closes[i]
                sell_idx = i + args.hold_days
                if sell_idx >= len(closes): break
                future_high = max(closes[i+1:sell_idx+1])
                future_low = min(closes[i+1:sell_idx+1])
                trades_a.append({
                    'code': code, 'date': dates[i],
                    'red_days': sig_a['red_days'],
                    'buy': buy, 'sell': closes[sell_idx],
                    'max_gain': (future_high - buy) / buy * 100,
                    'max_dd': (future_low - buy) / buy * 100,
                    'ret': (closes[sell_idx] - buy) / buy * 100,
                })
            sig_b = check_strategy_b(i, bars)
            if sig_b:
                buy = closes[i + 1] if i + 1 < len(closes) else closes[i]
                sell_idx = i + args.hold_days
                if sell_idx >= len(closes): break
                future_high = max(closes[i+1:sell_idx+1])
                future_low = min(closes[i+1:sell_idx+1])
                trades_b.append({
                    'code': code, 'date': dates[i],
                    'buy': buy, 'sell': closes[sell_idx],
                    'max_gain': (future_high - buy) / buy * 100,
                    'max_dd': (future_low - buy) / buy * 100,
                    'ret': (closes[sell_idx] - buy) / buy * 100,
                })

    elapsed = time.time() - t0
    print(f'耗时 {elapsed:.0f}s\n')

    # === 统计 ===
    def stat(name, trades):
        if not trades:
            print(f'{name}: 0 笔')
            return
        n = len(trades)
        for t in [3, 5, 8, 10]:
            wins = sum(1 for tr in trades if tr['max_gain'] >= t)
            print(f'  涨 ≥ {t}% 胜率: {wins/n*100:.1f}% ({wins}/{n})')
        avg_ret = sum(tr['ret'] for tr in trades) / n
        wins5 = sum(1 for tr in trades if tr['max_gain'] >= 5)
        losses5 = sum(1 for tr in trades if tr['max_dd'] <= -5)
        total5 = wins5 + losses5
        if total5 > 0:
            wr = wins5 / total5 * 100
            expected = (wins5 * 5 + losses5 * -5) / n
            print(f'  5%/5% 策略胜率: {wr:.1f}% ({wins5}/{total5})  单笔期望: {expected:+.2f}%')
        print(f'  平均持有终: {avg_ret:+.2f}%')

    print('=== 策略 A: 红柱 ≥ 20 天 + 最近 2 根翻绿 ===')
    print(f'总笔数: {len(trades_a)}')
    stat('A', trades_a)
    print('\n=== 策略 B: 红柱 ≥ 20 天 + bar_diff 刚转正 ===')
    print(f'总笔数: {len(trades_b)}')
    stat('B', trades_b)

    # === 策略 A 按红柱天数细分 ===
    if trades_a:
        print('\n=== 策略 A 按红柱天数细分 ===')
        for lo, hi in [(20, 25), (26, 35), (36, 50), (51, 999)]:
            sub = [t for t in trades_a if lo <= t['red_days'] <= hi]
            if not sub: continue
            wins = sum(1 for t in sub if t['max_gain'] >= 5)
            print(f'  {lo:3d}~{hi:3d}天: n={len(sub):4d}  涨≥5%胜率 {wins/len(sub)*100:.1f}%')

    # 保存明细
    out_path = ROOT / 'docs' / 'red-to-green-backtest-trades.md'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'# MACD 红转绿策略回测 (持有 {args.hold_days} 天)\n\n')
        f.write(f'> 策略 A: 红柱 ≥ 20 天 + 最近 2 根翻绿 (笔数: {len(trades_a)})\n')
        f.write(f'> 策略 B: 红柱 ≥ 20 天 + bar_diff 转正 (笔数: {len(trades_b)})\n\n')
        f.write('## 策略 A 明细\n\n')
        f.write('| 代码 | 触发日 | 红柱天 | 买入价 | max_gain | max_dd | 持有终 |\n')
        f.write('|---|---|---|---|---|---|---|\n')
        for t in trades_a:
            f.write(f"| {t['code']} | {t['date']} | {t['red_days']} | ¥{t['buy']:.2f} | "
                    f"{t['max_gain']:+.2f}% | {t['max_dd']:+.2f}% | {t['ret']:+.2f}% |\n")
        f.write('\n## 策略 B 明细\n\n')
        f.write('| 代码 | 触发日 | 买入价 | max_gain | max_dd | 持有终 |\n')
        f.write('|---|---|---|---|---|---|\n')
        for t in trades_b[:100]:
            f.write(f"| {t['code']} | {t['date']} | ¥{t['buy']:.2f} | "
                    f"{t['max_gain']:+.2f}% | {t['max_dd']:+.2f}% | {t['ret']:+.2f}% |\n")
        if len(trades_b) > 100:
            f.write(f'\n(仅显示前 100 笔, 实际 {len(trades_b)} 笔)\n')
    print(f'\n📄 {out_path}')


if __name__ == '__main__':
    main()