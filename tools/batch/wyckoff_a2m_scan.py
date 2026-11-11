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


# ── 过滤链档位 (2026-11-10 加, 2026-11-12 换口径) ───────────────────────
# 公共前置 (两档都生效, 候选池阶段就剔):
#     ① ST/*ST 退市风险        ② 亏损股 (PE_TTM 算不出 = TTM 亏损)
# 命中后分档 (两档互斥):
#     strict → **行业内市值分位 ≤ 10%** (行业地位前 10%)
#     loose  → 命中 A→M 但市值分位不在前 10%
#
# 2026-11-12 换口径 (用户决定): 原来的 PE/质量/杠杆 三闸 **整档作废** ——
# "现在的逻辑效果不好" → 改用**市值位置**。理由:
#   - 市值分位是**静态**的 (窗口末日快照), 不像涨幅分位那样对"刚启动"的票天然吃亏
#   - 跟报告 🏆行业地位 的市值排名**同口径同窗口** (120 交易日) → 两处数字能对上
#   - 代价: 小行业 (≤10 家) 几乎不可能有票进 strict (第 1 名分位也 > 10%)
#
# 为什么门槛放命中后而不是候选池: 放前面 loose 栏永远是空的 ——
# 被门槛剔掉的票压根不会被扫到, 分不出「命中了但位置不够」这一批。
#
# 改门槛改这个字典 (代码内, 跟过滤链放一起)。
FILTER_TIERS: dict[str, dict] = {
    # strict 档: 行业内市值分位前 N% (2026-11-12 用户定调)
    "strict": {
        "industry_pos_pctile": 10.0,   # 市值分位 ≤ 这个值 → strict (0 = 不限, 全进)
    },
    # loose 档: strict 的补集 (命中 A→M 但位置不够), 无额外门槛
    "loose": {},
}

# 🔴 契约 (2026-11-10 用户明确要求): 默认必须 **strict + loose 两档都跑**。
# 单档只是「本次只看一档」的视图, 不是「默认只跑一档」。
# 有回归测试锁这个默认值 (tests/unit/test_a2m_filter_tiers.py), 改之前先问用户。
DEFAULT_TIER = "both"

# 细 flag → 门槛字段 的映射 (显式传了才覆盖 FILTER_TIERS, None = 没传)
TIER_OVERRIDE_KEYS = (
    "industry_pos_pctile",
)


def _hit_row(r: dict, basic: dict, args) -> str:
    """命中票的一行 md (行业位置 / PE / PE_TTM 三列恒有)

    2026-11-12 换口径: 原来的「差在哪」(PE/质量/杠杆) 列 → 换成**行业位置**
    (行业内市值排名 + 分位)。两档同一张表同一列, 都能看位置差在哪。
    """
    code = r['code']
    pos = r.get("ind_pos") or {}
    name = basic.get(code, (code, "—"))[0]
    industry = pos.get("industry") or basic.get(code, (code, "—"))[1]
    stages_str = "/".join(s[0] for s in r['trans_stages'][-(args.lookback + 1):])
    chg = (r['last_close'] / r['trans_close'] - 1) * 100
    pe, pe_ttm = r.get('pe'), r.get('pe_ttm')
    pe_s = f"{pe:.1f}" if isinstance(pe, (int, float)) else "—"
    pe_ttm_s = f"{pe_ttm:.1f}" if isinstance(pe_ttm, (int, float)) else "—"
    if pos:
        rank = pos.get("market_value_rank")
        total = pos.get("market_value_rank_total")
        pct = pos.get("market_value_percentile")
        pos_s = f"{rank}/{total} (前{pct:.1f}%)"
    else:
        pos_s = "—"   # 不在 SW2021 成分表 / 窗口末日没市值 → 判定不了
    return (f"| **T-{r['trans_idx']}** | `{code}` | {name} | {industry} | "
            f"{r['trans_date']} | ¥{r['trans_close']:.2f} | ¥{r['last_close']:.2f} | "
            f"{chg:+.1f}% | {pos_s} | {pe_s} | {pe_ttm_s} | {stages_str} |")


