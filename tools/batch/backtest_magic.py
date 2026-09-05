"""
backtest_magic.py — Magic Formula MVP 回测 (v6.2.4)

设计:
  - 季频调仓 (年报披露后第二个月底: 04-30 / 08-31 / 10-31)
  - 每个调仓日: 用最新 ≤ 当日的全年财报 (12-31) 算 EY/ROC
  - 取 EY Top 20 / ROC Top 20 / Magic Top 20 3 种组合
  - 持有 60 / 90 / 120 日, 算最大涨幅
  - 输出: 累计收益 / 平均命中 / 胜率

数据 (0 网络, 全本地):
  - data/history/financials/{YYYYQN}.parquet   (TTM EBIT, NWC, FA, netdebt)
  - data/history/daily_basic/{YYYYQN}.parquet  (季末市值)
  - data/history/daily/*.parquet               (1200 天 K 线, 算持有期收益)

限制:
  - 季频 EY (财务一年 4 个点), 不是日频
  - 财报落盘最早 2024Q2, 所以调仓日从 2024-04-30 起
  - 实际可调仓点: 9 个 (2024Q2 ~ 2026Q2)
"""
from __future__ import annotations

import sys
from pathlib import Path

# 路径兜底 (3 层: batch/ → tools/ → 项目根)
_TOOLS = Path(__file__).resolve().parent.parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import duckdb
import pandas as pd

from tools.storage.store import DataStore
from tools.analysis.valuation import (
    EXCLUDED_INDUSTRIES,
    calc_magic_one_day,
    find_full_year_financials,
)


# ============================================================
# 调仓日 (年报披露后第二个月底)
# ============================================================

# 5 年 × 季频调仓:
#  每年 04-30 (用上一份年报) + 09-30 (用上一份年报 + 当年 H1)
#  实测: 2021Q4 财报是 2022-04-30 才能用 → 调仓日从 2022-04-30 起
#  期末: 2026-09-03 (今天)
REBALANCE_DATES = [
    "20220430",  # 用 2021Q4 财报
    "20220930",  # 用 2022Q2 财报
    "20230430",  # 用 2022Q4 财报
    "20230930",  # 用 2023Q2 财报
    "20240430",  # 用 2023Q4 财报
    "20240930",  # 用 2024Q2 财报
    "20250430",  # 用 2024Q4 财报
    "20250930",  # 用 2025Q2 财报
    "20260430",  # 用 2025Q4 财报
    "20260903",  # 最新
]
# 调仓日 → 对应财务期 (TTM 用该季财报)
REBALANCE_FIN_PERIOD = {
    "20220430": "2021Q4",
    "20220930": "2022Q2",
    "20230430": "2022Q4",
    "20230930": "2023Q2",
    "20240430": "2023Q4",
    "20240930": "2024Q2",
    "20250430": "2024Q4",
    "20250930": "2025Q2",
    "20260430": "2025Q4",
    "20260903": "2026Q2",
}

# 持有期 (天)
HOLD_PERIODS = [60, 90, 120]

# Top N
TOP_N = 20

# 基准指数 (沪深 300, 同期对比)
BENCHMARK_CODE = "000300"


# ============================================================
# 数据加载
# ============================================================

def load_quarter_financials(period: str) -> pd.DataFrame:
    """读某季财务 (全市场)"""
    f = Path(f"data/history/financials/{period}.parquet")
    if not f.exists():
        return pd.DataFrame()
    return pd.read_parquet(f)


def load_market_cap_at(date_str: str) -> dict[str, float]:
    """读某日所有票市值 (单位: 万元, 跟 Tushare daily_basic 一致)

    走 duckdb 直查 parquet, O(1) SQL
    """
    q = f"""
    SELECT ts_code, total_mv
    FROM read_parquet('data/history/daily_basic/*.parquet')
    WHERE trade_date = '{date_str}'
    """
    df = duckdb.execute(q).df()
    return dict(zip(df["ts_code"].str.split(".").str[0], df["total_mv"]))


def get_kline_window(code: str, start_date: str, days: int) -> list[dict]:
    """读 K 线 [start_date, start_date+days], 0 网络"""
    kline = DataStore.get_kline(code)
    if not kline:
        return []
    # 找 start_date 那根或之后第一根
    out = []
    started = False
    for k in kline:
        if not started and k["trade_date"] >= start_date:
            started = True
        if started:
            out.append(k)
            if len(out) >= days:
                break
    return out


# ============================================================
# 排名
# ============================================================

