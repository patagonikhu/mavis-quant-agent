"""
t_analyze_one.py — 单只股票 /t-analyze 入口 (2026-09-04 新增)

复用 t_analyze_all.py::process_one 的完整流程 (含 compute_factor_history),
保证单只详报跟 watchlist 批量 22 section 完全一致, 不再有 708 vs 829 行差异.

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


def process_one(code: str, name: str | None = None) -> dict:
    """单只分析 + 渲染. 复用 t_analyze_all.py 同款逻辑.

    Returns: {ok, md_path, elapsed, lines, current_price, list_type, signals}
    """
    t0 = time.time()
    wl_map = load_watchlist_map()
    s = wl_map.get(code, {"code": code, "name": name or "", "list_type": "自选"})
    list_type = s.get("list_type", "自选")
    subdir = "portfolio" if list_type == "持仓" else "watchlist"
    list_type_label = "持仓" if list_type == "持仓" else "自选"

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
        if not name_for_file or name_for_file == "未知":
            try:
                sb = DataStore.get_stock_basic(code6)
                name_for_file = sb.get("name", code6)
            except Exception:
                name_for_file = code6
        out = Path(f"docs/{subdir}/analyze-{code6}-{name_for_file}.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")

        # 自动 lint (2026-09-15 加: 防止 column mismatch 漏检)
        try:
            from tools.render.report_linter import lint_report
            lint_result = lint_report(str(out))
            if lint_result.get("warnings"):
                print(f"  ⚠️  Lint 报警 ({len(lint_result['warnings'])} 条):")
                for warn in lint_result["warnings"][:5]:
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
        description="单只股票 /t-analyze 详报 (跟 watchlist 批量 22 section 一致)",
    )
    parser.add_argument("--code", required=True, help="6 位股票代码 (例 300274)")
    parser.add_argument("--name", default=None, help="股票名称 (可选, 默认从 watchlist 读)")
    args = parser.parse_args()

    print(f"=== t-analyze {args.code} ===", flush=True)
    result = process_one(args.code, args.name)

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
            print(f"  {arrow} | {detail}")
    else:
        print("  无新信号")
    return 0


if __name__ == "__main__":
    sys.exit(main())
