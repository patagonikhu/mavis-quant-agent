"""
backtest_magic_filters.py — 小盘股阈值扫描 (v6.2.4)

设计:
  - 复用 backtest_magic.py 的 5 年调仓框架
  - 扫 3 维阈值网格 (min_mcap × max_roc × min_ebit)
  - 每组阈值跑一次完整回测, 算均终值 + 命中率
  - 输出对照表 → docs/backtest-magic-filters.md
"""
from __future__ import annotations

import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import pandas as pd
import duckdb

from tools.storage.store import DataStore
from tools.analysis.valuation import (
    EXCLUDED_INDUSTRIES,
    calc_magic_one_day,
)


# ============================================================
# 调仓日 (跟 backtest_magic.py 一致)
# ============================================================
REBALANCE_DATES = [
    "20220430", "20220930", "20230430", "20230930", "20240430",
    "20240930", "20250430", "20250930", "20260430", "20260903",
]
REBALANCE_FIN_PERIOD = {
    "20220430": "2021Q4", "20220930": "2022Q2", "20230430": "2022Q4",
    "20230930": "2023Q2", "20240430": "2023Q4", "20240930": "2024Q2",
    "20250430": "2024Q4", "20250930": "2025Q2", "20260430": "2025Q4",
    "20260903": "2026Q2",
}
BENCHMARK_CODE = "000300"
HOLD_DAYS = 90
TOP_N = 20

# 阈值扫描网格
THRESHOLD_GRID = []
for mcap in [0, 30, 50, 100, 200]:
    for roc in [9999, 300, 200, 100]:
        for ebit in [0, 1, 2, 3]:
            THRESHOLD_GRID.append((mcap, roc, ebit))


# ============================================================
# 数据加载
# ============================================================
def load_quarter_financials(period: str) -> pd.DataFrame:
    f = Path(f"data/history/financials/{period}.parquet")
    if not f.exists():
        return pd.DataFrame()
    return pd.read_parquet(f)


def load_market_cap_at(date_str: str) -> dict[str, float]:
    q = f"""
    SELECT ts_code, total_mv FROM read_parquet('data/history/daily_basic/*.parquet')
    WHERE trade_date = '{date_str}'
    """
    df = duckdb.execute(q).df()
    return dict(zip(df["ts_code"].str.split(".").str[0], df["total_mv"]))


# v6.2.4 性能优化: 1 次 SQL 拉全市场 K 线, 内存 (565 万行 0.9s, 比预加载快)
_KLINE_INDEX: dict[str, pd.DataFrame] = {}  # code → DataFrame(sort by trade_date)


def build_kline_index() -> int:
    """1 次 SQL 把全市场 K 线分组到 _KLINE_INDEX, 内存查"""
    global _KLINE_INDEX
    df = duckdb.execute("""
        SELECT ts_code, trade_date, close, high
        FROM read_parquet('data/history/daily/*.parquet')
        WHERE trade_date >= '20220101'
    """).df()
    _KLINE_INDEX = {code: g.sort_values("trade_date").reset_index(drop=True)
                    for code, g in df.groupby("ts_code")}
    return len(_KLINE_INDEX)


def calc_returns(code: str, start_date: str, days: int) -> dict | None:
    """单只查 (走 KLINE_INDEX)"""
    # 找后缀 (SH/SZ/BJ)
    g = None
    for suffix in (".SH", ".SZ", ".BJ"):
        candidate = _KLINE_INDEX.get(f"{code}{suffix}")
        if candidate is not None and not candidate.empty:
            g = candidate
            break
    if g is None or g.empty:
        return None
    window = g[g["trade_date"] >= start_date].head(days + 1)
    if len(window) < 2:
        return None
    entry_price = float(window.iloc[0]["close"])
    max_high = float(window["high"].max())
    final_close = float(window.iloc[-1]["close"])
    return {
        "code": code,
        "entry_date": window.iloc[0]["trade_date"],
        "entry_price": entry_price,
        "exit_date": window.iloc[-1]["trade_date"],
        "final_close": final_close,
        "max_high": max_high,
        "max_ret_pct": round((max_high / entry_price - 1) * 100, 2),
        "final_ret_pct": round((final_close / entry_price - 1) * 100, 2),
        "bars_held": len(window) - 1,
    }


