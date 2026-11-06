"""fallback2 单独加时效窗口 — 量化影响 (2026-11-03)

只给 fallback2 的入口加时效, **不动 Accumulation / Distribution 评分**,
避免上一轮「Accumulation 失效 → 票被 fallback2 兜成 Markup」的连锁。

测的口径:
  N 根内有**启动类事件** (SOS / TrendPullback / MarkupEntry) → 才允许 fallback2 放行
  vs
  N 根内有**任意事件** → 才允许 fallback2 放行
  vs
  现状 (有史以来任意事件都算数)

输出: 各档准确率/召回率/F1 + fallback2 命中集合变化 + 持仓状态

跑法: bash tools/with_venv.sh python -m tools.batch._eval_fb2_timeliness
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.factors.wyckoff.helpers import FB1_TREND30_MIN

from tools.batch.wyckoff_a2m_scan import (
    _load_all_klines, _filter_by_st, _filter_by_pe, _filter_by_quality,
    _filter_by_leverage, load_codes_all_market, load_stock_basic,
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

LAUNCH_EVENTS = ("SOS", "TrendPullback", "MarkupEntry")


def ret_pct(closes, back):
    if len(closes) <= back or closes[-1 - back] <= 0:
        return None
    return (closes[-1] / closes[-1 - back] - 1) * 100


def score(rows, pred_key, thresh=3.0):
    tp = fp = fn = tn = 0
    for r in rows:
        fwd = r.get("fwd10")
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
        fv = out.get("factor_values", {}) or {}

        # 各事件最近触发 idx
        latest = {}
        for e in evs:
            if e["idx"] <= i:
                latest[e["name"]] = max(latest.get(e["name"], -1), e["idx"])
        age = {nm: i - ix for nm, ix in latest.items()}

        slope_60 = fv.get("slope_60", 0)
        trend_30 = fv.get("trend_30", 0)
        dev_ma200 = fv.get("dev_ma200", 0)
        ma50 = fv.get("ma50", 0)
        ma200 = fv.get("ma200", 0)
        fb1 = (slope_60 > 0 and dev_ma200 > -15
               and _ma50_reached_ma200(ma50, ma200) and trend_30 > FB1_TREND30_MIN)
        fb2_markup = (len(sub_all) >= 1 and slope_60 > 5 and trend_30 > 5)

        # 「fb2 入口不通过时」生产代码会判成什么:
        #   直接把 sub_events 传空集调生产函数 ——
        #   fb2 是 elif 链最后一位, 空集时 fb2_base 必然不成立,
        #   于是走不到 fb2, 得到的就是「收紧 fb2 后」的正确答案。
        #   ⚠️ 注意: 空集也会影响 Accumulation 的 sub_event 加分 (score += 2),
        #      所以这只在「原 stage 是 Markup 且非 fb1」时才是准确的。
        stage_no_fb2 = None
        if stage == "Markup" and not fb1:
            out2 = wyckoff_judge(i, arrs, set(), window=arrs['window'], period_label="daily")
            stage_no_fb2 = "?" if out2 is None else out2.get("stage", "?")

        rows.append({
            "code": code, "name": name_by_code.get(code, code),
            "stage": stage, "fb1": fb1, "fb2_markup": fb2_markup,
            "stage_if_no_fb2": stage_no_fb2,
            "slope_60": slope_60, "trend_30": trend_30,
            "age": age, "n_sub": len(sub_all),
            "fwd5": ret_pct(closes, 5), "fwd10": ret_pct(closes, 10),
            "fwd20": ret_pct(closes, 20),
        })

    print(f"\n分析 {len(rows)} 只\n")

    # ── 各方案: 只改 fallback2 入口, 其余完全不动 ──
    def fb2_ok(r, window, mode):
        """fallback2 入口是否通过"""
        if window is None:
            return r["n_sub"] >= 1                    # 现状
        if mode == "launch":                          # N 根内有启动类事件
            ages = [a for nm, a in r["age"].items() if nm in LAUNCH_EVENTS]
            return bool(ages) and min(ages) <= window
        else:                                         # N 根内有任意事件
            return r["n_sub"] >= 1 and min(r["age"].values()) <= window

    def stage_of(r, window, mode):
        """复刻判定链 (只改 fb2 入口)

        ⚠️ 关键: 必须区分「原 stage 是 Markup」和「靠 fb2 才成为 Markup」。
        做法:
          - 原 stage 非 Markup → 原样返回 (fb2 收紧不会让非 Markup 变 Markup)
          - 原 stage 是 Markup 且 fb1 成立 → fallback1 通道, 不受影响
          - 原 stage 是 Markup 且 fb1 不成立 → 可能是主路径或 fb2。
            用「把 sub_events 传空集调生产函数」预先算好 stage_if_no_fb2:
              · 若 fb2 时效通过 → Markup (fb2 放行)
              · 若 fb2 时效不通过 → stage_if_no_fb2 (主路径仍成立则还是 Markup,
                否则掉回 Accumulation / ?)
        """
        if r["stage"] != "Markup":
            return r["stage"]
        if r["fb1"]:
            return "Markup"                       # fallback1 通道, 不受影响
        if r["slope_60"] > 5 and r["trend_30"] > 5:
            if fb2_ok(r, window, mode):
                return "Markup"                   # fb2 放行
            return r.get("stage_if_no_fb2") or "?"  # fb2 收紧 → 回落
        return "Markup"                           # 主路径 (fb2 条件不成立)

    scenarios = [
        ("现状 (无时效)", None, None),
        ("启动类事件 10 根", 10, "launch"),
        ("启动类事件 20 根", 20, "launch"),
        ("启动类事件 30 根", 30, "launch"),
        ("启动类事件 60 根", 60, "launch"),
        ("任意事件 20 根", 20, "any"),
        ("任意事件 60 根", 60, "any"),
    ]

    # ── 交叉验证: 现状方案下 stage_of 必须 == 生产 stage ──
    mism = 0
    for r in rows:
        if stage_of(r, None, None) != r["stage"]:
            mism += 1
            if mism <= 5:
                print(f"  ⚠️ 复刻不一致 {r['code']}: prod={r['stage']} "
                      f"rep={stage_of(r, None, None)}")
    print(f"\n复刻 vs 生产一致性: {len(rows)-mism}/{len(rows)} "
          f"{'✅' if mism == 0 else '❌ 结论不可信'}")
    if mism:
        print("❌ 退出")
        return

    vals = [r["fwd10"] for r in rows if r["fwd10"] is not None]
    pos_rate = sum(1 for v in vals if v >= 3.0) / len(vals)
    print(f"基准 (全池正样本占比): {pos_rate*100:.1f}%")

    print(f"\n{'='*104}")
    print("fallback2 加时效 — Markup 判定准确度 (标签: 过去10日涨幅 ≥ +3%)")
    print("=" * 104)
    print(f"  {'方案':20s} {'判定数':>7s} {'准确率':>8s} {'召回率':>8s} {'F1':>7s} {'vs基准':>8s}")
    print("-" * 104)

    base_fb2 = None
    for name, win, mode in scenarios:
        for r in rows:
            r["_p"] = (stage_of(r, win, mode) == "Markup")
        s = score(rows, "_p")
        fb2_set = {r["code"] for r in rows
                   if fb2_ok(r, win, mode) and r["slope_60"] > 5 and r["trend_30"] > 5}
        if base_fb2 is None:
            base_fb2 = fb2_set
        delta = f"+{len(fb2_set-base_fb2)}/-{len(base_fb2-fb2_set)}" if win else "—"
        print(f"  {name:20s} {s['n']:7d} {s['prec']*100:7.1f}% {s['rec']*100:7.1f}% "
              f"{s['f1']*100:6.1f}% {(s['prec']-pos_rate)*100:+7.1f}pp   fb2 {len(fb2_set):2d}只 {delta}")

    # ── fallback2 通道本身的准确率 (只统计走 fb2 的票) ──
    # ── 只看「靠 fb2 才成为 Markup」的票 ──
    #   判据: 原 stage == Markup 且 fb1 不成立 且 stage_if_no_fb2 != Markup
    #   (即: 把 fb2 入口堵掉之后, 这只票就不再是 Markup 了 → 它就是靠 fb2 的)
    print(f"\n{'='*104}")
    print("只看「靠 fb2 才成为 Markup」的票 (标签 过去10日涨幅 ≥ +3%)")
    print("=" * 104)
    print(f"  {'方案':20s} {'fb2只数':>8s} {'准确率':>8s} {'10日均涨':>10s} {'剔除数':>8s}")
    print("-" * 104)
    fb2_base_set = {r["code"] for r in rows
                    if r["stage"] == "Markup" and not r["fb1"]
                    and r.get("stage_if_no_fb2") != "Markup"}
    for name, win, mode in scenarios:
        fb2 = [r for r in rows if r["code"] in fb2_base_set
               and fb2_ok(r, win, mode) and r["slope_60"] > 5 and r["trend_30"] > 5]
        if not fb2:
            print(f"  {name:20s} {0:8d} {'—':>8s} {'—':>10s} {len(fb2_base_set):8d}")
            continue
        ok = sum(1 for r in fb2 if r["fwd10"] is not None and r["fwd10"] >= 3.0)
        vals_ok = [r["fwd10"] for r in fb2 if r["fwd10"] is not None]
        avg = sum(vals_ok) / max(len(vals_ok), 1)
        print(f"  {name:20s} {len(fb2):8d} {ok/len(fb2)*100:7.1f}% {avg:+9.2f}% "
              f"{len(fb2_base_set)-len(fb2):8d}")
    print(f"\n  (说明: fb2_base_set = {len(fb2_base_set)} 只「靠 fb2 才成为 Markup」的票; "
          f"「剔除数」= 收紧后掉出 fb2 的只数)")

    # ── 差集明细 ──
    print(f"\n{'='*104}")
    print("fallback2 通道的差集明细 (现状 vs 启动类事件 20 根)")
    print("=" * 104)
    cur = {r["code"]: r for r in rows if r["code"] in fb2_base_set}
    new = {r["code"]: r for r in rows if r["code"] in fb2_base_set
           and fb2_ok(r, 20, "launch") and r["slope_60"] > 5 and r["trend_30"] > 5}
    for c in sorted(set(cur) - set(new)):
        r = cur[c]
        la = min((a for nm, a in r["age"].items() if nm in LAUNCH_EVENTS), default=None)
        f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "   n/a"
        ok = "✅" if r["fwd10"] is not None and r["fwd10"] >= 3.0 else "❌"
        print(f"  剔除 {c} {r['name'][:10]:10s} 最近启动类={str(la)+'根' if la else '无':>6s} "
              f"fwd10={f10} {ok}  → 回落成 {r.get('stage_if_no_fb2')}")
    for c in sorted(set(new) - set(cur)):
        r = new[c]
        f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "   n/a"
        print(f"  新增 {c} {r['name'][:10]:10s} fwd10={f10}")

    # ── 持仓 ──
    print(f"\n{'='*104}")
    print("持仓 11 只 — 各方案下的最终 stage")
    print("=" * 104)
    print(f"  {'代码':8s} {'名称':8s} " + " ".join(f"{n[:12]:12s}" for n, _, _ in scenarios))
    print("-" * 104)
    for code, name in HOLDINGS.items():
        r = next((x for x in rows if x["code"] == code), None)
        if r is None:
            print(f"  {code:8s} {name:8s}  数据不足")
            continue
        cells = []
        for _, win, mode in scenarios:
            cells.append(f"{stage_of(r, win, mode)[:12]:12s}")
        print(f"  {code:8s} {name:8s} " + " ".join(cells))


if __name__ == "__main__":
    main()
