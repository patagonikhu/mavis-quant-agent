"""查: A→M 选中票 + 持仓票 的 MA50/MA200 实际值

两个口径都算:
  ① 生产口径: ma50 vs ma_window (250 日均线)  ← fallback1 实际用的
  ② 真 MA200:  ma50 vs ma200_true (200 日均线) ← 参考实现 WTA 用的

跑法: bash tools/with_venv.sh python -m tools.batch._check_ma_ratio
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.batch.wyckoff_a2m_scan import _load_all_klines, load_stock_basic
from tools.factors.kline_arrays import build_kline_features

HOLDINGS = [
    ("001219", "青岛食品"), ("603606", "东方电缆"), ("002415", "海康威视"),
    ("301101", "明月镜片"), ("601033", "永兴股份"), ("301109", "军信股份"),
    ("605338", "巴比食品"), ("300357", "我武生物"), ("002683", "广东宏大"),
    ("002690", "美亚光电"), ("603387", "基蛋生物"),
]

A2M = [
    ("000333", "美的集团"), ("002683", "广东宏大"), ("300488", "恒锋工具"),
    ("603199", "九华旅游"), ("605338", "巴比食品"), ("688128", "中国电研"),
]


def main():
    codes = [c for c, _ in HOLDINGS] + [c for c, _ in A2M]
    kl = _load_all_klines(sorted(set(codes)), limit_per_code=2000)

    print("=" * 112)
    print("MA50 / MA200 实际值 — 两套口径")
    print("=" * 112)
    print(f"{'代码':8s} {'名称':10s} {'组':6s} {'MA50':>9s} "
          f"{'MA250':>9s} {'MA200真':>9s} {'MA50/MA250':>11s} {'MA50/MA200':>11s} "
          f"{'过0.90?':>9s} {'严格>?':>8s}")
    print("-" * 112)

    rows = []
    for code, name in sorted(set([(c, n) for c, n in HOLDINGS] + [(c, n) for c, n in A2M]),
                             key=lambda x: x[0]):
        df = kl.get(code)
        if df is None or len(df) < 220:
            print(f"{code:8s} {name:10s}  数据不足")
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
        r250 = ma50 / ma250 * 100 if ma250 > 0 else 0
        r200 = ma50 / ma200 * 100 if ma200 > 0 else 0
        grp = "持仓" if (code, name) in HOLDINGS else ""
        if (code, name) in A2M:
            grp = (grp + "+A2M") if grp else "A2M"
        rows.append((code, name, grp, ma50, ma250, ma200, r250, r200))

    for code, name, grp, ma50, ma250, ma200, r250, r200 in rows:
        ok250 = "✅" if r250 >= 90 else "❌"
        ok200 = "✅" if r200 > 100 else "❌"
        print(f"{code:8s} {name:10s} {grp:6s} {ma50:9.2f} "
              f"{ma250:9.2f} {ma200:9.2f} {r250:10.1f}% {r200:10.1f}% "
              f"{ok250:>9s} {ok200:>8s}")

    # 汇总
    print("\n" + "=" * 112)
    print("汇总")
    print("=" * 112)
    a2m_rows = [r for r in rows if "A2M" in r[2]]
    hold_rows = [r for r in rows if "持仓" in r[2]]
    for label, rs in (("A→M 命中 (6 只)", a2m_rows), ("持仓 (11 只)", hold_rows)):
        if not rs:
            continue
        n250 = sum(1 for r in rs if r[6] >= 90)
        n200 = sum(1 for r in rs if r[7] > 100)
        print(f"\n{label}:")
        print(f"  MA50 >= MA250*0.90 (生产容差口径): {n250}/{len(rs)} 只通过")
        print(f"  MA50 >  MA200        (WTA 严格口径):   {n200}/{len(rs)} 只通过")
        for r in rs:
            if r[6] < 90 or r[7] <= 100:
                print(f"    ⚠️ {r[0]} {r[1]:8s} MA50/MA250={r[6]:6.1f}%  "
                      f"MA50/MA200={r[7]:6.1f}%")


if __name__ == "__main__":
    main()
