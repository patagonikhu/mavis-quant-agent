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
from tools.factors.wyckoff.helpers import _find_range
from tools.factors.wyckoff.detectors import scan_sub_events


def _mean(seq):
    """sum/len — 34x faster than statistics.mean for small lists."""
    return sum(seq) / len(seq)


def _sliding_ma(arr, n):
    """O(n) sliding-window MA of length n (warm-up uses actual count)."""
    result = []
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= n:
            s -= arr[i - n]
        result.append(s / min(i + 1, n))
    return result


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
    ma50_prev  = arrs['ma50'][i - 1]      if i >= 1   else ma50
    ma200_prev = arrs['ma_window'][i - 1] if i >= 200 else ma200
    # 2026-10-19 对齐 WTA: 当前 MA50 必须仍在 MA200 上方 (guard), 窗口 5→10 天,
    #   above_ma200_sustained 语义改为 MA50>MA200 (无容差), 与 markup_entry.py 路径统一
    golden_cross_recent = False
    if i >= 2 and i >= 200 and ma50 > ma200:   # guard: 当前仍须在上方
        # 近 10 日内任一天发生金叉即 True
        for j in range(max(1, i - 9), i + 1):
            if (arrs['ma50'][j - 1] <= arrs['ma_window'][j - 1]
                    and arrs['ma50'][j] > arrs['ma_window'][j]):
                golden_cross_recent = True
                break

    confirm_days = 5
    above_ma200 = p > ma200
    if i >= ma_long + confirm_days:
        # 对齐 WTA: MA50 > MA200 (无容差), 不用 price vs ma_long×0.98
        above_ma200_sustained = all(
            arrs['ma50'][j] > arrs['ma_long'][j]
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

    if accum_abc_pass:
        score_Accumulation += 3
        if any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
            score_Accumulation += 2
        if dev_ma20 < 5 and dev_ma60 < 0:
            score_Accumulation += 1
    elif abs(slope_60) < 8 and 25 < pos_pct < 75 and any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
        score_Accumulation += 1
    elif pos_pct < 30 and dev_ma200 < -5 and any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
        score_Accumulation += 1
    elif pos_pct < 30 and dev_ma200 < -5 and len(sub_events) >= 1:
        score_Accumulation += 1

    if accum_abc_pass:
        if accum_b_test_count_n >= 3:
            stage_detail = 'Accum_C' if accum_c_ok else 'Accum_B'
        else:
            stage_detail = 'Accum_A'

    # Markup 主升浪
    ma_gap_pct_above = (p - ma200) / ma200 * 100 if ma200 > 0 else 0.0
    ma50_angle_5d = 0.0
    if i >= 54:
        ma50_5d_ago = sum(closes[i - 54:i - 49]) / 5
        if ma50_5d_ago > 0:
            ma50_angle_5d = (ma50 / ma50_5d_ago - 1) * 100

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
    if max(scores.values()) > 0:
        stage = max(scores, key=scores.get)
    elif slope_60 > 0 and dev_ma200 > -15 and ma50 >= ma200 * 0.95 and trend_30 > -10:
        stage = 'Markup'; scores['Markup'] = 1
    elif len(sub_events) >= 1 and (slope_60 > 5 or slope_60 < -5):
        if trend_30 > 5 and slope_60 > -20:
            stage = 'Markup'; scores['Markup'] = 1
        elif trend_30 < -5:
            stage = 'Accumulation'; scores['Accumulation'] = 1
        else:
            stage = '?'
    else:
        stage = '?'

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
        "action": actions.get(stage, '—'),
        "confidence": confidence,
        "sub_events": sub_list, "sub_event_count": len(sub_list),
        "phase_progress": _phase_progress_map(stage, stage_detail, sub_list, accum_abc_pass, distribution_start),
        "scores": scores,
        "accum_detail": {
            "base_low_ok": accum_base_low_ok, "ma_gap_ok": accum_ma_gap_ok,
            "volume_dry_ok": accum_volume_ok, "b_test_count": accum_b_test_count_n,
            "c_stage_ok": accum_c_ok, "pass_all": accum_abc_pass,
        },
        "distribution_detail": {
            "bias_200": dev_ma200, "bias_200_ok": dev_ma200 > 30,
            "vol_dry_3d": vol_dry_3d_consecutive, "distribution_start": distribution_start,
        },
        "markup_detail": {
            "golden_cross_recent": golden_cross_recent,
            "above_ma200_sustained": above_ma200_sustained,
            "ma_gap_pct_above": ma_gap_pct_above, "ma50_angle_5d": ma50_angle_5d,
        },
        # 2026-10-17 加: 把判定用到的 24+ 个核心变量的实际值打包暴露
        # 方便用户自己手算验证 (报告里会渲染成"用到的 factor 实际值"表)
        "factor_values": {
            # ── 价格基准 ──
            "p": round(p, 4),
            # ── 均线 ──
            "ma20": round(ma20, 4), "ma50": round(ma50, 4),
            "ma60": round(ma60, 4), "ma200": round(ma200, 4),
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
            f"MA200偏({period_label}): {dev_ma200:+.1f}%",
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

