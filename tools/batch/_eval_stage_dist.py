"""全池 stage / channel 分布 (读生产 wyckoff_judge 输出, 不反推)

跑法: bash tools/with_venv.sh python -m tools.batch._eval_stage_dist
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import Counter

import pandas as pd

from tools.storage.store import DataStore
from tools.batch.wyckoff_a2m_scan import (
    _filter_by_st, load_codes_all_market,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events


def main():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = DataStore.filter_codes_by_pe(codes, 60.0)
    codes = DataStore.filter_codes_by_quality(codes, or_yoy_min=0, np_yoy_min=0,
                                roe_min=5.0, gm_min=20.0)
    codes = DataStore.filter_codes_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)

    stage_c = Counter()
    ch_c = Counter()
    markup = []
    for idx, code in enumerate(codes):
        try:
            ctx = DataStore.get_ctx(code, kline_only=True)
        except Exception:
            continue
        if not ctx.kline:
            continue
        df = pd.DataFrame(ctx.kline)
        df['date'] = df['trade_date']
        c = df['close'].tolist()
        h = df['high'].tolist()
        l = df['low'].tolist()
        v = df['vol'].tolist()
        o = df['open'].tolist() if 'open' in df.columns else c
        pct = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
        dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
        n = len(c)
        if n < 250:
            continue
        arrs = build_kline_features(c, h, l, v, window=min(250, n))
        subs = scan_sub_events(c, h, l, v, o=o, pct_chg=pct,
                               period_label="daily", code=code, as_of_idx=None,
                               dates=dates_, precomputed_arrs=arrs)
        i = n - 1
        sub_set = {e["name"] for e in subs if e["idx"] <= i}
        r = wyckoff_judge(i, arrs, sub_set, window=arrs['window'],
                          period_label="daily")
        if r is None:
            continue
        st = r.get("stage", "?")
        ch = r.get("channel") or ""
        stage_c[st] += 1
        if st == "Markup":
            ch_c[ch] += 1
            markup.append((code, ch))
        else:
            ch_c[f"accum:{ch}"] += 1
        if idx % 100 == 0:
            print(f"  {idx}/{len(codes)}", flush=True)

    tot = sum(stage_c.values())
    print()
    print("=" * 60)
    print(f"stage 分布 (有效 {tot}):")
    for k, v in stage_c.most_common():
        print(f"  {k:15s} {v:4d}  ({v / tot * 100:.2f}%)")
    print()
    print("Markup channel 分布:")
    mk = {k: v for k, v in ch_c.items() if not k.startswith("accum:")}
    for k, v in sorted(mk.items(), key=lambda x: -x[1]):
        print(f"  {k:30s} {v:4d}")
    print(f"  Markup 合计 {sum(mk.values())}")
    print()
    print("非 Markup channel 分布:")
    for k, v in sorted(((k, v) for k, v in ch_c.items() if k.startswith("accum:")),
                       key=lambda x: -x[1]):
        print(f"  {k:30s} {v:4d}")
    print()
    print("Markup 名单:")
    for code, ch in markup:
        print(f"  {code} {ch}")


if __name__ == "__main__":
    main()
