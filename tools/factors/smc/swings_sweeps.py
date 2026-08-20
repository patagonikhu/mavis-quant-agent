"""
smc/swings_sweeps.py - Swing 高低点 + Liquidity Sweep 算法

从 tools/smc.py find_swings / find_liquidity_sweeps 搬过来 (跟原版 1:1)
"""
from typing import List, Dict


def find_swings(highs: List[float], lows: List[float],
                left: int = 2, right: int = 2) -> List[Dict]:
    """
    找 swing 高低点

    swing high: 最高点 > 左右各 N 根 K 线的高点
    swing low:  最低点 < 左右各 N 根 K 线的低点

    对齐 OpenMobius: kb_klines.py:499 find_swings
    """
    swings = []
    n = len(highs)
    for i in range(left, n - right):
        if all(highs[i] > highs[i - j] for j in range(1, left + 1)) and \
           all(highs[i] > highs[i + j] for j in range(1, right + 1)):
            swings.append({
                "index": i,
                "price": highs[i],
                "kind": "high"
            })
        if all(lows[i] < lows[i - j] for j in range(1, left + 1)) and \
           all(lows[i] < lows[i + j] for j in range(1, right + 1)):
            swings.append({
                "index": i,
                "price": lows[i],
                "kind": "low"
            })
    return swings


def find_liquidity_sweeps(opens: List[float], highs: List[float], lows: List[float],
                          closes: List[float], dates: List[str],
                          lookback: int = 30,
                          swing_lookback: int = 15) -> List[Dict]:
    """
    找流动性扫 (Liquidity Sweep) - 对齐 OpenMobius find_sweeps

    算法 (5 步):
      1. find_swings 找 swing high/low
      2. 遍历每根 K 线
      3. 过滤 15 根 lookback
      4. 检查 K 线穿越 + 收回
      5. 输出 buy_side / sell_side sweep
    """
    sweeps = []
    n = len(closes)
    if n < 5:
        return sweeps
    swings = find_swings(highs, lows)
    swing_highs = [(s["index"], s["price"]) for s in swings if s["kind"] == "high"]
    swing_lows = [(s["index"], s["price"]) for s in swings if s["kind"] == "low"]

    start_i = max(1, n - lookback)
    for i in range(start_i, n):
        c = closes[i]
        h = highs[i]
        l = lows[i]

        # buy-side sweep (扫 swing high)
        for sh_idx, sh_price in swing_highs:
            if sh_idx >= i:
                continue
            if i - sh_idx > swing_lookback:
                continue
            if h > sh_price and c < sh_price:
                wick = h - max(opens[i], c)
                sweeps.append({
                    "type": "buy_side_sweep",
                    "swept_level": round(sh_price, 4),
                    "swept_level_index": sh_idx,
                    "sweep_candle_index": i,
                    "date": dates[i],
                    "age_bars": n - 1 - i,
                    "wick_size": round(wick, 4)
                })
                break

        # sell-side sweep (扫 swing low)
        for sl_idx, sl_price in swing_lows:
            if sl_idx >= i:
                continue
            if i - sl_idx > swing_lookback:
                continue
            if l < sl_price and c > sl_price:
                wick = min(opens[i], c) - l
                sweeps.append({
                    "type": "sell_side_sweep",
                    "swept_level": round(sl_price, 4),
                    "swept_level_index": sl_idx,
                    "sweep_candle_index": i,
                    "date": dates[i],
                    "age_bars": n - 1 - i,
                    "wick_size": round(wick, 4)
                })
                break

    return sweeps
