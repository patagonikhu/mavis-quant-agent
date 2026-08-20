"""
smc/atr.py - ATR (Average True Range, SMC 算法基础)

从 tools/smc.py calc_atr 搬过来 (跟原版 1:1)
"""
from typing import List, Optional


def calc_atr(highs: List[float], lows: List[float], closes: List[float],
             period: int = 14) -> Optional[float]:
    """
    ATR - 平均真实波幅
    True Range = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR = 14 根 TR 的平均

    对齐 OpenMobius: kb_klines.py:481 calc_atr
    """
    n = len(closes)
    if n < period + 1 or n < 2:
        return None
    trs = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        trs.append(tr)
    recent_trs = trs[-period:]
    return sum(recent_trs) / period if recent_trs else None
