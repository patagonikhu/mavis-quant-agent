"""
chan/stop_signal.py - 止跌信号因子

把 老 data 工具._factor_chan_signals 的 8. 止跌信号段 (原 line 421-433) 提炼成独立因子

止跌信号 = 缩量 + 长下影 + 次日不创新低 (3 条件 AND 门)

输入: kd_day (list, 日线 K 线) — 至少 22 根
输出: dict {stop_signal: bool, vol_ok, shadow_ok, not_new_low, lower_shadow, ...}
"""
import pandas as pd
from tools.factors.base import Factor


class StopSignalFactor(Factor):
    """止跌信号因子: 缩量 + 长下影 + 次日不创新低 (3 条件 AND 门)

    输出字段 (dict):
      - stop_signal: bool (3 条件 AND)
      - vol_ok: 缩量 (vol < vol_ma20 * 0.85)
      - shadow_ok: 长下影 (lower_shadow > 0.4)
      - not_new_low: 次日不创新低
      - lower_shadow: 下影线比例 (0-1)
      - vol_ratio: vol / vol_ma20 (越低越缩量)
    """

    name = "stop_signal"
    category = "chan"
    dependencies = []  # 用 kd_day
    description = "止跌信号: 缩量 + 长下影 + 次日不创新低 (3 条件 AND)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        kd_day = kwargs.get("kd_day", [])

        # 默认空结果
        empty = {
            "stop_signal": False,
            "vol_ok": False,
            "shadow_ok": False,
            "not_new_low": False,
            "lower_shadow": 0.0,
            "vol_ratio": None,
        }

        if not kd_day or len(kd_day) < 22:
            return empty

        last = kd_day[-1]
        vol_ma20 = sum([k.get("volume", 0) for k in kd_day[-21:-1]]) / 20

        if last.get("high", 0) > last.get("low", 0):
            lower_shadow = (min(last.get("open", 0), last.get("close", 0)) - last.get("low", 0)) / (last.get("high", 0) - last.get("low", 0))
        else:
            lower_shadow = 0

        vol_ok = last.get("volume", 0) < vol_ma20 * 0.85
        shadow_ok = lower_shadow > 0.4
        not_new_low = kd_day[-1].get("low", 0) >= kd_day[-2].get("low", 0) * 0.995
        stop_signal = vol_ok and shadow_ok and not_new_low

        vol_ratio = last.get("volume", 0) / vol_ma20 if vol_ma20 > 0 else None

        return {
            "stop_signal": stop_signal,
            "vol_ok": vol_ok,
            "shadow_ok": shadow_ok,
            "not_new_low": not_new_low,
            "lower_shadow": round(lower_shadow, 3),
            "vol_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
        }
