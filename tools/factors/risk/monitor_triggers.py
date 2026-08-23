"""
risk/monitor_triggers.py - 监控触发点因子 (Day D5, 2026-07-27)

把 老 data 工具.calc_monitor_triggers (原 line 568-634, 67 行) 提炼成独立 factor

监控 6 个触发: 缠论 60分底背/止跌/60分顶背/综合 + fflow 5日出货/今日进货 + 中枢突破/跌破
+ 时间触发 (从 events.json)

输入: price, chan_d, fflow, events, code, chan_signals
输出: dict (9 个触发器 + 时间触发)
"""
import pandas as pd
from tools.factors.base import Factor


class MonitorTriggersFactor(Factor):
    """监控触发点因子

    输出字段 (dict):
      - 缠论_60分底背/止跌信号/60分顶背/综合判定: 4 个缠论触发
      - fflow_5日_出货30亿/今日进货: 2 个 fflow 触发
      - 中枢_突破上沿/跌破下沿: 2 个中枢位触发
      - 时间触发: list (从 events.json 找)
    """

    name = "monitor_triggers"
    category = "risk"
    dependencies = []
    description = "监控触发点 (缠论+fflow+中枢+事件)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        price = kwargs.get("price", 0) or 0
        chan_d = kwargs.get("chan_d") or {}
        fflow = kwargs.get("fflow") or {}
        events = kwargs.get("events") or []
        code = kwargs.get("code", "")
        chan_signals = kwargs.get("chan_signals") or {}

        # 从 events.json 找时间触发
        time_triggers = []
        for ev in events:
            if ev.get("code") == code:
                time_triggers.append({
                    "时间": ev.get("event_date", ""),
                    "事件": ev.get("description", "")[:50],
                    "操作": "业绩兑现加仓 / 不达预期减仓",
                })

        fflow_today = ((fflow.get("data_columns") or {}).get("real") or [{}])[0]
        fflow_main = fflow_today.get("main_yi", 0) if fflow_today else 0
        hub = chan_d.get("hub", {}) if chan_d else {}
        hub_low = hub.get("low", 0)
        hub_high = hub.get("high", 0)

        bot_60 = chan_signals.get("60min_底背", False)
        top_60 = chan_signals.get("60min_顶背", False)
        stop_sig = chan_signals.get("止跌信号", False)
        chan_verdict = chan_signals.get("缠论综合", "—")

        return {
            "缠论_60分底背": {
                "条件": "60分底背驰触发 (1 买信号)",
                "操作": "建底仓 25-30%, 止损 ¥" + f"{price * 0.97:.2f}",
                "已触发": bot_60,
            },
            "缠论_止跌信号": {
                "条件": "缩量+长下影+次日不创新低 (3/3)",
                "操作": "确认反转, 加仓 5%",
                "已触发": stop_sig,
            },
            "缠论_60分顶背": {
                "条件": "60分顶背驰触发 (1 卖信号)",
                "操作": "减仓 1/3 (不等 -10%)",
                "已触发": top_60,
            },
            "缠论_综合判定": chan_verdict,
            "fflow_5日_出货30亿": {
                "条件": "5日主力净流出 > 30亿",
                "操作": "减中仓 1/3",
                "已触发": False,  # 简化为不在此处算
            },
            "fflow_今日进货": {
                "条件": "Tushare.money_flow 今日主力 +5亿以上",
                "操作": "确认主力进场, 可加仓",
                "已触发": fflow_main > 5,
            },
            "中枢_突破上沿": {
                "条件": f"站上 ¥{hub_high:.2f} 稳 3 日" if hub_high else f"站上 ¥{price * 1.20:.2f}",
                "操作": "加中仓 20-25%",
                "已触发": False,
            },
            "中枢_跌破下沿": {
                "条件": f"跌破 ¥{hub_low:.2f} (缠论破位)" if hub_low else f"跌破 ¥{price * 0.90:.2f}",
                "操作": "减仓 1/3 + 警惕主升浪结束",
                "已触发": False,
            },
            "时间触发": time_triggers,
        }
