"""
wyckoff/stage_factor.py - 威科夫 3 大阶段 (Accumulation / Markup / Distribution)

v5.4 重写 (2026-07-28): 从 tools.wyckoff 拆过来, 跟 WyckoffTradingAgent 1:1
- 9 detector: tools.factors.wyckoff.detectors
- 13 helper: tools.factors.wyckoff.helpers
- 评分: 内联 (跟原 wyckoff_stage_v2 评分逻辑 1:1, 保证 confidence 数值一致)

输入: K 线 DataFrame (close/high/low/volume/open/pct_chg)
输出: dict { 阶段, 含义, 操作, 置信度, 趋势_30日_pct, sub_events, sub_event_count, 判定,
              stage, stage_name, action, confidence, stage_detail, phase_progress, scores,
              accum_detail, distribution_detail, markup_detail, signals }
"""
import statistics
from typing import List, Optional
import pandas as pd
# 2026-09-09 改: tools.factors.base 已删, 临时定义个本地占位 Factor 类
# WyckoffStageFactor 继承这个 (Wyckoff 专用, 不在 factor-guard 范围, 保留 class 形式)
class Factor:
    """占位 Factor 基类 (2026-09-09 占位, 仅 Wyckoff 用)

    真实 Factor 基类在 tools.factors/base.py, 2026-09-09 删除.
    Wyckoff 是专用复杂框架, 保留 class 形式 (不受 factor-guard 约束).
    """
    name: str = ""
    category: str = ""
    dependencies: list = []
    description: str = ""
    version: str = "1.0"
    output_type: str = "series"
    def compute(self, df, **kwargs):
        raise NotImplementedError
from tools.factors.wyckoff.helpers import _find_range, _ma50_reached_ma200
from tools.factors.wyckoff.detectors import scan_sub_events


# ============================================================================
# Markup 判定 — 1:1 对齐 WyckoffTradingAgent/core/wyckoff_engine.py::_detect_markup_entry
# ============================================================================
#
# 参考实现 (Layer 2.5) 的 4 条判据, 必须**全满足**:
#   ① 当前 MA50 够到 MA200                           (0.90 容差, 见 _ma50_reached_ma200)
#   ② 近 lookback(=10) 日内发生过 MA50 上穿 MA200      (金叉, 同一容差口径)
#   ③ 最近 confirm_days(=5) 日, MA50 连续够到 MA200   (同一容差口径)
#   ④ MA50 近 5 日角度 >= 2.0%                        (markup_ma_angle_min)
#
# 与参考实现的差异 (有意为之, 已实测):
#   - 参考实现跑在已过 L1/L2/L3 漏斗的池子上 (本身就是强过滤);
#     本项目的 fallback2 是"主路径分数全 0 时的兜底", 地位不同。
#   - 参考实现用严格 `MA50 > MA200`; 本项目统一用 0.90 容差
#     (MA50 追到年线 90% 即算数), 口径集中定义在 helpers._ma50_reached_ma200
#     (常量 MA50_MA200_TOLERANCE = 0.90)。
#   - 所以这里做成**可开关的判定器**, 由 wyckoff_judge 统一调用,
#     而不是替换掉整个评分体系。
#
MARKUP_CONFIRM_DAYS = 5       # 对齐 cfg.markup_ma_crossover_confirm_days
MARKUP_CROSS_LOOKBACK = 10    # 对齐 max(confirm_days*2, 10)
MARKUP_ANGLE_MIN = 2.0        # 对齐 cfg.markup_ma_angle_min


