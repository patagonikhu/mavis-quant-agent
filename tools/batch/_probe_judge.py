"""单只票 wyckoff_judge 完整输出 (对比改前改后)

跑法: bash tools/with_venv.sh python -m tools.batch._probe_judge --codes 300864,002262
"""
import sys
import json
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from tools.storage.store import DataStore
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events


def probe(code):
    ctx = DataStore.get_ctx(code, kline_only=True)
    df = pd.DataFrame(ctx.kline)
    df['date'] = df['trade_date']
    c = df['close'].tolist()
    h = df['high'].tolist()
    l = df['low'].tolist()
    v = df['vol'].tolist()
    o = df['open'].tolist() if 'open' in df.columns else c
    pct = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
    dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
    n = len(c)
    arrs = build_kline_features(c, h, l, v, window=min(250, n))
    subs = scan_sub_events(c, h, l, v, o=o, pct_chg=pct,
                           period_label="daily", code=code, as_of_idx=None,
                           dates=dates_, precomputed_arrs=arrs)
    i = n - 1
    sub_set = {e["name"] for e in subs if e["idx"] <= i}
    r = wyckoff_judge(i, arrs, sub_set, window=arrs['window'], period_label="daily")
    print(f"\n===== {code}  n={n}  close={c[-1]:.4f} =====")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", default="300864")
    args = ap.parse_args()
    for code in args.codes.split(","):
        probe(code.strip())


if __name__ == "__main__":
    main()
