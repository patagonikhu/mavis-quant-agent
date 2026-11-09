"""
tools/batch/wyckoff_a2m_scan.py — 全市场扫描"最近 N 根 K 线 A→M 转变"

判定逻辑 (V13):
  对每只票最近 40 根 K 线 (判定窗口 n_tail=40):
  - wyckoff_judge 输出 stage (Accumulation / Markup / Distribution)
  - ① 当前 (最后) 根 stage == 'Markup'                    (必须)
  - ② 最近 40 根里 Markup 占比 < 10% (允许 ≤ 3 根)       (排除"反复 M"假突破)
  - ③ 转折点在最近 lookback 根内 (默认 5)
  - ④ 单日涨跌 > 20% 跳过                                 (除权/复牌)
  - ⑤ K 线长度 ≥ 11 根
  命中后定位 trans_idx: 从右往左第一根 != M 的位置

数据一致性 (跟 /t-analyze 100%):
  - 同源: 同 K 线 parquet + 同 wyckoff_judge 算法 + 同 arrs 预算
  - 已实测 (5 只票 5 周期全等): /tmp/diff_judges2.py
"""
from __future__ import annotations
import argparse
import sys
import time
import duckdb
import multiprocessing as mp
import os
import shutil
import pandas as pd
from pathlib import Path
from tools.storage.store import DataStore


def _filter_by_st(codes: list[str]) -> list[str]:
    """排除 ST/*ST 股 (退市风险)。"""
    if not codes:
        return []
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
    df = duckdb.execute(f"""
        SELECT ts_code FROM read_parquet('data/history/stock_basic/stock_basic.parquet')
        WHERE ts_code IN ({in_clause})
          AND (name LIKE '%ST%' OR name LIKE '%*ST%')
    """).fetchdf()
    st_codes = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
    return [c for c in codes if c not in st_codes]


def _load_all_klines(codes: list[str], limit_per_code: int = 2000) -> dict[str, pd.DataFrame]:
    """1 次 SQL 拿多只票的最近 N 根 K 线, 返回 {code: DataFrame}。"""
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH'" for c in codes)
    sql = f"""
        WITH ranked AS (
            SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
            FROM read_parquet('data/history/daily/*.parquet')
            WHERE ts_code IN ({in_clause})
        )
        SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg
        FROM ranked
        WHERE rn <= {limit_per_code}
        ORDER BY ts_code, trade_date
    """
    df = duckdb.execute(sql).fetchdf()
    df['date'] = df['trade_date']
    df['volume'] = df['vol']
    out = {}
    for code, sub in df.groupby('ts_code'):
        out[code.split('.')[0]] = sub.sort_values('trade_date').reset_index(drop=True)
    return out


def _process_one(code: str, df: pd.DataFrame, lookback: int = 5) -> dict | None:
    """单只票威科夫当前阶段检测。

    日线: 直调 wyckoff_judge (build_kline_features + scan_sub_events 预算一次)
    判定: V13 严判 — 当前 M + 最近 40 根 M 占比 < 10% + 转折在最近 lookback 根内
    """
    if df is None or len(df) < 220:
        return None

    closes = df['close'].tolist()
    highs  = df['high'].tolist()
    lows   = df['low'].tolist()
    vols   = df['volume'].tolist()
    opens  = df['open'].tolist() if 'open' in df.columns else closes
    pct_chgs = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
    dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
    n = len(closes)
    window = min(250, n)

    from tools.factors.wyckoff.stage_factor import wyckoff_judge
    from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
    from tools.factors.kline_arrays import build_kline_features

    arrs = build_kline_features(closes, highs, lows, vols, window=window)

    sub_events_all = scan_sub_events(
        closes, highs, lows, vols,
        o=opens, pct_chg=pct_chgs,
        period_label="daily", code=code,
        as_of_idx=None,
        dates=dates_,
        precomputed_arrs=arrs,
    )

    n_tail = max(40, 11)
    stages = []
    closes_tail = []
    dates_tail = []
    for k in range(n_tail):
        i = n - n_tail + k
        sub_set = {e["name"] for e in sub_events_all if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_set,
                             window=arrs['window'], period_label="daily")
        if out is None:
            stages.append("?")
        else:
            stages.append(out.get("stage", "?"))
        closes_tail.append(closes[i])
        dates_tail.append(dates_[i])

    last_chg = abs(closes_tail[-1] / closes_tail[-2] - 1) if len(closes_tail) >= 2 and closes_tail[-2] > 0 else 0
    if last_chg > 0.20:
        return None
    if stages[-1] != "Markup":
        return None
    if len(stages) < 11:
        return None

    trans_idx = 0
    for k in range(len(stages) - 1, 0, -1):
        if stages[k - 1] != "Markup":
            trans_idx = k
            break
    if trans_idx == 0 or trans_idx < len(stages) - lookback:
        return None

    m_window_size = min(40, len(stages))
    recent_stages = stages[-m_window_size:]
    m_count = sum(1 for s in recent_stages if s == "Markup")
    m_ratio = m_count / m_window_size
    if m_ratio >= 0.10:
        return None

    return {
        "code": code,
        "trans_idx": trans_idx,
        "trans_date": dates_tail[trans_idx],
        "trans_close": closes_tail[trans_idx],
        "last_date": dates_tail[-1],
        "last_close": closes_tail[-1],
        "prev_stage": stages[trans_idx - 1] if trans_idx > 0 else "?",
        "curr_stage": stages[-1],
        "trans_stages": stages,
        "m_ratio": m_ratio,
    }

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.batch.a2m_report_common import (  # noqa: E402
    scan_existing_reports as _scan_existing_reports,
    stamp_report_status as _stamp_report_status,
    report_worker as _report_worker,
)


