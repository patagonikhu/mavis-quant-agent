"""
blowout_backtest_v630.py — v6.3.0 历史回测

在历史披露季跑 v6.3.0 blowout 选股 (用历史 financials), 跟踪未来 N 天表现
对比 v6.2.7 (无 prefilter) 和沪深 300, 验证 v6.3.0 是否跑赢大盘.

用法:
  bash tools/with_venv.sh python -m tools.batch.blowout_backtest_v630
    # 默认 9 季 (2023Q4 ~ 2025Q4) × 30 天持仓
  ... --hold-days 60
  ... --compare-v627
  ... --write-md     # 写 docs/blowout-v630-backtest.md
"""
import argparse
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore  # noqa: E402
from tools.batch.finance_earnings_blowout import (  # noqa: E402
    _load_basic_map, _shift_quarters, _apply_prefilter, _apply_rules,
)


def run_v630_for_quarter(fin_all: pd.DataFrame, bm: dict, sf: pd.DataFrame,
                         target_q: str, use_prefilter: bool, args) -> list[dict]:
    """对单季跑 v6.3.0 (或 v6.2.7 对照) 选股.

    返回 [{code, name, quarter, end_date, disclosure_date_est}, ...]
    """
    q_to_end = {'Q1': '0331', 'Q2': '0630', 'Q3': '0930', 'Q4': '1231'}
    yr, qq = target_q[:4], target_q[4:]
    end_date = f"{yr}{q_to_end[qq]}"

    # 1. 切片到本季 + 之前 12 季 (shift_quarters 要 lag 4 = 1 年)
    fin = fin_all[fin_all['end_date'] <= end_date].copy()
    if len(fin) < 100:
        return []

    fin = _shift_quarters(fin, ['or_yoy','netprofit_yoy','grossprofit_margin','ebit','roe'], lookback_quarters=4)
    fin['or_yoy_meet'] = fin['or_yoy'] >= 15
    fin['netprofit_yoy_meet'] = fin['netprofit_yoy'] >= 20
    fin['gross_margin_qoq_stable'] = (fin['grossprofit_margin'] > fin['grossprofit_margin_last_quarter']) | ((fin['grossprofit_margin'] - fin['grossprofit_margin_last_quarter']).abs() <= 5)
    fin['gross_margin_yoy_stable'] = (fin['grossprofit_margin'] > fin['grossprofit_margin_one_year_ago']) | ((fin['grossprofit_margin'] - fin['grossprofit_margin_one_year_ago']).abs() <= 5)
    fin['np_jump'] = fin['netprofit_yoy'] - fin['netprofit_yoy_last_quarter']
    fin['reversal'] = (fin['np_jump'] >= 50) & fin['netprofit_yoy_last_quarter'].notna()
    fin['lead_revenue'] = fin['or_yoy'] >= 80
    fin['lead_profit'] = fin['netprofit_yoy'] >= 80
    fin['lead_margin'] = fin['grossprofit_margin'] > fin['grossprofit_margin_last_quarter']
    fin['leader'] = fin['lead_revenue'] & fin['lead_profit'] & fin['lead_margin'] & fin['grossprofit_margin_last_quarter'].notna()

    if use_prefilter:
        fin_post = _apply_prefilter(fin, bm, sf, args)
    else:
        fin_post = fin

    target_row = fin_post[fin_post['end_date'] == end_date]
    if target_row.empty:
        return []
    mask, _ = _apply_rules(target_row, revenue_floor=0.0, profit_floor=80.0)
    hits = target_row[mask]
    if hits.empty:
        return []

    end_dt = pd.Timestamp(end_date)
    disc_date = (end_dt + pd.Timedelta(days=50)).strftime('%Y%m%d')

    out = []
    for _, r in hits.iterrows():
        out.append({
            'ts_code': r['ts_code'],
            'code': r['ts_code'].split('.')[0],
            'name': bm.get(r['ts_code'].split('.')[0], {}).get('name', ''),
            'quarter': target_q,
            'end_date': end_date,
            'disclosure_date_est': disc_date,
        })
    return out


