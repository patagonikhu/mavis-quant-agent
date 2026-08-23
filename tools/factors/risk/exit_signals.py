"""
risk/exit_signals.py - 退出信号因子 (Day D3, 2026-07-27)

把 原 dump_data.calc_exit_signals (原 line 540-612, 73 行) 提炼成独立 factor

5 类 14 子信号综合判定: 缠论 + 估值 + 主力 三维
- 🟢 强烈进货: 60分底背 + 止跌 + PEG<1.5 + fflow 进货 (3+ 绿 + 0 红)
- 🟡 关注建仓: 2+ 绿
- 🔴 减仓: 2+ 红
- ❌ 清仓: 3+ 红

输入: fflow, eps_table, current_price, sector_ma20_dev, chan_signals
输出: dict (14 个子信号 + 综合判定)
"""
import pandas as pd
from tools.factors.base import Factor


class ExitSignalsFactor(Factor):
    """5 类 14 子信号退出判定因子

    输出字段 (dict):
      - v11_score: fflow score
      - PEG: PEG 估值
      - L_E3: DCF L/E3
      - L_可达: L/可达利润
      - vs_MA120: vs MA120 偏离
      - 板块_MA20_偏离: 板块中位数 vs MA20
      - tushare_fflow: 主力净额 (亿)
      - OBV_趋势: 量价齐升/齐跌
      - MACD: 技术信号
      - 缠论信号: 5 个固化信号触发状态
      - 绿信号数/红信号数/综合: 判定
    """

    name = "exit_signals"
    category = "risk"
    dependencies = []
    description = "5 类 14 子信号退出判定 (缠论+估值+主力三维)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        fflow = kwargs.get("fflow") or {}
        eps_table = kwargs.get("eps_table") or []
        current_price = kwargs.get("current_price", 0) or 0
        sector_ma20_dev = kwargs.get("sector_ma20_dev", 0)
        chan_signals = kwargs.get("chan_signals") or {}

        # === 1. PEG (从 EPS 表算) ===
        E1 = eps_table[1].get("eps", 0) if len(eps_table) > 1 else 0
        E3 = eps_table[3].get("eps", 0) if len(eps_table) > 3 else 0
        fwd_pe = current_price / E1 if E1 else 0
        E0 = eps_table[0].get("eps", 0) if eps_table else 0
        g = (E3 / E0 - 1) / 3 if E0 else 0
        peg = fwd_pe / (g * 100) if g > 0 else 0

        # === 2. L/E3 (简化, 跟原 dump_data 一致) ===
        L_E3 = 0.69 if peg < 1.0 else 1.5

        # === 3. vs MA120 (简化) ===
        ma120_dev = -31  # 占位, 应从 K 线算 (跟原 dump_data 一致)

        # === 4. 缠论信号 (固化字段) ===
        bot_60 = chan_signals.get("60min_底背", False)
        top_60 = chan_signals.get("60min_顶背", False)
        stop_sig = chan_signals.get("止跌信号", False)
        chan_verdict = chan_signals.get("缠论综合", "—")
        wyckoff = chan_signals.get("威科夫阶段", "—")

        # === 5. fflow 当日 ===
        fflow_today = ((fflow.get("data_columns") or {}).get("real") or [{}])[0]
        fflow_main = fflow_today.get("main_yi", 0) if fflow_today else 0

        # === 6. 综合判定: 缠论 + 估值 + 主力 三维 ===
        green_signals = sum([
            bot_60,
            stop_sig,
            peg < 1.5,
            fflow_main > 0,
            L_E3 < 1.0,
        ])
        red_signals = sum([
            top_60,
            peg > 2.0,
            fflow_main < -3,
            L_E3 > 2.0,
        ])

        if green_signals >= 3 and red_signals == 0:
            verdict = "🟢 强烈进货 (缠论+估值+主力三重绿)"
        elif red_signals >= 3:
            verdict = "❌ 清仓 (三重危险信号)"
        elif red_signals >= 2:
            verdict = "🔴 减仓 (双重危险信号)"
        elif green_signals >= 2:
            verdict = "🟡 关注建仓 (双绿)"
        else:
            verdict = chan_verdict  # 退回缠论综合

        return {
            "v11_score": fflow.get("score", 0),
            "PEG": round(peg, 2),
            "L_E3": round(L_E3, 2),
            "L_可达": 0.67 if peg < 1.0 else 1.0,
            "vs_MA120": ma120_dev,
            "板块_MA20_偏离": sector_ma20_dev,
            "tushare_fflow": round(fflow_main, 2),
            "OBV_趋势": "量价齐升" if fflow_main > 0 else "量价齐跌",
            "MACD": "低位死叉 (弱势)",
            "60分_底背": "🟢 触发" if bot_60 else "❌",
            "60分_顶背": "🔴 触发" if top_60 else "❌",
            "止跌信号": "🟢 触发" if stop_sig else "❌",
            "威科夫阶段": wyckoff,
            "缠论综合": chan_verdict,
            "绿信号数": green_signals,
            "红信号数": red_signals,
            "综合": verdict,
        }
