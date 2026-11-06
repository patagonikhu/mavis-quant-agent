"""
tools/batch/etf_wyckoff_a2m_scan.py — ETF 威科夫 A→M 扫描 (v2, 2026-11-05)

跟 /t-wyckoff-a2m (股票版) 的关系:
  - **判定逻辑完全同源**: 同一个 wyckoff_judge + 同一个 build_kline_features +
    同一个 scan_sub_events, 同一个 V13 严判窗口 (40 根)。威科夫是纯技术形态
    (MA 关系 + 价格位置 + 子事件), 不吃基本面 → ETF/股票/指数通用。
  - **过滤链完全不同**: 股票的 5 步过滤 (ST / PE / 质量 / 杠杆 / RSI) 对 ETF
    全不适用 (ETF 无退市标记 / 无市盈率 / 无财报 / 无资产负债表),
    ETF 唯一有效的过滤 = **fund_type**。

v2 改动 (2026-11-05):
  ① 数据改走 DataStore (v1 用 duckdb 直读 parquet, 违反项目铁律)
     - 基础信息: DataStore.get_etf_basic()
     - K 线:     DataStore.get_etf_klines(codes, limit)  (新增批量接口, 1 次 SQL)
  ② **ETF 重复去重** (两级并查集, 先全扫后去重, 保证不漏):
     - 级 1: 归一化 benchmark 相同 → 同指数, 合并
             (归一化要拆出复合基准里权重最大的指数成分)
     - 级 2: 60 日收益率相关性 ≥ 0.95 → 合并
             (源数据同一指数写法不统一: '中证酒指数' / '中证酒指数×95%+…×5%' /
              '中证白酒指数×95%+…×5%' 三种写法是同一指数的三只基金)
     - 每组只留**流动性最好**的代表 (最近 20 日日均成交额最大)
  ③ **去掉 ETF whitelist 依赖** (v1 有 --whitelist-only flag + config/etf_whitelist.json;
     该文件已于 2026-11-06 整体删除, 候选池口径收敛到 tools/batch/etf_universe.py)
  ④ 命中后**逐只生成 md 详报**到 docs/etf-wyckoff-a2m/, 行为跟 /t-wyckoff-a2m 一致
     (A 刷新 / B 补充 / C 留档标 ⚠️, 幂等)
     被同指数合并掉的代码, 已有详报改标 `merged` (不重新生成, 数据一样)

用法:
  bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan
  ... --types 股票型                        # 只扫指定 fund_type
  ... --codes 512480 515790                # 指定 ETF 代码
  ... --no-dedup                           # 不去重 (看原始命中)
  ... --no-full-report                     # 只出扫描汇总, 不跑详报
  ... --report-top 0                       # 详报跑全部命中 (0=全部)
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.batch.a2m_report_common import (  # noqa: E402
    scan_existing_reports,
    stamp_report_status,
    report_worker,
)
from tools.batch.etf_universe import (  # noqa: E402
    EXCLUDE_FUND_TYPES,
    DEDUP_CORR_THRESHOLD,
    DEDUP_CORR_WINDOW,
    load_etf_basic,
    filter_by_fund_type,
    load_etf_klines,
    group_by_tracked_index,
    normalize_benchmark,
    index_display,
    liquidity_score,
)

# ============================================================
# 常量
# ============================================================

# V13 严判窗口 (跟股票版一致)
N_TAIL = 40
M_RATIO_MAX = 0.10
MIN_KLINE = 220          # 够 MA200 warmup (199) + 40 根判定窗口
MAX_DAILY_CHG = 0.20     # 单日涨跌 > 20% 跳过 (除权/复牌)

# 输出
DEFAULT_MD = ROOT / "docs" / "etf-wyckoff-a2m.md"
DEFAULT_REPORT_SUBDIR = "etf-wyckoff-a2m"

# 详报状态标记 (跟股票版 A2M_STATUS 区分开, 免得两边互相覆盖)
ETF_MARKER = "ETF_A2M_STATUS"
ETF_SUBJECT = "本基金"
ETF_STALE_NOTE = ("可能已被 fund_type 过滤 (债券/货币/REITs/其他), "
                  "或威科夫阶段已离开 Markup。数据已刷新到最新交易日。")


# ============================================================
# 数据加载 — 走 tools/batch/etf_universe.py (候选池单一真源)
#   load_etf_basic()      ETF 基础信息全量
#   filter_by_fund_type() fund_type 过滤 → ts_code 列表
#   load_etf_klines()     批量读 K 线 (1 次 SQL)
# ============================================================


# ============================================================
# 去重: 同一跟踪指数的 ETF 合并成一只
#   分组口径在 tools/batch/etf_universe.py (ETF 扫描器唯一候选池真源)
# ============================================================


def _dedup_hits(hits: list[dict], klines: dict[str, list[dict]],
                basic_by_ts: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """按跟踪指数去重 → (代表命中列表, 合并明细列表)

    两级合并 (并查集):
      ① **benchmark 归一化 key 相同** → 同指数, 直接合并
      ② **60 日收益率相关性 ≥ 0.95** → 同一指数的不同写法 / 不同基金类型, 合并
         (源数据的 benchmark 写法不统一: '中证酒指数' vs '中证白酒指数',
          纯字符串匹配抓不到 —— 实测 160632 / 161725 / 512690 三只相关度
          0.970~0.982, 是同一指数的三只基金)

    代表选择规则 (同组内):
      1. 最近 20 日日均成交额大者优先 (流动性最好, 最可投)
      2. 成交额相同 → K 线根数多者优先 (历史长, 形态判定更稳)

    Args:
        hits:       原始命中 list[dict] (每项含 ts_code)
        klines:     {ts_code: rows} (算流动性 / 相关性用)
        basic_by_ts:{ts_code: basic row} (取 benchmark 用)

    Returns:
        (reps, groups)
          reps   — 每组一个代表, 保留命中项原字段 + 补 group_size / group_key /
                   group_peers / liquidity / corr_key
          groups — 合并明细 [{key, index_name, members, hit_members, rep_code, rep_name}]
    """
    # 分组 (口径在 etf_universe.group_by_tracked_index, ETF 候选池唯一真源)
    hit_codes = [h["ts_code"] for h in hits]
    by_code = {h["ts_code"]: h for h in hits}
    code_groups, pair_corr = group_by_tracked_index(hit_codes, klines, basic_by_ts)

    reps: list[dict] = []
    details: list[dict] = []
    for members in code_groups.values():
        members = [by_code[c] for c in members]

        def _rank(h: dict) -> tuple:
            liq = liquidity_score(klines.get(h["ts_code"], []))
            bars = len(klines.get(h["ts_code"], []))
            return (liq, bars)

        rep = max(members, key=_rank)
        liq = liquidity_score(klines.get(rep["ts_code"], []))
        row = basic_by_ts.get(rep["ts_code"], {})
        index_name = index_display(row.get("benchmark"))
        rep2 = dict(rep)
        rep2["index_name"] = index_name
        rep2["group_key"] = normalize_benchmark(row.get("benchmark")) or index_name
        rep2["group_size"] = len(members)
        rep2["group_peers"] = [m["code"] for m in members if m["code"] != rep["code"]]
        rep2["liquidity"] = liq
        reps.append(rep2)

        details.append({
            "key": index_name,
            "index_name": index_name,
            "members": len(members),
            "hit_members": len(members),
            "rep_code": rep["code"],
            "rep_name": row.get("name", ""),
            "peers": [f"{m['code']}" for m in members if m["code"] != rep["code"]],
            # 合并依据 (可审计): 每个同伴跟代表的相关度
            "peer_corr": {
                m["code"]: pair_corr.get((rep["ts_code"], m["ts_code"]),
                                         pair_corr.get((m["ts_code"], rep["ts_code"]), 0.0))
                for m in members if m["code"] != rep["code"]
            },
        })

    # 代表按转变日新→旧排 (跟股票版一致: 越近的转变越优先看)
    reps.sort(key=lambda x: (x["trans_idx"], x["code"]))
    details.sort(key=lambda x: x["rep_code"])
    return reps, details


# ============================================================
# 核心判定 (跟股票版 _process_one 同源, 只换数据源)
# ============================================================

def _process_one(ts_code: str, rows: list[dict]) -> dict | None:
    """单只 ETF 威科夫 A→M 判定 (V13 严判)

    跟 wyckoff_a2m_scan._process_one 逻辑完全一致, 只是数据源换成 DataStore 的 ETF K 线。
    判定用同源 wyckoff_judge + 同源 build_kline_features → 结果跟 /t-analyze 一致。

    Returns: 命中时返回 dict, 否则 None
    """
    if not rows or len(rows) < MIN_KLINE:
        return None

    closes = [float(r["close"]) for r in rows]
    highs = [float(r["high"]) for r in rows]
    lows = [float(r["low"]) for r in rows]
    vols = [float(r.get("vol") or 0.0) for r in rows]
    opens = [float(r["open"]) if r.get("open") is not None else c for r, c in zip(rows, closes)]
    pct_chgs = [float(r.get("pct_chg") or 0.0) for r in rows]
    dates_ = [str(r.get("trade_date") or "").replace("-", "")[:8] for r in rows]
    n = len(closes)
    window = min(250, n)

    from tools.factors.wyckoff.stage_factor import wyckoff_judge
    from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
    from tools.factors.kline_arrays import build_kline_features

    # ① arrs 预算 1 次 (O(N))
    arrs = build_kline_features(closes, highs, lows, vols, window=window)

    # ② sub_events 全段扫 1 次 (as_of_idx=None)
    sub_events_all = scan_sub_events(
        closes, highs, lows, vols,
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

    # ── V13 严判 (跟股票版逐条对齐) ──
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
# 输出
# ============================================================

def _write_md(reps: list[dict], details: list[dict], raw_hits: list[dict],
              scanned: int, candidates: int, elapsed: float,
              out_path: Path, types_desc: str, dedup_on: bool,
              last_date: str) -> Path:
    """写扫描汇总 md"""
    lines = [
        "# ETF 威科夫 A→M 转变扫描",
        "",
        f"> 数据日期: {last_date} | 命中: {len(reps)} 只 (去重后) / {scanned} 只可扫 "
        f"({len(reps) * 100 / max(scanned, 1):.1f}%)",
        f"> 原始命中 {len(raw_hits)} 只 → 去重后 {len(reps)} 只 "
        f"(同一跟踪指数合并, 留流动性最好的代表)",
        f"> 扫描耗时: {elapsed:.1f}s | 候选池: {candidates} 只 (fund_type 过滤后)",
        "",
        "## 本次生效条件",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 候选池 | fund_type ∈ {{{types_desc}}} |",
        f"| 排除 fund_type | {', '.join(sorted(EXCLUDE_FUND_TYPES))} |",
        f"| K 线门槛 | ≥ {MIN_KLINE} 根 (MA200 warmup + 40 根判定窗口) |",
        f"| ① 当前 stage | Markup (必须) |",
        f"| ② M 占比 | 最近 {N_TAIL} 根 < {M_RATIO_MAX:.0%} (排除\"反复 M 假突破\") |",
        f"| ③ 单日涨跌 | > {MAX_DAILY_CHG:.0%} 跳过 (除权/复牌) |",
        f"| 去重 | {'ON (按跟踪指数合并, 代表=流动性最好)' if dedup_on else 'OFF (原始命中全列)'} |",
        f"| 去重规则 | 归一化 benchmark 相同 **或** {DEDUP_CORR_WINDOW} 日收益率相关性 ≥ {DEDUP_CORR_THRESHOLD:.2f} |",
        "| 数据源 | DataStore.get_etf_basic / get_etf_klines (0 网络) |",
        "| 判定 | **V13 严判** — 同源 wyckoff_judge |",
        "",
        "## 命中清单",
        "",
        "| 代码 | 名称 | 类型 | 跟踪指数 | 同指数只数 | 转变日 | 转变价 | 当前价 | 涨幅% | M占比 | 20日均额(万) | 阶段序列 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for h in reps:
        seq = "/".join(h["trans_stages"][-8:])
        peers = ", ".join(h.get("group_peers", []))
        idx_name = h.get("index_name", "—")
        lines.append(
            f"| `{h['code']}` | {h.get('name', '')} | {h.get('fund_type', '—')} | "
            f"{idx_name} | {h.get('group_size', 1)} | {h['trans_date']} | ¥{h['trans_close']:.3f} | "
            f"¥{h['last_close']:.3f} | {h['chg_since_trans']:+.1f}% | "
            f"{h['m_ratio'] * 100:.0f}% | {h.get('liquidity', 0) / 10:.0f} | {seq} |"
        )
        if peers:
            lines.append(f"| | ↳ 同指数其它代码 | | | | | | | | | | {peers} |")

    if details and dedup_on:
        lines += [
            "",
            "## 同指数合并明细",
            "",
            "> 同一跟踪指数的 ETF 走势几乎一致, 只保留流动性最好的代表, 其余列在下面备选。",
            "> `相关度` = 60 日日收益率相关系数 (≥ 0.95 判定为同一指数)。",
            "",
            "| 跟踪指数 | 组内只数 | 命中只数 | 代表 | 代表名称 | 同组其它命中代码 | 相关度 |",
            "|---|---|---|---|---|---|---|",
        ]
        for d in details:
            if d["members"] <= 1:
                continue
            corr_s = ", ".join(f"{c}: {v:.3f}" for c, v in d.get("peer_corr", {}).items()) or "—"
            lines.append(
                f"| {d['index_name']} | {d['members']} | {d['hit_members']} | "
                f"`{d['rep_code']}` | {d['rep_name']} | {', '.join(d['peers']) or '—'} | {corr_s} |"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ============================================================
# 详报生成 (跟 /t-wyckoff-a2m 一致: A 刷新 / B 补充 / C 留档)
# ============================================================

def _run_reports(reps: list[dict], raw_hit_codes: set[str], subdir: str,
                 existing: dict, keep_stale: bool,
                 stale_only: bool, report_top: int, workers: int) -> None:
    """逐只生成 7 section 详报 → docs/<subdir>/

    Args:
        reps:          去重后的代表命中 (要出详报的)
        raw_hit_codes: 原始命中代码集合 (判断某只是否"本次命中")
        subdir:        输出子目录名
        existing:      scan_existing_reports() 结果 (已有详报)
        refresh:       刷新模式 (不清空目录)
        keep_stale:    留档票是否也刷新数据 (False=刷新 + 标 ⚠️, True=只标 ⚠️)
        stale_only:    只跑留档票
        report_top:    详报只跑前 N 只 (0=全部)
        workers:       多进程 worker 数 (0=os.cpu_count())
    """
    tasks: list[tuple[str, str, str, str, str, str]] = []   # code, name, subdir, status, note, sector

    if not stale_only:
        targets = reps[:report_top] if report_top > 0 else reps
        for r in targets:
            try:
                chg = (r["last_close"] / r["trans_close"] - 1) * 100
            except Exception:
                chg = 0.0
            try:
                sg = "/".join(s[0] for s in r["trans_stages"][-6:])
            except Exception:
                sg = ""
            note = f" — 转变日 {r.get('trans_date', '?')}, 涨幅 {chg:+.1f}%, 阶段 {sg}"
            sector = r.get("index_name") or None
            tasks.append((r["code"], r.get("name", ""), subdir, "hit", note, sector))

    # C 类: 曾命中 (目录里有) 但本次没命中
    for code, meta in existing.items():
        if code in raw_hit_codes:
            continue
        tasks.append((code, meta["name"], subdir, "stale", "", None))

    # D 类: 同指数被合并的其它代码 —— **只对目录里已有的**做刷新
    #   为什么刷新而不只改标记: 代表基金用 200 个交易日 (ETF), peer 文件若是旧的
    #   120 行版本, 同一文件夹两套行数会让人以为数据有问题。
    #   目录里没有的不新建 —— 保持"只出代表"的去重语义 (新跑一次目录里就 8 份)。
    merged_tasks: list[tuple[str, str, str, str, str, str]] = []
    for r in reps:
        for peer_code in r.get("group_peers", []):
            meta = existing.get(peer_code)
            if not meta:
                continue
            note = f" — 已合并到代表基金 `{r['code']}` ({r.get('index_name', '')})"
            merged_tasks.append((peer_code, meta["name"], subdir, "merged", note,
                                 r.get("index_name") or None))
    tasks.extend(merged_tasks)

    n_hit = len([t for t in tasks if t[3] == "hit"])
    n_stale = len([t for t in tasks if t[3] == "stale"])
    n_merged = len([t for t in tasks if t[3] == "merged"])
    print(f"\n=== 详报生成 (→ docs/{subdir}/) ===")
    print(f"  A. 本次命中 (刷新+生成): {n_hit} 只")
    print(f"  C. 历史留档 (本次未命中): {n_stale} 只"
          + ("  [已跳过刷新, 只标注]" if keep_stale else "  [会刷新数据]"))
    print(f"  D. 同指数被合并 (刷新已有): {n_merged} 只  [目录里没有的不新建]")
    if not tasks:
        print("  无任务可跑")
        return

    n_workers = workers if workers > 0 else (os.cpu_count() or 4)
    n_workers = max(1, min(n_workers, len(tasks)))
    rt0 = time.time()
    ok_n = 0
    stamped_hit = 0
    stamped_stale = 0
    stamped_merged = 0
    if n_workers == 1:
        print(f"  1 worker (单进程)", flush=True)
        for idx, t in enumerate(tasks, 1):
            code, name, sub, st, note, sector = t
            print(f"\n[{idx}/{len(tasks)}] {code} {name} ...", flush=True)
            code_r, ok, path_or_err, elapsed = report_worker((code, name, sub, sector))
            if ok:
                ok_n += 1
                if st == "hit":
                    stamped_hit += stamp_report_status(
                        Path(path_or_err), "hit", note,
                        marker=ETF_MARKER, subject=ETF_SUBJECT)
                elif st == "merged":
                    stamped_merged += stamp_report_status(
                        Path(path_or_err), "merged", note,
                        marker=ETF_MARKER, subject=ETF_SUBJECT)
                else:
                    stamped_stale += stamp_report_status(
                        Path(path_or_err), "stale",
                        marker=ETF_MARKER, subject=ETF_SUBJECT, stale_note=ETF_STALE_NOTE)
                print(f"  ✅ {code_r} → {path_or_err} ({elapsed:.2f}s)", flush=True)
            else:
                print(f"  ⚠️  {code_r} 失败: {path_or_err} ({elapsed:.2f}s)", flush=True)
    else:
        print(f"  {n_workers} workers (multiprocessing spawn)", flush=True)
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            for idx, result in enumerate(pool.imap_unordered(
                    report_worker, [(t[0], t[1], t[2], t[5]) for t in tasks]), 1):
                code, ok, path_or_err, elapsed = result
                mark = "✅" if ok else "⚠️ "
                sys.stdout.write(f"\r  [{idx}/{len(tasks)}] {mark} {code} ({elapsed:.2f}s)")
                sys.stdout.flush()
                if ok:
                    ok_n += 1
                    t = next((x for x in tasks if x[0] == code), None)
                    if t:
                        if t[3] == "hit":
                            stamped_hit += stamp_report_status(
                                Path(path_or_err), "hit", t[4],
                                marker=ETF_MARKER, subject=ETF_SUBJECT)
                        elif t[3] == "merged":
                            stamped_merged += stamp_report_status(
                                Path(path_or_err), "merged", t[4],
                                marker=ETF_MARKER, subject=ETF_SUBJECT)
                        else:
                            stamped_stale += stamp_report_status(
                                Path(path_or_err), "stale",
                                marker=ETF_MARKER, subject=ETF_SUBJECT, stale_note=ETF_STALE_NOTE)
        print()

    print(f"\n=== 详报完成 {ok_n}/{len(tasks)} ({ok_n / max(len(tasks), 1) * 100:.0f}%), "
          f"{time.time() - rt0:.0f}s (workers={n_workers}) ===")
    print(f"  状态标记: 命中 {stamped_hit} 份 / 同指数合并 {stamped_merged} 份 / 留档 {stamped_stale} 份")


# ============================================================
# 主流程
# ============================================================

def main() -> int:
    ap = argparse.ArgumentParser(description="ETF 威科夫 A→M 扫描 (0 网络, 走 DataStore)")
    ap.add_argument("--types", type=str, default="",
                    help="只扫指定 fund_type (逗号分隔, 默认=除债券/货币/REITs/其他外全扫)")
    ap.add_argument("--codes", type=str, nargs="*", default=None,
                    help="指定 ETF 代码 (6位, 空格分隔)")
    ap.add_argument("--no-dedup", action="store_true",
                    help="不按跟踪指数去重 (原始命中全列, 用于对比)")
    ap.add_argument("--no-md", action="store_true", help="不写汇总 md")
    ap.add_argument("--no-full-report", action="store_true", help="不跑详报 (只输出扫描汇总)")
    ap.add_argument("--report-top", type=int, default=100,
                    help="详报只跑前 N 只 (0=全部, 默认 100)")
    ap.add_argument("--report-subdir", default=DEFAULT_REPORT_SUBDIR,
                    help=f"详报输出子目录 (默认 {DEFAULT_REPORT_SUBDIR})")
    ap.add_argument("--refresh", action="store_true", default=True,
                    help="刷新模式 (默认): 不清空目录, A 刷新 / B 补充 / C 留档")
    ap.add_argument("--keep-stale", action="store_true",
                    help="留档票不刷新数据, 只在文件头标状态")
    ap.add_argument("--stale-only", action="store_true",
                    help="只刷新留档票, 不跑本次命中")
    ap.add_argument("--workers", type=int, default=0,
                    help="详报多进程 worker 数 (0=os.cpu_count())")
    ap.add_argument("--output", type=str, default=str(DEFAULT_MD), help="汇总 md 输出路径")
    args = ap.parse_args()

    allow_types: set[str] | None = None
    if args.types:
        allow_types = {t.strip() for t in args.types.split(",") if t.strip()}

    print("=== ETF 威科夫 A→M 扫描 (0 网络, 走 DataStore) ===")
    print(f"  fund_type 过滤: {', '.join(sorted(allow_types)) if allow_types else '排除 ' + ', '.join(sorted(EXCLUDE_FUND_TYPES))}")
    print(f"  判定: V13 严判 (当前 M + {N_TAIL} 根内 M 占比 < {M_RATIO_MAX:.0%})")
    print(f"  去重: {'OFF' if args.no_dedup else 'ON (按跟踪指数合并, 代表=流动性最好)'}")
    print()

    t0 = time.time()

    # 1. 基础信息 + fund_type 过滤 (DataStore)
    basic = load_etf_basic()
    if not basic:
        print("❌ DataStore 读不到 ETF 基础信息, 先跑 /t-sync-data --etf")
        return 1
    basic_by_ts = {str(r["ts_code"]): r for r in basic}
    candidate_ts = filter_by_fund_type(basic, allow_types)
    print(f"  1) fund_type 过滤: {len(basic)} → {len(candidate_ts)} 只")

    # 2. 指定代码收窄
    if args.codes:
        want = {str(c) for c in args.codes}
        candidate_ts = [ts for ts in candidate_ts if ts.split(".")[0] in want]
        print(f"     --codes 收窄: → {len(candidate_ts)} 只")

    # 3. 批量读 K 线 (DataStore, 1 次 SQL)
    codes6 = [ts.split(".")[0] for ts in candidate_ts]
    klines = load_etf_klines(codes6)
    print(f"  2) 加载 K 线 (DataStore 批量): {len(klines)} 只 ({time.time() - t0:.1f}s)")

    # 4. 逐只判定 (V13 严判, 同源 wyckoff_judge)
    scanned = 0
    hits: list[dict] = []
    errs = 0
    for ts_code, rows in klines.items():
        if len(rows) < MIN_KLINE:
            continue
        scanned += 1
        try:
            r = _process_one(ts_code, rows)
            if r:
                row = basic_by_ts.get(ts_code, {})
                r["name"] = row.get("name", "")
                r["fund_type"] = row.get("fund_type", "")
                r["index_name"] = index_display(row.get("benchmark"))
                hits.append(r)
        except Exception:
            errs += 1
    elapsed = time.time() - t0
    print(f"  3) 扫描 {scanned} 只 ({errs} errors), 耗时 {elapsed:.1f}s")
    print(f"     原始命中 {len(hits)} 只")

    # 5. 去重 (同跟踪指数合并)
    if args.no_dedup:
        reps, details = hits, []
        for h in reps:
            h.setdefault("group_size", 1)
            h.setdefault("group_peers", [])
            h["liquidity"] = liquidity_score(klines.get(h["ts_code"], []))
    else:
        reps, details = _dedup_hits(hits, klines, basic_by_ts)
    print(f"     去重后 {len(reps)} 只 (同一跟踪指数合并)")

    if not reps:
        print("\n  (无命中)")
        return 0

    for h in sorted(reps, key=lambda x: x["trans_date"], reverse=True):
        print(f"     {h['ts_code']}  {h.get('name', '')}  {h['trans_date']}  "
              f"¥{h['last_close']:.3f}  M占比{h['m_ratio'] * 100:.0f}%  "
              f"{'/'.join(h['trans_stages'][-8:])}")

    # 6. 汇总 md
    last_date = max((h["last_date"] for h in reps), default="—")
    if not args.no_md:
        out = _write_md(reps, details, hits, scanned, len(candidate_ts), elapsed,
                        Path(args.output),
                        ",".join(sorted(allow_types)) if allow_types else "全部(除排除项)",
                        not args.no_dedup, last_date)
        print(f"\n📄 汇总: {out}")

    # 7. 逐只详报 (跟 /t-wyckoff-a2m 一致)
    if args.no_full_report:
        return 0

    sub_dir = ROOT / "docs" / args.report_subdir
    existing = scan_existing_reports(sub_dir)
    raw_hit_codes = {h["code"] for h in hits}
    # D 类 (同指数被合并的已有详报) 也在 _run_reports 里跑 (刷新 + 改标 merged)
    _run_reports(reps, raw_hit_codes, args.report_subdir, existing,
                 args.keep_stale, args.stale_only,
                 args.report_top, args.workers)

    return 0


if __name__ == "__main__":
    sys.exit(main())
