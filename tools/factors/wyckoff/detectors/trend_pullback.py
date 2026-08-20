"""
detectors/trend_pullback.py - TrendPullback 趋势回踩 (跟 WyckoffTradingAgent 1586 行 1:1)

触发: 上升趋势 + 缩量回调 + 当前反弹
返回: bool
"""
from ..helpers import _bias_200_exceeds_limit, _trend_pullback_peak_idx, _trend_pullback_vol_threshold


def detect_trend_pullback(c, h, l, v, o, i, lookback=10, ma_window=20,
                          min_pullback=5.0, max_pullback=20.0,
                          vol_shrink_ratio=0.6, max_bias=35.0, market_cap_yi=0.0) -> bool:
    """TrendPullback 检测 (1:1 搬运 WyckoffTradingAgent 1586 行)

    v5.10.57 (P8.A, 2026-07-31): min_pullback/max_pullback 加显式参数 (默认 5/20 兼容老调用)
    60m 短周期 p50 1.65% 远小于 5%, 周期分层后 60m 用 0.5/8
    """
    n = len(c)
    if i < ma_window + lookback + 5:
        return False
    if _bias_200_exceeds_limit(c[:i], max_bias):
        return False
    # 找 peak (P8.A 透传 min/max_pullback, 默认 5/20 兼容老调用)
    peak_idx = _trend_pullback_peak_idx(c[:i], lookback, ma_window, min_pullback, max_pullback)
    if peak_idx is None:
        return False
    # 量阈值 (按市值/MA streak 调整, 1:1 对齐 WyckoffTradingAgent 1567 行)
    threshold = _trend_pullback_vol_threshold(c[:i], market_cap_yi, vol_shrink_ratio)
    # 缩量: 回落段均量 / 上涨段均量
    if i < lookback + 1:
        return False
    vol_tail = v[i-lookback-1:i]
    vol_up = sum(vol_tail[:peak_idx+1]) / (peak_idx+1) if peak_idx+1 > 0 else 0
    vol_down_slice = vol_tail[peak_idx+1:]
    if not vol_down_slice or vol_up <= 0:
        return False
    vol_down = sum(vol_down_slice) / len(vol_down_slice)
    return vol_down / vol_up <= threshold
