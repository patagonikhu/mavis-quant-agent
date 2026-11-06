"""
wyckoff/helpers.py - 威科夫核心 helper (跟 WyckoffTradingAgent 1:1 命名)

抽离 wyckoff.py 里的 13 个 helper 函数, 命名严格对齐 WyckoffTradingAgent/core/wyckoff_engine.py:
  - _op_from_prev_close / _is_frozen_board_day / _swing_values
  - _swing_high_points / _creek_line
  - _bias_200_exceeds_limit
  - _is_trading_range_context / _spring_support_level
  - _evr_turnover_ok / _evr_structure_ok / _evr_candidate_indexes
  - _evr_series / _evr_ref_volume_avg / _sos_series / _sos_volume_ratio / _sos_breakout_or_ma_cross
  - _compression_ohlcv / _compression_direction_ok / _compression_bias_ok / _compression_atr_ratio
  - _trend_pullback_peak_idx / _trend_pullback_vol_threshold

所有函数纯计算, 无 side effect. 9 detector 都在 detectors/ 目录, 通过这些 helper 拼装.
"""
from typing import Optional


# ============================================================
# Section 1: K 线 / 形态基础 helper (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _is_frozen_board_day(op, hi, low, cl) -> bool:
    """(WyckoffTradingAgent 1052 行) 一字板检测

    对齐 WTA: 振幅 ≤ 1% 且开收差 ≤ 1% (原版 < 0.1% 过严, 会漏近一字板)
    """
    if any(x is None for x in [op, hi, low, cl]):
        return False
    if op <= 0 or cl <= 0:
        return False
    if (hi - low) / cl * 100 <= 1.0 and abs(op - cl) / cl * 100 <= 1.0:
        return True
    return False


def _swing_values(values, *, kind: str, window: int) -> list:
    """(WyckoffTradingAgent / _price_math.py) swing 高/低点 (window 内极值)"""
    if not values or window < 1:
        return []
    if kind == "high":
        # local maxima: 比左右 window 根都大
        out = []
        for i in range(window, len(values) - window):
            if all(values[i] >= values[i-window+k] for k in range(0, window*2+1) if 0 <= i-window+k < len(values) and i-window+k != i):
                out.append(values[i])
        return out
    if kind == "low":
        out = []
        for i in range(window, len(values) - window):
            if all(values[i] <= values[i-window+k] for k in range(0, window*2+1) if 0 <= i-window+k < len(values) and i-window+k != i):
                out.append(values[i])
        return out
    return []


def _swing_high_points(high_values, window: int) -> list:
    """(WyckoffTradingAgent 1183 行) swing 高点 (index, value) 列表

    返回 [(idx, value), ...] 按 idx 升序
    """
    if not high_values or window < 1:
        return []
    points = []
    for i in range(window, len(high_values) - window):
        v = high_values[i]
        is_swing = True
        for j in range(i - window, i + window + 1):
            if j == i or j < 0 or j >= len(high_values):
                continue
            if high_values[j] > v:
                is_swing = False
                break
        if is_swing:
            points.append((i, v))
    return points


def _creek_line(swing_high_points_list):
    """(WyckoffTradingAgent 1195 行) swing high 拟合阻力线

    Returns: (slope, intercept) | None
    """
    if not swing_high_points_list:
        return None
    # 取最近 5 个 swing high
    recent = swing_high_points_list[-5:]
    if len(recent) < 2 or recent[-1][0] <= recent[0][0]:
        return None
    slope = (recent[-1][1] - recent[0][1]) / (recent[-1][0] - recent[0][0])
    rise_pct = slope / recent[-1][1] * 100.0 if recent[-1][1] > 0 else float("inf")
    if rise_pct > 30.0:  # lps_creek_max_rise_pct_per_bar 默认 30
        return None
    intercept = recent[-1][1] - slope * recent[-1][0]
    return float(slope), float(intercept)


# ============================================================
# Section 2: bias / trading range / support (跟 WyckoffTradingAgent 1:1)
# ============================================================

