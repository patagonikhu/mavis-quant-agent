"""
detectors/lps.py - LPS 最后支撑点缩量 (跟 WyckoffTradingAgent 1138 行 1:1)

触发: 近 N 日回踩 MA20 + 缩量 + MA 上升
返回: bool

v5.4 final: 加 lps_creek_confirmation_enabled 选项 (默认 False, 跟原版一致)
  启用后: LPS 必须先突破 creek 阻力线 (swing high 拟合线) 才算
  原版 cfg.lps_creek_confirmation_enabled = False, 我们默认 False 不破坏触发率
"""
from ..helpers import _bias_200_exceeds_limit, _lps_creek_confirmed


def detect_lps(c, h, l, v, o, i, lookback=3, ma_window=20, ma_tolerance=0.02,
               vol_dry_ratio=0.50, vol_ref_window=60, ma_rising_window=5,
               max_bias=25.0, pct_chg=None,
               creek_confirmation_enabled: bool = False) -> bool:
    """LPS 检测 (1:1 搬运 WyckoffTradingAgent 1138 行)

    Args:
        creek_confirmation_enabled: 是否需要 creek 阻力线突破确认 (默认 False, 跟原版一致)
    """
    n = len(c)
    if n < max(vol_ref_window, ma_window) + lookback:
        return False
    if i < max(vol_ref_window, ma_window) + lookback:
        return False
    # MA20
    if i < ma_window:
        return False
    ma_now = sum(c[i-ma_window:i]) / ma_window
    if ma_now <= 0:
        return False
    # 最近 N 日最后一根 close >= MA20 (对齐 WyckoffTradingAgent: 只检查 last_close)
    # 旧逻辑: any(cl < ma_now for cl in recent_closes) — 过严，回踩过程中任意一根低于MA即拒绝
    # 新逻辑: 只检查当前bar收盘价，允许近期K线短暂低于MA（回踩正常现象）
    recent_closes = c[i-lookback:i]
    if not recent_closes:
        return False
    if recent_closes[-1] < ma_now:
        return False
    # bias_200：price 高于 MA200 过多时拒绝（过热）
    if _bias_200_exceeds_limit(c[:i], max_bias):
        return False
    # 新增：price 深度低于 MA200（强下跌趋势）也拒绝 LPS
    # LPS = 累积末段最后支撑点，不该在主跌浪中出现
    if i >= 200:
        ma200_val = sum(c[i-200:i]) / 200
        if ma200_val > 0 and (c[i-1] / ma200_val - 1) * 100 < -20:
            return False  # 价格低于 MA200 超过 20%，是主跌浪，非累积末段
    # 新增：价格在近60日区间的上60%也拒绝（LPS应在区间下部，不该在高位）
    if i >= 60:
        period_low  = min(c[i-60:i])
        period_high = max(c[i-60:i])
        if period_high > period_low > 0:
            pos_in_range = (c[i-1] - period_low) / (period_high - period_low)
            if pos_in_range > 0.65:
                return False  # 价格在近60日区间上65%，不是 LPS 位置
    # MA 上升 (lookback + ma_rising_window 之前)
    rising_offset = lookback + ma_rising_window
    if i > rising_offset + ma_window:
        ma_prev_window = c[i-rising_offset-ma_window:i-rising_offset]
        if not ma_prev_window:
            return False
        ma_prev = sum(ma_prev_window) / len(ma_prev_window)
        if ma_now <= ma_prev:
            return False
    # low 接近 MA (ma_tolerance 2%)
    recent_lows = l[i-lookback:i]
    if not recent_lows:
        return False
    low_near_ma = min(recent_lows)
    if abs(low_near_ma - ma_now) / ma_now > ma_tolerance:
        return False
    # 缩量: recent N 日 max vol / 前 60 日 max vol
    if i < lookback + 1:
        return False
    recent_max_vol = max(v[i-lookback:i])
    ref_window = v[i-vol_ref_window-lookback:i-lookback]
    if not ref_window:
        return False
    ref_max_vol = max(ref_window)
    if ref_max_vol <= 0:
        return False
    if recent_max_vol / ref_max_vol > vol_dry_ratio:
        return False
    # creek 确认 (v5.4 final, 跟原版 cfg.lps_creek_confirmation_enabled 默认 False)
    if creek_confirmation_enabled:
        return _lps_creek_confirmed(c[:i], h[:i], l[:i])
    return True
