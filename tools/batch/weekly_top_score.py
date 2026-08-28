"""
tools/batch/weekly_top_score.py - 顶信号周度评分统计

把所有顶部信号按周聚合评分，用于找到逃顶阈值。

信号权重 (v2, 2026-08-01, 已迁到 tools.analysis.analysis_result_signals.TOP_SIGNAL_WEIGHTS):
  ⭐趋势1卖              8分  (bsp 含 1卖⭐)
  普通1卖                5分  (bsp 含 1卖)
  DistributionStart      6分  (威科夫子事件)
  UTAD                   6分  (威科夫子事件)
  EVR                    3分  (威科夫子事件)
  LPSY                   4分  (威科夫子事件 — 最后供给点)
  SOW                    4分  (威科夫子事件 — 弱势信号)
  日线顶背驰新出现        4分  (daily_beichi diff)
  周线顶背驰新出现        6分  (weekly_beichi diff)
  60分顶背驰新出现        3分  (60m_beichi diff)
  跌进中枢(上方→内部)    3分  (hub_daily_pos diff)
  跌出中枢(内部→下方)    5分  (hub_daily_pos diff)
  MA日线 ≥+20% 穿越       2分  (ma_dev_daily diff)
  MA日线 ≥+30% 穿越       4分  (ma_dev_daily diff)

同一天同一类信号只记一次（跨周期合并最高分）。

用法:
  bash tools/with_venv.sh python3 -m tools.batch.weekly_top_score 300604
  bash tools/with_venv.sh python3 -m tools.batch.weekly_top_score 300604 --lookback 120
"""
from __future__ import annotations

from datetime import datetime, timedelta

from tools.analysis.analysis_result_signals import score_top_signals, TOP_SIGNAL_WEIGHTS as _SIGNAL_WEIGHTS

_RATING = [
    (10, "🔴 强逃顶"),
    (5,  "🟠 偏强"),
    (2,  "🟡 观察"),
    (0,  "✅ 安全"),
]


def _rate(score: int) -> str:
    for threshold, label in _RATING:
        if score >= threshold:
            return label
    return "✅ 安全"


def _isoweek(date_str: str) -> str:
    """'20260601' or '2026-06-01' → '2026-W22'"""
    s = date_str.replace("-", "")[:8]
    d = datetime.strptime(s, "%Y%m%d")
    yr, wk, _ = d.isocalendar()
    return f"{yr}-W{wk:02d}"


def _week_range(date_strs: list[str]) -> tuple[str, str]:
    parsed = [datetime.strptime(d.replace("-", "")[:8], "%Y%m%d") for d in date_strs]
    lo, hi = min(parsed), max(parsed)
    return lo.strftime("%m-%d"), hi.strftime("%m-%d")


def compute_weekly_top_score(ctx, lookback: int = 250) -> list[dict]:
    """
    按自然周聚合顶信号评分。

    Returns:
        list[dict], 每条含:
          week        — '2026-W22'
          week_start  — '05-26'
          week_end    — '05-30'
          total_score — int
          rating      — str
          signals     — list[(signal_key, weight, date)]
          days        — list[str] (该周有信号的日期)
    """
    from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows

    rows = compute_factor_history(ctx, step=1, lookback=lookback)
    if not rows:
        return []

    # 按周分组
    weeks: dict[str, dict] = {}
    for i, row in enumerate(rows):
        wk = _isoweek(row["date"])
        if wk not in weeks:
            weeks[wk] = {"dates": [], "signals": []}
        weeks[wk]["dates"].append(row["date"])

        prev = rows[i - 1] if i > 0 else None
        changes = diff_rows(prev, row) if prev else {}
        day = score_top_signals(changes, row, prev)
        for key, weight, _label in day["signals"]:
            weeks[wk]["signals"].append((key, weight, row["date"]))

    result = []
    for wk in sorted(weeks.keys()):
        data = weeks[wk]
        total = sum(w for _, w, _ in data["signals"])
        # 去重：同一周同一类型只算最高分（跨多天可能重复触发）
        best: dict[str, tuple[int, str]] = {}
        for key, weight, date in data["signals"]:
            if key not in best or weight > best[key][0]:
                best[key] = (weight, date)
        deduped_score = sum(w for w, _ in best.values())
        deduped_signals = [(k, w, d) for k, (w, d) in best.items()]

        w_start, w_end = _week_range(data["dates"])
        result.append({
            "week":        wk,
            "week_start":  w_start,
            "week_end":    w_end,
            "total_score": deduped_score,
            "rating":      _rate(deduped_score),
            "signals":     sorted(deduped_signals, key=lambda x: -x[1]),
            "days":        sorted(set(d for _, _, d in data["signals"])),
        })

    return result


def format_table(results: list[dict], code: str = "", name: str = "") -> str:
    """渲染周度评分表格"""
    if not results:
        return "无数据"

    title = f"{code} {name} — 周度顶信号评分".strip()
    start = results[0]["week_start"] if results else "?"
    end   = results[-1]["week_end"]  if results else "?"

    lines = [
        f"**{title}**  ({start} ~ {end})\n",
        "| 周 | 起止 | 总分 | 评级 | 信号详情 |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        sig_str = "  ".join(
            f"{k.split('_',1)[-1]}({w})" for k, w, _ in r["signals"]
        ) or "—"
        lines.append(
            f"| {r['week']} | {r['week_start']}~{r['week_end']} "
            f"| **{r['total_score']}** | {r['rating']} | {sig_str} |"
        )
    return "\n".join(lines)


# ============================================================
# CLI
# ============================================================

def _main():
    import argparse, json, sys
    sys.path.insert(0, ".")

    parser = argparse.ArgumentParser(description="周度顶信号评分统计")
    parser.add_argument("code", help="股票代码，如 300604")
    parser.add_argument("--lookback", type=int, default=250, help="回看交易日数 (默认250≈1年)")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args()

    from tools.kline_store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.render_data import RenderData

    ctx = DataStore.get_ctx(args.code)
    if not ctx.kline:
        print(f"❌ {args.code} 本地无K线，请先运行 history_sync --init")
        sys.exit(1)
    _last = ctx.kline[-1]["trade_date"].replace("-", "")[:8] if ctx.kline else ""
    result = AnalysisEngine().analyze_history(ctx, [_last]).get(_last)
    data = RenderData.from_result(ctx, result)
    name = ctx.name or ""

    print(f"📊 计算 {args.code} {name} 周度顶信号评分 (lookback={args.lookback})...")
    results = compute_weekly_top_score(data.ctx, lookback=args.lookback)

    if args.format == "json":
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(format_table(results, args.code, name))
        # 汇总
        nonzero = [r for r in results if r["total_score"] > 0]
        red     = [r for r in results if r["total_score"] >= 10]
        orange  = [r for r in results if 5 <= r["total_score"] < 10]
        print(f"\n📈 汇总: 共{len(results)}周, "
              f"有信号{len(nonzero)}周, "
              f"🔴强逃顶{len(red)}周, "
              f"🟠偏强{len(orange)}周")
        if red:
            print("🔴 高分周:")
            for r in red:
                sigs = "  ".join(f"{k.split('_',1)[-1]}({w})" for k, w, _ in r["signals"])
                print(f"   {r['week']} ({r['week_start']}~{r['week_end']}) 总分{r['total_score']} | {sigs}")


if __name__ == "__main__":
    _main()