# MA50 相对 MA200 的容差比例。
# 语义: MA50 追到 MA200 的 90% 即视为"已接近/站上年线", 不要求严格穿越。
# 用途: 统一所有"MA50 是否站上 MA200"的判定口径, 避免各处写死产生分歧。
#   - stage_factor.py fallback1 (主升浪兜底路径)
#   - stage_factor.py::detect_markup_entry 判据 ①②③ (外部参考实现 Layer 2.5)
#   - detectors/markup_entry.py (MarkupEntry detector)
#   - report_renderer.py 报告渲染 (fallback1 判定复算 + 展示)
#
# 2026-11-03 调参轨迹 (443 只生产候选池实测):
#   容差   A→M命中              M总根数
#   0.95   4 (美的/九强/恒锋/九华)   2289
#   0.90   3 (美的/恒锋/九华)        2606   ← 当前
#   0.85   2 (美的/恒锋)             2929
#   0.80   3 (美的/恒锋/中国电研)    3068
#   0.75   5 (多出西麦食品, 净利 yoy +81.9%)  3107
#
# ⚠️ 两列方向相反, 因为 V13 的 m_ratio 上限对 M 增量高度敏感:
#   - M 总根数随容差放宽**单调增** (2289 → 3107)
#   - A→M 命中数**非单调**: 放宽容差让更多票在 40 根窗口内被判 Markup,
#     m_ratio 撑破 10% 上限 → 被 V13 当成"反复 M"踢掉
#   实测 300406 九强生物: tol=0.95 时 M数 3/40 (ratio 0.075) 过 V13,
#   tol=0.90 时 15/40 (ratio 0.375) 被踢。
MA50_MA200_TOLERANCE = 0.90


# ============================================================
# fallback1 的 trend_30 门槛 (近 31 根涨跌幅 %, 越负 = 跌得越狠)
#
# 语义: "近 31 根最多跌 X%" —— trend_30 = (closes[i]/closes[i-31]-1)*100
# 用途: stage_factor.py fallback1 判定第 4 项 + report_renderer.py 展示复算
#
# 调参轨迹 (443 只生产候选池, 标签 = 判定日**过去** 10 日涨幅 >= +3%, 0 回测):
#   门槛   放行   准确率(10日)  召回率   F1
#   >-10   63     60.3%          45.8%    52.1%   ← 旧值 (2026-08-03 拍脑袋定的)
#   >-5    54     70.4%          45.8%    55.5%   ← 当前 (F1 峰值)
#   >0     44     72.7%          38.6%    50.4%
#   >5     30     76.7%          27.7%    40.7%
#
# 为什么收紧: 旧门槛 -10 放行的 63 只里有 19 只 (30%) trend_30 < 0 (在跌),
#   这 19 只准确率只有 31.6%, 20 日涨幅仅 5.3% (≈随机噪声);
#   而 trend_30 >= 0 的 44 只准确率 72.7% / 20 日 75.0%。
#   → 门槛卡在 -5 能挡住最脏的那批 (被挡掉的 14 只里 13 只确实在跌)。
#
# ⚠️ 副作用 (2026-11-05 实测): 收紧后部分票从 fb1 掉到 fb2 (fb2 入口只看
#   sub_event + |slope_60| > 5, **不看 trend_30**) → 是**换通道**不是淘汰。
#   A→M 命中集合实测不变 (fb1 3 只 / fb2 3 只, 集合相同)。
# ============================================================
FB1_TREND30_MIN = -5.0


# ============================================================
# Markup MA50 角度阈值 (2026-11-05)
#
# 原来定义在 stage_factor.py, 报告渲染器拿不到 → 渲染器只能写死 2.0,
#   阈值一调, 报告显示就跟代码对不上 (第二份实现, 必然漂移)。
# 现在下沉到 helpers (跟 FB1_TREND30_MIN / MA50_MA200_TOLERANCE 同处),
#   生产侧与渲染侧共用同一个常量。
#
# 用途: Markup 加分项「MA50 近 5 日角度 >= 2%」
#   ⚠️ 注意它是**加分项**, 不参与 strict/weak/extra 三层门的通过与否。
# ============================================================
MARKUP_ANGLE_MIN = 2.0