def detect_markup_entry(i, arrs, window=None):
    """Markup 趋势判定 — 1:1 对齐参考实现 _detect_markup_entry。

    返回 dict:
        {
          "hit":        bool,   # 4 条判据是否全满足
          "score":      float,  # recent_above / confirm_days (对齐参考实现返回值)
          "ma50_gt":    bool,   # ① MA50 > MA200
          "cross":      bool,   # ② 近 10 日发生过金叉
          "sustained":  bool,   # ③ 最近 5 日 MA50 连续 > MA200
          "angle_ok":   bool,   # ④ MA50 近 5 日角度 >= 2%
          "angle_5d":   float,
          "above_days": int,    # 最近 5 日里 MA50 > MA200 的天数
          "ma50":       float,
          "ma200":      float,
        }
    """
    closes = arrs['closes']
    n = len(closes)
    ma50_arr = arrs['ma50']
    ma200_arr = arrs['ma200']

    # 参考实现: len(df) < max(ma_long=200, confirm_days) + 5 → None
    #           即至少 205 根 K 线 (200 日均线 warm-up + 5 日确认)
    if n < max(205, MARKUP_CONFIRM_DAYS + 5) or i < 205:
        return None

    ma50 = ma50_arr[i]
    ma200 = ma200_arr[i]

    # ① MA50 够到 MA200 (带 0.90 容差, 口径统一见 helpers._ma50_reached_ma200)
    ma50_gt = _ma50_reached_ma200(ma50, ma200)

    # ② 近 lookback 日内发生过 MA50 上穿 MA200 (同样用 0.95 容差口径)
    cross = False
    lo = max(1, i - MARKUP_CROSS_LOOKBACK + 1)
    for j in range(lo, i + 1):
        if (not _ma50_reached_ma200(ma50_arr[j - 1], ma200_arr[j - 1])
                and _ma50_reached_ma200(ma50_arr[j], ma200_arr[j])):
            cross = True
            break

    # ③ 最近 confirm_days 日 MA50 连续够到 MA200
    above_days = sum(1 for j in range(i - MARKUP_CONFIRM_DAYS + 1, i + 1)
                     if _ma50_reached_ma200(ma50_arr[j], ma200_arr[j]))
    sustained = above_days >= MARKUP_CONFIRM_DAYS

    # ④ MA50 近 5 日角度 (参考实现: tail(6) 首尾变化率)
    ma50_5d_ago = ma50_arr[i - 5]
    angle = (ma50 / ma50_5d_ago - 1) * 100.0 if ma50_5d_ago > 0 else 0.0
    angle_ok = angle >= MARKUP_ANGLE_MIN

    hit = ma50_gt and cross and sustained and angle_ok

    return {
        "hit": hit,
        "score": float(above_days / MARKUP_CONFIRM_DAYS),
        "ma50_gt": ma50_gt,
        "cross": cross,
        "sustained": sustained,
        "angle_ok": angle_ok,
        "angle_5d": angle,
        "above_days": above_days,
        "ma50": ma50,
        "ma200": ma200,
    }


