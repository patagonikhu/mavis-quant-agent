"""
risk/stop_profit_loss.py - 止盈 3 层 + 止损 4 档 因子 (Day D4, 2026-07-27)

把 原 dump_data.calc_stop_profit_loss (原 line 557-603, 47 行) 提炼成独立 factor

3 层止盈: +2% 中枢上沿 / +50% / +100%
4 档止损: -2% 中枢下沿 (或 -10%) / -15% / -25% / -35%
60分顶背触发 → 止损更紧

输入: price, chan_signals
输出: dict {止盈3层: [...], 止损4档: [...], 缠论信号: {...}}
"""
import pandas as pd
from tools.factors.base import Factor


class StopProfitLossFactor(Factor):
    """止盈止损因子

    输出字段 (dict):
      - 止盈3层: 3 档止盈 (含涨幅/触发价/操作)
      - 止损4档: 4 档止损 (含跌幅/触发价/操作)
      - 缠论信号: 4 个固化信号
    """

    name = "stop_profit_loss"
    category = "risk"
    dependencies = []
    description = "止盈 3 层 + 止损 4 档 (缠论中枢位)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        price = kwargs.get("price", 0) or 0
        chan_signals = kwargs.get("chan_signals") or {}

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

        # 如果 60分顶背触发, 止损更紧 (立即减仓)
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
