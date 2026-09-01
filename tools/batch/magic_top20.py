"""
magic_top20.py — 算全市场科技股 Magic Formula 排名, 写 docs/magic-top20.md

用法:
    bash tools/with_venv.sh python -m tools.batch.magic_top20
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --top 50
    bash tools/with_venv.sh python -m tools.batch.magic_top20 --period 2026Q2

公式 (Greenblatt 2005):
    ROC = EBIT / (净营运资本 + 固定资产)            ← 排名 1
    EY  = EBIT / EV                                ← 排名 2
    综合 = (ROC 排名 + EY 排名) / 2, 数字小的胜出

数据源:
    1) 财务: data/history/financials/{YYYYQN}.parquet
              (sync_financials 拉的 fina_indicator_vip 全市场)
    2) 市值: DataStore.get_daily_basic (Tushare daily_basic, 单位"万")
    3) 名称: DataStore.get_stock_basic

排名过滤:
    - 行业: EXCLUDED_INDUSTRIES (银行/地产/公用等 8 类) → 跳过
    - 数据: ROC/EY 任一 None → 跳过
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# 路径兜底 (跟其它 batch 一致)
_TOOLS = Path(__file__).resolve().parent.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from tools.factors.valuation.magic_formula import (  # noqa: E402
    EXCLUDED_INDUSTRIES,
    batch_magic_scores,
)


# ============================================================
# 排名
# ============================================================

def rank_magic(results: list[dict], top: int = 20) -> list[dict]:
    """联合排名 (Greenblatt 原版)

    流程:
      1) 筛有效 (roc, ey 都非 None)
      2) 按 roc 降序排 → roc_rank 1..N
      3) 按 ey  降序排 → ey_rank  1..N
      4) combined_rank = (roc_rank + ey_rank) / 2
      5) 按 combined_rank 升序排 → top N

    返回: 排序后 list[dict], 每项含 roc_rank / ey_rank / combined_rank
    """
    # 1) 过滤
    valid = [r for r in results if r.get("roc") is not None and r.get("ey") is not None]
    skipped = len(results) - len(valid)

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
    lines.append("# 改报告期 (需先跑 sync_financials 拉对应季)")
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
    args = parser.parse_args()

    # 1) 找财务文件
    fin_dir = _TOOLS.parent / "data" / "history" / "financials"
    parquet_files = sorted(fin_dir.glob("*.parquet"))
    if not parquet_files:
        print(f"❌ 财务数据空: {fin_dir} 没 parquet")
        print("   先跑: bash tools/with_venv.sh python -c \"from tools.kline_store import sync_financials; sync_financials()\"")
        return 1

    if args.period:
        fin_file = fin_dir / f"{args.period}.parquet"
        if not fin_file.exists():
            print(f"❌ 找不到 {fin_file}, 现有: {[p.name for p in parquet_files]}")
            return 1
    else:
        fin_file = parquet_files[-1]
        args.period = fin_file.stem  # e.g. "2026Q2"

    print(f"📂 财务文件: {fin_file.name}  |  读股票池...")
    df = pd.read_parquet(fin_file)
    df_ok = df[df["fetch_status"] == "ok"]
    codes = df_ok["code"].tolist()
    print(f"   {len(codes)} 只 (fetch_status=ok)")

    # 2) 批量算 ROC + EY
    print(f"🔄 跑 batch_magic_scores ({len(codes)} 只, 含 market_cap)...")
    t0 = datetime.now()
    results = batch_magic_scores(codes, with_market_cap=True)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"   耗时 {elapsed:.1f}s")

    # 3) 排名
    top_n = rank_magic(results, top=args.top)

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
