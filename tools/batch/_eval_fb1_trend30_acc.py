"""fallback1 的 trend_30 > -10 门槛: 收紧后准确率/召回率怎么变 (0 回测)

标签定义 (与 markup-accuracy-20261103 一致, **只用判定时点之前已发生的数据**):
    正样本 = 判定日**过去** 10 日涨幅 >= +3%  (即 trend_30 本身的前 10 日部分)
    基准 = 全池正样本占比

⚠️ 用户明确说不要回测 → 只用当前时点数据, 不做前向回测。

跑法: bash tools/with_venv.sh python -m tools.batch._eval_fb1_trend30_acc
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    all_kl = _load_all_klines(codes, limit_per_code=2000)
    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    rows = []
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
        evs = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
                              period_label="daily", code=code, as_of_idx=None,
                              dates=dates_, precomputed_arrs=arrs)
        i = n - 1
        sub_all = {e["name"] for e in evs if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_all, window=arrs['window'], period_label="daily")
        if out is None:
            continue

        slope_60 = arrs['slope_60'][i]
        trend_30 = arrs['trend_30'][i]
        ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
        ma50 = arrs['ma50'][i]
        dev_ma200 = ((closes[i] / ma200 - 1) * 100) if ma200 > 0 else 0.0

        c1 = slope_60 > 0
        c2 = dev_ma200 > -15
        c3 = _ma50_reached_ma200(ma50, ma200)

        # 标签: 判定日**过去** 10 日涨幅 (无未来函数)
        #  closes[i-1] 是前一根, closes[i-11] 是前 11 根
        past10 = ((closes[i - 1] / closes[i - 11] - 1) * 100) if i >= 11 else None
        # 判定日**过去** 20 日
        past20 = ((closes[i - 1] / closes[i - 21] - 1) * 100) if i >= 21 else None

        rows.append({
            "code": code, "name": name_by_code.get(code, code),
            "slope_60": slope_60, "trend_30": trend_30,
            "pre3": bool(c1 and c2 and c3),
            "past10": past10, "past20": past20,
        })

    n_pool = len(rows)
    pos_all = [r for r in rows if r["past10"] is not None and r["past10"] >= 3.0]
    base_rate = len(pos_all) / n_pool * 100
    print(f"有效池 {n_pool} 只")
    print(f"基准 (过去10日涨幅>=+3%): {len(pos_all)}/{n_pool} = {base_rate:.1f}%\n")

    print("=" * 96)
    print("fb1 门槛反事实: trend_30 门槛 vs 准确率/召回率")
    print("=" * 96)
    print(f"  {'门槛':>10s} {'放行':>6s} {'准确率(10日)':>13s} {'召回率':>9s} {'F1':>8s} "
          f"{'准确率(20日)':>13s}")
    print(f"  {'':>10s} {'':>6s} {'(正/总)':>13s} {'(占全池正)':>9s} {'':>8s} {'':>13s}")

    results = {}
    for g in (-15, -10, -5, 0, 5):
        sel = [r for r in rows if r["pre3"] and r["trend_30"] > g]
        pos10 = [r for r in sel if r["past10"] is not None and r["past10"] >= 3.0]
        pos20 = [r for r in sel if r["past20"] is not None and r["past20"] >= 3.0]
        acc10 = len(pos10) / len(sel) * 100 if sel else 0
        rec = len(pos10) / len(pos_all) * 100 if pos_all else 0
        f1 = (2 * acc10 * rec / (acc10 + rec)) if (acc10 + rec) else 0
        acc20 = len(pos20) / len(sel) * 100 if sel else 0
        results[g] = (len(sel), acc10, rec, f1, acc20)
        mark = "  ← 当前" if g == -10 else ""
        print(f"  trend_30>{g:5.0f} {len(sel):6d} {acc10:12.1f}% {rec:8.1f}% "
              f"{f1:7.1f}% {acc20:12.1f}%{mark}")

    # ── 被挡掉的 14 只, 它们的实际表现 ──
    blocked = [r for r in rows if r["pre3"] and r["trend_30"] <= -10]
    print("\n" + "=" * 96)
    print(f"被 trend_30 > -10 挡掉的 {len(blocked)} 只 — 挡对了还是挡错了?")
    print("=" * 96)
    print(f"  {'代码':8s} {'名称':10s} {'trend_30':>9s} {'过去10日':>10s} {'过去20日':>10s} {'正样本?':>8s}")
    n_blocked_pos = 0
    for r in sorted(blocked, key=lambda x: x["trend_30"]):
        p10 = f"{r['past10']:+9.2f}%" if r["past10"] is not None else "      n/a"
        p20 = f"{r['past20']:+9.2f}%" if r["past20"] is not None else "      n/a"
        is_pos = (r["past10"] is not None and r["past10"] >= 3.0)
        if is_pos:
            n_blocked_pos += 1
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['trend_30']:+9.2f} {p10} {p20} "
              f"{'✅是' if is_pos else '❌否':>8s}")
    print(f"\n  → 被挡掉的 {len(blocked)} 只里, {n_blocked_pos} 只其实是正样本 "
          f"(误挡), {len(blocked)-n_blocked_pos} 只确实是负样本 (挡对)")

    # ── 放行的 19 只在跌票, 实际表现 ──
    sel10 = [r for r in rows if r["pre3"] and r["trend_30"] > -10]
    down = [r for r in sel10 if r["trend_30"] < 0]
    up = [r for r in sel10 if r["trend_30"] >= 0]
    print("\n" + "=" * 96)
    print("放行票里 trend_30<0 (近31根在跌) vs >=0 的表现")
    print("=" * 96)
    for label, grp in (("trend_30 < 0 (在跌)", down), ("trend_30 >= 0 (在涨)", up)):
        if not grp:
            continue
        p10 = [r for r in grp if r["past10"] is not None and r["past10"] >= 3.0]
        p20 = [r for r in grp if r["past20"] is not None and r["past20"] >= 3.0]
        a10 = len(p10) / len(grp) * 100
        a20 = len(p20) / len(grp) * 100
        print(f"  {label:22s} {len(grp):3d} 只  准确率(10日) {a10:5.1f}%  "
              f"准确率(20日) {a20:5.1f}%")


if __name__ == "__main__":
    main()
