"""
detectors/markup_entry.py - MarkupEntry 主升浪起点 (跟 WyckoffTradingAgent 1742 行 1:1)

触发: MA50/MA200 金叉 + 持续 N 日在 MA200 上方 + MA50 角度足够
返回: bool

2026-11-03 改 1: MA200 用**真 200 日均线** (precomputed_arrs['ma200']),
   不再用 precomputed_arrs['ma_window'] (window=250 均线).
   参考实现 WTA 用的是 close.rolling(cfg.ma_long=200).mean() —— 真 200 日均线.
   原来用 ma_window 会让"MA50 刚上穿 250 日线"的票被误判为金叉
   (实测 688617: 只上穿 250 日线, 从未上穿真 200 日线, 却被判 True).

2026-11-03 改 2: "MA50 是否够到 MA200" 统一走 helpers._ma50_reached_ma200
   (0.95 容差), 跟 stage_factor.detect_markup_entry 保持同一口径 ——
   两条路径原本用不同容差, 会给出互相矛盾的结论。
"""
from ..helpers import _bias_200_exceeds_limit, _ma50_reached_ma200


def detect_markup_entry(c, h, l, v, o, i, ma_short_w=50, ma_long_w=200,
                        confirm_days=5, ma_angle_min=2.0,
                        precomputed_arrs=None) -> bool:
    """MarkupEntry 检测 (1:1 搬运 WyckoffTradingAgent 1742 行)"""
    n = len(c)
    if i < ma_long_w + 1:
        return False
    # MA50 / MA200 — 优先用预算数组
    #   注意: ma200 用 arrs['ma200'] (真 200 日均线), 不用 arrs['ma_window'] (250 日均线)
    if precomputed_arrs is not None:
        ma_short = precomputed_arrs['ma50'][i]
        ma_long  = precomputed_arrs['ma200'][i]
    else:
        ma_short = sum(c[i-ma_short_w:i]) / ma_short_w
        ma_long  = sum(c[i-ma_long_w:i])  / ma_long_w
    if not _ma50_reached_ma200(ma_short, ma_long):
        return False
    # 过去 confirm_days*2 内找穿过点 — 直接查预算数组切片
    lookback = max(confirm_days * 2, 10)
    if i < lookback + ma_long_w:
        return False
    if precomputed_arrs is not None:
        ma_short_recent = precomputed_arrs['ma50'][i-lookback:i+1]
        ma_long_recent  = precomputed_arrs['ma200'][i-lookback:i+1]
    else:
        ma_short_recent = [sum(c[i-ma_short_w+k:i+k]) / ma_short_w for k in range(-lookback+1, 1)]
        ma_long_recent  = [sum(c[i-ma_long_w+k:i+k])  / ma_long_w  for k in range(-lookback+1, 1)]
    crossover_found = False
    for j in range(1, len(ma_short_recent)):
        # 同一 0.95 容差口径: 之前"不够到", 之后"够到"
        if (not _ma50_reached_ma200(ma_short_recent[j-1], ma_long_recent[j-1])
                and _ma50_reached_ma200(ma_short_recent[j], ma_long_recent[j])):
            crossover_found = True
            break
    if not crossover_found:
        return False
    # 最近 confirm_days 持续在 MA200 上方
    if i < confirm_days + ma_short_w:
        return False
    recent_above = sum(1 for j in range(-confirm_days, 0)
                       if _ma50_reached_ma200(ma_short_recent[j], ma_long_recent[j]))
    if recent_above < confirm_days:
        return False
    # MA50 角度 (5 日变化率) — 直接查预算数组切片
    if i < 6:
        return False
    if precomputed_arrs is not None:
        ma_short_recent_5 = precomputed_arrs['ma50'][i-5:i+1]
    else:
        ma_short_recent_5 = [sum(c[i-ma_short_w+k:i+k]) / ma_short_w for k in range(-5, 1)]
    if len(ma_short_recent_5) < 2:
        return False
    ma50_angle = (ma_short_recent_5[-1] - ma_short_recent_5[0]) / ma_short_recent_5[0] * 100
    return ma50_angle >= ma_angle_min