def rank_at_date(rebalance_date: str, fin_df: pd.DataFrame) -> list[dict]:
    """某调仓日: 给全市场算 Magic 排名, 返 EY/ROC/combined 三种 Top N

    fin_df: load_quarter_financials 读出来的全市场财务
    """
    market_caps = load_market_cap_at(rebalance_date)
    if not market_caps:
        return []

    # 按 code group (一只票可能多行: H1 + 全年)
    rows = []
    for code, group in fin_df.groupby("ts_code"):
        code6 = code.split(".")[0]
        mc_wan = market_caps.get(code6)
        if not mc_wan or mc_wan <= 0:
            continue
        # 财务按 end_date 升序
        fins = sorted(group.to_dict("records"), key=lambda r: r.get("end_date", ""))
        if not fins:
            continue
        # 用 calc_magic_one_day (内部已处理 ROC+EY+combined)
        result = calc_magic_one_day(fins, rebalance_date, mc_wan)
        if result.get("roc") is None or result.get("ey") is None:
            continue
        rows.append({
            "code": code6,
            "industry": fins[-1].get("industry", ""),
            "roc": result["roc"],
            "ey": result["ey"],
            "combined": result.get("combined", (result["roc"] + result["ey"]) / 2),
            "market_cap_yi": mc_wan / 1e4,
        })

    if not rows:
        return []

    # 取行业过滤
    rows = [r for r in rows if r["industry"] not in EXCLUDED_INDUSTRIES]

    # 三种排名
    ey_top = sorted(rows, key=lambda r: r["ey"], reverse=True)[:TOP_N]
    roc_top = sorted(rows, key=lambda r: r["roc"], reverse=True)[:TOP_N]
    magic_top = sorted(rows, key=lambda r: r["combined"])[:TOP_N]

    return [
        {"date": rebalance_date, "type": "EY_Top20", "items": ey_top},
        {"date": rebalance_date, "type": "ROC_Top20", "items": roc_top},
        {"date": rebalance_date, "type": "Magic_Top20", "items": magic_top},
    ]


# ============================================================
# 收益计算
# ============================================================