# ============================================================
# 判定强度分档 (2026-11-05)
#
# 问题: Markup 判定有 3 层强弱不同的门, 报告只写「判 Markup」, 不告诉读者
#   走的是哪一档 → 600763 通策医疗 / 301109 军信股份只靠**加分线**成立
#   (连 MA200 都不看), 门槛最低, 但报告长得跟严格门命中的票一样。
#
# 单一事实源: 生产侧 stage_factor 算 strength, 渲染器只读不算
#   (同 markup_entry_conditions() 的「规则下沉」范式)。
#
# 分档口径 (按 stage 分, 由强到弱):
#   Markup:
#     strict     — 严格 AND 门全中 (金叉 + 持续 MA200 上方 + MA gap>0.5 + 上升趋势)
#     weak       — 弱信号门成立 (MA200 上方 + MA gap>0.5 + 上升趋势, 缺金叉)
#     extra_only — **仅**加分线成立 (MA20 偏离>5 + 位置>60% + 上升趋势), 不看 MA200
#     fallback   — fallback1 / fallback2 启发式路径 (主路径三层门全 0 时兜底)
#   Accumulation:
#     strict     — 累积 AND 门全中 (底部测试 + MA 胶着 + 缩量)
#     weak       — 弱信号 A/B/C 单条成立
#     fallback   — fallback2 双向下启发式 (真跌势中的底部累积区)
#   Distribution:
#     strict     — 分布起始信号成立 (MA250 偏离>30% + 连续缩量)
#     weak       — 分布弱信号 (MA250 偏离>15% + 缩量 + 派发类事件)
#     extra_only — 仅 UTAD 加分 (实际不可达: UTAD 加分要求 distribution_start 同时成立)
#
# ⚠️ channel 是**多值** ("main_weak+main_extra"), 因为 Markup/Distribution 的
#   加分线在代码里是**独立 if** —— 可以和主判据同时成立。所以分档取**最强档**:
#   strict > weak > extra_only。
# ============================================================
STRENGTH_LABEL = {
    "Markup": {
        "strict":     "强 — 严格 AND 门全中 (金叉 + 持续 MA200 上方 + MA gap>0.5% + 上升趋势)",
        "weak":       "中 — 弱信号门成立 (MA200 上方 + MA gap>0.5% + 上升趋势, 缺金叉)",
        "extra_only": "弱 — 仅加分线成立 (MA20 偏离>5% + 价格位置>60% + 上升趋势), 不看 MA200/金叉",
        "fallback":   "兜底 — fallback 启发式路径 (严格门 / 弱信号门 / 加分线均未过)",
        "none":       "未判定 — 三阶段均未给出得分",
    },
    "Accumulation": {
        "strict":     "强 — 累积 AND 门全中 (底部测试 + MA 胶着 + 缩量)",
        "weak":       "中 — 弱信号单条成立 (A / B / C 任一)",
        "fallback":   "兜底 — fallback 启发式路径 (真跌势中的底部累积区)",
        "none":       "未判定 — 三阶段均未给出得分",
    },
    "Distribution": {
        "strict":     "强 — 分布起始信号成立 (MA250 偏离>30% + 连续 3 日缩量)",
        "weak":       "中 — 分布弱信号 (MA250 偏离>15% + 缩量 + 派发类事件)",
        "extra_only": "弱 — 仅 UTAD 加分 (无严格门/弱信号)",
        "none":       "未判定 — 三阶段均未给出得分",
    },
}


# ============================================================
# 强度简写记法 (2026-11-05)
#
# 用途: 因子历史走势表「威科夫强度」列 —— 用极简记法表达阶段 + 强弱档,
#   避免每格塞一长串中文标签 (表会被撑爆, 也不便于扫读)。
#
# 记法:
#   m    — Markup 严格门 (最强)
#   m1   — Markup 弱信号门
#   m2   — Markup 加分线
#   mf1  — Markup fallback1 兜底 (4 项软条件)
#   mf2  — Markup fallback2 兜底 (sub-event + 双向上)
#   A    — Accumulation (不分强弱档, 一律 A)
#   D    — Distribution (不分强弱档, 一律 D)
#   —    — 未判定 (stage='?' 或 strength='none'/缺失)
#
# ⚠️ 两档兜底必须靠 **channel** 区分, 不能只看 strength:
#   helpers.stage_strength 把 fallback1 / fallback2_markup 都塌成 "fallback",
#   光看 strength 分不出是哪个兜底 → 必须带生产侧 channel。
#
# 单一事实源: 生产侧 helpers.strength_token, 渲染器只读不算。
# ============================================================
STRENGTH_TOKEN = {
    "Markup": {
        "strict":     "m",
        "weak":       "m1",
        "extra_only": "m2",
        "fallback":   "mf2",   # 兜底默认取 mf2; 有 channel 时按 channel 精确分流
        "none":       "—",
    },
    "Accumulation": {
        "strict":     "A",
        "weak":       "A",
        "fallback":   "A",
        "none":       "—",
    },
    "Distribution": {
        "strict":     "D",
        "weak":       "D",
        "extra_only": "D",
        "none":       "—",
    },
}

