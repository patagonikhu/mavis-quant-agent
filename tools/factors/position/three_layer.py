"""
position/three_layer.py - 三层仓位策略因子 (Day D2, 2026-07-27)

把 原 dump_data.calc_three_layer_position (原 line 518-629, 114 行) 提炼成独立 factor

4 层仓位: 逆势仓 (10-15%) / 底仓 (25-30%) / 中仓 (20-25%) / 波动仓 (20-25%)
+ 止损阶梯 + 目标阶梯

输入: price, chan_d (日线结果), fflow, peg, chan_signals (缠论固化信号)
输出: dict (市况/三级别中枢/位置/4 层仓位/止损阶梯/目标阶梯)
"""
import pandas as pd
from tools.factors.base import Factor


class ThreeLayerPositionFactor(Factor):
    """三层仓位策略因子 (含逆势仓/底仓/中仓/波动仓 + 止损目标阶梯)

    输出字段 (dict):
      - 市况: 缠论综合判定
      - 三级别中枢: 周/日/60分 中枢摘要
      - 位置: below/inside/above
      - 缠论信号: 5 个信号触发状态
      - 逆势仓/底仓/中仓/波动仓: 4 个仓位 dict (含进场/止损/目标/已触发)
      - 止损阶梯: 3 档 (逆势仓/波动仓/底仓)
      - 目标阶梯: 3 档 (第一关/第二关/第三关)
    """

    name = "three_layer_position"
    category = "position"
    dependencies = []  # 用 price + chan_d + fflow + peg + chan_signals
    description = "三层仓位策略 (逆势/底/中/波动 + 止损目标阶梯)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        price = kwargs.get("price", 0) or 0  # 2026-07-27 兜底防 None
        chan_d = kwargs.get("chan_d") or {}
        fflow = kwargs.get("fflow") or {}
        peg = kwargs.get("peg", 0)
        chan_signals = kwargs.get("chan_signals") or {}

        # 当前 fflow 数据 (兼容: real 可能为空列表)
        fflow_real = (fflow.get("data_columns") or {}).get("real") or []
        fflow_today = fflow_real[0] if fflow_real else {}
        fflow_main = fflow_today.get("main_yi", 0) if fflow_today else 0

        # 日线中枢
        hub = chan_d.get("hub", {}) if chan_d else {}
        hub_low = hub.get("low", 0)
        hub_high = hub.get("high", 0)
        hub_pos = str(hub.get("pos", ""))

        # 缠论信号 (从固化字段读)
        bot_60 = chan_signals.get("60min_底背", False)
        top_60 = chan_signals.get("60min_顶背", False)
        stop_sig = chan_signals.get("止跌信号", False)
        chan_verdict = chan_signals.get("缠论综合", "—")

        # 距当前价与中枢关系
        if hub_low and price < hub_low:
            zone = "below"
        elif hub_high and price > hub_high:
            zone = "above"
        else:
            zone = "inside"

        # 4 层仓位 + 止损 + 目标
        if zone == "below" and hub_low and hub_high:
            t1 = f"¥{hub_low:.2f} (日线下沿第一关)"
            t2 = f"¥{hub_high:.2f} (日线上沿第二关)"
            t3 = f"¥{hub_high * 1.10:.2f} (上沿+10%)"
        elif zone == "inside" and hub_high:
            t1 = f"¥{hub_high:.2f} (日线上沿)"
            t2 = f"¥{hub_high * 1.10:.2f} (上沿+10%)"
            t3 = f"¥{hub_high * 1.20:.2f} (上沿+20%)"
        elif zone == "above":
            t1 = f"¥{price * 1.05:.2f} (现价+5% 顺势第一关)"
            t2 = f"¥{price * 1.10:.2f} (现价+10% 顺势第二关)"
            t3 = f"¥{price * 1.20:.2f} (现价+20% 顺势第三关)"
        else:
            t1 = f"¥{price * 1.10:.2f} (+10%)"
            t2 = f"¥{price * 1.20:.2f} (+20%)"
            t3 = f"¥{price * 1.30:.2f} (+30%)"

        return {
            "市况": chan_verdict,
            "三级别中枢": {
                "周线": "无 (下跌延伸) / 上方 ✅ / 内部 ⬜" if hub_pos else "无",
                "日线": f"¥{hub_low} ~ ¥{hub_high} {hub_pos}" if hub_low else "无",
                "60分": "(同 JSON)",
            },
            "位置": zone,
            "缠论信号": {
                "60分_底背": "🟢 触发" if bot_60 else "❌",
                "60分_顶背": "🔴 触发" if top_60 else "❌",
                "止跌信号": "🟢 触发" if stop_sig else "❌",
                "威科夫阶段": chan_signals.get("威科夫阶段", "—"),
                "缠论综合": chan_verdict,
            },
            "逆势仓": {
                "仓位": "10-15%",
                "进场": "PEG<1.0 ✅ + 60分底背 + 止跌信号 (3/3 全满足)",
                "止损": f"¥{price * 0.85:.2f} (结构低点 -1×ATR)",
                "目标": t1,
                "已触发": fflow_main > 0 and peg < 1.0 and bot_60 and stop_sig,
            },
            "底仓": {
                "仓位": "25-30%",
                "进场": "60分底背驰 + 站上 ¥" + f"{price * 1.05:.2f}",
                "止损": f"¥{price * 0.97:.2f}",
                "目标": t1,
                "已触发": fflow_main > 0,
            },
            "中仓": {
                "仓位": "20-25%",
                "进场": f"站上 ¥{hub_high:.2f} 稳 3 日" if hub_high else f"站上 ¥{price * 1.20:.2f}",
                "止损": f"¥{hub_high:.2f}" if hub_high else f"¥{price * 1.05:.2f}",
                "目标": t2,
            },
            "波动仓": {
                "仓位": "20-25%",
                "进场": "60分中枢内 + 量比放大",
                "止损": f"¥{price * 0.92:.2f}",
                "目标": t2,
            },
            "止损阶梯": [
                f"¥{price * 0.85:.2f} → 逆势仓止损",
                f"¥{price * 0.92:.2f} → 波动仓全减",
                f"¥{price * 0.97:.2f} → 底仓减半",
            ],
            "目标阶梯": [t1, t2, t3],
        }
