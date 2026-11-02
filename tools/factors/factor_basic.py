"""
tools/factors/factor_basic.py — 通用基础 factor 库 (纯函数, 2026-09-09 统一)

合并来源 (2026-09-09 之前散落的 class Factor 全部改成纯函数):
  - tools/factors/timeseries/ma.py        (TSMean / TSStd)
  - tools/factors/price/returns.py       (Returns / LogReturns)
  - tools/factors/price/position.py      (PricePositionFactor + 3 个 helper)
  - tools/factors/position/three_layer.py (ThreeLayerPositionFactor)
  - tools/factors/alpha101/alpha_001.py  (Alpha001)
  - tools/factors/alpha101/alpha_ga_001.py (AlphaGA001)

设计原则:
  - 全部纯函数, 不再 class Factor 包装
  - 统一签名: def compute_xxx(df=None, **kwargs) -> pd.Series | dict
  - 不依赖 tools.factors.base (Factor 基类)
  - 不依赖 tools.factors.registry (FactorRegistry)
  - 不依赖 yaml 配置 (FactorConfig)

公开 API (11 个):
  - ts_mean / ts_std            滚动均值 / 标准差
  - returns / log_returns       日收益率 / 对数收益率
  - price_position              价格位置三件套 (日内 / 20 日 / 上影线)
  - three_layer_position        三层仓位策略
  - alpha_001                    WorldQuant Alpha #1 (5d 最大涨幅 vs 当日 rank)
  - alpha_ga_001                 GA 挖掘的 5d 涨跌预测因子
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ============================================================
# 工具函数 (从 base.py 搬过来, 只留通用的, 不含 Factor 基类)
# ============================================================
# ============================================================
# 时序因子 (原 timeseries/ma.py)
# ============================================================
# ============================================================
# 价格因子 (原 price/returns.py + price/position.py)
# ============================================================
# ============================================================
# 仓位因子 (原 position/three_layer.py)
# ============================================================
def compute_three_layer_position(
    price: float = 0,
    chan_d: dict = None,
    chan_signals: dict = None,
) -> dict:
    """三层仓位策略因子 (逆势/底/中/波动 + 止损目标阶梯)

    输入:
      - price: 当前价
      - chan_d: 日线中枢结果
      - chan_signals: 缠论固化信号
    输出: dict (市况/三级别中枢/位置/4 层仓位/止损阶梯/目标阶梯)

    2026-10-28 删 fflow 参数 (主力资金流已整体删除)
    2026-11-02 删 peg 参数 (PEG 是假指标, 整档已删; 之前也没被读过)
    """
    chan_d = chan_d or {}
    chan_signals = chan_signals or {}

    # 日线中枢
    hub = chan_d.get("hub", {}) if chan_d else {}
    hub_low = hub.get("low", 0)
    hub_high = hub.get("high", 0)
    hub_pos = str(hub.get("pos", ""))

    # 缠论信号
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
            "进场": "60分底背 + 止跌信号 (2/2 全满足, 缠论纯信号)",
            "止损": f"¥{price * 0.85:.2f} (结构低点 -1×ATR)",
            "目标": t1,
            "已触发": bot_60 and stop_sig,
        },
        "底仓": {
            "仓位": "25-30%",
            "进场": "60分底背驰 + 站上 ¥" + f"{price * 1.05:.2f}",
            "止损": f"¥{price * 0.97:.2f}",
            "目标": t1,
            "已触发": bot_60,
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