def calc_returns(code: str, start_date: str, days: int) -> dict | None:
    """算某票 [start_date, start_date+days] 收益

    Returns: {entry_date, entry_price, exit_date, exit_price, max_ret, final_ret}
      max_ret: 期间最大涨幅 (high/entry - 1)
      final_ret: 末根收盘/entry - 1
    """
    k = get_kline_window(code, start_date, days + 1)
    if len(k) < 2:
        return None
    entry = k[0]
    highs = [bar["high"] for bar in k[1:]]
    closes = [bar["close"] for bar in k[1:]]
    max_high = max(highs)
    final_close = closes[-1]
    return {
        "code": code,
        "entry_date": entry["trade_date"],
        "entry_price": entry["close"],
        "exit_date": k[-1]["trade_date"],
        "final_close": final_close,
        "max_high": max_high,
        "max_ret_pct": round((max_high / entry["close"] - 1) * 100, 2),
        "final_ret_pct": round((final_close / entry["close"] - 1) * 100, 2),
        "bars_held": len(k) - 1,
    }


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    print("🔬 Magic Formula 5 年真回测 (v6.2.4)")
    print(f"   调仓日: {REBALANCE_DATES}")
    print(f"   持有期: {HOLD_PERIODS} 日")
    print(f"   Top N: {TOP_N}")
    print(f"   基准: {BENCHMARK_CODE} (沪深 300)\n")

    # 预加载所有需要的财务期 (10 季)
    print("📂 预加载财务 (10 季全市场)...")
    fin_cache: dict[str, pd.DataFrame] = {}
    for rb_date, period in REBALANCE_FIN_PERIOD.items():
        df = load_quarter_financials(period)
        if df.empty:
            print(f"   ❌ 缺 {period}, 请跑: python -m tools.storage.sync --financials --all --period {period[:4]}{period[4:]}")
            return 1
        fin_cache[period] = df
    total_codes = sum(df['ts_code'].nunique() for df in fin_cache.values())
    print(f"   10 季总行数 {total_codes}\n")

    # 预加载基准 (沪深 300) K 线
    bench_kline = get_kline_window(BENCHMARK_CODE, "20220101", 1500)
    print(f"📈 基准 K线: {len(bench_kline)} 天 (沪深300)\n")

    # 逐调仓日跑
    all_results = []  # [{date, type, code, days, max_ret, final_ret}]
    bench_results = []  # 基准收益 (沪深 300)
    for rb_date in REBALANCE_DATES:
        # 跳过未来日期
        if rb_date > "20260903":
            print(f"⏭️  {rb_date} (未来, 跳过)")
            continue
        # 跳过 K 线没数据的早日期
        if rb_date < "20210701":
            print(f"⏭️  {rb_date} (K线无, 跳过)")
            continue

        period = REBALANCE_FIN_PERIOD[rb_date]
        fin_df = fin_cache[period]
        print(f"📅 调仓日: {rb_date} (用 {period} 财报, {fin_df['ts_code'].nunique()} 只)")
        ranked = rank_at_date(rb_date, fin_df)
        if not ranked:
            print(f"   ⚠️ 无数据\n")
            continue

        # 基准收益 (同期沪深 300)
        for days in HOLD_PERIODS:
            bench_ret = calc_returns(BENCHMARK_CODE, rb_date, days)
            if bench_ret:
                bench_results.append({
                    "date": rb_date,
                    "days": days,
                    **bench_ret,
                })

        for group in ranked:
            gtype = group["type"]
            items = group["items"]
            print(f"   {gtype}: {len(items)} 只")

            for days in HOLD_PERIODS:
                hits = 0
                total = 0
                rets = []
                for item in items:
                    ret = calc_returns(item["code"], rb_date, days)
                    if not ret:
                        continue
                    total += 1
                    rets.append(ret)
                    if ret["max_ret_pct"] >= 10.0:
                        hits += 1
                    all_results.append({
                        "date": rb_date,
                        "type": gtype,
                        "code": item["code"],
                        "industry": item["industry"],
                        "roc": item["roc"],
                        "ey": item["ey"],
                        "days": days,
                        **ret,
                    })
                if total:
                    avg_max = sum(r["max_ret_pct"] for r in rets) / total
                    print(f"      持 {days}日: {total}只有数据, 命中(>=10%) {hits}, "
                          f"均最大涨幅 {avg_max:+.2f}%")
        print()

    if not all_results:
        print("❌ 无回测结果")
        return 1

    # 汇总
    df = pd.DataFrame(all_results)
    print("=" * 60)
    print("📊 汇总 (按 type × days)")
    print("=" * 60)

    summary = df.groupby(["type", "days"]).agg(
        n=("code", "count"),
        hit_rate=("max_ret_pct", lambda x: (x >= 10).mean()),
        avg_max=("max_ret_pct", "mean"),
        median_max=("max_ret_pct", "median"),
        avg_final=("final_ret_pct", "mean"),
        median_final=("final_ret_pct", "median"),
    ).round(2)
    print(summary.to_string())

    # 写盘
    out_csv = Path("docs/backtest-magic-mvp.csv")
    out_md = Path("docs/backtest-magic-mvp.md")
    df.to_csv(out_csv, index=False)
    print(f"\n✅ 写: {out_csv} ({len(df)} 行)")

    # 简单 md 报告
    lines = [
        "# Magic Formula 5 年回测 — " + pd.Timestamp.now().strftime("%Y-%m-%d"),
        "",
        f"> **调仓日**: {', '.join(REBALANCE_DATES)}",
        f"> **持有期**: {HOLD_PERIODS} 日",
        f"> **Top N**: {TOP_N}",
        f"> **数据**: 10 季历史 financials (TTM 真实, 不再前视) + 本地 K线 (0 网络)",
        f"> **基准**: {BENCHMARK_CODE} 沪深 300",
        "",
        "## 汇总 (按 type × 持有期)",
        "",
        "| 选股 | 持有期 | 样本数 | 命中率 (>=10%) | 均最大涨幅 | 中位最大 | 均终值 | 中位终值 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (t, d), row in summary.iterrows():
        lines.append(
            f"| {t} | {d}日 | {row['n']:.0f} | {row['hit_rate']*100:.1f}% | "
            f"{row['avg_max']:+.2f}% | {row['median_max']:+.2f}% | "
            f"{row['avg_final']:+.2f}% | {row['median_final']:+.2f}% |"
        )

    # 基准对比
    if bench_results:
        bdf = pd.DataFrame(bench_results)
        bsum = bdf.groupby("days").agg(
            n=("code", "count"),
            avg_max=("max_ret_pct", "mean"),
            median_max=("max_ret_pct", "median"),
            avg_final=("final_ret_pct", "mean"),
            median_final=("final_ret_pct", "median"),
        ).round(2)
        lines.extend([
            "",
            "## 基准对比 (沪深 300 同期)",
            "",
            "| 持有期 | 调仓点数 | 均最大涨幅 | 中位最大 | 均终值 | 中位终值 |",
            "|---|---|---|---|---|---|",
        ])
        for d, row in bsum.iterrows():
            lines.append(
                f"| {d}日 | {row['n']:.0f} | {row['avg_max']:+.2f}% | {row['median_max']:+.2f}% | "
                f"{row['avg_final']:+.2f}% | {row['median_final']:+.2f}% |"
            )

        # 关键: alpha = Magic 均终值 - 沪深 300 均终值
        for d in HOLD_PERIODS:
            magic_final = summary.loc[("Magic_Top20", d), "avg_final"] if ("Magic_Top20", d) in summary.index else 0
            bench_final = bsum.loc[d, "avg_final"] if d in bsum.index else 0
            alpha = magic_final - bench_final
            lines.append(f"\n**Alpha (Magic_Top20 均终值 - 沪深300): {d}日 = {alpha:+.2f}%**")

    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 写: {out_md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
