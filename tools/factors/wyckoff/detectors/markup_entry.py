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
   (0.90 容差, 常量 MA50_MA200_TOLERANCE), 跟 stage_factor.detect_markup_entry
   保持同一口径 —— 两条路径原本用不同容差, 会给出互相矛盾的结论。

2026-11-03 改 3: 拆出 markup_entry_conditions() —— 返回 4 条判据的**逐条结果**。
   报告渲染直接读它, 不再在 report_renderer 里手算一遍 (手算 = 两份实现必然漂移)。
   detect_markup_entry() 变成它的薄封装, 行为与改前 100% 等价。
"""
from ..helpers import (MA50_MA200_TOLERANCE, _bias_200_exceeds_limit,
                      _ma50_reached_ma200)


def markup_entry_conditions(c, h, l, v, o, i, ma_short_w=50, ma_long_w=200,
                            confirm_days=5, ma_angle_min=2.0,
                            precomputed_arrs=None) -> dict:
    """MarkupEntry 4 条判据逐条结果 (单一事实源)

    返回 dict:
      ma50_reached : ① MA50 够到 MA200 (×MA50_MA200_TOLERANCE)
      golden_cross : ② 近 lookback 日内 MA50 从"不够到"→"够到" (金叉)
      above_5d     : ③ 最近 confirm_days 根 MA50 连续够到 MA200
      angle_ok     : ④ MA50 近 5 日角度 ≥ ma_angle_min
      hit          : 4 条全满足 (= detect_markup_entry 的返回值)
      lookback     : 金叉扫描窗口 (max(confirm_days*2, 10))
      pairs        : 实际扫描的 (前一日, 当日) 对数
      above_days   : 最近 5 根里"够到"的天数
      angle        : MA50 近 5 日角度 (%)
      ma50         : 当前 MA50
      ma200        : 当前真 MA200 (200 日均线)
      ma50_5d_ago  : 5 日前的 MA50 (角度分母)
      ma50_thr     : MA200 × 容差 (门槛)
    """
    res = {
        "ma50_reached": False, "golden_cross": False, "above_5d": False,
        "angle_ok": False, "hit": False,
        "lookback": max(confirm_days * 2, 10), "pairs": 0, "above_days": 0,
        "angle": 0.0, "ma50": 0.0, "ma200": 0.0, "ma50_5d_ago": 0.0,
        "ma50_thr": 0.0,
    }
    n = len(c)
    if i < 0 or i >= n:
        return res
    # MA50 / MA200 — 优先用预算数组
    #   注意: ma200 用 arrs['ma200'] (真 200 日均线), 不用 arrs['ma_window'] (250 日均线)
    if precomputed_arrs is not None:
        ma_short = precomputed_arrs['ma50'][i]
        ma_long = precomputed_arrs['ma200'][i]
        ma50_5 = precomputed_arrs['ma50'][i - 5] if i >= 5 else 0.0
    else:
        ma_short = sum(c[i - ma_short_w:i]) / ma_short_w
        ma_long = sum(c[i - ma_long_w:i]) / ma_long_w
        ma50_5 = (sum(c[i - 5 - ma_short_w:i - 5]) / ma_short_w) if i >= 5 else 0.0
    res["ma50"] = ma_short
    res["ma200"] = ma_long
    res["ma50_5d_ago"] = ma50_5
    res["ma50_thr"] = ma_long * MA50_MA200_TOLERANCE

    # ① MA50 够到 MA200 (带容差)
    res["ma50_reached"] = _ma50_reached_ma200(ma_short, ma_long)
    if i < ma_long_w + 1:
        return res

    # 过去 confirm_days*2 内找穿过点 — 直接查预算数组切片
    lookback = res["lookback"]
    if i < lookback + ma_long_w:
        return res
    if precomputed_arrs is not None:
        ma_short_recent = list(precomputed_arrs['ma50'][i - lookback:i + 1])
        ma_long_recent = list(precomputed_arrs['ma200'][i - lookback:i + 1])
    else:
        ma_short_recent = [sum(c[i - ma_short_w + k:i + k]) / ma_short_w for k in range(-lookback + 1, 1)]
        ma_long_recent = [sum(c[i - ma_long_w + k:i + k]) / ma_long_w for k in range(-lookback + 1, 1)]
    res["pairs"] = max(len(ma_short_recent) - 1, 0)

    # ② 金叉: 前一日"不够到" → 当日"够到" (同一容差口径)
    for j in range(1, len(ma_short_recent)):
        if (not _ma50_reached_ma200(ma_short_recent[j - 1], ma_long_recent[j - 1])
                and _ma50_reached_ma200(ma_short_recent[j], ma_long_recent[j])):
            res["golden_cross"] = True
            break

    # ③ 最近 confirm_days 持续够到 MA200 (range(-confirm_days, 0) → 最近 confirm_days 根, 不含当前根)
    res["above_days"] = sum(1 for j in range(-confirm_days, 0)
                            if _ma50_reached_ma200(ma_short_recent[j], ma_long_recent[j]))
    res["above_5d"] = res["above_days"] >= confirm_days

    # ④ MA50 角度 (5 日变化率) — 直接查预算数组切片
    if ma50_5 > 0:
        res["angle"] = (ma_short - ma50_5) / ma50_5 * 100
        res["angle_ok"] = res["angle"] >= ma_angle_min

    res["hit"] = bool(res["ma50_reached"] and res["golden_cross"]
                      and res["above_5d"] and res["angle_ok"])
    return res


def detect_markup_entry(c, h, l, v, o, i, ma_short_w=50, ma_long_w=200,
                        confirm_days=5, ma_angle_min=2.0,
                        precomputed_arrs=None) -> bool:
    """MarkupEntry 检测 (1:1 搬运 WyckoffTradingAgent 1742 行)"""
    cond = markup_entry_conditions(
        c, h, l, v, o, i, ma_short_w=ma_short_w, ma_long_w=ma_long_w,
        confirm_days=confirm_days, ma_angle_min=ma_angle_min,
        precomputed_arrs=precomputed_arrs)
    return cond["hit"]
