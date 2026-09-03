"""rotation_backtest.py — 创业板 / 红利低波 轮动策略回测

策略 (用 BB+OBV/BBW/MA20 斜率):
  创业板 BB+OBV 命中 (BOLL<15 + BBW<10 + OBV 实战信号) + 大盘 BULL/FLAT:
    → 持仓创业板
  创业板 BB+OBV 失效 (无信号) + 大盘 BEAR:
    → 持仓红利低波 (防御)
  否则 (震荡无信号):
    → 持仓红利 (保守)

实战可执行口径: 每天信号决定持仓, 不做止盈止损 (轮动策略本身就是止盈),
  跳过手续费 (因为是月度级别回测, 频率低)

数据: K线从 DataStore 读, BB+OBV 用 ObvStrategy 算
"""
import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "analysis_cache.db"

# 模块顶层 import (ThreadPool worker 需要)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine


def sliding_mean(arr, n):
    if not arr or n <= 0:
        return [None] * len(arr)
    out = [None] * len(arr)
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= n:
            s -= arr[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def compute_obv_signals(closes, vols):
    """算 OBV 数组 + obv5 + obv_trend"""
    n = len(closes)
    obv = [0.0] * n
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + vols[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - vols[i]
        else:
            obv[i] = obv[i - 1]
    obv5 = [0] * n
    for i in range(5, n):
        if closes[i] < closes[i - 5] and obv[i] > obv[i - 5]:
            obv5[i] = 1
    obv_ma20 = sliding_mean(obv, 20)
    obv_trend = [0] * n
    for i in range(n):
        if obv_ma20[i] and obv[i] > obv_ma20[i]:
            obv_trend[i] = 1
    return obv, obv5, obv_trend


def compute_boll(closes, period=20, std_n=2):
    """BOLL% (close 相对下轨 0-100) + BBW 带宽%"""
    n = len(closes)
    out_pct = [None] * n
    out_bw = [None] * n
    ma = sliding_mean(closes, period)
    for i in range(period - 1, n):
        m = ma[i]
        if not m:
            continue
        window = closes[i - period + 1: i + 1]
        var = sum((c - m) ** 2 for c in window) / period
        std = var ** 0.5
        upper = m + std_n * std
        lower = m - std_n * std
        if upper > lower:
            out_pct[i] = (closes[i] - lower) / (upper - lower) * 100
            out_bw[i] = (upper - lower) / m * 100 if m > 0 else 0
    return out_pct, out_bw


def compute_ma20_slope(closes, period=20, lookback=5):
    """MA20 5 日斜率 (%/日)"""
    n = len(closes)
    ma = sliding_mean(closes, period)
    out = [None] * n
    for i in range(lookback, n):
        if ma[i] is not None and ma[i - lookback] is not None and ma[i - lookback] > 0:
            out[i] = (ma[i] - ma[i - lookback]) / ma[i - lookback] / lookback * 100
    return out


def scan_one(code: str, lookback_days: int, boll_th: float, bbw_th: float,
              threshold: float) -> list:
    """对单只指数, 每天输出信号 dict"""
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 60:
            return []
        kline = ctx.kline
        rows = []
        for k in kline:
            d = str(k.get("trade_date", "")).replace("-", "")[:8]
            if not d or len(d) != 8:
                continue
            try:
                rows.append((d, float(k.get("close", 0)), float(k.get("high", 0)),
                             float(k.get("low", 0)), float(k.get("open", 0) or k.get("close", 0)),
                             float(k.get("volume", 0) or k.get("vol", 0) or 0)))
            except (TypeError, ValueError):
                continue
        if len(rows) < 60:
            return []
        rows.sort(key=lambda x: x[0])
        dates = [r[0] for r in rows]
        closes = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        opens = [r[4] for r in rows]
        vols = [r[5] for r in rows]

        # 取最近 lookback_days
        if len(rows) > lookback_days:
            rows = rows[-lookback_days:]
            dates = dates[-lookback_days:]
            closes = closes[-lookback_days:]
            highs = highs[-lookback_days:]
            lows = lows[-lookback_days:]
            opens = opens[-lookback_days:]
            vols = vols[-lookback_days:]

        # 算指标
        bpct, bbw = compute_boll(closes)
        _, obv5, obv_trend = compute_obv_signals(closes, vols)
        ma20_slope = compute_ma20_slope(closes)

        out = []
        for i in range(20, len(rows)):
            d = dates[i]
            c = closes[i]
            bp = bpct[i]
            bw = bbw[i]
            o5 = obv5[i]
            ot = obv_trend[i]
            slope = ma20_slope[i]
            # 信号: BB+OBV 命中
            if bp is not None and bw is not None:
                bb_obv_hit = (bp < boll_th) and (bw < bbw_th) and (o5 or ot)
            else:
                bb_obv_hit = False
            # 趋势: MA20 5 日斜率
            if slope is not None:
                if slope > 0.3:
                    trend = "BULL"
                elif slope < -0.3:
                    trend = "BEAR"
                else:
                    trend = "FLAT"
            else:
                trend = "?"
            out.append({
                "code": code,
                "date": d,
                "close": c,
                "high": highs[i],
                "low": lows[i],
                "open": opens[i],
                "bpct": bp,
                "bbw": bw,
                "obv5": o5,
                "obv_trend": ot,
                "ma20_slope": slope,
                "bb_obv_hit": bb_obv_hit,
                "trend": trend,
            })
        return out
    except Exception as e:
        import traceback
        traceback.print_exc()
        return []


def main():
    ap = argparse.ArgumentParser(description="创业板 / 红利轮动回测")
    ap.add_argument("--lookback", type=int, default=3, help="回看年数")
    ap.add_argument("--boll", type=float, default=15.0, help="BOLL% 阈值")
    ap.add_argument("--bbw", type=float, default=10.0, help="BBW 阈值")
    ap.add_argument("--workers", type=int, default=4, help="并发")
    ap.add_argument("--write-md", action="store_true", help="写 md")
    args = ap.parse_args()

    lookback_days = args.lookback * 365

    print(f"=== 创业板 / 红利低波 轮动回测 ({args.lookback}y) ===")
    print(f"    创业板 BB+OBV 命中 (BOLL<{args.boll} + BBW<{args.bbw} + OBV 实战信号)")
    print(f"    实战规则:")
    print(f"      创业板 BB+OBV 命中 + BULL/FLAT → 创业板")
    print(f"      创业板 BB+OBV 失效 + BEAR      → 红利低波")
    print(f"      创业板 BB+OBV 命中 + BEAR      → 红利低波 (风险)")
    print(f"      创业板 BB+OBV 失效 + BULL/FLAT → 创业板 (信号失效但趋势好)")

    # 算两指数的每日信号
    print("\n计算创业板信号...")
    cyb_signals = scan_one("399006.SZ", lookback_days, args.boll, args.bbw, 0)
    print(f"  创业板: {len(cyb_signals)} 日")
    print("计算红利低波信号...")
    red_signals = scan_one("930955", lookback_days, args.boll, args.bbw, 0)
    print(f"  红利低波: {len(red_signals)} 日")

    if not cyb_signals or not red_signals:
        print("数据不足")
        return

    # 按日期对齐
    cyb_by_date = {s["date"]: s for s in cyb_signals}
    red_by_date = {s["date"]: s for s in red_signals}
    common_dates = sorted(set(cyb_by_date.keys()) & set(red_by_date.keys()))
    print(f"共同交易日: {len(common_dates)}")

    # 轮动回测
    capital = 1.0
    capital_cyb = 1.0  # 一直持创业板
    capital_red = 1.0  # 一直持红利

    n_cyb_picks = 0
    n_red_picks = 0
    n_cyb_correct = 0  # 选对了 (>0 收益)
    n_red_correct = 0

    pick_log = []  # (date, pick, daily_ret)

    # 跳过前 30 日 (MA20 等需要暖机)
    skip = 30
    for i, d in enumerate(common_dates[skip:], start=skip):
        cs = cyb_by_date[d]
        rs = red_by_date[d]

        # 决策
        if i + 1 < len(common_dates):
            next_d = common_dates[i + 1]
        else:
            continue
        # 用 i 当天收盘价决策, 次日开盘价成交 (实战可行)
        cs_next = cyb_by_date[next_d]
        rs_next = red_by_date[next_d]

        # 决策规则
        pick = None
        if cs["bb_obv_hit"]:
            # 创业板 BB+OBV 命中
            if cs["trend"] in ("BULL", "FLAT"):
                pick = "cyb"  # 进攻
            else:  # BEAR
                pick = "red"  # 风险, 转防御
        else:
            # 创业板 BB+OBV 失效
            if cs["trend"] == "BEAR":
                pick = "red"  # 防御
            else:
                # BULL/FLAT 但 OBV 无信号 → 继续持创业板 (趋势好就行)
                pick = "cyb"

        # 次日收益 (开盘 → 收盘)
        if pick == "cyb":
            next_ret = (cs_next["close"] - cs_next["open"]) / cs_next["open"]
            n_cyb_picks += 1
        else:
            next_ret = (rs_next["close"] - rs_next["open"]) / rs_next["open"]
            n_red_picks += 1

        if next_ret > 0:
            if pick == "cyb":
                n_cyb_correct += 1
            else:
                n_red_correct += 1

        capital *= (1 + next_ret)
        capital_cyb *= (1 + (cs_next["close"] - cs_next["open"]) / cs_next["open"])
        capital_red *= (1 + (rs_next["close"] - rs_next["open"]) / rs_next["open"])

        pick_log.append((d, pick, next_ret))

    n = len(pick_log)
    print(f"\n=== 结果 (日级轮动) ===")
    print(f"总交易日: {n}")
    print(f"\n【轮动策略】")
    print(f"  最终资本: {capital:.4f} ({capital*100-100:+.2f}%)")
    print(f"  创业板选 {n_cyb_picks} 天 ({n_cyb_picks/n*100:.0f}%)")
    print(f"  红利选 {n_red_picks} 天 ({n_red_picks/n*100:.0f}%)")
    n_pos = sum(1 for _, _, r in pick_log if r > 0)
    print(f"  日胜率: {n_pos/n*100:.1f}% ({n_pos}/{n})")
    avg_ret = sum(r for _, _, r in pick_log) / n
    print(f"  日均收益: {avg_ret*100:+.3f}%")

    print(f"\n【对比: 一直持创业板】")
    print(f"  最终资本: {capital_cyb:.4f} ({capital_cyb*100-100:+.2f}%)")
    print(f"\n【对比: 一直持红利】")
    print(f"  最终资本: {capital_red:.4f} ({capital_red*100-100:+.2f}%)")

    # 按年统计
    year_stats = {}
    for d, pick, r in pick_log:
        y = d[:4]
        year_stats.setdefault(y, []).append((pick, r))
    print(f"\n【按年: 轮动 vs 持创业板 vs 持红利】")
    print(f"  {'年':<6}{'轮动':<10}{'创业板':<10}{'红利':<10}{'选cyb':<8}{'选red':<8}{'日胜率':<8}")
    cyb_idx = 0
    red_idx = 0
    for y in sorted(year_stats.keys()):
        rows = year_stats[y]
        # 算该年累计
        cap_rot = 1.0
        for _, r in rows:
            cap_rot *= (1 + r)
        # 该年创业板累计
        cap_cyb = 1.0
        for dt in [d for d in common_dates if d[:4] == y]:
            if dt in cyb_by_date and common_dates.index(dt) + 1 < len(common_dates):
                nd = common_dates[common_dates.index(dt) + 1]
                if nd in cyb_by_date:
                    cap_cyb *= (1 + (cyb_by_date[nd]["close"] - cyb_by_date[nd]["open"]) / cyb_by_date[nd]["open"])
        cap_red = 1.0
        for dt in [d for d in common_dates if d[:4] == y]:
            if dt in red_by_date and common_dates.index(dt) + 1 < len(common_dates):
                nd = common_dates[common_dates.index(dt) + 1]
                if nd in red_by_date:
                    cap_red *= (1 + (red_by_date[nd]["close"] - red_by_date[nd]["open"]) / red_by_date[nd]["open"])
        n_cyb_y = sum(1 for p, _ in rows if p == "cyb")
        n_red_y = sum(1 for p, _ in rows if p == "red")
        n_win = sum(1 for _, r in rows if r > 0)
        print(f"  {y:<6}{(cap_rot-1)*100:>+8.1f}%  {(cap_cyb-1)*100:>+8.1f}%  {(cap_red-1)*100:>+8.1f}%  {n_cyb_y:<8}{n_red_y:<8}{n_win/len(rows)*100:.0f}%")

    # 关键事件: 大赢/大亏
    print(f"\n【关键时刻: 前 20 大单笔】")
    pick_log_sorted = sorted(pick_log, key=lambda x: -x[2])
    for d, pick, r in pick_log_sorted[:10]:
        print(f"  ✅ {d} 选{pick.upper()} 收益 {r*100:+.2f}%")
    print(f"...")
    for d, pick, r in pick_log_sorted[-10:]:
        print(f"  ❌ {d} 选{pick.upper()} 收益 {r*100:+.2f}%")

    if args.write_md:
        out_path = ROOT / "docs" / "backtest-rotation.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# 创业板 / 红利低波 轮动回测 ({datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 策略: 创业板 BB+OBV 命中 + BULL/FLAT → 创业板; 失效 + BEAR → 红利低波\n")
        md.append(f"> 回看: {args.lookback}y ({n} 交易日)\n\n")
        md.append(f"## 结果\n\n")
        md.append(f"| 策略 | 最终资本 | 收益 | 日胜率 |\n|---|---|---|---|\n")
        md.append(f"| **轮动策略** | {capital:.4f} | {(capital-1)*100:+.2f}% | {n_pos/n*100:.1f}% |\n")
        md.append(f"| 一直持创业板 | {capital_cyb:.4f} | {(capital_cyb-1)*100:+.2f}% | - |\n")
        md.append(f"| 一直持红利 | {capital_red:.4f} | {(capital_red-1)*100:+.2f}% | - |\n")
        md.append(f"\n## 按年\n\n")
        md.append(f"| 年 | 轮动 | 创业板 | 红利 | 选cyb | 选red |\n|---|---|---|---|---|---|\n")
        for y in sorted(year_stats.keys()):
            rows = year_stats[y]
            cap_rot = 1.0
            for _, r in rows:
                cap_rot *= (1 + r)
            cap_cyb = 1.0
            for dt in [x for x in common_dates if x[:4] == y]:
                if dt in cyb_by_date and common_dates.index(dt) + 1 < len(common_dates):
                    nd = common_dates[common_dates.index(dt) + 1]
                    if nd in cyb_by_date:
                        cap_cyb *= (1 + (cyb_by_date[nd]["close"] - cyb_by_date[nd]["open"]) / cyb_by_date[nd]["open"])
            cap_red = 1.0
            for dt in [x for x in common_dates if x[:4] == y]:
                if dt in red_by_date and common_dates.index(dt) + 1 < len(common_dates):
                    nd = common_dates[common_dates.index(dt) + 1]
                    if nd in red_by_date:
                        cap_red *= (1 + (red_by_date[nd]["close"] - red_by_date[nd]["open"]) / red_by_date[nd]["open"])
            n_cyb_y = sum(1 for p, _ in rows if p == "cyb")
            n_red_y = sum(1 for p, _ in rows if p == "red")
            md.append(f"| {y} | {(cap_rot-1)*100:+.1f}% | {(cap_cyb-1)*100:+.1f}% | {(cap_red-1)*100:+.1f}% | {n_cyb_y} | {n_red_y} |\n")
        out_path.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
