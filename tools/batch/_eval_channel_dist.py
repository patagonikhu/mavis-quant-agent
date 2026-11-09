"""生产侧 channel 字段验证: 分布 + 交叉校验

跑法: bash tools/with_venv.sh python -m tools.batch._eval_channel_dist

校验点:
  1. channel 字段是否真的出现在 wyckoff_judge 返回值里 (读生产输出, 不反推)
  2. channel 分布
  3. channel 与 scores 的语义一致性 (fallback 只能给 1 分, main 给 >=1)
  4. Accumulation 弱信号: weak_hit 与 channel 后缀是否一致, 且
     weak_hit 必须落在 {A,B,C} 里, 且至少有一条 weak_*_ok 为真
  5. Markup 多值 channel: main_weak+main_extra 组合必须真的同时满足两条
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collections import Counter

import pandas as pd

from tools.storage.store import DataStore
from tools.batch.wyckoff_a2m_scan import (
    _load_all_klines, _filter_by_st, load_codes_all_market, load_stock_basic,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events


def main():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = DataStore.filter_codes_by_pe(codes, 60.0)
    codes = DataStore.filter_codes_by_quality(codes, or_yoy_min=0, np_yoy_min=0,
                              roe_min=5.0, gm_min=20.0)
    codes = DataStore.filter_codes_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)
    # 2026-11-03 改: K 线口径必须跟生产 (t_analyze_one → DataStore.get_ctx) 一致,
    #   即 config kline_days (1300) 窗口。之前用 _load_all_klines(limit=2000) 拿到
    #   更长的历史 → sub_event 集合不同 → 判定结果不同 (实测 000089: 1192 根无 LPS
    #   → 弱信号全 False → stage='?'; 1544 根有 LPS → main_weak_A)。
    all_kl = {}
    for idx, c in enumerate(codes):
        try:
            _ctx = DataStore.get_ctx(c, kline_only=True)
        except Exception:
            continue
        if not _ctx.kline:
            continue
        _df = pd.DataFrame(_ctx.kline)
        _df['date'] = _df['trade_date']
        all_kl[c] = _df
        if idx % 50 == 0:
            print(f"  loading {idx}/{len(codes)}", flush=True)
    print(f"载入 K 线 {len(all_kl)} 只 (生产口径)", flush=True)
    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    chan_cnt = Counter()
    stage_cnt = Counter()
    total = 0
    errs = []
    weak_rows = []
    combo_rows = []

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
        subs = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
                               period_label="daily", code=code, as_of_idx=None,
                               dates=dates_, precomputed_arrs=arrs)
        i = n - 1
        sub_set = {e["name"] for e in subs if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_set, window=arrs['window'], period_label="daily")
        if out is None:
            continue
        total += 1
        stage = out.get("stage", "?")
        stage_cnt[stage] += 1

        # --- 校验点 1: channel 字段存在 ---
        if "channel" not in out:
            errs.append(f"{code}: 返回值缺 channel 字段")
            continue
        ch = out.get("channel") or ""
        chan_cnt[f"{stage}|{ch}"] += 1
        if not ch:
            errs.append(f"{code}: channel 为空 (stage={stage})")

        scores = out.get("scores", {}) or {}
        score = scores.get(stage, 0)

        # --- 校验点 3: fallback 只能给 1 分 ---
        if ch.startswith("fallback") and score != 1:
            errs.append(f"{code}: {ch} 但 score={score} (fallback 应恒为 1)")

        # --- 校验点 4: Accumulation 弱信号 ---
        if stage == "Accumulation":
            ad = out.get("accum_detail", {}) or {}
            for k in ("weak_A_ok", "weak_B_ok", "weak_C_ok", "weak_hit"):
                if k not in ad:
                    errs.append(f"{code}: accum_detail 缺 {k}")
            wA = bool(ad.get("weak_A_ok"))
            wB = bool(ad.get("weak_B_ok"))
            wC = bool(ad.get("weak_C_ok"))
            hit = ad.get("weak_hit") or ""
            if ch.startswith("main_weak_"):
                suf = ch[len("main_weak_"):]
                if suf != hit:
                    errs.append(f"{code}: channel 后缀 {suf} != weak_hit {hit}")
                if hit not in ("A", "B", "C"):
                    errs.append(f"{code}: weak_hit 非法 {hit!r}")
                okmap = {"A": wA, "B": wB, "C": wC}
                if not okmap.get(hit):
                    errs.append(f"{code}: weak_hit={hit} 但该条 weak_*_ok=False")
                # elif 语义: 命中分支之前不能有成立的
                order = ["A", "B", "C"]
                pos = order.index(hit)
                for prev in order[:pos]:
                    pv = {"A": wA, "B": wB, "C": wC}[prev]
                    if pv:
                        errs.append(f"{code}: elif 顺序违例 — {prev} 成立却命中 {hit}")
            elif ch == "main_strict":
                if not ad.get("pass_all"):
                    errs.append(f"{code}: main_strict 但 pass_all=False")
            elif ch == "fallback2_accum":
                if hit:
                    errs.append(f"{code}: fallback2_accum 但 weak_hit={hit!r} (应为空)")
            weak_rows.append((code, name_by_code.get(code, code), ch, wA, wB, wC, hit,
                               ad.get("pass_all")))

        # --- 校验点 5: Markup 多值 channel ---
        if stage == "Markup" and "+" in ch:
            fv = out.get("factor_values", {}) or {}
            parts = ch.split("+")
            combo_rows.append((code, name_by_code.get(code, code), ch, score,
                               fv.get("above_ma200"), fv.get("ma_gap_pct_above"),
                               fv.get("dev_ma20"), fv.get("pos_pct")))
            if "main_extra" in parts and "main_weak" in parts:
                if not (fv.get("above_ma200") and (fv.get("ma_gap_pct_above") or 0) > 0.5):
                    errs.append(f"{code}: channel={ch} 但 main_weak 条件不满足")
            if "main_extra" in parts:
                if not ((fv.get("dev_ma20") or 0) > 5 and (fv.get("pos_pct") or 0) > 60):
                    errs.append(f"{code}: channel={ch} 但 main_extra 条件不满足")
            if "main_weak" in parts and "main_strict" in parts:
                errs.append(f"{code}: channel={ch} 严格门与弱信号互斥, 不应同时出现")

    print(f"\n{'='*100}")
    print(f"channel 分布 (全池 {total} 只)")
    print('='*100)
    print(f"  {'stage | channel':46s} {'只数':>6s} {'占比':>8s}")
    print("-"*100)
    for k, v in chan_cnt.most_common():
        print(f"  {k:46s} {v:6d} {v/total*100:7.2f}%")

    print(f"\n{'='*100}")
    print(f"stage 分布")
    print('='*100)
    for k, v in stage_cnt.most_common():
        print(f"  {k:16s} {v:6d} {v/total*100:7.2f}%")

    print(f"\n{'='*100}")
    print(f"Accumulation 弱信号 channel 分布")
    print('='*100)
    wc = Counter(r[2] for r in weak_rows)
    for k, v in wc.most_common():
        print(f"  {k:24s} {v:6d}")

    print(f"\n{'='*100}")
    print(f"Markup 多值 channel 组合 ({len(combo_rows)} 只)")
    print('='*100)
    for r in combo_rows[:30]:
        print(f"  {r[0]} {r[1]:12s} {r[2]:32s} score={r[3]} "
              f"above200={r[4]} gap={r[5]} dev20={r[6]} pos={r[7]}")

    print(f"\n{'='*100}")
    print(f"校验结果: {len(errs)} 条错误")
    print('='*100)
    for e in errs[:40]:
        print(f"  ❌ {e}")
    if not errs:
        print("  ✅ 全部通过")


if __name__ == "__main__":
    main()
