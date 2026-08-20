"""
chan/inclusion.py - K 线包含关系处理 (缠论 Step 1)

从 tools/chan_analysis.py merge_inclusion 搬过来 (跟原版 1:1)
"""
from typing import List, Tuple


def merge_inclusion(dates, closes, highs, lows) -> List[Tuple]:
    """Step1: 处理 K 线包含关系

    输入 4 个平行 list (dates/closes/highs/lows), 长度相同
    返回 list of (date, close, high, low) tuple, 已合并包含
    """
    m = [(dates[0], closes[0], highs[0], lows[0])]
    for i in range(1, len(dates)):
        pd, pc, ph, pl = m[-1]
        ch, cl = highs[i], lows[i]
        if (ch <= ph and cl >= pl) or (ch >= ph and cl <= pl):
            dirn = 1 if (len(m) >= 2 and ph > m[-2][2]) else -1
            if dirn == 1:
                m[-1] = (pd, closes[i], max(ph, ch), max(pl, cl))
            else:
                m[-1] = (pd, closes[i], min(ph, ch), min(pl, cl))
        else:
            m.append((dates[i], closes[i], highs[i], lows[i]))
    return m
