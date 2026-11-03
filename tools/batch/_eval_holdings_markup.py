"""持仓 11 只票的 Markup 判定 vs 实际表现 (2026-11-03)

用户视角: 只关心 Markup 判得准不准 —— 持仓票是实盘验证样本。

输出每只票:
  判定 (stage / 通道 / WTA 4 判据 / 结构路径)
  + 近 60 日 / 近 20 日 / 近 10 日 实际涨跌幅 (判定前的动量)
  + 当前相对 MA200 / MA50 的位置

跑法: bash tools/with_venv.sh python -m tools.batch._eval_holdings_markup
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, "/Users/I514959/workspace/WyckoffTradingAgent")

from tools.batch.wyckoff_a2m_scan import _load_all_klines, load_stock_basic
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
from tools.factors.wyckoff.helpers import _ma50_reached_ma200

from core.wyckoff_structure import identify_trading_range
from core.wyckoff_engine import FunnelConfig

HOLDINGS = [
    ("001219", "青岛食品"), ("603606", "东方电缆"), ("002415", "海康威视"),
    ("301101", "明月镜片"), ("601033", "永兴股份"), ("301109", "军信股份"),
    ("605338", "巴比食品"), ("300357", "我武生物"), ("002683", "广东宏大"),
    ("002690", "美亚光电"), ("603387", "基蛋生物"),
]


def ret_pct(closes, back):
    if len(closes) <= back or closes[-1 - back] <= 0:
        return None
    return (closes[-1] / closes[-1 - back] - 1) * 100


def main():
    cfg = FunnelConfig()
    kl = _load_all_klines([c for c, _ in HOLDINGS], limit_per_code=2000)
    basic = load_stock_basic()

    print("=" * 122)
    print("持仓 11 只 — Markup 判定 vs 实际表现 (数据日期 20261102)")
    print("=" * 122)
    print(f"{'代码':8s} {'名称':8s} {'stage':12s} {'通道':10s} {'WTA':>4s} {'结构':>5s} "
          f"{'MA50/200':>9s} {'slope60':>8s} {'trend30':>8s} {'60日':>8s} {'20日':>8s} {'10日':>8s}")
    print("-" * 122)

    for code, name in HOLDINGS:
        df = kl.get(code)
        if df is None or len(df) < 220:
            print(f"{code:8s} {name:8s}  数据不足")
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
        subs = scan_sub_events(closes, highs, lows, vols, None, o=opens, pct_chg=pct_chgs,
                               period_label="daily", code=code, as_of_idx=None,
                               dates=dates_, precomputed_arrs=arrs)

        i = n - 1
        sub_set = {e["name"] for e in subs if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_set, window=arrs['window'], period_label="daily")
        stage = "?" if out is None else out.get("stage", "?")
        md = (out or {}).get("markup_detail", {}) or {}

        slope_60 = arrs['slope_60'][i]
        trend_30 = arrs['trend_30'][i]
        ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
        ma50 = arrs['ma50'][i]
        dev_ma200 = ((closes[i] / ma200 - 1) * 100) if ma200 > 0 else 0.0
        n_sub = len(sub_set)

        fb2_base = (n_sub >= 1 and (slope_60 > 5 or slope_60 < -5))
        fb1 = (slope_60 > 0 and dev_ma200 > -15
               and _ma50_reached_ma200(ma50, ma200) and trend_30 > -10)
        fb2 = fb2_base and slope_60 > 5 and trend_30 > 5

        if stage == "Markup":
            ch = "fallback1" if fb1 else ("fallback2" if fb2 else "main")
        else:
            ch = stage

        tr = identify_trading_range(df, cfg)
        struct = "—"
        if tr is not None:
            bo = (closes[i] / tr.resistance - 1) * 100
            struct = "✅" if bo >= 0 else "—"

        wta = "✅" if md.get("wta_markup_hit") else "❌"
        r60 = ret_pct(closes, 60)
        r20 = ret_pct(closes, 20)
        r10 = ret_pct(closes, 10)

        f = lambda v: f"{v:+7.1f}%" if v is not None else "    n/a"
        print(f"{code:8s} {name:8s} {stage:12s} {ch:10s} {wta:>4s} {struct:>5s} "
              f"{ma50/ma200*100:8.1f}% {slope_60:+8.2f} {trend_30:+8.2f} "
              f"{f(r60):>8s} {f(r20):>8s} {f(r10):>8s}")

    print()
    print("图例: WTA = 4 判据全满足; 结构 = 收盘突破箱体阻力")
    print("      slope60/trend30 = 60日斜率% / 30日涨跌幅%;  60日/20日/10日 = 判定前实际涨跌幅")


if __name__ == "__main__":
    main()