# Markup 兜底档: channel → token (2026-11-05 用户要求: fallback1 / fallback2 分开显示)
#   fallback1       — 4 项软条件 (slope_60>0 + dev_ma200>-15% + MA50够到MA200 + trend_30>-5)
#   fallback2_markup— sub-event + 60周期斜率向上 + 30周期趋势向上
FALLBACK_TOKEN = {
    "fallback1":        "mf1",
    "fallback2_markup": "mf2",
}


def strength_token(stage: str, strength: str, channel: str = "") -> str:
    """(stage, strength, channel) → 简写记法 token (m / m1 / m2 / mf1 / mf2 / A / D / —)

    ⚠️ 必须带 stage: 同一档位在不同 stage 语义不同 (Accumulation 的 weak 也显示 A)。
    ⚠️ 兜底两档必须带 channel: strength 里 fallback1 / fallback2 都叫 "fallback",
      靠 channel 才能分成 mf1 / mf2。channel 缺失时退回 strength 默认值。
    stage 未知 ('?') 或 strength 缺失/未知 → 返回 "—" (调用方直接渲染)。
    """
    if not stage or stage == "?":
        return "—"
    if stage == "Markup" and (strength or "none") == "fallback":
        parts = [p for p in (channel or "").split("+") if p]
        for p in parts:
            if p in FALLBACK_TOKEN:
                return FALLBACK_TOKEN[p]
    return STRENGTH_TOKEN.get(stage, {}).get(strength or "none", "—")


def strength_label(stage: str, strength: str) -> str:
    """(stage, strength) → 中文强度标签。

    ⚠️ 必须带 stage: 同一个 channel token 在不同 stage 语义不同
      ("main_strict" 在 Accumulation = 累积 AND 门, 在 Markup = 主升 AND 门),
      标签文案也完全不同。stage 未知 (如 '?') 时返回空串 —— 调用方据此不渲染。
    """
    return STRENGTH_LABEL.get(stage, {}).get(strength or "", "")


def stage_strength(stage: str, channel: str) -> str:
    """(stage, channel) → 判定强度档位

    Returns: "strict" / "weak" / "extra_only" / "fallback" / "none"

    ⚠️ stage 相关: 同一个 channel token 在不同 stage 下语义不同 ——
    "main_strict" 在 Accumulation 指「累积 AND 门严格通过」,
    在 Markup 指「主升 AND 门严格通过」。所以必须带 stage。
    """
    parts = [p for p in (channel or "").split("+") if p]
    if not parts:
        return "none"

    if stage == "Markup":
        if "main_strict" in parts:
            return "strict"
        if "main_weak" in parts:
            return "weak"
        if "main_extra" in parts:
            return "extra_only"
        if "fallback1" in parts or "fallback2_markup" in parts:
            return "fallback"
        return "none"

    if stage == "Accumulation":
        if "main_strict" in parts:
            return "strict"
        if any(p.startswith("main_weak_") for p in parts):
            return "weak"
        if "fallback2_accum" in parts or "fallback1" in parts:
            return "fallback"
        return "none"

    if stage == "Distribution":
        if "main_strict" in parts:
            return "strict"
        if "main_weak" in parts:
            return "weak"
        if "main_extra" in parts:
            return "extra_only"
        return "none"

    return "none"


