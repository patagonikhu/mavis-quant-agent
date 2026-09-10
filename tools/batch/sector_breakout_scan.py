"""
sector_breakout_scan.py
板块行情启动检测 (三大条件):
  1. 板块等权指数站上 MA60 (close > MA60)
  2. 板块成交量放大 (今日 vol > 5日均量 * 1.5)
  3. 板块内 ≥3 只个股同步放量突破 (今日 close>MA20 且 vol>5日均量*1.5 且 pct_chg>3%)

输入: data/history/daily/{季}.parquet + data/history/stock_basic/stock_basic.parquet
输出: 终端 ASCII 表格, 按突破股数降序

用法:
  bash tools/with_venv.sh python tools/batch/sector_breakout_scan.py
  bash tools/with_venv.sh python tools/batch/sector_breakout_scan.py --min-stocks 5
  bash tools/with_venv.sh python tools/batch/sector_breakout_scan.py --pct 5 --vol-mult 2.0
"""
import argparse
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def load_all_daily() -> pd.DataFrame:
    """加载最近 2 季 daily (够 MA60 + 5日均量) — 走 DataStore"""
    from tools.storage.store import DataStore
    return DataStore.load_all_daily_full(years=0.5)


def compute_indicators(daily: pd.DataFrame) -> pd.DataFrame:
    """每只股票算: MA20, MA60, vol_ma5, pct_chg(已有)"""
    daily = daily.sort_values(["ts_code", "trade_date"]).copy()
    daily["ma20"] = daily.groupby("ts_code")["close"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    daily["ma60"] = daily.groupby("ts_code")["close"].transform(
        lambda s: s.rolling(60, min_periods=30).mean()
    )
    daily["vol_ma5"] = daily.groupby("ts_code")["vol"].transform(
        lambda s: s.rolling(5, min_periods=3).mean()
    )
    return daily


def build_industry_index(daily: pd.DataFrame, basic: pd.DataFrame) -> pd.DataFrame:
    """按 industry 分组, 等权合成板块指数 (close 用个股 close 百分比变化累乘)"""
    df = daily.merge(basic[["ts_code", "industry"]], on="ts_code", how="inner")
    df = df.dropna(subset=["industry", "close"])
    df = df.sort_values(["industry", "ts_code", "trade_date"])

    # 个股归一化到 1.0 起点
    df["ret"] = df.groupby("ts_code")["close"].pct_change()
    df["norm"] = df.groupby("ts_code")["ret"].transform(lambda s: (1 + s.fillna(0)).cumprod())
    # 起点的归一化值由 groupby 内部计算, 但 cumprod 跨 group 没问题

    # 等权合成 (按 trade_date x industry 算 norm 平均)
    idx = df.groupby(["industry", "trade_date"]).agg(
        idx_close=("norm", "mean"),
        idx_vol=("vol", "sum"),
        idx_amt=("amount", "sum"),
        n_stocks=("ts_code", "nunique"),
    ).reset_index()

    # 板块级 MA60 / vol_ma5
    idx = idx.sort_values(["industry", "trade_date"])
    idx["idx_ma60"] = idx.groupby("industry")["idx_close"].transform(
        lambda s: s.rolling(60, min_periods=30).mean()
    )
    idx["idx_vol_ma5"] = idx.groupby("industry")["idx_vol"].transform(
        lambda s: s.rolling(5, min_periods=3).mean()
    )
    return idx


def count_breakout_stocks(
    daily_today: pd.DataFrame, basic: pd.DataFrame, pct_min: float, vol_mult: float
) -> pd.DataFrame:
    """今日板块内满足 '放量突破' 的个股数 + 列表

    个股放量突破定义:
      - close > ma20
      - vol > vol_ma5 * vol_mult
      - pct_chg > pct_min
    """
    df = daily_today.merge(basic[["ts_code", "industry", "name"]], on="ts_code", how="inner")
    df = df.dropna(subset=["ma20", "ma60", "vol_ma5"])
    cond = (df["close"] > df["ma20"]) & (df["vol"] > df["vol_ma5"] * vol_mult) & (df["pct_chg"] > pct_min)
    df["is_breakout"] = cond.astype(int)
    out = []
    for ind, sub in df.groupby("industry"):
        bo = sub[sub["is_breakout"] == 1]
        out.append({
            "industry": ind,
            "n_breakout": len(bo),
            "breakout_stocks": ", ".join(
                f"{r['name']}({r['pct_chg']:+.1f}%)"
                for _, r in bo.sort_values("pct_chg", ascending=False).iterrows()
            ),
        })
    return pd.DataFrame(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-stocks", type=int, default=5, help="板块最少股票数 (过滤小板块)")
    parser.add_argument("--pct", type=float, default=3.0, help="个股涨幅阈值 %")
    parser.add_argument("--vol-mult", type=float, default=1.5, help="个股量比阈值 (vs 5日均量)")
    parser.add_argument("--top", type=int, default=30, help="输出板块数")
    args = parser.parse_args()

    print("📥 加载数据 ...")
    daily = load_all_daily()
    from tools.storage.store import DataStore
    basic = DataStore.load_stock_basic()
    print(f"  daily: {len(daily):,} 行,  {daily['ts_code'].nunique()} 只股")
    print(f"  basic: {len(basic):,} 行,  {basic['industry'].nunique()} 个行业")

    print("🔧 计算 MA20/MA60/vol_ma5 ...")
    daily = compute_indicators(daily)

    today = daily["trade_date"].max()
    print(f"📅 数据最新一天: {today}")

    print("🏭 合成板块等权指数 ...")
    idx = build_industry_index(daily, basic)

    today_idx = idx[idx["trade_date"] == today].dropna(subset=["idx_ma60", "idx_vol_ma5"]).copy()

    # 过滤: 板块股票数 >= min_stocks
    today_idx = today_idx[today_idx["n_stocks"] >= args.min_stocks]

    # 三大条件
    today_idx["cond_ma60"] = today_idx["idx_close"] > today_idx["idx_ma60"]
    today_idx["cond_vol"] = today_idx["idx_vol"] > today_idx["idx_vol_ma5"] * args.vol_mult

    # 个股放量突破
    daily_today = daily[daily["trade_date"] == today]
    breakout_df = count_breakout_stocks(daily_today, basic, args.pct, args.vol_mult)
    today_idx = today_idx.merge(breakout_df, on="industry", how="left").fillna({"n_breakout": 0, "breakout_stocks": ""})

    today_idx["cond_breakout"] = today_idx["n_breakout"] >= 3

    # 三条件全过
    today_idx["all_three"] = (
        today_idx["cond_ma60"] & today_idx["cond_vol"] & today_idx["cond_breakout"]
    )

    # 排序: 先看三条件全过, 再按突破股数排
    result = today_idx[today_idx["all_three"]].sort_values(
        ["n_breakout", "n_stocks"], ascending=False
    ).head(args.top)

    print()
    print("=" * 100)
    print(f"🚨 板块行情启动信号 (三条件: MA60✅ + 量比>×{args.vol_mult}✅ + ≥3只放量突破✅)")
    print(f"   个股突破阈值: 涨幅>{args.pct}% + vol>5日均量×{args.vol_mult} + close>MA20")
    print(f"   板块最小股票数: {args.min_stocks} | 输出前 {args.top}")
    print("=" * 100)

    if len(result) == 0:
        print("❌ 今日无板块满足三条件")
        print()
        print("📋 接近满足 (≥1 个条件) 的板块 Top 10:")
        partial = today_idx.copy()
        partial["n_pass"] = (
            partial["cond_ma60"].astype(int) +
            partial["cond_vol"].astype(int) +
            (partial["n_breakout"] >= 3).astype(int)
        )
        partial = partial[partial["n_pass"] >= 1].sort_values(
            ["n_pass", "n_breakout", "n_stocks"], ascending=False
        ).head(10)
        if len(partial) == 0:
            print("  (无数据)")
            return
        for _, r in partial.iterrows():
            flags = (
                ("MA60✅ " if r["cond_ma60"] else "MA60❌ ") +
                (f"量比✅ " if r["cond_vol"] else "量比❌ ") +
                (f"突破×{int(r['n_breakout'])}")
            )
            print(f"  {r['industry']:<10} {flags:<25} 股票数={int(r['n_stocks'])}")
        return

    print(f"{'板块':<10} {'股票数':<6} {'突破数':<6} {'板块涨%':<8} {'量比':<6} {'突破个股':<60}")
    print("-" * 100)
    for _, r in result.iterrows():
        # 板块今日涨幅 (近似: 用 idx_close 较前一日变化)
        prev_close_series = idx[(idx["industry"] == r["industry"]) & (idx["trade_date"] < today)].tail(1)
        if len(prev_close_series) > 0:
            prev = prev_close_series["idx_close"].iloc[0]
            pct = (r["idx_close"] / prev - 1) * 100
        else:
            pct = 0
        vol_ratio = r["idx_vol"] / r["idx_vol_ma5"]
        print(
            f"{r['industry']:<10} {int(r['n_stocks']):<6} {int(r['n_breakout']):<6} "
            f"{pct:+.2f}%   {vol_ratio:.2f}  {r['breakout_stocks'][:60]}"
        )

    print()
    print(f"✅ 共 {len(result)} 个板块触发三条件 (按突破数排序)")


if __name__ == "__main__":
    main()
