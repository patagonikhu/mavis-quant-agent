"""
t_analyze_one.py — 单只股票 /t-analyze 入口 (2026-09-04 新增)

复用 t_analyze_all.py::process_one 的完整流程 (含 compute_factor_history),
保证单只详报跟 watchlist 批量 11 section 完全一致, 不再有 708 vs 829 行差异.

用法:
    bash tools/with_venv.sh python3 tools/batch/t_analyze_one.py --code 300274
    bash tools/with_venv.sh python3 tools/batch/t_analyze_one.py --code 300274 --name 阳光电源

输出:
    docs/{portfolio,watchlist}/analyze-{code}-{name}.md
    (按 list_type 自动分流, 持仓→portfolio, 其它→watchlist)

0 网络, 0 网络. 只依赖本地 parquet.
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from pathlib import Path

# 路径兜底 (3 层: batch/ → tools/ → 项目根)
_TOOLS = Path(__file__).resolve().parent.parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_result_signals import (
    compute_factor_history,
    diff_rows,
    extract_signals,
    format_signals_for_render,
)
from tools.analysis.render_data import RenderData
from tools.render.report_renderer import render_report


def load_watchlist_map() -> dict[str, dict]:
    """code → watchlist entry (走 DataStore, 不硬编码路径)"""
    try:
        return {s["code"]: s for s in DataStore.load_watchlist().get("stocks", [])}
    except Exception:
        return {}


def process_one(code: str, name: str | None = None, output_subdir: str | None = None) -> dict:
    """单只分析 + 渲染. 复用 t_analyze_all.py 同款逻辑.

    Args:
        code: 6 位股票代码
        name: 股票名称 (可选)
        output_subdir: 强制指定输出子目录 (如 'wyckoff-a2m'),
            跳过 watchlist list_type 分流, 直接 docs/{output_subdir}/analyze-{code}-{name}.md
            默认 None = 按 watchlist list_type 分流 (持仓/自选/blowout)

    Returns: {ok, md_path, elapsed, lines, current_price, list_type, signals}
    """
    t0 = time.time()
    wl_map = load_watchlist_map()
    s = wl_map.get(code, {"code": code, "name": name or "", "list_type": "自选"})
    list_type = s.get("list_type", "自选")

    if output_subdir:
        # 2026-10-16 加: 强制指定子目录, 跳过 watchlist 分流 (避免污染)
        subdir, list_type_label = output_subdir, output_subdir
    else:
        # 2026-09-18 加: 3 路分流 — 持仓/自选/blowout
        if list_type == "持仓":
            subdir, list_type_label = "portfolio", "持仓"
        elif list_type == "blowout":
            subdir, list_type_label = "blowout", "blowout"
        else:
            subdir, list_type_label = "watchlist", "自选"

    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            return {"ok": False, "err": f"{code} 无 K 线数据, 请先 /t-sync-data", "code": code}

        all_dates = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
        # v6.2.7 改: 扩大到 480 天 (覆盖 4 季 + 季末 15 天), 4 季 Magic 估值才完整
        dates = all_dates[-480:] if len(all_dates) >= 480 else all_dates
        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2:
            return {"ok": False, "err": f"history 不足 ({len(history)} 根)", "code": code}

        result = history[dates[-1]]
        data = RenderData.from_result(ctx, result)
        # 关键: 调 compute_factor_history (跟 t_analyze_all.py 一致, 不空置)
        data.factor_history_rows = compute_factor_history(
            ctx, step=1, lookback=120, history=history
        )
        md = render_report(data)

        # 文件名: code 用 6 位 (去掉 .SZ/.SH 后缀), name 中文
        code6 = code.split(".")[0] if "." in code else code
        name_for_file = s.get("name") or name or ctx.name or code6
        # 2026-09-18 修: 把"占位符/未知/空"统一回退到 stock_basic; 真没有再回退 code6
        # 避免 file=`analyze-601091-601091.md` 这种双重 code
        if not name_for_file or name_for_file in ("未知", "C", f"{code6}"):
            try:
                sb = DataStore.get_stock_basic(code6)
                cand = sb.get("name", "")
                if cand and cand != "未知" and cand != code6:
                    name_for_file = cand
            except Exception:
                pass
            if not name_for_file or name_for_file == "未知":
                name_for_file = code6
        out = Path(f"docs/{subdir}/analyze-{code6}-{name_for_file}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")

        # 自动 lint (2026-09-15 加: 防止 column mismatch 漏检)
        try:
            from tools.render.report_linter import lint_report
            lint_result = lint_report(str(out))
            if lint_result.get("warnings"):
                # 2026-10-14 改: 不再截 [:5], 表格列数不匹配的 warning 经常在第 6+ 条
                # (前几条都是 placeholder), 截断会让人看不到真正的列数错位 bug
                warnings = lint_result["warnings"]
                print(f"  ⚠️  Lint 报警 ({len(warnings)} 条):")
                for warn in warnings:
                    print(f"    {warn}")
        except Exception as e:
            print(f"  ⚠️  Lint 失败: {e}")

        # 今日信号 (stdout 输出)
        rows = data.factor_history_rows
        signals = []
        if len(rows) >= 2:
            r = rows[-1]
            changes = diff_rows(rows[-2], rows[-1])
            sigs = extract_signals(changes)
            sig_fmtd = format_signals_for_render(changes)
            for _, detail, direction in sigs:
                signals.append((direction, code, name_for_file, detail))

        return {
            "ok": True,
            "code": code,
            "name": name_for_file,
            "md_path": out,
            "elapsed": time.time() - t0,
            "lines": len(md.splitlines()),
            "current_price": result.current_price,
            "list_type": list_type_label,
            "signals": signals,
        }
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {str(e)[:200]}", "code": code}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="单只 / 多只股票 /t-analyze 详报 (跟 watchlist 批量 11 section 一致)",
    )
    parser.add_argument("--code", help="6 位股票代码 (单只, 例 300274)")
    parser.add_argument("--codes", help="多只代码, 逗号或空格分隔 (例 '600177,300274,300750')")
    parser.add_argument("--name", default=None, help="股票名称 (可选, 默认从 watchlist 读)")
    parser.add_argument("--output-subdir", default=None,
                        help="强制输出子目录 (如 'wyckoff-a2m'), 跳过 watchlist 分流")
    parser.add_argument("--workers", type=int, default=0,
                        help="多进程 worker 数 (--codes 时用; 默认 0 = 单进程; 设 N 用 mp.spawn 并发)")
    args = parser.parse_args()

    # 解析 codes
    if args.code and args.codes:
        print("❌ --code 和 --codes 互斥, 用其中一个", file=sys.stderr)
        return 1
    if args.code:
        codes = [args.code.strip()]
    elif args.codes:
        codes = [c.strip() for c in args.codes.replace(",", " ").split() if c.strip()]
    else:
        print("❌ 必须 --code 或 --codes", file=sys.stderr)
        return 1

    if len(codes) == 1:
        # 单只快路径 (无 mp, 不需要 fork)
        code = codes[0]
        print(f"=== t-analyze {code} ===", flush=True)
        result = process_one(code, args.name, output_subdir=args.output_subdir)

        if not result["ok"]:
            print(f"❌ {result['err']}")
            return 1

        print(f"✅ REPORT: {result['md_path']}")
        print(f"   {result['name']}  ¥{result['current_price']:.2f}  "
              f"[{result['list_type']}]  {result['lines']} lines  {result['elapsed']:.1f}s")
        if result["signals"]:
            print(f"\n今日信号 ({len(result['signals'])} 个):")
            for direction, code, name, detail in result["signals"]:
                arrow = "⬆️买" if direction == "buy" else "⬇️卖"
                print(f"  {arrow} [{code}] {name}: {detail}")
        return 0

    # 多只: 按 --workers 选择走单进程串行还是 mp.spawn 并发
    n_workers = args.workers if args.workers > 0 else 1
    n_workers = min(n_workers, len(codes))
    tasks = [(c, None, args.output_subdir) for c in codes]
    print(f"=== t-analyze {len(codes)} 只 (workers={n_workers}) ===", flush=True)
    rt0 = time.time()
    ok_n = 0
    if n_workers == 1:
        # 单进程串行
        for idx, t in enumerate(tasks, 1):
            code, name_, sub = t
            print(f"\n[{idx}/{len(codes)}] {code} ...", flush=True)
            code_r, ok, path_or_err, elapsed, list_type = _process_one_mp(t)
            if ok:
                ok_n += 1
                print(f"  ✅ {code_r} → {path_or_err} ({elapsed:.1f}s)")
            else:
                print(f"  ⚠️  {code_r}: {path_or_err}")
    else:
        # 多进程 spawn (跨 GIL, 不能 fork 因为 duckdb)
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=n_workers) as pool:
            for idx, result in enumerate(pool.imap_unordered(_process_one_mp, tasks), 1):
                code, ok, path_or_err, elapsed, list_type = result
                mark = "✅" if ok else "⚠️ "
                sys.stdout.write(f"\r  [{idx}/{len(codes)}] {mark} {code} ({elapsed:.1f}s)")
                sys.stdout.flush()
                if ok:
                    ok_n += 1
            print()
    elapsed_total = time.time() - rt0
    print(f"\n=== t-analyze 完成 {ok_n}/{len(codes)} ({ok_n/len(codes)*100:.0f}%), "
          f"总耗时 {elapsed_total:.0f}s (workers={n_workers}) ===")
    return 0 if ok_n == len(codes) else 2


# 2026-10-17 加: --codes 多股票 + multiprocessing worker
# 模块顶层定义, mp.spawn 可以 pickle
def _process_one_mp(task: tuple[str, str | None, str | None]) -> tuple[str, bool, str, float, str]:
    """单只详报 worker (跑在子进程)

    Args:
        task: (code, name, output_subdir) 三元组 — name/output_subdir 都可 None

    Returns:
        (code, ok, md_path_or_err, elapsed, list_type)
    """
    code, name, output_subdir = task
    t0 = time.time()
    try:
        r = process_one(code, name, output_subdir=output_subdir)
        elapsed = time.time() - t0
        ok = bool(r.get("ok"))
        path_or_err = r.get("md_path") if ok else r.get("err", "unknown")
        return (code, ok, path_or_err, elapsed, r.get("list_type", "?"))
    except Exception as e:
        return (code, False, f"{type(e).__name__}: {e}", time.time() - t0, "?")


if __name__ == "__main__":
    sys.exit(main())
