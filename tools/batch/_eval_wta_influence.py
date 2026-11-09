"""量化 WTA (detectors/markup_entry.py) 对 stage 判定的实际影响

背景: WTA Layer 2.5 的 4 条判据在代码里有**两层身份**:
  1. stage_factor.detect_markup_entry()  → 只输出 markup_detail.wta_* 供报告展示 (不参与判定)
  2. detectors/markup_entry.detect_markup_entry() → 产出 'MarkupEntry' sub-event
     → sub_event 集合进 wyckoff_judge
     → stage_factor.py:275  accum_weak_C_ok 条件是 `len(sub_events) >= 1` (不区分事件名!)
     → stage_factor.py:400  fb2_base          条件是 `len(sub_events) >= 1` (不区分事件名!)
     → 即: **MarkupEntry 单独就能撑起弱信号 C 和 fallback2**

方法: 生产口径 K 线 (DataStore.get_ctx), 对每只票跑两遍 wyckoff_judge:
  A) 完整 sub_set
  B) 摘掉 'MarkupEntry' 后的 sub_set
比较 stage / channel 差异。

跑法: bash tools/with_venv.sh python -m tools.batch._eval_wta_influence
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
    _filter_by_st, load_codes_all_market, load_stock_basic,
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

    all_kl = {}
    for idx, c in enumerate(codes):
        try:
            ctx = DataStore.get_ctx(c, kline_only=True)
        except Exception:
            continue
        if not ctx.kline:
            continue
        df = pd.DataFrame(ctx.kline)
        df['date'] = df['trade_date']
        all_kl[c] = df
        if idx % 100 == 0:
            print(f"  loading {idx}/{len(codes)}", flush=True)
    print(f"载入 K 线 {len(all_kl)} 只 (生产口径)", flush=True)

    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    has_me = 0
    total = 0
    changed = []          # 摘掉 MarkupEntry 后判定变了
    me_only = []          # 唯一 sub_event 就是 MarkupEntry
    stage_cnt = Counter()

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
            continue
        total += 1
        stage_cnt[out.get("stage", "?")] += 1

        if "MarkupEntry" not in sub_set:
            continue
        has_me += 1
        nm = name_by_code.get(code, "")

        # 摘掉 MarkupEntry 再跑一遍
        sub_set2 = sub_set - {"MarkupEntry"}
        out2 = wyckoff_judge(i, arrs, sub_set2, window=arrs['window'], period_label="daily")
        s1, c1 = out.get("stage", "?"), out.get("channel", "")
        s2, c2 = (out2.get("stage", "?"), out2.get("channel", "")) if out2 else ("?", "")

        rec = (code, nm, s1, c1, s2, c2, sorted(sub_set))
        if s1 != s2 or c1 != c2:
            changed.append(rec)
        if len(sub_set) == 1:
            me_only.append(rec)

    print("\n" + "=" * 78)
    print(f"有效判定 {total} 只")
    print(f"含 MarkupEntry sub-event: {has_me} 只 ({has_me/max(total,1)*100:.1f}%)")
    print(f"stage 分布: {dict(stage_cnt)}")
    print(f"摘掉 MarkupEntry 后判定改变: {len(changed)} 只 ({len(changed)/max(total,1)*100:.1f}%)")
    print(f"唯一 sub-event 就是 MarkupEntry: {len(me_only)} 只")

    if changed:
        print("\n--- 判定受 MarkupEntry 影响的票 ---")
        print(f"{'代码':<8}{'名称':<12}{'原 stage':<14}{'原 channel':<22}{'摘后 stage':<14}{'摘后 channel'}")
        for code, nm, s1, c1, s2, c2, subs in changed:
            print(f"{code:<8}{nm:<12}{s1:<14}{c1:<22}{s2:<14}{c2}")
        print("\n--- 各票的 sub_event 集合 ---")
        for code, nm, s1, c1, s2, c2, subs in changed:
            print(f"  {code} {nm}: {subs}")

    if me_only:
        print("\n--- 唯一 sub-event = MarkupEntry 的票 ---")
        for code, nm, s1, c1, s2, c2, subs in me_only:
            print(f"  {code} {nm}: 原 {s1}/{c1} → 摘后 {s2}/{c2}")


if __name__ == "__main__":
    main()