def _ma50_reached_ma200(ma50: float, ma200: float,
                        tolerance: float = MA50_MA200_TOLERANCE) -> bool:
    """MA50 是否"够到" MA200 (带容差)

    判定: ma50 >= ma200 * tolerance
      - ma200 <= 0 (数据不足) → False
      - tolerance 默认 0.90, 即 MA50 追到年线的 90% 就算数

    ⚠️ 注意这是**放宽**口径, 跟 WyckoffTradingAgent Layer 2.5 的严格
       `MA50 > MA200` 不同。参考实现跑在已过 L1/L2/L3 漏斗的池子上 (本身就是强过滤),
       本项目跑在财务初筛后的池子上, 两者地位不同, 阈值不可直接照抄。

    Returns: bool
    """
    if ma200 is None or ma50 is None or ma200 <= 0:
        return False
    return ma50 >= ma200 * tolerance


def _bias_200_exceeds_limit(closes, max_bias_200: float = 25.0,
                            precomputed_ma200: float | None = None) -> bool:
    """(WyckoffTradingAgent 975 行) bias_200 过滤

    Returns: True = bias_200 > max_bias (超限, 应排除)
    precomputed_ma200: 直接传入 arrs['ma_window'][i]，跳过 sum(closes[-200:])
    """
    if precomputed_ma200 is not None:
        ma200 = precomputed_ma200
    else:
        if len(closes) < 200:
            return False
        ma200 = sum(closes[-200:]) / 200
    if ma200 <= 0:
        return False
    bias = (closes[-1] / ma200 - 1) * 100
    return bias > max_bias_200


def _is_trading_range_context(zone_h, zone_l, zone_c, full_h, full_l, full_c,
                              max_range_pct: float = 30.0,
                              max_drift_pct: float = 12.0,
                              atr_window: int = 20,
                              atr_multiple: float = 4.0,
                              precomputed_atr: float | None = None) -> bool:
    """(WyckoffTradingAgent 989 行) Spring 必须先发生在可接受的 TR 内

    动态 ATR 计算最大允许 range_pct (atr_pct × 4, 范围 [30, 60])
    precomputed_atr: 外部预算的 ATR 值（float），有则跳过内部 O(n) ATR 计算
    """
    if not zone_h or not zone_l or not zone_c:
        return False
    high_max = max(zone_h)
    low_min = min(zone_l)
    if low_min <= 0:
        return False
    range_pct = (high_max - low_min) / low_min * 100.0

    max_allowed_range_pct = max_range_pct
    if precomputed_atr is not None:
        atr = precomputed_atr
        if atr > 0 and full_c and full_c[-1] > 0:
            atr_pct = (atr / full_c[-1]) * 100.0
            max_allowed_range_pct = atr_pct * atr_multiple
            max_allowed_range_pct = min(max(max_allowed_range_pct, max_range_pct), 60.0)
    elif full_h and len(full_h) > atr_window:
        # True Range 序列
        trs = []
        for j in range(len(full_h)):
            tr1 = full_h[j] - full_l[j]
            tr2 = abs(full_h[j] - (full_c[j-1] if j > 0 else full_c[j]))
            tr3 = abs(full_l[j] - (full_c[j-1] if j > 0 else full_c[j]))
            trs.append(max(tr1, tr2, tr3))
        if len(trs) >= atr_window:
            atr = sum(trs[-atr_window:]) / atr_window
            if atr > 0 and full_c[-1] > 0:
                atr_pct = (atr / full_c[-1]) * 100.0
                max_allowed_range_pct = atr_pct * atr_multiple
                max_allowed_range_pct = min(max(max_allowed_range_pct, max_range_pct), 60.0)

    if range_pct > max_allowed_range_pct:
        return False
    if zone_c[0] <= 0:
        return False
    c_start = zone_c[0]
    c_end = zone_c[-1]
    drift_pct = abs((c_end - c_start) / c_start * 100.0)
    return not drift_pct > max_drift_pct


