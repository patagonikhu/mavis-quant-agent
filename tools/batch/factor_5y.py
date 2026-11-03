"""factor_5y.py — 5 年因子历史批量跑批

用途: 单只 / 批量跑 5 年因子历史 (30+ 列: 价格 / 缠论 / 威科夫 / 结构 / 技术指标),
       落 1 个 md / 只, 默认 watchlist (持仓)。

复用链路 (跟 /t-analyze 完全同接口, 仅 lookback 改 1250 + 输出目录换):
  DataStore.get_ctx(code) → AnalysisEngine.analyze_history → compute_factor_history
  → RenderData.from_result → _section_factor_history(lookback=1250)
  → 落盘 docs/factor-5y/analyze-{code}-{name}-5y.md

数据特性:
  - 0 网络, 走 DataStore parquet
  - 5 年窗口硬编码: lookback=1250 (≈ 252 交易日 × 5 年 = 1260, 1250 是项目默认)
  - `--step` 控制采样密度: step=1 默认(每天 1 行, 2026-10-12 改:用户要日度 PE 数据)
                                step=5 紧凑视图(每 5 个交易日 1 行, 1250/5=250 行)
                                step=N 推荐 >= 1 大表降采样
  - 批量按 `--workers` 并发 (默认 4), 23 只持仓 ~20 秒

复用基础:
  - tools/analysis/analysis_result_signals.py::compute_factor_history (跟 t_analyze_one 完全一致)
  - tools/render/report_renderer.py::_section_factor_history (跟 t-analyze --all 📈 因子历史走势表同一函数)
  - tools/render/factor_history_only.py (单只 stdout 版, 无 md 文件)

输出:
  - docs/factor-5y/analyze-{code}-{name}-5y.md (每只 1 个 md)
    └─ 头部:"数据范围 ~1250 日 ≈ 5.0 年" + "采样 step=N"
    └─ 📈 因子历史走势 (30+ 列 GFM 表)
    └─ 🗒 5 年阶段摘要 (5 年累计, MA20 偏离段)

不替代 /t-analyze:
  - /t-analyze 是 7 section 全要素详报 (近 120 日 lookback=120)
  - /t-factor-5y 只输出因子历史表 (长周期 1-2-3-5 年节奏视角)
  - 信号 / 买/卖/警告 不在长周期表里, 看 /t-analyze

用法:
  bash tools/with_venv.sh python -m tools.batch.factor_5y --code 600089
  bash tools/with_venv.sh python -m tools.batch.factor_5y                       # 默认 watchlist 持仓
  bash tools/with_venv.sh python -m tools.batch.factor_5y --watchlist-types 持仓,blowout
  bash tools/with_venv.sh python -m tools.batch.factor_5y --all-market
  bash tools/with_venv.sh python -m tools.batch.factor_5y --step 5              # 紧凑 250 行
  bash tools/with_venv.sh python -m tools.batch.factor_5y --codes 600089 000792  # 多只
"""
from __future__ import annotations
import argparse
import sys
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def load_codes_from_watchlist(types: list[str] | None) -> list[tuple[str, str]]:
    """读 watchlist.json, 返回 [(code, name), ...]"""
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


