"""
detectors/compression.py - Compression 压缩蓄势 (跟 WyckoffTradingAgent 1521 行 1:1)

触发: 连续 N 日 ATR 收窄 (分位数) + 缩量 + 方向向上
返回: bool (v5.3 分位数算法, 跟 WyckoffTradingAgent _compression_atr_ratio 1:1)
"""
from ..helpers import _compression_direction_ok, _compression_bias_ok, _compression_atr_ratio


def detect_compression(c, h, l, v, i, lookback=5, atr_window=20, atr_quantile=0.20,
                       vol_decline_ratio=0.60, max_bias=25.0,
                       precomputed_ma200=None,
                       precomputed_arrs=None) -> bool:
    """Compression 检测 (1:1 搬运 WyckoffTradingAgent 1521 行, v5.3 分位数)"""
    if i < atr_window + lookback + 5:
        return False
    if i < atr_window + lookback + 1:
        return False
    if i < lookback + 1:
        return False
    # 1. direction_ok: 短期 MA >= 长期 MA
    ma_now  = precomputed_arrs['ma20'][i-1]         if precomputed_arrs is not None else None
    ma_prev = precomputed_arrs['ma20'][i-1-lookback] if precomputed_arrs is not None and i >= lookback + 1 else None
    if not _compression_direction_ok(c[:i], ma_window=20, lookback=lookback,
                                     precomputed_ma=ma_now, precomputed_ma_prev=ma_prev):
        return False
    # 2. bias_ok
    if not _compression_bias_ok(c[:i], max_bias, precomputed_ma200=precomputed_ma200):
        return False
    # 3. 缩量: 近期均量 / 基线均量 < vol_decline_ratio (默认 0.60 = "近期比基线少 40% 才算缩量")
    # 命名跟 stage_factor 统一: vol_recent_avg / vol_baseline_avg (2026-10-17)
    if i < atr_window + lookback + 1:
        return False
    vol_baseline_avg = sum(v[i-atr_window-lookback-1:i-lookback-1]) / atr_window
    vol_recent_avg = sum(v[i-lookback-1:i]) / lookback
    if vol_baseline_avg <= 0 or vol_recent_avg / vol_baseline_avg > vol_decline_ratio:
        return False
    # 4. ATR 收窄 (分位数算法, 1:1 对齐 WyckoffTradingAgent 1488 行)
    if _compression_atr_ratio(c[:i], h[:i], l[:i],
                              lookback, atr_window, atr_quantile) is None:
        return False
    return True