def max_return(df: pd.DataFrame, entry_date: str, hold_days: int) -> tuple:
    """从 entry_date 起 hold_days 个交易日的最大涨幅."""
    if df.empty:
        return (None, None)
    entry_dt = pd.Timestamp(entry_date)
    future = df[df['trade_date'] >= entry_dt].head(hold_days + 1)
    if future.empty or len(future) < 2:
        return (None, None)
    entry_close = future.iloc[0]['close']
    future_max = future['high'].max()
    entry_actual_date = future.iloc[0]['trade_date'].strftime('%Y%m%d')
    return ((future_max - entry_close) / entry_close * 100, entry_actual_date)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-quarter", default="2023Q4")
    parser.add_argument("--max-quarter", default="2025Q4")
    parser.add_argument("--hold-days", type=int, default=30)
    parser.add_argument("--compare-v627", action="store_true")
    parser.add_argument("--write-md", action="store_true")
    parser.add_argument("--include-end-quarter-2026", action="store_true",
                        help="包含 2026Q1/Q2 (未来 120 天可能不全)")
    args = parser.parse_args()

    # 1. 列出季
    quarters = []
    for yr in range(int(args.min_quarter[:4]), int(args.max_quarter[:4])+1):
        for qq in ['Q1','Q2','Q3','Q4']:
            q = f"{yr}{qq}"
            if q < args.min_quarter or q > args.max_quarter:
                continue
            quarters.append(q)
    if not args.include_end_quarter_2026 and quarters and quarters[-1] in ['2026Q1', '2026Q2']:
        quarters = [q for q in quarters if q not in ['2026Q1', '2026Q2']]

    print(f"=== v6.3.0 blowout 历史回测 ({len(quarters)} 季: {quarters[0]} ~ {quarters[-1]}) ===")
    print(f"持仓期 {args.hold_days} 天")
    print()

    # 2. 一次性加载 financials / basic_map / 全市场 kline
    t_load = time.time()
    print("⏳ 加载 financials + 全市场 kline (~5s)...")
    fin_all = DataStore.load_all_financials()
    sf = DataStore.get_stk_factor_latest()
    bm = _load_basic_map()
    kline_all = DataStore.load_all_kline(years=10)
    # 一次性转成 DataFrame dict (key 同时记 6 位 + 带后缀, 看哪种都能命中)
    kline_df = {}
    for code_with_suffix, bars in kline_all.items():
        if not bars:
            continue
        code_short = code_with_suffix.split('.')[0]  # '300972.SZ' -> '300972'
        df_bars = pd.DataFrame(bars)
        df_bars['trade_date'] = pd.to_datetime(df_bars['trade_date'])
        df_sorted = df_bars.sort_values('trade_date').reset_index(drop=True)
        kline_df[code_short] = df_sorted
        kline_df[code_with_suffix] = df_sorted  # 万一传带后缀也能命中
    print(f"⏳ 加载完成 ({time.time()-t_load:.1f}s): financials {len(fin_all)} 行, kline {len(kline_df)} 只\n")

    ns = argparse.Namespace(
        cycle_industries='小金属,铜,铝,化工原料,农药化肥,铅锌,矿物制品,钢铁,煤炭,石油,化纤,水运,仓储物流,电器仪表,家用电器,工程机械,石油开采,黄金,塑料,造纸,建材,玻璃,陶瓷,纺织,化纤,电气设备',
        include_st=False, include_cycle=False, include_junk=False,
        min_mv=30.0, min_listing_q=16,
        ebit_floor=-50.0, ebit_peak_kill=-30.0,
    )

    # 3. 每季跑
    all_hits_v630 = []
    all_hits_v627 = []
    for q in quarters:
        hits = run_v630_for_quarter(fin_all, bm, sf, q, use_prefilter=True, args=ns)
        all_hits_v630.extend(hits)
        if args.compare_v627:
            hits_old = run_v630_for_quarter(fin_all, bm, sf, q, use_prefilter=False, args=ns)
            all_hits_v627.extend(hits_old)
        print(f"  {q}: v6.3.0 {len(hits)} 只次"
              + (f" | v6.2.7 (无 prefilter) {len(hits_old)} 只次" if args.compare_v627 else ""))

    if not all_hits_v630:
        print("\n无任何 v6.3.0 命中")
        return

    print(f"\n=== 总计 v6.3.0: {len(all_hits_v630)} 只次 ({len(set(h['quarter'] for h in all_hits_v630))}/{len(quarters)} 季有命中) ===")
    if args.compare_v627:
        print(f"=== 总计 v6.2.7: {len(all_hits_v627)} 只次 ===")

    # 4. 跟踪每只
    def track_returns(hits, kline_df):
        out = []
        for h in hits:
            code = h['code']
            df = kline_df.get(code)
            if df is None or df.empty:
                continue
            ret, entry_date = max_return(df, h['disclosure_date_est'], args.hold_days)
            if ret is None:
                continue
            out.append({**h, 'return': ret, 'entry_date': entry_date})
        return out

    print(f"\n⏳ 跟踪未来 {args.hold_days} 天表现...")
    t_track = time.time()
    v630_returns = track_returns(all_hits_v630, kline_df)
    v627_returns = track_returns(all_hits_v627, kline_df) if args.compare_v627 else []

    # 大盘对比 (沪深 300)
    hs300_df = kline_df.get('000300.SH')
    hs300_benchmarks = []
    for q in quarters:
        hits_q = [h for h in all_hits_v630 if h['quarter'] == q]
        if not hits_q or hs300_df is None:
            continue
        ret, _ = max_return(hs300_df, hits_q[0]['disclosure_date_est'], args.hold_days)
        if ret is not None:
            hs300_benchmarks.append({'quarter': q, 'return': ret})
    print(f"⏳ 跟踪完成 ({time.time()-t_track:.1f}s): v6.3.0 {len(v630_returns)}/{len(all_hits_v630)} 只有完整未来价格")

    # 5. 输出每只
    print(f"\n=== v6.3.0 命中 + {args.hold_days} 天 max 涨幅 ===")
    print(f"{'季':<10}{'代码':<10}{'名称':<10}{'披露日':<12}{'entry日':<12}{'max涨幅':>10}")
    for r in sorted(v630_returns, key=lambda x: -x['return']):
        print(f"{r['quarter']:<10}{r['code']:<10}{r['name'][:8]:<10}{r['disclosure_date_est']:<12}{r['entry_date']:<12}{r['return']:>+8.1f}%")

    # 6. 统计
    def stats(returns, name):
        rets = [r['return'] for r in returns]
        if not rets:
            print(f"\n{name}: 无样本")
            return None
        win = sum(1 for r in rets if r > 0)
        win_pct = win / len(rets) * 100
        avg = sum(rets) / len(rets)
        median = sorted(rets)[len(rets)//2]
        top_decile = sorted(rets, reverse=True)[max(0, len(rets)//10)]
        print(f"\n=== {name} (持仓 {args.hold_days} 天) ===")
        print(f"  样本: {len(rets)} 只")
        print(f"  胜率 (> 0%): {win} / {len(rets)} = {win_pct:.1f}%")
        print(f"  平均 max 涨幅: {avg:+.1f}%")
        print(f"  中位 max 涨幅: {median:+.1f}%")
        print(f"  头部 10% (前 1/10): {top_decile:+.1f}%")
        return {'n': len(rets), 'win_pct': win_pct, 'avg': avg, 'median': median, 'top_decile': top_decile}

    s_v6 = stats(v630_returns, 'v6.3.0')
    s_v62 = stats(v627_returns, 'v6.2.7 (无 prefilter)') if args.compare_v627 else None

    # 大盘
    if hs300_benchmarks:
        avg_b = sum(b['return'] for b in hs300_benchmarks) / len(hs300_benchmarks)
        print(f"\n=== 沪深 300 同期 ({len(hs300_benchmarks)} 季, 持仓 {args.hold_days} 天) ===")
        print(f"  平均 max 涨幅: {avg_b:+.1f}%")
        # 各季明细
        for b in hs300_benchmarks:
            print(f"    {b['quarter']}: {b['return']:+.1f}%")

    # Alpha 对比
    if s_v6 and hs300_benchmarks:
        alpha_v6 = s_v6['avg'] - avg_b
        print(f"\n=== Alpha 对比 (v6.3.0 平均 - 沪深 300 平均) ===")
        print(f"  v6.3.0 平均:    {s_v6['avg']:+.1f}%")
        print(f"  沪深 300 平均:  {avg_b:+.1f}%")
        print(f"  Alpha:           {alpha_v6:+.1f}% pp")
        if alpha_v6 > 5:
            print(f"  ✅ v6.3.0 显著跑赢大盘 (alpha > 5pp)")
        elif alpha_v6 > 0:
            print(f"  ⚠️ v6.3.0 小幅跑赢大盘 ({alpha_v6:.1f}pp)")
        else:
            print(f"  ❌ v6.3.0 跑输大盘 (alpha = {alpha_v6:.1f}pp)")

    if s_v6 and s_v62:
        alpha_compare = s_v6['avg'] - s_v62['avg']
        print(f"\n=== v6.3.0 vs v6.2.7 (同持仓期) ===")
        print(f"  v6.3.0 平均: {s_v6['avg']:+.1f}% ({s_v6['n']} 只)")
        print(f"  v6.2.7 平均: {s_v62['avg']:+.1f}% ({s_v62['n']} 只)")
        print(f"  差异:        {alpha_compare:+.1f}% pp")

    # 7. 写 md
    if args.write_md:
        import datetime
        out_path = ROOT / 'docs' / 'blowout-v630-backtest.md'
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# v6.3.0 blowout 历史回测 ({datetime.datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 测试期: {quarters[0]} ~ {quarters[-1]} ({len(quarters)} 季), 持仓 {args.hold_days} 天\n\n")
        md.append(f"## v6.3.0 选股表现\n\n")
        md.append(f"| 季 | 代码 | 名称 | 披露日 | entry日 | max涨幅 |\n")
        md.append(f"|---|---|---|---|---|---|\n")
        for r in sorted(v630_returns, key=lambda x: -x['return']):
            md.append(f"| {r['quarter']} | {r['code']} | {r['name']} | {r['disclosure_date_est']} | {r['entry_date']} | {r['return']:+.1f}% |\n")
        if s_v6:
            md.append(f"\n**统计**: 样本 {s_v6['n']}, 胜率 {s_v6['win_pct']:.1f}%, 平均 {s_v6['avg']:+.1f}%, 中位 {s_v6['median']:+.1f}%\n")
        if hs300_benchmarks:
            md.append(f"\n**大盘对比 (沪深 300)**: 同期平均 max 涨幅 {avg_b:+.1f}%\n")
            md.append(f"**Alpha**: v6.3.0 - 沪深300 = {s_v6['avg'] - avg_b:+.1f}% pp\n")
        out_path.write_text(''.join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