def _worker(code: str, df: pd.DataFrame) -> dict | None:
    """单只票检测当前威科夫 M 阶段 (df 来自 _load_all_klines 一次 SQL 预加载)"""
    try:
        return _process_one(code, df)
    except Exception as e:
        return {"code": code, "error": f"{type(e).__name__}: {e}"}


def scan_batch_worker(task: tuple) -> list[dict]:
    """扫描一批票的 worker (跑在 mp.spawn 子进程, 顶层函数才能 pickle)

    自己从 parquet 读 K 线 (只读本地, 0 网络), 不走主进程传 DataFrame ——
    传 DataFrame 要 pickle 500 万行, 比重读一遍 parquet 还慢。

    Args:
        task: (codes, lookback, limit_per_code) 三元组

    Returns:
        命中列表 (每项是 _process_one 的返回 dict, 含 error 键表示该票异常)
    """
    codes, lookback, limit = task
    kline_map = _load_all_klines(codes, limit_per_code=limit)
    out: list[dict] = []
    for code, df in kline_map.items():
        if df is None or len(df) < 220:
            continue
        try:
            r = _process_one(code, df, lookback=lookback)
        except Exception as e:
            out.append({"code": code, "error": f"{type(e).__name__}: {e}"})
            continue
        if r is not None:
            out.append(r)
    return out


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
    """code → (name, industry) 映射

    2026-11-12 改: 走 DataStore.load_stock_basic ——
    stock_basic.parquet 里的 industry 列已删 (申万旧版), 由 SW2021 成员表 merge 回来。
    """
    try:
        from tools.storage.store import DataStore
        df = DataStore.load_stock_basic()
        if df.empty:
            return {}
        return {str(r["ts_code"]).split(".")[0]: (r.get("name", ""), r.get("industry", ""))
                for _, r in df.iterrows()}
    except Exception:
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

    thr = args.tier_thr

    # strict 档门槛 (2026-11-12 换口径): 行业内**市值分位** ≤ N%
    # 原 PE/质量/杠杆 三闸已整档作废, 不再是门槛, 只作为展示列。
    _pct = thr.get("industry_pos_pctile", 0)
    if _pct > 0:
        pos_desc = f"✅ ON  行业内市值分位 ≤ {_pct:g}% → strict"
    else:
        pos_desc = "❌ OFF (不限, 有位置信息的命中票全进 strict)"

    if args.include_loss:
        fin_desc = "❌ OFF (--include-loss 显式包含亏损股)"
    else:
        fin_desc = "✅ ON  剔除亏损股 (PE_TTM 缺失/<=0)"

    print("\n" + "=" * 72)
    print("  本次运行生效条件 (Effective Conditions)")
    print("=" * 72)
    print(f"\n  【扫描范围】")
    print(f"    scope            : {scope_desc}")
    print(f"    lookback         : 最近 {args.lookback} 根 K 线")
    print(f"    V13 判定窗口     : 固定 40 根 (硬编码, 不可调)")

    print(f"\n  【公共前置】(候选池阶段, 两档都生效)")
    print(f"    1. exclude-st        : {st_desc}")
    print(f"    2. exclude-loss      : {fin_desc}")

    print(f"\n  【strict 档门槛】(命中后分档用, 不满足 → 进 loose 档)")
    print(f"    3. industry-pos-pctile: {pos_desc}")
    print(f"    4. 行业位置口径        : SW2021 二级行业 · 总市值排名 · 窗口 120 交易日 (同报告 🏆行业地位)")
    print(f"    5. PE / 质量 / 杠杆    : 仅展示, 不参与分档 (2026-11-12 起整档作废)")

    tier_desc = {"strict": "只输出 strict 档", "loose": "只输出 loose 档",
                 "both": "两档都输出"}[args.tier]
    print(f"\n  【输出档位】: {args.tier} ({tier_desc})")

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
                    help="strict 档详报输出子目录 (默认 wyckoff-a2m)")
    ap.add_argument("--loose-report-subdir", default=None,
                    help="loose 档详报输出子目录 (默认 <report-subdir>-loose, "
                         "如 docs/wyckoff-a2m-loose/)")
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
    ap.add_argument("--tier", choices=["strict", "loose", "both"], default=DEFAULT_TIER,
                    help="输出档位: strict(只市值前10%) / loose(只其余) / both(两档都出, 默认)")
    ap.add_argument("--exclude-loss", action="store_true", default=True,
                    help="排除亏损股 (PE_TTM 算不出) — 公共前置, 默认 True")
    ap.add_argument("--include-loss", action="store_true",
                    help="包含亏损股 (覆盖 --exclude-loss)")
    ap.add_argument("--with-finance", action="store_true",
                    help="[已废弃] 亏损股现在是公共前置默认就剔, 想保留用 --include-loss")
    ap.add_argument("--exclude-st", action="store_true", default=True,
                    help="排除 ST/*ST 股 (默认 True)")
    ap.add_argument("--include-st", action="store_true",
                    help="包含 ST/*ST 股 (覆盖 --exclude-st)")
    ap.add_argument("--industry-pos-pctile", type=float, default=None,
                    help="strict 档门槛: 行业内市值分位上限 %% (≤ 该值 → strict; "
                         "0=不限, 默认取 FILTER_TIERS = 10)")
    ap.add_argument("--workers", type=int, default=0,
                    help="详报多进程 worker 数 (默认 0=os.cpu_count(), 1=单进程)")
    ap.add_argument("--scan-workers", type=int, default=8,
                    help="扫描并发进程数 (mp.spawn, 默认 8; 0 = 串行)")
    ap.add_argument("--bench-report", nargs="?", const=12, type=int,
                    help="跳过扫描, 只跑 N 个固定代码详报测试不同 workers")
    args = ap.parse_args()

    # 门槛解析: FILTER_TIERS.strict 打底, 显式传的细 flag 覆盖 (None = 没传)
    tier_thr = dict(FILTER_TIERS["strict"])
    for _k in TIER_OVERRIDE_KEYS:
        _v = getattr(args, _k, None)
        if _v is not None:
            tier_thr[_k] = _v
    args.tier_thr = tier_thr
    if args.loose_report_subdir is None:
        args.loose_report_subdir = f"{args.report_subdir}-loose"
    if args.with_finance:
        print("⚠️  --with-finance 已废弃: 亏损股现在是公共前置默认就剔 "
              "(想保留亏损股用 --include-loss)")

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

    # 公共前置②: 剔亏损股 (PE_TTM 算不出 = TTM 亏损)
    if args.exclude_loss and not args.include_loss:
        before_n = len(codes)
        kept, _pe_map = DataStore.filter_codes_by_finance(codes, pe_max=500.0)
        print(f"  exclude-loss (亏损股 PE_TTM 算不出): {before_n} → {len(kept)} 票", flush=True)
        funnel.append(("step2 exclude-loss", "亏损股 + PE>500 异常", before_n, len(kept)))
        codes = kept
        if not codes:
            print("❌ 亏损股过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    funnel.append(("step3 wyckoff_a2m 扫描", f"lookback={args.lookback}", len(codes), len(codes)))

    basic = load_stock_basic()
    print(f"=== Wyckoff A→M 扫描 (scope: {scope}, lookback: {args.lookback}) ===")
    print(f"  任务: {len(codes)} 只, "
          + (f"{max(0, args.scan_workers)} 进程并行 (mp.spawn)" if max(0, args.scan_workers) > 0
             else "单进程顺序 (scan_sub_events 是纯 Python 循环, ThreadPool 无效)"))
    print(f"  链路: DataStore → AnalysisEngine(WyckoffStrategy) → compute_factor_history")
    print(f"  复用跟 /t-analyze 同源, 0 网络\n")

    t0 = time.time()
    t1 = time.time()
    hits = []
    errors = []
    completed = 0
    n_workers = max(0, args.scan_workers)
    if n_workers > 0 and len(codes) > n_workers:
        # 并行扫描: mp.spawn 绕开 GIL (ThreadPool 不行, scan_sub_events 是纯 Python 循环)
        # 不能用 fork —— duckdb fork 后死锁
        chunks = [codes[i::n_workers] for i in range(n_workers)]
        print(f"  2) 并行扫描 ({n_workers} workers, mp.spawn) ...", flush=True)
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            for part in pool.imap_unordered(
                scan_batch_worker,
                [(ch, args.lookback, 2000) for ch in chunks],
            ):
                for r in part:
                    if 'error' in r:
                        errors.append(r)
                    else:
                        hits.append(r)
                completed += len(part)
                print(f"  2) {completed}/{len(codes)} done, {len(hits)} hits, "
                      f"{len(errors)} errs, {time.time()-t1:.0f}s", flush=True)
    else:
        # 串行扫描 (--scan-workers 0)
        print(f"  1) 加载全市场 K 线 (1 次 SQL) ...", flush=True)
        kline_map = _load_all_klines(codes, limit_per_code=2000)
        print(f"  1) 完成: {len(kline_map)} 票 ({time.time()-t0:.2f}s)", flush=True)
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
                print(f"  2) {completed}/{len(codes)} done, {len(hits)} hits, "
                      f"{len(errors)} errs, {time.time()-t1:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n=== 完成 {len(hits)}/{len(codes)} 命中 ({len(errors)} errors), {elapsed:.1f}s ===\n")

    hits.sort(key=lambda x: (x['trans_idx'], x['code']))

    # ── 分档: strict (行业内市值分位 ≤ N%) vs loose (其余, 两档互斥) ──
    #   2026-11-12 换口径: 原来的 PE/质量/杠杆 三闸整档作废, 改用**市值位置**
    thr = args.tier_thr
    hit_codes_list = [h['code'] for h in hits]
    if hit_codes_list:
        from tools.analysis.industry_position import compute_industry_market_cap_ranks
        ind_pos = compute_industry_market_cap_ranks(hit_codes_list)
        pctile = thr["industry_pos_pctile"]

        strict_hits, loose_hits = [], []
        n_no_pos = 0
        for h in hits:
            pos = ind_pos.get(h['code'])
            h['ind_pos'] = pos
            if not pos:
                ok = False              # 判定不了位置 (不在成分表 / 窗口末日没市值) → 不能算 strict
                n_no_pos += 1
            elif pctile <= 0:
                ok = True               # 0 = 不限 → 有位置信息的全进
            else:
                ok = pos["market_value_percentile"] <= pctile
            (strict_hits if ok else loose_hits).append(h)

        # PE 列回填 (两档都显示 PE / PE_TTM —— 只展示, 不参与分档)
        _, pe_map = DataStore.filter_codes_by_finance(hit_codes_list, pe_max=500.0)
        for h in hits:
            info = pe_map.get(h['code'])
            if info:
                h['pe'] = info.get('pe')
                h['pe_ttm'] = info.get('pe_ttm')

        print(f"\n=== 分档: strict {len(strict_hits)} 只 / loose {len(loose_hits)} 只 "
              f"(共 {len(hits)} 命中, 行业市值分位 ≤ {pctile:g}% → strict)"
              f"{f', {n_no_pos} 只判定不了行业位置' if n_no_pos else ''}) ===\n")
    else:
        strict_hits, loose_hits = [], []

    # --tier 决定输出哪档 (both = 全出)
    tier_hits = {"strict": strict_hits, "loose": loose_hits,
                 "both": strict_hits + loose_hits}[args.tier]
    strict_hit_codes = {r['code'] for r in strict_hits}
    loose_hit_codes = {r['code'] for r in loose_hits}

    sub_dir = Path(f"docs/{args.report_subdir}")
    sub_dir_loose = Path(f"docs/{args.loose_report_subdir}")
    existing = _scan_existing_reports(sub_dir)
    existing_loose = _scan_existing_reports(sub_dir_loose)
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
    lines.append(f"> 扫描耗时: {elapsed:.1f}s ("
                 + (f"{max(0, args.scan_workers)} 进程并行 (mp.spawn)" if max(0, args.scan_workers) > 0
                    else "单进程顺序") + ")")
    lines.append(f"> 数据源: AnalysisEngine + compute_factor_history (跟 /t-analyze 同源, 0 网络)")
    lines.append(f"> 判定: **V13 严判** — 当前 M + 最近 40 根 K 线 M 占比 < 10% (排除\"反复 M 假突破\")\n")

    lines.append("## 本次生效条件\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| scope | {scope} |")
    lines.append(f"| lookback | 最近 {args.lookback} 根 K 线 |")
    _thr = args.tier_thr
    lines.append(f"| ① exclude-st (公共前置) | {'ON (排除 ST/*ST)' if (args.exclude_st and not args.include_st) else 'OFF'} |")
    lines.append(f"| ② exclude-loss (公共前置) | {'ON (剔亏损股 PE_TTM 缺失)' if (args.exclude_loss and not args.include_loss) else 'OFF'} |")
    lines.append(f"| ③ 行业市值分位 (strict 门槛) | "
                 f"{'ON (市值分位 ≤ ' + format(_thr['industry_pos_pctile'], 'g') + '% → strict)' if _thr['industry_pos_pctile'] > 0 else 'OFF (不限)'} |")
    lines.append(f"| ④ 行业位置口径 | SW2021 二级行业 · 总市值排名 · 窗口 120 交易日 (同报告 🏆行业地位) |")
    lines.append(f"| 输出档位 | {args.tier} ({'两档都出' if args.tier == 'both' else ('只 strict' if args.tier == 'strict' else '只 loose')}) |")
    lines.append(f"| V13 严判 | 当前 M + 最近 40 根 M 占比<10% + 转折在 lookback 根内 + 单日涨跌<20% + K线≥11根 |")
    lines.append("")

    lines.append("## 过滤漏斗\n")
    lines.append("| 步骤 | 过滤 | 阈值 | 前 → 后 | 剩余 |")
    lines.append("|---|---|---|---|---|")
    for idx, (step_name, params, before_n, after_n) in enumerate(funnel):
        lines.append(f"| {step_name} | {params or '—'} | — | {before_n} → {after_n} | {after_n} |")
    lines.append(f"| **A→M 命中** | V13 严判 | lookback={args.lookback} | {len(codes)} → {len(hits)} | **{len(hits)}** |")
    lines.append(f"| ├ strict 档 | 行业内市值分位 ≤ {_thr['industry_pos_pctile']:g}% | — | {len(hits)} → {len(strict_hits)} | **{len(strict_hits)}** |")
    lines.append(f"| └ loose 档 | 命中但市值位置不够 (行业位置见下表) | — | {len(hits)} → {len(loose_hits)} | **{len(loose_hits)}** |\n")

    if args.refresh and (existing or existing_loose):
        std_stale = sorted(c for c in existing
                           if c not in strict_hit_codes and c not in loose_hit_codes)
        loose_stale = sorted(c for c in existing_loose
                             if c not in loose_hit_codes and c not in strict_hit_codes)
        if std_stale or loose_stale:
            lines.append("## 历史留档 (曾命中, 本次未命中)\n")
            lines.append(f"> 目录里有但本次扫描没命中的票 —— 可能威科夫阶段已离开 Markup, 或已跌出 A→M 命中集。")
            lines.append(f"> `--refresh` 下这些票的详报**会一并刷新到最新交易日**, 文件头标 ⚠️。\n")
            lines.append("| 目录 | 代码 | 名称 | 状态 |")
            lines.append("|---|---|---|---|")
            for c in std_stale:
                lines.append(f"| `docs/{args.report_subdir}/` | `{c}` | {existing[c]['name']} | ⚠️ 本次未命中 (留档) |")
            for c in loose_stale:
                lines.append(f"| `docs/{args.loose_report_subdir}/` | `{c}` | {existing_loose[c]['name']} | ⚠️ 本次未命中 (留档) |")
            lines.append("")

    if hits:
        lines.append("## 命中清单\n")
        _hdr = ("| 转变天数 | 代码 | 名称 | 行业 | 转变日 | 转变价 | 当前价 | 涨幅% | "
                "行业位置 (市值排名) | PE | PE_TTM | 阶段序列 |")
        _sep = "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
        if args.tier in ("strict", "both"):
            lines.append(f"### strict 档 ({len(strict_hits)} 只) — 行业内市值分位 ≤ {_thr['industry_pos_pctile']:g}%\n")
            lines.append(_hdr)
            lines.append(_sep)
            for r in strict_hits:
                lines.append(_hit_row(r, basic, args))
            lines.append("")
        if args.tier in ("loose", "both"):
            lines.append(f"### loose 档 ({len(loose_hits)} 只) — 命中 A→M 但市值位置不在前 {_thr['industry_pos_pctile']:g}%\n")
            lines.append(f"> 详报目录: `docs/{args.loose_report_subdir}/` (strict 档在 `docs/{args.report_subdir}/`)\n")
            lines.append(_hdr)
            lines.append(_sep)
            for r in loose_hits:
                lines.append(_hit_row(r, basic, args))
            lines.append("")

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

        def _add_hits(rows, subdir):
            for r in rows:
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
                _pos = r.get('ind_pos') or {}
                if _pos:
                    note += (f", 行业位置 {_pos.get('market_value_rank')}/"
                             f"{_pos.get('market_value_rank_total')} "
                             f"(前{_pos.get('market_value_percentile', 0):.1f}%)")
                else:
                    note += ", 行业位置判定不了 (不在 SW2021 成分表 / 窗口末日无市值)"
                tasks.append((code, nm, subdir, "hit", note))

        if not args.stale_only:
            _add_hits(strict_hits, args.report_subdir)
            _add_hits(loose_hits, args.loose_report_subdir)

        # 历史留档: 两个目录各自算 (graduated 的票 —— 从 loose 升到 strict ——
        # 在 loose 目录标 stale 说明"已升入 strict 档", 不算漏)
        # 两个目录的票集合互斥: loose 命中的票不在 strict 目录留档, 反之亦然
        stale_list = [c for c in existing if c not in strict_hit_codes and c not in loose_hit_codes]
        stale_list_loose = [c for c in existing_loose if c not in loose_hit_codes and c not in strict_hit_codes]
        if not args.keep_stale:
            for code in stale_list:
                nm = existing[code]["name"]
                tasks.append((code, nm, args.report_subdir, "stale", ""))
            for code in stale_list_loose:
                nm = existing_loose[code]["name"]
                note = "已升入 strict 档" if code in strict_hit_codes else ""
                tasks.append((code, nm, args.loose_report_subdir, "stale", note))
        n_hit_tasks = len([t for t in tasks if t[3] == "hit"])
        n_stale_tasks = len([t for t in tasks if t[3] == "stale"])
        print(f"\n=== step 2 [--refresh]: 刷新/生成详报 ===")
        print(f"  strict 档 → docs/{args.report_subdir}/")
        print(f"  loose 档    → docs/{args.loose_report_subdir}/")
        print(f"  A. 本次命中 (刷新+生成): {n_hit_tasks} 只 "
              f"(strict {len(strict_hit_codes)} + loose {len(loose_hit_codes)})")
        print(f"  C. 历史留档 (本次未命中): {n_stale_tasks} 只"
              + ("  [已跳过刷新, 只标注]" if args.keep_stale else "  [会刷新数据]"))
        print(f"  strict 目录现有: {len(existing)} 份 → 跑完预计: "
              f"{len(existing) + len(strict_hit_codes - set(existing))} 份")
        print(f"  loose 目录现有: {len(existing_loose)} 份 → 跑完预计: "
              f"{len(existing_loose) + len(loose_hit_codes - set(existing_loose))} 份")
        if not tasks:
            print("  无任务可跑")
            return
    elif args.full_report and hits:
        target_hits = tier_hits[:args.report_top] if args.report_top > 0 else tier_hits
        if args.clean and not args.no_clean:
            from tools.storage.safe_fs import safe_delete
            for _d, _sub in ((sub_dir, args.report_subdir),
                             (sub_dir_loose, args.loose_report_subdir)):
                if not _d.exists():
                    continue
                for p in _d.glob("analyze-*.md"):
                    safe_delete(p, reason=f"--clean a2m {_sub}")
                print(f"  ⚠️ 清空旧 md: {_sub}/ (--clean 显式指定, 已移到回收站)")
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只) ===")
        else:
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只) ===")
            print(f"  strict 档 → docs/{args.report_subdir}/")
            print(f"  loose 档    → docs/{args.loose_report_subdir}/")
            if args.clean:
                print(f"  子目录不存在, 无需清空")
            elif args.no_clean:
                print(f"  --no-clean: 保留历史详报")
            else:
                print(f"  保留历史详报 (默认不清理, 加 --clean 才清空)")
        def _dir_of(r):
            # 档位归属 = 分档结果 (strict_hit_codes), 不用 'miss' 字段
            # (原「差在哪」机制 2026-11-12 已随财务三闸一起作废)
            return (args.report_subdir if r['code'] in strict_hit_codes
                    else args.loose_report_subdir)
        tasks = [(r['code'], basic.get(r['code'], (r['code'], "—"))[0], _dir_of(r))
                 for r in target_hits]
    else:
        tasks = []

    # ── 档位迁移清理 ──
    # 一只票换档 (strict ↔ loose) 时, 旧目录里那份副本要挪走,
    # 否则同一票会在两个目录各留一份 (详报内容一样, 但档位标注不同)。
    from tools.storage.safe_fs import safe_delete
    _moved = 0
    for _c in loose_hit_codes:
        _old = existing.get(_c)
        if _old and _old["path"].exists():
            if safe_delete(_old["path"], reason="A→M 换档 → loose 目录"):
                _moved += 1
    for _c in strict_hit_codes:
        _old = existing_loose.get(_c)
        if _old and _old["path"].exists():
            if safe_delete(_old["path"], reason="A→M 换档 → strict 目录"):
                _moved += 1
    if _moved:
        print(f"  ♻️  换档迁移: {_moved} 份旧副本已移到回收站 "
              f"(loose {len(loose_hit_codes)} / strict {len(strict_hit_codes)})")

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
                        stamped_stale += _stamp_report_status(
                            Path(path_or_err), "stale", stale_note=note or None)
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
                                stamped_stale += _stamp_report_status(
                                    Path(path_or_err), "stale", stale_note=t[4] or None)
            print()
        print(f"\n=== step 2 完成 {ok_n}/{len(_tasks_wrapped)} ({ok_n/len(_tasks_wrapped)*100:.0f}%), {time.time()-rt0:.0f}s "
              f"(workers={n_workers}) ===")
        if args.refresh:
            print(f"  状态标记: 命中 {stamped_hit} 份 / 留档 {stamped_stale} 份")


if __name__ == "__main__":
    main()
