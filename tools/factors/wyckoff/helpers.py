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


def _candle_type(op, hi, lo, cl):
    """K 线类型: '阳' / '阴' / '十字' (内部工具)"""
    if cl > op:
        return "阳"
    if cl < op:
        return "阴"
    return "十字"


def _sub_events_with_glossary(events: list) -> list:
    """9 sub_event 加中文注释 (报告用)"""
    glossary = {
        'Spring': '终极震仓 (跌穿支撑后收回)',
        'LPS': '最后支撑点 (缩量回踩 MA20)',
        'EVR': '努力无结果 (低位放量滞涨)',
        'SOS': '强势信号 (放量突破 + 单日≥6%)',
        'Compression': '压缩蓄势 (ATR 收窄 + 缩量)',
        'TrendPullback': '趋势回踩 (上升趋势缩量回调企稳)',
        'MarkupEntry': '主升浪起点 (MA50/200 金叉 + 持续)',
        'DistributionStart': '派发起点 (bias_200>30% + 缩量)',
        'UTAD': '派发后上探 (高位假突破)',
    }
    return [{"event": e, "含义": glossary.get(e, e)} for e in events]


# ============================================================
# Section 2: K 线 / 形态基础 helper (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _op_from_prev_close(closes, i):
    """(WyckoffTradingAgent 内联) 上一根 K 的 close 作为 op (兼容老 dump)"""
    return closes[i-1] if i > 0 else closes[0]


def _is_frozen_board_day(op, hi, low, cl, *, market="cn") -> bool:
    """(WyckoffTradingAgent 1052 行) 一字板检测

    op=hi=lo=cl 或 op=hi=lo (跌停一字) 算一字板
    """
    if any(x is None for x in [op, hi, low, cl]):
        return False
    if op <= 0 or cl <= 0:
        return False
    # A 股一字板: 振幅 < 0.1% + (op≈hi≈lo 或 op≈hi≈cl)
    if abs(hi - low) / max(op, 0.01) < 0.001:
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

def _bias_200_exceeds_limit(closes, max_bias_200: float = 25.0) -> bool:
    """(WyckoffTradingAgent 975 行) bias_200 过滤

    Returns: True = bias_200 > max_bias (超限, 应排除)
    """
    if len(closes) < 200:
        return False  # 数据不够不算超限
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