# ============================================================
# 排名 (含过滤)
# ============================================================
def rank_at_date_filtered(rebalance_date: str, fin_df: pd.DataFrame,
                          min_mcap: float, max_roc: float, min_ebit: float) -> list[dict]:
    market_caps = load_market_cap_at(rebalance_date)
    if not market_caps:
        return []

    rows = []
    for code, group in fin_df.groupby("ts_code"):
        code6 = code.split(".")[0]
        mc_wan = market_caps.get(code6)
        if not mc_wan or mc_wan <= 0:
            continue
        fins = sorted(group.to_dict("records"), key=lambda r: r.get("end_date", ""))
        if not fins:
            continue
        result = calc_magic_one_day(fins, rebalance_date, mc_wan)
        if result.get("roc") is None or result.get("ey") is None:
            continue

        mc_yi = mc_wan / 1e4
        ebit_yi = (result.get("ebit_yi") or 0)
        roc = result["roc"]
        ey = result["ey"]

        if min_mcap > 0 and mc_yi < min_mcap:
            continue
        if max_roc < 9999 and roc > max_roc:
            continue
        if min_ebit > 0 and ebit_yi < min_ebit:
            continue

        rows.append({
            "code": code6,
            "industry": fins[-1].get("industry", ""),
            "roc": roc,
            "ey": ey,
            "market_cap_yi": mc_yi,
            "ebit_yi": ebit_yi,
        })

    rows = [r for r in rows if r["industry"] not in EXCLUDED_INDUSTRIES]
    if not rows:
        return []

    for rank, r in enumerate(sorted(rows, key=lambda x: x["roc"], reverse=True), 1):
        r["roc_rank"] = rank
    for rank, r in enumerate(sorted(rows, key=lambda x: x["ey"], reverse=True), 1):
        r["ey_rank"] = rank
    for r in rows:
        r["combined_rank"] = (r["roc_rank"] + r["ey_rank"]) / 2
    return sorted(rows, key=lambda x: x["combined_rank"])[:TOP_N]


# ============================================================
# 跑一组阈值 (v6.2.4 性能版: 1 次算全市场 ROC/EY, 存预计算字典)
# ============================================================

# 预计算: {date: [(code, industry, roc, ey, mcap_yi, ebit_yi), ...]}
PRECOMPUTED: dict[str, list[tuple]] = {}


def precompute_all_dates(fin_cache: dict) -> int:
    """对所有调仓日, 1 次 calc_magic_one_day 算全市场, 存 PRECOMPUTED"""
    global PRECOMPUTED
    n_total = 0
    for rb_date in REBALANCE_DATES:
        if rb_date > "20260903" or rb_date < "20220430":
            continue
        period = REBALANCE_FIN_PERIOD[rb_date]
        fin_df = fin_cache[period]
        market_caps = load_market_cap_at(rb_date)

        rows = []
        for code, group in fin_df.groupby("ts_code"):
            code6 = code.split(".")[0]
            mc_wan = market_caps.get(code6)
            if not mc_wan or mc_wan <= 0:
                continue
            fins = sorted(group.to_dict("records"), key=lambda r: r.get("end_date", ""))
            if not fins:
                continue
            result = calc_magic_one_day(fins, rb_date, mc_wan)
            if result.get("roc") is None or result.get("ey") is None:
                continue
            industry = fins[-1].get("industry", "")
            if industry in EXCLUDED_INDUSTRIES:
                continue
            rows.append((
                code6, industry,
                result["roc"], result["ey"],
                mc_wan / 1e4, (result.get("ebit_yi") or 0),
            ))
        PRECOMPUTED[rb_date] = rows
        n_total += len(rows)
        print(f"   {rb_date}: {len(rows)} 只")
    return n_total


