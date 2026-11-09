"""fallback1 的 trend_30 > -10 门槛有多松? (只读评估, 不改生产代码)

问题: fallback1 是 4 项 AND 门:
    slope_60 > 0 AND dev_ma200 > -15 AND ma50>=ma200*TOL AND trend_30 > -10
trend_30 > -10 意味着「近 31 根最多跌 10%」—— 门槛松不松? 松了会漏进什么?

本脚本:
  1) 复刻 fallback1 的 4 条件 (必须与生产 stage_factor.py L385 100% 一致)
  2) 列出所有走 fallback1 的票, 看 trend_30 分布
  3) 反事实: 如果 trend_30 门槛收紧 (0 / -5 / -10 / 5), 各放行几只
  4) 关键: 看被门槛挡住 / 放行的票, **近 30 日实际涨跌** 是多少
     (trend_30 就是近 31 根涨幅, 这里直接用它自己)

跑法: bash tools/with_venv.sh python -m tools.batch._eval_fb1_trend30
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
from tools.factors.wyckoff.helpers import _ma50_reached_ma200, MA50_MA200_TOLERANCE


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
    n_pool = 0
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
        n_pool += 1
        stage = out.get("stage", "?")
        fv = out.get("factor_values", {}) or {}

        slope_60 = fv.get("slope_60", 0)
        trend_30 = fv.get("trend_30", 0)
        dev_ma200 = fv.get("dev_ma200", 0)
        ma50 = fv.get("ma50", 0)
        ma200 = fv.get("ma200", 0)

        # ── 复刻 fallback1 (生产 stage_factor.py L385) ──
        # 逐条件, 与生产完全一致
        c1 = slope_60 > 0
        c2 = dev_ma200 > -15
        c3 = _ma50_reached_ma200(ma50, ma200)
        c4 = trend_30 > -10
        fb1_hit = c1 and c2 and c3 and c4

        # 反事实: 只看前 3 项 (门槛前的池子)
        fb1_pre3 = c1 and c2 and c3

        # 反事实: 不同 trend_30 门槛
        gates = {}
        for g in (-15, -10, -5, 0, 5):
            gates[g] = bool(c1 and c2 and c3 and trend_30 > g)

        rows.append({
            "code": code, "name": name_by_code.get(code, code),
            "stage": stage, "fb1": fb1_hit, "fb1_pre3": fb1_pre3,
            "slope_60": slope_60, "trend_30": trend_30, "dev_ma200": dev_ma200,
            "ma50_ma200": (ma50 / ma200 * 100) if ma200 > 0 else 0.0,
            "c1": c1, "c2": c2, "c3": c3, "c4": c4,
            "gates": gates, "n_sub": len(sub_all),
        })

    print(f"\n有效池 {n_pool} 只")
    fb1 = [r for r in rows if r["fb1"]]
    pre3 = [r for r in rows if r["fb1_pre3"]]
    print(f"走 fallback1 的: {len(fb1)} 只")
    print(f"前 3 项就过 (只差 trend_30>-10) 的: {len(pre3)} 只")
    blocked = [r for r in pre3 if not r["fb1"]]
    if blocked:
        print(f"  → 被 trend_30 > -10 挡掉的: {len(blocked)} 只")

    # ── fallback1 名单 + trend_30 分布 ──
    print("\n" + "=" * 118)
    print("走 fallback1 的票 (trend_30 = 近 31 根涨幅 %)")
    print("=" * 118)
    print(f"  {'代码':8s} {'名称':10s} {'slope_60':>9s} {'trend_30':>9s} {'dev_ma200':>10s} "
          f"{'ma50/ma200':>10s} {'近30日跌?':>10s}")
    print(f"  {'':8s} {'':10s} {'(60日%)':>9s} {'(31日%)':>9s} {'':>10s} {'':>10s}")
    down30 = 0
    for r in sorted(fb1, key=lambda x: x["trend_30"]):
        is_down = "❌在跌" if r["trend_30"] < 0 else "✅在涨"
        if r["trend_30"] < 0:
            down30 += 1
        print(f"  {r['code']:8s} {r['name'][:10]:10s} {r['slope_60']:+9.2f} "
              f"{r['trend_30']:+9.2f} {r['dev_ma200']:+10.2f} "
              f"{r['ma50_ma200']:9.1f}% {is_down:>10s}")
    print(f"\n  → trend_30 < 0 (近 31 根在跌): {down30}/{len(fb1)} 只 "
          f"({down30/max(len(fb1),1)*100:.0f}%)")

    # trend_30 分档
    print("\n  trend_30 分档:")
    buckets = [(10, 999, ">= +10"), (0, 10, "0 ~ +10"), (-5, 0, "-5 ~ 0"),
               (-10, -5, "-10 ~ -5"), (-999, -10, "< -10 (被挡)")]
    for lo, hi, label in buckets:
        c = len([r for r in fb1 if lo <= r["trend_30"] < hi])
        print(f"    {label:14s}: {c:3d} 只")

    # ── 反事实: 收紧门槛 ──
    print("\n" + "=" * 118)
    print("反事实: trend_30 门槛收紧后 fallback1 放行数")
    print("=" * 118)
    for g in (-15, -10, -5, 0, 5):
        c = len([r for r in rows if r["gates"][g]])
        print(f"  trend_30 > {g:4d}: 放行 {c:3d} 只  (vs 当前 {len(fb1)})")

    # ── 被挡掉的票长啥样 ──
    if blocked:
        print("\n" + "=" * 118)
        print(f"被 trend_30 > -10 挡掉的 {len(blocked)} 只 (前 3 项都满足)")
        print("=" * 118)
        for r in sorted(blocked, key=lambda x: x["trend_30"]):
            print(f"  {r['code']:8s} {r['name'][:10]:10s} slope_60={r['slope_60']:+8.2f} "
                  f"trend_30={r['trend_30']:+8.2f} dev_ma200={r['dev_ma200']:+8.2f} "
                  f"ma50/ma200={r['ma50_ma200']:6.1f}%  stage={r['stage']}")


if __name__ == "__main__":
    main()
