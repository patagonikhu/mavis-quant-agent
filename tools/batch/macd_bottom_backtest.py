#!/usr/bin/env python3
"""MACD 红柱底反弹信号 5 年回测

策略: 红柱持续 ≥ 5 天 + 柱子顶点已过 ≥ 2 天 + bar_diff 转稳 (3日均值 ≥ -0.05)
      + BOLL% < 15 + BBW < 10 + OBV 触底 (obv5 或 obv_trend)

回测: 触发当天收盘价买入, 持有 20 天 (1 个月), 看胜率 (max_gain >= 5%)
"""
import time
import numpy as np
import pandas as pd
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

def get_kline(code, limit=1250):
    from tools.storage.store import read_kline, _to_ts_code
    rows = read_kline(_to_ts_code(code), limit=limit)
    if not rows: return None
    df = pd.DataFrame(rows)
    df['trade_date'] = df['trade_date'].astype(str).str.replace('-', '').str[:8]
    return df.sort_values('trade_date').reset_index(drop=True)

def _sliding_ma(arr, n):
    if len(arr) < n: return [None] * len(arr)
    out = [None] * len(arr)
    s = sum(arr[:n])
    out[n-1] = s / n
    for i in range(n, len(arr)):
        s += arr[i] - arr[i-n]
        out[i] = s / n
    return out

def _boll_pct_width(closes, n=20, k=2.0):
    sma = _sliding_ma(closes, n)
    std = [None] * len(closes)
    for i in range(n-1, len(closes)):
        m = sum(closes[i-n+1:i+1]) / n
        var = sum((c - m)**2 for c in closes[i-n+1:i+1]) / n
        std[i] = var ** 0.5
    pct_b = [None] * len(closes)
    width_pct = [None] * len(closes)
    for i in range(n-1, len(closes)):
        if sma[i] and std[i] is not None and sma[i] > 0:
            upper = sma[i] + k * std[i]
            lower = sma[i] - k * std[i]
            if upper > lower:
                pct_b[i] = (closes[i] - lower) / (upper - lower) * 100
                width_pct[i] = (upper - lower) / sma[i] * 100
    return pct_b, width_pct

def _obv_arr(closes, vols):
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:   obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i-1]: obv.append(obv[-1] - vols[i])
        else:                          obv.append(obv[-1])
    return obv

def _macd_bar(closes):
    """MACD bar 数组"""
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

