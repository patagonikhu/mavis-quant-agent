"""
tools/factors/factor_risk.py — 风控类 factor 库 (纯函数, 2026-09-09 统一)

合并来源 (2026-09-09 之前散落的 class Factor 全部改成纯函数):
  - tools/factors/risk/monitor_triggers.py (MonitorTriggersFactor)

公开 API (1 个):
  - compute_monitor_triggers  监控触发点 (缠论+中枢+事件)

2026-11-03 删: compute_stop_profit_loss (止盈 3 层 + 止损 4 档整档已删)
  → 价位是"当前价 × 固定百分比"的机械外推, 无标的特异性判断

2026-11-03 删: compute_exit_signals (退场信号检查整档已删)
  → 纯缠论退出判定, 与 监控触发点 重叠;
    卖点 (1卖/2卖/3卖) 仍由 缠论买卖点 section 提供
"""
from __future__ import annotations


def compute_monitor_triggers(
    price: float = 0,
    chan_d: dict = None,
    events: list = None,
    code: str = "",
    chan_signals: dict = None,
) -> dict:
    """监控触发点 (2026-09-09 改: 删 fflow 触发, 纯缠论 + 中枢位 + 事件)

    输出字段 (dict):
      - 缠论_60分底背/止跌信号/60分顶背/综合判定: 4 个缠论触发
      - 中枢_突破上沿/跌破下沿: 2 个中枢位触发
      - 时间触发: list (events 参数, v6.2 起调用方传)
    """
    chan_d = chan_d or {}
    events = events or []
    chan_signals = chan_signals or {}

    # 从 events 参数 (调用方传) 找时间触发
    time_triggers = []
    for ev in events:
        if ev.get("code") == code:
            time_triggers.append({
                "时间": ev.get("event_date", ""),
                "事件": ev.get("description", "")[:50],
                "操作": "业绩兑现加仓 / 不达预期减仓",
            })

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