def apply_filter_and_rank(date: str, min_mcap: float, max_roc: float, min_ebit: float) -> list[dict]:
    """对预计算结果 in-memory 过滤 + 排名, O(N)"""
    rows = PRECOMPUTED.get(date, [])
    # 过滤
    if min_mcap > 0:
        rows = [r for r in rows if r[4] >= min_mcap]
    if max_roc < 9999:
        rows = [r for r in rows if r[2] <= max_roc]
    if min_ebit > 0:
        rows = [r for r in rows if r[5] >= min_ebit]
    if not rows:
        return []

    # 排名 (ROC 降序, EY 降序)
    roc_sorted = sorted(rows, key=lambda x: x[2], reverse=True)
    ey_sorted = sorted(rows, key=lambda x: x[3], reverse=True)
    roc_rank = {r[0]: i for i, r in enumerate(roc_sorted, 1)}
    ey_rank = {r[0]: i for i, r in enumerate(ey_sorted, 1)}
    combined = []
    for r in rows:
        combined.append({
            "code": r[0],
            "industry": r[1],
            "roc": r[2], "ey": r[3],
            "market_cap_yi": r[4], "ebit_yi": r[5],
            "combined_rank": (roc_rank[r[0]] + ey_rank[r[0]]) / 2,
        })
    return sorted(combined, key=lambda x: x["combined_rank"])[:TOP_N]


def run_one_threshold(min_mcap: float, max_roc: float, min_ebit: float,
                      all_results: list):
    """对所有调仓日, 内存过滤 + 算收益"""
    for rb_date in REBALANCE_DATES:
        if rb_date > "20260903" or rb_date < "20220430":
            continue
        items = apply_filter_and_rank(rb_date, min_mcap, max_roc, min_ebit)
        for item in items:
            ret = calc_returns(item["code"], rb_date, HOLD_DAYS)
            if not ret:
                continue
            all_results.append({
                "min_mcap": min_mcap, "max_roc": max_roc, "min_ebit": min_ebit,
                "date": rb_date, "code": item["code"],
                "industry": item["industry"],
                "roc": item["roc"], "ey": item["ey"],
                "market_cap_yi": item["market_cap_yi"],
                "ebit_yi": item["ebit_yi"],
                **ret,
            })


