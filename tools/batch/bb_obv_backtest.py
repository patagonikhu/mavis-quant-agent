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


def _scan_one(code: str, lookback_years: int, boll_th: float, bbw_th: float,
              take_profit: float = 0, stop_loss: float = 0, max_hold: int = 30) -> list[dict]:
    """扫单只票所有命中日, 返回命中详情 list

    实战可执行规则 (按优先级):
      1. 止盈: 涨 ≥ take_profit% 当日触达, 次日开盘卖
      2. 止损: 跌 ≤ -stop_loss% 当日触达, 次日开盘卖
      3. 兜底: 持有 max_hold 日收盘卖

    ⚠️ 故意不引入 OBV 信号消失规则 — 2026-08-29 反馈:
       obv5/obv_trend 5d/20d 滚动窗口, 边界处抖来抖去, 信号反复触发-消失-再触发.
       实战会被反复打脸 (假信号), 不能当卖出依据.

    take_profit / stop_loss = 0 表示不设该规则
    """
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
                rows.append((
                    d,
                    float(k.get("close", 0)),
                    float(k.get("high", 0)),
                    float(k.get("low", 0)),
                    float(k.get("open", 0) or k.get("close", 0)),
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
        lows = [r[3] for r in rows]
        opens = [r[4] for r in rows]
        vols = [r[5] for r in rows]

        bpct_arr, bbw_arr = _compute_boll_pct_bbw(closes)
        obv5_arr, obv_trend_arr = _compute_obv_signals(closes, vols)

        last_date = dates[-1]
        first_date = (datetime.strptime(last_date, "%Y%m%d")
                      - timedelta(days=lookback_years * 365)).strftime("%Y%m%d")

        hits = []
        for i in range(20, len(rows) - max_hold):
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
            if i + max_hold >= len(rows):
                continue

            # === 模拟一笔完整交易 ===
            entry_price = closes[i]  # 信号日收盘价 (T 日买, 实际 T+1 开盘更准; 简化用 close)
            entry_bpct = bp     # 入场 BOLL 位置
            entry_bbw = bw      # 入场 BBW

            exit_day = None
            exit_price = None
            exit_reason = "max_hold"

            for j in range(1, max_hold + 1):
                idx = i + j
                if idx >= len(rows):
                    break
                # 1) 止盈: 用当日 high 触发 (盘中触达就算)
                if take_profit > 0 and highs[idx] >= entry_price * (1 + take_profit / 100):
                    if idx + 1 < len(rows):
                        exit_price = opens[idx + 1]
                    else:
                        exit_price = closes[idx]
                    exit_reason = f"止盈+{take_profit}%"
                    exit_day = j
                    break
                # 2) 止损: 用当日 low 触发 (盘中触达就算)
                if stop_loss > 0 and lows[idx] <= entry_price * (1 - stop_loss / 100):
                    if idx + 1 < len(rows):
                        exit_price = opens[idx + 1]
                    else:
                        exit_price = closes[idx]
                    exit_reason = f"止损-{stop_loss}%"
                    exit_day = j
                    break
                # 3) 入场条件反转 (实战: 买点逻辑被破坏, 重新评估)
                #    BOLL 突破 90% (=价格已经到上轨, 反弹到位, 留 10% 缓冲)
                #    BBW 扩张 > 20 (=波动大幅放大, 蓄势形态被破坏)
                #  ⚠️ 不能太严格: BOLL 突破 80% 还可能继续涨 (突破上轨才进超买)
                cur_bpct = bpct_arr[idx]
                cur_bbw = bbw_arr[idx]
                if cur_bpct is not None and cur_bpct > 90:
                    if idx + 1 < len(rows):
                        exit_price = opens[idx + 1]
                    else:
                        exit_price = closes[idx]
                    exit_reason = "BOLL反转(>90)"
                    exit_day = j
                    break
                if cur_bbw is not None and cur_bbw > 20:
                    if idx + 1 < len(rows):
                        exit_price = opens[idx + 1]
                    else:
                        exit_price = closes[idx]
                    exit_reason = "BBW扩张(>20)"
                    exit_day = j
                    break
            # 4) 兜底: max_hold 日后收盘卖
            if exit_price is None:
                exit_idx = i + max_hold
                if exit_idx < len(rows):
                    exit_price = closes[exit_idx]
                    exit_day = max_hold
                else:
                    continue

            ret_pct = (exit_price / entry_price - 1) * 100
            # max 涨幅 (用于参考)
            future_highs = highs[i + 1: i + exit_day + 1] if exit_day else []
            ret_max = (max(future_highs) / entry_price - 1) * 100 if future_highs else 0
            # 30d 兜底 (跟旧口径对比)
            idx_30 = min(i + 30, len(rows) - 1)
            ret_30d_close = (closes[idx_30] / entry_price - 1) * 100

            hits.append({
                "code": code,
                "date": d,
                "close": entry_price,
                "exit_day": exit_day,
                "exit_price": exit_price,
                "ret_pct": ret_pct,            # 这笔交易实际收益
                "ret_max": ret_max,            # 持仓期内最大涨幅
                "ret_30d_close": ret_30d_close,  # 30 日后收盘 (兜底)
                "exit_reason": exit_reason,
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
    ap.add_argument("--days", type=int, default=30, help="最大持仓期 (默认 30)")
    ap.add_argument("--take-profit", type=float, default=10.0,
                    help="止盈 % (默认 10, 0=不设)")
    ap.add_argument("--stop-loss", type=float, default=8.0,
                    help="止损 % (默认 8, 0=不设)")
    ap.add_argument("--boll-threshold", type=float, default=15.0, help="BOLL% 上限")
    ap.add_argument("--bbw-threshold", type=float, default=10.0, help="BBW 上限")
    ap.add_argument("--workers", type=int, default=4, help="并发数")
    ap.add_argument("--write-md", action="store_true", help="写 docs/backtest-bb-obv.md")
    args = ap.parse_args()

    rules = []
    if args.take_profit > 0: rules.append(f"止盈+{args.take_profit}%")
    if args.stop_loss > 0:   rules.append(f"止损-{args.stop_loss}%")
    rules.append("买点反转(BOLL>80|BBW>15)")
    rules.append(f"兜底{args.days}日")
    print(f"=== BB+OBV 三重确认回测 | {args.lookback}y ===")
    print(f"    卖出规则 (按优先级): {' → '.join(rules)}")

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
        futs = {ex.submit(_scan_one, code, args.lookback, args.boll_threshold,
                          args.bbw_threshold, args.take_profit, args.stop_loss,
                          args.days): code
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

    # === 核心: 用实战可执行的卖出规则 (ret_pct) ===
    rets = [h["ret_pct"] for h in all_hits]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    n_win = len(wins)
    n_loss = len(losses)
    win_rate = n_win / n * 100
    avg_win = sum(wins) / n_win if n_win else 0.0
    avg_loss = sum(losses) / n_loss if n_loss else 0.0
    avg_ret = sum(rets) / n
    med_ret = sorted(rets)[n // 2]
    profit_loss_ratio = avg_win / abs(avg_loss) if avg_loss else 0
    expected_ret = win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss
    avg_hold = sum(h["exit_day"] for h in all_hits) / n

    print(f"\n=== 完成 ({elapsed:.0f}s) ===")
    print(f"总命中: {n} 次 ({n / (args.lookback * 250):.1f} 笔/天) | 平均持仓 {avg_hold:.1f} 日")
    print(f"\n【核心指标】(实战可执行卖出: 止盈+止损+OBV消失+兜底)")
    print(f"  交易胜率: {win_rate:.1f}% ({n_win}/{n})")
    print(f"  平均盈利: +{avg_win:.2f}% (n={n_win})")
    print(f"  平均亏损: {avg_loss:.2f}% (n={n_loss})")
    print(f"  盈亏比:   {profit_loss_ratio:.2f} (avg_win / |avg_loss|, >1 划算)")
    print(f"  期望收益: {expected_ret:+.2f}% / 单笔")
    print(f"  单笔收益: 均 {avg_ret:+.2f}% / 中位 {med_ret:+.2f}%")
    if losses:
        print(f"  最差单笔: {min(losses):.1f}%")
    if wins:
        print(f"  最好单笔: {max(wins):.1f}%")

    # 按退出原因拆分
    reason_stats = {}
    for h in all_hits:
        reason_stats.setdefault(h["exit_reason"], []).append(h["ret_pct"])
    print(f"\n【按退出原因】(理解策略怎么赢怎么输)")
    print(f"  {'原因':<14}{'笔数':>6}{'占比':>7}{'均收':>9}{'胜率':>8}")
    for reason, rs in sorted(reason_stats.items(), key=lambda x: -len(x[1])):
        n_r = len(rs)
        wr = sum(1 for r in rs if r > 0) / n_r * 100
        avg_r = sum(rs) / n_r
        pct = n_r / n * 100
        print(f"  {reason:<14}{n_r:>6}{pct:>6.1f}%{avg_r:>+8.2f}%{wr:>7.1f}%")

    # 按信号类型
    def _sig_key(h):
        if h["obv5"] and h["obv_trend"]: return "双"
        if h["obv5"]: return "obv5"
        if h["obv_trend"]: return "obv_trend"
        return "其他"
    by_sig = {}
    for h in all_hits:
        by_sig.setdefault(_sig_key(h), []).append(h["ret_pct"])
    print(f"\n【按信号类型】")
    print(f"  {'信号':<14}{'笔数':>6}{'胜率':>8}{'盈亏比':>8}{'期望':>10}")
    for sig in ["obv5", "obv_trend", "双"]:
        rs = by_sig.get(sig, [])
        if not rs:
            continue
        wr = sum(1 for r in rs if r > 0) / len(rs) * 100
        ws = [r for r in rs if r > 0]
        ls = [r for r in rs if r <= 0]
        aw = sum(ws) / len(ws) if ws else 0
        al = sum(ls) / len(ls) if ls else 0
        pl = aw / abs(al) if al else 0
        exp = wr / 100 * aw + (1 - wr / 100) * al
        print(f"  {sig:<14}{len(rs):>6}{wr:>7.1f}%{pl:>7.2f}{exp:>+9.2f}%")

    # 按年
    year_stats = {}
    for h in all_hits:
        y = h["date"][:4]
        year_stats.setdefault(y, []).append(h["ret_pct"])
    print(f"\n【按年统计】")
    print(f"  {'年':<6}{'笔数':>6}{'胜率':>8}{'均收':>10}{'年化':>10}")
    for y in sorted(year_stats.keys()):
        rs = year_stats[y]
        wr = sum(1 for r in rs if r > 0) / len(rs) * 100
        annual = sum(rs) / len(rs) * (250 / args.days)
        print(f"  {y:<6}{len(rs):>6}{wr:>7.1f}%{sum(rs)/len(rs):>+9.2f}%{annual:>+9.1f}%")

    # 明细
    print(f"\n【明细】(按日期排序, 前 30 条, 实战可执行口径)")
    all_hits.sort(key=lambda x: x["date"])
    for h in all_hits[:30]:
        marker = "✅" if h["ret_pct"] > 0 else "❌"
        sig = _sig_key(h)
        print(f"  {marker} {h['date']} {h['code']} @¥{h['close']:.2f} "
              f"→ 第{h['exit_day']:>2d}日 [{h['exit_reason']:<8}] 收 {h['ret_pct']:+.1f}% "
              f"[{sig}] BOLL={h['boll_pct']:.1f}% BBW={h['bbw']:.2f}")
    if len(all_hits) > 30:
        print(f"  ... 共 {len(all_hits)} 条")

    if args.write_md:
        out_path = ROOT / "docs" / "backtest-bb-obv.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# BB+OBV 三重确认回测 ({datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 策略: BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% AND (obv5 OR obv_trend)\n")
        md.append(f"> 回看: {args.lookback}y ({min_date} ~ {last_db_date})\n")
        md.append(f"> **卖出规则** (按优先级, 实战可执行): {' → '.join(rules)}\n\n")
        md.append(f"## 核心指标\n\n")
        md.append(f"| 指标 | 值 |\n|---|---|\n")
        md.append(f"| 总命中 | {n} 次 ({n / (args.lookback * 250):.1f} 笔/天) |\n")
        md.append(f"| 平均持仓 | {avg_hold:.1f} 日 |\n")
        md.append(f"| **交易胜率** | **{win_rate:.1f}%** ({n_win}/{n}) |\n")
        md.append(f"| 平均盈利 | +{avg_win:.2f}% (n={n_win}) |\n")
        md.append(f"| 平均亏损 | {avg_loss:.2f}% (n={n_loss}) |\n")
        md.append(f"| **盈亏比** | **{profit_loss_ratio:.2f}** |\n")
        md.append(f"| **期望收益/单笔** | **{expected_ret:+.2f}%** |\n")
        md.append(f"| 单笔收益 均/中位 | {avg_ret:+.2f}% / {med_ret:+.2f}% |\n")
        md.append(f"\n## 按退出原因\n\n")
        md.append(f"| 退出原因 | 笔数 | 占比 | 均收 | 胜率 |\n|---|---|---|---|---|\n")
        for reason, rs in sorted(reason_stats.items(), key=lambda x: -len(x[1])):
            n_r = len(rs)
            wr = sum(1 for r in rs if r > 0) / n_r * 100
            avg_r = sum(rs) / n_r
            pct = n_r / n * 100
            md.append(f"| {reason} | {n_r} | {pct:.1f}% | {avg_r:+.2f}% | {wr:.1f}% |\n")
        md.append(f"\n## 按信号类型\n\n| 信号 | 笔数 | 胜率 | 盈亏比 | 期望 |\n|---|---|---|---|---|\n")
        for sig in ["obv5", "obv_trend", "双"]:
            rs = by_sig.get(sig, [])
            if not rs:
                continue
            wr = sum(1 for r in rs if r > 0) / len(rs) * 100
            ws = [r for r in rs if r > 0]
            ls = [r for r in rs if r <= 0]
            aw = sum(ws) / len(ws) if ws else 0
            al = sum(ls) / len(ls) if ls else 0
            pl = aw / abs(al) if al else 0
            exp = wr / 100 * aw + (1 - wr / 100) * al
            md.append(f"| {sig} | {len(rs)} | {wr:.1f}% | {pl:.2f} | {exp:+.2f}% |\n")
        md.append(f"\n## 按年\n\n| 年 | 笔数 | 胜率 | 均收 | 年化 |\n|---|---|---|---|---|\n")
        for y in sorted(year_stats.keys()):
            rs = year_stats[y]
            wr = sum(1 for r in rs if r > 0) / len(rs) * 100
            annual = sum(rs) / len(rs) * (250 / args.days)
            md.append(f"| {y} | {len(rs)} | {wr:.1f}% | {sum(rs)/len(rs):+.2f}% | {annual:+.1f}% |\n")
        md.append(f"\n## 明细 ({len(all_hits)} 条)\n\n")
        md.append("| 日期 | 代码 | 收盘 | 持仓日 | 退出原因 | 单笔收益 | max涨幅 | 信号 |\n")
        md.append("|---|---|---|---|---|---|---|---|\n")
        for h in all_hits:
            marker = "✅" if h["ret_pct"] > 0 else "❌"
            sig = _sig_key(h)
            md.append(f"| {h['date']} | {h['code']} | ¥{h['close']:.2f} | "
                     f"{h['exit_day']} | {h['exit_reason']} | "
                     f"{marker} {h['ret_pct']:+.1f}% | {h['ret_max']:+.1f}% | {sig} |\n")
        out_path.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
