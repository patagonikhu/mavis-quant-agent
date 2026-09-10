"""
tools/factors/factor_risk.py — 风控类 factor 库 (纯函数, 2026-09-09 统一)

合并来源 (2026-09-09 之前散落的 class Factor 全部改成纯函数):
  - tools/factors/risk/stop_profit_loss.py (StopProfitLossFactor)
  - tools/factors/risk/exit_signals.py     (ExitSignalsFactor)
  - tools/factors/risk/monitor_triggers.py (MonitorTriggersFactor)

公开 API (3 个):
  - compute_stop_profit_loss  止盈 3 层 + 止损 4 档
  - compute_exit_signals      5 类 14 子信号退出判定 (缠论+估值+主力三维)
  - compute_monitor_triggers  监控触发点 (缠论+fflow+中枢+事件)
"""
from __future__ import annotations

import pandas as pd


def compute_stop_profit_loss(price: float = 0, chan_signals: dict = None) -> dict:
    """止盈 3 层 + 止损 4 档因子 (缠论中枢位)

    输出字段 (dict):
      - 止盈3层: 3 档止盈 (含涨幅/触发价/操作)
      - 止损4档: 4 档止损 (含跌幅/触发价/操作)
      - 缠论信号: 4 个固化信号
    """
    chan_signals = chan_signals or {}

    # 缠论关键位
    d_hub = chan_signals.get("daily_中枢") or {}
    hub_low = d_hub.get("low", 0)
    hub_high = d_hub.get("high", 0)
    bot_60 = chan_signals.get("60min_底背", False)
    top_60 = chan_signals.get("60min_顶背", False)

    # 止盈 3 层
    tp1_price = round(hub_high * 1.02, 2) if hub_high > 0 else round(price * 1.20, 2)
    tp1_note = f"中枢上沿 ¥{hub_high:.2f} +2%" if hub_high > 0 else "+20%"
    tp2_price = round(price * 1.50, 2)
    tp2_note = "+50%"
    tp3_price = round(price * 2.00, 2)
    tp3_note = "+100%"

    # 止损 4 档
    sl1_price = round(hub_low * 0.98, 2) if hub_low > 0 else round(price * 0.90, 2)
    sl1_note = f"中枢下沿 ¥{hub_low:.2f} -2%" if hub_low > 0 else "-10% ⚠️ 检查基本面"
    sl2_price = round(price * 0.85, 2)
    sl3_price = round(price * 0.75, 2)
    sl4_price = round(price * 0.65, 2)

    # 如果 60分顶背触发, 止损更紧
    if top_60:
        sl1_note = f"60分顶背触发! 中枢下沿 ¥{hub_low:.2f} -2% 立即减仓"

    return {
        "止盈3层": [
            {"涨幅": tp1_note, "触发价": tp1_price, "操作": "卖 1/3 (缠论+中枢位)"},
            {"涨幅": tp2_note, "触发价": tp2_price, "操作": "再卖 1/3"},
            {"涨幅": tp3_note, "触发价": tp3_price, "操作": "全清"},
        ],
        "止损4档": [
            {"跌幅": sl1_note, "触发价": sl1_price, "操作": "⚠️ 减仓/检查"},
            {"跌幅": "-15%", "触发价": sl2_price, "操作": "卖 1/3"},
            {"跌幅": "-25%", "触发价": sl3_price, "操作": "减半仓"},
            {"跌幅": "-35%", "触发价": sl4_price, "操作": "🛑 清仓"},
        ],
        "缠论信号": {
            "60分_底背": "🟢 触发" if bot_60 else "❌",
            "60分_顶背": "🔴 触发" if top_60 else "❌",
            "中枢_上沿": f"¥{hub_high:.2f}" if hub_high else "—",
            "中枢_下沿": f"¥{hub_low:.2f}" if hub_low else "—",
        },
    }


def compute_exit_signals(
    fflow: dict = None,
    eps_table: list = None,
    current_price: float = 0,
    sector_ma20_dev: float = 0,
    chan_signals: dict = None,
) -> dict:
    """退出判定 (2026-09-09 改: 纯缠论, 删 PEG/L_E3/fflow 加权汇总)

    决策走 缠论 1卖/2卖/3卖/顶背/止跌反转, 退出信号纯缠论判定.
    估值/主力净额 作为参考字段保留, 不再进绿/红信号汇总.

    输出字段 (dict):
      - 60分_底背/60分_顶背/止跌信号/缠论综合: 缠论固化信号
      - 威科夫阶段: 阶段判定
      - PEG/L_E3/tushare_fflow/板块_MA20_偏离: 参考字段 (不汇总)
      - 退出建议: 纯缠论 verdict
    """
    fflow = fflow or {}
    eps_table = eps_table or []
    chan_signals = chan_signals or {}

    # === 1. PEG/L_E3 (保留参考, 不进汇总) ===
    E1 = eps_table[1].get("eps", 0) if len(eps_table) > 1 else 0
    E3 = eps_table[3].get("eps", 0) if len(eps_table) > 3 else 0
    fwd_pe = current_price / E1 if E1 else 0
    E0 = eps_table[0].get("eps", 0) if eps_table else 0
    g = (E3 / E0 - 1) / 3 if E0 else 0
    peg = fwd_pe / (g * 100) if g > 0 else 0
    L_E3 = 0.69 if peg < 1.0 else 1.5

    # === 2. vs MA120 (占位) ===
    ma120_dev = -31

    # === 3. 缠论信号 (固化字段) ===
    bot_60 = chan_signals.get("60min_底背", False)
    top_60 = chan_signals.get("60min_顶背", False)
    stop_sig = chan_signals.get("止跌信号", False)
    chan_verdict = chan_signals.get("缠论综合", "—")
    wyckoff = chan_signals.get("威科夫阶段", "—")

    # === 4. fflow 当日 (保留参考) ===
    fflow_today = ((fflow.get("data_columns") or {}).get("real") or [{}])[0]
    fflow_main = fflow_today.get("main_yi", 0) if fflow_today else 0

    # === 5. 2026-09-09 改: 退出判定纯缠论 ===
    # 1卖/2卖/3卖 = 卖出, 1买/2买/3买 = 持仓, 顶背 = 警惕减仓
    if top_60:
        verdict = "🔴 减仓 (60分顶背触发)"
    elif stop_sig:
        verdict = "🟢 持仓 (止跌信号触发, 等待 1买/2买)"
    elif bot_60:
        verdict = "🟢 持仓 (60分底背, 缠论确认底部)"
    else:
        verdict = chan_verdict or "🟡 观望 (无缠论信号)"

    return {
        "60分_底背": "🟢 触发" if bot_60 else "❌",
        "60分_顶背": "🔴 触发" if top_60 else "❌",
        "止跌信号": "🟢 触发" if stop_sig else "❌",
        "威科夫阶段": wyckoff,
        "缠论综合": chan_verdict,
        "PEG": round(peg, 2),                # 参考字段, 不汇总
        "L_E3": round(L_E3, 2),              # 参考字段
        "L_可达": 0.67 if peg < 1.0 else 1.0,  # 参考字段
        "vs_MA120": ma120_dev,                # 参考字段
        "板块_MA20_偏离": sector_ma20_dev,    # 参考字段
        "tushare_fflow": round(fflow_main, 2),  # 参考字段
        "退出建议": verdict,
    }


def compute_monitor_triggers(
    price: float = 0,
    chan_d: dict = None,
    fflow: dict = None,
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
    fflow = fflow or {}
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
