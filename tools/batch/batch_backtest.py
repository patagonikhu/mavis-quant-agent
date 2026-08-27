"""
batch_backtest.py — 信号回测引擎

两步回测:
  1. 粗筛 step=5  (快速扫全量, ~5s/只)
  2. 精筛 step=1 ±2天  (精确命中, ~2s/候选日)

用法:
  python tools/batch/batch_backtest.py --signal Spring --days 30 --threshold 10
  python tools/batch/batch_backtest.py --signal Spring --signal fflow:强进货 --days 30
"""
import argparse, datetime, json, os, sys, time

# 让 python tools/batch/batch_backtest.py 能 import tools.*
_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _root)

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── 参数解析 ──────────────────────────────────────────────
ap = argparse.ArgumentParser(description="信号回测引擎")
ap.add_argument("--signal", action="append", dest="signals", default=[],
               help="信号 (可多次, AND). 例: --signal Spring --signal fflow:强进货")
ap.add_argument("--days", type=int, default=30, help="持仓期 (默认30)")
ap.add_argument("--threshold", type=float, default=10.0, help="涨幅阈值%% (默认10%%)")
ap.add_argument("--lookback", type=int, default=5, help="回看年数 (默认5)")
ap.add_argument("--codes", nargs="+", default=None)
ap.add_argument("--all", action="store_true", help="全watchlist")
ap.add_argument("--portfolio", action="store_true", help="仅持仓")
ap.add_argument("--write-md", action="store_true")
ap.add_argument("--workers", type=int, default=4, help="并发数 (默认4)")
args = ap.parse_args()

if not args.signals:
    print("❌ 需指定 --signal"); sys.exit(1)

# ── 数据加载 ────────────────────────────────────────────────
t0 = time.time()
from tools.data_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine

ds = DataStore()
engine = AnalysisEngine()

if args.all:
    wl = json.load(open("data/watchlist.json"))["stocks"]
    CODES = [s["code"] for s in wl]
elif args.codes:
    CODES = args.codes
else:
    wl = json.load(open("data/watchlist.json"))["stocks"]
    CODES = [s["code"] for s in wl if s.get("list_type") == "持仓"]

print(f"[{len(CODES)} 只] 回看 {args.lookback}y | 持仓 {args.days}d | 阈值 {args.threshold}%")
print(f"信号: {args.signals}")

# ── 信号匹配 ────────────────────────────────────────────────
def match_signal(result, signals):
    raw = result.raw
    wy = raw.get("wyckoff") or {}
    chan = raw.get("chan") or {}
    bsp = chan.get("buy_sell_points", {}) if isinstance(chan, dict) else {}
    fflow_v = (raw.get("fflow") or {}).get("verdict", "")
    scene = result.scene or ""
    for sig in signals:
        sig = sig.strip()
        hit = False
        if sig in ("Spring","LPS","EVR","SOS","Compression",
                   "TrendPullback","MarkupEntry","DistributionStart","UTAD"):
            se = wy.get("sub_events", [])
            if isinstance(se, list):
                hit = any(isinstance(e, dict) and e.get("name") == sig for e in se)
        elif sig in ("1买","1买⭐","2买","3买","双中枢","笔结束","吞没"):
            daily_bsp = bsp.get("daily", {}) if isinstance(bsp, dict) else bsp
            if isinstance(daily_bsp, dict):
                hit = sig in daily_bsp
        elif sig in ("Accumulation","Markup","Distribution","Markdown"):
            hit = wy.get("stage") == sig
        elif sig.startswith("fflow:"):
            hit = sig.split(":", 1)[1] in fflow_v
        elif sig.startswith("scene:"):
            hit = sig.split(":", 1)[1] == scene
        if not hit:
            return False
    return True

# ── 单只回测 (粗筛+精筛) ─────────────────────────────────
def backtest_one(code):
    t1 = time.time()
    ctx = ds.get_ctx(code)
    if not ctx.kline:
        return code, [], 0, time.time()-t1

    kline = ctx.kline
    cutoff = kline[-1]["trade_date"]
    cutoff_dt = datetime.datetime.strptime(cutoff[:8], "%Y%m%d")
    lookback_dt = cutoff_dt - datetime.timedelta(days=args.lookback * 365)
    lookback_str = lookback_dt.strftime("%Y%m%d")
    kline = [k for k in kline if k["trade_date"] >= lookback_str]

    dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
    n = len(dates)

    # ── 粗筛 step=5 ──────────────────────────────────────
    dates_coarse = dates[::5]
    coarse_rows = engine.analyze_history(ctx, dates_coarse)

    date_to_idx = {d: i for i, d in enumerate(dates)}

    coarse_candidates = set()
    for d, r in coarse_rows.items():
        if match_signal(r, args.signals):
            coarse_candidates.add(d)

    if not coarse_candidates:
        return code, [], n, time.time()-t1

    # ── 精筛 step=1 ±2天 ───────────────────────────────
    fine_dates = set()
    for sd in coarse_candidates:
        if sd not in date_to_idx:
            continue
        base = date_to_idx[sd]
        for delta in range(-2, 3):
            i = base + delta
            if 0 <= i < n:
                fine_dates.add(dates[i])

    fine_rows = engine.analyze_history(ctx, sorted(fine_dates))

    hits = []
    for d, r in fine_rows.items():
        if match_signal(r, args.signals):
            idx = date_to_idx.get(d)
            if idx is None or idx >= len(kline) - args.days - 1:
                continue
            buy_price = kline[idx]["close"]
            future = kline[idx + 1 : idx + 1 + args.days]
            if not future:
                continue
            max_price = max(k["high"] for k in future)
            ret = (max_price / buy_price - 1) * 100
            hits.append({"date": d, "price": buy_price, "return": ret, "code": code})

    return code, hits, n, time.time()-t1

