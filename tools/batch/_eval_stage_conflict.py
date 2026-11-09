"""实测: Accumulation 与 Markup 判定的冲突情况

问题: 两个阶段判定会不会互相打架?

机制 (stage_factor.py L383-386):
    if max(scores.values()) > 0:  stage = max(scores, key=scores.get)   # 取高分者
    elif fallback1/fallback2 ...

所以: 两边都得分时, **分高者赢**。要问的是:
  ① 有多少票两个都得分?
  ② 其中 Markup 赢 / Accumulation 赢 各多少?
  ③ 有多少票 "本该是 Markup 却被 Accumulation 抢走"?
  ④ 若把 Accumulation 的均线胶着改成真 MA200 (WTA 口径), 冲突怎么变?

只读评估。跑法: bash tools/with_venv.sh python -m tools.batch._eval_stage_conflict
"""
import sys
from pathlib import Path

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
from tools.factors.wyckoff.helpers import _ma50_reached_ma200

HOLDINGS = {
    "001219": "青岛食品", "002415": "海康威视", "301101": "明月镜片",
    "601033": "永兴股份", "301109": "军信股份", "605338": "巴比食品",
    "300357": "我武生物", "002683": "广东宏大", "002690": "美亚光电",
    "603387": "基蛋生物", "603606": "东方电缆",
}


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

    rows = []
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
        evs = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
                              period_label="daily", code=code, as_of_idx=None,
                              dates=dates_, precomputed_arrs=arrs)
        i = n - 1
        sub_all = {e["name"] for e in evs if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_all, window=arrs['window'], period_label="daily")
        if out is None:
            continue

        stage = out.get("stage", "?")
        md = out.get("markup_detail", {}) or {}
        ad = out.get("accum_detail", {}) or {}
        fv = out.get("factor_values", {}) or {}

        # 从 markup_detail 反推 Markup 得分 (主路径 3 档)
        score_Markup = 0
        gc = md.get("golden_cross_recent", False)
        sus = md.get("above_ma200_sustained", False)
        gap = fv.get("ma_gap_pct_above", 0)
        up = fv.get("is_uptrend", False)
        if gc and sus and gap > 0.5 and up:
            score_Markup += 3
        elif fv.get("above_ma200", False) and gap > 0.5 and up:
            score_Markup += 1
        if fv.get("dev_ma20", 0) > 5 and fv.get("pos_pct", 0) > 60 and up:
            score_Markup += 1

        # Accumulation 得分 (复刻 stage_factor L268-285)
        ma50 = fv.get("ma50", 0)
        ma200_win = fv.get("ma200", 0)
        ma200_true = fv.get("ma200_true", 0)
        dev_ma200 = fv.get("dev_ma200", 0)
        dev_ma20 = fv.get("dev_ma20", 0)
        pos_pct = fv.get("pos_pct", 50)
        slope_60 = fv.get("slope_60", 0)

        def accum_scores(ma_ref):
            s = 0
            base_ok = ad.get("base_low_ok", False)
            gap_ok = abs(ma50 - ma_ref) / ma_ref * 100 <= 8 if ma_ref > 0 else False
            vol_ok = ad.get("volume_dry_ok", False)
            abc = base_ok and gap_ok and vol_ok
            if abc:
                s += 3
                if any(e in sub_all for e in ('Spring', 'LPS', 'EVR')):
                    s += 2
                if dev_ma20 < 5 and dev_ma60 < 0:
                    s += 1
            elif abs(slope_60) < 8 and 25 < pos_pct < 75 and \
                    any(e in sub_all for e in ('Spring', 'LPS', 'EVR')):
                s += 1
            elif pos_pct < 30 and dev_ma200 < -5 and \
                    any(e in sub_all for e in ('Spring', 'LPS', 'EVR')):
                s += 1
            elif pos_pct < 30 and dev_ma200 < -5 and len(sub_all) >= 1:
                s += 1
            return s

        dev_ma60 = fv.get("dev_ma60", 0)
        rows.append({
            "code": code, "name": name_by_code.get(code, code),
            "stage": stage,
            "s_markup": score_Markup,
            "s_acc_win": accum_scores(ma200_win),
            "s_acc_true": accum_scores(ma200_true),
            "wta_hit": md.get("wta_markup_hit"),
        })

    print(f"\n分析 {len(rows)} 只\n")
    print("=" * 96)
    print("Accumulation vs Markup 得分冲突")
    print("=" * 96)

    both = [r for r in rows if r["s_markup"] > 0 and r["s_acc_win"] > 0]
    both_true = [r for r in rows if r["s_markup"] > 0 and r["s_acc_true"] > 0]
    print(f"  两边都得分 (window250 口径): {len(both)} / {len(rows)}")
    print(f"  两边都得分 (真MA200 口径):   {len(both_true)} / {len(rows)}")

    if both:
        acc_win = [r for r in both if r["s_acc_win"] > r["s_markup"]]
        m_win = [r for r in both if r["s_markup"] > r["s_acc_win"]]
        tie = [r for r in both if r["s_markup"] == r["s_acc_win"]]
        print(f"\n  其中 Accumulation 分更高 (Markup 被抢走): {len(acc_win)}")
        print(f"  其中 Markup 分更高:                      {len(m_win)}")
        print(f"  打平 (max() 取字典序第一个 → Accumulation): {len(tie)}")

        print(f"\n  {'代码':8s} {'名称':10s} {'Markup分':>8s} {'Accum分':>8s} {'最终stage':>12s}")
        print("  " + "-" * 92)
        for r in sorted(both, key=lambda x: -(x["s_acc_win"] - x["s_markup"])):
            mark = "← 被抢" if r["s_acc_win"] > r["s_markup"] else ""
            print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['s_markup']:8d} "
                  f"{r['s_acc_win']:8d} {r['stage']:>12s} {mark}")

    # 换真 MA200 后冲突变化
    print(f"\n{'='*96}")
    print("若 Accumulation 均线胶着改用真 MA200 (WTA 口径)")
    print("=" * 96)
    print(f"  两边都得分: {len(both)} → {len(both_true)}")
    acc_win_t = [r for r in both_true if r["s_acc_true"] > r["s_markup"]]
    print(f"  Accumulation 抢走 Markup: {len([r for r in both if r['s_acc_win']>r['s_markup']])} → {len(acc_win_t)}")
    changed = [r for r in rows if (r["s_acc_win"] > 0) != (r["s_acc_true"] > 0)]
    print(f"\n  Accumulation 得分有无变化的票: {len(changed)}")
    for r in sorted(changed, key=lambda x: x["code"])[:20]:
        d = r["s_acc_true"] - r["s_acc_win"]
        print(f"    {r['code']} {r['name'][:10]:10s} accum {r['s_acc_win']}→{r['s_acc_true']} "
              f"({d:+d})  Markup分={r['s_markup']}  stage={r['stage']}")

    # 持仓
    print(f"\n{'='*96}")
    print("持仓 11 只")
    print("=" * 96)
    print(f"  {'代码':8s} {'名称':8s} {'Markup分':>8s} {'Accum(250)':>11s} {'Accum(200)':>11s} {'stage':>12s}")
    for code, name in HOLDINGS.items():
        r = next((x for x in rows if x["code"] == code), None)
        if r is None:
            print(f"  {code:8s} {name:8s}  数据不足")
            continue
        flag = " ⚠️" if r["s_markup"] > 0 and r["s_acc_win"] > 0 else ""
        print(f"  {code:8s} {name:8s} {r['s_markup']:8d} {r['s_acc_win']:11d} "
              f"{r['s_acc_true']:11d} {r['stage']:>12s}{flag}")


if __name__ == "__main__":
    main()
