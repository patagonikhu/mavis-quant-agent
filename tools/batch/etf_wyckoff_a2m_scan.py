"""
tools/batch/etf_wyckoff_a2m_scan.py — ETF 威科夫 A→M 扫描 (2026-11-04)

参考 /t-wyckoff-a2m (股票版), 但**过滤链完全不同**:
  - 股票的 5 步过滤 (ST / PE / 质量 / 杠杆 / RSI) **对 ETF 全部不适用**:
      ST      — ETF 无退市风险标记
      PE      — ETF 无市盈率 (指数基金没有盈利)
      质量     — 无营收/净利/毛利率/ROE (fund_type 只有股票型/债券型/REITs...)
      杠杆     — 无资产负债表
  - ETF 唯一需要的过滤 = **fund_type** (债券型/货币型/REITs 不适用"吸筹→主升"语义)

判定逻辑: **完全复用股票版的 V13 严判** (同源 wyckoff_judge + 同输入数据)
  ① 当前 (最后) 根 stage == 'Markup'                    (必须)
  ② 最近 40 根 K 线里 Markup 占比 < 10% (允许 ≤ 3 根)    (排除"反复 M"假突破)
  ③ 单日涨跌 > 20% 跳过                                  (ETF 涨跌停 10%, 几乎不会触发)
  ④ K 线长度 ≥ 11 根 (实际门槛 220, 见 _process_one)

为什么 ETF 也能用威科夫:
  威科夫判定是**纯技术形态** (MA 关系 + 价格位置 + 子事件), 不依赖任何基本面。
  `stage_factor.wyckoff_judge` 只吃 arrs + sub_events, 所以 ETF/股票/指数通用。

数据源:
  - K 线: data/history/etf/kline/{YYYYQN}.parquet (跟 /t-sync-data --etf 同源)
  - 基础: data/history/etf/etf_basic.parquet (name / fund_type / benchmark)

性能 (2026-11-04 实测):
  - 2348 只 ETF → 1908 只 (根数≥220) → 扫描 9.8s (单进程)
  - 命中 14 只 (0.73%) —— 股票版是 2.6%, ETF 更严 (长期横盘, 难有干净启动)

用法:
  bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan
  ... --types 股票型                        # 只扫指定 fund_type
  ... --codes 512480 515790                # 指定 ETF 代码
  ... --whitelist-only                      # 只扫 config/etf_whitelist.json 里的
  ... --no-md                               # 不写 md
  ... --top 20                              # 详报只跑前 N 只 (0=全部)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ============================================================
# 常量
# ============================================================

# ETF K 线 parquet glob (季度文件, 全部读)
ETF_KLINE_GLOB = "data/history/etf/kline/*.parquet"
ETF_BASIC_PATH = "data/history/etf/etf_basic.parquet"

# 排除的 fund_type (不适用"吸筹→主升"语义)
#   债券型 — 震荡上行, 没有主升浪
#   货币型 — 收益单调, 无形态
#   REITs  — 租金现金流驱动, 跟股票形态逻辑不同
EXCLUDE_FUND_TYPES = {"债券型", "货币型", "REITs", "其他"}

# V13 严判窗口 (跟股票版一致)
N_TAIL = 40
M_RATIO_MAX = 0.10
MIN_KLINE = 220          # 够 MA200 warmup + 40 根判定窗口
MAX_DAILY_CHG = 0.20     # 单日涨跌 > 20% 跳过 (除权/复牌)

# 输出
DEFAULT_MD = ROOT / "docs" / "etf-wyckoff-a2m.md"
DEFAULT_REPORT_DIR = ROOT / "docs" / "etf-wyckoff-a2m"


# ============================================================
# 数据加载
# ============================================================

def _load_etf_basic() -> pd.DataFrame:
    """ETF 基础信息 (name / fund_type / benchmark)

    走 duckdb (跟股票版 wyckoff_a2m_scan 同构, 不直接 pd.read_parquet)。
    """
    try:
        return duckdb.execute(f"""
            SELECT ts_code, name, fund_type, benchmark
            FROM read_parquet('{ETF_BASIC_PATH}')
        """).fetchdf()
    except Exception:
        return pd.DataFrame(columns=["ts_code", "name", "fund_type", "benchmark"])


def _load_whitelist_codes() -> list[str]:
    """config/etf_whitelist.json 里的 ETF 代码 (6 位)"""
    p = ROOT / "config" / "etf_whitelist.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [str(x.get("code")) for x in data.get("etfs", []) if x.get("code")]
    except Exception:
        return []


def _load_etf_klines(codes: list[str] | None = None, limit_per_code: int = 300) -> dict[str, pd.DataFrame]:
    """1 次 SQL 拿 ETF K 线, 返回 {ts_code: DataFrame}

    limit_per_code=300: 够 MA200 warmup (199) + V13 判定窗口 (40), 多留余量。
    """
    where = ""
    if codes:
        in_clause = ",".join(f"'{c}'" for c in codes)
        where = f"WHERE ts_code IN ({in_clause})"
    sql = f"""
        WITH ranked AS (
            SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
            FROM read_parquet('{ETF_KLINE_GLOB}')
            {where}
        )
        SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg
        FROM ranked
        WHERE rn <= {limit_per_code}
        ORDER BY ts_code, trade_date
    """
    df = duckdb.execute(sql).fetchdf()
    df["trade_date"] = df["trade_date"].astype(str)
    out: dict[str, pd.DataFrame] = {}
    for ts_code, sub in df.groupby("ts_code"):
        out[str(ts_code)] = sub.sort_values("trade_date").reset_index(drop=True)
    return out


# ============================================================
# 核心判定 (跟股票版 _process_one 同源, 只换数据源)
# ============================================================

def _process_one(ts_code: str, df: pd.DataFrame) -> dict | None:
    """单只 ETF 威科夫 A→M 判定 (V13 严判)

    跟 wyckoff_a2m_scan._process_one 逻辑完全一致, 只是数据源换成 ETF parquet。
    判定用同源 wyckoff_judge + 同源 build_kline_features → 结果跟 /t-analyze 一致。

    Returns: 命中时返回 dict, 否则 None
    """
    if df is None or len(df) < MIN_KLINE:
        return None

    closes = df["close"].tolist()
    highs = df["high"].tolist()
    lows = df["low"].tolist()
    vols = df["vol"].tolist()
    opens = df["open"].tolist() if "open" in df.columns else closes
    pct_chgs = df["pct_chg"].tolist() if "pct_chg" in df.columns else None
    dates_ = df["trade_date"].astype(str).str.replace("-", "").str[:8].tolist()
    n = len(closes)
    window = min(250, n)

    from tools.factors.wyckoff.stage_factor import wyckoff_judge
    from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
    from tools.factors.kline_arrays import build_kline_features

    # ① arrs 预算 1 次 (O(N))
    arrs = build_kline_features(closes, highs, lows, vols, window=window)

    # ② sub_events 全段扫 1 次 (as_of_idx=None)
    sub_events_all = scan_sub_events(
        closes, highs, lows, vols, None,
        o=opens, pct_chg=pct_chgs,
        period_label="daily", code=ts_code,
        as_of_idx=None,
        dates=dates_,
        precomputed_arrs=arrs,
    )

    # ③ wyckoff_judge O(1) per i (最近 N_TAIL 根)
    stages: list[str] = []
    closes_tail: list[float] = []
    dates_tail: list[str] = []
    for k in range(N_TAIL):
        i = n - N_TAIL + k
        sub_set = {e["name"] for e in sub_events_all if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_set,
                            window=arrs["window"], period_label="daily")
        stages.append((out or {}).get("stage", "?"))
        closes_tail.append(closes[i])
        dates_tail.append(dates_[i])

    # ── V13 严判 ──
    # ③ 单日涨跌幅 > 20% 跳过 (ETF 涨跌停 10%, 主要防折算/拆分)
    last_chg = abs(closes_tail[-1] / closes_tail[-2] - 1) if len(closes_tail) >= 2 and closes_tail[-2] > 0 else 0
    if last_chg > MAX_DAILY_CHG:
        return None
    # ① 当前 stage == Markup
    if stages[-1] != "Markup":
        return None
    if len(stages) < 11:
        return None
    # ② 最近 40 根里 M 占比 < 10% (排除"反复 M"假突破)
    m_window_size = min(N_TAIL, len(stages))
    recent_stages = stages[-m_window_size:]
    m_count = sum(1 for s in recent_stages if s == "Markup")
    m_ratio = m_count / m_window_size
    if m_ratio >= M_RATIO_MAX:
        return None

    # 转变点: 从右往左第一根 != M 的位置
    trans_idx = 0
    for k in range(len(stages) - 1, 0, -1):
        if stages[k - 1] != "Markup":
            trans_idx = k
            break

    return {
        "ts_code": ts_code,
        "code": ts_code.split(".")[0],
        "trans_idx": trans_idx,
        "trans_date": dates_tail[trans_idx],
        "trans_close": closes_tail[trans_idx],
        "last_date": dates_tail[-1],
        "last_close": closes_tail[-1],
        "prev_stage": stages[trans_idx - 1] if trans_idx > 0 else "?",
        "curr_stage": stages[-1],
        "trans_stages": stages,
        "m_ratio": m_ratio,
        "chg_since_trans": ((closes_tail[-1] / closes_tail[trans_idx] - 1) * 100
                            if closes_tail[trans_idx] > 0 else 0.0),
    }


# ============================================================
# 过滤 (ETF 版: 只按 fund_type)
# ============================================================

def _filter_by_fund_type(basic: pd.DataFrame, allow_types: set[str] | None) -> set[str]:
    """按 fund_type 过滤 → 保留的 ts_code 集合

    ETF 没有 PE/ROE/毛利率, 唯一有意义的过滤就是 fund_type。
    """
    if basic.empty:
        return set()
    if allow_types:
        ok = basic[basic["fund_type"].isin(allow_types)]
    else:
        ok = basic[~basic["fund_type"].isin(EXCLUDE_FUND_TYPES)]
    return set(ok["ts_code"].astype(str))


# ============================================================
# 输出
# ============================================================

def _write_md(hits: list[dict], basic: pd.DataFrame, total_scanned: int,
              out_path: Path, types_desc: str) -> Path:
    """写扫描汇总 md"""
    name_map = {}
    bench_map = {}
    if not basic.empty:
        for r in basic.to_dict("records"):
            name_map[str(r["ts_code"])] = r.get("name", "")
            bench_map[str(r["ts_code"])] = r.get("benchmark", "")

    lines = [
        "# ETF 威科夫 A→M 转变扫描",
        "",
        f"> 数据日期: {hits[0]['last_date'] if hits else '—'} | 命中: {len(hits)} 只 / {total_scanned} "
        f"({len(hits) * 100 // max(total_scanned, 1)}%)",
        f"> 过滤: fund_type ∈ {{{types_desc}}} | 排除: {', '.join(sorted(EXCLUDE_FUND_TYPES))}",
        "> 判定: **V13 严判** — 当前 M + 最近 40 根 K 线 M 占比 < 10% (排除\"反复 M 假突破\")",
        "> 数据源: data/history/etf/kline/*.parquet (同 /t-sync-data --etf) + 同源 wyckoff_judge",
        "",
        "## 命中清单",
        "",
        "| 代码 | 名称 | 类型 | 跟踪指数 | 转变日 | 转变价 | 当前价 | 涨幅% | M占比 | 阶段序列 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for h in sorted(hits, key=lambda x: -x["m_ratio"]):
        ts = h["ts_code"]
        seq = "/".join(h["trans_stages"][-8:])
        lines.append(
            f"| `{h['code']}` | {name_map.get(ts, ts)} | "
            f"{basic.loc[basic['ts_code'] == ts, 'fund_type'].iloc[0] if not basic.empty and (basic['ts_code'] == ts).any() else '—'} | "
            f"{bench_map.get(ts, '—')} | {h['trans_date']} | ¥{h['trans_close']:.3f} | "
            f"¥{h['last_close']:.3f} | {h['chg_since_trans']:+.1f}% | "
            f"{h['m_ratio'] * 100:.0f}% | {seq} |"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 威科夫 A→M 扫描")
    ap.add_argument("--types", type=str, default="",
                    help="只扫指定 fund_type (逗号分隔, 默认=除债券/货币/REITs/其他外全扫)")
    ap.add_argument("--codes", type=str, nargs="*", default=None,
                    help="指定 ETF 代码 (6位, 空格分隔)")
    ap.add_argument("--whitelist-only", action="store_true",
                    help="只扫 config/etf_whitelist.json 里的 ETF")
    ap.add_argument("--no-md", action="store_true", help="不写 md")
    ap.add_argument("--top", type=int, default=20,
                    help="详报只跑前 N 只 (0=全部, 默认 20)")
    ap.add_argument("--output-dir", type=str, default=str(DEFAULT_REPORT_DIR),
                    help="详报输出目录")
    args = ap.parse_args()

    allow_types: set[str] | None = None
    if args.types:
        allow_types = {t.strip() for t in args.types.split(",") if t.strip()}

    print("=== ETF 威科夫 A→M 扫描 (0 网络) ===")
    print(f"  fund_type 过滤: {', '.join(sorted(allow_types)) if allow_types else '排除 ' + ', '.join(sorted(EXCLUDE_FUND_TYPES))}")
    print(f"  判定: V13 严判 (当前 M + 40 根内 M 占比 < {M_RATIO_MAX:.0%})")
    print()

    t0 = time.time()

    # 1. 基础信息 + fund_type 过滤
    basic = _load_etf_basic()
    allowed = _filter_by_fund_type(basic, allow_types)
    if basic.empty:
        print("❌ 读不到 etf_basic.parquet, 先跑 /t-sync-data --etf")
        return 1
    print(f"  fund_type 过滤: {len(basic)} → {len(allowed)} 只")

    # 2. 指定代码 / whitelist 收窄
    target_codes: list[str] | None = None
    if args.codes:
        target_codes = [str(c) for c in args.codes]
    elif args.whitelist_only:
        wl = _load_whitelist_codes()
        target_codes = [f"{c}.SH" for c in wl] + [f"{c}.SZ" for c in wl]

    # 3. 1 次 SQL 拿 K 线
    klines = _load_etf_klines(target_codes)
    print(f"  1) 加载 K 线 (1 次 SQL): {len(klines)} 只 ({time.time() - t0:.1f}s)")

    # 4. 逐只判定
    scanned = 0
    hits: list[dict] = []
    errs = 0
    for ts_code, df in klines.items():
        if ts_code not in allowed:
            continue
        if len(df) < MIN_KLINE:
            continue
        scanned += 1
        try:
            r = _process_one(ts_code, df)
            if r:
                hits.append(r)
        except Exception:
            errs += 1

    elapsed = time.time() - t0
    print(f"  2) 扫描 {scanned} 只 ({errs} errors), 耗时 {elapsed:.1f}s")
    print(f"  ✅ A→M 命中 {len(hits)} 只")

    if not hits:
        print("\n  (无命中)")
        return 0

    for h in sorted(hits, key=lambda x: x["trans_date"], reverse=True):
        print(f"     {h['ts_code']}  {h['trans_date']}  ¥{h['last_close']:.3f}  "
              f"M占比{h['m_ratio'] * 100:.0f}%  {'/'.join(h['trans_stages'][-8:])}")

    # 5. 写汇总 md
    if not args.no_md:
        types_desc = ",".join(sorted(allow_types)) if allow_types else "全部(除排除项)"
        out = _write_md(hits, basic, scanned, DEFAULT_MD, types_desc)
        print(f"\n📄 汇总: {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