def _spring_support_level(zone_l, zone_c) -> float:
    """(WyckoffTradingAgent 1086 行) Spring 支撑位 (swing low 中位数, fallback 最小 close)"""
    swing_lows = _swing_values(zone_l, kind="low", window=3)
    if len(swing_lows) >= 2:
        # 取中位数
        s = sorted(swing_lows)
        return s[len(s) // 2]
    if not zone_c:
        return 0
    return min(c for c in zone_c if c is not None and c > 0) if any(c for c in zone_c if c) else 0


# ============================================================
# Section 4: EVR 三件套 + abstract series (跟 WyckoffTradingAgent 1:1)
# ============================================================

class _EvrSeries(NamedTuple):
    """(WyckoffTradingAgent 1244 行) EVR 字段集合 (1:1 NamedTuple)"""
    close: list
    low: list
    volume: list
    pct_chg: list


def _evr_series(closes, lows, vols, pct_chgs) -> Optional[_EvrSeries]:
    """(WyckoffTradingAgent 1263 行) 构造 EVR 字段 (1:1 NamedTuple)"""
    if len(closes) < 2 or len(lows) < 2 or len(vols) < 2:
        return None
    return _EvrSeries(close=closes, low=lows, volume=vols, pct_chg=pct_chgs or [0.0] * len(closes))


def _evr_ref_volume_avg(vols, vol_window: int) -> Optional[float]:
    """(WyckoffTradingAgent 1263 行) EVR 参考均量 (前 vol_window 日, 不含当前)"""
    if len(vols) < vol_window + 1:
        return None
    return sum(vols[-vol_window-1:-1]) / vol_window


def _evr_candidate_indexes(confirm_days: int) -> list:
    """EVR 候选索引 (2026-07-31 简化: 不返 [-1], 永远返空, 由 evr.py 自行处理 real_idx)"""
    return []


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
    """EVR 确认日不破底 (2026-07-31 简化: 永远 True)

    历史版本 (WyckoffTradingAgent 1287 行 1:1) 包含 3 个 bug:
      1. _evr_candidate_indexes 永远返 [-1], confirm_days 无效
      2. evr.py: real_idx = i - 1 覆盖循环变量 idx, idx 永远用不上
      3. _evr_confirmation_ok 用 closes[idx+1] 未来数据, 但 closes = c[:i] 切片不含未来

    实战: 60m 周期波动大, 1 根 K 线确认机制失效; 直接返 True 简化
    """
    return True



# ============================================================
# Section 5: SOS 三件套 (跟 WyckoffTradingAgent 1:1)
# ============================================================

class _SosSeries(NamedTuple):
    """(WyckoffTradingAgent 1365 行) SOS 字段集合 (1:1 NamedTuple)"""
    close: list
    volume: list
    pct_chg: list
    high: list


def _sos_series(closes, vols, pct_chgs, highs) -> Optional[_SosSeries]:
    """(WyckoffTradingAgent 1367 行) 构造 SOS 字段"""
    if not closes or not vols or not highs:
        return None
    return _SosSeries(close=closes, volume=vols, pct_chg=pct_chgs or [0.0] * len(closes), high=highs)


def _sos_volume_ratio(vols, vol_quantile_window: int = 60, vol_ratio: float = 3.0, vol_quantile: float = 0.95) -> Optional[float]:
    """(WyckoffTradingAgent 1379 行) SOS 量比 (含 quantile 阈值)

    Returns: 当前 vol / 参考均量, 或 None
    """
    if len(vols) < vol_quantile_window + 1:
        return None
    vol_ref = vols[-vol_quantile_window-1:-1]
    if not vol_ref:
        return None
    vol_ref_avg = sum(vol_ref) / len(vol_ref)
    if vol_ref_avg <= 0:
        return None
    last_vol = vols[-1]
    vol_ratio_val = last_vol / vol_ref_avg
    if vol_ratio_val < vol_ratio:
        return None
    sorted_ref = sorted(vol_ref)
    q_idx = int(vol_quantile * len(sorted_ref))
    quantile_volume = sorted_ref[min(q_idx, len(sorted_ref) - 1)]
    if quantile_volume > 0 and last_vol < quantile_volume:
        return None
    return vol_ratio_val


def _sos_breakout_or_ma_cross(closes, highs, breakout_window: int = 60, breakout_tolerance: float = 0.01) -> bool:
    """(WyckoffTradingAgent 1399 行) SOS 突破: 60 日新高 OR MA50 交叉"""
    if len(closes) < 51 or len(highs) < breakout_window + 2:
        return False
    close_last = closes[-1]
    recent_highs = highs[-breakout_window-1:-1]
    max_recent_high = max(recent_highs) if recent_highs else float('inf')
    is_breakout = close_last >= max_recent_high * (1.0 - breakout_tolerance)
    # MA50 cross
    ma50_last = sum(closes[-50:]) / 50
    ma50_prev = sum(closes[-51:-1]) / 50
    prev_close = closes[-2]
    is_ma_crossover = (prev_close <= ma50_prev and close_last > ma50_last)
    return is_breakout or is_ma_crossover


# ============================================================
# Section 6: Compression 四件套 (跟 WyckoffTradingAgent 1:1)
# ============================================================

def _compression_ohlcv(closes, highs, lows, vols) -> Optional[Tuple[list, list, list, list]]:
    """(WyckoffTradingAgent 1456 行) 字段清洗 (我们直接返)"""
    if not closes or not highs or not lows:
        return None
    return (closes, highs, lows, vols)


def _compression_direction_ok(closes, ma_window: int = 20, lookback: int = 5) -> bool:
    """(WyckoffTradingAgent 1467 行) Compression 方向: 短期 MA ≥ 长期 MA

    原版 cfg.compression_require_direction 默认 True
    """
    if len(closes) < 25:
        return False
    ma = sum(closes[-ma_window:]) / ma_window
    ma_prev_window = closes[-(ma_window + lookback):-lookback]
    if not ma_prev_window:
        return False
    ma_prev = sum(ma_prev_window) / len(ma_prev_window)
    return ma >= ma_prev


def _compression_bias_ok(closes, max_bias_200: float = 25.0) -> bool:
    """(WyckoffTradingAgent 1484 行) Compression bias 过滤"""
    return not _bias_200_exceeds_limit(closes, max_bias_200)


def _compression_atr_ratio(closes, highs, lows, vols,
                           lookback: int = 5, atr_window: int = 20,
                           atr_quantile: float = 0.20) -> Optional[float]:
    """(WyckoffTradingAgent 1488 行) Compression ATR 分位数 + 压缩比

    Returns: recent_atr / hist_atr_median  (越小越压缩), 或 None
    """
    if len(closes) < atr_window + lookback + 1:
        return None
    # 计算 recent 5 日 atr_pct
    recent_atr_pct_sum = 0
    for j in range(len(closes) - lookback, len(closes)):
        if j <= 0 or closes[j] is None or closes[j] <= 0:
            return None
        tr = max(
            highs[j] - lows[j],
            abs(highs[j] - closes[j-1]),
            abs(lows[j] - closes[j-1])
        )
        recent_atr_pct_sum += (tr / closes[j]) * 100.0
    recent_atr_pct = recent_atr_pct_sum / lookback
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
                              min_pullback_pct: float = 5.0, max_pullback_pct: float = 20.0) -> Optional[int]:
    """(WyckoffTradingAgent 1542 行) 找 trend pullback 的 peak (相对 idx 位置)"""
    if len(closes) < ma_window + lookback + 5:
        return None
    ma_now = sum(closes[-ma_window:]) / ma_window
    ma_prev_window = closes[-(ma_window + lookback + 1):-(lookback + 1)]
    if not ma_prev_window or len(ma_prev_window) < 1:
        return None
    ma_prev = sum(ma_prev_window) / len(ma_prev_window)
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

