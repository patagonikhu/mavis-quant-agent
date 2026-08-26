"""
detectors/spring.py - Spring 终极震仓 (跟 WyckoffTradingAgent 1093 行 1:1)

触发: 允许前一日或当日盘中跌破近 N 日支撑位, 当日收盘收回 + 放量 + 前日量扩张
返回: bool
"""
from ..helpers import _is_trading_range_context, _spring_support_level, _bias_200_exceeds_limit, _is_frozen_board_day


def detect_spring(c, h, l, v, o, i, support_window=60, vol_ratio=1.3, max_bias=15.0,
                  precomputed_atr=None) -> bool:
    """Spring 检测 (1:1 搬运 WyckoffTradingAgent 1093 行)"""
    n = len(c)
    if i < support_window + 2:
        return False
    # 支撑位不能包含 prev (i-1)
    support_zone = list(zip(
        h[max(0, i - support_window - 2):i - 1] if i >= 1 else [],
        l[max(0, i - support_window - 2):i - 1] if i >= 1 else [],
        c[max(0, i - support_window - 2):i - 1] if i >= 1 else []
    ))
    zone_h = [x[0] for x in support_zone if x[0] is not None]
    zone_l = [x[1] for x in support_zone if x[1] is not None]
    zone_c = [x[2] for x in support_zone if x[2] is not None]
    full_h = h[:i]
    full_l = l[:i]
    full_c = c[:i]
    if not _is_trading_range_context(zone_h, zone_l, zone_c, full_h, full_l, full_c,
                                     precomputed_atr=precomputed_atr):
        return False
    support_level = _spring_support_level(zone_l, zone_c)
    if support_level <= 0:
        return False
    if i < 2:
        return False
    prev = {"low": l[i-2], "volume": v[i-2]}
    last = {"open": o[i-1], "high": h[i-1], "low": l[i-1], "close": c[i-1], "volume": v[i-1]}
    if _is_frozen_board_day(prev.get("open", last["close"]), prev["low"], prev["low"], prev.get("close", last["close"])):
        return False
    if _is_frozen_board_day(last["open"], last["high"], last["low"], last["close"]):
        return False
    if _bias_200_exceeds_limit(c[:i], max_bias):
        return False
    # 允许单日盘中洗盘: prev/last 至少一日跌破
    if prev["low"] >= support_level and last["low"] >= support_level:
        return False
    # 收盘收回
    if last["close"] <= support_level:
        return False
    # 5日均量 + 1.3×
    vol_avg_5 = sum(v[max(0, i-6):i-1]) / 5 if i >= 6 else 0
    if vol_avg_5 <= 0 or last["volume"] < vol_avg_5 * vol_ratio:
        return False
    # 当日放量比前日 1.15×
    if prev["volume"] > 0 and last["volume"] / prev["volume"] < 1.15:
        return False
    return True
