"""
blowout 历史回测: 对每个历史季报跑 3 rule OR,模拟当季发布后 30 天股价表现.

2026-09-23 加: 验证 blowout rule 在历史的胜率/收益.

用法:
    bash tools/with_venv.sh python -m tools.batch.backtest_blowout
        # 默认: 2021Q2-2026Q2, 持有 30 天, 写入 docs/blowout-backtest.md

    ... --hold-days 60         # 改持有期
    ... --rev-yoy 25 --np-yoy 50   # 用旧阈值
    ... --start 2023Q1 --end 2025Q4  # 自定义区间
"""
import sys, json
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '.')

import duckdb
import pandas as pd
from tools.storage.store import DataStore


def compute_blowout_mask(fin_q: pd.DataFrame,
                         rev_growth_th: float, np_growth_th: float,
                         gm_tol: float, np_jump_th: float, np_surge_floor: float,
                         np_floor: float = 0.0) -> pd.Series:
    """对单季 fin_q (含 prev/prev2/prev4 LAG) 算 3 rule OR mask"""
    fin_q = fin_q.copy()
    fin_q['rev_growth']    = fin_q['or_yoy']         >= rev_growth_th
    fin_q['np_growth']     = fin_q['netprofit_yoy']  >= np_growth_th
    fin_q['gm_qoq_stable'] = (fin_q['grossprofit_margin'] > fin_q['gm_prev']) | \
                             ((fin_q['grossprofit_margin'] - fin_q['gm_prev']).abs() <= gm_tol)
    fin_q['gm_yoy_stable'] = (fin_q['grossprofit_margin'] > fin_q['gm_prev4']) | \
                             ((fin_q['grossprofit_margin'] - fin_q['gm_prev4']).abs() <= gm_tol)
    fin_q['np_jump']       = fin_q['netprofit_yoy'] - fin_q['np_prev']
    fin_q['reversal']      = (fin_q['np_jump'] >= np_jump_th) & fin_q['np_prev'].notna()
    fin_q['leader']        = (fin_q['or_yoy'] >= 80) & (fin_q['netprofit_yoy'] >= 80) & \
                             (fin_q['grossprofit_margin'] > fin_q['gm_prev'])

    rule_main_path = fin_q['rev_growth'] & fin_q['np_growth'] & fin_q['gm_qoq_stable'] & fin_q['gm_yoy_stable'] & (fin_q['reversal'] | fin_q['leader'])
    rule_reversal  = fin_q['rev_growth'] & fin_q['np_growth'] & fin_q['gm_qoq_stable'] & fin_q['gm_yoy_stable'] & fin_q['reversal']
    rule_np_surge  = (fin_q['netprofit_yoy'] > np_surge_floor) & fin_q['gm_qoq_stable'] & fin_q['gm_yoy_stable'] & (fin_q['or_yoy'] >= np_floor)
    return rule_main_path | rule_reversal | rule_np_surge