def wyckoff_judge(i, arrs, sub_events, window, period_label,
                  ma_short=20, ma_long=60, pos_lookback=60):
    """威科夫阶段判定 — O(1) per call，使用 kline_arrays.build_kline_features() 返回的预算数组。

    参数:
        i:           绝对 K 线索引（0-based）
        arrs:        kline_arrays.build_kline_features() 返回的 dict
        sub_events:  该日期之前的 sub_events 名称集合（set[str]）
        window:      Wyckoff 回看窗口（与 arrs['window'] 一致即可）

    算法源: WyckoffTradingAgent/core/wyckoff_engine.py 1:1 搬运, 入参 kline_arrays
    预计算后所有滑动窗口操作变 O(1), 比 _judge_full 慢路径快 3800-10000x.
    """
    closes = arrs['closes']
    lows   = arrs['lows']
    n_bars = len(closes)
    if i < 1 or i >= n_bars:
        return None

    p     = closes[i]
    ma20  = arrs['ma20'][i]
    ma50  = arrs['ma50'][i]
    ma60  = arrs['ma60'][i]
    # ma200 = window 均线, 当 i < 199 时退化为 ma60
    ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]
    # 2026-11-03 加: 真 MA200 (arrs['ma200'] = sliding_ma(closes, 200)).
    #   本项目的 "ma200" 变量是 window(250) 均线, 不是真的 200 日均线;
    #   而参考实现 WyckoffTradingAgent 的 ma_long=200 用的是 rolling(200).
    #   Markup 判定的 4 条判据全部要跟 MA200 比, 所以这里必须用真 MA200。
    ma200_true = arrs['ma200'][i]
    # 2026-11-03 修 BUG (MA 基准混用): ma_gap_pct_above / above_ma200 原来用 ma200
    #   (window=250 均线), 但同一组 Markup 判据里的金叉 / 持续上方用的是**真 MA200**
    #   → 同一个判定块里混了两个 MA200 源, 报告里也会出现「ma200_prev=真MA200
    #   而 ma200=250均线」这种同格两个基准的情况。
    #   统一改用真 MA200。warm-up (i < 199, MA200 还没满 200 根) 时退回 ma60,
    #   与 ma200 变量本身的降级策略一致。
    ma200_gap = ma200_true if i >= 199 else ma60

    dev_ma20  = (p / ma20  - 1) * 100 if ma20  > 0 else 0.0
    dev_ma60  = (p / ma60  - 1) * 100 if ma60  > 0 else 0.0
    dev_ma200 = (p / ma200 - 1) * 100 if ma200 > 0 else 0.0

    slope_60 = arrs['slope_60'][i]
    trend_30 = arrs['trend_30'][i]

    recent_low  = arrs['rmin_l_pos'][i]
    recent_high = arrs['rmax_h_pos'][i]
    pos_pct = (
        (p - recent_low) / (recent_high - recent_low) * 100
        if recent_high > recent_low > 0 else 50.0
    )

    # MA 金叉 / 持续上方
    # 2026-11-03: ma200_prev / golden_cross_recent 统一改用**真 MA200** (arrs['ma200']),
    #   不再用 arrs['ma_window'] (window=250 均线) —— 跟 above_ma200_sustained 保持一致,
    #   也跟参考实现 WTA 的 close.rolling(200).mean() 一致。
    ma50_prev  = arrs['ma50'][i - 1]  if i >= 1   else ma50
    # 2026-11-03 修 BUG (MA 基准混用): else 分支原来退回 `ma200` (250 均线),
    #   但 ma200_prev 的正常分支是真 MA200 → 同一变量两个基准。
    #   统一退回真 MA200。
    ma200_prev = arrs['ma200'][i - 1] if i >= 200 else ma200_true
    # 2026-10-19 对齐 WTA: 当前 MA50 必须仍在 MA200 上方 (guard), 窗口 5→10 天,
    #   above_ma200_sustained 语义改为 MA50>MA200 (无容差), 与 markup_entry.py 路径统一
    golden_cross_recent = False
    # 2026-11-03 修 BUG: guard 原来比的是 250 均线 (ma200), 而循环体内用的是
    #   真 MA200 (arrs['ma200']) → guard 和被检条件不是同一个基准, 金叉被误挡。
    #   统一改用真 MA200。
    if i >= 2 and i >= 200 and ma50 > ma200_true:   # guard: 当前仍须在上方
        # 近 10 日内任一天发生金叉即 True
        for j in range(max(1, i - 9), i + 1):
            if (arrs['ma50'][j - 1] <= arrs['ma200'][j - 1]
                    and arrs['ma50'][j] > arrs['ma200'][j]):
                golden_cross_recent = True
                break

    confirm_days = 5
    # 2026-11-03 修 BUG: 原来比的是 250 均线 (ma200), 但同一组判据里的
    #   above_ma200_sustained / golden_cross_recent 用的是真 MA200。
    #   统一改用 ma200_gap (真 MA200, warm-up 退回 ma60)。
    above_ma200 = p > ma200_gap
    # 2026-11-03 修 BUG: 原来比的是 arrs['ma_long'] (= MA60!) 而不是 MA200.
    #   注释写"MA50 > MA200"但代码用 ma_long, 语义与注释不符 → 134/443 只候选票判定错误
    #   (含持仓 002415 海康威视). 现在改用真 MA200 (arrs['ma200']).
    if i >= 205:   # 需要 200 日均线 warm-up + 5 日确认窗口
        above_ma200_sustained = all(
            arrs['ma50'][j] > arrs['ma200'][j]
            for j in range(i - confirm_days + 1, i + 1)
        )
    else:
        above_ma200_sustained = above_ma200

    # 量能
    vol_5  = arrs['vol5'][i]
    vol_60 = arrs['vol60'][i]
    vol_shrink = vol_5 < vol_60 * 0.5 if vol_60 > 0 else False

    vol_3  = arrs['vol3'][i]
    dist_confirm_days = 3
    vol_dry_3d_consecutive = (
        vol_3 < vol_60 * 0.5
        if vol_60 > 0 and i >= 60 + dist_confirm_days
        else vol_shrink
    )

    # 评分初始化
    score_Accumulation = score_Markup = score_Distribution = 0
    stage_detail = ""
    accum_base_low_ok = accum_ma_gap_ok = accum_volume_ok = False
    accum_b_test_count_n = 0
    accum_c_ok = accum_abc_pass = False
    recent_20_low = None  # 2026-10-17: 默认 None, 防止 fallback 路径下 i<20 时 UnboundLocalError
    period_low = 0.0  # 2026-10-17: 默认 0.0, 防止 K 线 < 30 时 UnboundLocalError (2026-10-17 报 688758)
    # 2026-10-19 改名 (明示窗口数):
    #   vol_recent_avg (近 20d 均量)  / vol_baseline_avg (基线 100d 均量) = 缩量比
    #   K 线 < 120 根 fallback 短窗: vol_recent_avg = vol10 (10d) / vol_baseline_avg = vol_ref_30_10 (20d)
    #   详报里看到的 "近 20d/基线 100d" 是 99% 走长窗的情况, 新股短 K 线才走 fallback
    vol_recent_avg = 0.0
    vol_baseline_avg = 0.0

    # Accumulation 累积
    if i >= 30:
        period_low = arrs['rmin_l_250'][i]
        accum_base_low_ok = period_low > 0 and p <= period_low * 1.45

        if ma200 > 0:
            accum_ma_gap_ok = abs(ma50 - ma200) / ma200 * 100 <= 8

        # 量比双窗: 长窗 20d/100d 优先, 短窗 10d/20d fallback (K 线 < 120 根)
        if i >= 120:
            vol_recent_avg   = arrs['vol20'][i]              # 近 20d 均量
            vol_baseline_avg = arrs['vol_ref_120_20'][i]    # 基线 100d 均量 (i-120 ~ i-20, 长度 100)
        elif i >= 40:
            vol_recent_avg   = arrs['vol10'][i]              # 近 10d 均量 (fallback 短窗)
            vol_baseline_avg = arrs['vol_ref_30_10'][i]     # 基线 20d 均量 (fallback)
        else:
            vol_recent_avg = vol_baseline_avg = 0.0

        if vol_baseline_avg > 0 and vol_recent_avg / vol_baseline_avg < 0.75:
            accum_volume_ok = True

        # B 阶段底部测试次数 — O(60) 有界常数
        if accum_base_low_ok:
            start_j = max(0, i - 59)
            accum_b_test_count_n = sum(
                1 for j in range(start_j, i + 1)
                if period_low > 0 and abs(lows[j] - period_low) / period_low <= 0.05
            )

        if accum_base_low_ok and i >= 20:
            recent_20_low = arrs['rmin_l_20'][i]
            if recent_20_low >= period_low * 0.97 and vol_baseline_avg > 0 and vol_recent_avg / vol_baseline_avg < 0.75:
                accum_c_ok = True

        if accum_base_low_ok and accum_ma_gap_ok and accum_volume_ok:
            accum_abc_pass = True

    # 2026-11-03: 弱信号 A/B/C 抽成具名变量。
    #   三条是 **OR 关系** (任一成立即 +1 分), 但执行上是 elif 链 → 只命中第一条。
    #   渲染器以前自己复刻这三个条件, 跟生产逻辑可能漂移 (跟 fallback 标签出错的教训同源),
    #   现在把**真实判定结果**下沉到 accum_detail, 报告直接读。
    _has_accum_ev = any(e in sub_events for e in ('Spring', 'LPS', 'EVR'))
    accum_weak_A_ok = abs(slope_60) < 8 and 25 < pos_pct < 75 and _has_accum_ev
    accum_weak_B_ok = pos_pct < 30 and dev_ma200 < -5 and _has_accum_ev
    accum_weak_C_ok = pos_pct < 30 and dev_ma200 < -5 and len(sub_events) >= 1
    # 实际命中分支 (elif 链语义: A→B→C 顺序, 只记第一条成立)
    accum_weak_hit = ""
    if accum_abc_pass:
        pass  # 严格 AND 门命中, 弱信号链根本没执行
    elif accum_weak_A_ok:
        accum_weak_hit = "A"
    elif accum_weak_B_ok:
        accum_weak_hit = "B"
    elif accum_weak_C_ok:
        accum_weak_hit = "C"

    if accum_abc_pass:
        score_Accumulation += 3
        if any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
            score_Accumulation += 2
        if dev_ma20 < 5 and dev_ma60 < 0:
            score_Accumulation += 1
    elif accum_weak_A_ok:
        score_Accumulation += 1
    elif accum_weak_B_ok:
        score_Accumulation += 1
    elif accum_weak_C_ok:
        score_Accumulation += 1

    if accum_abc_pass:
        if accum_b_test_count_n >= 3:
            stage_detail = 'Accum_C' if accum_c_ok else 'Accum_B'
        else:
            stage_detail = 'Accum_A'

    # Markup 主升浪
    # 2026-11-03 修 BUG (MA 基准混用): ma_gap_pct_above 原来用 ma200 (window=250 均线),
    #   而同一组判据里的金叉 / 持续上方用真 MA200 → 同一 AND 门里两个 MA200 源。
    #   统一改用 ma200_gap (真 MA200)。
    ma_gap_pct_above = (p - ma200_gap) / ma200_gap * 100 if ma200_gap > 0 else 0.0
    # 2026-11-03 修 BUG (分母写错): ma50_angle_5d 原来分母是
    #   sum(closes[i-54:i-49])/5 —— 那是「5 天前的收盘均价」, **不是 MA50**。
    #   报告里印的公式却是 (ma50[i]/ma50[i-5]-1)*100, 公式和数值对不上。
    #   改成真正的 arrs['ma50'][i-5] (i>=54 保证 i-5>=49, 是完整 50 日均线)。
    ma50_angle_5d = 0.0
    ma50_5d_ago = 0.0
    if i >= 54:
        ma50_5d_ago = arrs['ma50'][i - 5]
        if ma50_5d_ago > 0:
            ma50_angle_5d = (ma50 / ma50_5d_ago - 1) * 100

    # 2026-11-03 加: 参考实现 Layer 2.5 的 Markup 判定 (4 条全满足)
    #   跟本项目原评分体系并行: 原评分算 score_Markup, 这里算独立的 hit 标志
    markup_wta = detect_markup_entry(i, arrs, window=window)
    markup_wta_hit = bool(markup_wta and markup_wta["hit"])

    is_uptrend = slope_60 > 0 and dev_ma200 > -10 and trend_30 > -15

    if golden_cross_recent and above_ma200_sustained and ma_gap_pct_above > 0.5 and is_uptrend:
        score_Markup += 3
        if 'SOS' in sub_events:
            score_Markup += 1
        if ma50_angle_5d >= 2.0:
            score_Markup += 1
    elif above_ma200 and ma_gap_pct_above > 0.5 and is_uptrend:
        score_Markup += 1
    if dev_ma20 > 5 and pos_pct > 60 and is_uptrend:
        score_Markup += 1

    # Distribution 派发
    distribution_start = dev_ma200 > 30 and vol_dry_3d_consecutive
    distribution_weak  = (
        dev_ma200 > 15 and vol_shrink
        and any(e in sub_events for e in ('DistributionStart', 'UTAD', 'EVR'))
    )
    if distribution_start:
        score_Distribution += 3
        if any(e in sub_events for e in ('DistributionStart', 'UTAD')):
            score_Distribution += 1
        if 'EVR' in sub_events:
            score_Distribution += 1
    elif distribution_weak:
        score_Distribution += 2
        if any(e in sub_events for e in ('DistributionStart', 'UTAD')):
            score_Distribution += 1
    if distribution_start and 'UTAD' in sub_events:
        stage_detail = 'UTAD'

    # 取 max stage
    scores = {
        'Accumulation': score_Accumulation,
        'Markup': score_Markup,
        'Distribution': score_Distribution,
    }
    # 2026-11-03 改 (方案 B → 方案 C 修正):
    #
    # ⚠️ 原实现把 WTA Layer 2.5 的 4 条判据当 fallback2 的门槛 —— **这是错的**:
    #   fallback2 的定位是「主路径分数全 0 时的**兜底**」, 逻辑上必须比主路径宽松,
    #   否则它永远兜不住任何东西 (实测: 加 WTA 后 fallback2 放行 **0 只** = 死代码)。
    #   参考实现里 Markup 判定是**独立一层** (L2.5, 跑在已过漏斗的池子上),
    #   跟我们的「主路径 → fallback 兜底」串行链不是一回事, 门槛不能直接套。
    #
    # 改前 fallback2: len(sub_events)>=1 + |slope_60|>5 + trend_30>5 → 判 Markup
    #   真实 bug: **完全不看 60 日方向** —— 一只「60 日跌 15%、只是近 30 日反弹」的票
    #   也会被判成主升浪。实测 10 只命中里 4 只 60 日斜率为负:
    #     000423 东阿阿胶 -10.18% / 002946 新乳业 -13.38%
    #     300357 我武生物 -8.96% / 688615 合合信息 -15.38%
    #
    # 改后 (方案 C): 保留兜底的宽松性质, 只把 **slope_60 > -20 收紧为 slope_60 > 5**
    #   → 要求 60 日方向确实向上, 跟 fallback1 的 slope_60 > 0 语义一致但更严。
    #
    # WTA 4 判据不再作为 fallback2 门槛, 但仍作为**独立信号**输出
    #   (markup_detail.wta_* / 报告「WTA Layer 2.5 判定」表), 供人工判断参考。
    #
    # ── 2026-11-03 语义归属定案 (实测 462 只生产候选池) ──
    #
    # 原始设计 (2026-07-25 commit「fix: 周线威科夫 0 个 `?` 阶段」):
    #   "弱信号 fallback 4 (避免 `?`): 有 sub_event + 明确趋势
    #    (slope > 5 → Markup, slope < -5 → Accumulation)"
    #   → Markup / Accumulation 是**互斥双分支, 靠 slope 正负号区分**,
    #     入口条件只有「有 sub_event + |slope| > 5」, **没有 trend_30 门槛**。
    #
    # ⚠️ 已确认的**结构 bug** (非语义之争, 必须修):
    #   入口条件里多了一条 `trend_30 > 5`, 导致内部分支 `elif trend_30 < -5`
    #   (判 Accumulation) **永远进不去** —— 原始设计的「跌势 → Accumulation」
    #   这条分支是死代码。当前唯一能走到 fb2 的情况是 trend_30 > 5。
    #   修法: 入口去掉 trend_30 门槛, 让两个分支真正互斥;
    #         Accumulation 分支额外要求 trend_30 < -5 (60日跌 + 30日也跌 = 真跌势)。
    #
    # 语义之争 (slope_60 > 5 vs > -20) 实测结论:
    #   对 A→M 命中集合**零影响**。分歧区 (-20 < slope_60 <= 5) 的票
    #   (000333 美的 slope=+0.07 / 603199 九华 slope=+4.76) 都走 **fallback1**
    #   —— fallback1 在串行链里优先于 fallback2, 根本轮不到 fallback2 判定。
    #   6 只命中票在两种语义下集合完全相同 (fallback1 3 + fallback2 3)。
    #   → 保留当前 `slope_60 > 5` (它拦住了历史 4 只「60日跌15%死猫反弹」误判:
    #     000423 东阿阿胶 -10.18% / 002946 新乳业 -13.38%
    #     300357 我武生物 -8.96% / 688615 合合信息 -15.38%)。
    fb2_base = (len(sub_events) >= 1 and (slope_60 > 5 or slope_60 < -5))

    # 2026-11-03: 记录**实际命中通道**。
    #   之前渲染器靠"显示侧反推"猜通道 (读 factor_values 重算一遍条件),
    #   跟生产实际分支对不上 (实测 45 只标 fallback1 的里只有 6 只真走 fallback1)。
    #   现在生产侧直接输出 channel, 渲染器只读不算。
    channel = ""

    if max(scores.values()) > 0:
        stage = max(scores, key=scores.get)
        # 主路径内部再分: 严格 AND 门 / 弱信号 A-B-C / Markup 弱信号
        if stage == 'Accumulation':
            if accum_abc_pass:
                channel = 'main_strict'
            elif accum_weak_hit:
                channel = f'main_weak_{accum_weak_hit}'
        elif stage == 'Markup':
            # ⚠️ Markup 的**加分线是独立 if**, 不是 elif —— 可以和弱信号**同时**成立
            #   (score_Markup 累加)。所以 channel 允许多值, 用 "+" 连接。
            #   写成互斥单值会把「弱信号 + 加分线」误标成只有弱信号。
            _ch = []
            if golden_cross_recent and above_ma200_sustained and ma_gap_pct_above > 0.5 and is_uptrend:
                _ch.append('main_strict')
            elif above_ma200 and ma_gap_pct_above > 0.5 and is_uptrend:
                _ch.append('main_weak')
            if dev_ma20 > 5 and pos_pct > 60 and is_uptrend:
                _ch.append('main_extra')
            channel = '+'.join(_ch) if _ch else 'none'
        elif stage == 'Distribution':
            # 同理: UTAD 加分是独立 if, 可与 distribution_start 同时成立
            _ch = []
            if distribution_start:
                _ch.append('main_strict')
            elif distribution_weak:
                _ch.append('main_weak')
            if distribution_start and 'UTAD' in sub_events:
                _ch.append('main_extra')
            channel = '+'.join(_ch) if _ch else 'none'
    elif slope_60 > 0 and dev_ma200 > -15 and _ma50_reached_ma200(ma50, ma200) and trend_30 > -10:
        stage = 'Markup'; scores['Markup'] = 1; channel = 'fallback1'
    elif fb2_base:
        if slope_60 > 5 and trend_30 > 5:
            # fallback2 → Markup: 60 日 + 30 日双向上
            # (2026-11-03: 加 slope_60 > 5 修 "60日跌但近30日反弹" 误判;
            #  实测拦住 000423 东阿阿胶 -10.18% / 002946 新乳业 -13.38%
            #        300357 我武生物 -8.96% / 688615 合合信息 -15.38%)
            stage = 'Markup'; scores['Markup'] = 1; channel = 'fallback2_markup'
        elif slope_60 < -5 and trend_30 < -5:
            # fallback2 → Accumulation: 60 日 + 30 日双向下 (真跌势 → 底部累积区)
            # 2026-11-03 修: 原入口条件带 `trend_30 > 5`, 把这个分支堵成死代码。
            #   原始设计 (2026-07-25) 明确要 "slope < -5 → Accumulation"。
            stage = 'Accumulation'; scores['Accumulation'] = 1; channel = 'fallback2_accum'
        else:
            stage = '?'; channel = 'fb2_base_unresolved'
    else:
        stage = '?'; channel = 'none'

    max_score = scores.get(stage, 0)
    confidence = int(max_score / (sum(scores.values()) or 1) * 100)

    if not stage_detail and (stage == 'Accumulation' or score_Accumulation > 0):
        sub_names = {e if isinstance(e, str) else e.get('name', '') for e in sub_events}
        if 'Spring' in sub_names:
            stage_detail = 'Accum_Spring'
        elif accum_b_test_count_n >= 3:
            stage_detail = 'Accum_B'
        elif any(n in sub_names for n in ('LPS', 'EVR')):
            stage_detail = 'Accum_A'
        else:
            stage_detail = 'Accum_Fallback'

    if stage == 'Distribution' and stage_detail == 'UTAD':
        stage_name = '派发/UTAD'
    elif stage == 'Distribution':
        stage_name = '派发'
    else:
        stage_name = {'Accumulation': '累积', 'Markup': '主升浪', '?': '未知'}.get(stage, '未知')

    actions = {
        'Accumulation': '等待 Spring 触发 (sub-event) 确认建仓',
        'Markup': '持有, 主升浪中',
        'Distribution': '减仓, 警惕 UTAD',
        '?': '数据不足',
    }
    sub_list = sorted(sub_events) if isinstance(sub_events, set) else list(sub_events)

    return {
        "stage": stage, "stage_name": stage_name, "stage_detail": stage_detail,
        "channel": channel,  # 2026-11-03: 实际命中通道 (main_strict/main_weak_X/fallback1/fallback2_*)
        "action": actions.get(stage, '—'),
        "confidence": confidence,
        "sub_events": sub_list, "sub_event_count": len(sub_list),
        "phase_progress": _phase_progress_map(stage, stage_detail, sub_list, accum_abc_pass, distribution_start),
        "scores": scores,
        "accum_detail": {
            "base_low_ok": accum_base_low_ok, "ma_gap_ok": accum_ma_gap_ok,
            "volume_dry_ok": accum_volume_ok, "b_test_count": accum_b_test_count_n,
            "c_stage_ok": accum_c_ok, "pass_all": accum_abc_pass,
            # 2026-11-03: 弱信号 A/B/C 真实判定 (OR 关系, 报告直接读, 渲染器不再复刻)
            #   weak_A_ok/weak_B_ok/weak_C_ok = 各条条件是否成立
            #   weak_hit                   = 实际拿到那 +1 分的是哪条 (A/B/C, 空 = 严格门已过或都没成立)
            "weak_A_ok": accum_weak_A_ok, "weak_B_ok": accum_weak_B_ok,
            "weak_C_ok": accum_weak_C_ok, "weak_hit": accum_weak_hit,
        },
        "distribution_detail": {
            "bias_200": dev_ma200, "bias_200_ok": dev_ma200 > 30,
            "vol_dry_3d": vol_dry_3d_consecutive, "distribution_start": distribution_start,
        },
        "markup_detail": {
            "golden_cross_recent": golden_cross_recent,
            "above_ma200_sustained": above_ma200_sustained,
            "ma_gap_pct_above": ma_gap_pct_above, "ma50_angle_5d": ma50_angle_5d,
            # 2026-11-03 加: 参考实现 Layer 2.5 的 Markup 4 条判据 + 判定依据
            #   报告可显示 "WTA Layer2.5: ✅通过 / ❌未过 (缺 MA50>MA200)" 等,
            #   避免 fallback2 判了 Markup 但看不出凭什么。
            "wta_markup_hit": markup_wta_hit,
            "wta_ma50_gt_ma200": bool(markup_wta["ma50_gt"]) if markup_wta else None,
            "wta_cross_recent": bool(markup_wta["cross"]) if markup_wta else None,
            "wta_sustained": bool(markup_wta["sustained"]) if markup_wta else None,
            "wta_angle_ok": bool(markup_wta["angle_ok"]) if markup_wta else None,
            "wta_angle_5d": round(markup_wta["angle_5d"], 2) if markup_wta else None,
            "wta_above_days": markup_wta["above_days"] if markup_wta else None,
            "wta_ma50": round(markup_wta["ma50"], 4) if markup_wta else None,
            "wta_ma200": round(markup_wta["ma200"], 4) if markup_wta else None,
        },
        # 2026-10-17 加: 把判定用到的 24+ 个核心变量的实际值打包暴露
        # 方便用户自己手算验证 (报告里会渲染成"用到的 factor 实际值"表)
        "factor_values": {
            # ── 价格基准 ──
            "p": round(p, 4),
            # ── 均线 ──
            "ma20": round(ma20, 4), "ma50": round(ma50, 4),
            "ma60": round(ma60, 4), "ma200": round(ma200, 4),
            # 2026-11-03 加: 真 MA200 (200 日均线) — ma200 变量是 window(250) 均线,
            #   跟参考实现 WTA 的 rolling(200) 不是一回事. Markup 判据用真 MA200.
            "ma200_true": round(ma200_true, 4),
            # 2026-11-03 加: ma200_gap = Markup gap / above_ma200 的统一基准 (真 MA200,
            #   warm-up i<199 退回 ma60)。跟 "ma200" (window=250 均线) 区别见渲染器标签。
            "ma200_gap": round(ma200_gap, 4),
            # 2026-11-03 加: MA50 五日前的值 (ma50_angle_5d 的分母)
            "ma50_5d_ago": round(ma50_5d_ago, 4) if ma50_5d_ago > 0 else None,
            "ma50_ma200_ratio_pct": round(ma50 / ma200_true * 100, 2) if ma200_true > 0 else None,
            "ma50_prev": round(ma50_prev, 4) if (i >= 51 and ma50_prev is not None) else None,
            "ma200_prev": round(ma200_prev, 4) if (i >= 201 and ma200_prev is not None) else None,
            # ── 偏离度 ──
            "dev_ma20": round(dev_ma20, 2), "dev_ma60": round(dev_ma60, 2),
            "dev_ma200": round(dev_ma200, 2),
            # ── 斜率 / 趋势 ──
            "slope_60": round(slope_60, 2), "trend_30": round(trend_30, 2),
            "ma50_angle_5d": round(ma50_angle_5d, 2),
            "ma_gap_pct_above": round(ma_gap_pct_above, 2),
            "ma_gap_pct": round(abs(ma50 - ma200) / ma200 * 100, 2) if ma200 > 0 else 0,
            # ── 价格位置 ──
            "recent_low": round(recent_low, 4), "recent_high": round(recent_high, 4),
            "pos_pct": round(pos_pct, 1),
            # 2026-11-03 加: 活跃 sub-event 数 (fallback2 判定用 len(sub_events) >= 1)
            #   注意: 跟顶层 sub_event_count (历史累计 sub_list) 不是一回事
            "active_sub_event_count": len(sub_events),
            "active_sub_events": sorted(sub_events),
            # ── Accumulation 用 ──
            "period_low": round(period_low, 4),
            "vol_recent_avg": round(vol_recent_avg, 2), "vol_baseline_avg": round(vol_baseline_avg, 2),
            # 量比窗口天数: 详报里直白显示 "近 20d / 基线 100d" 或 fallback "近 10d / 基线 20d"
            "vol_recent_days": 20 if i >= 120 else (10 if i >= 40 else 0),
            "vol_baseline_days": 100 if i >= 120 else (20 if i >= 40 else 0),
            "vol_ratio_accum": round(vol_recent_avg / vol_baseline_avg, 3) if vol_baseline_avg > 0 else None,
            "b_test_count": accum_b_test_count_n,
            "recent_20_low": round(recent_20_low, 4) if (recent_20_low is not None and i >= 20) else None,
            # ── Distribution 用 ──
            "vol_5": round(vol_5, 2), "vol_60": round(vol_60, 2),
            "vol_shrink": bool(vol_shrink),
            "vol_dry_3d": bool(vol_dry_3d_consecutive),
            # ── Markup / Accum 共同用 ──
            "is_uptrend": bool(is_uptrend),
            "above_ma200": bool(above_ma200),
            "golden_cross_recent": bool(golden_cross_recent),
            "above_ma200_sustained": bool(above_ma200_sustained),
        },
        "signals": [
            f"{window}根斜率({period_label}): {slope_60:+.1f}%",
            f"MA20偏({period_label}): {dev_ma20:+.1f}%",
            f"MA60偏({period_label}): {dev_ma60:+.1f}%",
            # 2026-11-03: dev_ma200 的基准是 window(250) 均线 (ma200 变量), 不是真 MA200
            #   → 标签改成 MA250, 避免和 Markup 段的真 MA200 混淆
            f"MA250偏({period_label}): {dev_ma200:+.1f}%",
            f"{window//2}根位置({period_label}): {pos_pct:.0f}%",
            f"子事件: {','.join(sub_list) if sub_list else '无'}",
        ],
        "verdict": f"{stage_name} (置信度 {confidence}%)",
        "趋势_30日_pct": trend_30,
    }


