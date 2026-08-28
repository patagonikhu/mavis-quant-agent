"""
batch_backtest.py — 信号回测引擎 (SQLite 缓存版)

缓存优先:
  1. 查 SQLite → 命中则直接读
  2. 缺失则 AnalysisEngine.analyze_history → 写 SQLite

用法:
  python tools/batch/batch_backtest.py --signal Spring --days 30 --threshold 10
  python tools/batch/batch_backtest.py --signal Accumulation --days 30
"""
import argparse, datetime, json, os, sys, time

_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _root)

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── 参数解析 ──────────────────────────────────────────────
ap = argparse.ArgumentParser(description="信号回测引擎")
ap.add_argument("--signal", action="append", dest="signals", default=[],
                help="信号 (可多次, AND). 例: --signal Spring --signal LPS")
ap.add_argument("--days", type=int, default=30, help="持仓期 (默认30)")
ap.add_argument("--threshold", type=float, default=10.0, help="涨幅阈值%% (默认10%%)")
ap.add_argument("--lookback", type=int, default=5, help="回看年数 (默认5)")
ap.add_argument("--step", type=int, default=20,
                help="计算间隔，默认20（需与 warm_cache --step 一致，否则缓存miss；step=1最密但慢）")
ap.add_argument("--codes", nargs="+", default=None)
ap.add_argument("--all", action="store_true", help="全watchlist")
ap.add_argument("--portfolio", action="store_true", help="仅持仓")
ap.add_argument("--write-md", action="store_true")
ap.add_argument("--workers", type=int, default=4, help="并发数 (默认4)")
ap.add_argument("--no-cache", action="store_true", help="跳过缓存，强制重算")
args = ap.parse_args()

if not args.signals:
    print("❌ 需指定 --signal"); sys.exit(1)

# ── 数据加载 ────────────────────────────────────────────────
t0 = time.time()
from tools.kline_store import DataStore
from tools.analysis.signal_cache import get_cached, write_batch, get_stats

ds = DataStore()

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
print(f"缓存: {'停用' if args.no_cache else '启用'}")

# ── AnalysisResult → dict 标准化 ───────────────────────────
def _result_to_row(r) -> dict:
    """把 AnalysisResult 或 dict 统一转成 dict（兼容两种来源）"""
    if isinstance(r, dict):
        return r
    raw = getattr(r, "raw", None) or {}
    wy = raw.get("wyckoff", {}) or {}
    chan = raw.get("chan", {}) or {}

    se = wy.get("sub_events", [])
    se_names = {e.get("name") for e in se} if isinstance(se, list) else set()

    bsp = chan.get("buy_sell_points", {}) or {}
    if not isinstance(bsp, dict):
        bsp = {}

    def _bsp_bool(key):
        return 1 if bsp.get(key) else None

    return {
        "wy_stage":        wy.get("stage"),
        "wy_spring":       1 if "Spring" in se_names else None,
        "wy_lps":          1 if "LPS" in se_names else None,
        "wy_evr":          1 if "EVR" in se_names else None,
        "wy_sos":          1 if "SOS" in se_names else None,
        "wy_compression":  1 if "Compression" in se_names else None,
        "wy_trendpullback": 1 if "TrendPullback" in se_names else None,
        "wy_markup_entry": 1 if "MarkupEntry" in se_names else None,
        "wy_distributionstart": 1 if "DistributionStart" in se_names else None,
        "wy_utad":         1 if "UTAD" in se_names else None,
        "chan_1buy":       _bsp_bool("🟢1买"),
        "chan_2buy":       _bsp_bool("🟢2买"),
        "chan_3buy":       _bsp_bool("🟢3买"),
        "chan_1sell":      _bsp_bool("🔴1卖"),
        "chan_2sell":      _bsp_bool("🔴2卖"),
        "chan_3sell":      _bsp_bool("🔴3卖"),
    }


# ── 信号匹配 ─────────────────────────────────────────────────
SE_COLS = {
    "Spring":           "wy_spring",
    "LPS":             "wy_lps",
    "EVR":             "wy_evr",
    "SOS":             "wy_sos",
    "Compression":     "wy_compression",
    "TrendPullback":   "wy_trendpullback",
    "MarkupEntry":     "wy_markup_entry",
    "DistributionStart":"wy_distributionstart",
    "UTAD":            "wy_utad",
}


