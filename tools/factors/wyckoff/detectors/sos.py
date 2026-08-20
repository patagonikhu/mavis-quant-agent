"""
detectors/sos.py - SOS 强势信号 (跟 WyckoffTradingAgent 1416 行 1:1)

触发: 单日涨幅 ≥6% + 量比≥3.0× + 95% 分位 + 60 日新高 OR MA50 交叉
返回: bool
"""
from ..helpers import _bias_200_exceeds_limit, _is_frozen_board_day, _sos_volume_ratio, _sos_breakout_or_ma_cross


def detect_sos(c, h, l, v, o, i, vol_window=20, vol_ratio=3.0, vol_quantile_window=60,
               vol_quantile=0.95, breakout_window=60, breakout_tolerance=0.01,
               pct_min=6.0, max_bias=25.0, pct_chg=None) -> bool:
    """SOS 检测 (1:1 搬运 WyckoffTradingAgent 1416 行)"""
    n = len(c)
    if i < max(vol_window, breakout_window) + 2:
        return False
    if _bias_200_exceeds_limit(c[:i], max_bias):
        return False
    if _is_frozen_board_day(o[i-1], h[i-1], l[i-1], c[i-1]):
        return False
    if i < 2:
        return False
    # 单日涨幅 (pct_chg 优先)
    if pct_chg is not None and i - 1 < len(pct_chg):
        day_pct = pct_chg[i - 1]
    else:
        day_pct = (c[i-1] / c[i-2] - 1) * 100
    if day_pct < pct_min:
        return False
    # 量比 (用 _sos_volume_ratio 抽象, 含 quantile 阈值)
    if _sos_volume_ratio(v[:i], vol_quantile_window, vol_ratio, vol_quantile) is None:
        return False
    # 突破 (用 _sos_breakout_or_ma_cross 抽象)
    if not _sos_breakout_or_ma_cross(c[:i], h[:i], breakout_window, breakout_tolerance):
        return False
    return True