def _spring_support_level(zone_l, zone_c,
                           precomputed_swing_lows: list | None = None) -> float:
    """(WyckoffTradingAgent 1086 行) Spring 支撑位 (swing low 中位数, fallback 最小 close)

    precomputed_swing_lows: 与 zone_l 等长的布尔数组（来自 arrs['swing_low_3'] 切片），
                            有则跳过 _swing_values 重算。
    """
    if precomputed_swing_lows is not None:
        swing_lows = [v for v, is_sw in zip(zone_l, precomputed_swing_lows) if is_sw]
    else:
        swing_lows = _swing_values(zone_l, kind="low", window=3)
    if len(swing_lows) >= 2:
        s = sorted(swing_lows[-5:])  # 对齐 WTA: 只取最近5个，更贴近近期价格行为
        return s[len(s) // 2]
    if not zone_c:
        return 0
    return min(c for c in zone_c if c is not None and c > 0) if any(c for c in zone_c if c) else 0


# ============================================================
# Section 3: EVR 三件套 + abstract series (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _evr_turnover_ok(turnovers, idx: int, min_turnover: float) -> bool:
    """(WyckoffTradingAgent 1273 行) EVR 流动性检查

    原版用 daily_basic.turnover, 我们默认 0 跳过 (没拉 turnover)
    """
    if min_turnover <= 0:
        return True
    if idx < 0 or idx >= len(turnovers):
        return True
    t = turnovers[idx]
    if t is None:
        return True
    return float(t) >= min_turnover


def _evr_structure_ok(closes, close_last: float) -> bool:
    """(WyckoffTradingAgent 1280 行) EVR 结构稳定 (close_last >= 3 日前 close × 0.98)"""
    if len(closes) < 4:
        return True
    close_3d_ago = closes[-4]
    if close_3d_ago is None or close_3d_ago <= 0:
        return True
    return float(close_last) >= float(close_3d_ago) * 0.98


# ============================================================
# Section 4: SOS 三件套 (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _sos_volume_ratio(vols, vol_quantile_window: int = 60, vol_ratio: float = 3.0, vol_quantile: float = 0.95) -> Optional[float]:
    """(WyckoffTradingAgent 1379 行) SOS 量比 (含 quantile 阈值)

    Returns: 当前 vol / 参考均量, 或 None
    """
    if len(vols) < vol_quantile_window + 1:
        return None
    # 2026-10-17 改名: vol_ref (局部样本) → vol_samples (跟 stage_factor 的 vol_baseline_avg 区分)
    vol_samples = vols[-vol_quantile_window-1:-1]
    if not vol_samples:
        return None
    vol_ref_avg = sum(vol_samples) / len(vol_samples)
    if vol_ref_avg <= 0:
        return None
    last_vol = vols[-1]
    vol_ratio_val = last_vol / vol_ref_avg
    if vol_ratio_val < vol_ratio:
        return None
    sorted_samples = sorted(vol_samples)
    q_idx = int(vol_quantile * len(sorted_samples))
    quantile_volume = sorted_samples[min(q_idx, len(sorted_samples) - 1)]
    if quantile_volume > 0 and last_vol < quantile_volume:
        return None
    return vol_ratio_val


def _sos_breakout_or_ma_cross(closes, highs, breakout_window: int = 60, breakout_tolerance: float = 0.01,
                               precomputed_ma50: float | None = None,
                               precomputed_ma50_prev: float | None = None) -> bool:
    """(WyckoffTradingAgent 1399 行) SOS 突破: 60 日新高 OR MA50 交叉"""
    if len(closes) < 51 or len(highs) < breakout_window + 2:
        return False
    close_last = closes[-1]
    recent_highs = highs[-breakout_window-1:-1]
    max_recent_high = max(recent_highs) if recent_highs else float('inf')
    is_breakout = close_last >= max_recent_high * (1.0 - breakout_tolerance)
    # MA50 cross
    ma50_last = precomputed_ma50      if precomputed_ma50      is not None else sum(closes[-50:]) / 50
    ma50_prev = precomputed_ma50_prev if precomputed_ma50_prev is not None else sum(closes[-51:-1]) / 50
    prev_close = closes[-2]
    is_ma_crossover = (prev_close <= ma50_prev and close_last > ma50_last)
    return is_breakout or is_ma_crossover


# ============================================================
# Section 5: Compression 四件套 (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _compression_direction_ok(closes, ma_window: int = 20, lookback: int = 5,
                               precomputed_ma: float | None = None,
                               precomputed_ma_prev: float | None = None) -> bool:
    """(WyckoffTradingAgent 1467 行) Compression 方向: 短期 MA ≥ 长期 MA

    原版 cfg.compression_require_direction 默认 True
    """
    if len(closes) < 25:
        return False
    ma      = precomputed_ma      if precomputed_ma      is not None else sum(closes[-ma_window:]) / ma_window
    ma_prev_window = closes[-(ma_window + lookback):-lookback]
    if not ma_prev_window:
        return False
    ma_prev = precomputed_ma_prev if precomputed_ma_prev is not None else sum(ma_prev_window) / len(ma_prev_window)
    return ma >= ma_prev


