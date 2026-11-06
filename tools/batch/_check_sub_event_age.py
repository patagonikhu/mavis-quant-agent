"""查: sub_event 的实际"年龄" — fallback2 依赖的 sub_event 是多久前触发的?

跑法: bash tools/with_venv.sh python -m tools.batch._check_sub_event_age
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.batch.wyckoff_a2m_scan import _load_all_klines, load_stock_basic
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events

# A→M 命中 + fallback2 走的那几只
CASES = [
    ("000333", "美的集团"), ("002683", "广东宏大"), ("300488", "恒锋工具"),
    ("603199", "九华旅游"), ("605338", "巴比食品"), ("688128", "中国电研"),
    ("001219", "青岛食品"), ("002415", "海康威视"), ("301101", "明月镜片"),
    ("601033", "永兴股份"), ("301109", "军信股份"), ("002690", "美亚光电"),
    ("300357", "我武生物"), ("603387", "基蛋生物"), ("603606", "东方电缆"),
]


def main():
    kl = _load_all_klines([c for c, _ in CASES], limit_per_code=2000)

    print("=" * 118)
    print("sub_event 年龄 — 判定时(最后一根)往前看, 每个 sub_event 是多少根 K 线之前触发的")
    print("=" * 118)
    print(f"{'代码':8s} {'名称':10s} {'总事件':>6s} {'最老(根)':>9s} {'最新(根)':>9s} "
          f"{'最近事件明细 (事件: 距今根数)':<44s}")
    print("-" * 118)

    for code, name in CASES:
        df = kl.get(code)
        if df is None or len(df) < 220:
            print(f"{code:8s} {name:10s}  数据不足")
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
        # 去重 (同名事件保留最近一次)
        latest = {}
        for e in evs:
            if e["idx"] <= i:
                latest[e["name"]] = e
        ages = sorted(((i - e["idx"], nm) for nm, e in latest.items()))
        detail = "  ".join(f"{nm}:{ag}" for ag, nm in ages)
        oldest = ages[0][0] if ages else 0
        newest = ages[-1][0] if ages else 0
        print(f"{code:8s} {name:10s} {len(evs):6d} {oldest:9d} {newest:9d}  {detail}")

    print("\n" + "=" * 118)
    print("结论参考")
    print("=" * 118)
    print("  '最老(根)' = 距今最久的那次 sub_event 是多少根 K 线之前触发的")
    print("  如果数值很大 (比如 >100 根), 说明该 sub_event 早已失去时效性,")
    print("  但 fallback2 只要求 len(sub_events) >= 1, 照样会放行。")


if __name__ == "__main__":
    main()
