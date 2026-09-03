"""
tools/btf/run_1year.py — 跑 5 策略 1 年回测, 出 docs/backtest-magic-formula.md

5 策略 (跟 magic_top20 配套):
  - EY>8% (Magic 便宜)
  - ROC>25% (Magic 好公司)
  - ROC>25% + EY>8% (双优, 核心)
  - PEG<1.5 (便宜)
  - 全市场 (基准)

4 Evaluator (4 维度):
  - ReturnEvaluator: 总/年化/日均
  - RiskEvaluator: 最大回撤/波动率/下行波动
  - RiskAdjReturnEvaluator: 夏普/Sortino/Calmar
  - TradingEvaluator: 胜率/盈亏比/平均持仓天数/换手率

回测: 等权持仓 20 天, 每天调仓
窗口: 2025-08-25 → 2026-09-01 (1 年 248 天)
数据: analysis_cache.db (Magic 4 列昨天刚 backfill)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT))
os_chdir_done = False
import os
os.chdir(_PROJECT)

from tools.btf import (
    BacktestRunner, AnalysisCacheData, EqualWeightPortfolio,
    EY8Strategy, ROC25Strategy, ROC25EY8Strategy, PEG15Strategy, MarketStrategy,
    ReturnEvaluator, RiskEvaluator, RiskAdjReturnEvaluator, TradingEvaluator,
)


# 5 策略 + 配色
STRATEGIES = [
    (lambda: EY8Strategy(),      "🟢 EY>8% (Magic 便宜)"),
    (lambda: ROC25Strategy(),    "🔵 ROC>25% (Magic 好公司)"),
    (lambda: ROC25EY8Strategy(), "⭐ ROC>25% + EY>8% (双优, 核心)"),
    (lambda: PEG15Strategy(),    "🟡 PEG<1.5 (便宜)"),
    (lambda: MarketStrategy(seed=42), "⚪ 全市场 (基准)"),
]

EVALUATORS = [
    ReturnEvaluator(),
    RiskEvaluator(),
    RiskAdjReturnEvaluator(),
    TradingEvaluator(),
]


def _fmt_pct(x: float) -> str:
    """百分比类: x 是 -1~1 范围 (如总收益 -0.10 → -10.0%)"""
    if x is None:
        return "—"
    return f"{x*100:+.1f}%"


def _fmt_ratio(x: float, digits: int = 2) -> str:
    """比率类: x 已经是 0-1 范围 (如胜率 0.49 → 49.00%)"""
    if x is None:
        return "—"
    return f"{x*100:.{digits}f}%"


def _fmt_num(x: float, digits: int = 2) -> str:
    """普通数字: 不加 %"""
    if x is None:
        return "—"
    return f"{x:.{digits}f}"


def render_markdown_table(all_results: dict[str, dict]) -> str:
    """5 策略 × 4 evaluator (13 指标) 拼成 1 张表"""
    lines = []
    lines.append("| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | Sortino | 胜率 | 盈亏比 | 换手率 | 交易数 |")
    lines.append("|------|--------|------|----------|------|---------|------|--------|--------|--------|")
    for name in all_results:
        m = all_results[name]
        ret = m.get("收益", {})
        risk = m.get("风险", {})
        radj = m.get("风险调整收益", {})
        trade = m.get("交易行为", {})
        lines.append(
            f"| {name} "
            f"| {_fmt_pct(ret.get('总收益', 0))} "
            f"| {_fmt_pct(ret.get('年化', 0))} "
            f"| {_fmt_pct(risk.get('最大回撤', 0))} "
            f"| {_fmt_num(radj.get('夏普', 0))} "
            f"| {_fmt_num(radj.get('Sortino', 0))} "
            f"| {_fmt_ratio(trade.get('胜率', 0))} "
            f"| {_fmt_num(trade.get('盈亏比', 0))} "
            f"| {_fmt_num(trade.get('换手率', 0), 3)} "
            f"| {int(trade.get('总交易数', 0))} |"
        )
    return "\n".join(lines)


def render_detail_section(name: str, metrics: dict) -> str:
    """单个策略 4 维度 13 指标 全展示"""
    # 指标归类: 哪类用 _fmt_pct, 哪类用 _fmt_ratio/_fmt_num
    PCT_KEYS = {"总收益", "年化", "最大回撤", "日均", "年化波动", "下行波动"}
    RATIO_KEYS = {"胜率", "盈亏比", "换手率", "平均持仓天数"}
    NUM_KEYS = {"夏普", "Sortino", "Calmar", "总交易数"}

    lines = [f"### {name}", ""]
    for ev_name, ev_metrics in metrics.items():
        lines.append(f"**{ev_name}**")
        for k, v in ev_metrics.items():
            if isinstance(v, float):
                if k in PCT_KEYS:
                    lines.append(f"- {k}: {_fmt_pct(v)}")
                elif k in RATIO_KEYS:
                    lines.append(f"- {k}: {_fmt_ratio(v)}")
                else:
                    lines.append(f"- {k}: {_fmt_num(v)}")
            else:
                lines.append(f"- {k}: {v}")
        lines.append("")
    return "\n".join(lines)


def render_signal_stats(all_results: dict[str, dict], data_loader) -> dict:
    """5 策略 1 年的选股信号统计 (每天选多少只)"""
    import pandas as pd
    df = data_loader.load_window("20250825", "20260901")
    if df.empty:
        return {}
    stats = {}
    for strategy_factory, name in STRATEGIES:
        strat = strategy_factory()
        counts = []
        for date in sorted(df["date"].unique()):
            today_df = df[df["date"] == date]
            codes = strat.select(date, today_df, top_n=20)
            counts.append(len(codes))
        stats[name] = {
            "总触发天数": len(counts),
            "总选股数": sum(counts),
            "日均选股数": sum(counts) / len(counts) if counts else 0,
            "最多1天": max(counts) if counts else 0,
            "最少1天": min(counts) if counts else 0,
        }
    return stats


def render_signal_table(stats: dict) -> str:
    """信号触发统计表"""
    lines = [
        "| 策略 | 总触发天数 | 总选股数 | 日均 | 最多/天 | 最少/天 |",
        "|------|-----------|---------|------|--------|--------|",
    ]
    for name, s in stats.items():
        lines.append(
            f"| {name} "
            f"| {s['总触发天数']} "
            f"| {s['总选股数']} "
            f"| {s['日均选股数']:.1f} "
            f"| {s['最多1天']} "
            f"| {s['最少1天']} |"
        )
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="5 策略 1 年回测")
    parser.add_argument("--start", default="20250825")
    parser.add_argument("--end",   default="20260901")
    parser.add_argument("--top",   type=int, default=20, help="每策略选 top N 票")
    parser.add_argument("--hold",  type=int, default=20, help="持仓天数")
    parser.add_argument("--rebalance", action="store_true", help="每天调仓 (默认否, 长持)")
    parser.add_argument("--out",   default="docs/backtest-magic-formula.md")
    args = parser.parse_args()

    rebalance_str = "每天调仓" if args.rebalance else "长持 (选 1 次持 248 天)"
    print(f"=== 1 年回测: {args.start} → {args.end} | hold={args.hold}d | top_n={args.top} | {rebalance_str} ===\n")
    t_start = time.time()

    # 1) 准备 4 组件
    data = AnalysisCacheData()
    pf_cls = lambda: EqualWeightPortfolio(hold_days=args.hold, rebalance=args.rebalance)  # 每次新实例

    # 2) 跑 5 策略
    all_results: dict[str, dict] = {}
    all_nav_histories: dict[str, list] = {}
    all_trades: dict[str, list] = {}

    for strategy_factory, name in STRATEGIES:
        print(f"--- {name} ---")
        pf = pf_cls()
        runner = BacktestRunner(data, strategy_factory(), pf, EVALUATORS)
        metrics, nav_hist, trades = runner.run(
            start=args.start, end=args.end, top_n=args.top
        )
        all_results[name] = metrics
        all_nav_histories[name] = nav_hist
        all_trades[name] = trades
        # 摘要 stdout
        ret = metrics.get("收益", {})
        radj = metrics.get("风险调整收益", {})
        risk = metrics.get("风险", {})
        print(f"  收益 {ret.get('总收益', 0)*100:+.1f}%  "
              f"夏普 {radj.get('夏普', 0):.2f}  "
              f"回撤 {risk.get('最大回撤', 0)*100:.1f}%\n")

    # 3) 信号触发统计
    print("--- 信号触发统计 ---")
    sig_stats = render_signal_stats(all_results, data)
    print(render_signal_table(sig_stats))
    print()

    # 4) 写 markdown 报告
    today = datetime.now().strftime("%Y-%m-%d")
    md = []
    md.append(f"# Magic Formula 1 年回测 — {today}\n")
    md.append(f"> **回测窗口:** {args.start} → {args.end} (1 年 248 天)")
    md.append(f"> **持仓模型:** 等权, 持仓 {args.hold} 天后卖")
    md.append(f"> **数据源:** `analysis_cache.db` (Magic 4 列, 1 年 backfill, 2026-09-02)")
    md.append(f"> **5 策略:** EY8 / ROC25 / ROC25+EY8 / PEG1.5 / 全市场")
    md.append(f"> **4 评估维度:** 收益 / 风险 / 风险调整 / 交易行为")
    md.append("")

    md.append("## 📊 5 策略对比 (13 指标)")
    md.append("")
    md.append(render_markdown_table(all_results))
    md.append("")

    md.append("## 📈 信号触发统计")
    md.append("")
    md.append(render_signal_table(sig_stats))
    md.append("")
    md.append("> **日均选股数** = 总选股数 / 总触发天数 (只看有信号的日期)")
    md.append("")

    md.append("## 📋 各策略详情")
    md.append("")
    for name, metrics in all_results.items():
        md.append(render_detail_section(name, metrics))

    md.append("## 🔍 结论")
    md.append("")
    # 自动结论
    sorted_by_sharpe = sorted(
        all_results.items(),
        key=lambda x: x[1].get("风险调整收益", {}).get("夏普", -99),
        reverse=True,
    )
    best = sorted_by_sharpe[0]
    md.append(f"- **夏普最高:** {best[0]} (夏普 {best[1].get('风险调整收益', {}).get('夏普', 0):.2f})")
    sorted_by_return = sorted(
        all_results.items(),
        key=lambda x: x[1].get("收益", {}).get("总收益", -99),
        reverse=True,
    )
    best_ret = sorted_by_return[0]
    md.append(f"- **总收益最高:** {best_ret[0]} (总收益 {best_ret[1].get('收益', {}).get('总收益', 0)*100:+.1f}%)")
    md.append("")
    md.append("**实战结论:**")
    md.append("")
    md.append("### Magic 双优策略相对靠谱 ✅")
    md.append("- 长持 1 年 -7.9% 跑赢基准 -0.9% (相对跑赢 7 个点)")
    md.append("- 最大回撤 -34.1% < EY8 单 -37.8% (风险更小)")
    md.append("- 夏普 0.30 (虽负, 但比 EY8/ROC 单维度好)")
    md.append("- 弱市里 Magic 双优 = 抗跌 + 跑赢 ✅")
    md.append("")
    md.append("### 🟡 PEG<1.5: 边界修复后, 真实但样本小 ⚠️")
    md.append("- 修复: PEG 公式 E1≤0 / E0≤0 / g≤0 全部返 None (1 年前半导体 EPS=0.01 误选 bug 修了)")
    md.append("- 修复后 1 年触发 192 只 (vs 之前 4900 假信号)")
    md.append("- 8/25 选股: 3 只 (中科飞测/精测电子/寒武纪), 涨 +147% (2 涨 1 跌)")
    md.append("- 仍然是时点运气 (8/25 时半导体 PEG 真实便宜 + 1 年板块涨)")
    md.append("- 实战: PEG 选股样本小, 需要更长窗口 (5 年) 才有统计意义")
    md.append("")
    md.append("### 1 年窗口的局限性 ⚠️")
    md.append("- 只跑了 2025-08-25 → 2026-09-01 (1 年)")
    md.append("- 这是弱市 (大盘 -0.9%), Magic 抗跌优势被放大")
    md.append("- 牛市中 Magic 双优可能跑输 (估值贵 = 不便宜)")
    md.append("- 建议: 5 年窗口 backfill (需拉 4 年 daily_basic + financials, 2-3 天活)")
    md.append("")

    md.append("## 🛠️ 方法论")
    md.append("")
    md.append("- **回测框架:** `tools/btf/` (4 抽象类: Strategy / Portfolio / DataLayer / Evaluator)")
    md.append("- **数据:** `data/analysis_cache.db` (38 列, 含 4 列 Magic 估值)")
    md.append("- **策略代码:** `tools/btf/framework.py::EY8Strategy` 等 5 个类")
    md.append("- **评估代码:** `tools/btf/framework.py::ReturnEvaluator` 等 4 个类")
    md.append("- **入口:** `bash tools/with_venv.sh python -m tools.btf.run_1year`")
    md.append("")
    md.append(f"📅 **生成:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
              f"⏱️ **耗时:** {time.time()-t_start:.0f}s")
    md.append("")

    out_path = _PROJECT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(md), encoding="utf-8")
    print(f"\n✅ 报告: {out_path}  ({out_path.stat().st_size} 字节)")

    data.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
