"""rolling_dd_backtest.py — 滚动 3 年回撤超跌策略回测

策略定义:
  1. 近 N 年内 max_dd 跌 ≥ 70%  (近 3 年曾大跌)
  2. 当前价 距 近 N 年最低 < gap%  (现在接近近 3 年底部)

实战可执行卖出规则:
  1. 止盈: 涨 ≥ take_profit% 当日触达, 次日开盘卖
  2. 止损: 跌 ≤ -stop_loss% 当日触达, 次日开盘卖
  3. 兜底: 持有 max_hold 日收盘卖

跟 t-near-low 的核心区别: 5y low → 近 N 年 low (滚动窗口)
  - t-near-low: 5y 内最深, 现在距 5y 全局最低
  - rolling_dd: 近 3 年内最深, 现在距近 3 年最低 (避免历史最低的 bias)
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

# 模块顶层 import
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.kline_store import DataStore


def find_max_drawdown_window(closes, dates, lookback_days=None):
    """在传入的窗口内找最深回撤
    Args:
        closes: 已经是窗口的 closes (调用方自己切片)
        dates:  同样切片过的 dates (保留兼容, 函数未使用)
        lookback_days: 保留兼容, 不再使用

    Returns: (max_dd_pct, low_idx, high_idx)  idx 是窗口内 idx
    """
    n = len(closes)
    if n < 2:
        return None, None, None
    max_dd = 0
    max_low_i = 0
    max_high_i = 0
    high = closes[0]
    high_i = 0
    for i in range(1, n):
        if closes[i] > high:
            high = closes[i]
            high_i = i
        dd = (closes[i] - high) / high
        if dd < max_dd:
            max_dd = dd
            max_low_i = i
            max_high_i = high_i
    return max_dd, max_low_i, max_high_i


def scan_one(code: str, lookback_years: int, drop: float, recent_drop: float,
             take_profit: float, stop_loss: float, max_hold: int,
             basic_map: dict) -> list:
    """扫单只票所有"近 N 年 low 附近" 命中日"""
    try:
        sb = basic_map.get(code, {})
        if sb.get("name") and "ST" in sb["name"]:
            return []
        mcap = sb.get("market_cap", 0) or 0
        if mcap < 50:
            return []

        ctx = DataStore.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 250:
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
                ))
            except (TypeError, ValueError):
                continue
        if len(rows) < 250:
            return []
        rows.sort(key=lambda x: x[0])
        dates = [r[0] for r in rows]
        closes = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        opens = [r[4] for r in rows]

        lookback_days = lookback_years * 252

        # 回看窗口
        cutoff_date = (datetime.now() - timedelta(days=lookback_years * 365)).strftime("%Y%m%d")
        hits = []
        last_hit_date = None

        for i in range(60, len(rows) - max_hold):
            d = dates[i]
            if d < cutoff_date:
                continue

            # 条件 1: 近 N 年 max_dd 跌 ≥ drop
            window_start = max(0, i - lookback_days)
            window_closes = closes[window_start: i + 1]
            if len(window_closes) < 60:
                continue
            max_dd, _, _ = find_max_drawdown_window(window_closes, dates, len(window_closes))
            if max_dd is None or max_dd > -drop:
                continue

            # 条件 2: 近 252 日 (1 年) 从高点回撤 ≥ recent_drop
            recent_start = max(0, i - 252)
            recent_closes = closes[recent_start: i + 1]
            if len(recent_closes) < 30:
                continue
            recent_high = max(recent_closes)
            cur = closes[i]
            recent_dd = (cur - recent_high) / recent_high
            if recent_dd > -recent_drop:
                continue

            # dedup: 同票 30 日内只记第一笔
            if last_hit_date is not None:
                last_dt = datetime.strptime(last_hit_date, "%Y%m%d")
                cur_dt = datetime.strptime(d, "%Y%m%d")
                if (cur_dt - last_dt).days < 30:
                    continue

            # === 模拟一笔完整交易 ===
            entry_price = closes[i]
            exit_day = None
            exit_price = None
            exit_reason = "max_hold"

            for j in range(1, max_hold + 1):
                idx = i + j
                if idx >= len(rows):
                    break
                if take_profit > 0 and highs[idx] >= entry_price * (1 + take_profit / 100):
                    if idx + 1 < len(rows):
                        exit_price = opens[idx + 1]
                    else:
                        exit_price = closes[idx]
                    exit_reason = f"止盈+{take_profit}%"
                    exit_day = j
                    break
                if stop_loss > 0 and lows[idx] <= entry_price * (1 - stop_loss / 100):
                    if idx + 1 < len(rows):
                        exit_price = opens[idx + 1]
                    else:
                        exit_price = closes[idx]
                    exit_reason = f"止损-{stop_loss}%"
                    exit_day = j
                    break
            if exit_price is None:
                exit_idx = i + max_hold
                if exit_idx < len(rows):
                    exit_price = closes[exit_idx]
                    exit_day = max_hold
                else:
                    continue

            ret_pct = (exit_price / entry_price - 1) * 100

            hits.append({
                "code": code,
                "date": d,
                "close": entry_price,
                "exit_day": exit_day,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "ret_pct": ret_pct,
                "recent_dd": recent_dd * 100,
                "max_dd": max_dd * 100,
            })
            last_hit_date = d
        return hits
    except Exception as e:
        import traceback
        traceback.print_exc()
        return []


def _load_all_basic() -> dict:
    try:
        import pyarrow.parquet as pq
        tbl = pq.read_table("data/history/stock_basic/stock_basic.parquet")
        df = tbl.to_pandas()
        result = {}
        for _, row in df.iterrows():
            result[row["code"]] = {
                "name": row.get("name", "") or "",
                "industry": row.get("industry", "") or "",
                "market_cap": 0.0,
            }
        try:
            db = pq.read_table("data/history/daily_basic/2026Q3.parquet").to_pandas()
            db_latest = db.sort_values("trade_date").groupby("ts_code").tail(1)
            for _, r in db_latest.iterrows():
                code = str(r["ts_code"]).split(".")[0]
                # total_mv 单位是万元, 转亿
                total_mv = float(r.get("total_mv", 0) or 0)
                mcap = total_mv / 10000 if total_mv > 0 else 0
                if code in result:
                    result[code]["market_cap"] = mcap
        except Exception as e:
            print(f"[WARN] daily_basic 加载失败: {e}", flush=True)
        return result
    except Exception as e:
        print(f"[WARN] _load_all_basic 失败: {e}", flush=True)
        return {}


def _load_codes_with_cache(min_date: str) -> list:
    conn = sqlite3.connect(str(DB))
    rows = conn.execute(
        "SELECT DISTINCT code FROM analysis_cache WHERE date_str >= ?",
        (min_date,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def main():
    ap = argparse.ArgumentParser(description="滚动 N 年回撤超跌策略回测")
    ap.add_argument("--lookback", type=int, default=3, help="滚动窗口年数 (默认 3)")
    ap.add_argument("--days", type=int, default=30, help="最大持仓期 (默认 30)")
    ap.add_argument("--drop", type=float, default=0.30, help="近 N 年 max_dd 阈值 (默认 0.30)")
    ap.add_argument("--recent-drop", type=float, default=0.15,
                    help="近 1 年(252 日)从高点回撤阈值 (默认 0.15 = 跌 15%)")
    ap.add_argument("--take-profit", type=float, default=15.0, help="止盈 % (默认 15)")
    ap.add_argument("--stop-loss", type=float, default=5.0, help="止损 % (默认 5)")
    ap.add_argument("--workers", type=int, default=4, help="并发数")
    ap.add_argument("--write-md", action="store_true", help="写 docs/backtest-rolling-dd.md")
    args = ap.parse_args()

    rules = []
    if args.take_profit > 0: rules.append(f"止盈+{args.take_profit}%")
    if args.stop_loss > 0:   rules.append(f"止损-{args.stop_loss}%")
    rules.append(f"兜底{args.days}日")
    print(f"=== 滚动 {args.lookback}y 回撤超跌策略回测 ===")
    print(f"    命中条件: 近 {args.lookback}y max_dd ≥ {args.drop*100:.0f}% + 近 1 年从高点跌 ≥ {args.recent_drop*100:.0f}%")
    print(f"    卖出规则: {' → '.join(rules)}")

    today = datetime.now()
    lookback_start = (today - timedelta(days=args.lookback * 365)).strftime("%Y%m%d")
    print(f"    回看窗口: {lookback_start} ~ 今天")

    codes = _load_codes_with_cache(lookback_start)
    print(f"\n主线程预加载 stock_basic ...", flush=True)
    basic_map = _load_all_basic()
    print(f"  {len(basic_map)} 只票基础信息已加载", flush=True)
    print(f"\n扫描 {len(codes)} 只票 ({args.workers} workers, 含市值 ≥ 50亿 + 非 ST)...")

    t0 = time.time()
    all_hits = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan_one, code, args.lookback, args.drop, args.recent_drop,
                          args.take_profit, args.stop_loss, args.days, basic_map): code
                for code in codes}
        done = 0
        for fut in as_completed(futs):
            done += 1
            all_hits.extend(fut.result())
            if done % 200 == 0:
                print(f"  [{done}/{len(codes)}] 累计命中 {len(all_hits)} | {time.time()-t0:.0f}s",
                      flush=True)
    elapsed = time.time() - t0

    n = len(all_hits)
    print(f"\n=== 完成 ({elapsed:.0f}s) ===")
    print(f"总命中: {n} 次 (平均每天 {n / (args.lookback * 250):.1f} 笔)")
    if n == 0:
        return

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

    print(f"  平均持仓: {avg_hold:.1f} 日")
    print(f"\n【核心指标】(实战可执行: 止盈+止损+兜底)")
    print(f"  交易胜率: {win_rate:.1f}% ({n_win}/{n})")
    print(f"  平均盈利: +{avg_win:.2f}% (n={n_win})")
    print(f"  平均亏损: {avg_loss:.2f}% (n={n_loss})")
    print(f"  盈亏比:   {profit_loss_ratio:.2f}")
    print(f"  期望收益: {expected_ret:+.2f}% / 单笔")
    print(f"  单笔收益: 均 {avg_ret:+.2f}% / 中位 {med_ret:+.2f}%")
    if losses:
        print(f"  最差单笔: {min(losses):.1f}%")
    if wins:
        print(f"  最好单笔: {max(wins):.1f}%")

    reason_stats = {}
    for h in all_hits:
        reason_stats.setdefault(h["exit_reason"], []).append(h["ret_pct"])
    print(f"\n【按退出原因】")
    print(f"  {'原因':<14}{'笔数':>6}{'占比':>7}{'均收':>9}{'胜率':>8}")
    for reason, rs in sorted(reason_stats.items(), key=lambda x: -len(x[1])):
        n_r = len(rs)
        wr = sum(1 for r in rs if r > 0) / n_r * 100
        avg_r = sum(rs) / n_r
        pct = n_r / n * 100
        print(f"  {reason:<14}{n_r:>6}{pct:>6.1f}%{avg_r:>+8.2f}%{wr:>7.1f}%")

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

    print(f"\n【明细】(前 20 条)")
    all_hits.sort(key=lambda x: x["date"])
    for h in all_hits[:20]:
        marker = "✅" if h["ret_pct"] > 0 else "❌"
        print(f"  {marker} {h['date']} {h['code']} @¥{h['close']:.2f} "
              f"→ 第{h['exit_day']:>2d}日 [{h['exit_reason']:<8}] 收 {h['ret_pct']:+.1f}% "
              f"近期回撤={h['recent_dd']:.0f}% max_dd={h['max_dd']:.0f}%")

    if args.write_md:
        out_path = ROOT / "docs" / "backtest-rolling-dd.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md = [f"# 滚动 {args.lookback}y 回撤超跌策略回测 ({datetime.now().strftime('%Y-%m-%d')})\n\n"]
        md.append(f"> 策略: 近 {args.lookback}y max_dd ≥ {args.drop*100:.0f}% + 近 1 年从高点跌 ≥ {args.recent_drop*100:.0f}%\n")
        md.append(f"> 卖出规则: {' → '.join(rules)}\n\n")
        md.append(f"## 核心指标\n\n")
        md.append(f"| 指标 | 值 |\n|---|---|\n")
        md.append(f"| 总命中 | {n} 次 ({n / (args.lookback * 250):.1f} 笔/天) |\n")
        md.append(f"| 平均持仓 | {avg_hold:.1f} 日 |\n")
        md.append(f"| **交易胜率** | **{win_rate:.1f}%** ({n_win}/{n}) |\n")
        md.append(f"| 平均盈利 | +{avg_win:.2f}% (n={n_win}) |\n")
        md.append(f"| 平均亏损 | {avg_loss:.2f}% (n={n_loss}) |\n")
        md.append(f"| **盈亏比** | **{profit_loss_ratio:.2f}** |\n")
        md.append(f"| **期望/单笔** | **{expected_ret:+.2f}%** |\n")
        md.append(f"| 单笔 均/中位 | {avg_ret:+.2f}% / {med_ret:+.2f}% |\n")
        md.append(f"\n## 按退出原因\n\n| 原因 | 笔数 | 占比 | 均收 | 胜率 |\n|---|---|---|---|---|\n")
        for reason, rs in sorted(reason_stats.items(), key=lambda x: -len(x[1])):
            n_r = len(rs)
            wr = sum(1 for r in rs if r > 0) / n_r * 100
            avg_r = sum(rs) / n_r
            pct = n_r / n * 100
            md.append(f"| {reason} | {n_r} | {pct:.1f}% | {avg_r:+.2f}% | {wr:.1f}% |\n")
        md.append(f"\n## 按年\n\n| 年 | 笔数 | 胜率 | 均收 | 年化 |\n|---|---|---|---|---|\n")
        for y in sorted(year_stats.keys()):
            rs = year_stats[y]
            wr = sum(1 for r in rs if r > 0) / len(rs) * 100
            annual = sum(rs) / len(rs) * (250 / args.days)
            md.append(f"| {y} | {len(rs)} | {wr:.1f}% | {sum(rs)/len(rs):+.2f}% | {annual:+.1f}% |\n")
        md.append(f"\n## 明细 ({len(all_hits)} 条)\n\n")
        md.append("| 日期 | 代码 | 收盘 | 持仓日 | 退出原因 | 单笔收益 | gap% | max_dd% |\n")
        md.append("|---|---|---|---|---|---|---|---|\n")
        for h in all_hits:
            marker = "✅" if h["ret_pct"] > 0 else "❌"
            md.append(f"| {h['date']} | {h['code']} | ¥{h['close']:.2f} | "
                     f"{h['exit_day']} | {h['exit_reason']} | "
                     f"{marker} {h['ret_pct']:+.1f}% | {h['recent_dd']:.0f}% | {h['max_dd']:.0f}% |\n")
        out_path.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
