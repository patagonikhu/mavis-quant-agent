"""WTA Layer 2.5 判据 vs 本项目 Markup 主路径 —— 是否重复?

背景:
  WTA 4 判据 (stage_factor.detect_markup_entry):
    ① MA50 够到 MA200 (容差 0.90)
    ② 近 10 日内 MA50 上穿 MA200
    ③ 最近 5 日 MA50 连续够到 MA200
    ④ MA50 近 5 日角度 >= 2%
    → 全部只看 **MA50 vs MA200 均线形态**

  本项目 Markup 主路径 (stage_factor.py:316-330):
    强信号: golden_cross_recent AND above_ma200_sustained
            AND ma_gap_pct_above > 0.5 AND is_uptrend   → +3
    弱信号: above_ma200 AND ma_gap_pct_above > 0.5 AND is_uptrend → +1
    加分线: dev_ma20 > 5 AND pos_pct > 60 AND is_uptrend → +1
    → 混了 **收盘价位置 / 60日斜率 / 30日趋势 / MA20偏离 / 区间位置**

  口径差异:
    - 容差:  主路径严格 MA50 > MA200 (无容差) / WTA 用 0.90 放宽
    - 主对象: 主路径核心是 **收盘价 p vs MA200** (above_ma200 = p > ma200)
              WTA 核心是 **MA50 vs MA200**

方法: 生产口径 K 线 (DataStore.get_ctx), 对每只票跑生产 wyckoff_judge,
      从返回的 channel / markup_detail / factor_values 读真实判定结果 (不复刻条件)。

跑法: bash tools/with_venv.sh python -m tools.batch._eval_wta_vs_main
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
    _filter_by_st, _filter_by_pe, _filter_by_quality,
    _filter_by_leverage, load_codes_all_market, load_stock_basic,
)
from tools.factors.kline_arrays import build_kline_features
from tools.factors.wyckoff.stage_factor import wyckoff_judge
from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events


def main():
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = _filter_by_pe(codes, 60.0)
    codes = _filter_by_quality(codes, or_yoy_min=0, gm_min=0, np_yoy_min=0,
                                roe_min=5.0, gm_abs_min=20.0)
    codes = _filter_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)

    all_kl = {}
    for idx, c in enumerate(codes):
        try:
            ctx = DataStore.get_ctx(c, kline_only=True)
        except Exception:
            continue
        if not ctx.kline:
            continue
        df = pd.DataFrame(ctx.kline)
        df['date'] = df['trade_date']
        all_kl[c] = df
        if idx % 100 == 0:
            print(f"  loading {idx}/{len(codes)}", flush=True)
    print(f"载入 K 线 {len(all_kl)} 只 (生产口径)", flush=True)

    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    total = 0
    stage_cnt = Counter()
    chan_cnt = Counter()
    wta_cnt = 0

    # 2x2 交叉表
    cell = Counter()          # (wta_hit, markup_kind) -> 只数
    wta_but_not_markup = []   # WTA hit 但主路径/fallback 都没判 Markup
    markup_but_not_wta = []   # 判了 Markup 但 WTA 没 hit
    # 主路径 Markup 票里, WTA 4 条判据各自通过率
    mp_crit = Counter()
    mp_n = 0
    all_c = Counter()          # 全池 WTA 判据逐步收敛
    # WTA hit 但不是 Markup 的, 卡在哪
    block_cnt = Counter()

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
        chan = out.get("channel", "")
        md = out.get("markup_detail", {}) or {}
        fv = out.get("factor_values", {}) or {}
        stage_cnt[stage] += 1
        chan_cnt[chan] += 1

        wta_hit = bool(md.get("wta_markup_hit"))
        if wta_hit:
            wta_cnt += 1

        # 全池 WTA 4 条判据通过率 + 逐步收敛
        c1 = bool(md.get("wta_ma50_gt_ma200"))
        c2 = bool(md.get("wta_cross_recent"))
        c3 = bool(md.get("wta_sustained"))
        c4 = bool(md.get("wta_angle_ok"))
        if c1:
            all_c["①"] += 1
        if c2:
            all_c["②"] += 1
        if c3:
            all_c["③"] += 1
        if c4:
            all_c["④"] += 1
        if c1 and c2:
            all_c["①②"] += 1
        if c1 and c2 and c3:
            all_c["①②③"] += 1
        if c1 and c2 and c3 and c4:
            all_c["①②③④"] += 1

        # ⚠️ channel 前缀撞名: Accumulation 的弱信号也叫 'main_weak_A/B/C',
        #   Markup 的弱信号叫 'main_weak'。必须先看 stage。
        if stage == "Markup" and chan.startswith("main_"):
            kind = "main"
        elif stage == "Markup" and chan in ("fallback1", "fallback2_markup"):
            kind = "fallback"
        else:
            kind = "none"

        cell[(wta_hit, kind)] += 1

        nm = name_by_code.get(code, "")

        if wta_hit and kind == "none":
            wta_but_not_markup.append((code, nm, chan, md, fv))
            # 卡在哪
            gc = bool(md.get("golden_cross_recent"))
            sus = bool(md.get("above_ma200_sustained"))
            gap = fv.get("ma_gap_pct_above") or 0.0
            sl = fv.get("slope_60") or 0.0
            dev = fv.get("dev_ma200") or 0.0
            tr = fv.get("trend_30") or 0.0
            is_up = sl > 0 and dev > -10 and tr > -15
            if not (gc and sus):
                block_cnt["金叉/sustained 不满足"] += 1
            elif gap <= 0.5:
                block_cnt["ma_gap_pct_above <= 0.5"] += 1
            elif not is_up:
                block_cnt["is_uptrend 不满足"] += 1
            else:
                block_cnt["(其它: 走的是 Distribution?)"] += 1

        if kind != "none" and not wta_hit:
            markup_but_not_wta.append((code, nm, chan, md, fv))
            mp_n += 1
            if md.get("wta_ma50_gt_ma200"):
                mp_crit["① MA50够到MA200"] += 1
            if md.get("wta_cross_recent"):
                mp_crit["② 近10日金叉"] += 1
            if md.get("wta_sustained"):
                mp_crit["③ 连续5日在上方"] += 1
            if md.get("wta_angle_ok"):
                mp_crit["④ 角度>=2%"] += 1

    print("\n" + "=" * 78)
    print(f"有效判定 {total} 只")
    print(f"stage 分布: {dict(stage_cnt)}")
    print(f"WTA hit: {wta_cnt} 只 ({wta_cnt/max(total,1)*100:.1f}%)")
    print("\n--- 2x2 交叉表 (WTA hit × Markup 通道) ---")
    print(f"{'':<14}{'主路径 Markup':<16}{'fallback Markup':<18}{'非 Markup':<12}")
    for wh in (True, False):
        r = [cell[(wh, k)] for k in ("main", "fallback", "none")]
        print(f"WTA hit={str(wh):<8}{r[0]:<16}{r[1]:<18}{r[2]:<12}")
    tot_markup = cell[(True, "main")] + cell[(True, "fallback")] + cell[(False, "main")] + cell[(False, "fallback")]
    print(f"\nMarkup 总数 {tot_markup} 只")
    print(f"  其中 WTA 也 hit: {cell[(True,'main')] + cell[(True,'fallback')]} 只 "
          f"({(cell[(True,'main')]+cell[(True,'fallback')])/max(tot_markup,1)*100:.1f}%)")
    print(f"  其中 WTA 没 hit: {cell[(False,'main')] + cell[(False,'fallback')]} 只 "
          f"({(cell[(False,'main')]+cell[(False,'fallback')])/max(tot_markup,1)*100:.1f}%)")
    print(f"WTA hit 但不是 Markup: {cell[(True,'none')]} 只")

    print("\n--- 全池 443 只, WTA 4 条判据通过数 (逐步收敛) ---")
    for k in ("①", "②", "③", "④", "①②", "①②③", "①②③④"):
        print(f"  {k:<8} {all_c[k]:>4} / {total}  ({all_c[k]/max(total,1)*100:.1f}%)")

    print("\n--- 主路径/fallback 判 Markup 的票里, WTA 4 条判据各自通过率 ---")
    if mp_n:
        for k in ("① MA50够到MA200", "② 近10日金叉", "③ 连续5日在上方", "④ 角度>=2%"):
            print(f"  {k:<20} {mp_crit[k]:>4} / {mp_n}  ({mp_crit[k]/mp_n*100:.1f}%)")

    if block_cnt:
        print("\n--- WTA hit 但没判 Markup 的票, 卡在哪 ---")
        for k, v in block_cnt.most_common():
            print(f"  {k}: {v} 只")

    if wta_but_not_markup:
        print("\n--- WTA hit 但非 Markup (前 20) ---")
        print(f"{'代码':<8}{'名称':<12}{'stage':<14}{'channel':<18}{'gc':<5}{'sus':<6}{'gap':>8}{'slope':>8}{'dev200':>8}{'tr30':>8}")
        for code, nm, chan, md, fv in wta_but_not_markup[:20]:
            print(f"{code:<8}{nm:<12}{'':<14}{chan:<18}"
                  f"{'Y' if md.get('golden_cross_recent') else 'n':<5}"
                  f"{'Y' if md.get('above_ma200_sustained') else 'n':<6}"
                  f"{fv.get('ma_gap_pct_above', 0):>8}{fv.get('slope_60', 0):>8}"
                  f"{fv.get('dev_ma200', 0):>8}{fv.get('trend_30', 0):>8}")

    if markup_but_not_wta:
        print(f"\n--- 判 Markup 但 WTA 没 hit (前 20, 共 {len(markup_but_not_wta)} 只) ---")
        print(f"{'代码':<8}{'名称':<12}{'channel':<20}{'gc':<5}{'sus':<6}{'gap':>8}{'slope':>8}{'dev200':>8}{'tr30':>8}{'pos%':>7}")
        for code, nm, chan, md, fv in markup_but_not_wta[:20]:
            print(f"{code:<8}{nm:<12}{chan:<20}"
                  f"{'Y' if md.get('golden_cross_recent') else 'n':<5}"
                  f"{'Y' if md.get('above_ma200_sustained') else 'n':<6}"
                  f"{fv.get('ma_gap_pct_above', 0):>8}{fv.get('slope_60', 0):>8}"
                  f"{fv.get('dev_ma200', 0):>8}{fv.get('trend_30', 0):>8}{fv.get('pos_pct', 0):>7}")


if __name__ == "__main__":
    main()