# ============================================================
# phase_progress 映射: 经典 Wyckoff 子阶段 → 进度 0-100
# 2026-10-16: wyckoff_judge 唯一入口, 这里删了 wyckoff_stage 函数式 + _judge_full 慢路径
# ============================================================
PHASE_PROGRESS_MAP = {
    # Accumulation 子阶段 (经典 Wyckoff 4 阶段 A/B/C/D)
    ('Accumulation', 'Accum_A'):        20,   # 停止下跌
    ('Accumulation', 'Accum_B'):        50,   # 吸筹区间 (≥3 次测试底部)
    ('Accumulation', 'Accum_C'):        80,   # 测试成功 (准备 Spring)
    ('Accumulation', 'Accum_Spring'):   90,   # Spring 触发: 假跌破快速收回 → 即将主升
    ('Accumulation', 'Accum_Fallback'): 35,   # 阶段确认但无强信号 (放宽 fallback)
    # Markup 主升浪
    ('Markup', ''):                     100,  # 主升进行中
    # Distribution 派发子阶段
    ('Distribution', 'UTAD'):           30,   # 派发后上探前期阻力 (顶部信号)
}

def _phase_progress_map(stage, stage_detail, sub_events, accum_abc_pass, distribution_start):
    """经典 Wyckoff 子阶段 → phase_progress 0-100"""
    # 1. 优先用 stage_detail 精确映射
    if (stage, stage_detail) in PHASE_PROGRESS_MAP:
        base = PHASE_PROGRESS_MAP[(stage, stage_detail)]
    # 2. fallback: 阶段确认但 detail 空
    elif stage == 'Accumulation':
        base = 35  # fallback 中位
    elif stage == 'Markup':
        base = 100
    elif stage == 'Distribution':
        base = 60  # fallback 中位
    else:
        return 0   # stage = '?' 数据不足

    # 3. sub_event 加成 (Spring/SOS +10, LPS/UTAD +5, 其他 +2)
    bonus = 0
    for e in sub_events:
        if e in ('Spring', 'SOS'):
            bonus += 10
        elif e in ('LPS', 'UTAD', 'MarkupEntry', 'DistributionStart'):
            bonus += 5
        else:  # EVR, TrendPullback, Compression
            bonus += 2
    bonus = min(bonus, 20)  # 最多 +20

    # 4. 子条件加成
    if stage == 'Accumulation' and accum_abc_pass:
        bonus += 10
    if stage == 'Distribution' and distribution_start:
        bonus += 10

    return min(base + bonus, 100)