def main():
    import argparse
    p = argparse.ArgumentParser(description="blowout 历史回测")
    p.add_argument("--start", default="2021Q2")
    p.add_argument("--end", default="2026Q2")
    p.add_argument("--hold-days", type=int, default=30)
    p.add_argument("--rev-yoy", type=float, default=15.0)
    p.add_argument("--np-yoy", type=float, default=20.0)
    p.add_argument("--gm-tol", type=float, default=5.0)
    p.add_argument("--np-jump", type=float, default=30.0)
    p.add_argument("--np-surge-floor", type=float, default=80.0)
    p.add_argument("--out", default="docs/blowout-backtest.md")
    args = p.parse_args()

    con = duckdb.connect()

    # 1) 加载所有 financials (2020Q1 之后够 5 年 + 1)
    files = sorted(list(Path("data/history/financials").glob("20*Q*.parquet")) +
                    list(Path("data/history/financials").glob("202*Q*.parquet")))
    # 过滤 range
    start_q = args.start
    end_q = args.end

    print(f"=== blowout 历史回测 ({start_q} - {end_q}, 持有 {args.hold_days} 天) ===\n")
    print(f"阈值: rev≥{args.rev_yoy}% / np≥{args.np_yoy}% / jump≥{args.np_jump}pp / gm-tol={args.gm_tol}pp / np-surge>{args.np_surge_floor}%\n")

    # 一次加载所有 financials (in-memory LAG)
    all_fin = []
    for f in files:
        try:
            # 先查 schema, 然后 SELECT 字段 (避开 duckdb 列数 binder bug)
            cols = [r[0] for r in con.execute(f"SELECT * FROM read_parquet('{f}') LIMIT 0").description]
            needed = ['ts_code', 'end_date', 'or_yoy', 'netprofit_yoy', 'grossprofit_margin']
            present = [c for c in needed if c in cols]
            if 'or_yoy' not in present or 'netprofit_yoy' not in present:
                continue  # 跳过 schema 不全的文件 (e.g., 2025Q3)
            df = con.execute(f"SELECT {', '.join(present)} FROM read_parquet('{f}')").df()
            all_fin.append(df)
        except Exception as e:
            print(f"  [WARN] 跳过 {f.name}: {str(e)[:80]}")
    big = pd.concat(all_fin).sort_values(['ts_code', 'end_date']).reset_index(drop=True)
    print(f"加载 financials: {len(big)} 行 ({big['end_date'].nunique()} 季)\n")

    # LAG
    big['gm_prev']  = big.groupby('ts_code')['grossprofit_margin'].shift(1)
    big['gm_prev4'] = big.groupby('ts_code')['grossprofit_margin'].shift(4)
    big['np_prev']  = big.groupby('ts_code')['netprofit_yoy'].shift(1)

    # 2) K线不预先加载, 在 SQL 里直接 glob read_parquet 一次性 JOIN

    # 3) 对每个季报跑 rule + 算 hold_days 收益 (用 SQL 一次性 join)
    quarters = sorted(big['end_date'].unique())
    start_y, start_qn = int(start_q[:4]), int(start_q[5:])
    end_y, end_qn = int(end_q[:4]), int(end_q[5:])
    quarters = [q for q in quarters if (int(q[:4]), (int(q[4:6])-1)//3+1) >= (start_y, start_qn) and
                                       (int(q[:4]), (int(q[4:6])-1)//3+1) <= (end_y, end_qn)]

    results = []
    for q in quarters:
        fin_q = big[big['end_date'] == q].copy()
        if fin_q.empty:
            continue

        mask = compute_blowout_mask(fin_q, args.rev_yoy, args.np_yoy,
                                    args.gm_tol, args.np_jump, args.np_surge_floor)
        hits = fin_q[mask][['ts_code', 'netprofit_yoy']].copy()
        if hits.empty:
            results.append({'quarter': q, 'n_hits': 0, 'hold_return': 0})
            continue

        # 用 SQL 计算每只命中票在 hold_days 后的收益 (一次性 JOIN)
        hit_codes = hits['ts_code'].tolist()
        codes_str = ','.join(repr(c) for c in hit_codes)
        q_int = int(q)
        # 计算信号日 (季报日之后的第一个交易日) + hold_days 后收盘价
        ret_df = con.execute(f"""
            WITH ranked AS (
                SELECT
                    a.ts_code,
                    b.trade_date AS sig_date,
                    b.close AS sig_close,
                    ROW_NUMBER() OVER (PARTITION BY a.ts_code ORDER BY b.trade_date) AS rn_sig
                FROM (SELECT UNNEST([{codes_str}]) AS ts_code) a
                JOIN read_parquet('data/history/daily/*.parquet') b
                  ON b.ts_code = a.ts_code
                 AND CAST(b.trade_date AS INTEGER) >= {q_int}
            ),
            future AS (
                SELECT
                    a.ts_code,
                    a.sig_close,
                    b.trade_date AS end_date,
                    b.close AS end_close,
                    ROW_NUMBER() OVER (PARTITION BY a.ts_code ORDER BY b.trade_date) AS rn_fut
                FROM ranked a
                JOIN read_parquet('data/history/daily/*.parquet') b
                  ON b.ts_code = a.ts_code
                 AND b.trade_date > a.sig_date
            )
            SELECT ts_code, sig_close, end_close
            FROM future
            WHERE rn_fut = {args.hold_days}
        """).df()
        # 合并 hits
        for _, r in hits.iterrows():
            code = r['ts_code']
            np_yoy = r['netprofit_yoy']
            m = ret_df[ret_df['ts_code'] == code]
            if m.empty:
                continue
            sig_p = m.iloc[0]['sig_close']
            end_p = m.iloc[0]['end_close']
            ret = (end_p / sig_p - 1) * 100
            results.append({'quarter': q, 'code': code,
                            'sig_p': sig_p, 'end_p': end_p,
                            'hold_return': ret, 'np_yoy': np_yoy})

    # 4) 汇总
    df_res = pd.DataFrame(results)
    if df_res.empty:
        print("无命中,无法回测")
        return

    # 按季聚合
    by_q = df_res.groupby('quarter').agg(
        n_hits=('hold_return', 'count'),
        avg_return=('hold_return', 'mean'),
        median_return=('hold_return', 'median'),
        win_rate=('hold_return', lambda x: (x > 0).mean() * 100),
    ).reset_index()

    # 全局
    all_returns = df_res['hold_return'].dropna()
    print(f"\n=== 全局汇总 ({len(all_returns)} 笔命中) ===")
    print(f"持有 {args.hold_days} 天:")
    print(f"  胜率 (return>0):       {(all_returns > 0).mean() * 100:.1f}%")
    print(f"  平均收益:             {all_returns.mean():+.2f}%")
    print(f"  中位收益:             {all_returns.median():+.2f}%")
    print(f"  最大收益:             {all_returns.max():+.2f}%")
    print(f"  最大亏损:             {all_returns.min():+.2f}%")
    print(f"  收益 > 5%:            {(all_returns > 5).mean() * 100:.1f}%")
    print(f"  收益 > 10%:           {(all_returns > 10).mean() * 100:.1f}%")
    print(f"  亏损 < -10%:          {(all_returns < -10).mean() * 100:.1f}%\n")

    print(f"=== 按季汇总 (top 10 + bottom 5 by avg_return) ===")
    by_q_sorted = by_q.sort_values('avg_return', ascending=False)
    print(by_q_sorted.head(10).to_string(index=False))
    print("---")
    print(by_q_sorted.tail(5).to_string(index=False))

    # 5) 写 md 报告
    md_lines = [f"# Blowout 历史回测 ({start_q} - {end_q})\n\n"]
    md_lines.append(f"> **0 网络**, 走 DataStore (financials + K线 parquet)\n")
    md_lines.append(f"> 阈值: rev≥{args.rev_yoy}% / np≥{args.np_yoy}% / jump≥{args.np_jump}pp / gm-tol={args.gm_tol}pp / np-surge>{args.np_surge_floor}%\n")
    md_lines.append(f"> 持有期: **{args.hold_days} 天** (季报发布日 → +N 天收盘价)\n\n")
    md_lines.append("## 全局汇总\n\n")
    md_lines.append(f"| 指标 | 值 |\n|---|---|\n")
    md_lines.append(f"| 总命中笔数 | {len(all_returns)} |\n")
    md_lines.append(f"| 胜率 (return>0) | **{(all_returns > 0).mean() * 100:.1f}%** |\n")
    md_lines.append(f"| 平均收益 | **{all_returns.mean():+.2f}%** |\n")
    md_lines.append(f"| 中位收益 | {all_returns.median():+.2f}% |\n")
    md_lines.append(f"| 最大收益 | {all_returns.max():+.2f}% |\n")
    md_lines.append(f"| 最大亏损 | {all_returns.min():+.2f}% |\n")
    md_lines.append(f"| 收益>5% 比例 | {(all_returns > 5).mean() * 100:.1f}% |\n")
    md_lines.append(f"| 收益>10% 比例 | {(all_returns > 10).mean() * 100:.1f}% |\n")
    md_lines.append(f"| 亏损<-10% 比例 | {(all_returns < -10).mean() * 100:.1f}% |\n\n")

    md_lines.append("## 按季汇总 (按平均收益降序)\n\n")
    md_lines.append("| 季报 | 命中数 | 平均收益 % | 中位 % | 胜率 % |\n|---|---|---|---|---|\n")
    for _, r in by_q_sorted.iterrows():
        md_lines.append(f"| {r['quarter']} | {int(r['n_hits'])} | {r['avg_return']:+.2f} | {r['median_return']:+.2f} | {r['win_rate']:.1f} |\n")

    md_lines.append("\n## Top 20 命中 (按 hold_return 降序)\n\n")
    top20 = df_res.nlargest(20, 'hold_return')[['quarter', 'code', 'np_yoy', 'sig_p', 'end_p', 'hold_return']]
    md_lines.append("| 季报 | 代码 | 净利 yoy % | 信号价 | 终价 | 收益 % |\n|---|---|---|---|---|---|\n")
    for _, r in top20.iterrows():
        md_lines.append(f"| {r['quarter']} | {r['code']} | {r['np_yoy']:+.1f} | ¥{r['sig_p']:.2f} | ¥{r['end_p']:.2f} | **{r['hold_return']:+.2f}** |\n")

    md_lines.append("\n## Worst 10 命中\n\n")
    bot10 = df_res.nsmallest(10, 'hold_return')[['quarter', 'code', 'np_yoy', 'sig_p', 'end_p', 'hold_return']]
    md_lines.append("| 季报 | 代码 | 净利 yoy % | 信号价 | 终价 | 收益 % |\n|---|---|---|---|---|---|\n")
    for _, r in bot10.iterrows():
        md_lines.append(f"| {r['quarter']} | {r['code']} | {r['np_yoy']:+.1f} | ¥{r['sig_p']:.2f} | ¥{r['end_p']:.2f} | **{r['hold_return']:+.2f}** |\n")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(''.join(md_lines), encoding='utf-8')
    print(f"\n📄 {args.out}")


if __name__ == "__main__":
    main()