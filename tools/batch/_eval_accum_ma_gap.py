"""实测: Accumulation「均线胶着」判定用 window(250) vs 真 MA200 的差异

WTA (L2367): ma_long = close.rolling(200).mean()  → 真 MA200
我们 (L152/L237): ma200 = arrs['ma_window']        → window(250) 均线

只读评估, 不改生产代码。
跑法: bash tools/with_venv.sh python -m tools.batch._eval_accum_ma_gap
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

HOLDINGS = {
    "001219": "青岛食品", "002415": "海康威视", "301101": "明月镜片",
    "601033": "永兴股份", "301109": "军信股份", "605338": "巴比食品",
    "300357": "我武生物", "002683": "广东宏大", "002690": "美亚光电",
    "603387": "基蛋生物", "603606": "东方电缆",
}


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

    diffs = []
    both_ok = win_ok = 0
    for code, df in sorted(all_kl.items()):
        if df is None or len(df) < 220:
            continue
        closes = df['close'].tolist()
        highs = df['high'].tolist()
        lows = df['low'].tolist()
        vols = df['vol'].tolist()
        n = len(closes)
        arrs = build_kline_features(closes, highs, lows, vols, window=min(250, n))
        i = n - 1
        if i < 30:
            continue
        ma50 = arrs['ma50'][i]
        ma250 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
        ma200 = arrs['ma200'][i]

        gap_win = abs(ma50 - ma250) / ma250 * 100 if ma250 > 0 else 999
        gap_true = abs(ma50 - ma200) / ma200 * 100 if ma200 > 0 else 999
        ok_win = gap_win <= 8
        ok_true = gap_true <= 8

        if ok_win:
            win_ok += 1
        if ok_true:
            true_ok += 1
        if ok_win and ok_true:
            both_ok += 1
        elif ok_win != ok_true:
            diffs.append({
                "code": code, "name": name_by_code.get(code, code),
                "gap_win": gap_win, "gap_true": gap_true,
                "win_ok": ok_win, "true_ok": ok_true,
                "ma50": ma50, "ma250": ma250, "ma200": ma200,
            })

    total_all = both_ok + len(diffs)
    print(f"\n{'='*104}")
    print("「均线胶着」判定: window(250) vs 真 MA200 (阈值 |gap| ≤ 8%)")
    print("=" * 104)
    print(f"  两者都通过:                {both_ok}")
    print(f"  只有 window(250) 通过:     {len([d for d in diffs if d['win_ok']])}")
    print(f"  只有 真MA200 通过:         {len([d for d in diffs if not d['win_ok']])}")
    print(f"  判定不一致:                {len(diffs)} / {total_all} ({len(diffs)/total_all*100:.1f}%)")

    if diffs:
        print(f"\n{'='*104}")
        print("不一致的票")
        print("=" * 104)
        print(f"  {'代码':8s} {'名称':10s} {'gap(250)':>10s} {'gap(200)':>10s} "
              f"{'250通过':>8s} {'200通过':>8s}")
        print("-" * 104)
        for d in sorted(diffs, key=lambda x: abs(x["gap_win"] - x["gap_true"])):
            print(f"  {d['code']:8s} {d['name'][:10]:10s} {d['gap_win']:9.2f}% "
                  f"{d['gap_true']:9.2f}% {'✅' if d['win_ok'] else '❌':>8s} "
                  f"{'✅' if d['true_ok'] else '❌':>8s}")

    # 持仓
    print(f"\n{'='*104}")
    print("持仓 11 只")
    print("=" * 104)
    for code, name in HOLDINGS.items():
        df = all_kl.get(code)
        if df is None or len(df) < 220:
            print(f"  {code:8s} {name:8s}  数据不足")
            continue
        closes = df['close'].tolist()
        highs = df['high'].tolist()
        lows = df['low'].tolist()
        vols = df['vol'].tolist()
        n = len(closes)
        arrs = build_kline_features(closes, highs, lows, vols, window=min(250, n))
        i = n - 1
        ma50 = arrs['ma50'][i]
        ma250 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
        ma200 = arrs['ma200'][i]
        gw = abs(ma50 - ma250) / ma250 * 100
        gt = abs(ma50 - ma200) / ma200 * 100
        flag = "  ⚠️ 不一致" if (gw <= 8) != (gt <= 8) else ""
        print(f"  {code:8s} {name:8s} gap(250)={gw:6.2f}%  gap(200)={gt:6.2f}%  "
              f"250={'✅' if gw <= 8 else '❌'} 200={'✅' if gt <= 8 else '❌'}{flag}")


if __name__ == "__main__":
    main()
