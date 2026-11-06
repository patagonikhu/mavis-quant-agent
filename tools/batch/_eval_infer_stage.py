"""零侵入评估: WTA 结构路径 (identify_trading_range + _infer_stage) 对我们的影响

**不修改任何生产代码**, 只读数据 + 调用 WTA 原生函数, 输出对照表。

评估三件事:
  ① 结构路径单独在 443 只池子上会判哪些票 (含假突破风险评估)
  ② 结构路径 ∩ 趋势条件 (slope_60>0 且 trend_30>5) 会判哪些票 ← 推荐形态
  ③ 跟现有 fallback1 / fallback2 / 主路径 的重叠与差集

跑法: bash tools/with_venv.sh python -m tools.batch._eval_infer_stage
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.factors.wyckoff.helpers import FB1_TREND30_MIN

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
    # 生产默认 --max-net-debt-ratio 0.5 (462 → 443), 必须补上以对齐生产池
    codes = _filter_by_leverage(codes, 0.5)
    return codes


def analyze_one(code, df, cfg):
    """对单只票做完整判定 (含 WTA 结构路径)"""
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
    window = min(250, n)

    arrs = build_kline_features(closes, highs, lows, vols, window=window)
    sub_events_all = scan_sub_events(
        closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
        period_label="daily", code=code, as_of_idx=None, dates=dates_,
        precomputed_arrs=arrs,
    )

    i = n - 1
    slope_60 = arrs['slope_60'][i]
    trend_30 = arrs['trend_30'][i]
    # ⚠️ 严格对齐生产代码 (stage_factor.py L152/L157):
    #   生产 fallback1 的 ma200 变量 = arrs['ma_window'] (window=250 均线), 不是真 MA200!
    #   真 MA200 是 arrs['ma200']。dev_ma200 也用 ma200 (250 均线)。
    ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
    ma200_true = arrs['ma200'][i]
    ma50 = arrs['ma50'][i]
    dev_ma200 = ((closes[i] / ma200 - 1) * 100) if ma200 > 0 else 0.0
    n_sub = len({e["name"] for e in sub_events_all if e["idx"] <= i})
    keys = {e["name"].lower() for e in sub_events_all if e["idx"] <= i}

    # ── 现有判定 (主路径 / fallback1 / fallback2) ──
    # 严格用生产函数 wyckoff_judge 的输出判定, 不复刻逻辑 (避免口径漂移)
    n_tail = max(40, 11)
    stages = []
    for k in range(n_tail):
        j = n - n_tail + k
        sub_set = {e["name"] for e in sub_events_all if e["idx"] <= j}
        out = wyckoff_judge(j, arrs, sub_set, window=arrs['window'], period_label="daily")
        stages.append("?" if out is None else out.get("stage", "?"))

    # 生产代码的 fb2_base 入口 (L381)
    fb2_base = (n_sub >= 1 and (slope_60 > 5 or slope_60 < -5))
    # 生产代码 fallback1 (L385) — ma200 = window(250) 均线
    fb1 = (slope_60 > 0 and dev_ma200 > -15
           and _ma50_reached_ma200(ma50, ma200) and trend_30 > FB1_TREND30_MIN)

    # 判定通道: 用「条件是否成立」反推 (与生产 if/elif 链同序)
    if stages[-1] == "Markup":
        if fb2_base and slope_60 > 5 and trend_30 > 5 and not fb1:
            cur_channel = "fallback2"
        elif fb1:
            cur_channel = "fallback1"
        else:
            cur_channel = "main"
    else:
        cur_channel = stages[-1]

    # V13
    m_window = min(40, len(stages))
    m_count = sum(1 for s in stages[-m_window:] if s == "Markup")
    m_ratio = m_count / m_window
    v13_ok = (stages[-1] == "Markup" and m_ratio < 0.10
              and abs(closes[-1] / closes[-2] - 1) <= 0.20)

    # ── WTA 结构路径 ──
    tr = identify_trading_range(df, cfg)
    struct = None
    tr_info = None
    if tr is not None:
        struct = _infer_stage(df, tr, keys)
        tr_info = {
            "support": tr.support, "resistance": tr.resistance,
            "width_pct": tr.width_pct, "quality": tr.quality_score,
            "sup_tests": tr.support_tests, "res_tests": tr.resistance_tests,
            "breakout": closes[i] >= tr.resistance,
            "gap_pct": (closes[i] / tr.resistance - 1) * 100,
        }

    return {
        "code": code, "close": closes[i], "slope_60": slope_60,
        "trend_30": trend_30, "dev_ma200": dev_ma200,
        "ma50_ma200": (ma50 / ma200 * 100) if ma200 > 0 else 0.0,
        "ma50_ma200_true": (ma50 / ma200_true * 100) if ma200_true > 0 else 0.0,
        "n_sub": n_sub, "cur_channel": cur_channel, "v13_ok": v13_ok,
        "m_ratio": m_ratio, "struct": struct, "tr": tr_info,
        "stages": stages,
    }


def main():
    cfg = FunnelConfig()
    print("加载候选池 ...", flush=True)
    codes = load_candidate_codes()
    print(f"  候选池 {len(codes)} 只", flush=True)

    all_kl = _load_all_klines(codes, limit_per_code=2000)
    print(f"  K 线 {len(all_kl)} 票\n", flush=True)

    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            if isinstance(v, (tuple, list)) and v:
                name_by_code[c] = str(v[0])
            else:
                name_by_code[c] = str(v)

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

    print(f"\n分析完成 {len(rows)} 只\n")

    # ═══ ① 结构路径概览 ═══
    print("=" * 110)
    print("① WTA 结构路径单独判定")
    print("=" * 110)
    has_tr = [r for r in rows if r["tr"]]
    no_tr = [r for r in rows if not r["tr"]]
    struct_markup = [r for r in has_tr if r["struct"] == "Markup"]
    print(f"  能识别出箱体: {len(has_tr)} / {len(rows)}  (无箱体 {len(no_tr)})")
    print(f"  结构路径判 Markup: {len(struct_markup)}")
    from collections import Counter
    print(f"  结构路径分布: {dict(Counter(r['struct'] for r in has_tr))}")

    # 突破幅度 vs 趋势
    print(f"\n  结构判 Markup 的票, 按趋势方向拆分:")
    up = [r for r in struct_markup if r["slope_60"] > 0 and r["trend_30"] > 5]
    mixed = [r for r in struct_markup if r not in up]
    print(f"    趋势双向上 (slope>0 且 trend>5): {len(up)} ← 推荐形态")
    print(f"    其它 (含反弹/下跌中突破):       {len(mixed)} ← 假突破风险")

    print(f"\n  {'代码':8s} {'名称':10s} {'收盘':>8s} {'阻力':>8s} {'突破%':>7s} {'区间%':>6s} "
          f"{'质量':>5s} {'slope60':>8s} {'trend30':>8s} {'通道':>10s}")
    print("-" * 110)
    for r in sorted(struct_markup, key=lambda x: -x["slope_60"])[:30]:
        t = r["tr"]
        flag = "✅" if r in up else "⚠️"
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['close']:8.2f} {t['resistance']:8.2f} "
              f"{t['gap_pct']:+7.2f} {t['width_pct']:6.1f} {t['quality']:5.2f} "
              f"{r['slope_60']:+8.2f} {r['trend_30']:+8.2f} {r['cur_channel']:>10s} {flag}")

    # ═══ ② 结构 ∩ 趋势 ═══
    print("\n" + "=" * 110)
    print("② 结构路径 ∩ 趋势条件 (推荐形态: 突破箱体 AND slope_60>0 AND trend_30>5)")
    print("=" * 110)
    cand = up
    print(f"  候选 {len(cand)} 只")
    print(f"\n  {'代码':8s} {'名称':10s} {'突破%':>7s} {'slope60':>8s} {'trend30':>8s} "
          f"{'ma50/200':>8s} {'m_ratio':>7s} {'现有通道':>10s} {'当前V13':>8s}")
    print("-" * 110)
    for r in sorted(cand, key=lambda x: -x["slope_60"]):
        t = r["tr"]
        v13 = "✅命中" if r["v13_ok"] else "—"
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {t['gap_pct']:+7.2f} "
              f"{r['slope_60']:+8.2f} {r['trend_30']:+8.2f} {r['ma50_ma200']:7.1f}% "
              f"{r['m_ratio']:7.3f} {r['cur_channel']:>10s} {v13:>8s}")

    # ═══ ③ 跟现有判定的差集 ═══
    print("\n" + "=" * 110)
    print("③ 跟现有 fallback 路径的差集")
    print("=" * 110)
    cur_fb = {r["code"] for r in rows if r["cur_channel"] in ("fallback1", "fallback2")}
    struct_codes = {r["code"] for r in struct_markup}
    new_cands = {r["code"] for r in cand}

    print(f"  现有 fallback1/fallback2 判 Markup 的票: {len(cur_fb)}")
    print(f"  结构路径判 Markup 的票:                   {len(struct_codes)}")
    print(f"  结构∩趋势 候选票:                        {len(new_cands)}")
    print(f"\n  ▸ 结构∩趋势 新增 (现有 fallback 没判, 结构路径能认): {len(new_cands - cur_fb)}")
    for r in sorted([r for r in cand if r["code"] in (new_cands - cur_fb)],
                    key=lambda x: -x["slope_60"]):
        print(f"      {r['code']} {r['name'][:10]:10s} 突破{r['tr']['gap_pct']:+.2f}% "
              f"slope{r['slope_60']:+.2f}% trend{r['trend_30']:+.2f}% [{r['cur_channel']}]")
    print(f"\n  ▸ 现有 fallback 判了, 但结构路径不认 (可能被误杀): {len(cur_fb - struct_codes)}")
    for r in sorted([r for r in rows if r["code"] in (cur_fb - struct_codes)],
                    key=lambda x: -x["slope_60"]):
        s = r['struct'] or "无箱体"
        print(f"      {r['code']} {r['name'][:10]:10s} 结构={s:8s} "
              f"slope{r['slope_60']:+.2f}% trend{r['trend_30']:+.2f}%")

    # ═══ ④ 假突破风险量化 ═══
    print("\n" + "=" * 110)
    print("④ 假突破风险: 结构判 Markup 但趋势在跌的票")
    print("=" * 110)
    risky = [r for r in struct_markup if r not in up]
    print(f"  共 {len(risky)} 只 (结构说突破, 但 60日/30日方向不向上)")
    for r in sorted(risky, key=lambda x: x["slope_60"])[:20]:
        t = r["tr"]
        print(f"      {r['code']} {r['name'][:10]:10s} 突破{t['gap_pct']:+6.2f}% "
              f"slope{r['slope_60']:+7.2f}% trend{r['trend_30']:+7.2f}% "
              f"devMA200{r['dev_ma200']:+7.2f}% [{r['cur_channel']}]")


if __name__ == "__main__":
    main()
