"""A→M 命中清单 (只算命中, 不落盘详报)

复用 wyckoff_a2m_scan 的生产判定路径 (_process_one) + 生产过滤链,
只跑扫描不跑 7 section 详报, 用于评估 stage_factor 改动的影响。

跑法: bash tools/with_venv.sh python -m tools.batch._eval_a2m_hits
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore

import multiprocessing as mp

from tools.batch.wyckoff_a2m_scan import (
    _filter_by_st, _load_all_klines, _process_one, load_codes_all_market,
)


def main():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = DataStore.filter_codes_by_pe(codes, 60.0)
    codes = DataStore.filter_codes_by_quality(codes, or_yoy_min=0, np_yoy_min=0,
                                roe_min=5.0, gm_min=20.0)
    codes = DataStore.filter_codes_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)

    klines = _load_all_klines(codes)
    print(f"K 线载入 {len(klines)}", flush=True)

    with mp.Pool(processes=max(1, mp.cpu_count() - 1)) as pool:
        results = pool.starmap(
            _scan_one, [(c, klines.get(c)) for c in codes], chunksize=8
        )

    hits = [r for r in results if r and "code" in r and "error" not in r]
    errs = [r for r in results if r and "error" in r]
    print(f"命中 {len(hits)}  异常 {len(errs)}")
    for r in sorted(hits, key=lambda x: x["code"]):
        print(f"  {r['code']}  trans={r['trans_date']}  "
              f"m_ratio={r['m_ratio']:.3f}  last={r['last_date']}  "
              f"close={r['last_close']}")


def _scan_one(code, df):
    try:
        return _process_one(code, df)
    except Exception as e:
        return {"code": code, "error": f"{type(e).__name__}: {e}"}


if __name__ == "__main__":
    main()