def match_signal(row, signals: list[str]) -> bool:
    """在标准化 dict row 上匹配信号"""
    for sig in signals:
        sig = sig.strip()
        hit = False
        if sig in SE_COLS:
            hit = bool(row.get(SE_COLS[sig]))
        elif sig in ("Accumulation", "Markup", "Distribution", "Markdown"):
            hit = row.get("wy_stage") == sig
        elif sig in ("1买", "2买", "3买", "1卖", "2卖", "3卖"):
            hit = bool(row.get(f"chan_{sig}"))
        if not hit:
            return False
    return True


# ── 单只回测 ────────────────────────────────────────────────
def backtest_one(code: str):
    t1 = time.time()
    ctx = ds.get_ctx(code)
    if not ctx.kline:
        return code, [], 0, time.time() - t1, False

    kline = ctx.kline
    cutoff = kline[-1]["trade_date"]
    cutoff_dt = datetime.datetime.strptime(cutoff[:8], "%Y%m%d")
    lookback_dt = cutoff_dt - datetime.timedelta(days=args.lookback * 365)
    lookback_str = lookback_dt.strftime("%Y%m%d")
    kline = [k for k in kline if k["trade_date"] >= lookback_str]

    dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
    n = len(dates)
    date_to_idx = {d: i for i, d in enumerate(dates)}

    # ── 缓存优先 ──────────────────────────────────────────
    rows = {}
    from_cache = False
    if not args.no_cache:
        cached = get_cached(code, dates)
        if cached:
            from_cache = True   # kline 是增量追加的，stale 检查无意义（用 INSERT OR REPLACE 覆盖）
            rows = cached

    # 缺失部分 → 重算（无论是否有缓存，只要不完整就补算）
    missing = [d for d in dates if d not in rows]
    if missing:
        from tools.analysis.analysis_engine import AnalysisEngine
        history = AnalysisEngine().analyze_history(ctx, missing)
        to_write = {}
        for d, result in history.items():
            if d in missing:
                rows[d] = _result_to_row(result)
                to_write[d] = result
        if to_write:
            write_batch(code, kline, to_write)

    # 匹配信号
    hits = []
    for d in dates:
        r = rows.get(d)
        if r is None:
            continue
        if match_signal(r, args.signals):
            idx = date_to_idx.get(d)
            if idx is None or idx >= n - args.days - 1:
                continue
            buy_price = kline[idx]["close"]
            future = kline[idx + 1: idx + 1 + args.days]
            if not future:
                continue
            max_price = max(k["high"] for k in future)
            ret = (max_price / buy_price - 1) * 100
            hits.append({"date": d, "price": buy_price, "return": ret, "code": code})

    from_cache_label = "📦缓存" if from_cache else "⚡重算"
    return code, hits, n, time.time() - t1, from_cache


# ── 并发执行 ────────────────────────────────────────────────
print(f"并发 {args.workers} worker ...")
results_all = []
total_steps = 0
done = 0
cached_count = 0

with ThreadPoolExecutor(max_workers=args.workers) as ex:
    futs = {ex.submit(backtest_one, code): code for code in CODES}
    for fut in as_completed(futs):
        code, hits, steps, elapsed, from_cache = fut.result()
        results_all.extend(hits)
        total_steps += steps
        done += 1
        if from_cache:
            cached_count += 1
        tag = "📦" if from_cache else "⚡"
        print(f"  [{done}/{len(CODES)}] {tag} {code}: {len(hits)}命中 {elapsed:.1f}s")

# ── 统计 ────────────────────────────────────────────────────
valid = [h for h in results_all if h["return"] is not None]
returns = [h["return"] for h in valid]
hits_pass = [h for h in valid if h["return"] >= args.threshold]

print(f"\n{'='*60}")
print(f"信号: {args.signals}")
print(f"回看 {args.lookback}y | 持仓 {args.days}d | 阈值 {args.threshold}%")
print(f"step={args.step} | 命中: {len(valid)} 次 | 缓存命中: {cached_count}/{len(CODES)} 只")
print(f"缓存状态: {get_stats()}")

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
        md.append(f"| 最大跌幅 | {min(returns):+.1f}% |\n")
        md.append(f"| 缓存命中 | {cached_count}/{len(CODES)} |\n\n")
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