def process_one(code: str, name: str, step: int, output_dir: Path,
                list_type_label: str = "持仓") -> dict:
    """单只跑 5 年因子历史, 落盘 1 个 md"""
    from tools.storage.store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.render_data import RenderData
    from tools.analysis.analysis_result_signals import compute_factor_history
    from tools.render.report_renderer import _section_factor_history

    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            return {"ok": False, "err": f"{code} 无 K 线 (请先 /t-sync-data)", "code": code}

        all_dates = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
        # 5 年窗口 (1250 日, 留 buffer 取全)
        dates = all_dates[-1250:] if len(all_dates) >= 1250 else all_dates

        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2:
            return {"ok": False, "err": f"history 不足 ({len(history)} 根)", "code": code}

        result = history[dates[-1]]
        data = RenderData.from_result(ctx, result)
        data.factor_history_rows = compute_factor_history(
            ctx, step=step, lookback=1250, history=history
        )

        section = _section_factor_history(data, lookback=1250)

        # md header
        first_d = ctx.kline[max(0, len(ctx.kline) - 1250)]["trade_date"]
        last_d = ctx.kline[-1]["trade_date"]
        rows_n = len(data.factor_history_rows)
        header = (
            f"# 因子历史走势 (5 年) — {code} {ctx.name or name}\n\n"
            f"> 数据范围: {first_d} ~ {last_d} ({len(ctx.kline)} 日 ≈ {len(ctx.kline)/252:.1f} 年)\n"
            f"> 采样: step={step} (每 {step} 个交易日 1 行, 共 {rows_n} 行)\n"
            f"> list_type: {list_type_label}\n"
            f"> 数据源: DataStore (本地 K 线, 0 网络)\n\n"
        )

        # 加一段"5 年阶段"摘要 (缠论三要素 + RSI / 威科夫 1 句话)
        latest = data.factor_history_rows[-1] if data.factor_history_rows else {}
        first_row = data.factor_history_rows[0] if data.factor_history_rows else {}
        summary_lines = ["## 🗒 5 年阶段摘要\n"]
        if first_row and latest:
            p_open = first_row.get("close")
            p_close = latest.get("close")
            rsi6_now = latest.get("rsi6")
            wy_d = latest.get("wyckoff_daily", "?")
            bsp_d = latest.get("bsp_daily", "")
            if p_open and p_close:
                cum = (p_close / p_open - 1) * 100
                summary_lines.append(
                    f"- **5 年累计**: {p_open:.2f} → {p_close:.2f} ({cum:+.1f}%)"
                )
            if rsi6_now is not None:
                summary_lines.append(f"- **当日 RSI6**: {rsi6_now:.1f}")
            summary_lines.append(f"- **当日 Wyckoff 阶段**: {wy_d}")
            if bsp_d:
                summary_lines.append(f"- **当日 缠论买卖点**: {bsp_d}")
            summary_lines.append("")

        md = header + "\n".join(summary_lines) + "## 📈 因子历史走势\n\n" + section

        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"analyze-{code}-{ctx.name or name}-5y.md"
        out_path.write_text(md, encoding="utf-8")

        return {
            "ok": True, "code": code, "name": ctx.name or name,
            "rows": rows_n, "out": str(out_path), "msgs": len(dates),
            "first": first_d, "last": last_d,
        }
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}", "code": code}


def main():
    ap = argparse.ArgumentParser(description="5 年因子历史批量跑批 (复用 /t-analyze 同链路, 仅 lookback=1250)")
    ap.add_argument("--code", help="单只代码 (例 600089 / 600089.SH)")
    ap.add_argument("--codes", nargs="+", help="多只代码 (空格分隔)")
    ap.add_argument("--all-market", action="store_true", help="全市场扫 (默认 watchlist 持仓)")
    ap.add_argument("--watchlist-types", default="持仓",
                    help="watchlist 类型过滤, 逗号分隔 (默认 持仓)")
    ap.add_argument("--step", type=int, default=1, help="采样间隔, 每 N 个交易日采 1 行 (默认 1=每天 1 行, 推荐 step=5 看紧凑 250 行)")
    ap.add_argument("--output-dir", default="docs/factor-5y", help="md 输出目录 (默认 docs/factor-5y)")
    ap.add_argument("--workers", type=int, default=4, help="批量并发数 (默认 4)")
    ap.add_argument("--no-summary", action="store_true", help="不输出 chat summary")
    args = ap.parse_args()

    if args.code:
        codes = [(args.code, args.code)]
    elif args.codes:
        codes = [(c, c) for c in args.codes]
    elif args.all_market:
        codes = None  # 全市场 (下面单独处理)
        codes = []  # 全市场跑 5500 只不现实, 不实现, 报错
        print("❌ --all-market 暂不支持 (5500 只, 跑不完)", file=sys.stderr)
        sys.exit(1)
    else:
        types = [t.strip() for t in args.watchlist_types.split(",") if t.strip()]
        codes = load_codes_from_watchlist(types)

    if not codes:
        print("❌ 无代码可跑", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir)
    types_label = args.watchlist_types if not args.code and not args.codes else "single"

    print(f"=== 5 年因子历史 (scope: {types_label}, step={args.step}) ===")
    print(f"  输出: {output_dir}/")
    print(f"  并发: {args.workers}")
    print(f"  任务: {len(codes)} 只\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(process_one, code, name, args.step, output_dir, types_label): (code, name)
            for code, name in codes
        }
        for fut in as_completed(futs):
            code, name = futs[fut]
            r = fut.result()
            tag = "✅" if r.get("ok") else "❌"
            extra = f" rows={r['rows']} → {r['out']}" if r.get("ok") else f" err={r.get('err')}"
            print(f"  {tag} {code} {name}: {extra}")
            results.append(r)

    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"\n=== 完成 {ok_n}/{len(results)} ===")

    if not args.no_summary and ok_n:
        print(f"\n############################################################")
        print(f"# /t-factor-5y chat summary ({ok_n} 只)")
        print(f"############################################################\n")
        for r in results:
            if not r.get("ok"):
                continue
            print(f"📄 {r['code']} {r['name']}: {r['rows']} 行 ({r['first']} ~ {r['last']}) → {r['out']}")


if __name__ == "__main__":
    main()
