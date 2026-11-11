#!/usr/bin/env python
"""
tools/batch/t_analyze_sw.py — 行业版批量报告 (2026-11-12 加, /t-analyze --sw)

给 SW2021 **二级行业指数**出跟个股同款的分析报告 → `docs/sw/analyze-{code}-{name}.md`。

复用个股的整条链路 (**零改动**):
    行业指数 K 线 → RawContext → AnalysisEngine().analyze_history()
    → 跟个股**同一套** WyckoffStrategy → RenderData → industry_renderer.render_industry_report()

行业版跟个股版的差异 (只有 2 段):
    - 「财务数据」        → 换「行业估值 (PE/PB/市值)」   (行业指数没财报, 但指数自带估值口径)
    - 「🏆 行业地位」     → 换「行业间地位」+「行业内龙头榜」

用法:
    bash tools/with_venv.sh python -m tools.batch.t_analyze_sw
    bash tools/with_venv.sh python -m tools.batch.t_analyze_sw --codes 801012.SI,801010.SI
    bash tools/with_venv.sh python -m tools.batch.t_analyze_sw --workers 8
    bash tools/with_venv.sh python -m tools.batch.t_analyze_sw --level L1

⚠️ 0 网络, 走本地 parquet (DataStore)。
⚠️ 默认**不清空** docs/sw/ (跟个股版一致: 历史报告是复盘素材, 要清自己加 --clean)。
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_result_signals import compute_factor_history
from tools.analysis.render_data import RenderData


# ---- 行业版 factor history 回看窗口 (跟个股股票口径一致: 120 交易日) ----
SW_FACTOR_LOOKBACK = 120
# ---- analyze_history 预算的日期数 (跟个股版一致: 480 天, 覆盖 4 季 + 季末) ----
SW_ANALYZE_DAYS = 480


def process_one(code: str, peer_ranks: dict) -> dict:
    """单个行业 → 一份 md (生产侧算好, 渲染器只读)"""
    from tools.analysis.industry_analysis import (
        build_industry_ctx,
        compute_industry_leader_board,
        judge_industry_peer,
    )
    from tools.render.industry_renderer import render_industry_report

    t0 = time.time()
    try:
        ctx = build_industry_ctx(code)
        if not ctx or not ctx.kline:
            return {"ok": False, "code": code, "err": "无 K 线数据", "elapsed": 0.0}

        all_dates = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
        dates = all_dates[-SW_ANALYZE_DAYS:] if len(all_dates) >= SW_ANALYZE_DAYS else all_dates
        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2:
            return {"ok": False, "code": code, "err": "history 不足", "elapsed": 0.0}

        result = history[dates[-1]]
        data = RenderData.from_result(ctx, result)
        data.asset_label = "行业指数"
        data.price_unit = ""          # 行业指数是**点位**, 不是钱
        data.factor_history_rows = compute_factor_history(
            ctx, lookback=SW_FACTOR_LOOKBACK, history=history)

        # ---- 行业专属 3 段 (生产侧算好) ----
        last = ctx.kline[-1]
        data.sw_valuation = {
            "pe": last.get("pe"),
            "pb": last.get("pb"),
            "total_mv_yi": ctx.market_cap_yi,
            "float_mv_yi": (last.get("float_mv") or 0.0) / 1e4,
            "amount": last.get("amount"),
        }
        peer = peer_ranks.get(code)
        if peer:
            verdict, level = judge_industry_peer(peer)
            peer = dict(peer)
            peer["verdict"] = verdict
            peer["verdict_level"] = level
        data.sw_peer_position = peer
        data.sw_leader_board = compute_industry_leader_board(
            code, trading_days=SW_FACTOR_LOOKBACK, top_n=10)

        md = render_industry_report(data, sector=ctx.industry or "—")

        out = Path(f"docs/sw/analyze-{code.replace('.SI', '')}-{ctx.name}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        return {"ok": True, "code": code, "name": ctx.name, "path": str(out),
                "chars": len(md), "elapsed": time.time() - t0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": code, "err": f"{type(e).__name__}: {e}",
                "elapsed": time.time() - t0}


def _worker(args):
    code, peer_ranks = args
    return process_one(code, peer_ranks)


def main() -> int:
    ap = argparse.ArgumentParser(description="行业版批量报告 (/t-analyze --sw)")
    ap.add_argument("--codes", help="行业代码, 逗号分隔 (默认全部有数据的二级行业)")
    ap.add_argument("--code", help="单个行业代码 (例 801012.SI) — 等价于 --codes")
    ap.add_argument("--level", choices=["L1", "L2"], default="L2", help="行业层级 (默认 L2)")
    ap.add_argument("--workers", type=int, default=0, help="并发进程数 (默认 0=os.cpu_count())")
    ap.add_argument("--output-subdir", default="sw", help="输出子目录 (默认 sw)")
    ap.add_argument("--clean", action="store_true", help="⚠️ 先清空 docs/<subdir> 旧 md (走回收站)")
    args = ap.parse_args()

    from tools.analysis.industry_analysis import list_industry_codes, compute_industry_peer_ranks

    if args.codes or args.code:
        raw = args.codes or args.code
        codes = [c.strip() for c in raw.replace(",", " ").split() if c.strip()]
        codes = [c if "." in c else f"{c}.SI" for c in codes]
    else:
        codes = list_industry_codes(level=args.level)
    if not codes:
        print("❌ 没有可跑的行业 (检查 data/history/sw_index/kline/)", file=sys.stderr)
        return 1

    print(f"=== 行业版批量 (/t-analyze --sw) | {len(codes)} 个{args.level}行业 "
          f"| {time.strftime('%H:%M:%S')} ===")
    print(f"  ℹ️  0 网络, 走本地行业 K 线 parquet")

    # 行业间排名: **跟全部有数据的行业比** (不是只跟本次跑的这些比)
    #   → 否则单跑 1 个行业时, 它的"行业间排名"永远是 1/1 (无意义)
    t0 = time.time()
    all_codes = list_industry_codes(level=args.level)
    peer_all = compute_industry_peer_ranks(all_codes, trading_days=SW_FACTOR_LOOKBACK)
    peer_ranks = {c: peer_all.get(c) for c in codes if peer_all.get(c)}
    print(f"  行业间排名: 对比 {len(peer_all)} 个行业, 本次跑 {len(peer_ranks)} 个 "
          f"({time.time() - t0:.1f}s)")

    out_dir = Path(f"docs/{args.output_subdir}")
    if args.clean and out_dir.exists():
        from tools.storage.safe_fs import safe_delete
        n = 0
        for p in out_dir.glob("analyze-*.md"):
            if safe_delete(p, reason="t_analyze_sw --clean"):
                n += 1
        print(f"  ⚠️  清空旧 md: {n} 份 (已移到回收站)")

    n_workers = args.workers if args.workers > 0 else (os.cpu_count() or 4)
    n_workers = max(1, min(n_workers, len(codes)))
    print(f"  {n_workers} workers (multiprocessing spawn)\n")

    t0 = time.time()
    ok_n, errs = 0, []
    results = []
    ctx_mp = mp.get_context("spawn")
    with ctx_mp.Pool(processes=n_workers) as pool:
        for res in pool.imap_unordered(_worker, [(c, peer_ranks) for c in codes]):
            results.append(res)
            if res.get("ok"):
                ok_n += 1
                sys.stdout.write(f"\r  ✅ {res['code']} {res.get('name', '')} "
                                 f"({res['elapsed']:.1f}s)      ")
            else:
                errs.append(res)
            sys.stdout.flush()
    print()

    print(f"\n=== 完成 {ok_n}/{len(codes)} ({ok_n / len(codes) * 100:.0f}%), "
          f"{time.time() - t0:.1f}s ===")
    if errs:
        print(f"  ⚠️  {len(errs)} 个失败:")
        for e in errs[:10]:
            print(f"     {e['code']}: {e.get('err')}")
    print(f"  📄 输出目录: docs/{args.output_subdir}/ ({len(list(out_dir.glob('analyze-*.md')))} 份)")

    # 行业威科夫 Markup 汇总 (2026-11-12 加) —— 跟个股路径同一个数据源
    try:
        from tools.analysis.industry_stage import (
            format_markup_summary,
            summarize_markup_industries,
        )
        print()
        print(format_markup_summary(summarize_markup_industries()))
    except Exception as _e:
        print(f"\n=== 行业威科夫 Markup 汇总 ===\n  ⚠️ 读取失败: {type(_e).__name__}: {_e}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