def _worker(code: str, df: pd.DataFrame) -> dict | None:
    """单只票检测当前威科夫 M 阶段 (df 来自 _load_all_klines 一次 SQL 预加载)"""
    try:
        return _process_one(code, df)
    except Exception as e:
        return {"code": code, "error": f"{type(e).__name__}: {e}"}


def load_codes_all_market() -> list[str]:
    """全市场 codes (data/history/daily 有数据的, 排除北交所 .BJ)"""
    df = duckdb.execute(
        "SELECT DISTINCT ts_code FROM read_parquet('data/history/daily/*.parquet')"
        " WHERE ts_code NOT LIKE '%.BJ'"
    ).fetchdf()
    return [c.split('.')[0] for c in df['ts_code'].tolist()]


def load_codes_watchlist(types: list[str] | None) -> list[tuple[str, str]]:
    """从 watchlist.json 取 (code, name)"""
    import json
    wl_path = ROOT / "config" / "watchlist.json"
    with open(wl_path) as f:
        wl = json.load(f)
    out = []
    for s in wl.get("stocks", []):
        lt = s.get("list_type", "")
        if types and lt not in types:
            continue
        code = s.get("code", "").strip()
        name = s.get("name", "").strip() or code
        if code:
            out.append((code, name))
    return out


_STOCK_BASIC_PATHS = (
    "data/history/stock_basic/stock_basic.parquet",
    "data/stock_basic.parquet",
    "data/basics/stock_basic.parquet",
)

def load_stock_basic() -> dict:
    """code → (name, industry) 映射"""
    for path in _STOCK_BASIC_PATHS:
        try:
            df = duckdb.execute(
                f"SELECT ts_code, name, industry FROM read_parquet('{path}')"
            ).fetchdf()
            if len(df) > 0:
                return {row['ts_code'].split('.')[0]: (row['name'], row['industry'])
                        for _, row in df.iterrows()}
        except Exception:
            continue
    return {}


