"""
chan/fenxing.py - 分型 (底/顶分型 + 确认, 缠论 1买/1卖基础)

从 tools/chan_analysis.py is_bottom_fenxing / is_top_fenxing / fenxing_confirmed / has_recent_confirmed_fenxing 搬过来 (跟原版 1:1)
"""


def is_bottom_fenxing(klines, i):
    """
    检查 klines[i] 是否是底分型中间 (即 i 位置是 3 根 K 线最低)
    底分型: klines[i].low < klines[i-1].low AND klines[i].low < klines[i+1].low
    """
    if i < 1 or i >= len(klines) - 1:
        return False
    return klines[i]["low"] < klines[i - 1]["low"] and klines[i]["low"] < klines[i + 1]["low"]


def is_top_fenxing(klines, i):
    """
    检查 klines[i] 是否是顶分型中间
    顶分型: klines[i].high > klines[i-1].high AND klines[i].high > klines[i+1].high
    """
    if i < 1 or i >= len(klines) - 1:
        return False
    return klines[i]["high"] > klines[i - 1]["high"] and klines[i]["high"] > klines[i + 1]["high"]


def fenxing_confirmed(klines, i, kind="bottom"):
    """
    检查底/顶分型是否被确认
    确认条件: 第 3 根 K 线收盘价突破中间 K 线
    - 底分型确认: klines[i+1].close > klines[i].close
    - 顶分型确认: klines[i+1].close < klines[i].close
    返回: True / False / None (不构成对应分型)
    """
    if kind == "bottom":
        if not is_bottom_fenxing(klines, i):
            return None
        return klines[i + 1]["close"] > klines[i]["close"]
    elif kind == "top":
        if not is_top_fenxing(klines, i):
            return None
        return klines[i + 1]["close"] < klines[i]["close"]
    return None


def has_recent_confirmed_fenxing(klines, lookback=5, kind="bottom"):
    """
    检查最近 lookback 根 K 線内是否有刚被确认的底/顶分型
    用于: 1买 = 背驰 + 分型确认; 1卖 = 顶背驰 + 顶分型确认
    返回: (是否确认, 中间K线索引, 中间K线价格) 或 (False, -1, 0)
    """
    if len(klines) < lookback + 2:
        return False, -1, 0
    for j in range(1, lookback + 1):
        i = len(klines) - 1 - j
        if i < 1:
            break
        if kind == "bottom":
            conf = fenxing_confirmed(klines, i, "bottom")
            if conf is True:
                return True, i, klines[i]["low"]
        else:
            conf = fenxing_confirmed(klines, i, "top")
            if conf is True:
                return True, i, klines[i]["high"]
    return False, -1, 0
