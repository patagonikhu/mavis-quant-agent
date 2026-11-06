"""fallback2 的 slope_60 > 5 门槛要不要放宽到 3? (只读评估, 不改生产代码)

问题: fallback2 是兜底路径, 入口条件是
    channel == 'none' AND slope_60 > 5 AND trend_30 > 5 AND 有活跃子事件
`slope_60 > 5` 意味着「近 61 根净涨超 5%」。放宽到 3 会多放进来什么? 收益几何?

本脚本 (一次 judge 遍历同时算两套门槛, 差异只在 channel=='none' 且 3<slope_60<=5):
  1) 1 次 SQL 拿全市场 5 年 K 线 (642 万行, ~1.3s)
  2) 每只票算一次 build_kline_features + 一次 scan_sub_events (全段扫, O(n))
     → 再对每根 bar 调 wyckoff_judge (O(1)) 拿完整 stage 序列
  3) 滑窗复现 V13 (当前是 Markup + 40 根内 M 占比 <10% + 单日涨跌 <=20%)
  4) 转变日 (trans_idx) 收盘买入 → 未来 N 天最大涨幅 / 到期涨幅
  5) **按 (股票, 转变日) 去重** —— 滑窗会把同一个转变日重复记多次
     (不���重会虚增样本量, 实测 raw 101845 → unique 51460, 重复 50385 条)

跑法: bash tools/with_venv.sh python -m tools.batch._eval_fb2_slope
"""
import sys
import time
import argparse
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import duckdb

from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
from tools.factors.kline_arrays import build_kline_features

# 与 tools/batch/wyckoff_a2m_scan.py 的 V13 口径一致
V13_WINDOW = 40
M_RATIO_MAX = 0.10
MAX_DAILY_CHG = 0.20
MIN_KLINE = 220