def check_signal(i, closes, vols, bars, boll_pct, boll_width, obv, obv_ma20):
    """检查第 i 天是否触发红柱底反弹信号"""
    if i < 60: return None
    bp = boll_pct[i]
    bw = boll_width[i]
    if bp is None or bw is None: return None
    if bp >= 15 or bw >= 10: return None
    # OBV
    if i >= 5:
        obv5 = closes[i] < closes[i-5] and obv[i] > obv[i-5]
    else:
        obv5 = False
    obv_trend = obv_ma20[i] is not None and obv[i] > obv_ma20[i]
    if not (obv5 or obv_trend): return None
    # 红柱
    if bars[i] >= 0: return None
    # 红柱持续 ≥ 5 天
    cur_red_days = 0
    for j in range(i, -1, -1):
        if bars[j] < 0:
            cur_red_days += 1
        else:
            break
    if cur_red_days < 5: return None
    # 红柱区间起点
    red_start = i - cur_red_days + 1
    red_seg = bars[red_start:i+1]
    bar_min = min(red_seg)
    bar_min_pos = red_seg.index(bar_min)
    if (cur_red_days - 1 - bar_min_pos) < 2: return None
    # bar_diff 3 日均值
    bar_diff = [0.0]
    for j in range(1, len(bars)):
        bar_diff.append(bars[j] - bars[j-1])
    avg_diff_3d = sum(bar_diff[i-2:i+1]) / 3.0
    if avg_diff_3d < -0.05: return None
    # 通过
    return {
        'red_days': cur_red_days,
        'bar_min_pos': bar_min_pos / max(1, cur_red_days - 1),
        'cur_diff': bar_diff[i],
        'avg_diff_3d': avg_diff_3d,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--hold-days', type=int, default=20, help='持有天数 (默认20)')
    parser.add_argument('--max-signals-per-code', type=int, default=5, help='每只票最多回测多少次')
    parser.add_argument('--limit', type=int, default=0, help='只跑前N只股票')
    args = parser.parse_args()

    basic = pd.read_parquet('data/history/stock_basic/stock_basic.parquet')
    all_codes = basic['ts_code'].str[:6].tolist()
    if args.limit:
        all_codes = all_codes[:args.limit]
    print(f'回测 {len(all_codes)} 只股票')
    print(f'参数: 持有 {args.hold_days} 天, 每只票最多 {args.max_signals_per_code} 次信号')

    all_trades = []
    t0 = time.time()

    for idx, code in enumerate(all_codes):
        df = get_kline(code)
        if df is None or len(df) < 200: continue
        closes = df['close'].values
        vols = df['vol'].values
        dates = df['trade_date'].values

        boll_pct, boll_width = _boll_pct_width(closes)
        obv = _obv_arr(closes, vols)
        obv_ma20 = _sliding_ma(obv, 20)
        bars = _macd_bar(closes)

        signal_count = 0
        for i in range(60, len(closes) - args.hold_days):
            if signal_count >= args.max_signals_per_code:
                break
            sig = check_signal(i, closes, vols, bars, boll_pct, boll_width, obv, obv_ma20)
            if sig is None: continue
            # 持有 N 天看收益
            buy_price = closes[i + 1] if i + 1 < len(closes) else closes[i]  # 次日开盘近似收盘
            sell_idx = i + args.hold_days
            if sell_idx >= len(closes): break
            sell_price = closes[sell_idx]
            future_high = max(closes[i+1:sell_idx+1])
            future_low = min(closes[i+1:sell_idx+1])
            max_gain = (future_high - buy_price) / buy_price * 100
            max_dd = (future_low - buy_price) / buy_price * 100
            ret = (sell_price - buy_price) / buy_price * 100
            all_trades.append({
                'code': code, 'trigger_date': dates[i],
                'red_days': sig['red_days'],
                'cur_diff': sig['cur_diff'],
                'buy_price': buy_price, 'sell_price': sell_price,
                'max_gain': max_gain, 'max_dd': max_dd, 'ret': ret,
                'quality': 'high' if sig['cur_diff'] > 0 else 'normal',
            })
            signal_count += 1

        if (idx + 1) % 500 == 0:
            print(f'  [{idx+1}/{len(all_codes)}] 用时 {time.time()-t0:.0f}s, 累计交易 {len(all_trades)}')

    elapsed = time.time() - t0
    df_tr = pd.DataFrame(all_trades)
    print(f'\n=== 完成 ({elapsed:.0f}s) ===')
    print(f'总交易数: {len(df_tr)}')

    if df_tr.empty:
        print('无信号触发')
        return

    # === 整体胜率 ===
    print('\n=== 整体统计 ===')
    for thresh in [3, 5, 8, 10, 15]:
        wins = (df_tr['max_gain'] >= thresh).sum()
        losses = (df_tr['max_dd'] <= -thresh).sum()
        win_rate = wins / len(df_tr) * 100
        avg_gain = df_tr['max_gain'].mean()
        avg_dd = df_tr['max_dd'].mean()
        avg_ret = df_tr['ret'].mean()
        print(f'  涨 ≥ {thresh}% 算赢: 胜率 {win_rate:.1f}% ({wins}/{len(df_tr)}) | '
              f'止损率 (跌≤{thresh}%) {losses/len(df_tr)*100:.1f}% | '
              f'平均区间涨 {avg_gain:+.2f}% / 跌 {avg_dd:+.2f}% / 持有终 {avg_ret:+.2f}%')

    # === 按信号质量分 ===
    print('\n=== 按信号质量分 (high=bar_diff 已转正, normal=未转正) ===')
    for q in ['high', 'normal']:
        sub = df_tr[df_tr['quality'] == q]
        if sub.empty: continue
        wins = (sub['max_gain'] >= 5).sum()
        win_rate = wins / len(sub) * 100
        avg_gain = sub['max_gain'].mean()
        avg_dd = sub['max_dd'].mean()
        print(f'  {q}: n={len(sub)}  胜率(≥5%) {win_rate:.1f}%  涨 {avg_gain:+.2f}% / 跌 {avg_dd:+.2f}%')

    # === 按红柱天数分 ===
    print('\n=== 按红柱持续天数分 ===')
    for lo, hi in [(5, 10), (11, 20), (21, 30), (31, 999)]:
        sub = df_tr[(df_tr['red_days'] >= lo) & (df_tr['red_days'] <= hi)]
        if sub.empty: continue
        wins = (sub['max_gain'] >= 5).sum()
        win_rate = wins / len(sub) * 100
        avg_gain = sub['max_gain'].mean()
        avg_ret = sub['ret'].mean()
        print(f'  {lo:3d}~{hi:3d}天: n={len(sub):4d}  胜率(≥5%) {win_rate:.1f}%  '
              f'涨 {avg_gain:+.2f}% / 持有终 {avg_ret:+.2f}%')

    # === 5% 止盈 / 5% 止损 策略 ===
    print('\n=== 5% 止盈 / 5% 止损 策略期望 ===')
    wins_list = []
    losses_list = []
    for _, tr in df_tr.iterrows():
        # 持有期内的 bar 序列
        i = np.where(dates == tr['trigger_date'])[0]
        if len(i) == 0: continue
        i = i[0]
        # 找止盈/止损触发点
        buy_price = tr['buy_price']
        sell_at_5 = None
        stop_at_5 = None
        # 简化: 用 max_gain / max_dd 判定
        if tr['max_gain'] >= 5:
            sell_at_5 = True
        if tr['max_dd'] <= -5:
            stop_at_5 = True
        if sell_at_5 and (not stop_at_5):
            wins_list.append(5.0)
        elif stop_at_5:
            losses_list.append(-5.0)
    total_trades = len(wins_list) + len(losses_list)
    if total_trades > 0:
        win_rate = len(wins_list) / total_trades * 100
        avg_ret = (sum(wins_list) + sum(losses_list)) / len(df_tr)
        print(f'  出场: {total_trades}/{len(df_tr)} = {total_trades/len(df_tr)*100:.1f}%')
        print(f'  胜率 (止盈占出场): {win_rate:.1f}%')
        print(f'  单笔期望 (÷全部): {avg_ret:+.2f}%')

    # 保存交易明细
    out_path = ROOT / 'docs' / 'macd-bottom-backtest-trades.md'
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(f'# MACD 红柱底反弹信号 回测明细\n\n')
        f.write(f'> 总交易: {len(df_tr)} | 持有: {args.hold_days} 天\n\n')
        f.write(f'{"代码":<8}{"触发日":<12}{"红柱天":<6}{"质量":<8}{"买入价":<10}{"max_gain":<10}{"max_dd":<10}{"持有终":<10}\n')
        for _, tr in df_tr.iterrows():
            f.write(f"{tr['code']:<8}{tr['trigger_date']:<12}{tr['red_days']:<6}"
                    f"{tr['quality']:<8}¥{tr['buy_price']:<9.2f}"
                    f"{tr['max_gain']:>+9.2f}% {tr['max_dd']:>+9.2f}% {tr['ret']:>+9.2f}%\n")
    print(f'\n📄 {out_path}')

if __name__ == '__main__':
    main()