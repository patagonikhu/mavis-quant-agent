"""
detectors/upthrust.py - UTAD 派发后上探 (跟 WyckoffTradingAgent 2556 行 1:1)

触发: 高位假突破 + 上影线长 + 放量 + bias_200 > 15%
返回: bool

原版函数名: _detect_upthrust_after_distribution
我们改名 detect_upthrust (兼容历史)
"""
def detect_upthrust(c, h, l, v, o, i, ma_long_w=200, lookback=60,
                    breakout_pct=1.0, close_back_pct=0.3, upper_shadow_thr=0.35,
                    vol_ratio_thr=1.5, min_bias=15.0,
                    precomputed_ma=None) -> bool:
    """UTAD 检测 (1:1 搬运 WyckoffTradingAgent 2556 行 _detect_upthrust_after_distribution)"""
    n = len(c)
    if i < max(ma_long_w, lookback) + 1:
        return False
    if i < lookback + 2:
        return False
    # prior_high (前 lookback 日, 不含当前)
    prior_high = h[i-lookback-2:i-1]
    if not prior_high:
        return False
    # 阻力位算法 (2026-07-31 P3.5 修复):
    # 原版用 swing_highs[-5:] 平均, 但会把 2 周前的 swing high (老阻力位)
    # 平均进去, 拉低整体阻力位, 导致 close_back_pct 永远是大负数
    # (close 远高于阻力位 = 看起来是"真突破"而不是"假突破")
    # 修复: 用前 lookback 根 K 线的最高价 (max(prior_high))
    # 600584 6-26 例子:
    #   原算法: swing_highs[-5:] = [75.88, 78.38, 91.96, 94.70, 104.17], avg=89.02
    #   修复后: max(prior_high) = 109.33 (当日 high)
    resistance = max(prior_high)
    # precomputed_ma[i-1] = mean(c[i-200:i])，等价于原来 sum(c[i-200:i])/200
    ma200 = precomputed_ma[i - 1] if precomputed_ma is not None else sum(c[i-ma_long_w:i]) / ma_long_w
    ref_volume = sum(v[i-22:i-1]) / 21 if i >= 22 else 0
    if not resistance or not ma200 or ref_volume is None or resistance <= 0 or ma200 <= 0:
        return False
    last_h = h[i-1]; last_l = l[i-1]; last_o = o[i-1]; last_c = c[i-1]
    day_range = last_h - last_l
    if day_range <= 0:
        return False
    shadow = last_h - max(last_o, last_c)
    breakout_pct_val = (last_h / resistance - 1.0) * 100.0
    close_back_pct_val = (resistance / last_c - 1.0) * 100.0 if last_c > 0 else 0.0
    vol_ratio_val = v[i-1] / ref_volume if ref_volume > 0 else 0.0
    bias_200 = (last_c / ma200 - 1.0) * 100.0
    upper_shadow_ratio_val = shadow / day_range
    if (breakout_pct_val < breakout_pct
        or close_back_pct_val < close_back_pct
        or upper_shadow_ratio_val < upper_shadow_thr
        or vol_ratio_val < vol_ratio_thr
        or bias_200 < min_bias):
        return False
    return True
