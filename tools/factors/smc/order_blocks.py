"""
smc/order_blocks.py - Order Block (OB) 算法

从 tools/smc.py find_order_blocks 搬过来 (跟原版 1:1)
"""
from typing import List, Dict
from .atr import calc_atr


def find_order_blocks(opens: List[float], highs: List[float], lows: List[float],
                      closes: List[float], dates: List[str],
                      lookback: int = 50,
                      displacement_atr_mult: float = 1.5) -> Dict:
    """
    找 Order Block (OB) - 对齐 OpenMobius find_order_blocks

    算法:
      1. OB = "最后一根反向 K 线" + 紧接强势 displacement
      2. displacement = 后续 1-3 根 K 线的累计波动 ≥ 1.5 × ATR
      3. OB 区间: 看涨 [low, open] (主力最后买的位置), 看跌 [open, high] (主力最后卖的位置)
    """
    bull_obs = []
    bear_obs = []

    n = min(lookback, len(closes))
    if n < 4:
        return {"bull": bull_obs, "bear": bear_obs}

    atr = calc_atr(highs, lows, closes) or 0
    threshold = displacement_atr_mult * atr

    for i in range(n - 3):
        c = closes[i]
        o = opens[i]
        is_bullish = c > o

        next3 = []
        for j in range(1, 4):
            if i + j < n:
                next3.append({
                    'open': opens[i + j],
                    'high': highs[i + j],
                    'low': lows[i + j],
                    'close': closes[i + j]
                })
        if len(next3) < 3:
            continue

        # 看涨 OB: 当前是阴线, 紧接强势上涨
        if not is_bullish and c < o:
            move = next3[-1]['close'] - o
            cum_up = sum(max(0, x['close'] - x['open']) for x in next3)
            if threshold > 0 and (move > threshold and cum_up > threshold):
                ob = {
                    "type": "bull",
                    "top": round(o, 4),
                    "bottom": round(lows[i], 4),
                    "formed_at_index": i,
                    "date": dates[i],
                    "age_bars": n - 1 - i,
                    "displacement_atr": round(move / atr, 2) if atr > 0 else 0,
                }
                bull_obs.append(ob)

        # 看跌 OB: 当前是阳线, 紧接强势下跌
        elif is_bullish and c > o:
            move = o - next3[-1]['close']
            cum_dn = sum(max(0, x['open'] - x['close']) for x in next3)
            if threshold > 0 and (move > threshold and cum_dn > threshold):
                ob = {
                    "type": "bear",
                    "top": round(highs[i], 4),
                    "bottom": round(o, 4),
                    "formed_at_index": i,
                    "date": dates[i],
                    "age_bars": n - 1 - i,
                    "displacement_atr": round(move / atr, 2) if atr > 0 else 0,
                }
                bear_obs.append(ob)

    return {"bull": bull_obs, "bear": bear_obs}
