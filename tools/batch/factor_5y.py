"""factor_5y.py — 5 年因子历史批量跑批

用途: 单只 / 批量跑 5 年因子历史 (20 列: 价格 / 威科夫 / 技术指标),
       默认 watchlist (持仓)。

**输出分两种 (2026-11-06 改, 治"目录里堆几百份 md"):**
  - **点名** (`--code` / `--codes`) → 逐只 1 个详细 md (要细看某只票, 就点它)
  - **按范围批量** (默认 watchlist) → **只出 1 份汇总 md**
    `docs/factor-5y/_summary-{scope}.md` (每只 1 行: 5 年累计 / 最大回撤 / RSI6 / 阶段 / 阶段转变)
    逐只 md 要显式加 `--write-md-all` 才出 (并同步清掉不在本次范围的旧 md)
    —— 批量场景的用途是"多只对比找穿越牛熊的票", 对比看**汇总表**才有用,
    一只一只翻 20 列长表没意义, 而且 164 只 × 290KB ≈ 49MB 把目录撑爆。

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
  - 批量按 `--workers` 并发 (默认 4), 21 只持仓 ~20 秒

复用基础:
  - tools/analysis/analysis_result_signals.py::compute_factor_history (跟 t_analyze_one 完全一致)
  - tools/render/report_renderer.py::_section_factor_history (跟 t-analyze --all 📈 因子历史走势表同一函数)
  - tools/render/factor_history_only.py (单只 stdout 版, 无 md 文件)

输出:
  - 汇总 (按范围批量, 默认):
    docs/factor-5y/_summary-{scope}.md
    └─ 1 张表: 每只 1 行 (5 年累计% / 最大回撤% / 当前RSI6 / 5年最低RSI6 / 阶段(日/周) / 阶段转变数 / 行数)
  - 逐只 (点名, 或批量加 --write-md-all):
    docs/factor-5y/analyze-{code}-{name}-5y.md (每只 1 个 md)
    └─ 头部:"数据范围 ~1250 日 ≈ 5.0 年" + "采样 step=N"
    └─ 📈 因子历史走势 (20 列 GFM 表)
    └─ 🗒 5 年阶段摘要 (5 年累计, MA20 偏离段)

不替代 /t-analyze:
  - /t-analyze 是 7 section 全要素详报 (近 120 日 lookback=120)
  - /t-factor-5y 只输出因子历史表 (长周期 1-2-3-5 年节奏视角)
  - 信号 / 买/卖/警告 不在长周期表里, 看 /t-analyze

用法:
  bash tools/with_venv.sh python -m tools.batch.factor_5y --code 600089
  bash tools/with_venv.sh python -m tools.batch.factor_5y                       # 默认 watchlist 持仓 → 只出汇总 md
  bash tools/with_venv.sh python -m tools.batch.factor_5y --write-md-all        # 批量 + 逐只 md
  bash tools/with_venv.sh python -m tools.batch.factor_5y --watchlist-types 持仓,blowout
  bash tools/with_venv.sh python -m tools.batch.factor_5y --all-market
  bash tools/with_venv.sh python -m tools.batch.factor_5y --step 5              # 紧凑 250 行
  bash tools/with_venv.sh python -m tools.batch.factor_5y --codes 600089 000792  # 多只 (逐只出 md)
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
                list_type_label: str = "持仓", write_md: bool = True) -> dict:
    """单只跑 5 年因子历史

    Args:
        write_md: True = 落 1 份详细 md; False = 只算不落盘 (批量汇总模式)
    """
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

        # 加一段"5 年阶段"摘要 (RSI / 威科夫 1 句话)
        latest = data.factor_history_rows[-1] if data.factor_history_rows else {}
        first_row = data.factor_history_rows[0] if data.factor_history_rows else {}
        summary_lines = ["## 🗒 5 年阶段摘要\n"]
        if first_row and latest:
            p_open = first_row.get("close")
            p_close = latest.get("close")
            rsi6_now = latest.get("rsi6")
            wy_d = latest.get("wyckoff_daily", "?")
            if p_open and p_close:
                cum = (p_close / p_open - 1) * 100
                summary_lines.append(
                    f"- **5 年累计**: {p_open:.2f} → {p_close:.2f} ({cum:+.1f}%)"
                )
            if rsi6_now is not None:
                summary_lines.append(f"- **当日 RSI6**: {rsi6_now:.1f}")
            summary_lines.append(f"- **当日 Wyckoff 阶段**: {wy_d}")
            summary_lines.append("")

        md = header + "\n".join(summary_lines) + "## 📈 因子历史走势\n\n" + section

        out_path = None
        if write_md:
            output_dir.mkdir(parents=True, exist_ok=True)
            out_path = output_dir / f"analyze-{code}-{ctx.name or name}-5y.md"
            out_path.write_text(md, encoding="utf-8")

        return {
            "ok": True, "code": code, "name": ctx.name or name,
            "rows": rows_n, "out": str(out_path) if out_path else "",
            "msgs": len(dates), "first": first_d, "last": last_d,
            "metrics": _summarize_metrics(data.factor_history_rows),
        }
    except Exception as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}", "code": code}



# ============================================================
# 批量汇总 (按范围跑时只出 1 份表, 不逐只落 md)
# ============================================================

def _summarize_metrics(rows: list[dict]) -> dict:
    """从 5 年因子历史行算汇总指标 (批量汇总表用)"""
    closes = [float(r["close"]) for r in rows if r.get("close")]
    rsi_list = [float(r["rsi6"]) for r in rows if r.get("rsi6") is not None]

    cum_pct = None
    if len(closes) >= 2 and closes[0] > 0:
        cum_pct = (closes[-1] / closes[0] - 1) * 100

    max_drawdown = None
    if closes:
        peak = closes[0]
        worst = 0.0
        for c in closes:
            peak = max(peak, c)
            if peak > 0:
                worst = min(worst, (c / peak - 1) * 100)
        max_drawdown = worst

    # 阶段转变: 项目口径 (跟 diff_rows 的 wyckoff_daily 一致)
    #   A→M (Accumulation→Markup) = 买信号, M→D (Markup→Distribution) = 卖信号
    buy_count = 0
    sell_count = 0
    last_buy_date = ""
    last_sell_date = ""
    prev_stage = None
    for r in rows:
        cur = str(r.get("wyckoff_daily") or "")
        if prev_stage is not None and cur and cur != prev_stage:
            if prev_stage == "Accumulation" and cur == "Markup":
                buy_count += 1
                last_buy_date = str(r.get("date", ""))
            elif prev_stage == "Markup" and cur == "Distribution":
                sell_count += 1
                last_sell_date = str(r.get("date", ""))
        prev_stage = cur

    latest = rows[-1] if rows else {}
    return {
        "cum_pct": cum_pct,
        "max_drawdown": max_drawdown,
        "rsi6_now": latest.get("rsi6"),
        "rsi6_min": min(rsi_list) if rsi_list else None,
        "stage_daily": latest.get("wyckoff_daily", "?"),
        "stage_weekly": latest.get("wyckoff_weekly", "?"),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "last_buy_date": last_buy_date,
        "last_sell_date": last_sell_date,
    }


def _write_summary_md(results: list[dict], output_dir: Path,
                       scope_label: str, step: int) -> Path:
    """写批量汇总 md (每只 1 行, 便于横向对比)"""
    ok = [r for r in results if r.get("ok")]
    lines = [
        "# 5 年因子历史 — 批量汇总",
        "",
        f"> 范围: {scope_label} ({len(ok)} 只) | 窗口: 1250 个交易日 ≈ 5.0 年 | 采样 step={step}",
        "> 数据源: DataStore (本地 K 线, 0 网络)",
        "> 逐只详细表: `python -m tools.batch.factor_5y --code <代码>`",
        "",
        "| 代码 | 名称 | 5年累计% | 最大回撤% | 当前RSI6 | 5年最低RSI6 | 阶段(日/周) | 5年阶段转变 | 最近A→M/M→D | 行数 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(ok, key=lambda x: x.get("code", "")):
        m = r.get("metrics") or {}
        cum = f"{m['cum_pct']:+.1f}%" if m.get("cum_pct") is not None else "—"
        mdd = f"{m['max_drawdown']:.1f}%" if m.get("max_drawdown") is not None else "—"
        rsi_now = f"{m['rsi6_now']:.1f}" if m.get("rsi6_now") is not None else "—"
        rsi_min = f"{m['rsi6_min']:.1f}" if m.get("rsi6_min") is not None else "—"
        stage = f"{m.get('stage_daily', '?')}/{m.get('stage_weekly', '?')}"
        turns = f"{m.get('buy_count', 0)}A→M/{m.get('sell_count', 0)}M→D"
        last_turns = f"{m.get('last_buy_date', '') or '—'}/{m.get('last_sell_date', '') or '—'}"
        lines.append(f"| {r['code']} | {r.get('name', '')} | {cum} | {mdd} | {rsi_now} | "
                     f"{rsi_min} | {stage} | {turns} | {last_turns} | {r.get('rows', 0)} |")

    output_dir.mkdir(parents=True, exist_ok=True)
    scope = scope_label.replace(",", "-").replace("/", "-")
    out_path = output_dir / f"_summary-{scope}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="5 年因子历史批量跑批 (复用 /t-analyze 同链路, 仅 lookback=1250)")
    ap.add_argument("--code", help="单只代码 (例 600089 / 600089.SH)")
    ap.add_argument("--codes", nargs="+", help="多只代码 (空格分隔)")
    ap.add_argument("--all-market", action="store_true", help="全市场扫 (默认 watchlist 持仓)")
    ap.add_argument("--watchlist-types", default="持仓",
                    help="watchlist 类型过滤, 逗号分隔 (默认 持仓)")
    ap.add_argument("--step", type=int, default=1, help="采样间隔, 每 N 个交易日采 1 行 (默认 1=每天 1 行, 推荐 step=5 看紧凑 250 行)")
    ap.add_argument("--output-dir", default="docs/factor-5y", help="md 输出目录 (默认 docs/factor-5y)")
    ap.add_argument("--write-md-all", action="store_true",
                    help="按范围批量时也逐只落 md (默认只出 1 份汇总 md)")
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
    scope_mode = not args.code and not args.codes
    types_label = args.watchlist_types if scope_mode else "single"
    # 点名 (--code/--codes) → 逐只出 md; 按范围批量 → 默认只出汇总 md
    write_md = (not scope_mode) or args.write_md_all

    print(f"=== 5 年因子历史 (scope: {types_label}, step={args.step}) ===")
    print(f"  输出: {output_dir}/")
    print(f"  并发: {args.workers}")
    print(f"  任务: {len(codes)} 只")
    print(f"  模式: {'逐只 md' if write_md else '只出 1 份汇总 md (逐只 md 要加 --write-md-all)'}\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(process_one, code, name, args.step, output_dir, types_label,
                        write_md): (code, name)
            for code, name in codes
        }
        for fut in as_completed(futs):
            code, name = futs[fut]
            r = fut.result()
            tag = "✅" if r.get("ok") else "❌"
            if r.get("ok"):
                extra = f" rows={r['rows']}" + (f" → {r['out']}" if r.get("out") else "")
            else:
                extra = f" err={r.get('err')}"
            print(f"  {tag} {code} {name}: {extra}")
            results.append(r)

    ok_n = sum(1 for r in results if r.get("ok"))
    print(f"\n=== 完成 {ok_n}/{len(results)} ===")

    summary_path = None
    if scope_mode and ok_n:
        summary_path = _write_summary_md(results, output_dir, types_label, args.step)
        print(f"📊 汇总: {summary_path}")

    if scope_mode and write_md:
        # --write-md-all: 目录镜像本次范围 (清掉不在范围内的旧逐只 md)
        keep = {Path(r["out"]).name for r in results if r.get("ok") and r.get("out")}
        if ok_n >= max(1, len(codes) // 2):
            from tools.storage.safe_fs import safe_delete
            removed = 0
            for f in sorted(output_dir.glob("analyze-*-5y.md")):
                if f.name not in keep and safe_delete(f, reason="factor_5y: 不在本次范围内"):
                    removed += 1
            if removed:
                print(f"🧹 清理 {removed} 个不在本次范围内的旧 md")

    if not args.no_summary and ok_n:
        print(f"\n############################################################")
        print(f"# /t-factor-5y chat summary ({ok_n} 只)")
        print(f"############################################################\n")
        for r in sorted(results, key=lambda x: x.get("code", "")):
            if not r.get("ok"):
                continue
            m = r.get("metrics") or {}
            cum = f"{m['cum_pct']:+.1f}%" if m.get("cum_pct") is not None else "—"
            mdd = f"{m['max_drawdown']:.1f}%" if m.get("max_drawdown") is not None else "—"
            rsi = f"{m['rsi6_now']:.1f}" if m.get("rsi6_now") is not None else "—"
            target = r["out"] if r.get("out") else (str(summary_path) if summary_path else "")
            print(f"📄 {r['code']} {r['name']}: {r['rows']} 行 | 5年{cum} | 回撤{mdd} | RSI6 {rsi} → {target}")


if __name__ == "__main__":
    main()
