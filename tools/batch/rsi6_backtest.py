#!/usr/bin/env python3
"""RSI6 超卖反弹回测 — 多阈值对比

策略: RSI6 < threshold 当日, 次日开盘买入, 持有 N 天
阈值扫描: < 5 / < 10 / < 15 / < 20 / < 30
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


def rsi6(closes):
    """Wilder's RSI(6), 前 6 位置 NaN"""
    n = len(closes)
    if n < 7: return np.full(n, np.nan)
    diff = np.diff(closes, prepend=closes[0])
    up = np.where(diff > 0, diff, 0.0)
    dn = np.where(diff < 0, -diff, 0.0)
    rsi = np.full(n, np.nan)
    # Wilder smoothing
    avg_up = up[:6].mean()
    avg_dn = dn[:6].mean()
    rs = avg_up / avg_dn if avg_dn > 0 else 100
    rsi[5] = 100 - 100 / (1 + rs)
    for i in range(6, n):
        avg_up = (avg_up * 5 + up[i]) / 6
        avg_dn = (avg_dn * 5 + dn[i]) / 6
        rs = avg_up / avg_dn if avg_dn > 0 else 100
        rsi[i] = 100 - 100 / (1 + rs)
    return rsi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--hold-days', type=int, default=20)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--thresholds', type=str, default='5,10,15,20,30')
    parser.add_argument('--write-md', action='store_true')
    args = parser.parse_args()

    thresholds = [int(t) for t in args.thresholds.split(',')]

    basic = pd.read_parquet('data/history/stock_basic/stock_basic.parquet')
    all_codes = basic['ts_code'].str[:6].tolist()
    if args.limit:
        all_codes = all_codes[:args.limit]
    print(f'RSI6 回测 | {len(all_codes)} 只 | 持有 {args.hold_days} 天 | 阈值 {thresholds}')

    # 先一次性算每只的 RSI6 (避免重复算)
    code_rsi = {}
    t0 = time.time()
    for ci, code in enumerate(all_codes):
        if ci % 500 == 0:
            print(f'  [{ci}/{len(all_codes)}] 耗时 {time.time()-t0:.0f}s', flush=True)
        df = get_kline(code)
        if df is None or len(df) < 60: continue
        closes = df['close'].values
        dates = df['trade_date'].values
        rsi = rsi6(closes)
        code_rsi[code] = (closes, dates, rsi)

    print(f'RSI 预算完成: {len(code_rsi)} 只 ({time.time()-t0:.0f}s)\n')

    # 跑每个阈值
    summary = []
    trade_db = {t: [] for t in thresholds}
    for thr in thresholds:
        for code, (closes, dates, rsi) in code_rsi.items():
            for i in range(30, len(closes) - args.hold_days):
                if np.isnan(rsi[i]) or rsi[i] >= thr: continue
                buy = closes[i + 1] if i + 1 < len(closes) else closes[i]
                sell_idx = i + args.hold_days
                if sell_idx >= len(closes): break
                ret = (closes[sell_idx] - buy) / buy * 100
                future_high = max(closes[i+1:sell_idx+1])
                future_low = min(closes[i+1:sell_idx+1])
                max_gain = (future_high - buy) / buy * 100
                max_dd = (future_low - buy) / buy * 100
                trade_db[thr].append({
                    'code': code, 'date': dates[i], 'rsi6': rsi[i],
                    'buy': buy, 'sell': closes[sell_idx],
                    'max_gain': max_gain, 'max_dd': max_dd, 'ret': ret,
                })
        n = len(trade_db[thr])
        if n == 0:
            summary.append({'thr': thr, 'n': 0})
            continue
        wins = sum(1 for tr in trade_db[thr] if tr['max_gain'] >= 5)
        losses = sum(1 for tr in trade_db[thr] if tr['max_dd'] <= -5)
        total = wins + losses
        wr5 = wins/total*100 if total else 0
        expected = (wins*5 + losses*-5) / n
        avg_ret = sum(tr['ret'] for tr in trade_db[thr]) / n
        wins3 = sum(1 for tr in trade_db[thr] if tr['ret'] > 0)
        wr_pos = wins3/n*100
        summary.append({
            'thr': thr, 'n': n,
            'wr5': wr5, 'wr_pos': wr_pos,
            'expected': expected, 'avg_ret': avg_ret,
            'wins5': wins, 'losses5': losses, 'total5': total,
        })

    # 输出
    print(f"{'阈值':<8}{'笔数':<8}{'5%/5% 胜率':<14}{'持股胜率':<12}{'期望':<10}{'平均终收益':<10}")
    print('-' * 70)
    for s in summary:
        if s['n'] == 0:
            print(f"RSI6<{s['thr']:<6}{0:<8}{'—':<14}{'—':<12}{'—':<10}{'—':<10}")
        else:
            print(f"RSI6<{s['thr']:<6}{s['n']:<8}{s['wr5']:<14.1f}{s['wr_pos']:<12.1f}{s['expected']:<+10.2f}{s['avg_ret']:<+10.2f}")

    # 推荐阈值 (max expected) 写入文件
    if args.write_md:
        valid = [s for s in summary if s['n'] > 0]
        if valid:
            best = max(valid, key=lambda s: s['expected'])
            out_path = ROOT / 'docs' / 'rsi6-backtest-trades.md'
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(f'# RSI6 超卖反弹回测 (持有 {args.hold_days} 天)\n\n')
                f.write(f'> 推荐阈值: **RSI6 < {best["thr"]}** (单笔期望 {best["expected"]:+.2f}%)\n\n')
                f.write('## 阈值对比\n\n')
                f.write('| 阈值 | 笔数 | 5%/5% 胜率 | 持股胜率 | 单笔期望 | 平均终 |\n')
                f.write('|---|---|---|---|---|---|\n')
                for s in summary:
                    if s['n'] == 0:
                        f.write(f"| RSI6<{s['thr']} | 0 | — | — | — | — |\n")
                    else:
                        f.write(f"| RSI6<{s['thr']} | {s['n']} | {s['wr5']:.1f}% | {s['wr_pos']:.1f}% | {s['expected']:+.2f}% | {s['avg_ret']:+.2f}% |\n")
                # 推荐阈值明细 (前 200 笔)
                best_trades = trade_db[best['thr']]
                f.write(f'\n## RSI6<{best["thr"]} 明细 (前 200 / 总 {len(best_trades)})\n\n')
                f.write('| 代码 | 触发日 | RSI6 | 买入价 | max_gain | max_dd | 持有终 |\n')
                f.write('|---|---|---|---|---|---|---|\n')
                for t in best_trades[:200]:
                    f.write(f"| {t['code']} | {t['date']} | {t['rsi6']:.1f} | ¥{t['buy']:.2f} | "
                            f"{t['max_gain']:+.2f}% | {t['max_dd']:+.2f}% | {t['ret']:+.2f}% |\n")
                f.write(f'\n(总 {len(best_trades)} 笔, 仅显示前 200)\n')
            print(f'\n📄 {out_path}')


if __name__ == '__main__':
    main()