def _compression_bias_ok(closes, max_bias_200: float = 25.0,
                         precomputed_ma200: float | None = None) -> bool:
    """(WyckoffTradingAgent 1484 行) Compression bias 过滤"""
    return not _bias_200_exceeds_limit(closes, max_bias_200, precomputed_ma200=precomputed_ma200)


def _compression_atr_ratio(closes, highs, lows,
                           lookback: int = 5, atr_window: int = 20,
                           atr_quantile: float = 0.20) -> Optional[float]:
    """(WyckoffTradingAgent 1488 行) Compression ATR 分位数 + 压缩比

    Returns: recent_atr / hist_atr_median  (越小越压缩), 或 None
    """
    if len(closes) < atr_window + lookback + 1:
        return None
    # 计算 recent lookback 日 atr_pct 序列 (对齐 WTA: 保留序列做单调性检查)
    recent_atr_pct_arr = []
    for j in range(len(closes) - lookback, len(closes)):
        if j <= 0 or closes[j] is None or closes[j] <= 0:
            return None
        tr = max(
            highs[j] - lows[j],
            abs(highs[j] - closes[j-1]),
            abs(lows[j] - closes[j-1])
        )
        recent_atr_pct_arr.append((tr / closes[j]) * 100.0)
    if len(recent_atr_pct_arr) < lookback:
        return None
    recent_atr_pct = sum(recent_atr_pct_arr) / lookback
    # 对齐 WTA: ATR 序列须单调收窄，允许至多 1 次反弹
    violations = sum(1 for k in range(1, len(recent_atr_pct_arr))
                     if recent_atr_pct_arr[k] > recent_atr_pct_arr[k-1])
    if violations > 1:
        return None
    # hist (前 atr_window 日) atr_pct
    hist_atr_pcts = []
    for j in range(len(closes) - atr_window - lookback, len(closes) - lookback):
        if j <= 0 or closes[j] is None or closes[j] <= 0:
            continue
        tr = max(
            highs[j] - lows[j],
            abs(highs[j] - closes[j-1]),
            abs(lows[j] - closes[j-1])
        )
        hist_atr_pcts.append((tr / closes[j]) * 100.0)
    if not hist_atr_pcts:
        return None
    hist_sorted = sorted(hist_atr_pcts)
    q_idx = int(atr_quantile * len(hist_sorted))
    q_idx = min(max(q_idx, 0), len(hist_sorted) - 1)
    threshold = hist_sorted[q_idx]
    if recent_atr_pct > threshold:
        return None
    # 算 compression 比例
    hist_atr_median = hist_sorted[len(hist_sorted) // 2]
    return float(recent_atr_pct / hist_atr_median) if hist_atr_median > 0 else None


# ============================================================
# Section 6: TrendPullback 两件套 (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _trend_pullback_peak_idx(closes, lookback: int = 10, ma_window: int = 20,
                              min_pullback_pct: float = 5.0, max_pullback_pct: float = 20.0,
                              precomputed_ma: float | None = None,
                              precomputed_ma_prev: float | None = None) -> Optional[int]:
    """(WyckoffTradingAgent 1542 行) 找 trend pullback 的 peak (相对 idx 位置)"""
    if len(closes) < ma_window + lookback + 5:
        return None
    ma_now  = precomputed_ma      if precomputed_ma      is not None else sum(closes[-ma_window:]) / ma_window
    ma_prev_window = closes[-(ma_window + lookback + 1):-(lookback + 1)]
    if not ma_prev_window or len(ma_prev_window) < 1:
        return None
    ma_prev = precomputed_ma_prev if precomputed_ma_prev is not None else sum(ma_prev_window) / len(ma_prev_window)
    if ma_now <= ma_prev:
        return None
    # 找最近 lookback+1 根内的 peak
    recent = closes[-(lookback + 1):]
    peak = max(recent)
    peak_idx = recent.index(peak)
    if peak_idx < 1 or peak <= 0:
        return None
    trough_window = recent[peak_idx + 1:-1] if peak_idx + 1 < len(recent) - 1 else [recent[-1]]
    trough = min(trough_window) if trough_window else recent[-1]
    last_close = recent[-1]
    pullback_pct = (peak - min(trough, last_close)) / peak * 100.0
    if pullback_pct < min_pullback_pct or pullback_pct > max_pullback_pct:
        return None
    if last_close <= recent[-2]:
        return None
    return peak_idx


