"""
detectors/markup_entry.py - MarkupEntry 主升浪起点 (跟 WyckoffTradingAgent 1742 行 1:1)

触发: MA50/MA200 金叉 + 持续 N 日在 MA200 上方 + MA50 角度足够
返回: bool
"""
from ..helpers import _bias_200_exceeds_limit


def detect_markup_entry(c, h, l, v, o, i, ma_short_w=50, ma_long_w=200,
                        confirm_days=5, ma_angle_min=2.0) -> bool:
    """MarkupEntry 检测 (1:1 搬运 WyckoffTradingAgent 1742 行)"""
    n = len(c)
    if i < ma_long_w + 1:
        return False
    ma_short = sum(c[i-ma_short_w:i]) / ma_short_w
    ma_long = sum(c[i-ma_long_w:i]) / ma_long_w
    if ma_short <= ma_long:
        return False
    # 过去 confirm_days*2 内找穿过点
    lookback = max(confirm_days * 2, 10)
    if i < lookback + ma_long_w:
        return False
    ma_short_recent = [sum(c[i-ma_short_w+k:i+k]) / ma_short_w for k in range(-lookback+1, 1)]
    ma_long_recent = [sum(c[i-ma_long_w+k:i+k]) / ma_long_w for k in range(-lookback+1, 1)]
    crossover_found = False
    for j in range(1, len(ma_short_recent)):
        if ma_short_recent[j-1] <= ma_long_recent[j-1] and ma_short_recent[j] > ma_long_recent[j]:
            crossover_found = True
            break
    if not crossover_found:
        return False
    # 最近 confirm_days 持续在 MA200 上方
    if i < confirm_days + ma_short_w:
        return False
    recent_above = sum(1 for j in range(-confirm_days, 0) if ma_short_recent[j] > ma_long_recent[j])
    if recent_above < confirm_days:
        return False
    # MA50 角度 (5 日变化率)
    if i < 6:
        return False
    ma_short_recent_5 = [sum(c[i-ma_short_w+k:i+k]) / ma_short_w for k in range(-5, 1)]
    if len(ma_short_recent_5) < 2:
        return False
    ma50_angle = (ma_short_recent_5[-1] - ma_short_recent_5[0]) / ma_short_recent_5[0] * 100
    return ma50_angle >= ma_angle_min
