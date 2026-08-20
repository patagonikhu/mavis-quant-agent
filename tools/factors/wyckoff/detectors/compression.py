"""
detectors/compression.py - Compression 压缩蓄势 (跟 WyckoffTradingAgent 1521 行 1:1)

触发: 连续 N 日 ATR 收窄 (分位数) + 缩量 + 方向向上
返回: bool (v5.3 分位数算法, 跟 WyckoffTradingAgent _compression_atr_ratio 1:1)
"""
from ..helpers import _compression_direction_ok, _compression_bias_ok, _compression_atr_ratio


def detect_compression(c, h, l, v, o, i, lookback=5, atr_window=20, atr_quantile=0.20,
                       vol_decline_ratio=0.60, max_bias=25.0, pct_chg=None) -> bool:
    """Compression 检测 (1:1 搬运 WyckoffTradingAgent 1521 行, v5.3 分位数)"""
    n = len(c)
    if i < atr_window + lookback + 5:
        return False
    if i < atr_window + lookback + 1:
        return False
    if i < lookback + 1:
        return False
    # 1. direction_ok: 短期 MA >= 长期 MA
    if not _compression_direction_ok(c[:i], ma_window=20, lookback=lookback):
        return False
    # 2. bias_ok
    if not _compression_bias_ok(c[:i], max_bias):
        return False
    # 3. 缩量: recent 5 日均量 / 前 25 日均量
    if i < atr_window + lookback + 1:
        return False
    vol_ref = sum(v[i-atr_window-lookback-1:i-lookback-1]) / atr_window
    vol_recent = sum(v[i-lookback-1:i]) / lookback
    if vol_ref <= 0 or vol_recent / vol_ref > vol_decline_ratio:
        return False
    # 4. ATR 收窄 (分位数算法, 1:1 对齐 WyckoffTradingAgent 1488 行)
    if _compression_atr_ratio(c[:i], h[:i], l[:i], v[:i],
                              lookback, atr_window, atr_quantile) is None:
        return False
    return True
