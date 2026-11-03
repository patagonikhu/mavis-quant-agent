"""重构成败验证: 新 markup_entry_conditions() vs 旧 detect_markup_entry() 逐根 100% 等价

背景:
  2026-11-03 把 detectors/markup_entry.py 的判定逻辑抽成 markup_entry_conditions()
  (返回 4 条判据逐条结果), detect_markup_entry() 变成薄封装 (返回 hit)。
  报告渲染改为直接读 conditions, 不再自己手算一遍。

风险: 重构过程中可能改了 early-return 语义 / 索引偏移 / 容差口径。

方法:
  1. 从 git HEAD 取出**旧实现** (/tmp/old_markup_entry.py, 或用 --old-file 传入)
  2. 对生产候选池每只票的**每一根** K 线 (近 400 根), 跑新旧两版
  3. 断言 bool 结果 100% 一致; 同时统计 4 条判据各自的命中率

跑法: bash tools/with_venv.sh python -m tools.batch._check_markup_entry_refactor
"""
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

import pandas as pd

from tools.storage.store import DataStore
from tools.batch.wyckoff_a2m_scan import (
    _filter_by_st, _filter_by_pe, _filter_by_quality,
    _filter_by_leverage, load_codes_all_market,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.detectors.markup_entry import (
    detect_markup_entry, markup_entry_conditions,
)


def _load_old():
    """加载 git HEAD 的旧实现"""
    old_path = Path("/tmp/old_markup_entry.py")
    if not old_path.exists():
        old_path.write_bytes(subprocess.check_output(
            ["git", "-C", str(ROOT), "show",
             "HEAD:tools/factors/wyckoff/detectors/markup_entry.py"]))
    # 相对 import -> 绝对 import, 否则 spec 加载时无父包
    src = old_path.read_text()
    src = src.replace("from ..helpers import",
                      "from tools.factors.wyckoff.helpers import")
    src = src.replace("from .", "from tools.factors.wyckoff.detectors.")
    patched = Path("/tmp/old_markup_entry_patched.py")
    patched.write_text(src)
    spec = importlib.util.spec_from_file_location("_old_markup_entry", patched)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.detect_markup_entry


def main():
    old_detect = _load_old()

    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = _filter_by_pe(codes, 60.0)
    codes = _filter_by_quality(codes, or_yoy_min=0, gm_min=0, np_yoy_min=0,
                                roe_min=5.0, gm_abs_min=20.0)
    codes = _filter_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)

    n_checked = 0
    n_new_true = 0
    n_old_true = 0
    mismatch = []
    cond_cnt = {"ma50_reached": 0, "golden_cross": 0, "above_5d": 0, "angle_ok": 0}

    for idx, code in enumerate(codes):
        try:
            ctx = DataStore.get_ctx(code, kline_only=True)
        except Exception:
            continue
        if not ctx.kline:
            continue
        df = pd.DataFrame(ctx.kline)
        c = df['close'].tolist()
        h = df['high'].tolist()
        l = df['low'].tolist()
        v = df['vol'].tolist()
        o = df['open'].tolist() if 'open' in df.columns else c
        n = len(c)
        if n < 250:
            continue
        arrs = build_kline_features(c, h, l, v, window=min(250, n))
        # 扫近 400 根 (覆盖 warm-up 边界 i=210 附近)
        for i in range(max(0, n - 400), n):
            old = bool(old_detect(c, h, l, v, o, i, precomputed_arrs=arrs))
            new = bool(detect_markup_entry(c, h, l, v, o, i, precomputed_arrs=arrs))
            n_checked += 1
            n_old_true += int(old)
            n_new_true += int(new)
            if old != new:
                if len(mismatch) < 20:
                    mismatch.append((code, i, old, new))
            if i == n - 1:
                cd = markup_entry_conditions(c, h, l, v, o, i, precomputed_arrs=arrs)
                for k in cond_cnt:
                    cond_cnt[k] += int(bool(cd[k]))
        if idx % 100 == 0:
            print(f"  {idx}/{len(codes)}", flush=True)

    print()
    print(f"逐根比对次数: {n_checked:,}")
    print(f"旧实现 True: {n_old_true:,} ({n_old_true/max(n_checked,1)*100:.3f}%)")
    print(f"新实现 True: {n_new_true:,} ({n_new_true/max(n_checked,1)*100:.3f}%)")
    print(f"最后一根 K 线上 4 条判据命中率:")
    for k, v in cond_cnt.items():
        print(f"  {k}: {v}")
    print()
    if mismatch:
        print(f"❌ 不一致 {len(mismatch)} 处 (前 20):")
        for m in mismatch:
            print(f"  {m}")
        sys.exit(1)
    print("✅ 新旧实现 100% 一致")


if __name__ == "__main__":
    main()
