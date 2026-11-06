#!/usr/bin/env python
"""分析 /t-wyckoff-a2m 过滤条件的门槛效果 (全市场漏斗模拟)

2026-10-28: 用户想改过滤条件但不确定方向/阈值, 用数据分布来定。
本脚本只做「前置过滤链」漏斗模拟 (ST → PE → quality → 新候选条件),
不跑 A→M 判定 (那是 wyckoff_judge_v12 的事, 44s)。

结论 (已落地到 wyckoff_a2m_scan.py):
- exclude-pe 80 → 40
- 新增 min-roe 5 (默认) / min-gross-margin 20 (默认) / max-net-debt-ratio 0.5 (默认)
- 效果: 22 只 → 9 只, 9 只全是"便宜且干净"那批, 扫描 44s → 11s

⚠️ 顺带发现的质量门槛语义 bug (2026-10-28 已在代码里澄清):
  `grossprofit_margin` 是**毛利率水平值 (绝对 %)**, 不是毛利率同比增速.
  旧代码把 quality 档位门槛 (0/10/20) 直接传给它, 导致 "strong=10" 实际是
  "毛利率 > 10%" 而非文档写的 "毛利率增速 > 10%". financials 表没有毛利率 yoy 字段.

用法:
    bash tools/with_venv.sh python -m tools.batch.analyze_a2m_filters

⚠️ 2026-11-05 修 (跟 wyckoff_a2m_scan.py 同源 bug, 不修会让调参结论失真):
  - stk_factor 原来取 `WHERE trade_date = (SELECT MAX(trade_date) ...)` (全局最新那一天)
    → Tushare 当天陆续发布 (20261105 实测只有 2387/5572 只) → 缺数据的票 JOIN 不上,
      **杠杆/PE 分布直接少 3185 只**, 算出来的 P90/P50 门槛是错的。
    改成 per-code latest (ROW_NUMBER PARTITION BY ts_code)。
  - financials 季份原来写死 2026Q2 + 2025Q4 → 等 2026Q3 落盘后落后一季, 跟生产过滤链不一致。
    改成动态取 (最新季 + 上一季兜底), 跟 wyckoff_a2m_scan._financials_quarters 同口径。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import duckdb  # noqa: E402
import pandas as pd  # noqa: E402

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 60)

BASIC = "data/history/stock_basic/stock_basic.parquet"


def _stk_glob() -> str:
    """stk_factor 最近 2 个季度 parquet 的 glob 表达式 (不硬编码季度)"""
    from tools.storage.store import STK_FACTOR_DIR
    files = sorted(STK_FACTOR_DIR.glob("*.parquet"))
    if not files:
        return str(STK_FACTOR_DIR / "*.parquet")
    return "[" + ", ".join(f"'{f}'" for f in files[-2:]) + "]"


def _fin_quarters(n: int = 2) -> list[str]:
    """最近 N 个已落盘财务季 parquet (不硬编码季度, 跟 wyckoff_a2m_scan 同口径)"""
    from tools.storage.store import FIN_DIR
    files = sorted(FIN_DIR.glob("*.parquet")) if FIN_DIR.exists() else []
    return [str(f) for f in files[-n:]]


# 22 只 A→M 命中 (2026-10-28 扫描结果, **历史快照**), 用来看新门槛会砍掉谁
# ⚠️ 这不是当前命中集 (2026-11-05 实测 19 只, 成员已变) —— 当"历史对照"用, 别当现状
HITS_22 = {
    "300519", "300832", "301101", "000538", "000810", "002380", "600323", "601766",
    "601808", "603337", "603387", "603901", "603967", "688410", "688469", "001219",
    "002421", "300406", "300428", "301048", "301109", "603683",
}


def load() -> pd.DataFrame:
    """1 次 SQL 拿全市场: code + name + industry + PE + PB + 市值 + 财务指标。"""
    quarters = _fin_quarters(2)
    if not quarters:
        raise RuntimeError("本地没有 financials parquet, 先跑 /t-sync-data --financials")
    q1 = quarters[-1]
    fin_sql = f"""
        SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, roe, netdebt,
               debt_to_assets, ebit, fixed_assets, networking_capital
        FROM read_parquet('{q1}', union_by_name=true)
        WHERE fetch_status = 'ok'
    """
    if len(quarters) > 1:
        q2 = quarters[-2]
        fin_sql += f"""
        UNION ALL
        SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, roe, netdebt,
               debt_to_assets, ebit, fixed_assets, networking_capital
        FROM read_parquet('{q2}', union_by_name=true)
        WHERE fetch_status = 'ok'
          AND ts_code NOT IN (SELECT ts_code FROM read_parquet('{q1}', union_by_name=true)
                              WHERE fetch_status = 'ok')
        """
    df = duckdb.execute(f"""
    WITH sf AS (
        -- per-code latest: 每只票自己最新那天的估值 (不是全局 MAX(trade_date))
        SELECT ts_code, pe, pe_ttm, pb, total_mv
        FROM (
            SELECT ts_code, pe, pe_ttm, pb, total_mv, trade_date,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
            FROM read_parquet({_stk_glob()})
        ) WHERE rn = 1
    ),
    sb AS (
        SELECT ts_code, name, industry FROM read_parquet('{BASIC}')
    ),
    fin AS ({fin_sql}
    )
    SELECT
        split_part(sf.ts_code, '.', 1) AS code,
        sb.name AS name,
        sb.industry AS industry,
        sf.pe, sf.pe_ttm, sf.pb, sf.total_mv,
        fin.grossprofit_margin AS gm,
        fin.or_yoy, fin.netprofit_yoy AS np_yoy,
        fin.roe, fin.netdebt, fin.debt_to_assets,
        fin.ebit, fin.fixed_assets, fin.networking_capital
    FROM sf
    LEFT JOIN sb ON sb.ts_code = sf.ts_code
    LEFT JOIN fin ON fin.ts_code = sf.ts_code
    """).fetchdf()
    # 净负债 / 市值 比率 (亿)
    df["mc_yi"] = df["total_mv"] / 1e4  # 万元 → 亿元
    df["nd_yi"] = df["netdebt"] / 1e8
    df["nd_ratio"] = df["nd_yi"] / df["mc_yi"]
    # 投入资本 (NWC + FA), 跟 ROC 分母同口径
    df["capital_yi"] = ((df["networking_capital"].fillna(0) + df["fixed_assets"].fillna(0)) / 1e8)
    df["is_st"] = df["name"].fillna("").str.contains("ST", regex=False)
    return df


def funnel(df: pd.DataFrame, pe_max: float, quality_thr: float) -> pd.DataFrame:
    """按过滤链逐步过滤, 返回每步剩余票。"""
    steps = []
    cur = df[~df["is_st"]].copy()
    steps.append(("exclude-st", len(df), len(cur)))

    if pe_max > 0:
        before = len(cur)
        cur = cur[~(cur["pe"].fillna(0) > pe_max) | (cur["pe"].fillna(0) <= 0)]
        steps.append((f"exclude-pe {pe_max:.0f}", before, len(cur)))

    before = len(cur)
    cur = cur[(cur["or_yoy"] > quality_thr) & (cur["gm"] > quality_thr) & (cur["np_yoy"] > quality_thr)]
    steps.append((f"quality basic(>{quality_thr:.0f})", before, len(cur)))
    return cur, steps


def main():
    print("🔄 加载全市场数据 (1 次 SQL)...")
    df = load()
    print(f"   全市场 {len(df)} 票 | 有财务 {df['gm'].notna().sum()} | 有 PE {df['pe'].notna().sum()}")

    # ---- 1. 关键指标的全市场分布 ----
    print("\n" + "=" * 78)
    print("【1】关键指标全市场分布 (有财务数据的票)")
    print("=" * 78)
    for col, label in [("roe", "ROE %"), ("gm", "毛利率 %"),
                       ("pe", "PE"), ("nd_ratio", "净负债/市值")]:
        s = df[col].dropna()
        if col == "pe":
            s = s[s > 0]
        if s.empty:
            continue
        qs = s.quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        print(f"\n  {label}  (n={len(s)})")
        print(f"    min={s.min():.2f}  P10={qs[0.1]:.2f}  P25={qs[0.25]:.2f}  "
              f"中位={qs[0.5]:.2f}  P75={qs[0.75]:.2f}  P90={qs[0.9]:.2f}  max={s.max():.2f}")

    # ---- 2. 候选门槛的漏斗效果 ----
    print("\n" + "=" * 78)
    print("【2】门槛漏斗 (PE 上限 × quality 档)")
    print("=" * 78)
    header = f"{'配置':<34}{'剩余票':>9}{'命中率*':>10}"
    print(header)
    print("-" * 78)
    configs = [
        ("当前默认 (pe80 + basic)", 80, 0),
        ("pe50 + basic", 50, 0),
        ("pe40 + basic", 40, 0),
        ("pe30 + basic", 30, 0),
        ("pe80 + strong(>10)", 80, 10),
        ("pe50 + strong(>10)", 50, 10),
        ("pe40 + strong(>10)", 40, 10),
        ("pe30 + strong(>10)", 30, 10),
        ("pe40 + elite(>20)", 40, 20),
        ("pe30 + elite(>20)", 30, 20),
    ]
    for name, pe, q in configs:
        cur, _ = funnel(df, pe, q)
        # 假设命中率先用 22/1470 ≈ 1.5% 估
        est = len(cur) * 0.015
        print(f"{name:<34}{len(cur):>9}{est:>9.0f} 只*")
    print("* 命中率按当前 22/1470=1.5% 线性估算, 实际命中数需真跑")

    # ---- 3. 新增候选条件 (ROE / 毛利率绝对值 / 杠杆) 各自效果 ----
    print("\n" + "=" * 78)
    print("【3】新增条件单独效果 (在 pe40 + basic 基础上)")
    print("=" * 78)
    base, _ = funnel(df, 40, 0)
    print(f"  基线 (pe40 + basic): {len(base)} 票\n")
    tests = [
        ("ROE >= 3", lambda d: d["roe"] >= 3),
        ("ROE >= 5", lambda d: d["roe"] >= 5),
        ("ROE >= 8", lambda d: d["roe"] >= 8),
        ("毛利率 >= 15", lambda d: d["gm"] >= 15),
        ("毛利率 >= 20", lambda d: d["gm"] >= 20),
        ("毛利率 >= 30", lambda d: d["gm"] >= 30),
        ("净负债/市值 < 0.5", lambda d: d["nd_ratio"] < 0.5),
        ("净负债/市值 < 0.3", lambda d: d["nd_ratio"] < 0.3),
        ("ROE>=5 且 毛利率>=20", lambda d: (d["roe"] >= 5) & (d["gm"] >= 20)),
        ("ROE>=5 且 毛利率>=20 且 杠杆<0.5",
         lambda d: (d["roe"] >= 5) & (d["gm"] >= 20) & (d["nd_ratio"] < 0.5)),
    ]
    for name, fn in tests:
        kept = base[fn(base)]
        print(f"  {name:<34} {len(kept):>6} 票  (砍掉 {len(base)-len(kept)})")

    # ---- 4. 这些门槛对已命中 22 只的影响 ----
    print("\n" + "=" * 78)
    print("【4】对已命中 22 只的影响 (哪些会被砍)")
    print("=" * 78)
    hit = df[df["code"].isin(HITS_22)].copy()
    hit["hit"] = hit["code"].isin(HITS_22)
    cond_tests = [
        ("PE <= 30", hit["pe"] <= 30),
        ("PE <= 40", hit["pe"] <= 40),
        ("ROE >= 5", hit["roe"] >= 5),
        ("ROE >= 8", hit["roe"] >= 8),
        ("毛利率 >= 20", hit["gm"] >= 20),
        ("毛利率 >= 30", hit["gm"] >= 30),
        ("杠杆 净负债/市值 < 0.5", hit["nd_ratio"] < 0.5),
        ("ROE>=5 且 毛利率>=20", (hit["roe"] >= 5) & (hit["gm"] >= 20)),
    ]
    for name, mask in cond_tests:
        kept = hit[mask.fillna(False)]
        cut = [f"{r['name']}({r['code']})" for _, r in hit[~mask.fillna(False)].iterrows()]
        print(f"\n  {name}: 留 {len(kept)}/22")
        if cut:
            print(f"    砍掉: {', '.join(cut)}")

    # ---- 5. 组合推荐 ----
    print("\n" + "=" * 78)
    print("【5】组合方案对比 (剩余票数 + 预计命中 + 保留的 22 只里几只)")
    print("=" * 78)
    combos = [
        ("A 当前默认 pe80+basic", 80, 0, None, None, None),
        ("B pe40+basic", 40, 0, None, None, None),
        ("C pe40+basic+ROE5+GM20", 40, 0, 5, 20, None),
        ("D pe40+basic+ROE5+GM20+杠杆0.5", 40, 0, 5, 20, 0.5),
        ("E pe30+basic+ROE5+GM20", 30, 0, 5, 20, None),
        ("F pe30+strong+ROE5+GM20", 30, 10, 5, 20, None),
    ]
    for name, pe, q, roe, gm, ndr in combos:
        cur, _ = funnel(df, pe, q)
        if roe is not None:
            cur = cur[cur["roe"] >= roe]
        if gm is not None:
            cur = cur[cur["gm"] >= gm]
        if ndr is not None:
            cur = cur[cur["nd_ratio"] < ndr]
        kept_hits = [c for c in HITS_22 if c in set(cur["code"])]
        print(f"\n  {name}")
        print(f"    剩余 {len(cur)} 票 | 预计命中 ~{len(cur)*0.015:.0f} 只 | 22 只里保留 {len(kept_hits)}")


if __name__ == "__main__":
    main()