def _print_effective_conditions(args) -> None:
    """打印本次运行实际生效的完整条件。"""
    # --- 扫描范围 ---
    if args.watchlist_only:
        scope_desc = f"watchlist (类型: {args.watchlist_types})"
    else:
        scope_desc = "全市场"

    # --- 前置过滤链 ---
    if args.include_st:
        st_desc = "❌ OFF (--include-st 显式包含 ST/*ST)"
    elif args.exclude_st:
        st_desc = "✅ ON  排除 ST/*ST"
    else:
        st_desc = "❌ OFF (不排除 ST)"

    if args.exclude_pe > 0:
        pe_desc = f"✅ ON  PE > {args.exclude_pe:.0f} 排除"
    else:
        pe_desc = "❌ OFF (--exclude-pe 0, 不限 PE)"

    quality_thr = {"basic": 0, "strong": 10, "elite": 20}.get(args.with_quality, 0)
    if args.min_growth is not None:
        quality_thr = args.min_growth
        growth_note = "(--min-growth 覆盖档位)"
    else:
        growth_note = "(档位默认值)"
    if args.with_quality != "off":
        q_parts = [f"营收yoy>{quality_thr:g}%", f"净利yoy>{quality_thr:g}%"]
        if args.min_roe > 0:
            q_parts.append(f"ROE>{args.min_roe:g}%")
        if args.min_gross_margin > 0:
            q_parts.append(f"毛利率>{args.min_gross_margin:g}%")
        q_desc = "✅ ON  " + " + ".join(q_parts)
    else:
        q_desc = "❌ OFF (--with-quality off)"

    if args.max_net_debt_ratio > 0:
        lv_desc = f"✅ ON  净负债/市值 < {args.max_net_debt_ratio:g}"
    else:
        lv_desc = "❌ OFF (--max-net-debt-ratio 0, 不限杠杆)"

    fin_desc = "✅ ON  剔除亏损股 (PE_TTM 缺失/<=0)" if args.with_finance else "❌ OFF (--with-finance 未开)"

    print("\n" + "=" * 72)
    print("  本次运行生效条件 (Effective Conditions)")
    print("=" * 72)
    print(f"\n  【扫描范围】")
    print(f"    scope            : {scope_desc}")
    print(f"    lookback         : 最近 {args.lookback} 根 K 线")
    print(f"    V13 判定窗口     : 固定 40 根 (硬编码, 不可调)")

    print(f"\n  【前置过滤链】(按执行顺序)")
    print(f"    1. exclude-st        : {st_desc}")
    print(f"    2. exclude-pe        : {pe_desc}")
    print(f"    3. with-quality      : {q_desc}")
    if args.with_quality != "off":
        print(f"         └ 档位={args.with_quality}, yoy 门槛={quality_thr:g}% {growth_note}")
        print(f"         └ ⚠️ 毛利率门槛作用在 grossprofit_margin (水平值/绝对%), 不是毛利率同比增速")
    print(f"    4. max-net-debt-ratio: {lv_desc}")
    print(f"    5. with-finance      : {fin_desc}")

    print(f"\n  【V13 严判】(硬编码, 无 flag 可调)")
    print(f"    ① 当前 K 线 stage == Markup            (必须)")
    print(f"    ② 最近 40 根 K 线 M 占比 < 10%         (允许 ≤3 根, 排除'反复 M'假突破)")
    print(f"    ③ 转折点在最近 lookback 根内")
    print(f"    ④ 单日涨跌幅 > 20% 跳过                 (除权/复牌)")
    print(f"    ⑤ K 线长度 ≥ 11 根")

    print(f"\n  【输出】")
    write_md_on = args.write_md and not args.no_write_md
    full_report_on = args.full_report and not args.no_full_report
    print(f"    汇总 md             : {args.output if write_md_on else '❌ 不落盘 (--no-write-md)'}")
    print(f"    详报                : {'✅ 跑 (前 %d 只)' % args.report_top if full_report_on else '❌ 不跑 (--no-full-report)'}")
    if args.refresh:
        print(f"    详报模式            : 🔄 --refresh (默认, 不清空!)")
        print(f"                         A. 刷新 = 已有 且 本次命中 → 重新生成")
        print(f"                         B. 补充 = 没有 且 本次命中 → 新生成")
        if args.keep_stale:
            print(f"                         C. 留档 = 已有 但本次未命中 → 不刷新, 只标注 ⚠️")
        else:
            print(f"                         C. 留档 = 已有 但本次未命中 → 也刷新数据 + 标注 ⚠️")
        if args.stale_only:
            print(f"                         ⚠️ --stale-only: 只跑 C 类, 不跑本次命中")
    elif full_report_on:
        print(f"    详报目录            : docs/{args.report_subdir}/"
              f" {'(保留旧文件)' if args.no_clean else '(先清空旧文件)'}")
    print(f"    详报 workers        : {args.workers if args.workers else 'os.cpu_count()'}")
    print("=" * 72 + "\n")