# 放宽档位: slope_60 落在 (LO, HI] 区间且其余 fallback2 条件满足 → 改判 Markup
SLOPE_LO = 3.0
SLOPE_HI = 5.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30, help='持有天数')
    ap.add_argument('--threshold', type=float, default=10.0, help='命中率阈值 (%)')
    ap.add_argument('--lookback', type=int, default=5, help='回看年数')
    ap.add_argument('--limit', type=int, default=0, help='0 = 全市场')
    ap.add_argument('--out', default='/tmp/bt_fb2_slope.json')
    args = ap.parse_args()

    bars = 243 * args.lookback

    print(f'=== 回测参数: 持仓 {args.days} 天 / 涨幅阈值 {args.threshold}% / '
          f'回看 {args.lookback} 年 ===', flush=True)

    t0 = time.time()
    df_all = duckdb.execute(f"""
        WITH ranked AS (
            SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
            FROM read_parquet('data/history/daily/*.parquet')
        )
        SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg
        FROM ranked WHERE rn <= {bars}
        ORDER BY ts_code, trade_date
    """).fetchdf()
    print(f'K线加载: {len(df_all)} 行 / {df_all["ts_code"].nunique()} 只 '
          f'({time.time() - t0:.1f}s)', flush=True)

    df_all['date'] = df_all['trade_date']
    df_all['volume'] = df_all['vol']

    groups = list(df_all.groupby('ts_code'))
    if args.limit:
        groups = groups[:args.limit]

    def stage_series(code, sub):
        """返回 (stages_5, stages_3, dates, closes, highs)"""
        closes = sub['close'].tolist()
        highs = sub['high'].tolist()
        lows = sub['low'].tolist()
        vols = sub['volume'].tolist()
        opens = sub['open'].tolist()
        pct = sub['pct_chg'].tolist()
        dates = sub['date'].astype(str).str.replace('-', '').str[:8].tolist()
        n = len(closes)
        if n < MIN_KLINE:
            return None
        arrs = build_kline_features(closes, highs, lows, vols, window=min(250, n))
        se = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct,
                             period_label='daily', code=code, as_of_idx=None,
                             dates=dates, precomputed_arrs=arrs)
        s5, s3 = [], []
        for i in range(n):
            sub_set = {e['name'] for e in se if e['idx'] <= i}
            out = wyckoff_judge(i, arrs, sub_set, window=arrs['window'],
                                period_label='daily')
            if out is None:
                s5.append('?')
                s3.append('?')
                continue
            st = out.get('stage', '?')
            s5.append(st)
            ch = out.get('channel', '')
            if st == 'Markup' or ch != 'none':
                s3.append(st)
                continue
            fv = out.get('factor_values', {})
            slope = fv.get('slope_60', 0.0) or 0.0
            t30 = fv.get('trend_30', 0.0) or 0.0
            nev = fv.get('active_sub_event_count', 0) or 0
            if ch == 'none' and SLOPE_LO < slope <= SLOPE_HI and t30 > 5 and nev >= 1:
                s3.append('Markup')
            else:
                s3.append(st)
        return s5, s3, dates, closes, highs

    def scan_signals(stages, dates, closes):
        """滑窗复现 V13 → [(转变日 date, 转变日 idx, 今天 idx)]"""
        out = []
        n = len(stages)
        for t in range(V13_WINDOW - 1, n):
            if t + args.days >= n:
                continue
            win = stages[t - V13_WINDOW + 1: t + 1]
            if win[-1] != 'Markup':
                continue
            m_cnt = sum(1 for s in win if s == 'Markup')
            if m_cnt / len(win) >= M_RATIO_MAX:
                continue
            last_chg = abs(closes[t] / closes[t - 1] - 1) if closes[t - 1] > 0 else 0
            if last_chg > MAX_DAILY_CHG:
                continue
            trans_idx = 0
            for k in range(len(win) - 1, 0, -1):
                if win[k - 1] != 'Markup':
                    trans_idx = k
                    break
            gidx = t - V13_WINDOW + 1 + trans_idx
            out.append((dates[gidx], gidx, t))
        return out

    def fwd(closes, highs, gidx):
        """转变日收盘买入 → 未来 days 天 (最大涨幅%, 到期涨幅%)"""
        buy = closes[gidx]
        fut_h = highs[gidx + 1: gidx + 1 + args.days]
        fut_c = closes[gidx + 1: gidx + 1 + args.days]
        if not fut_h:
            return None
        return (max(fut_h) / buy - 1) * 100, (fut_c[-1] / buy - 1) * 100

    results = {'slope5': [], 'slope3': [], 'only_slope3': []}
    t1 = time.time()
    done = 0
    for ts_code, sub in groups:
        code = ts_code.split('.')[0]
        r = stage_series(ts_code, sub)
        if r is None:
            continue
        s5, s3, dates, closes, highs = r
        sig5 = scan_signals(s5, dates, closes)
        sig3 = scan_signals(s3, dates, closes)
        set5 = {(d, g) for d, g, _ in sig5}
        for label, sig in (('slope5', sig5), ('slope3', sig3)):
            for d, g, _t in sig:
                f = fwd(closes, highs, g)
                if f is None:
                    continue
                results[label].append({'code': code, 'date': d,
                                       'max': f[0], 'end': f[1]})
        for d, g, _t in sig3:
            if (d, g) not in set5:
                f = fwd(closes, highs, g)
                if f:
                    results['only_slope3'].append({'code': code, 'date': d,
                                                  'max': f[0], 'end': f[1]})
        done += 1
        if done % 200 == 0:
            print(f'  [{done}/{len(groups)}] {time.time() - t1:.0f}s', flush=True)

    print(f'\n扫描完成: {done} 只 / {time.time() - t1:.1f}s', flush=True)

    def dedup(rows):
        """按 (股票, 转变日) 去重 —— 滑窗会把同一转变日重复记多次"""
        seen = {}
        for r in rows:
            seen[(r['code'], r['date'])] = r
        return list(seen.values())

    def agg(rows):
        mx = np.array([r['max'] for r in rows])
        en = np.array([r['end'] for r in rows])
        return (len(rows), float((mx >= args.threshold).mean() * 100),
                float(mx.mean()), float(np.median(mx)),
                float(en.mean()), float((en < 0).mean() * 100))

    def stat(name, rows):
        if not rows:
            print(f'  {name}: 无样本')
            return None
        n, hit, mmean, mmed, emean, neg = agg(rows)
        print(f'  {name}: n={n:>5} | 命中率(≥{args.threshold:.0f}%) {hit:>5.1f}% | '
              f'最大涨幅 均 {mmean:>+6.2f}% 中位 {mmed:>+6.2f}% | '
              f'到期 均 {emean:>+6.2f}% | 负收益 {neg:>4.1f}%')
        return n, hit, mmean, mmed, emean, neg

    print('\n=== 回测结果 (按 股票+转变日 去重) ===')
    s5 = dedup(results['slope5'])
    s3 = dedup(results['slope3'])
    o3 = dedup(results['only_slope3'])
    print(f'  (原始条数 slope5={len(results["slope5"])} / slope3={len(results["slope3"])} '
          f'/ 新增={len(results["only_slope3"])})')
    a = stat('slope>5 (现状) ', s5)
    b = stat('slope>3 (放宽) ', s3)
    c = stat('仅放宽新增的  ', o3)
    if a and b and c:
        print(f'  → 信号量 {(b[0] / a[0] - 1) * 100:+.1f}% | '
              f'命中率 {b[1] - a[1]:+.1f}pp | 到期 {b[4] - a[4]:+.2f}pp | '
              f'新增部分命中率相对现状 {c[1] - a[1]:+.1f}pp')

    print('\n=== 同期对照: 现状 vs 仅放宽新增 (按转变年份) ===')
    b5 = defaultdict(list)
    b3 = defaultdict(list)
    for r in s5:
        b5[r['date'][:4]].append(r)
    for r in o3:
        b3[r['date'][:4]].append(r)
    print(f'{"年":<6}{"现状n":>8}{"命中率":>8}{"到期":>9}  |{"新增n":>7}{"命中率":>8}{"到期":>9}  |{"Δ命中":>8}')
    for y in sorted(set(b5) | set(b3)):
        x = agg(b5[y]) if b5.get(y) else (0, 0, 0, 0, 0, 0)
        z = agg(b3[y]) if b3.get(y) else (0, 0, 0, 0, 0, 0)
        print(f'{y:<6}{x[0]:>8}{x[1]:>7.1f}%{x[4]:>+9.2f}  |{z[0]:>7}{z[1]:>7.1f}%{z[4]:>+9.2f}  |{z[1] - x[1]:>+8.1f}')

    c5 = {r['code'] for r in s5}
    c3 = {r['code'] for r in s3}
    print(f'\n=== 覆盖度 ===')
    print(f'  slope5 覆盖 {len(c5)} 只 | slope3 覆盖 {len(c3)} 只 | '
          f'新增覆盖 {len(c3 - c5)} 只')

    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(results, fh, ensure_ascii=False)
    print(f'\n明细已存: {args.out}')


if __name__ == '__main__':
    main()