# ── 并发执行 ────────────────────────────────────────────────
print(f"并发 {args.workers} worker ...")
results_all = []
total_steps = 0
done = 0

with ThreadPoolExecutor(max_workers=args.workers) as ex:
    futs = {ex.submit(backtest_one, code): code for code in CODES}
    for fut in as_completed(futs):
        code, hits, steps, elapsed = fut.result()
        results_all.extend(hits)
        total_steps += steps
        done += 1
        if hits:
            print(f"  [{done}/{len(CODES)}] {code}: {len(hits)} 命中, {elapsed:.0f}s")
        else:
            print(f"  [{done}/{len(CODES)}] {code}: 0 命中, {elapsed:.0f}s")

# ── 统计 ────────────────────────────────────────────────────
valid = [h for h in results_all if h["return"] is not None]
returns = [h["return"] for h in valid]
hits_pass = [h for h in valid if h["return"] >= args.threshold]

print(f"\n{'='*60}")
print(f"信号: {args.signals}")
print(f"回看 {args.lookback}y | 持仓 {args.days}d | 阈值 {args.threshold}%")
print(f"粗筛步: {total_steps} | 精筛命中: {len(valid)} 次")

if valid:
    rate = len(hits_pass) / len(valid) * 100
    avg = sum(returns) / len(returns)
    med = sorted(returns)[len(returns) // 2]
    print(f"命中率: {len(hits_pass)}/{len(valid)} = {rate:.0f}%")
    print(f"均涨幅: {avg:+.1f}% | 中位: {med:+.1f}% | 最大: {max(returns):+.1f}% | 最小: {min(returns):+.1f}%")

    print(f"\n明细 ({len(valid)} 次):")
    for h in sorted(valid, key=lambda x: x["date"]):
        flag = "✅" if h["return"] >= args.threshold else "❌"
        print(f"  {h['date']} {h['code']:8s} @{h['price']:7.2f} → {h['return']:+6.1f}% {flag}")

    if args.write_md:
        sig_str = "_".join(s.replace(" ", "_") for s in args.signals)
        sig_label = " + ".join(args.signals)
        md = [f"# 回测报告: {sig_label}\n\n"]
        md.append(f"| 指标 | 值 |\n|---|---|\n")
        md.append(f"| 信号 | `{sig_label}` |\n")
        md.append(f"| 回看 | {args.lookback}年 |\n")
        md.append(f"| 持仓期 | {args.days}天 |\n")
        md.append(f"| 涨幅阈值 | {args.threshold}% |\n")
        md.append(f"| 命中次数 | {len(valid)} |\n")
        md.append(f"| 命中率 | {rate:.0f}% ({len(hits_pass)}/{len(valid)}) |\n")
        md.append(f"| 均涨幅 | {avg:+.1f}% |\n")
        md.append(f"| 中位涨幅 | {med:+.1f}% |\n")
        md.append(f"| 最大涨幅 | {max(returns):+.1f}% |\n")
        md.append(f"| 最大跌幅 | {min(returns):+.1f}% |\n\n")
        md.append(f"| 日期 | 代码 | 买入价 | {args.days}日最大涨幅 | 结果 |\n")
        md.append(f"|---|---|---|---|---|\n")
        for h in sorted(valid, key=lambda x: x["date"]):
            flag = "✅" if h["return"] >= args.threshold else "❌"
            md.append(f"| {h['date']} | {h['code']} | ¥{h['price']:.2f} | {h['return']:+.1f}% | {flag} |\n")
        out = Path(f"docs/backtest-{sig_str}.md")
        out.write_text("".join(md), encoding="utf-8")
        print(f"\n📄 {out}")
else:
    print("(无命中)")

print(f"\n总耗时: {time.time()-t0:.0f}s")

