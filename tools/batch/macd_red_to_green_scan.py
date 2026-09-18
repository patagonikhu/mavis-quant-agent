#!/usr/bin/env python3
"""MACD 红转绿 信号: 红柱 ≥ N 天 + 最近 2 根 K 线翻绿 (刚翻绿)

用法:
    python -m tools.batch.macd_red_to_green_scan                    # 默认红柱 ≥ 20 天
    python -m tools.batch.macd_red_to_green_scan --min-red 30       # 红柱 ≥ 30 天
    python -m tools.batch.macd_red_to_green_scan --write-md         # 写 docs/red-to-green-watchlist.md
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


def get_kline(code, limit=200):
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


def check_red_to_green(code, name, min_red_days, last_date):
    """单只扫描: 红柱 ≥ N 天 + 最近 2 根 K 线翻绿"""
    df = get_kline(code)
    if df is None or len(df) < 60: return None
    closes = df['close'].values
    bars = _macd_bar(closes)
    if len(bars) < 3: return None
    if not (bars[-1] > 0 and bars[-2] > 0): return None  # 最近 2 根必须翻绿
    cur_red_days = 0
    for i in range(len(bars)-3, -1, -1):
        if bars[i] < 0:
            cur_red_days += 1
        else:
            break
    if cur_red_days < min_red_days: return None
    return {
        'code': code,
        'name': name,
        'date': df['trade_date'].iloc[-1],
        'close': closes[-1],
        'red_days': cur_red_days,
        'cur_bar': bars[-1],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--min-red', type=int, default=20, help='红柱最短天数 (默认 20)')
    parser.add_argument('--write-md', action='store_true', help='写 docs/red-to-green-watchlist.md')
    args = parser.parse_args()

    basic = pd.read_parquet('data/history/stock_basic/stock_basic.parquet')
    name_map = dict(zip(basic['ts_code'].str[:6], basic['name']))
    all_codes = basic['ts_code'].str[:6].tolist()
    print(f'扫描 {len(all_codes)} 只 | 红柱 ≥ {args.min_red} 天 + 最近2 根翻绿')

    t0 = time.time()
    hits = []
    for code in all_codes:
        r = check_red_to_green(code, name_map.get(code, ''), args.min_red, None)
        if r: hits.append(r)

    elapsed = time.time() - t0
    hits.sort(key=lambda x: x['red_days'], reverse=True)
    print(f'\n=== 完成 ({elapsed:.0f}s) ===')
    print(f'命中: {len(hits)} 只\n')
    print(f'{"代码":>8}{"名称":>10}{"日期":>10}{"close":>10}{"红柱天":>7}{"现bar":>10}')
    for h in hits:
        print(f"{h['code']:>8}{h['name'][:8]:>8}{h['date']:>10}{h['close']:>10.2f}{h['red_days']:>7}{h['cur_bar']:>+10.3f}")

    if args.write_md and hits:
        out_path = ROOT / 'docs' / 'red-to-green-watchlist.md'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')
        md = [f'# MACD 红转绿信号 ({today})\n\n']
        md.append(f'> 全市场扫描 | 红柱持续 ≥ **{args.min_red} 天** + **最近 2 根 K 线翻绿**\n\n')
        md.append(f'**{len(hits)} 只命中**\n\n')
        md.append('| 代码 | 名称 | 触发日 | 价格 | 红柱天 | 现bar |\n')
        md.append('|---|---|---|---|---|---|\n')
        for h in hits:
            md.append(f"| {h['code']} | {h['name']} | {h['date']} | ¥{h['close']:.2f} | {h['red_days']} | {h['cur_bar']:+.3f} |\n")
        md.append('\n**信号含义**: 之前持续下跌 ≥ 20 天,刚刚转绿 2 天. 这是**最早的抄底信号**,但也可能假反转.\n')
        out_path.write_text(''.join(md), encoding='utf-8')
        print(f'\n📄 {out_path}')


if __name__ == '__main__':
    main()