def _trend_pullback_vol_threshold(closes, market_cap_yi: float = 0.0,
                                   base_threshold: float = 0.6,
                                   ma_long_w: int = 200) -> float:
    """(WyckoffTradingAgent 1567 行) TrendPullback 量阈值 (按市值/MA50 streak 调整)"""
    market_cap_yi = market_cap_yi or 0.0  # 兼容 None (v5.3 修)
    threshold = base_threshold
    if market_cap_yi >= 200.0:
        threshold = min(threshold + 0.15, 0.85)  # 大市值放宽
    if len(closes) < ma_long_w + 60:
        return threshold
    # MA50 streak (持续在 MA200 上方)
    ma50_window = closes[-min(60, len(closes)):]
    streak = 0
    for i in range(1, len(ma50_window) + 1):
        if i + ma_long_w - 1 >= len(closes):
            break
        ma50_i = sum(closes[-(ma_long_w + i):-i]) / ma_long_w
        ma200_i = sum(closes[-(2 * ma_long_w + i):-(ma_long_w + i)]) / ma_long_w if i + 2 * ma_long_w <= len(closes) else 0
        if ma200_i <= 0:
            break
        if ma50_i <= ma200_i:
            break
        streak += 1
    if streak >= 20:
        threshold = min(threshold + 0.10, 0.90)
    return threshold


# ============================================================
# Section 7: LPS creek 确认 (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _lps_creek_confirmed(c, h,
                          lps_lookback: int = 3,
                          anchor_days: int = 60,
                          breakout_days: int = 15,
                          swing_window: int = 3,
                          breakout_pct: float = 0.5,
                          hold_tolerance_pct: float = 2.0) -> bool:
    """(WyckoffTradingAgent 1207 行) LPS creek 确认

    逻辑: 找 anchor 60 日的 swing high 阻力线 → 之后 N 日有突破 → 当前价站稳线之上
    默认 cfg.lps_creek_confirmation_enabled = False, 跟原版一致 (不会破坏 LPS 触发率)

    Returns: bool
    """
    total = anchor_days + breakout_days + lps_lookback
    if len(c) < total:
        return False
    # anchor (前 60 日 high, 不含最后 breakout + lps 段)
    anchor_highs = h[-(total):-(breakout_days + lps_lookback)]
    if not anchor_highs:
        return False
    # 拟合阻力线
    line = _creek_line(_swing_high_points(anchor_highs, swing_window))
    if line is None:
        return False
    slope, intercept = line
    if slope <= 0:
        return False  # 阻力线必须向上 (上升趋势)
    # breakout 段 (之后 15 日) 是否有 close 穿过阻力线 × 1.005
    breakout_start = -(breakout_days + lps_lookback)
    breakout_end = -lps_lookback if lps_lookback > 0 else len(c)
    crossed = False
    for idx, value in enumerate(c[breakout_start:breakout_end]):
        line_value = slope * (len(c) - total + idx) + intercept
        if line_value > 0 and value >= line_value * (1.0 + breakout_pct / 100.0):
            crossed = True
            break
    if not crossed:
        return False
    # 当前 close 仍在线上 (hold tolerance 2%)
    current_line = slope * (len(c) - 1) + intercept
    if current_line <= 0:
        return False
    return c[-1] >= current_line * (1.0 - hold_tolerance_pct / 100.0)

