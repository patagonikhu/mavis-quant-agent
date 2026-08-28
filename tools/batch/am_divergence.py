"""
tools/batch/am_divergence.py — 全市场扫描 布林%触底 + 三重确认

算法:
  1. sync_incremental() 补缺失交易日
  2. 遍历本地历史库所有股票（DataStore.list_codes()）
  3. 每只只跑 2 个 strategy（ChanStrategy/MacdDivergenceStrategy）
  4. 找最近 window 天内 布林% ≤ boll_threshold 且 MA120偏离 ≥ ma120_min 的触底行
  5. 检查前 30d 内缠论底背驰 + MACD 底背驰（可选）
  6. 命中 → 输出清单

用法:
  bash tools/with_venv.sh python -m tools.batch.am_divergence
  bash tools/with_venv.sh python -m tools.batch.am_divergence --boll-threshold 10
  bash tools/with_venv.sh python -m tools.batch.am_divergence --no-macd
  bash tools/with_venv.sh python -m tools.batch.am_divergence --workers 16
"""

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# 单只扫描
# ============================================================

def scan_one(code: str, window: int, require_macd: bool,
             boll_threshold: float, ma120_min: float, ma120_max: float) -> dict | None:
    """扫描单只股票，找布林%触底 + 确认信号。返回命中信息或 None。"""
    try:
        from tools.kline_store import DataStore
        from tools.analysis.analysis_engine import ChanStrategy
        from tools.analysis.factor_history import compute_factor_history

        strategies = [ChanStrategy]
        ctx = DataStore.get_ctx(code, kline_only=True, limit=250)
        if len(ctx.kline) < 60:
            return None

        lookback = window + 40
        rows = compute_factor_history(ctx, step=1, lookback=lookback,
                                      strategies=strategies)
        if not rows:
            return None

        # 找最近 window 天内 布林% ≤ threshold + MA120 未破位
        recent = rows[-window:] if len(rows) >= window else rows

        trigger_row = None
        for row in reversed(recent):
            bpct  = row.get('boll_pct')
            ma120 = row.get('ma120_dev')
            if bpct is None or ma120 is None:
                continue
            if 0 <= bpct <= boll_threshold and ma120_min <= ma120 <= ma120_max:
                trigger_row = row
                break

        if trigger_row is None:
            return None

        trigger_date  = trigger_row['date']
        trigger_price = trigger_row.get('close', 0)
        trigger_bpct  = trigger_row.get('boll_pct', 0)
        trigger_ma120 = trigger_row.get('ma120_dev', 0)

        trigger_idx = next((i for i, r in enumerate(rows) if r['date'] == trigger_date), None)
        if trigger_idx is None:
            return None

        lookback_rows = rows[max(0, trigger_idx - 30): trigger_idx + 1]

        # 缠论买卖点（30天内 czsc 信号）
        chan_date = None
        for r in reversed(lookback_rows):
            sigs = r.get("czsc_signals") or {}
            if sigs.get("1买") or sigs.get("3买") or sigs.get("MACD底背"):
                chan_date = r["date"]
                break

        has_chan = chan_date is not None
        has_macd = False  # MacdDivergenceStrategy 已移除，改用 czsc 信号
        confirmed = True

        try:
            fmt = "%Y%m%d" if len(trigger_date) == 8 else "%Y-%m-%d"
            dt = datetime.strptime(trigger_date, fmt)
            days_ago = (datetime.now() - dt).days
        except Exception:
            days_ago = 99

        sb       = DataStore.get_stock_basic(code)
        name     = sb.get("name", code) or code
        industry = sb.get("industry", "")

        if has_chan and has_macd:
            triple = "✅✅✅"
        elif has_chan or has_macd:
            triple = "✅✅⬜"
        else:
            triple = "✅⬜⬜"

        return {
            "code":          code,
            "name":          name,
            "industry":      industry,
            "trigger_date":  trigger_date,
            "trigger_price": trigger_price,
            "boll_pct":      trigger_bpct,
            "ma120_dev":     trigger_ma120,
            "chan_date":      chan_date or "—",
            "macd_date":     macd_date or "—",
            "days_ago":      days_ago,
            "triple":        triple,
        }
    except Exception:
        return None


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="全市场扫描 布林%触底 + 三重确认")
    parser.add_argument("--window",         type=int,   default=5,    help="触底窗口天数（默认5）")
    parser.add_argument("--boll-threshold", type=float, default=15.0, help="布林%上限（默认15，越小越严格）")
    parser.add_argument("--ma120-min",      type=float, default=-5.0, help="MA120偏离下限（默认-5%，排除强下跌）")
    parser.add_argument("--ma120-max",      type=float, default=15.0, help="MA120偏离上限（默认15%，排除离均线太远）")
    parser.add_argument("--no-macd",        action="store_true",      help="只要布林触底+缠论，不强求 MACD")
    parser.add_argument("--workers",        type=int,   default=2,    help="并发数（默认2）")
    parser.add_argument("--write-md",       action="store_true",      help="写 docs/am-divergence-watchlist.md")
    parser.add_argument("--limit",          type=int,   default=0,    help="调试：只扫前 N 只（0=全部）")
    parser.add_argument("--min-amount",     type=float, default=3.0,  help="20日均成交额下限（亿元，默认3亿）")
    parser.add_argument("--min-atr-pct",   type=float, default=0.0,  help="ATR(14)/收盘价下限（%，默认0=不过滤，建议4.0）")
    args = parser.parse_args()

    require_macd = not args.no_macd

    print("🔄 同步K线历史...")
    from tools.kline_history_backfill import sync_incremental
    sync_incremental()

    from tools.kline_store import DataStore
    codes = DataStore.list_codes()
    if args.limit:
        codes = codes[:args.limit]

    # 预筛低成交额小票
    if args.min_amount > 0:
        before = len(codes)
        def _check_amount(code):
            try:
                from tools.kline_history_backfill import read_kline
                from tools.kline_store import _to_ts_code
                rows = read_kline(_to_ts_code(code), limit=20)
                if len(rows) < 10:
                    return True
                amounts = [b.get("amount", 0) or 0 for b in rows]
                avg = sum(amounts) / len(amounts) / 1e5
                return avg >= args.min_amount
            except Exception:
                return True
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=8) as ex:
            keep = list(ex.map(_check_amount, codes))
        codes = [c for c, ok in zip(codes, keep) if ok]
        print(f"  过滤小票: {before} → {len(codes)} 只 (去掉 {before-len(codes)} 只)")

    # 预筛低波动率股票
    if args.min_atr_pct > 0:
        before = len(codes)
        def _check_atr(code):
            try:
                from tools.kline_history_backfill import read_kline
                from tools.kline_store import _to_ts_code
                rows = read_kline(_to_ts_code(code), limit=20)
                if len(rows) < 15:
                    return True
                trs = []
                for i in range(1, len(rows)):
                    h = rows[i].get("high", 0) or 0
                    l = rows[i].get("low",  0) or 0
                    pc = rows[i-1].get("close", 0) or 0
                    if h and l and pc:
                        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
                if not trs:
                    return True
                atr14 = sum(trs[-14:]) / min(14, len(trs))
                close = rows[-1].get("close", 0) or 0
                if not close:
                    return True
                return (atr14 / close * 100) >= args.min_atr_pct
            except Exception:
                return True
        from concurrent.futures import ThreadPoolExecutor as _TPE2
        with _TPE2(max_workers=8) as ex:
            keep2 = list(ex.map(_check_atr, codes))
        codes = [c for c, ok in zip(codes, keep2) if ok]
        print(f"  过滤低波动: {before} → {len(codes)} 只 (ATR%≥{args.min_atr_pct}%)")

    print(f"📊 扫描 {len(codes)} 只 | window={args.window}d | "
          f"布林%≤{args.boll_threshold}% | MA120 [{args.ma120_min}%,{args.ma120_max}%] | "
          f"ATR%≥{args.min_atr_pct}% | "
          f"macd={'是' if require_macd else '否'} | {args.workers} 并发")

    t0 = time.time()
    results = []
    done = 0

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {
            ex.submit(scan_one, code, args.window, require_macd,
                      args.boll_threshold, args.ma120_min, args.ma120_max): code
            for code in codes
        }
        for fut in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  进度: {done}/{len(codes)} ({done/len(codes)*100:.0f}%)")
            r = fut.result()
            if r:
                results.append(r)

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["days_ago"])

    print(f"\n⏱️  耗时 {elapsed:.1f}s | 命中 {len(results)} 只\n")
    if not results:
        print("  无命中")
        return

    header = (f"{'代码':<8}{'名称':<12}{'行业':<12}"
              f"{'触底日':<12}{'价格':>6}{'布林%':>6}{'MA120':>6}"
              f"{'缠论日':<12}{'MACD日':<12}{'距今':>5}  三重")
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['code']:<8}{r['name']:<12}{r['industry']:<12}"
              f"{r['trigger_date']:<12}{r['trigger_price']:>6.2f}"
              f"{r['boll_pct']:>5.0f}%{r['ma120_dev']:>+6.1f}%"
              f"{r['chan_date']:<12}{r['macd_date']:<12}"
              f"{r['days_ago']:>4}d  {r['triple']}")

    if args.write_md:
        md_path = ROOT / "docs" / "am-divergence-watchlist.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# 布林%触底 + 三重确认 扫描结果",
            f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"扫描 {len(codes)} 只 | 命中 {len(results)} 只 | "
            f"布林%≤{args.boll_threshold}% | MA120≥{args.ma120_min}%\n",
            f"| 代码 | 名称 | 行业 | 触底日 | 价格 | 布林% | MA120偏离 | 缠论日 | MACD日 | 距今 | 三重 |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            lines.append(
                f"| {r['code']} | {r['name']} | {r['industry']} | "
                f"{r['trigger_date']} | ¥{r['trigger_price']:.2f} | "
                f"{r['boll_pct']:.0f}% | {r['ma120_dev']:+.1f}% | "
                f"{r['chan_date']} | {r['macd_date']} | "
                f"{r['days_ago']}d | {r['triple']} |"
            )
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n✅ 报告已存: {md_path}")


if __name__ == "__main__":
    main()
