"""rotation_v2_backtest.py — 创业板/红利低波 v12 轮动策略回测

策略 (双条件):
  切红利:
    A. 创业板 250 斜率从 120 日内最高降温 ≥ 20pp
       AND 250 斜率 < 10% (还在涨, 但已降温)
       AND close < MA20 (短期已转弱)
    B. 10 日跌 ≥ 10% (急跌) AND close < MA20 AND 250 斜率 > 10%
       (主升后短期急跌, 趋势还没完全破坏)

  切回创业板:
    close > MA60 AND MA60 10 日斜率 > 0.5%
    (明确回升才切回, 避免震荡市反复切)

数据: K线从 DataStore 读, 0 网络
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from tools.storage.store import DataStore


def load_data(code: str) -> pd.DataFrame:
    ctx = DataStore.get_ctx(code)
    rows = []
    for k in ctx.kline:
        d = str(k.get("trade_date", "")).replace("-", "")[:8]
        try:
            rows.append({"dt": d, "close": float(k.get("close", 0))})
        except (TypeError, ValueError):
            continue
    return pd.DataFrame(rows).reset_index(drop=True)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df["ma20"] = df["close"].rolling(20).mean()
    df["ma60"] = df["close"].rolling(60).mean()
    df["ma60_slope_10"] = (df["ma60"] - df["ma60"].shift(10)) / df["ma60"].shift(10) * 100
    df["slope_250"] = (df["close"] - df["close"].shift(250)) / df["close"].shift(250) * 100
    df["slope_250_max_120"] = df["slope_250"].rolling(120).max()
    df["slope_peak_pullback"] = df["slope_250_max_120"] - df["slope_250"]
    df["drawdown_10d"] = (df["close"] - df["close"].shift(10)) / df["close"].shift(10) * 100
    return df


def signal_hold_red(row) -> bool:
    """v12: 双条件切红利"""
    if pd.isna(row["ma20"]) or pd.isna(row["slope_250"]):
        return False
    # A: 主升降温 ≥ 20pp + 250 斜率 < 10% + close < MA20
    cond_a = (row["slope_peak_pullback"] >= 20) and (row["slope_250"] < 10) and (row["close"] < row["ma20"])
    # B: 急跌 10 日 ≥ 10% + close < MA20 + 250 斜率 > 10%
    cond_b = (row["drawdown_10d"] < -10) and (row["close"] < row["ma20"]) and (row["slope_250"] > 10)
    return cond_a or cond_b


def signal_back_cyb(row) -> bool:
    """切回创业板: close > MA60 + MA60 10 日斜率 > 0.5%"""
    if pd.isna(row["ma60"]) or pd.isna(row["ma60_slope_10"]):
        return False
    return (row["close"] > row["ma60"]) and (row["ma60_slope_10"] > 0.5)


def main():
    ap = argparse.ArgumentParser(description="创业板/红利低波 v12 轮动回测")
    ap.add_argument("--lookback", type=int, default=3, help="回看年数")
    ap.add_argument("--write-md", action="store_true", help="写 md")
    args = ap.parse_args()

    cyb = load_data("399006.SZ")  # close 留给 build_features
    red = load_data("930955").rename(columns={"close": "red_close"})
    df = cyb.merge(red, on="dt", how="inner")
    df = build_features(df)
    df["hold_red"] = df.apply(signal_hold_red, axis=1)
    df["back_cyb"] = df.apply(signal_back_cyb, axis=1)
    df["cyb_ret"] = df["close"].pct_change().shift(-1)
    df["red_ret"] = df["red_close"].pct_change().shift(-1)
    df = df.dropna().reset_index(drop=True)

    cutoff = df["dt"].iloc[0]
    if args.lookback:
        from datetime import datetime, timedelta
        # 找 lookback 年前的日期
        all_dates = sorted(df["dt"].unique())
        last_dt = all_dates[-1]
        last_yyyymm = int(last_dt[:6])
        target_yyyymm = last_yyyymm - args.lookback * 100
        # 简化: 用行数
        n_lookback = args.lookback * 250  # 约一年 250 个交易日
        if len(df) > n_lookback:
            df = df.iloc[-n_lookback:].reset_index(drop=True)

    n = len(df)
    print(f"=== 创业板/红利 v12 轮动回测 ({df['dt'].iloc[0]} → {df['dt'].iloc[-1]}, {n} 日) ===")
    print(f"切红利信号: {df['hold_red'].sum()} 日 ({(df['hold_red'].sum()/n*100):.1f}%)")

    # 状态机
    state = "cyb"
    state_log = []
    n_cyb = n_red = 0
    cap_rot = 1.0
    results = []
    for i in range(n - 1):
        cur = df.iloc[i]
        rc = cur["cyb_ret"]
        rr = cur["red_ret"]
        if state == "cyb" and cur["hold_red"]:
            state = "red"
        elif state == "red" and cur["back_cyb"]:
            state = "cyb"
        if state == "cyb":
            cap_rot *= (1 + rc) if not pd.isna(rc) else 1
            n_cyb += 1
        else:
            cap_rot *= (1 + rr) if not pd.isna(rr) else 1
            n_red += 1
        state_log.append((cur["dt"], state))
        results.append((cur["dt"], state, rc, rr))

    cap_cyb = cap_red = 1.0
    for i in range(n - 1):
        cur = df.iloc[i]
        if not pd.isna(cur["cyb_ret"]):
            cap_cyb *= (1 + cur["cyb_ret"])
        if not pd.isna(cur["red_ret"]):
            cap_red *= (1 + cur["red_ret"])

    print(f"\n选创业板: {n_cyb} 日 ({n_cyb/(n-1)*100:.0f}%)")
    print(f"选红利:   {n_red} 日 ({n_red/(n-1)*100:.0f}%)")
    print(f"\n轮动 v12:        {(cap_rot-1)*100:+.2f}%")
    print(f"一直持创业板:    {(cap_cyb-1)*100:+.2f}%")
    print(f"一直持红利低波:  {(cap_red-1)*100:+.2f}%")

    print(f"\n=== 按年 ===")
    print(f"{'年':<6}{'轮动':<10}{'创业板':<10}{'红利':<10}{'cyb日':<8}{'red日':<8}")
    for y in ["2024", "2025", "2026"]:
        y_rows = [(d, st, rc, rr) for d, st, rc, rr in results
                  if d[:4] == y and not pd.isna(rc) and not pd.isna(rr)]
        if not y_rows:
            continue
        cap_r = cap_c = cap_red_y = 1.0
        n_cyb_y = n_red_y = 0
        for d, st, rc, rr in y_rows:
            if st == "cyb":
                cap_r *= (1 + rc); n_cyb_y += 1
            else:
                cap_r *= (1 + rr); n_red_y += 1
            cap_c *= (1 + rc)
            cap_red_y *= (1 + rr)
        print(f"  {y:<6}{(cap_r-1)*100:>+8.1f}%  {(cap_c-1)*100:>+8.1f}%  {(cap_red_y-1)*100:>+8.1f}%  {n_cyb_y:<8}{n_red_y:<8}")

    print(f"\n=== 状态切换 ===")
    prev_state = "cyb"
    for d, st in state_log:
        if st == "red" and prev_state == "cyb":
            print(f"  {d}: 🔴 切到红利")
        elif st == "cyb" and prev_state == "red":
            print(f"  {d}: 🟢 切回创业板")
        prev_state = st

    if args.write_md:
        from datetime import datetime as dt_cls
        out_path = ROOT / "docs" / "backtest-rotation-v12.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# 创业板/红利低波 v12 轮动回测 ({dt_cls.now().strftime('%Y-%m-%d')})\n\n"]
        md.append("## 策略\n\n")
        md.append("**切红利 (任一)**\n")
        md.append("- A. 250 斜率从 120 日内最高降温 ≥ 20pp AND 250 斜率 < 10% AND close < MA20\n")
        md.append("- B. 10 日跌 ≥ 10% AND close < MA20 AND 250 斜率 > 10%\n\n")
        md.append("**切回创业板**: close > MA60 AND MA60 10 日斜率 > 0.5%\n\n")
        md.append(f"回测: {df['dt'].iloc[0]} → {df['dt'].iloc[-1]} ({n} 日)\n\n")
        md.append("## 结果\n\n")
        md.append("| 策略 | 最终资本 | 收益 |\n|---|---|---|\n")
        md.append(f"| **v12 轮动** | {cap_rot:.4f} | **{(cap_rot-1)*100:+.2f}%** |\n")
        md.append(f"| 一直持创业板 | {cap_cyb:.4f} | {(cap_cyb-1)*100:+.2f}% |\n")
        md.append(f"| 一直持红利 | {cap_red:.4f} | {(cap_red-1)*100:+.2f}% |\n\n")
        md.append("## 按年\n\n")
        md.append("| 年 | 轮动 | 创业板 | 红利 | cyb日 | red日 |\n|---|---|---|---|---|---|\n")
        for y in ["2024", "2025", "2026"]:
            y_rows = [(d, st, rc, rr) for d, st, rc, rr in results
                      if d[:4] == y and not pd.isna(rc) and not pd.isna(rr)]
            if not y_rows:
                continue
            cap_r = cap_c = cap_red_y = 1.0
            n_cyb_y = n_red_y = 0
            for d, st, rc, rr in y_rows:
                if st == "cyb":
                    cap_r *= (1 + rc); n_cyb_y += 1
                else:
                    cap_r *= (1 + rr); n_red_y += 1
                cap_c *= (1 + rc)
                cap_red_y *= (1 + rr)
            md.append(f"| {y} | {(cap_r-1)*100:+.1f}% | {(cap_c-1)*100:+.1f}% | {(cap_red_y-1)*100:+.1f}% | {n_cyb_y} | {n_red_y} |\n")
        md.append("\n## 状态切换\n\n")
        prev_state = "cyb"
        for d, st in state_log:
            if st == "red" and prev_state == "cyb":
                md.append(f"- **{d}**: 🔴 切到红利\n")
            elif st == "cyb" and prev_state == "red":
                md.append(f"- **{d}**: 🟢 切回创业板\n")
            prev_state = st
        out_path.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
