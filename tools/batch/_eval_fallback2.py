"""fallback2 逐票深挖: 8 只命中票靠什么通过 + 它们跟 fallback1 的区别

只读评估。跑法: bash tools/with_venv.sh python -m tools.batch._eval_fallback2
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

    fb2_rows = []
    fb1_rows = []
    main_rows = []
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
        if stage != "Markup":
            continue
        fv = out.get("factor_values", {}) or {}
        md = out.get("markup_detail", {}) or {}

        slope_60 = fv.get("slope_60", 0)
        trend_30 = fv.get("trend_30", 0)
        dev_ma200 = fv.get("dev_ma200", 0)
        ma50 = fv.get("ma50", 0)
        ma200 = fv.get("ma200", 0)
        fb1 = (slope_60 > 0 and dev_ma200 > -15
               and _ma50_reached_ma200(ma50, ma200) and trend_30 > FB1_TREND30_MIN)
        fb2 = (len(sub_all) >= 1 and slope_60 > 5 and trend_30 > 5)

        # 各 sub_event 的年龄
        latest = {}
        for e in evs:
            if e["idx"] <= i:
                latest[e["name"]] = max(latest.get(e["name"], -1), e["idx"])
        ages = {nm: i - ix for nm, ix in latest.items()}
        sos_age = ages.get("SOS")
        # 最近的"启动类"事件 (SOS/TrendPullback/MarkupEntry)
        launch_ages = {nm: a for nm, a in ages.items()
                       if nm in ("SOS", "TrendPullback", "MarkupEntry")}

        def ret(b):
            if len(closes) <= b or closes[-1-b] <= 0:
                return None
            return (closes[-1] / closes[-1-b] - 1) * 100

        rec = {
            "code": code, "name": name_by_code.get(code, code),
            "slope_60": slope_60, "trend_30": trend_30, "dev_ma200": dev_ma200,
            "ma50_ma200": ma50 / ma200 * 100 if ma200 > 0 else 0,
            "n_sub": len(sub_all), "subs": sorted(sub_all),
            "ages": ages, "sos_age": sos_age, "launch_ages": launch_ages,
            "min_age": min(ages.values()) if ages else 10**9,
            "wta": md.get("wta_markup_hit"),
            "fwd5": ret(5), "fwd10": ret(10), "fwd20": ret(20),
        }
        if fb2 and not fb1:
            fb2_rows.append(rec)
        elif fb1:
            fb1_rows.append(rec)
        else:
            main_rows.append(rec)

    def show(title, rs, extra=""):
        print(f"\n{'='*116}")
        print(f"{title} ({len(rs)} 只)")
        print("=" * 116)
        print(f"  {'代码':8s} {'名称':10s} {'slope60':>8s} {'trend30':>8s} {'ma50/200':>9s} "
              f"{'SOS距今':>8s} {'最近事件距今':>11s} {'10日':>8s} {'20日':>8s}")
        print("-" * 116)
        for r in sorted(rs, key=lambda x: -(x["fwd10"] or -99)):
            sos = f"{r['sos_age']}根" if r["sos_age"] is not None else "无SOS"
            la = (f"{min(r['launch_ages'].values())}根" if r["launch_ages"] else "无")
            f10 = f"{r['fwd10']:+7.2f}%" if r["fwd10"] is not None else "    n/a"
            f20 = f"{r['fwd20']:+7.2f}%" if r["fwd20"] is not None else "    n/a"
            print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['slope_60']:+8.2f} "
                  f"{r['trend_30']:+8.2f} {r['ma50_ma200']:8.1f}% {sos:>8s} {la:>11s} "
                  f"{f10:>8s} {f20:>8s}")
        if extra:
            print(extra)

    show("fallback2 通道 (有 sub-event + 60日/30日双向上)", fb2_rows)
    show("fallback1 通道 (4 项软条件)", fb1_rows)
    show("主路径 (评分制)", main_rows)

    # fallback2 的 sub_event 构成
    print(f"\n{'='*116}")
    print("fallback2 的 8 只 — 每个 sub_event 的年龄 (根)")
    print("=" * 116)
    for r in sorted(fb2_rows, key=lambda x: x["code"]):
        items = "  ".join(f"{nm}:{a}" for nm, a in sorted(r["ages"].items(), key=lambda kv: kv[1]))
        print(f"  {r['code']} {r['name'][:10]:10s} {items}")

    # 统计
    print(f"\n{'='*116}")
    print("统计")
    print("=" * 116)
    def stats(rs, nm):
        n = len(rs)
        if not n:
            return
        ok10 = sum(1 for r in rs if r["fwd10"] is not None and r["fwd10"] >= 3.0)
        ok20 = sum(1 for r in rs if r["fwd20"] is not None and r["fwd20"] >= 3.0)
        with_sos = sum(1 for r in rs if r["sos_age"] is not None)
        sos_recent = sum(1 for r in rs if r["sos_age"] is not None and r["sos_age"] <= 20)
        print(f"  {nm:12s} n={n:3d}  准确率(10日)={ok10/n*100:5.1f}%  "
              f"准确率(20日)={ok20/n*100:5.1f}%  有SOS={with_sos}/{n}  "
              f"SOS在20根内={sos_recent}/{n}")
    stats(fb2_rows, "fallback2")
    stats(fb1_rows, "fallback1")
    stats(main_rows, "main")
    print(f"\n  ★ fallback2 的 SOS 距离与准确率的关系是核心看点")


if __name__ == "__main__":
    main()
