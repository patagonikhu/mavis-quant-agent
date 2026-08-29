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

    close_rets = [h["ret_30d_close"] for h in all_hits]
    max_rets = [h["ret_30d_max"] for h in all_hits]

    # 业界标准 (wbt/czsc): 胜率 = 30 日后收盘 > 0 的占比
    # 不算 max 高点 (散户看不到未来, 不可执行)
    wins = [r for r in close_rets if r > 0]
    losses = [r for r in close_rets if r <= 0]
    n_win = len(wins)
    n_loss = len(losses)
    win_rate = n_win / n * 100
    avg_win = sum(wins) / n_win if n_win else 0.0
    avg_loss = sum(losses) / n_loss if n_loss else 0.0
    avg_close = sum(close_rets) / n
    avg_max = sum(max_rets) / n
    med_close = sorted(close_rets)[n // 2]
    # 盈亏比: 平均盈利 / |平均亏损|
    profit_loss_ratio = avg_win / abs(avg_loss) if avg_loss else 0
    # 期望收益 (per trade): win_rate * avg_win - loss_rate * |avg_loss|
    expected_ret = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss

    print(f"\n=== 完成 ({elapsed:.0f}s) ===")
    print(f"总命中: {n} 次 (平均每天 {n / (args.lookback * 250):.1f} 只)")
    print(f"\n【核心指标】(30 日后收盘 vs 命中日, 业界 wbt/czsc 标准)")
    print(f"  交易胜率: {win_rate:.1f}% ({n_win}/{n})  ← '按信号完整做一单, 赚的比例'")
    print(f"  平均盈利: +{avg_win:.1f}% (n={n_win})")
    print(f"  平均亏损: {avg_loss:.1f}% (n={n_loss})")
    print(f"  盈亏比:   {profit_loss_ratio:.2f} (avg_win / |avg_loss|, >1 划算)")
    print(f"  期望收益: {expected_ret:+.2f}% / 单笔 (胜率*均盈 + 败率*均亏)")
    print(f"\n【次要指标】")
    print(f"  30 日收盘涨幅: 均 {avg_close:+.1f}% / 中位 {med_close:+.1f}%")
    print(f"  30 日最大涨幅: 均 {avg_max:+.1f}% (含盘中冲高, 不可执行, 仅参考)")
    print(f"  失败组:  {n_loss} 次, 最差 {min(losses):.1f}%")
    print(f"  命中组:  {n_win} 次, 最好 {max(wins):.1f}%")

    # 按信号类型分组
    def _sig_key(h):
        if h["obv5"] and h["obv_trend"]: return "双"
        if h["obv5"]: return "obv5"
        if h["obv_trend"]: return "obv_trend"
        return "其他"
    by_sig = {}
    for h in all_hits:
        by_sig.setdefault(_sig_key(h), []).append(h["ret_30d_close"])
    print(f"\n【按信号类型分组】(30 日收盘胜率)")
    print(f"  {'信号':<14}{'笔数':>6}{'胜率':>8}{'均收':>10}{'盈亏比':>10}{'期望':>10}")
    for sig in ["obv5", "obv_trend", "双"]:
        rets = by_sig.get(sig, [])
        if not rets:
            continue
        w = sum(1 for r in rets if r > 0)
        wr = w / len(rets) * 100
        wins_s = [r for r in rets if r > 0]
        losses_s = [r for r in rets if r <= 0]
        avg_w = sum(wins_s) / len(wins_s) if wins_s else 0
        avg_l = sum(losses_s) / len(losses_s) if losses_s else 0
        pl = avg_w / abs(avg_l) if avg_l else 0
        exp = wr / 100 * avg_w + (1 - wr / 100) * avg_l
        print(f"  {sig:<14}{len(rets):>6}{wr:>7.1f}%{sum(rets)/len(rets):>+9.1f}%{pl:>9.2f}{exp:>+9.2f}%")

    # 按年分组
    year_stats = {}
    for h in all_hits:
        y = h["date"][:4]
        year_stats.setdefault(y, []).append(h["ret_30d_close"])
    print(f"\n【按年统计】(30 日收盘胜率)")
    print(f"  {'年':<6}{'笔数':>6}{'胜率':>8}{'均收':>10}{'年化':>10}")
    for y in sorted(year_stats.keys()):
        rets = year_stats[y]
        w = sum(1 for r in rets if r > 0)
        wr = w / len(rets) * 100
        annual_factor = 250 / args.days  # 年化倍数
        annual = sum(rets) / len(rets) * annual_factor
        print(f"  {y:<6}{len(rets):>6}{wr:>7.1f}%{sum(rets)/len(rets):>+9.1f}%{annual:>+9.1f}%")

    print(f"\n【明细】(按日期排序, 前 30 条, 30 日收盘为准)")
    all_hits.sort(key=lambda x: x["date"])
    for h in all_hits[:30]:
        marker = "✅" if h["ret_30d_close"] > 0 else "❌"
        sig = _sig_key(h)
        print(f"  {marker} {h['date']} {h['code']} @¥{h['close']:.2f} "
              f"→ 30日收盘 {h['ret_30d_close']:+.1f}% (max {h['ret_30d_max']:+.1f}%) "
              f"[{sig}] BOLL={h['boll_pct']:.1f}% BBW={h['bbw']:.2f}")
    if len(all_hits) > 30:
        print(f"  ... 共 {len(all_hits)} 条")

    if args.write_md:
        out_path = ROOT / "docs" / "backtest-bb-obv.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# BB+OBV 三重确认回测 ({datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 策略: BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% AND (obv5 OR obv_trend)\n")
        md.append(f"> 回看: {args.lookback}y ({min_date} ~ {last_db_date}) | 持仓 {args.days} 日\n")
        md.append(f"> **胜率口径**: 30 日后**收盘价** vs 命中日收盘价, ret > 0 算赢 (业界 wbt/czsc 标准)\n")
        md.append(f"> ⚠️ 不再以盘中 max 算胜率 (散户不可执行, 仅作 max 列参考)\n\n")
        md.append(f"## 核心指标\n\n")
        md.append(f"| 指标 | 值 |\n|---|---|\n")
        md.append(f"| 总命中 | {n} 次 ({n / (args.lookback * 250):.1f}/天) |\n")
        md.append(f"| **交易胜率** | **{win_rate:.1f}%** ({n_win}/{n}) |\n")
        md.append(f"| 平均盈利 | +{avg_win:.1f}% (n={n_win}) |\n")
        md.append(f"| 平均亏损 | {avg_loss:.1f}% (n={n_loss}) |\n")
        md.append(f"| **盈亏比** | **{profit_loss_ratio:.2f}** (avg_win / |avg_loss|) |\n")
        md.append(f"| **期望收益/单笔** | **{expected_ret:+.2f}%** |\n")
        md.append(f"| 30 日收盘涨幅 均/中位 | {avg_close:+.1f}% / {med_close:+.1f}% |\n")
        md.append(f"| 30 日最大涨幅 均 (参考) | {avg_max:+.1f}% |\n")
        md.append(f"\n## 按信号类型\n\n")
        md.append(f"| 信号 | 笔数 | 胜率 | 均收 | 盈亏比 | 期望 |\n|---|---|---|---|---|---|\n")
        for sig in ["obv5", "obv_trend", "双"]:
            rets = by_sig.get(sig, [])
            if not rets:
                continue
            w = sum(1 for r in rets if r > 0)
            wr = w / len(rets) * 100
            wins_s = [r for r in rets if r > 0]
            losses_s = [r for r in rets if r <= 0]
            avg_w = sum(wins_s) / len(wins_s) if wins_s else 0
            avg_l = sum(losses_s) / len(losses_s) if losses_s else 0
            pl = avg_w / abs(avg_l) if avg_l else 0
            exp = wr / 100 * avg_w + (1 - wr / 100) * avg_l
            md.append(f"| {sig} | {len(rets)} | {wr:.1f}% | {sum(rets)/len(rets):+.1f}% | {pl:.2f} | {exp:+.2f}% |\n")
        md.append(f"\n## 按年\n\n| 年 | 笔数 | 胜率 | 均收 | 年化 |\n|---|---|---|---|---|\n")
        for y in sorted(year_stats.keys()):
            rets = year_stats[y]
            w = sum(1 for r in rets if r > 0)
            wr = w / len(rets) * 100
            annual = sum(rets) / len(rets) * (250 / args.days)
            md.append(f"| {y} | {len(rets)} | {wr:.1f}% | {sum(rets)/len(rets):+.1f}% | {annual:+.1f}% |\n")
        md.append(f"\n## 明细 ({len(all_hits)} 条, 收盘为准)\n\n")
        md.append("| 日期 | 代码 | 收盘 | 30日收盘 | 30日最大 | 信号 | 胜/败 |\n")
        md.append("|---|---|---|---|---|---|---|\n")
        for h in all_hits:
            marker = "✅" if h["ret_30d_close"] > 0 else "❌"
            sig = _sig_key(h)
            md.append(f"| {h['date']} | {h['code']} | ¥{h['close']:.2f} | "
                     f"{h['ret_30d_close']:+.1f}% | {h['ret_30d_max']:+.1f}% | {sig} | {marker} |\n")
        out_path.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
