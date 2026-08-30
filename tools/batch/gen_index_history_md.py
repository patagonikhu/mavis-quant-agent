"""gen_index_history_md.py — 生成大盘指数因子历史走势 md

用法:
  python tools/batch/gen_index_history_md.py                       # 默认创业板
  python tools/batch/gen_index_history_md.py --index 000688.SH     # 科创板
  python tools/batch/gen_index_history_md.py --index 000300.SH     # 沪深300
  python tools/batch/gen_index_history_md.py --years 5              # 5 年
  python tools/batch/gen_index_history_md.py --out docs/cyb-history.md
"""
import argparse
import glob
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent.parent

# 指数代码 → 中文名
INDEX_NAMES = {
    "000688.SH": "科创板",
    "399006.SZ": "创业板",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
}


def sliding_mean(arr, period):
    out = [None] * len(arr)
    for i in range(period - 1, len(arr)):
        out[i] = sum(arr[i - period + 1: i + 1]) / period
    return out


def state_of(slope):
    if slope is None:
        return "?"
    if slope > 0.3:
        return "BULL"
    if slope < -0.3:
        return "BEAR"
    return "FLAT"


def main():
    ap = argparse.ArgumentParser(description="生成大盘指数因子历史走势 md")
    ap.add_argument("--index", default="399006.SZ", help="指数代码 (默认 399006.SZ 创业板)")
    ap.add_argument("--years", type=int, default=5, help="回看年数 (默认 5)")
    ap.add_argument("--out", default=None, help="输出文件 (默认 docs/{name}-history.md)")
    args = ap.parse_args()

    name = INDEX_NAMES.get(args.index, args.index)
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = ROOT / "docs" / f"{name}-history.md"

    # 加载数据
    files = sorted(glob.glob(str(ROOT / "data/history/daily/*.parquet")))
    df = pd.concat([pq.read_table(f).to_pandas() for f in files])
    idx = df[df["ts_code"] == args.index].sort_values("trade_date").reset_index(drop=True)
    if idx.empty:
        print(f"❌ 没找到指数 {args.index}")
        return
    idx["trade_date_str"] = idx["trade_date"].astype(str).str.replace("-", "").str[:8]

    # 取最近 N 年
    n_days = args.years * 252
    if len(idx) > n_days:
        idx = idx.iloc[-n_days:].reset_index(drop=True)
    closes = idx["close"].astype(float).tolist()
    dates = idx["trade_date_str"].tolist()
    n = len(closes)

    ma20 = sliding_mean(closes, 20)
    ma60 = sliding_mean(closes, 60)
    ma250 = sliding_mean(closes, min(250, n - 1)) if n > 250 else [None] * n

    # 5 日斜率 (%)
    slope5 = [None] * n
    for i in range(5, n):
        if ma20[i] is not None and ma20[i - 5] is not None and ma20[i - 5] != 0:
            slope5[i] = (ma20[i] - ma20[i - 5]) / ma20[i - 5] / 5 * 100

    # 状态聚合
    date_to_state = {dates[i]: state_of(slope5[i]) for i in range(n) if slope5[i] is not None}
    date_to_slope = {dates[i]: slope5[i] for i in range(n) if slope5[i] is not None}

    idx["ym"] = idx["trade_date_str"].str[:6]
    monthly = idx.groupby("ym").agg(
        open_=("close", "first"),
        close_=("close", "last"),
        high=("close", "max"),
        low_=("close", "min"),
    ).reset_index()
    monthly["ret"] = (monthly["close_"] / monthly["open_"] - 1) * 100
    monthly["bull_days"] = 0
    monthly["flat_days"] = 0
    monthly["bear_days"] = 0
    monthly["last_slope"] = 0.0

    for ym in monthly["ym"]:
        mask = idx["ym"] == ym
        states_in_month = [date_to_state.get(d) for d in idx.loc[mask, "trade_date_str"] if d in date_to_state]
        monthly.loc[monthly["ym"] == ym, "bull_days"] = states_in_month.count("BULL")
        monthly.loc[monthly["ym"] == ym, "flat_days"] = states_in_month.count("FLAT")
        monthly.loc[monthly["ym"] == ym, "bear_days"] = states_in_month.count("BEAR")
        last_day = idx.loc[mask, "trade_date_str"].iloc[-1]
        monthly.loc[monthly["ym"] == ym, "last_slope"] = date_to_slope.get(last_day, 0.0)

    # 5 年总览
    bull_total = sum(1 for s in slope5 if s is not None and s > 0.3)
    flat_total = sum(1 for s in slope5 if s is not None and -0.3 <= s <= 0.3)
    bear_total = sum(1 for s in slope5 if s is not None and s < -0.3)
    total_with_state = bull_total + flat_total + bear_total

    # 写 md
    md = []
    md.append(f"# {name}指数 ({args.index}) 因子历史走势\n")
    md.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    md.append(f"> 数据范围: {dates[0]} ~ {dates[-1]} ({n} 个交易日, ~{args.years} 年)")
    md.append(f"> 起始点位: {closes[0]:.2f} | 末点位: {closes[-1]:.2f} | "
              f"累计涨跌: {(closes[-1]/closes[0]-1)*100:+.1f}%\n")
    md.append(f"## 当前状态\n")
    md.append(f"| 指标 | 值 |")
    md.append(f"|---|---|")
    md.append(f"| close | {closes[-1]:.2f} |")
    if ma20[-1]: md.append(f"| MA20 | {ma20[-1]:.2f} (偏离: {(closes[-1]-ma20[-1])/ma20[-1]*100:+.2f}%) |")
    if ma60[-1]: md.append(f"| MA60 | {ma60[-1]:.2f} (偏离: {(closes[-1]-ma60[-1])/ma60[-1]*100:+.2f}%) |")
    if ma250[-1]: md.append(f"| MA250 | {ma250[-1]:.2f} (偏离: {(closes[-1]-ma250[-1])/ma250[-1]*100:+.2f}%) |")
    md.append(f"| MA20 5日斜率 | **{slope5[-1]:+.3f}%/日** |")
    md.append(f"| 当前状态 | **{state_of(slope5[-1])}** (🟢 BULL >+0.3% / ⚪ FLAT ±0.3% / 🔴 BEAR <-0.3%) |")
    md.append(f"| 多/空头排列 | {'多头 MA20>MA60' if ma20[-1] and ma60[-1] and ma20[-1]>ma60[-1] else '空头 MA20<MA60'} |")
    md.append(f"")

    # 5 年状态分布
    md.append(f"## {args.years} 年市场状态分布\n")
    md.append(f"| 状态 | 天数 | 占比 |")
    md.append(f"|---|---|---|")
    md.append(f"| 🟢 BULL (主升, 斜率 > +0.3%) | {bull_total} | {bull_total/total_with_state*100:.1f}% |")
    md.append(f"| ⚪ FLAT (震荡, -0.3% ~ +0.3%) | {flat_total} | {flat_total/total_with_state*100:.1f}% |")
    md.append(f"| 🔴 BEAR (熊市, 斜率 < -0.3%) | {bear_total} | {bear_total/total_with_state*100:.1f}% |")
    md.append(f"| **总计** | **{total_with_state}** | 100% |")
    md.append(f"")

    # 实战启示
    if bull_total/total_with_state < 0.2:
        md.append(f"⚠️ **实战启示**: {name} {args.years} 年里只有 {bull_total/total_with_state*100:.0f}% 是主升期, "
                  f"{(flat_total+bear_total)/total_with_state*100:.0f}% 时间震荡/熊市。**BB+OBV 类趋势策略只适合主升期**。\n")
    md.append(f"")

    # 按年汇总
    md.append(f"## 年度汇总\n")
    md.append(f"| 年份 | 起点 | 终点 | 涨跌 | BULL日 | FLAT日 | BEAR日 |")
    md.append(f"|---|---|---|---|---|---|---|")
    idx["year"] = idx["trade_date_str"].str[:4]
    yearly = idx.groupby("year").agg(
        open_=("close", "first"),
        close_=("close", "last"),
    ).reset_index()
    yearly["ret"] = (yearly["close_"] / yearly["open_"] - 1) * 100
    yearly["bull_days"] = 0
    yearly["flat_days"] = 0
    yearly["bear_days"] = 0
    for y in yearly["year"]:
        mask = idx["year"] == y
        states_in_year = [date_to_state.get(d) for d in idx.loc[mask, "trade_date_str"] if d in date_to_state]
        yearly.loc[yearly["year"] == y, "bull_days"] = states_in_year.count("BULL")
        yearly.loc[yearly["year"] == y, "flat_days"] = states_in_year.count("FLAT")
        yearly.loc[yearly["year"] == y, "bear_days"] = states_in_year.count("BEAR")
    for _, r in yearly.iterrows():
        icon = "🟢" if r["ret"] > 0 else ("🔴" if r["ret"] < 0 else "⚪")
        md.append(f"| {r['year']} | {r['open_']:.0f} | {r['close_']:.0f} | {icon} {r['ret']:+.1f}% | "
                  f"{int(r['bull_days'])} | {int(r['flat_days'])} | {int(r['bear_days'])} |")
    md.append(f"")

    # 月度表 (近 36 个月)
    md.append(f"## 月度走势 (近 36 个月)\n")
    md.append(f"| 月份 | 开盘 | 收盘 | 最高 | 最低 | 月涨跌 | 末状态 | BULL | FLAT | BEAR |")
    md.append(f"|---|---|---|---|---|---|---|---|---|---|")
    for _, r in monthly.tail(36).iterrows():
        icon = "🟢" if r["ret"] > 0 else ("🔴" if r["ret"] < 0 else "⚪")
        s = state_of(r["last_slope"])
        s_icon = {"BULL": "🟢", "FLAT": "⚪", "BEAR": "🔴"}.get(s, "?")
        md.append(f"| {r['ym']} | {r['open_']:.0f} | {r['close_']:.0f} | {r['high']:.0f} | {r['low_']:.0f} | "
                  f"{icon} {r['ret']:+.1f}% | {s_icon} {s} | {int(r['bull_days'])} | {int(r['flat_days'])} | {int(r['bear_days'])} |")
    md.append(f"")

    # 最近 60 天日线
    md.append(f"## 最近 60 个交易日 (实战参考)\n")
    md.append(f"| 日期 | 收盘 | MA20 | MA60 | 5日斜率 | 状态 |")
    md.append(f"|---|---|---|---|---|---|")
    last60 = list(zip(dates, closes, ma20, ma60, slope5))[-60:]
    for d, c, m20, m60, sl in last60:
        s = state_of(sl)
        s_icon = {"BULL": "🟢", "FLAT": "⚪", "BEAR": "🔴"}.get(s, "?")
        sl_str = f"{sl:+.3f}%/日" if sl is not None else "—"
        m20_str = f"{m20:.0f}" if m20 is not None else "—"
        m60_str = f"{m60:.0f}" if m60 is not None else "—"
        md.append(f"| {d} | {c:.0f} | {m20_str} | {m60_str} | {sl_str} | {s_icon} {s} |")
    md.append(f"")

    # MA20 5日斜率公式
    md.append(f"## MA20 5 日斜率公式\n")
    md.append(f"```")
    md.append(f"slope(%) = (MA20[今天] - MA20[5天前]) / MA20[5天前] / 5 × 100")
    md.append(f"```\n")
    md.append(f"| 斜率 | 状态 | 实战 |")
    md.append(f"|---|---|---|")
    md.append(f"| > +0.3%/日 | 🟢 BULL | 启用 BB+OBV, 满仓 |")
    md.append(f"| -0.3 ~ +0.3%/日 | ⚪ FLAT | 观望或减半仓 |")
    md.append(f"| < -0.3%/日 | 🔴 BEAR | 空仓 / 防御 |")
    md.append(f"")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"✅ 已生成: {out_path}")
    print(f"   {n} 个交易日, {len(monthly)} 个月, {len(yearly)} 年")
    print(f"   当前状态: {state_of(slope5[-1])}, 斜率 {slope5[-1]:+.3f}%/日")


if __name__ == "__main__":
    main()
