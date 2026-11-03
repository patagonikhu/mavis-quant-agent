"""
wyckoff/helpers.py - 威科夫核心 helper (跟 WyckoffTradingAgent 1:1 命名)

抽离 wyckoff.py 里的 13 个 helper 函数, 命名严格对齐 WyckoffTradingAgent/core/wyckoff_engine.py:
  - _find_range / _candle_type (内部工具, 不暴露 WyckoffTradingAgent)
  - _op_from_prev_close / _is_frozen_board_day / _swing_values
  - _swing_high_points / _creek_line
  - _bias_200_exceeds_limit
  - _is_trading_range_context / _spring_support_level
  - _evr_turnover_ok / _evr_structure_ok / _evr_confirmation_ok / _evr_candidate_indexes
  - _evr_series / _evr_ref_volume_avg / _sos_series / _sos_volume_ratio / _sos_breakout_or_ma_cross
  - _compression_ohlcv / _compression_direction_ok / _compression_bias_ok / _compression_atr_ratio
  - _trend_pullback_peak_idx / _trend_pullback_vol_threshold

所有函数纯计算, 无 side effect. 9 detector 都在 detectors/ 目录, 通过这些 helper 拼装.
"""
from typing import List, Tuple, Optional, NamedTuple
import math


# ============================================================
# Section 1: 内部工具 (wyckoff.py 自有, 不是 WyckoffTradingAgent 1:1)
# ============================================================

def _find_range(closes, highs, lows, vols, lookback=30):
    """找最近 lookback 根 K 线的 TR 区间 (内部工具, Stage 用)

    Returns: (range_low, range_high) - 取 lookback 内 low min, high max
    """
    if not closes or len(closes) < lookback:
        return None, None
    recent_lows = lows[-lookback:] if lows else []
    recent_highs = highs[-lookback:] if highs else []
    if not recent_lows or not recent_highs:
        return None, None
    return min(recent_lows), max(recent_highs)


# ============================================================
# Section 2: K 线 / 形态基础 helper (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _is_frozen_board_day(op, hi, low, cl, *, market="cn") -> bool:
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


def _creek_line(swing_high_points_list, breakout_pct: float = 0.5):
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
# Section 3: bias / trading range / support (跟 WyckoffTradingAgent 1:1)
# ============================================================

# MA50 相对 MA200 的容差比例。
# 语义: MA50 追到 MA200 的 95% 即视为"已接近/站上年线", 不要求严格穿越。
# 用途: 统一所有"MA50 是否站上 MA200"的判定口径, 避免各处写死 0.95 产生分歧。
#   - stage_factor.py fallback1 (主升浪兜底路径)
#   - report_renderer.py 报告渲染 (fallback1 判定复算 + 展示)
MA50_MA200_TOLERANCE = 0.95


def _ma50_reached_ma200(ma50: float, ma200: float,
                        tolerance: float = MA50_MA200_TOLERANCE) -> bool:
    """MA50 是否"够到" MA200 (带容差)

    判定: ma50 >= ma200 * tolerance
      - ma200 <= 0 (数据不足) → False
      - tolerance 默认 0.95, 即 MA50 追到年线的 95% 就算数

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
# Section 4: EVR 三件套 + abstract series (跟 WyckoffTradingAgent 1:1)
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


def _evr_confirmation_ok(closes, lows, idx: int, confirm_days: int, allow_break_pct: float) -> bool:
    """EVR 确认日不破底

    WTA 检查事件日后 confirm_days 根 K 线收盘不低于事件日低点。
    mavis index-based 框架中 closes = c[:i]，idx = i-1 是最后一根，
    confirm 窗口 c[i:i+confirm_days] 是未来数据，无法前瞻。
    结构稳定性已由 _evr_structure_ok (close >= 3日前close×0.98) 覆盖，此处恒 True。
    """
    return True


# ============================================================
# Section 5: SOS 三件套 (跟 WyckoffTradingAgent 1:1)
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
# Section 6: Compression 四件套 (跟 WyckoffTradingAgent 1:1)
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


def _compression_atr_ratio(closes, highs, lows, vols,
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
# Section 7: TrendPullback 两件套 (跟 WyckoffTradingAgent 1:1)
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
# Section 8: LPS creek 确认 (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _lps_creek_confirmed(c, h, l,
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
    line = _creek_line(_swing_high_points(anchor_highs, swing_window), 0.5)
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

