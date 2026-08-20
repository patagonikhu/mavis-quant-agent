"""
chan/verdict.py - 缠论综合判定因子

把 dump_data._factor_chan_signals 末段 (line 324-336) 缠论综合判定提炼成独立因子

输入: bot_60, top_60, stop_signal, d_pos (4 个判定值)
输出: dict {verdict: str} — 跟原 verdict 变量完全一致
"""
import pandas as pd
from tools.factors.base import Factor


class ChanVerdictFactor(Factor):
    """缠论综合判定因子 (5 档)

    判定优先级 (跟原 dump_data 完全一致):
      1. 60分底背 + 止跌信号 → 🟢 建仓窗口
      2. 60分底背            → 🟡 关注
      3. 60分顶背 + 中枢上方  → 🔴 减仓
      4. 中枢下方            → 🟢 超跌反弹
      5. 中枢上方            → 🟡 持有
      6. 其他                → ⚪ 观望
    """

    name = "chan_verdict"
    category = "chan"
    dependencies = []
    description = "缠论综合判定 (6 档: 建仓/关注/减仓/超跌/持有/观望)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        bot_60 = kwargs.get("bot_60", False)
        top_60 = kwargs.get("top_60", False)
        stop_signal = kwargs.get("stop_signal", False)
        d_pos = kwargs.get("d_pos", "")

        if bot_60 and stop_signal:
            verdict = "🟢 建仓窗口 (60分底背 + 止跌信号)"
        elif bot_60:
            verdict = "🟡 关注 (60分底背, 等止跌)"
        elif top_60 and "上方" in d_pos:
            verdict = "🔴 减仓 (日线顶背 + 中枢上方)"
        elif d_pos == "下方⚠️":
            verdict = "🟢 超跌反弹窗口 (中枢下方)"
        elif d_pos == "上方✅":
            verdict = "🟡 持有 (中枢上方, 不追)"
        else:
            verdict = "⚪ 观望 (方向未定)"

        return {"verdict": verdict}
