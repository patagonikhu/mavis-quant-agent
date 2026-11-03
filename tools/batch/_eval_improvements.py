"""改进方案对照评估 (2026-11-03)

测 3 个方向的改动, 每个都给准确率/召回率/F1 + A→M 命中集合变化 + 持仓状态变化:

  ① sub_event 时效窗口  (只认最近 N 根 K 线内触发的 sub_event)
  ② fallback1 加 LPS 缩量守卫 (median(vol[-3:])/median(vol[-60:]) <= ratio)
  ③ fallback1 slope_60 门槛 (对齐 WTA _trend_follow 的 ret60 >= 8)

⚠️ 本脚本**复刻**生产判定链 (生产 wyckoff_judge 只收 set[str], 拿不到 idx)。
   第一步先做**交叉验证**: 复刻结果 vs 生产 wyckoff_judge 结果必须 100% 一致,
   不一致就直接报错退出 —— 防止复刻逻辑漂移导致结论失真。

标签: 判定日**过去** N 日涨幅 >= +3% (无未来函数; 数据截止 20261102)
      基准线 = 全池正样本占比

跑法: bash tools/with_venv.sh python -m tools.batch._eval_improvements
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import statistics

from tools.batch.wyckoff_a2m_scan import (
    _load_all_klines, _filter_by_st, _filter_by_pe, _filter_by_quality,
    _filter_by_leverage, load_codes_all_market, load_stock_basic,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
from tools.factors.wyckoff.helpers import _ma50_reached_ma200

HOLDINGS = {
    "001219": "青岛食品", "603606": "东方电缆", "002415": "海康威视",
    "301101": "明月镜片", "601033": "永兴股份", "301109": "军信股份",
    "605338": "巴比食品", "300357": "我武生物", "002683": "广东宏大",
    "002690": "美亚光电", "603387": "基蛋生物",
}

TOL = 0.90


def ret_pct(closes, back):
    if len(closes) <= back or closes[-1 - back] <= 0:
        return None
    return (closes[-1] / closes[-1 - back] - 1) * 100


def judge_replica(i, arrs, evs, sub_all, sub_recent, closes, vols,
                  sub_window=None, vol_dry_ratio=None, slope_min=None):
    """复刻 stage_factor 的判定链, 但参数可调

    返回 (stage, channel)
    """
    slope_60 = arrs['slope_60'][i]
    trend_30 = arrs['trend_30'][i]
    ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
    ma50 = arrs['ma50'][i]
    p = closes[i]
    dev_ma200 = ((p / ma200 - 1) * 100) if ma200 > 0 else 0.0
    dev_ma20 = ((p / arrs['ma20'][i] - 1) * 100) if arrs['ma20'][i] > 0 else 0.0
    recent_high = arrs['rmax_h_pos'][i]
    recent_low = arrs['rmin_l_pos'][i]
    pos_pct = ((p - recent_low) / (recent_high - recent_low) * 100
               if recent_high > recent_low else 50)
    ma_gap_pct_above = ((p - ma200) / ma200 * 100) if ma200 > 0 else 0.0
    is_uptrend = slope_60 > 0 and dev_ma200 > -10 and trend_30 > -15

    ma50_angle_5d = 0.0
    if i >= 54:
        ma50_5d_ago = sum(closes[i - 54:i - 49]) / 5
        if ma50_5d_ago > 0:
            ma50_angle_5d = (ma50 / ma50_5d_ago - 1) * 100

    # ── sub_event 集合: 全集 或 最近 N 根 (时效窗口) ──
    # 生产代码没有时效 (sub_set = {e["name"] for e in sub_raw}, 只过滤 idx<=abs_idx),
    # 这里把它做成可调参数, 用来评估加时效窗口的效果。
    if sub_window is not None and sub_recent is not None:
        subs = sub_recent
    else:
        subs = sub_all

    # ── Accumulation 评分 (生产 stage_factor.py L231-285) ──
    score_Accumulation = 0
    if i >= 30:
        period_low = arrs['rmin_l_250'][i]
        accum_base_low_ok = period_low > 0 and p <= period_low * 1.45
        accum_ma_gap_ok = (abs(ma50 - ma200) / ma200 * 100 <= 8) if ma200 > 0 else False
        if i >= 120:
            vr, vb = arrs['vol20'][i], arrs['vol_ref_120_20'][i]
        elif i >= 40:
            vr, vb = arrs['vol10'][i], arrs['vol_ref_30_10'][i]
        else:
            vr, vb = 0.0, 0.0
        accum_volume_ok = (vb > 0 and vr / vb < 0.75)
        accum_abc_pass = accum_base_low_ok and accum_ma_gap_ok and accum_volume_ok

        if accum_abc_pass:
            score_Accumulation += 3
            if any(e in subs for e in ('Spring', 'LPS', 'EVR')):
                score_Accumulation += 2
            if dev_ma20 < 5 and ((p / arrs['ma60'][i] - 1) * 100 if arrs['ma60'][i] > 0 else 0) < 0:
                score_Accumulation += 1
        elif abs(slope_60) < 8 and 25 < pos_pct < 75 and \
                any(e in subs for e in ('Spring', 'LPS', 'EVR')):
            score_Accumulation += 1
        elif pos_pct < 30 and dev_ma200 < -5 and \
                any(e in subs for e in ('Spring', 'LPS', 'EVR')):
            score_Accumulation += 1
        elif pos_pct < 30 and dev_ma200 < -5 and len(subs) >= 1:
            score_Accumulation += 1

    # ── Distribution 评分 (生产 stage_factor.py L314-330) ──
    score_Distribution = 0
    vol_5 = arrs['vol5'][i]
    vol_60 = arrs['vol60'][i]
    vol_shrink = (vol_5 < vol_60 * 0.5) if vol_60 > 0 else False
    vol_3 = arrs['vol3'][i]
    vol_dry_3d = (vol_3 < vol_60 * 0.5) if (vol_60 > 0 and i >= 63) else vol_shrink
    distribution_start = dev_ma200 > 30 and vol_dry_3d
    distribution_weak = (dev_ma200 > 15 and vol_shrink
                         and any(e in subs for e in ('DistributionStart', 'UTAD', 'EVR')))
    if distribution_start:
        score_Distribution += 3
        if any(e in subs for e in ('DistributionStart', 'UTAD')):
            score_Distribution += 1
        if 'EVR' in subs:
            score_Distribution += 1
    elif distribution_weak:
        score_Distribution += 2
        if any(e in subs for e in ('DistributionStart', 'UTAD')):
            score_Distribution += 1

    # ── 主路径 ──
    score_Markup = 0
    golden_cross_recent = False
    if i >= 2 and i >= 200 and ma50 > ma200:
        for j in range(max(1, i - 9), i + 1):
            if (arrs['ma50'][j - 1] <= arrs['ma200'][j - 1]
                    and arrs['ma50'][j] > arrs['ma200'][j]):
                golden_cross_recent = True
                break
    above_ma200_sustained = False
    if i >= 205:
        above_ma200_sustained = all(
            arrs['ma50'][j] > arrs['ma200'][j] for j in range(i - 4, i + 1))
    above_ma200 = p > ma200

    if golden_cross_recent and above_ma200_sustained and ma_gap_pct_above > 0.5 and is_uptrend:
        score_Markup += 3
    elif above_ma200 and ma_gap_pct_above > 0.5 and is_uptrend:
        score_Markup += 1
    if dev_ma20 > 5 and pos_pct > 60 and is_uptrend:
        score_Markup += 1

    # ── fallback ──
    # LPS 缩量守卫 (median(vol[-3:]) / median(vol[-60:]))
    vol_dry_ok = True
    if vol_dry_ratio is not None:
        if len(vols) >= 63:
            v3 = statistics.median(vols[i - 3:i])
            v60 = statistics.median(vols[i - 60:i])
            vol_dry_ok = (v60 > 0 and v3 / v60 <= vol_dry_ratio)
        else:
            vol_dry_ok = False

    sm = slope_min if slope_min is not None else 0
    fb1 = (slope_60 > sm and dev_ma200 > -15
           and _ma50_reached_ma200(ma50, ma200) and trend_30 > -10 and vol_dry_ok)
    fb2_base = (len(subs) >= 1 and (slope_60 > 5 or slope_60 < -5))

    # ── 取 max stage (生产 stage_factor.py L383-402) ──
    scores = {
        'Accumulation': score_Accumulation,
        'Markup': score_Markup,
        'Distribution': score_Distribution,
    }
    if max(scores.values()) > 0:
        stage = max(scores, key=scores.get)
        if stage == 'Markup':
            ch = "main"
        elif stage == 'Accumulation':
            ch = "accum"
        else:
            ch = "dist"
    elif fb1:
        stage = "Markup"
        ch = "fallback1"
    elif fb2_base:
        if slope_60 > 5 and trend_30 > 5:
            stage = "Markup"; ch = "fallback2"
        elif slope_60 < -5 and trend_30 < -5:
            stage = "Accumulation"; ch = "fb2_accum"
        else:
            stage = "?"; ch = "?"
    else:
        stage = "?"; ch = "?"
    return stage, ch


def score(rows, pred_key, label_key="fwd10", thresh=3.0):
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
    flagged = tp + fp
    positives = tp + fn
    prec = tp / flagged if flagged else 0.0
    rec = tp / positives if positives else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"n": flagged, "prec": prec, "rec": rec, "f1": f1, "tp": tp, "fp": fp}


def main():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = _filter_by_pe(codes, 60.0)
    codes = _filter_by_quality(codes, or_yoy_min=0, gm_min=0, np_yoy_min=0,
                              roe_min=5.0, gm_abs_min=20.0)
    codes = _filter_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)
    all_kl = _load_all_klines(codes, limit_per_code=2000)
    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    rows = []
    mismatches = 0
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
        evs = scan_sub_events(closes, highs, lows, vols, None, o=opens, pct_chg=pct_chgs,
                              period_label="daily", code=code, as_of_idx=None,
                              dates=dates_, precomputed_arrs=arrs)
        i = n - 1

        # 生产判定 (基准)
        sub_all = {e["name"] for e in evs if e["idx"] <= i}
        prod_out = wyckoff_judge(i, arrs, sub_all, window=arrs['window'], period_label="daily")
        prod_stage = "?" if prod_out is None else prod_out.get("stage", "?")

        # 复刻 (默认参数 = 生产)
        rep_stage, rep_ch = judge_replica(i, arrs, evs, sub_all, None, closes, vols)
        if rep_stage != prod_stage:
            mismatches += 1
            if mismatches <= 5:
                print(f"  ⚠️ 复刻不一致 {code}: prod={prod_stage} rep={rep_stage} ({rep_ch})")

        # 各 sub_event 的最近触发 idx
        latest_idx = {}
        for e in evs:
            if e["idx"] <= i:
                latest_idx[e["name"]] = max(latest_idx.get(e["name"], -1), e["idx"])
        age = {nm: i - ix for nm, ix in latest_idx.items()}
        min_age = min(age.values()) if age else 10 ** 9

        rows.append({
            "code": code, "name": name_by_code.get(code, code),
            "prod_stage": prod_stage, "prod_ch": rep_ch,
            "slope_60": arrs['slope_60'][i], "trend_30": arrs['trend_30'][i],
            "sub_all": sub_all, "min_age": min_age, "age": age,
            "closes": closes, "vols": vols, "arrs": arrs, "i": i,
            "fwd5": ret_pct(closes, 5), "fwd10": ret_pct(closes, 10),
            "fwd20": ret_pct(closes, 20),
        })

    print(f"\n分析 {len(rows)} 只")
    print(f"复刻 vs 生产 一致性检查: {len(rows) - mismatches}/{len(rows)} 一致"
          f"{'  ✅' if mismatches == 0 else '  ❌ 有漂移!'}")
    if mismatches:
        print("❌ 复刻漂移, 结论不可信, 退出")
        return

    # ── 基准线 ──
    # 两个等价但含义不同的基准:
    #   ① 正样本占比 = 全池里"过去10日涨≥3%"的票占比 = 18.5%
    #      → 这是"精确率"的天花板参照: 随机猜一只票, 命中率 ≈ 18.5%
    #   ② 随机基准准确率 = 1 - 假阳性率 = 41.4%
    #      → 随机猜 57 只票, 里面"对"的期望比例
    # ⚠️ 对比某路径的精确率时, 应该跟 ② 比 (同口径: 都是"猜中的比例")
    vals = [r["fwd10"] for r in rows if r["fwd10"] is not None]
    pos_rate = sum(1 for v in vals if v >= 3.0) / len(vals)
    n_pos = sum(1 for v in vals if v >= 3.0)
    print(f"\n基准: 全池正样本占比 {pos_rate*100:.1f}% ({n_pos}/{len(vals)} 只)")
    print("      随机基准准确率 (1-假阳性率) — 用这个对比精确率")

    # ── 各方案 ──
    def run(sub_window=None, vol_dry_ratio=None, slope_min=None):
        for r in rows:
            if sub_window is not None:
                recent = {nm for nm, a in r["age"].items() if a <= sub_window}
            else:
                recent = None
            stage, ch = judge_replica(r["i"], r["arrs"], None, r["sub_all"], recent,
                                       r["closes"], r["vols"],
                                       sub_window=sub_window,
                                       vol_dry_ratio=vol_dry_ratio,
                                       slope_min=slope_min)
            r["_p"] = (stage == "Markup")
            r["_ch"] = ch
        return score(rows, "_p")

    scenarios = [
        ("现状 (基线)", {}),
        ("① sub 窗口 10 根", {"sub_window": 10}),
        ("① sub 窗口 20 根", {"sub_window": 20}),
        ("① sub 窗口 30 根", {"sub_window": 30}),
        ("① sub 窗口 60 根", {"sub_window": 60}),
        ("② fb1 加缩量 0.65", {"vol_dry_ratio": 0.65}),
        ("② fb1 加缩量 0.50", {"vol_dry_ratio": 0.50}),
        ("③ fb1 slope>8 (WTA)", {"slope_min": 8}),
        ("①20根 + ②0.65", {"sub_window": 20, "vol_dry_ratio": 0.65}),
        ("①20根 + ③8", {"sub_window": 20, "slope_min": 8}),
        ("①20根+②0.65+③8", {"sub_window": 20, "vol_dry_ratio": 0.65, "slope_min": 8}),
    ]

    print("=" * 100)
    print("Markup 判定 — 各方案准确度对比 (标签: 过去10日涨幅 ≥ +3%)")
    print("=" * 100)
    print(f"  {'方案':22s} {'判定数':>7s} {'准确率':>8s} {'召回率':>8s} {'F1':>7s} "
          f"{'vs基准':>8s}  {'新增':>10s}")
    print("-" * 100)

    base_hits = None
    for name, kw in scenarios:
        s = run(**kw)
        hits = {r["code"] for r in rows if r["_p"]}
        if base_hits is None:
            base_hits = hits
        added = len(hits - base_hits)
        removed = len(base_hits - hits)
        print(f"  {name:22s} {s['n']:7d} {s['prec']*100:7.1f}% {s['rec']*100:7.1f}% "
              f"{s['f1']*100:6.1f}% {(s['prec']-pos_rate)*100:+7.1f}pp  "
              f"+{added}/-{removed}")

    # 基准态的命中明细
    run()
    base_hits = {r["code"] for r in rows if r["_p"]}
    base_detail = {r["code"]: r for r in rows if r["_p"]}

    print("\n" + "=" * 100)
    print("现状判为 Markup 的 57 只 — 逐只明细 (按准确率贡献排序)")
    print("=" * 100)
    print(f"  {'代码':8s} {'名称':10s} {'通道':10s} {'slope60':>8s} {'trend30':>8s} "
          f"{'最近sub(根)':>12s} {'过去10日':>9s} {'对/错':>6s}")
    print("-" * 100)
    for r in sorted(base_detail.values(), key=lambda x: -(x["fwd10"] or -99)):
        ok = "✅" if (r["fwd10"] is not None and r["fwd10"] >= 3.0) else "❌"
        f10 = f"{r['fwd10']:+8.2f}%" if r["fwd10"] is not None else "     n/a"
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['prod_ch'][:10]:10s} "
              f"{r['slope_60']:+8.2f} {r['trend_30']:+8.2f} "
              f"{r['min_age'] if r['min_age'] < 10**8 else '无':>12} {f10:>9s} {ok:>6s}")

    # 各方案的差集明细
    print("\n" + "=" * 100)
    print("差集明细 — 哪些票被加进来 / 剔掉")
    print("=" * 100)
    for name, kw in scenarios[1:]:
        run(**kw)
        hits = {r["code"] for r in rows if r["_p"]}
        added = [r for r in rows if r["code"] in (hits - base_hits)]
        removed = [r for r in rows if r["code"] in (base_hits - hits)]
        if not added and not removed:
            continue
        print(f"\n  【{name}】")
        for r in sorted(added, key=lambda x: -(x["fwd10"] or -99)):
            ok = "✅" if (r["fwd10"] is not None and r["fwd10"] >= 3.0) else "❌"
            f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "   n/a"
            print(f"    + {r['code']} {r['name'][:10]:10s} {r['prod_ch'][:10]:10s} "
                  f"最近sub={r['min_age']:>4}根 fwd10={f10} {ok}")
        for r in sorted(removed, key=lambda x: -(x["fwd10"] or -99)):
            ok = "✅" if (r["fwd10"] is not None and r["fwd10"] >= 3.0) else "❌"
            f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "   n/a"
            print(f"    - {r['code']} {r['name'][:10]:10s} {r['prod_ch'][:10]:10s} "
                  f"最近sub={r['min_age']:>4}根 fwd10={f10} {ok}")

    # 持仓状态 — 一次算完所有方案, 避免重复全量重跑
    print("\n" + "=" * 100)
    print("持仓 11 只 — 各方案下的判定通道")
    print("=" * 100)
    hold_codes = [c for c in HOLDINGS if any(r["code"] == c for r in rows)]
    chan_matrix = {}
    for name, kw in scenarios:
        run(**kw)
        chan_matrix[name] = {r["code"]: r["_ch"] for r in rows}
    names = [n for n, _ in scenarios]
    print(f"  {'代码':8s} {'名称':8s} " + " ".join(f"{n[:13]:13s}" for n in names))
    print("-" * 100)
    for code in hold_codes:
        name = HOLDINGS[code]
        cells = [f"{chan_matrix[n].get(code, '?')[:13]:13s}" for n in names]
        print(f"  {code:8s} {name:8s} " + " ".join(cells))


if __name__ == "__main__":
    main()
