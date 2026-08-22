#!/usr/bin/env python3
"""
backtest_top_signals.py — UTAD 和 缠论 ⭐trend 顶信号回测

遍历全部 dump 文件，对每只票跑全量历史（step=1），统计：
  UTAD 触发后 N 天胜率（precision）和召回率（recall）
  ⭐trend 顶触发后 N 天胜率

用法:
  bash tools/with_venv.sh python -m tools.backtest_top_signals
  bash tools/with_venv.sh python -m tools.backtest_top_signals --forward 20 --threshold 3
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DUMP_DIR = ROOT / "data" / "dump"


def utad_signal(row: dict) -> bool:
    sub = row.get("sub_event_daily") or ""
    return "UTAD" in sub


def trend_top_signal(row: dict) -> bool:
    bc = row.get("daily_beichi")
    if not isinstance(bc, dict):
        return False
    return bc.get("direction") == "top" and bc.get("bc_type") == "trend"


def normal_top_signal(row: dict) -> bool:
    bc = row.get("daily_beichi")
    if not isinstance(bc, dict):
        return False
    return bc.get("direction") == "top" and bc.get("bc_type") == "normal"


def consolidation_top_signal(row: dict) -> bool:
    bc = row.get("daily_beichi")
    if not isinstance(bc, dict):
        return False
    return bc.get("direction") == "top" and bc.get("bc_type") == "consolidation"


def spring_signal(row: dict) -> bool:
    sub = row.get("sub_event_daily") or ""
    return "Spring" in sub


def lps_signal(row: dict) -> bool:
    sub = row.get("sub_event_daily") or ""
    return "LPS" in sub


# 买卖点信号: bsp_daily[key] 非空且非 "—"
def _bsp(row: dict, key: str) -> bool:
    bsp = row.get("bsp_daily") or {}
    v = bsp.get(key, "—")
    return bool(v) and v != "—"


def trend_bot_signal(row: dict) -> bool:
    bc = row.get("daily_beichi")
    if not isinstance(bc, dict):
        return False
    return bc.get("direction") == "bot" and bc.get("bc_type") == "trend"


def normal_bot_signal(row: dict) -> bool:
    bc = row.get("daily_beichi")
    if not isinstance(bc, dict):
        return False
    return bc.get("direction") == "bot" and bc.get("bc_type") == "normal"


def consolidation_bot_signal(row: dict) -> bool:
    bc = row.get("daily_beichi")
    if not isinstance(bc, dict):
        return False
    return bc.get("direction") == "bot" and bc.get("bc_type") == "consolidation"


TOP_SIGNALS = {
    "UTAD":               (utad_signal,              "top"),
    "⭐trend 顶":          (trend_top_signal,          "top"),
    "🔵normal 顶":         (normal_top_signal,         "top"),
    "🟡consolidation 顶":  (consolidation_top_signal,  "top"),
}

BOT_SIGNALS = {
    "Spring":             (spring_signal,                           "bot"),
    "LPS":                (lps_signal,                             "bot"),
    "⭐trend 底":          (trend_bot_signal,                       "bot"),
    "🔵normal 底":         (normal_bot_signal,                      "bot"),
    "🟡consolidation 底":  (consolidation_bot_signal,               "bot"),
    "🟢0买":               (lambda r: _bsp(r, "🟢0买"),             "bot"),
    "🟢1买":               (lambda r: _bsp(r, "🟢1买"),             "bot"),
    "🟢1买⭐":             (lambda r: _bsp(r, "🟢1买⭐"),            "bot"),
    "🟢2买":               (lambda r: _bsp(r, "🟢2买"),             "bot"),
    "🟢3买":               (lambda r: _bsp(r, "🟢3买"),             "bot"),
}

SELL_SIGNALS = {
    "🔴1卖":               (lambda r: _bsp(r, "🔴1卖"),             "bot"),
    "🔴1卖⭐":             (lambda r: _bsp(r, "🔴1卖⭐"),            "bot"),
    "🔴2卖":               (lambda r: _bsp(r, "🔴2卖"),             "bot"),
    "🔴3卖":               (lambda r: _bsp(r, "🔴3卖"),             "bot"),
}

SIGNALS = {**TOP_SIGNALS, **BOT_SIGNALS, **SELL_SIGNALS}


def run(forward_days: int, threshold_pct: float) -> None:
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.factor_history import compute_factor_history, backtest_signal
    from tools.data_store import DataStore

    codes = DataStore.watchlist_codes() or DataStore.list_codes()
    print(f"扫描 {len(codes)} 只股票 | forward={forward_days}d | threshold={threshold_pct}%\n")

    totals: dict[str, dict] = {k: {"tp": 0, "fp": 0, "fn": 0} for k in SIGNALS}
    engine = AnalysisEngine()

    for code in codes:
        try:
            ctx = DataStore.get_ctx(code)
            if len(ctx.kline) < forward_days + 10:
                continue
            # lookback 用全量 K 线长度
            lookback = len(ctx.kline)
            rows = compute_factor_history(ctx, step=1, lookback=lookback)
            for sig_name, (sig_fn, direction) in SIGNALS.items():
                result = backtest_signal(
                    rows,
                    signal_fn=sig_fn,
                    direction=direction,
                    forward_days=forward_days,
                    threshold_pct=threshold_pct,
                )
                totals[sig_name]["tp"] += len(result["tp"])
                totals[sig_name]["fp"] += len(result["fp"])
                totals[sig_name]["fn"] += len(result["fn"])
        except Exception as e:
            print(f"  ⚠️ {code} 跳过: {e}")
            continue
        print(f"  ✓ {code} ({len(ctx.kline)} bars)", end="\r")

    print("\n")
    print(f"  {'信号':<22} {'触发':>8} {'胜率':>10} {'召回率':>8} {'TP':>6} {'FP':>6} {'FN':>6}")
    print("  " + "-" * 72)
    groups = [
        ("背驰顶 + 威科夫顶", TOP_SIGNALS),
        ("买卖点卖出", SELL_SIGNALS),
        ("背驰底 + 威科夫底", {k: v for k, v in BOT_SIGNALS.items() if not k.startswith("🟢")}),
        ("买卖点买入", {k: v for k, v in BOT_SIGNALS.items() if k.startswith("🟢")}),
    ]
    for group_label, group_signals in groups:
        print(f"\n  ── {group_label} (threshold={threshold_pct}%) ──")
        for sig_name in group_signals:
            t = totals[sig_name]
            triggered = t["tp"] + t["fp"]
            precision = t["tp"] / triggered if triggered else 0.0
            recall    = t["tp"] / (t["tp"] + t["fn"]) if (t["tp"] + t["fn"]) else 0.0
            print(
                f"  {sig_name:<22} {triggered:>8} {precision*100:>9.1f}%"
                f" {recall*100:>7.1f}%"
                f" {t['tp']:>6} {t['fp']:>6} {t['fn']:>6}"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UTAD / 缠论顶信号回测")
    parser.add_argument("--forward",   type=int,   default=20,  help="后验窗口天数 (默认 20)")
    parser.add_argument("--threshold", type=float, default=3.0, help="判定'跌'的幅度阈值 %% (默认 3.0)")
    args = parser.parse_args()
    run(args.forward, args.threshold)