def main():
    ap = argparse.ArgumentParser(description="Wyckoff A→M 转变扫描")
    ap.add_argument("--lookback", type=int, default=5, help="看最近 N 根 K 线 (默认 5)")
    ap.add_argument("--all-market", action="store_true", default=True,
                    help="全市场扫 (默认)")
    ap.add_argument("--watchlist-types", default="持仓,blowout,自选",
                    help="watchlist 类型过滤 (用 --watchlist-only 启用)")
    ap.add_argument("--watchlist-only", action="store_true",
                    help="只扫 watchlist (默认全市场)")
    ap.add_argument("--write-md", action="store_true", default=True,
                    help="落盘 docs/wyckoff-a2m.md (默认 True)")
    ap.add_argument("--no-write-md", action="store_true", help="不落盘")
    ap.add_argument("--output", default="docs/wyckoff-a2m.md", help="md 输出路径")
    ap.add_argument("--full-report", action="store_true", default=True,
                    help="命中后逐只跑 /t-analyze 详报 (默认 True)")
    ap.add_argument("--no-full-report", action="store_true", help="不跑详报 (只输出扫描汇总)")
    ap.add_argument("--report-top", type=int, default=100,
                    help="--full-report 时只详报前 N 只 (默认 100, 0=全部)")
    ap.add_argument("--report-subdir", default="wyckoff-a2m",
                    help="详报输出子目录 (默认 wyckoff-a2m)")
    ap.add_argument("--no-clean", action="store_true",
                    help="不清理 docs/<subdir> 已有 md")
    ap.add_argument("--clean", action="store_true",
                    help="显式清空 docs/<subdir> 旧 md (不可恢复, 慎用)")
    ap.add_argument("--refresh", action="store_true", default=True,
                    help="刷新模式 (默认开): 不清空目录, A刷新/B补充/C留档")
    ap.add_argument("--keep-stale", action="store_true",
                    help="--refresh 时, C 类不刷新数据, 只标注状态")
    ap.add_argument("--stale-only", action="store_true",
                    help="只刷新 C 类 (历史留档), 不跑本次命中票的详报")
    ap.add_argument("--with-finance", action="store_true",
                    help="加 finance 过滤: 剔除亏损股 (PE_TTM 缺失或 <= 0) + PE>500 异常")
    ap.add_argument("--with-quality", choices=["off", "basic", "strong", "elite"], default="basic",
                    help="quality 过滤: off/basic(yoy>0)/strong(yoy>10)/elite(yoy>20) (默认 basic)")
    ap.add_argument("--exclude-st", action="store_true", default=True,
                    help="排除 ST/*ST 股 (默认 True)")
    ap.add_argument("--include-st", action="store_true",
                    help="包含 ST/*ST 股 (覆盖 --exclude-st)")
    ap.add_argument("--exclude-pe", type=float, default=60.0,
                    help="前置 PE 过滤上限 (排除 pe > N; 0=不限, 默认 60)")
    ap.add_argument("--min-growth", type=float, default=None,
                    help="营收/净利 yoy 门槛 %%, 覆盖 --with-quality 档位门槛 (默认 None=用档位)")
    ap.add_argument("--min-roe", type=float, default=5.0,
                    help="ROE 绝对值下限 %% (默认 5, 0=不限)")
    ap.add_argument("--min-gross-margin", type=float, default=20.0,
                    help="毛利率绝对值下限 %% (默认 20, 0=不限)")
    ap.add_argument("--max-net-debt-ratio", type=float, default=0.5,
                    help="净负债/总市值上限 (默认 0.5, 0=不限)")
    ap.add_argument("--workers", type=int, default=0,
                    help="详报多进程 worker 数 (默认 0=os.cpu_count(), 1=单进程)")
    ap.add_argument("--bench-report", nargs="?", const=12, type=int,
                    help="跳过扫描, 只跑 N 个固定代码详报测试不同 workers")
    args = ap.parse_args()

    _print_effective_conditions(args)

    if args.watchlist_only:
        wl = load_codes_watchlist([t.strip() for t in args.watchlist_types.split(",") if t.strip()])
        codes = [c for c, _ in wl]
        scope = f"watchlist({args.watchlist_types})"
    else:
        codes = load_codes_all_market()
        scope = "全市场"

    if not codes:
        print("❌ 无代码可跑", file=sys.stderr)
        sys.exit(1)

    funnel = []
    funnel.append(("原始候选 (scope={})".format(scope), "—", len(codes), len(codes)))

    if args.bench_report:
        bench_codes = ['301487', '605189', '605228', '600869', '301592',
                       '600177', '601107', '605566', '002232', '600408',
                       '603188', '000829'][:args.bench_report]
        print(f"\n=== BENCH: 跳扫描, 跑 {len(bench_codes)} 只固定代码详报对比 workers ===", flush=True)
        tasks = [(c, '', '_bench') for c in bench_codes]
        t0 = time.time()
        with mp.get_context("spawn").Pool(1) as p:
            list(p.imap_unordered(_report_worker, tasks))
        t1 = time.time() - t0
        print(f"  1 spawn-worker: {t1:.1f}s ({len(bench_codes)} 只 = {t1/len(bench_codes):.2f}s/只)", flush=True)
        cpu_n = os.cpu_count() or 4
        nw = min(cpu_n, len(bench_codes))
        t0 = time.time()
        with mp.get_context("spawn").Pool(nw) as p:
            list(p.imap_unordered(_report_worker, tasks))
        tn = time.time() - t0
        speedup = t1 / tn if tn > 0 else 0
        print(f"  {nw} spawn-worker: {tn:.1f}s ({len(bench_codes)} 只 = {tn/len(bench_codes):.2f}s/只)")
        print(f"  ⚡ 加速: {speedup:.2f}x")
        out_dir = Path("docs/_bench")
        if out_dir.exists():
            for f in out_dir.glob("*.md"):
                f.unlink()
        sys.exit(0)

    if args.exclude_st and not args.include_st:
        before_n = len(codes)
        kept = _filter_by_st(codes)
        print(f"  exclude-st (ST/*ST 退市风险): {before_n} → {len(kept)} 票", flush=True)
        funnel.append(("step1 exclude-st", "ST/*ST 退市风险", before_n, len(kept)))
        codes = kept
        if not codes:
            print("❌ ST 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    if args.exclude_pe > 0:
        before_n = len(codes)
        kept = DataStore.filter_codes_by_pe(codes, args.exclude_pe)
        print(f"  exclude-pe (PE>{args.exclude_pe:.0f} 高估值): {before_n} → {len(kept)} 票", flush=True)
        funnel.append(("step2 exclude-pe", f"PE>{args.exclude_pe:.0f}", before_n, len(kept)))
        codes = kept
        if not codes:
            print(f"❌ PE>{args.exclude_pe:.0f} 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    if args.with_quality != "off":
        quality_thr = {"basic": 0, "strong": 10, "elite": 20}[args.with_quality]
        if args.min_growth is not None:
            quality_thr = args.min_growth
        before_n = len(codes)
        kept_codes = DataStore.filter_codes_by_quality(
            codes, or_yoy_min=quality_thr, np_yoy_min=quality_thr,
            roe_min=args.min_roe, gm_min=args.min_gross_margin)
        extra = []
        if args.min_roe is not None:
            extra.append(f"ROE>{args.min_roe:.0f}%")
        if args.min_gross_margin is not None:
            extra.append(f"毛利>{args.min_gross_margin:.0f}%")
        extra_s = " + " + " + ".join(extra) if extra else ""
        print(f"  quality={args.with_quality} (营收yoy>{quality_thr}% + 净利yoy>{quality_thr}%"
              f"{extra_s}): {before_n} → {len(kept_codes)} 票", flush=True)
        funnel.append((f"step3 with-quality={args.with_quality}",
                       f"营收yoy/净利yoy>{quality_thr}%" + (f" + {' + '.join(extra)}" if extra else ""),
                       before_n, len(kept_codes)))
        codes = kept_codes
        if not codes:
            print("❌ 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    if args.max_net_debt_ratio > 0:
        before_n = len(codes)
        kept = DataStore.filter_codes_by_leverage(codes, args.max_net_debt_ratio)
        print(f"  max-net-debt-ratio (净负债/市值>{args.max_net_debt_ratio:.2f}): "
              f"{before_n} → {len(kept)} 票", flush=True)
        funnel.append((f"step4 max-net-debt-ratio", f"净负债/市值<{args.max_net_debt_ratio:.2f}",
                       before_n, len(kept)))
        codes = kept
        if not codes:
            print("❌ 杠杆过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    funnel.append(("step5 wyckoff_a2m 扫描", f"lookback={args.lookback}", len(codes), len(codes)))

    basic = load_stock_basic()
    print(f"=== Wyckoff A→M 扫描 (scope: {scope}, lookback: {args.lookback}) ===")
    print(f"  任务: {len(codes)} 只, 单进程顺序 (scan_sub_events GIL 锁死, ThreadPool 0 加速)")
    print(f"  链路: DataStore → AnalysisEngine(WyckoffStrategy) → compute_factor_history")
    print(f"  复用跟 /t-analyze 同源, 0 网络\n")

    t0 = time.time()
    print(f"  1) 加载全市场 K 线 (1 次 SQL) ...", flush=True)
    kline_map = _load_all_klines(codes, limit_per_code=2000)
    print(f"  1) 完成: {len(kline_map)} 票 ({time.time()-t0:.2f}s)", flush=True)

    t1 = time.time()
    hits = []
    errors = []
    completed = 0
    for code in codes:
        df = kline_map.get(code)
        if df is None or len(df) < 220:
            continue
        r = _process_one(code, df, lookback=args.lookback)
        if r is None:
            pass
        elif 'error' not in r:
            hits.append(r)
        else:
            errors.append(r)
        completed += 1
        if completed % 500 == 0 or completed == len(codes):
            print(f"  2) {completed}/{len(codes)} done, {len(hits)} hits, {len(errors)} errs, {time.time()-t1:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n=== 完成 {len(hits)}/{len(codes)} 命中 ({len(errors)} errors), {elapsed:.1f}s ===\n")

    hits.sort(key=lambda x: (x['trans_idx'], x['code']))

    if args.with_finance and hits:
        hit_codes_list = [h['code'] for h in hits]
        kept_finance, pe_map = DataStore.filter_codes_by_finance(hit_codes_list, pe_max=500.0)
        kept_set = set(kept_finance)
        before = len(hits)
        hits = [h for h in hits if h['code'] in kept_set]
        for h in hits:
            info = pe_map.get(h['code'])
            if info:
                h['pe'] = info.get('pe')
                h['pe_ttm'] = info.get('pe_ttm')
        funnel.append(("step6 with-finance", "剔亏损股(PE_TTM缺失/<=0) + PE>500", before, len(hits)))
        print(f"\n=== finance 过滤: {before} → {len(hits)} 只 "
              f"(剔除 {before - len(hits)} 只亏损/异常) ===\n")

    sub_dir = Path(f"docs/{args.report_subdir}")
    existing = _scan_existing_reports(sub_dir) if args.refresh else {}
    hit_codes = {r['code'] for r in hits}
    lines = []
    lines.append(f"# Wyckoff A→M 转变扫描 ({scope})\n")
    try:
        last_trade_date = str(duckdb.execute(
            "SELECT MAX(trade_date) FROM read_parquet('data/history/daily/*.parquet')"
        ).fetchone()[0])[:8]
    except Exception:
        last_trade_date = hits[0]['last_date'] if hits else 'N/A'
    lines.append(f"> 数据日期: {last_trade_date} (lookback={args.lookback} 根 K 线)")
    lines.append(f"> 命中: {len(hits)} 只 / {len(codes)} ({len(hits)/len(codes)*100:.1f}%)")
    lines.append(f"> 扫描耗时: {elapsed:.1f}s (单进程顺序)")
    lines.append(f"> 数据源: AnalysisEngine + compute_factor_history (跟 /t-analyze 同源, 0 网络)")
    lines.append(f"> 判定: **V13 严判** — 当前 M + 最近 40 根 K 线 M 占比 < 10% (排除\"反复 M 假突破\")\n")

    lines.append("## 本次生效条件\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| scope | {scope} |")
    lines.append(f"| lookback | 最近 {args.lookback} 根 K 线 |")
    lines.append(f"| ① exclude-st | {'ON (排除 ST/*ST)' if (args.exclude_st and not args.include_st) else 'OFF'} |")
    lines.append(f"| ② exclude-pe | {f'ON (PE>{args.exclude_pe:.0f} 排除)' if args.exclude_pe > 0 else 'OFF (不限)'} |")
    if args.with_quality != "off":
        _thr = {"basic": 0, "strong": 10, "elite": 20}.get(args.with_quality, 0)
        if args.min_growth is not None:
            _thr = args.min_growth
        _q = f"ON (营收yoy>{_thr:g}% + 净利yoy>{_thr:g}%"
        if args.min_roe > 0:
            _q += f" + ROE>{args.min_roe:g}%"
        if args.min_gross_margin > 0:
            _q += f" + 毛利率>{args.min_gross_margin:g}%"
        _q += ")"
    else:
        _q = "OFF"
    lines.append(f"| ③ with-quality | {_q} |")
    lines.append(f"| ④ max-net-debt-ratio | {f'ON (净负债/市值<{args.max_net_debt_ratio:g})' if args.max_net_debt_ratio > 0 else 'OFF (不限)'} |")
    lines.append(f"| ⑤ with-finance | {'ON (剔除亏损股)' if args.with_finance else 'OFF'} |")
    lines.append(f"| V13 严判 | 当前 M + 最近 40 根 M 占比<10% + 转折在 lookback 根内 + 单日涨跌<20% + K线≥11根 |")
    lines.append("")

    lines.append("## 过滤漏斗\n")
    lines.append("| 步骤 | 过滤 | 阈值 | 前 → 后 | 剩余 |")
    lines.append("|---|---|---|---|---|")
    for idx, (step_name, params, before_n, after_n) in enumerate(funnel):
        lines.append(f"| {step_name} | {params or '—'} | — | {before_n} → {after_n} | {after_n} |")
    lines.append(f"| **A→M 命中** | V13 严判 | lookback={args.lookback} | {len(codes)} → {len(hits)} | **{len(hits)}** |\n")

    if args.refresh and existing:
        stale_codes = sorted(c for c in existing if c not in hit_codes)
        if stale_codes:
            lines.append("## 历史留档 (曾命中, 本次未命中)\n")
            lines.append(f"> 目录里有但本次扫描没命中的票 —— 可能已被 PE/质量/杠杆过滤, 或威科夫阶段已离开 Markup.")
            lines.append(f"> `--refresh` 下这些票的详报**会一并刷新到最新交易日**, 文件头标 ⚠️。\n")
            lines.append("| 代码 | 名称 | 状态 |")
            lines.append("|---|---|---|")
            for c in stale_codes:
                lines.append(f"| `{c}` | {existing[c]['name']} | ⚠️ 本次未命中 (留档) |")
            lines.append("")

    if hits:
        if args.with_finance:
            lines.append("| 转变天数 | 代码 | 名称 | 行业 | 转变日 | 转变价 | 当前价 | 涨幅% | PE | PE_TTM | 阶段序列 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        else:
            lines.append("| 转变天数 | 代码 | 名称 | 行业 | 转变日 | 转变价 | 当前价 | 涨幅% | 阶段序列 |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in hits:
            code = r['code']
            name, industry = basic.get(code, (code, "—"))
            stages_str = "/".join(s[0] for s in r['trans_stages'][-(args.lookback + 1):])
            chg = (r['last_close'] / r['trans_close'] - 1) * 100
            if args.with_finance:
                pe = r.get('pe', '')
                pe_ttm = r.get('pe_ttm', '')
                pe_s = f"{pe:.1f}" if isinstance(pe, (int, float)) else "—"
                pe_ttm_s = f"{pe_ttm:.1f}" if isinstance(pe_ttm, (int, float)) else "—"
                lines.append(
                    f"| **T-{r['trans_idx']}** | `{code}` | {name} | {industry} | "
                    f"{r['trans_date']} | ¥{r['trans_close']:.2f} | ¥{r['last_close']:.2f} | "
                    f"{chg:+.1f}% | {pe_s} | {pe_ttm_s} | {stages_str} |"
                )
            else:
                lines.append(
                    f"| **T-{r['trans_idx']}** | `{code}` | {name} | {industry} | "
                    f"{r['trans_date']} | ¥{r['trans_close']:.2f} | ¥{r['last_close']:.2f} | "
                    f"{chg:+.1f}% | {stages_str} |"
                )

    out_path = Path(args.output)
    if args.write_md:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"📄 落盘: {out_path}\n")
    else:
        print("\n".join(lines))

    if args.no_full_report:
        args.full_report = False
    if args.no_write_md:
        args.write_md = False

    if args.refresh and args.full_report:
        tasks: list = []
        if not args.stale_only:
            for r in hits:
                code = r['code']
                nm = basic.get(code, (code, "—"))[0]
                try:
                    _chg = (r['last_close'] / r['trans_close'] - 1) * 100
                except Exception:
                    _chg = 0.0
                try:
                    _sg = "/".join(s[0] for s in r['trans_stages'][-(args.lookback + 1):])
                except Exception:
                    _sg = ""
                note = f" — 转变日 {r.get('trans_date', '?')}, 涨幅 {_chg:+.1f}%, 阶段 {_sg}"
                tasks.append((code, nm, args.report_subdir, "hit", note))
        stale_list = [c for c in existing if c not in hit_codes]
        if not args.keep_stale:
            for code in stale_list:
                nm = existing[code]["name"]
                tasks.append((code, nm, args.report_subdir, "stale", ""))
        n_hit_tasks = len([t for t in tasks if t[3] == "hit"])
        n_stale_tasks = len([t for t in tasks if t[3] == "stale"])
        print(f"\n=== step 2 [--refresh]: 刷新/生成详报 (→ docs/{args.report_subdir}/) ===")
        print(f"  A. 本次命中 (刷新+生成): {n_hit_tasks} 只")
        print(f"  C. 历史留档 (本次未命中): {n_stale_tasks} 只"
              + ("  [已跳过刷新, 只标注]" if args.keep_stale else "  [会刷新数据]"))
        print(f"  目录现有: {len(existing)} 份 → 跑完预计: {len(existing) + len(hit_codes - set(existing))} 份")
        if not tasks:
            print("  无任务可跑")
            return
    elif args.full_report and hits:
        target_hits = hits[:args.report_top] if args.report_top > 0 else hits
        if sub_dir.exists() and args.clean and not args.no_clean:
            old_mds = list(sub_dir.glob("*.md"))
            trash = Path.home() / ".Trash" / f"mavis-a2m-{args.report_subdir}-{int(time.time())}"
            trash.mkdir(parents=True, exist_ok=True)
            for p in old_mds:
                try:
                    shutil.move(str(p), str(trash / p.name))
                except Exception:
                    p.unlink()
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只 → docs/{args.report_subdir}/) ===")
            print(f"  ⚠️ 清空旧 md: {len(old_mds)} 份 (--clean 显式指定, 已移到 {trash})")
        else:
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只 → docs/{args.report_subdir}/) ===")
            if args.clean:
                print(f"  子目录不存在, 无需清空")
            elif args.no_clean:
                print(f"  --no-clean: 保留历史详报")
            else:
                print(f"  保留历史详报 (默认不清理, 加 --clean 才清空)")
        tasks = [(r['code'], basic.get(r['code'], (r['code'], "—"))[0], args.report_subdir)
                 for r in target_hits]
    else:
        tasks = []

    if tasks:
        n_workers = args.workers if args.workers > 0 else (os.cpu_count() or 4)
        n_workers = min(n_workers, len(tasks))
        if args.refresh:
            _tasks_wrapped = tasks
        else:
            _tasks_wrapped = [(t[0], t[1], t[2], "hit", "") for t in tasks]
        rt0 = time.time()
        ok_n = 0
        stamped_hit = 0
        stamped_stale = 0
        if n_workers == 1:
            print(f"  1 worker (单进程回退)", flush=True)
            for idx, t in enumerate(_tasks_wrapped, 1):
                code, name, sub, st, note = t
                print(f"\n[{idx}/{len(_tasks_wrapped)}] {code} {name} ...", flush=True)
                code_r, ok, path_or_err, elapsed = _report_worker((code, name, sub))
                if ok:
                    ok_n += 1
                    if st == "hit":
                        stamped_hit += _stamp_report_status(Path(path_or_err), "hit", note)
                    else:
                        stamped_stale += _stamp_report_status(Path(path_or_err), "stale")
                    print(f"  ✅ {code_r} → {path_or_err} ({elapsed:.2f}s)", flush=True)
                else:
                    print(f"  ⚠️  {code_r} 失败: {path_or_err} ({elapsed:.2f}s)", flush=True)
        else:
            print(f"  {n_workers} workers (multiprocessing spawn)", flush=True)
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=n_workers) as pool:
                for idx, result in enumerate(pool.imap_unordered(_report_worker,
                                                                [(t[0], t[1], t[2]) for t in _tasks_wrapped]), 1):
                    code, ok, path_or_err, elapsed = result
                    mark = "✅" if ok else "⚠️ "
                    sys.stdout.write(f"\r  [{idx}/{len(_tasks_wrapped)}] {mark} {code} ({elapsed:.2f}s)")
                    sys.stdout.flush()
                    if ok:
                        ok_n += 1
                        t = next((x for x in _tasks_wrapped if x[0] == code), None)
                        if t:
                            if t[3] == "hit":
                                stamped_hit += _stamp_report_status(Path(path_or_err), "hit", t[4])
                            else:
                                stamped_stale += _stamp_report_status(Path(path_or_err), "stale")
            print()
        print(f"\n=== step 2 完成 {ok_n}/{len(_tasks_wrapped)} ({ok_n/len(_tasks_wrapped)*100:.0f}%), {time.time()-rt0:.0f}s "
              f"(workers={n_workers}) ===")
        if args.refresh:
            print(f"  状态标记: 命中 {stamped_hit} 份 / 留档 {stamped_stale} 份")


if __name__ == "__main__":
    main()
