"""统计全池: Markup 里有多少只**只靠主路径加分线**(dev_ma20>5 + pos_pct>60 + is_uptrend)判出来的
即: 严格 AND 门(强/弱信号)全不命中, fallback1/fallback2 也不命中, 只有加分线 +1 分
"""
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore

from tools.batch.wyckoff_a2m_scan import (
    _load_all_klines, _filter_by_st, load_codes_all_market, load_stock_basic,
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
    all_kl = _load_all_klines(codes, limit_per_code=2000)
    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    cnt = Counter()
    only_addline = []
    markup_all = []
    for code, df in sorted(all_kl.items()):
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
        cnt[out.get("stage")] += 1
        if out.get("stage") != "Markup":
            continue
        markup_all.append(code)
        fv = out.get("factor_values", {})
        md = out.get("markup_detail", {})
        # 严格 AND 门 (强信号 / 弱信号) 是否命中
        strict = bool(md.get("golden_cross_recent") and md.get("above_ma200_sustained")
                      and md.get("ma_gap_pct_above", 0) > 0.5)
        weak = bool(fv.get("above_ma200") and md.get("ma_gap_pct_above", 0) > 0.5)
        # fallback1 4 条件
        slope60 = fv.get("slope_60", 0)
        trend30 = fv.get("trend_30", 0)
        dev_ma200 = fv.get("dev_ma200", 0)
        ma50 = fv.get("ma50", 0)
        ma250 = arrs["ma_window"][i]
        fb1 = bool(slope60 > 0 and dev_ma200 > -15 and ma50 >= ma250 * 0.9 and trend30 > -10)
        # fallback2
        fb2 = bool(len(sub_set) >= 1 and (slope60 > 5 or slope60 < -5) and trend30 > 5)
        addline = bool(fv.get("dev_ma20", 0) > 5 and fv.get("pos_pct", 0) > 60
                       and (slope60 > 0 and dev_ma200 > -10 and trend30 > -15))
        if not strict and not weak and not fb1 and not fb2 and addline:
            only_addline.append((code, name_by_code.get(code, ""), fv.get("dev_ma20"),
                                  fv.get("pos_pct"), slope60, trend30, dev_ma200))

    print(f"\nstage 分布: {dict(cnt)}")
    print(f"Markup 总数: {len(markup_all)}")
    print(f"只靠加分线判 Markup: {len(only_addline)} ({len(only_addline)/max(1,len(markup_all))*100:.1f}%)")
    for r in sorted(only_addline, key=lambda x: -(x[2] or 0))[:30]:
        print(f"  {r[0]} {r[1]} dev_ma20={r[2]} pos={r[3]} slope60={r[4]} tr30={r[5]} dev200={r[6]}")


if __name__ == "__main__":
    main()