# ============================================================
# Main
# ============================================================
def main() -> int:
    print("🔬 小盘股阈值扫描 (5 年回测, 90 日持有, Magic Top 20)")
    print(f"   阈值组合: {len(THRESHOLD_GRID)} 组 ({len(REBALANCE_DATES)} 调仓日)\n")

    # 预加载 K 线索引 (1 次 SQL, 0.9s)
    print("📂 建 K 线索引 (1 次 SQL 拉全市场)...")
    n_kline = build_kline_index()
    print(f"   ✅ {n_kline} 只\n")

    # 预加载财务
    print("📂 预加载 10 季财务...")
    fin_cache: dict[str, pd.DataFrame] = {}
    for rb_date, period in REBALANCE_FIN_PERIOD.items():
        df = load_quarter_financials(period)
        if df.empty:
            print(f"   ❌ 缺 {period}")
            return 1
        fin_cache[period] = df
    print(f"   ✅ {len(fin_cache)} 季\n")

    # 预计算全市场 ROC/EY (1 次, 内存)
    print("🧮 预计算全市场 ROC/EY (9 调仓日 × 全市场)...")
    n_total = precompute_all_dates(fin_cache)
    print(f"   ✅ 总计 {n_total} (code,date) 对\n")

    # 跑所有阈值 (in-memory, 应该 < 5s)
    print(f"🔬 跑 {len(THRESHOLD_GRID)} 组阈值 (in-memory)...")
    all_results = []
    for i, (mcap, roc, ebit) in enumerate(THRESHOLD_GRID, 1):
        if i % 20 == 0 or i == 1:
            print(f"   [{i}/{len(THRESHOLD_GRID)}] mcap>={mcap} / ROC<={roc} / EBIT>={ebit} ...")
        run_one_threshold(mcap, roc, ebit, all_results)
    print(f"   ✅ 全部完成, {len(all_results)} 条记录\n")

    if not all_results:
        print("❌ 无结果")
        return 1

    # 算基准
    bench = []
    for rb_date in REBALANCE_DATES:
        if rb_date < "20220430" or rb_date > "20260903":
            continue
        r = calc_returns(BENCHMARK_CODE, rb_date, HOLD_DAYS)
        if r:
            bench.append(r["final_ret_pct"])
    bench_mean = sum(bench) / len(bench) if bench else 0
    print(f"📈 基准 (沪深 300 90 日均终值): {bench_mean:+.2f}%\n")

    # 聚合
    df = pd.DataFrame(all_results)
    summary = df.groupby(["min_mcap", "max_roc", "min_ebit"]).agg(
        n=("code", "count"),
        n_dates=("date", "nunique"),
        avg_mcap=("market_cap_yi", "mean"),
        hit_rate=("max_ret_pct", lambda x: (x >= 10).mean()),
        avg_max=("max_ret_pct", "mean"),
        median_max=("max_ret_pct", "median"),
        avg_final=("final_ret_pct", "mean"),
        median_final=("final_ret_pct", "median"),
    ).round(2)
    summary["alpha_vs_300"] = (summary["avg_final"] - bench_mean).round(2)
    summary = summary.sort_values("alpha_vs_300", ascending=False)
    print("=" * 90)
    print(f"📊 {len(THRESHOLD_GRID)} 组阈值回测 (按 alpha_vs_300 排序, top 30)")
    print("=" * 90)
    print(summary.head(30).to_string())

    # 写盘
    out_csv = Path("docs/backtest-magic-filters.csv")
    out_md = Path("docs/backtest-magic-filters.md")
    df.to_csv(out_csv, index=False)
    print(f"\n✅ 写: {out_csv} ({len(df)} 行)")

    # 写 md
    top20 = summary.head(20)
    lines = [
        "# 小盘股阈值回测 — " + pd.Timestamp.now().strftime("%Y-%m-%d"),
        "",
        f"> **回测**: 5 年 9 调仓日 × 90 日持有 × Magic Top 20",
        f"> **基准**: 沪深 300 90 日均终值 {bench_mean:+.2f}%",
        f"> **扫描**: {len(THRESHOLD_GRID)} 组 (mcap × max_roc × min_ebit)",
        "",
        "## Top 20 推荐阈值 (按 alpha_vs_300 排序)",
        "",
        "| mcap≥ | ROC≤ | EBIT≥ | 样本 | 调仓日 | 均市值(亿) | 命中率 | 均最大 | 均终值 | **Alpha vs 300** |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (mcap, roc, ebit), row in top20.iterrows():
        lines.append(
            f"| {mcap}亿 | {roc}% | {ebit}亿 | {row['n']:.0f} | {row['n_dates']} | "
            f"{row['avg_mcap']:.0f} | {row['hit_rate']*100:.0f}% | "
            f"{row['avg_max']:+.1f}% | {row['avg_final']:+.2f}% | "
            f"**{row['alpha_vs_300']:+.2f}%** |"
        )

    lines.extend([
        "",
        "## 重点对比 (无过滤 vs 经验阈值 vs 严格阈值)",
        "",
    ])
    for key in [(0, 9999, 0), (50, 200, 2), (100, 200, 2), (200, 200, 3), (50, 300, 1), (30, 200, 1)]:
        if key in summary.index:
            row = summary.loc[key]
            lines.append(
                f"- **mcap≥{key[0]}亿 / ROC≤{key[1]}% / EBIT≥{key[2]}亿**: "
                f"n={row['n']:.0f}, 均市值 {row['avg_mcap']:.0f}亿, "
                f"命中率 {row['hit_rate']*100:.0f}%, 均终值 {row['avg_final']:+.2f}%, "
                f"**alpha {row['alpha_vs_300']:+.2f}%**"
            )
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 写: {out_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
