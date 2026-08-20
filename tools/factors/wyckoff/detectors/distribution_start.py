"""
detectors/distribution_start.py - DistributionStart 派发起点 (跟 WyckoffTradingAgent 2521 行 1:1)

触发: bias_200 > 30% + 最近 3 日均量 < 60 日均量 × 50%
返回: bool
"""
from ..helpers import _bias_200_exceeds_limit


def detect_distribution_start(c, h, l, v, o, i, ma_long_w=200, high_thr=30.0,
                              confirm_days=3, vol_dry_ratio=0.5) -> bool:
    """DistributionStart 检测 (1:1 搬运 WyckoffTradingAgent 2521 行)"""
    n = len(c)
    if i < max(ma_long_w, confirm_days) + 20:
        return False
    if i < ma_long_w + 1:
        return False
    ma_long = sum(c[i-ma_long_w:i]) / ma_long_w
    last_close = c[i-1]
    if ma_long <= 0:
        return False
    bias = (last_close - ma_long) / ma_long * 100.0
    if bias < high_thr:
        return False
    # 缩量: 最近 confirm_days 均量 < 60 日均量
    if i < 60 + confirm_days:
        return False
    ref_vol = sum(v[i-61:i-1]) / 60
    recent_vol = sum(v[i-confirm_days-1:i]) / confirm_days
    if ref_vol <= 0:
        return False
    return recent_vol / ref_vol <= vol_dry_ratio
