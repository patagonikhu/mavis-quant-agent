"""
smc/fvg.py - Fair Value Gap (FVG) 算法

从 tools/smc.py find_fvg / _fvg_mitigation_pct 搬过来 (跟原版 1:1)
"""
from typing import List, Dict
from .atr import calc_atr


def _fvg_mitigation_pct(top: float, bottom: float, fvg_type: str,
                        highs: List[float], lows: List[float], closes: List[float],
                        formed_index: int) -> float:
    """
    FVG mitigation 进度 (回补百分比)
      0% = 完全没回补, 100% = 完全回补
    对齐 OpenMobius: kb_klines.py:515
    """
    if formed_index + 1 >= len(closes):
        return 0.0
    future_highs = highs[formed_index + 1:]
    future_lows = lows[formed_index + 1:]
    future_closes = closes[formed_index + 1:]

    size = top - bottom
    if size <= 0:
        return 0.0

    if fvg_type == "bull":
        if not future_lows:
            return 0.0
        min_low = min(future_lows)
        if min_low >= top:
            return 0.0
        if min_low <= bottom:
            return 100.0
        return round((top - min_low) / size * 100, 1)
    else:
        if not future_highs:
            return 0.0
        max_high = max(future_highs)
        if max_high <= bottom:
            return 0.0
        if max_high >= top:
            return 100.0
        return round((max_high - bottom) / size * 100, 1)


def find_fvg(opens: List[float], highs: List[float], lows: List[float],
             closes: List[float], dates: List[str],
             lookback: int = 50,
             min_size_atr: float = 0.2) -> List[Dict]:
    """
    找 Fair Value Gap (FVG) - 对齐 OpenMobius find_fvgs

    算法:
      1. 3 根 K 线 non-overlap
      2. 中间 K 线必须是同向
      3. gap 尺寸 ≥ min_size_atr × ATR
      4. mitigation_pct 字段
    """
    fvgs = []
    n = min(lookback, len(closes))
    if n < 3:
        return fvgs

    atr = calc_atr(highs, lows, closes) or 0
    min_size = min_size_atr * atr if atr > 0 else 0

    for i in range(1, n - 1):
        idx_k1 = i - 1
        idx_mid = i
        idx_k3 = i + 1

        # 看涨 FVG (K1.high < K3.low)
        if highs[idx_k1] < lows[idx_k3]:
            if closes[idx_mid] > opens[idx_mid]:
                top = lows[idx_k3]
                bottom = highs[idx_k1]
                gap_size = top - bottom
                if gap_size < min_size:
                    continue
                fvg = {
                    "type": "bull",
                    "top": round(top, 4),
                    "bottom": round(bottom, 4),
                    "formed_at_index": idx_mid,
                    "date": dates[idx_mid],
                    "age_bars": len(closes) - 1 - idx_mid,
                    "size": round(gap_size, 4),
                    "size_atr": round(gap_size / atr, 2) if atr > 0 else 0,
                    "mitigation_pct": _fvg_mitigation_pct(
                        top, bottom, "bull", highs, lows, closes, idx_mid
                    ),
                }
                fvgs.append(fvg)
        # 看跌 FVG (K1.low > K3.high)
        elif lows[idx_k1] > highs[idx_k3]:
            if closes[idx_mid] < opens[idx_mid]:
                top = lows[idx_k1]
                bottom = highs[idx_k3]
                gap_size = top - bottom
                if gap_size < min_size:
                    continue
                fvg = {
                    "type": "bear",
                    "top": round(top, 4),
                    "bottom": round(bottom, 4),
                    "formed_at_index": idx_mid,
                    "date": dates[idx_mid],
                    "age_bars": len(closes) - 1 - idx_mid,
                    "size": round(gap_size, 4),
                    "size_atr": round(gap_size / atr, 2) if atr > 0 else 0,
                    "mitigation_pct": _fvg_mitigation_pct(
                        top, bottom, "bear", highs, lows, closes, idx_mid
                    ),
                }
                fvgs.append(fvg)
    return fvgs
