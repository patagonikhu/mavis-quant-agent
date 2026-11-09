"""Markup 各判定通道在全池的占比 + 主路径(默认判定)详情

跑法: bash tools/with_venv.sh python -m tools.batch._eval_markup_share
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore
from tools.factors.wyckoff.helpers import FB1_TREND30_MIN

from collections import Counter

from tools.batch.wyckoff_a2m_scan import (
    _load_all_klines, _filter_by_st, load_codes_all_market, load_stock_basic,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
from tools.factors.wyckoff.helpers import _ma50_reached_ma200


def main():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = DataStore.filter_codes_by_pe(codes, 60.0)
    codes = DataStore.filter_codes_by_quality(codes, or_yoy_min=0, np_yoy_min=0,
                              roe_min=5.0, gm_min=20.0)
    codes = DataStore.filter_codes_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)
    all_kl = _load_all_klines(codes, limit_per_code=2000)
    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    stage_cnt = Counter()
    chan_cnt = Counter()
    main_rows = []
    total = 0

    for idx, (code, df) in enumerate(sorted(all_kl.items())):
        if idx % 100 == 0:
            print(f"  ... {idx}/{len(all_kl)}", flush=True)
        if df is None or len(df) < 220:
            continue
        closes = df['close'].tolist()
        highs = df['high'].tolist()
        lows = df['low'].tolist()
        vols = df['vol'].tolist()
        opens = df['open'].tolist() if 'open' in df.columns else closes
        pct_chgs = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
        dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
        n = len(closes)
        arrs = build_kline_features(closes, highs, lows, vols, window=min(250, n))
        subs = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
                               period_label="daily", code=code, as_of_idx=None,
                               dates=dates_, precomputed_arrs=arrs)
        i = n - 1
        sub_set = {e["name"] for e in subs if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_set, window=arrs['window'], period_label="daily")
        if out is None:
            stage_cnt["?"] += 1
            chan_cnt["?"] += 1
            total += 1
            continue
        stage = out.get("stage", "?")
        stage_cnt[stage] += 1
        total += 1

        slope_60 = arrs['slope_60'][i]
        trend_30 = arrs['trend_30'][i]
        ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
        ma50 = arrs['ma50'][i]
        dev_ma200 = ((closes[i] / ma200 - 1) * 100) if ma200 > 0 else 0.0
        n_sub = len(sub_set)
        fb2_base = (n_sub >= 1 and (slope_60 > 5 or slope_60 < -5))
        fb1 = (slope_60 > 0 and dev_ma200 > -15
               and _ma50_reached_ma200(ma50, ma200) and trend_30 > FB1_TREND30_MIN)

        if stage == "Markup":
            if fb1:
                ch = "fallback1"
            elif fb2_base and slope_60 > 5 and trend_30 > 5:
                ch = "fallback2"
            else:
                ch = "main(主路径)"
        else:
            ch = stage
        chan_cnt[ch] += 1

        if ch == "main(主路径)":
            md = out.get("markup_detail", {}) or {}
            def r(v):
                return f"{v:+.2f}%" if isinstance(v, (int, float)) else "n/a"
            main_rows.append({
                "code": code, "name": name_by_code.get(code, code),
                "gc": md.get("golden_cross_recent"), "sus": md.get("above_ma200_sustained"),
                "gap": md.get("ma_gap_pct_above"), "ang": md.get("ma50_angle_5d"),
                "wta": md.get("wta_markup_hit"),
                "slope": slope_60, "trend": trend_30,
                "ratio": ma50 / ma200 * 100 if ma200 > 0 else 0,
                "dev": dev_ma200,
            })

    print(f"\n{'='*100}")
    print(f"判定分布 (全池 {total} 只, 最后一根 K 线)")
    print('='*100)
    print(f"  {'stage':16s} {'只数':>6s} {'占比':>8s}")
    print("-"*100)
    for k, v in stage_cnt.most_common():
        print(f"  {k:16s} {v:6d} {v/total*100:7.2f}%")

    print(f"\n{'='*100}")
    print("Markup 判定通道拆分")
    print('='*100)
    markup_total = sum(v for k, v in chan_cnt.items()
                       if k in ("main(主路径)", "fallback1", "fallback2"))
    print(f"  {'通道':16s} {'只数':>6s} {'占全池':>8s} {'占Markup':>9s}")
    print("-"*100)
    for k in ("main(主路径)", "fallback1", "fallback2"):
        v = chan_cnt.get(k, 0)
        print(f"  {k:16s} {v:6d} {v/total*100:7.2f}% "
              f"{(v/markup_total*100 if markup_total else 0):8.1f}%")
    print(f"  {'--- Markup 合计':16s} {markup_total:6d} {markup_total/total*100:7.2f}%")

    print(f"\n{'='*100}")
    print(f"主路径 (默认判定) 判出的 {len(main_rows)} 只")
    print('='*100)
    if main_rows:
        print(f"  {'代码':8s} {'名称':10s} {'金叉':>5s} {'持续':>5s} {'gap%':>8s} "
              f"{'角度%':>7s} {'WTA':>5s} {'ma50/200':>9s} {'slope60':>8s}")
        print("-"*100)
        for r in main_rows:
            g = lambda b: "✅" if b else "❌"
            gap = f"{r['gap']:+.2f}" if isinstance(r['gap'], (int, float)) else "n/a"
            ang = f"{r['ang']:.2f}" if isinstance(r['ang'], (int, float)) else "n/a"
            print(f"  {r['code']:8s} {r['name'][:10]:10s} {g(r['gc']):>5s} {g(r['sus']):>5s} "
                  f"{gap:>8s} {ang:>7s} {g(r['wta']):>5s} {r['ratio']:8.1f}% {r['slope']:+8.2f}")


if __name__ == "__main__":
    main()
