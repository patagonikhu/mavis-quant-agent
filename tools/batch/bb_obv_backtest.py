"""bb_obv_backtest.py — BB+OBV 三重确认 策略回测

策略定义 (与 bb_obv_scan.py 完全一致):
  1. BOLL% < 15  (接近下轨, 短期超卖)
  2. BBW < 10    (布林带收窄, 低波/蓄势)
  3. OBV 实战信号: obv5 (5日价跌+OBV涨) OR obv_trend (OBV>MA20)

历史回测: 命中后未来 N 日 (默认 30) 最大涨幅, 命中率/均涨幅统计

数据: K 线走 DataStore (跟其它工具一致), BOLL/BBW/OBV 实时算
"""
import argparse
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "data" / "analysis_cache.db"

# 模块顶层 import, ThreadPool worker 才有 sys.path
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.kline_store import DataStore


def _load_codes_with_obv(min_date: str) -> list[str]:
    """从 cache 查有 obv5/obv_trend 的代码"""
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT DISTINCT code FROM analysis_cache "
        "WHERE obv5 IS NOT NULL AND date_str >= ?",
        (min_date,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _sliding_mean(arr, n):
    """前缀和 O(1) 滑动均值"""
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


def _compute_boll_pct_bbw(closes, period=20, std_n=2):
    """BOLL% (close 相对下轨位置 0-100, <15 接近下轨) + BBW (带宽%)
    跟 WyckoffStrategy 一致.
    """
    n = len(closes)
    out_pct = [None] * n
    out_bw  = [None] * n
    ma = _sliding_mean(closes, period)
    for i in range(period - 1, n):
        m = ma[i]
        window = closes[i - period + 1: i + 1]
        var = sum((c - m) ** 2 for c in window) / period
        std = var ** 0.5
        upper = m + std_n * std
        lower = m - std_n * std
        if upper > lower:
            out_pct[i] = (closes[i] - lower) / (upper - lower) * 100
            out_bw[i] = (upper - lower) / m * 100 if m > 0 else 0
    return out_pct, out_bw


def _compute_obv_signals(closes, vols):
    """算 OBV 数组 + obv5 + obv_trend (跟 ObvStrategy 一致)"""
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

    obv_ma20 = _sliding_mean(obv, 20)
    obv_trend = [0] * n
    for i in range(n):
        if obv_ma20[i] and obv[i] > obv_ma20[i]:
            obv_trend[i] = 1
    return obv5, obv_trend


def _scan_one(code: str, lookback_years: int, boll_th: float, bbw_th: float) -> list[dict]:
    """扫单只票所有命中日, 返回命中详情 list"""
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 60:
            return []

        kline = ctx.kline
        # 标准化: trade_date 字符串 + 数值
        rows = []
        for k in kline:
            d = str(k.get("trade_date", "")).replace("-", "")[:8]
            if not d or len(d) != 8:
                continue
            try:
                rows.append((
                    d,
                    float(k.get("close", 0)),
                    float(k.get("high", 0)),
                    float(k.get("low", 0)),
                    float(k.get("volume", 0) or k.get("vol", 0) or 0),
                ))
            except (TypeError, ValueError):
                continue
        if len(rows) < 60:
            return []
        rows.sort(key=lambda x: x[0])
        dates = [r[0] for r in rows]
        closes = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        vols = [r[4] for r in rows]

        bpct_arr, bbw_arr = _compute_boll_pct_bbw(closes)
        obv5_arr, obv_trend_arr = _compute_obv_signals(closes, vols)

        last_date = dates[-1]
        first_date = (datetime.strptime(last_date, "%Y%m%d")
                      - timedelta(days=lookback_years * 365)).strftime("%Y%m%d")

        hits = []
        for i in range(20, len(rows)):
            d = dates[i]
            if d < first_date:
                continue
            bp = bpct_arr[i]
            bw = bbw_arr[i]
            if bp is None or bw is None:
                continue
            if bp >= boll_th or bw >= bbw_th:
                continue
            o5 = obv5_arr[i]
            ot = obv_trend_arr[i]
            if (o5 + ot) == 0:
                continue
            if i + 30 >= len(rows):
                continue
            close_now = closes[i]
            future_highs = highs[i + 1: i + 31]
            future_close = closes[i + 30]
            ret_max = (max(future_highs) / close_now - 1) * 100
            ret_close = (future_close / close_now - 1) * 100
            hits.append({
                "code": code,
                "date": d,
                "close": close_now,
                "ret_30d_max": ret_max,
                "ret_30d_close": ret_close,
                "obv5": int(o5),
                "obv_trend": int(ot),
                "boll_pct": bp,
                "bbw": bw,
            })
        return hits
    except Exception as e:
        import traceback
        traceback.print_exc()
        return []


def main():
    ap = argparse.ArgumentParser(description="BB+OBV 三重确认策略回测")
    ap.add_argument("--lookback", type=int, default=3, help="回看年数 (默认 3)")
    ap.add_argument("--days", type=int, default=30, help="持仓期 (默认 30)")
    ap.add_argument("--threshold", type=float, default=10.0, help="涨幅阈值 % (默认 10)")
    ap.add_argument("--boll-threshold", type=float, default=15.0, help="BOLL% 上限")
    ap.add_argument("--bbw-threshold", type=float, default=10.0, help="BBW 上限")
    ap.add_argument("--workers", type=int, default=4, help="并发数")
    ap.add_argument("--write-md", action="store_true", help="写 docs/backtest-bb-obv.md")
    args = ap.parse_args()

    print(f"=== BB+OBV 三重确认回测 | {args.lookback}y | 持仓{args.days}日 | 阈值{args.threshold}% ===")

    last_db_date = sqlite3.connect(str(DB)).execute(
        "SELECT MAX(date_str) FROM analysis_cache"
    ).fetchone()[0]
    min_date = (datetime.strptime(last_db_date, "%Y%m%d")
                - timedelta(days=args.lookback * 365)).strftime("%Y%m%d")
    print(f"回看窗口: {min_date} ~ {last_db_date} ({args.lookback}y)")

    codes = _load_codes_with_obv(min_date)
    print(f"扫描 {len(codes)} 只票 ({args.workers} workers)...")

    t0 = time.time()
    all_hits = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_scan_one, code, args.lookback, args.boll_threshold, args.bbw_threshold): code
                for code in codes}
        done = 0
        for fut in as_completed(futs):
            done += 1
            r = fut.result()
            all_hits.extend(r)
            if done % 200 == 0:
                print(f"  [{done}/{len(codes)}] 累计命中 {len(all_hits)} | {time.time()-t0:.0f}s",
                      flush=True)
    elapsed = time.time() - t0

    n = len(all_hits)
    if n == 0:
        print(f"\n无命中 ({elapsed:.0f}s)")
        return

    max_rets = [h["ret_30d_max"] for h in all_hits]
    close_rets = [h["ret_30d_close"] for h in all_hits]
    wins = [r for r in max_rets if r >= args.threshold]
    losses = [r for r in max_rets if r < args.threshold]
    avg_max = sum(max_rets) / n
    med_max = sorted(max_rets)[n // 2]
    avg_close = sum(close_rets) / n
    win_rate = len(wins) / n * 100
    avg_loss = sum(losses) / len(losses) if losses else 0.0

    print(f"\n=== 完成 ({elapsed:.0f}s) ===")
    print(f"总命中: {n} 次 (平均每天 {n / (args.lookback * 250):.1f} 只)")
    print(f"持仓 {args.days} 日, 阈值 ≥{args.threshold}%")
    print(f"  命中数: {len(wins)} ({win_rate:.1f}%)")
    print(f"  失败数: {len(losses)}")
    print(f"  最大涨幅: 均 {avg_max:.1f}% / 中位 {med_max:.1f}% / 峰值 {max(max_rets):.1f}%")
    print(f"  30 日收盘涨幅: 均 {avg_close:.1f}%")
    if losses:
        print(f"  失败组均跌: {avg_loss:.1f}% / 最差 {min(losses):.1f}%")

    obv5_only = sum(1 for h in all_hits if h["obv5"] and not h["obv_trend"])
    obv_trend_only = sum(1 for h in all_hits if h["obv_trend"] and not h["obv5"])
    obv_both = sum(1 for h in all_hits if h["obv5"] and h["obv_trend"])
    print(f"\n信号分布:")
    print(f"  obv5 only: {obv5_only}")
    print(f"  obv_trend only: {obv_trend_only}")
    print(f"  obv5+obv_trend: {obv_both}")

    year_stats = {}
    for h in all_hits:
        y = h["date"][:4]
        year_stats.setdefault(y, []).append(h["ret_30d_max"])
    print(f"\n按年统计:")
    for y in sorted(year_stats.keys()):
        rets = year_stats[y]
        wins_y = sum(1 for r in rets if r >= args.threshold)
        print(f"  {y}: {len(rets):3d} 命中, 胜率 {wins_y/len(rets)*100:.0f}%, "
              f"均涨幅 {sum(rets)/len(rets):+.1f}%")

    print(f"\n明细 (按日期排序, 前 30 条):")
    all_hits.sort(key=lambda x: x["date"])
    for h in all_hits[:30]:
        marker = "✅" if h["ret_30d_max"] >= args.threshold else "❌"
        sig = "obv5" if h["obv5"] and not h["obv_trend"] else (
            "obv_trend" if h["obv_trend"] and not h["obv5"] else "双"
        )
        print(f"  {marker} {h['date']} {h['code']} @¥{h['close']:.2f} "
              f"→ 30日最大 {h['ret_30d_max']:+.1f}% / 收盘 {h['ret_30d_close']:+.1f}% "
              f"[{sig}] BOLL={h['boll_pct']:.1f}% BBW={h['bbw']:.2f}")
    if len(all_hits) > 30:
        print(f"  ... 共 {len(all_hits)} 条")

    if args.write_md:
        out_path = ROOT / "docs" / "backtest-bb-obv.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# BB+OBV 三重确认回测 ({datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 策略: BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% AND (obv5 OR obv_trend)\n")
        md.append(f"> 回看: {args.lookback}y ({min_date} ~ {last_db_date}) | 持仓 {args.days} 日 | 阈值 ≥{args.threshold}%\n\n")
        md.append(f"## 汇总\n\n")
        md.append(f"| 指标 | 值 |\n|---|---|\n")
        md.append(f"| 总命中 | {n} 次 ({n / (args.lookback * 250):.1f}/天) |\n")
        md.append(f"| 命中 ≥{args.threshold}% | {len(wins)} ({win_rate:.1f}%) |\n")
        md.append(f"| 失败 <{args.threshold}% | {len(losses)} |\n")
        md.append(f"| 最大涨幅 均/中位/峰 | {avg_max:.1f}% / {med_max:.1f}% / {max(max_rets):.1f}% |\n")
        md.append(f"| 30 日收盘涨幅 均 | {avg_close:.1f}% |\n")
        if losses:
            md.append(f"| 失败组均跌/最差 | {avg_loss:.1f}% / {min(losses):.1f}% |\n")
        md.append(f"\n## 按年\n\n| 年 | 命中 | 胜率 | 均涨幅 |\n|---|---|---|---|\n")
        for y in sorted(year_stats.keys()):
            rets = year_stats[y]
            wins_y = sum(1 for r in rets if r >= args.threshold)
            md.append(f"| {y} | {len(rets)} | {wins_y/len(rets)*100:.0f}% | {sum(rets)/len(rets):+.1f}% |\n")
        md.append(f"\n## 明细 ({len(all_hits)} 条)\n\n")
        md.append("| 日期 | 代码 | 收盘 | 30日最大 | 30日收盘 | 信号 | BOLL% | BBW |\n")
        md.append("|---|---|---|---|---|---|---|---|\n")
        for h in all_hits:
            marker = "✅" if h["ret_30d_max"] >= args.threshold else "❌"
            sig = "obv5" if h["obv5"] and not h["obv_trend"] else (
                "obv_trend" if h["obv_trend"] and not h["obv5"] else "双"
            )
            md.append(f"| {h['date']} | {h['code']} | ¥{h['close']:.2f} | "
                     f"{marker} {h['ret_30d_max']:+.1f}% | {h['ret_30d_close']:+.1f}% | "
                     f"{sig} | {h['boll_pct']:.1f} | {h['bbw']:.2f} |\n")
        out_path.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
