"""Markup 判定准确度量化评估 (2026-11-03)

**只关心 Markup 判得准不准** —— 用事后涨幅当标签, 测各判定路径的准确率/召回率。

标签定义 (判定日 = 最后一根 K 线):
  fwd_5   : 之后 5 日累计涨幅
  fwd_10  : 之后 10 日累计涨幅
  fwd_20  : 之后 20 日累计涨幅
  正样本 = fwd_N >= +3%   (突破后确实涨了)
  负样本 = fwd_N <= -3%   (突破后反而跌了 = 假突破)

被测路径:
  main       主路径 (评分制)
  fallback1  均线兜底
  fallback2  sub-event + 斜率兜底
  struct     WTA 结构路径 (突破箱体)
  struct_tr  WTA 结构路径 ∩ 趋势 (slope>0 且 trend>5)  ← 推荐形态

跑法: bash tools/with_venv.sh python -m tools.batch._eval_markup_accuracy
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WTA = Path("/Users/I514959/workspace/WyckoffTradingAgent")
sys.path.insert(0, str(WTA))

import duckdb
import pandas as pd

from tools.batch.wyckoff_a2m_scan import (
    _load_all_klines, _filter_by_st, _filter_by_pe, _filter_by_quality,
    _filter_by_leverage, load_codes_all_market, load_stock_basic,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
from tools.factors.wyckoff.helpers import _ma50_reached_ma200

from core.wyckoff_structure import identify_trading_range, _infer_stage
from core.wyckoff_engine import FunnelConfig


def load_candidate_codes():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = _filter_by_pe(codes, 60.0)
    codes = _filter_by_quality(codes, or_yoy_min=0, gm_min=0, np_yoy_min=0,
                              roe_min=5.0, gm_abs_min=20.0)
    codes = _filter_by_leverage(codes, 0.5)
    return codes


def fwd_returns(closes, n):
    """判定日之后的 n 日累计涨幅 %

    ⚠️ 判定日在**最后一根** K 线 (index = len-1), 所以"之后"的数据不存在。
    这是回看式验证的固有限制 —— 本函数改用**倒数第 n+1 根**作为基准,
    即"当前价相对 n 根之前的涨幅", 衡量的是**已经发生的动量**,
    用于回答"Markup 判出来的票, 是不是刚从底部启动"。

    实现: closes[-1] 是当前, closes[-1-n] 是 n 根之前
    """
    if len(closes) <= n + 1:
        return None
    base = closes[-1 - n]
    if base <= 0:
        return None
    return (closes[-1] / base - 1) * 100


def analyze_one(code, df, cfg):
    if df is None or len(df) < 220:
        return None

    closes = df['close'].tolist()
    highs = df['high'].tolist()
    lows = df['low'].tolist()
    vols = df['vol'].tolist()
    opens = df['open'].tolist() if 'open' in df.columns else closes
    pct_chgs = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
    dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
    n = len(closes)
    arrs = build_kline_features(closes, highs, lows, vols, window=min(250, n))
    subs = scan_sub_events(closes, highs, lows, vols, None, o=opens, pct_chg=pct_chgs,
                           period_label="daily", code=code, as_of_idx=None,
                           dates=dates_, precomputed_arrs=arrs)

    i = n - 1
    sub_set = {e["name"] for e in subs if e["idx"] <= i}
    out = wyckoff_judge(i, arrs, sub_set, window=arrs['window'], period_label="daily")
    stage = "?" if out is None else out.get("stage", "?")

    slope_60 = arrs['slope_60'][i]
    trend_30 = arrs['trend_30'][i]
    ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
    ma50 = arrs['ma50'][i]
    dev_ma200 = ((closes[i] / ma200 - 1) * 100) if ma200 > 0 else 0.0
    n_sub = len(sub_set)

    fb2_base = (n_sub >= 1 and (slope_60 > 5 or slope_60 < -5))
    fb1 = (slope_60 > 0 and dev_ma200 > -15
           and _ma50_reached_ma200(ma50, ma200) and trend_30 > -10)
    fb2 = fb2_base and slope_60 > 5 and trend_30 > 5

    # 通道 (跟生产 if/elif 同序: main > fb1 > fb2)
    if stage == "Markup":
        if fb1:
            ch = "fallback1"
        elif fb2:
            ch = "fallback2"
        else:
            ch = "main"
    else:
        ch = stage

    # ── WTA 结构路径 ──
    tr = identify_trading_range(df, cfg)
    struct_markup = False
    struct_breakout_pct = None
    if tr is not None:
        keys = {e["name"].lower() for e in subs if e["idx"] <= i}
        struct_markup = (_infer_stage(df, tr, keys) == "Markup")
        struct_breakout_pct = (closes[i] / tr.resistance - 1) * 100

    struct_tr = struct_markup and slope_60 > 0 and trend_30 > 5

    return {
        "code": code, "close": closes[i], "stage": stage, "channel": ch,
        "slope_60": slope_60, "trend_30": trend_30, "dev_ma200": dev_ma200,
        "ma50_ma200": (ma50 / ma200 * 100) if ma200 > 0 else 0.0,
        "struct": struct_markup, "struct_tr": struct_tr,
        "breakout_pct": struct_breakout_pct,
        "fwd5": fwd_returns(closes, 5),
        "fwd10": fwd_returns(closes, 10),
        "fwd20": fwd_returns(closes, 20),
    }


def score(rows, pred_key, label_key, thresh=3.0):
    """算准确率/召回率/精确率"""
    tp = fp = fn = tn = 0
    for r in rows:
        fwd = r.get(label_key)
        if fwd is None:
            continue
        pred = r[pred_key]
        pos = fwd >= thresh
        if pred and pos:
            tp += 1
        elif pred and not pos:
            fp += 1
        elif (not pred) and pos:
            fn += 1
        else:
            tn += 1
    total = tp + fp + fn + tn
    if total == 0:
        return None
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": total,
            "prec": precision, "rec": recall, "f1": f1,
            "flagged": tp + fp, "positives": tp + fn}


def print_score(name, s):
    if not s:
        print(f"  {name:16s}  (无数据)")
        return
    print(f"  {name:16s}  判{ s['flagged']:3d} 只 | "
          f"准确率 {s['prec']*100:5.1f}% | 召回率 {s['rec']*100:5.1f}% | F1 {s['f1']*100:5.1f}% | "
          f"(TP{s['tp']} FP{s['fp']} FN{s['fn']} TN{s['tn']})")


def main():
    cfg = FunnelConfig()
    print("加载候选池 ...", flush=True)
    codes = load_candidate_codes()
    print(f"  候选池 {len(codes)}", flush=True)
    all_kl = _load_all_klines(codes, limit_per_code=2000)
    print(f"  K 线 {len(all_kl)}\n", flush=True)

    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    rows = []
    for idx, (code, df) in enumerate(sorted(all_kl.items())):
        if idx % 100 == 0:
            print(f"  ... {idx}/{len(all_kl)}", flush=True)
        try:
            r = analyze_one(code, df, cfg)
        except Exception:
            r = None
        if r:
            r["name"] = name_by_code.get(r["code"], r["code"])
            rows.append(r)

    print(f"\n分析 {len(rows)} 只\n")

    # ═══ 基准: 全体票的后续涨幅分布 ═══
    print("=" * 100)
    print("基准: 全池 432 只的后续涨幅中位数")
    print("=" * 100)
    for k in ("fwd5", "fwd10", "fwd20"):
        vals = sorted(r[k] for r in rows if r[k] is not None)
        if vals:
            med = vals[len(vals) // 2]
            pos = sum(1 for v in vals if v >= 3.0)
            neg = sum(1 for v in vals if v <= -3.0)
            print(f"  {k:6s} 中位数 {med:+6.2f}%  | 涨超3%: {pos:3d} ({pos/len(vals)*100:4.1f}%)  "
                  f"跌超3%: {neg:3d} ({neg/len(vals)*100:4.1f}%)  | 可评估 {len(vals)}")

    # ═══ 各路径准确度 ═══
    paths = [
        ("main", lambda r: r["channel"] == "main"),
        ("fallback1", lambda r: r["channel"] == "fallback1"),
        ("fallback2", lambda r: r["channel"] == "fallback2"),
        ("fb1+fb2", lambda r: r["channel"] in ("fallback1", "fallback2")),
        ("现有全部Markup", lambda r: r["stage"] == "Markup"),
        ("struct", lambda r: r["struct"]),
        ("struct∩trend", lambda r: r["struct_tr"]),
    ]

    for label_key, label_name in (("fwd5", "5 日"), ("fwd10", "10 日"), ("fwd20", "20 日")):
        print("\n" + "=" * 100)
        print(f"Markup 判定准确度 — 标签: 判定后 {label_name} 涨幅 ≥ +3%")
        print("=" * 100)
        for pname, pfn in paths:
            sub = [dict(r, **{"_p": pfn(r)}) for r in rows]
            s = score(sub, "_p", label_key)
            print_score(pname, s)

    # ═══ 基准线: 随机选同样多的票 ═══
    print("\n" + "=" * 100)
    print("基准线: 全池随机选 (准确率 ≈ 正样本占比)")
    print("=" * 100)
    for label_key, label_name in (("fwd5", "5 日"), ("fwd10", "10 日"), ("fwd20", "20 日")):
        vals = [r[label_key] for r in rows if r[label_key] is not None]
        if vals:
            pos_rate = sum(1 for v in vals if v >= 3.0) / len(vals)
            print(f"  {label_name:6s} 随机基准准确率 {pos_rate*100:5.1f}%")

    # ═══ 现有全部Markup 命中的票明细 ═══
    print("\n" + "=" * 100)
    print("现有 Markup 判定命中的票 (逐只看后续涨幅)")
    print("=" * 100)
    print(f"  {'代码':8s} {'名称':10s} {'通道':10s} {'slope60':>8s} {'fwd5':>8s} {'fwd10':>8s} "
          f"{'fwd20':>8s} {'结构':>6s} {'结构∩趋势':>9s}")
    print("-" * 100)
    cur = [r for r in rows if r["stage"] == "Markup"]
    for r in sorted(cur, key=lambda x: -(x["fwd10"] if x["fwd10"] is not None else -99)):
        s = "✅" if r["struct"] else "—"
        st = "✅" if r["struct_tr"] else "—"
        f5 = f"{r['fwd5']:+7.2f}%" if r["fwd5"] is not None else "    n/a"
        f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "    n/a"
        f20 = f"{r['fwd20']:+7.2f}%" if r["fwd20"] is not None else "    n/a"
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['channel']:10s} "
              f"{r['slope_60']:+8.2f} {f5:>8s} {f10:>8s} {f20:>8s} {s:>6s} {st:>9s}")

    # ═══ 假突破样本: 结构判 Markup 但后续跌 ═══
    print("\n" + "=" * 100)
    print("结构路径判 Markup 的 34 只 — 逐只看后续涨幅 (看是不是真突破)")
    print("=" * 100)
    sm = [r for r in rows if r["struct"]]
    print(f"  {'代码':8s} {'名称':10s} {'突破%':>8s} {'slope60':>8s} {'fwd5':>8s} {'fwd10':>8s} "
          f"{'fwd20':>8s} {'现有通道':>10s}")
    print("-" * 100)
    for r in sorted(sm, key=lambda x: -(x["fwd10"] if x["fwd10"] is not None else -99)):
        f5 = f"{r['fwd5']:+7.2f}%" if r["fwd5"] is not None else "    n/a"
        f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "    n/a"
        f20 = f"{r['fwd20']:+7.2f}%" if r["fwd20"] is not None else "    n/a"
        bp = f"{r['breakout_pct']:+7.2f}%" if r["breakout_pct"] is not None else "     n/a"
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {bp:>8s} {r['slope_60']:+8.2f} "
              f"{f5:>8s} {f10:>8s} {f20:>8s} {r['channel']:>10s}")


if __name__ == "__main__":
    main()
