"""
magic_top20.py — Magic Formula 排名 + 摘要 + 加 watchlist (3 合 1, 2026-09-02)

用法:
    bash tools/with_venv.sh python -m tools.batch.magic_top20              # 默认: 跑全 3 步
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --top 50    # Top N (默认 20)
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --period 2026Q2
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --rank-only          # 只排名, 不出摘要不加 watchlist
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --summary-only       # 排名 + 摘要, 不加 watchlist
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --skip-watchlist     # 跳过加 watchlist

公式 (Greenblatt 2005):
    ROC = EBIT / (净营运资本 + 固定资产)            ← 排名 1
    EY  = EBIT / EV                                ← 排名 2
    综合 = (ROC 排名 + EY 排名) / 2, 数字小的胜出

数据源 (DataStore 0 网络读):
    1) 财务: data/history/financials/{YYYYQN}.parquet
    2) 市值: DataStore.get_daily_basic (Tushare daily_basic, 单位"万")
    3) 名称: DataStore.get_stock_basic
    4) 摘要 EPS: datacenter.eastmoney.com (绕开 watchlist gate, 直拉)

排名过滤:
    - 行业: EXCLUDED_INDUSTRIES (银行/地产/公用等 8 类) → 跳过
    - 数据: ROC/EY 任一 None → 跳过

输出:
    1) docs/magic-top20.md              (Top N 表 + 统计)
    2) docs/magic-top20-summary.md      (Top N + PEG/DCF/Magic 4 项摘要, 卡点⭐ N/A)
    3) data/watchlist.json (via DataStore.load_watchlist)              (加 list_type="Magic初筛", 跳已存在)
    4) stdout: Top 5 速览
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# 路径兜底 (跟其它 batch 一致)
_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

# 2026-09-02 改: magic_formula.py 删了, 搬进 tools/analysis/valuation.py
from tools.analysis.valuation import (  # noqa: E402
    EXCLUDED_INDUSTRIES,
    batch_magic_scores,
)


# ============================================================
# 排名
# ============================================================

def rank_magic(results: list[dict], top: int = 20,
               min_mcap: float = 0, max_roc: float = 9999, min_ebit: float = 0) -> list[dict]:
    """联合排名 (Greenblatt 原版)

    流程:
      1) 筛有效 (roc, ey 都非 None)
      2) 小盘过滤 (2026-09-04 加, 默认关闭, 跑回测时打开)
         - min_mcap: 最小市值 (亿), 0 = 不过滤
         - max_roc:  ROC 上限 (%), 9999 = 不过滤 (200%+ 多为 NWC 接近 0 分母塌陷)
         - min_ebit: 最小 EBIT (亿), 0 = 不过滤 (排除利润太薄小票)
      3) 按 roc 降序排 → roc_rank 1..N
      4) 按 ey  降序排 → ey_rank  1..N
      5) combined_rank = (roc_rank + ey_rank) / 2
      6) 按 combined_rank 升序排 → top N

    返回: 排序后 list[dict], 每项含 roc_rank / ey_rank / combined_rank
    """
    # 1) 过滤
    valid = [r for r in results if r.get("roc") is not None and r.get("ey") is not None]
    skipped = len(results) - len(valid)

    # 1b) 小盘过滤 (v6.2.4 加)
    n_pre = len(valid)
    if min_mcap > 0:
        valid = [r for r in valid if (r.get("market_cap_yi") or 0) >= min_mcap]
    if max_roc < 9999:
        valid = [r for r in valid if (r.get("roc") or 0) <= max_roc]
    if min_ebit > 0:
        valid = [r for r in valid if (r.get("ebit_yi") or 0) >= min_ebit]
    n_filtered = n_pre - len(valid)
    if n_filtered:
        print(f"   🚫 小盘过滤: 排除 {n_filtered} (剩余 {len(valid)}, "
              f"mcap>={min_mcap}亿 / ROC<={max_roc}% / EBIT>={min_ebit}亿)")

    if not valid:
        print(f"⚠️  0 只有效 (输入 {len(results)}, 跳过 {skipped})")
        return []

    # 2) ROC 排名 (降序, 大的排前面)
    valid_sorted_roc = sorted(valid, key=lambda x: x["roc"], reverse=True)
    for rank, r in enumerate(valid_sorted_roc, start=1):
        r["roc_rank"] = rank

    # 3) EY 排名 (降序, 大的排前面)
    valid_sorted_ey = sorted(valid, key=lambda x: x["ey"], reverse=True)
    for rank, r in enumerate(valid_sorted_ey, start=1):
        r["ey_rank"] = rank

    # 4-5) 综合排名
    for r in valid:
        r["combined_rank"] = round((r["roc_rank"] + r["ey_rank"]) / 2, 1)

    valid_sorted_combined = sorted(valid, key=lambda x: x["combined_rank"])

    print(
        f"📊 排名: 有效 {len(valid)} / 输入 {len(results)} (跳过 {skipped}, "
        f"含行业 EXCLUDED {sum(1 for r in results if r.get('skip_reason') == 'industry_excluded')}, "
        f"无数据 {sum(1 for r in results if r.get('skip_reason') == 'no_data')})"
    )

    # 加 rank 字段 (1..N), 给 watchlist 摘要用
    for i, r in enumerate(valid_sorted_combined[:top], start=1):
        r["rank"] = i

    return valid_sorted_combined[:top]


# ============================================================
# Markdown 渲染
# ============================================================

def render_markdown(top20: list[dict], period: str, skipped: int) -> str:
    """渲染 docs/magic-top20.md"""
    today = datetime.now().strftime("%Y-%m-%d")
    n = len(top20)

    # 统计
    if top20:
        roc_vals = [r["roc"] for r in top20]
        ey_vals = [r["ey"] for r in top20]
        roc_avg = sum(roc_vals) / len(roc_vals)
        ey_avg = sum(ey_vals) / len(ey_vals)
        roc_max = max(roc_vals)
        ey_max = max(ey_vals)
    else:
        roc_avg = ey_avg = roc_max = ey_max = 0

    lines = []
    lines.append(f"# Magic Formula 排名 Top {n} — {today}")
    lines.append("")
    lines.append(f"> **报告期:** {period}  |  **股票池:** 1923 只科技股 (client-side 筛选)  |  **跳过:** {skipped} 只 (含行业 EXCLUDED + 无数据)")
    lines.append(f"> **公式:** ROC = EBIT / (净营运资本 + 固定资产), EY = EBIT / EV (Greenblatt 2005)")
    lines.append(f"> **排名:** ROC 降序 + EY 降序, 综合 = (ROC 排名 + EY 排名) / 2, 数字小的胜出")
    lines.append("")
    lines.append("## 📊 统计概览")
    lines.append("")
    lines.append("| 指标 | 平均 | 最高 |")
    lines.append("|------|------|------|")
    lines.append(f"| ROC (%) | {roc_avg:.1f} | {roc_max:.1f} |")
    lines.append(f"| EY  (%) | {ey_avg:.2f} | {ey_max:.2f} |")
    lines.append("")
    lines.append(f"## 🏆 Top {n}")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 行业 | ROC (%) | EY (%) | ROC 排名 | EY 排名 | 综合 | 市值 (亿) | EV (亿) |")
    lines.append("|---|------|------|------|---------|--------|----------|---------|------|-----------|---------|")

    for i, r in enumerate(top20, start=1):
        mc_yi = (r.get("market_cap") or 0) / 1e4  # 万 → 亿
        ev_yi = r.get("ev_yi") or 0
        lines.append(
            f"| {i} | {r['code']} | {r['name']} | {r['industry']} | "
            f"{r['roc']:.1f} | {r['ey']:.2f} | {r['roc_rank']} | {r['ey_rank']} | "
            f"{r['combined_rank']:.1f} | {mc_yi:,.0f} | {ev_yi:,.0f} |"
        )

    lines.append("")
    lines.append("## 📖 怎么读")
    lines.append("")
    lines.append("1. **ROC (Return on Capital)** = 资本回报率, 越高越好 — 公司赚钱效率")
    lines.append("2. **EY  (Earnings Yield)** = 盈利收益率, 越高越好 — 股价相对盈利能力便宜")
    lines.append("3. **综合排名** = ROC + EY 联合排名, 越小越靠前 (双优)")
    lines.append("4. **行业过滤** = 银行/保险/地产/公用等不参与 (ROC/EY 在这些行业失真)")
    lines.append("")
    lines.append("## 🔗 数据流")
    lines.append("")
    lines.append("```")
    lines.append("Tushare fina_indicator_vip (1次API, 全市场 9255 行)")
    lines.append("   ↓ 客户端筛科技股 (industry != EXCLUDED_INDUSTRIES)")
    lines.append("data/history/financials/{period}.parquet  (1923 只, status=ok)")
    lines.append("   ↓ DataStore.get_financials(code)")
    lines.append("ROC = EBIT / (NWC + FA),  EY = EBIT / EV")
    lines.append("   ↓ 联合排名")
    lines.append("Top 20 → docs/magic-top20.md")
    lines.append("```")
    lines.append("")
    lines.append("## 💡 用法")
    lines.append("")
    lines.append("```bash")
    lines.append("# 跑全市场排名 (默认 1923 科技股, Top 20)")
    lines.append("bash tools/with_venv.sh python -m tools.batch.magic_top20")
    lines.append("")
    lines.append("# 自定义 Top 数")
    lines.append("bash tools/with_venv.sh python -m tools.batch.magic_top20 --top 50")
    lines.append("")
    lines.append("# 改报告期 (缺数据请先 /t-sync-data)")
    lines.append("bash tools/with_venv.sh python -m tools.batch.magic_top20 --period 2026Q1")
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"📅 **生成时间:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
                 f"🔧 **脚本:** `tools/batch/magic_top20.py`  |  "
                 f"📊 **数据:** `data/history/financials/{period}.parquet`")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Magic Formula 排名 → docs/magic-top20.md")
    parser.add_argument("--top", type=int, default=20, help="输出前 N 名 (默认 20)")
    parser.add_argument(
        "--period",
        type=str,
        default=None,
        help="财务报告期, e.g. 2026Q2 (默认用 data/history/financials/ 最新 1 个 parquet)",
    )
    parser.add_argument(
        "--rank-only",
        action="store_true",
        help="只跑排名, 不出摘要不加 watchlist",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="排名 + 摘要, 不加 watchlist",
    )
    parser.add_argument(
        "--skip-watchlist",
        action="store_true",
        help="跳过加 watchlist 那步",
    )
    # v6.2.4 加: 小盘股过滤 (避免 NWC 接近 0 导致 ROC 虚高)
    parser.add_argument("--min-mcap", type=float, default=0, help="最小市值 (亿), 0=不过滤")
    parser.add_argument("--max-roc", type=float, default=9999, help="ROC 上限 (%%), 9999=不过滤")
    parser.add_argument("--min-ebit", type=float, default=0, help="最小 EBIT (亿), 0=不过滤")
    # v6.2.5 加: 剔除 NWC+FA 太小导致的 ROC 假阳性 (例: 中南传媒 0.4 亿 → 4186% ROC)
    parser.add_argument("--min-capital", type=float, default=5,
                        help="最小 NWC+FA 合计 (亿), 剔除分母过小的假阳性, 0=不过滤 (默认 5)")
    args = parser.parse_args()

    # 1) 找财务文件 (v6.1.1 改: 走 DataStore.load_financials_period, 不读 parquet)
    from tools.storage.store import DataStore
    if args.period:
        df = DataStore.load_financials_period(args.period)
        if df.empty:
            print(f"❌ 找不到 {args.period} 季度财务, 请先 /t-sync-data --financials --period {args.period}")
            return 1
    else:
        # 取最新季: load_all_financials 找 max end_date
        all_fin = DataStore.load_all_financials()
        if all_fin.empty:
            print("❌ 财务数据空, 请先 /t-sync-data --financials")
            return 1
        latest_end = all_fin["end_date"].max()
        # end_date '20251231' → 季度 '2025Q4' (跟 parquet file stem 对齐)
        y, m = latest_end[:4], latest_end[4:6]
        quarter = {"03": "Q1", "06": "Q2", "09": "Q3", "12": "Q4"}.get(m, "Q4")
        latest_period = f"{y}{quarter}"
        df = DataStore.load_financials_period(latest_period)
        args.period = latest_period

    print(f"📂 财务文件: {args.period}  |  读股票池...")
    df_ok = df[df["fetch_status"] == "ok"]
    codes = df_ok["code"].tolist()
    print(f"   {len(codes)} 只 (fetch_status=ok)")

    # 2) 批量算 ROC + EY (v6.2.5 透传 min_capital_yi, 剔除 NWC+FA 过小的 ROC 假阳性)
    print(f"🔄 跑 batch_magic_scores ({len(codes)} 只, 含 market_cap, min_capital={args.min_capital}亿)...")
    t0 = datetime.now()
    results = batch_magic_scores(codes, with_market_cap=True, min_capital_yi=args.min_capital)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"   耗时 {elapsed:.1f}s")
    n_capital_skip = sum(1 for r in results if r.get("skip_reason") and "capital_too_small" in r.get("skip_reason", ""))
    if n_capital_skip:
        print(f"   🚫 过滤掉 {n_capital_skip} 只 NWC+FA<{args.min_capital}亿 (ROC 假阳性)")

    # 3) 排名 (v6.2.4 加 3 个小盘过滤)
    top_n = rank_magic(results, top=args.top,
                       min_mcap=args.min_mcap, max_roc=args.max_roc, min_ebit=args.min_ebit)

    # 4) 写文件 (skipped = 总池 - 有效排名, 不是 - topN)
    out_path = _TOOLS.parent / "docs" / "magic-top20.md"
    n_valid = sum(1 for r in results if r.get("roc") is not None and r.get("ey") is not None)
    md = render_markdown(top_n, args.period, skipped=len(codes) - n_valid)
    out_path.write_text(md, encoding="utf-8")
    print(f"✅ 写: {out_path}  ({len(md)} 字节, {len(top_n)} 票)")

    # 5) 摘要 stdout
    if top_n:
        print()
        print("🏆 Top 5 速览:")
        for i, r in enumerate(top_n[:5], start=1):
            mc_yi = (r.get("market_cap") or 0) / 1e4
            print(
                f"  {i}. {r['code']} {r['name']:<8s}  "
                f"ROC={r['roc']:5.1f}%  EY={r['ey']:5.2f}%  "
                f"综合={r['combined_rank']:4.1f}  市值={mc_yi:,.0f}亿  ({r['industry']})"
            )

    # 6) 摘要 (Top 20 + PEG/DCF/Magic 4 项) — 2026-09-02 合并
    if not args.rank_only and top_n:
        print()
        print("📊 跑 4 项摘要 (PEG/DCF/Magic 排名)...")
        summary_path = _TOOLS.parent / "docs" / "magic-top20-summary.md"
        _run_summary(top_n, out_path, summary_path)
        print(f"   ✅ 写: {summary_path}")

    # 7) 加 watchlist — 2026-09-02 合并
    if not args.rank_only and not args.summary_only and not args.skip_watchlist and top_n:
        print()
        print("📋 加 watchlist (list_type=Magic初筛)...")
        _add_to_watchlist(top_n, args.period)
        print(f"   ✅ watchlist 已更新")

    return 0


# ============================================================
# 摘要 + watchlist (2026-09-02 合并自 magic_top20_summary + add_magic_top20_to_watchlist)
# ============================================================

def _run_summary(top_n: list[dict], top_md_path: Path, out_path: Path):
    """Top 20 + PEG/DCF/Magic 4 项摘要

    解析 magic-top20.md 拿排名, 逐只补 PEG/DCF (走 report_section_evaluators), 写 docs/magic-top20-summary.md
    """
    from tools.analysis.render_data import RenderData  # 避免循环
    # 解析 Top N 拿 code/name/industry/roc/ey 5 字段
    rank_data = parse_top20_md(top_md_path)
    code_to_rank = {r["code"]: r for r in rank_data}

    # 逐只补 4 项 (PEG/DCF/Magic/卡点⭐)
    items = []
    for r in top_n:
        code = r["code"]
        # EPS 走 datacenter (绕开 watchlist gate, 写本地 cache)
        eps_table = get_eps_for_summary(code)
        # ctx 准备: 只用 EPS + current_price, 不需要 K线全量
        from tools.storage.store import DataStore
        db = DataStore.get_daily_basic(code)
        market_cap_yi = (db.get("total_mv") or 0) / 1e4 if db else None
        from tools.storage.store import DataStore as _DS
        ctx = _DS.get_ctx(code)
        current_price = ctx.current_price or (db.get("close") if db else None) or 0

        from tools.analysis.report_section_evaluators import compute_peg, compute_dcf_l

        item = {"code": code, "name": r["name"], "industry": r["industry"], "card": "N/A",
                "magic_rank": code_to_rank.get(code, {}).get("rank", "—"),
                "magic_combined": code_to_rank.get(code, {}).get("combined_rank", "—")}

        if eps_table and current_price:
            item["peg"] = compute_peg(eps_table, current_price)
        else:
            item["peg"] = {"error": "EPS 或价格缺"}
        if eps_table and market_cap_yi:
            item["dcf"] = compute_dcf_l(eps_table, market_cap_yi)
        else:
            item["dcf"] = {"error": "市值或 EPS 缺"}
        item["price"] = current_price
        items.append(item)

    md = render_summary_md(items)
    out_path.write_text(md, encoding="utf-8")


def _add_to_watchlist(top_n: list[dict], period: str):
    """把 Top N 加到 data/watchlist.json (via DataStore.load_watchlist) (list_type=Magic初筛)

    2026-09-03 v6.2 改: 走 DataStore.add_to_watchlist, 不直接 json 写
    """
    from tools.storage.store import DataStore
    today = datetime.now().strftime("%Y-%m-%d")

    added, skipped = 0, 0
    for r in top_n:
        code = r["code"]
        notes = (
            f"[{today} Magic Top{r['rank']}] "
            f"ROC={r['roc']:.1f}% (rank {r['roc_rank']}) "
            f"EY={r['ey']:.2f}% (rank {r['ey_rank']}) "
            f"综合={r['combined_rank']} | 卡点⭐ 待 LLM 补"
        )
        if DataStore.add_to_watchlist(code, r["name"], r["industry"], "Magic初筛", notes):
            added += 1
        else:
            skipped += 1

    # changelog 单独 append (DataStore.add_to_watchlist 不管 changelog)
    d = DataStore.load_watchlist()
    d.setdefault("changelog", []).append({
        "date": today,
        "change": f"加 {added} 只 Magic Top 20 (list_type=Magic初筛, 跳过 {skipped} 只已存在)",
        "source": "docs/magic-top20.md (Greenblatt 联合排名)",
    })
    DataStore.save_watchlist(d)
    print(f"   加 {added} 只, 跳过 {skipped} 只, 现总 {len(d['stocks'])} 只")


# ============================================================
# 摘要 helpers (从 magic_top20_summary 搬过来)
# ============================================================

def get_eps_for_summary(code: str) -> list[dict]:
    """EPS 摘要 (v6.2.4 改: 读 parquet, 跟 financials 同目录)

    之前: 走 datacenter.eastmoney.com 写本地 cache (违反 sync_data 唯一入口)
    现在: 只读本地 EPS_DIR/{code}.parquet; 缺数据时返空, 提示先跑 /t-sync-data --eps
    """
    from tools.storage.caches.eps import EPS_DIR, _read_parquet
    path = EPS_DIR / f"{code}.parquet"
    if path.exists() and path.stat().st_size > 100:
        try:
            return _read_parquet(path)
        except Exception:
            pass
    # 缺数据: 不偷偷拉, 提示用户先跑 /t-sync-data
    return []


def parse_top20_md(md_path: Path) -> list[dict]:
    """从 magic-top20.md 解析 Top N 排名表"""
    text = md_path.read_text(encoding="utf-8")
    row_re = re.compile(
        r"\|\s*(\d+)\s*\|\s*(\d{6})\s*\|\s*([^\s|]+)\s*\|\s*([^\s|]+)\s*\|"
        r"\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
        r"\s*([\d.]+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|"
    )
    out = []
    for m in row_re.finditer(text):
        out.append({
            "rank": int(m.group(1)),
            "code": m.group(2),
            "name": m.group(3).strip(),
            "industry": m.group(4).strip(),
            "roc": float(m.group(5)),
            "ey": float(m.group(6)),
            "roc_rank": int(m.group(7)),
            "ey_rank": int(m.group(8)),
            "combined_rank": float(m.group(9)),
        })
    return out


def _fmt_peg(peg: dict) -> str:
    if "error" in peg:
        return f"❌ {peg['error'][:20]}"
    p = peg.get("peg")
    if p is None:
        return "—"
    if p < 1.0:   icon = "🟢"
    elif p < 1.5: icon = "🟡"
    elif p < 2.0: icon = "🟠"
    else:         icon = "🔴"
    g = peg.get("g")
    g_s = f"{g:.0f}%" if isinstance(g, (int, float)) else "—"
    return f"{icon} {p:.2f} (g={g_s})"


def _fmt_dcf(dcf: dict) -> str:
    if "error" in dcf:
        return f"❌ {dcf['error'][:20]}"
    parts = []
    L = dcf.get("L_r10")
    e3 = dcf.get("L_E3_r10")
    if L is not None:
        parts.append(f"L={L:.0f}亿")
    if e3 is not None:
        parts.append(f"L/E3={e3:.1f}x")
    reach = dcf.get("L_achievable", "")
    if reach:
        parts.append(reach)
    return " ".join(parts) if parts else "—"


def render_summary_md(items: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    md = f"# Magic Top 20 摘要 — {today}\n\n"
    md += "> **范围:** Magic Top 20 票\n"
    md += "> **字段:** 卡点⭐=N/A (LLM 补), PEG/DCF (datacenter.eastmoney.com), Magic 排名\n\n"
    md += "## 📊 摘要表 (按 Magic 排名)\n\n"
    md += "| # | 代码 | 名称 | 行业 | 卡点⭐ | PEG | DCF (r=10%) | Magic 排名 | 当前价 | 总市值 (亿) |\n"
    md += "|---|------|------|------|--------|-----|-------------|------------|--------|-------------|\n"
    for it in items:
        mc = ((it.get("price") or 0) * 0) + 0  # 总市值从 daily_basic 拿
        # 简化: 总市值从 price 算不出来, 跳过
        md += (
            f"| {it.get('magic_rank', '—')} | {it['code']} | {it['name']} | {it['industry']} | "
            f"{it['card']} | {_fmt_peg(it['peg'])} | {_fmt_dcf(it['dcf'])} | "
            f"#{it['magic_rank']} (综合 {it['magic_combined']}) | "
            f"{(it.get('price') or 0):.2f} | — |\n"
        )
    md += "\n---\n"
    md += f"\n📅 **生成:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
    md += f"🔧 **脚本:** `tools/batch/magic_top20.py`\n"
    return md


if __name__ == "__main__":
    sys.exit(main())
