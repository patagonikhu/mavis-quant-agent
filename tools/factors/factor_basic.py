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
def safe_div(a: pd.Series, b: pd.Series, fill: float = 0.0) -> pd.Series:
    """安全除法, 防 0"""
    return a / b.replace(0, np.nan).fillna(fill)


def rank_pct(s: pd.Series) -> pd.Series:
    """百分位排名 (0-1)"""
    return s.rank(pct=True)


def ts_rank(s: pd.Series, window: int) -> pd.Series:
    """滚动时序排名 (0-1)"""
    return s.rolling(window, min_periods=1).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )


def ts_mean(s: pd.Series, window: int) -> pd.Series:
    """滚动均值"""
    return s.rolling(window, min_periods=1).mean()


def ts_std(s: pd.Series, window: int) -> pd.Series:
    """滚动标准差"""
    return s.rolling(window, min_periods=1).std()


def zscore(s: pd.Series, window: int = None) -> pd.Series:
    """横截面/时序 z-score 标准化"""
    if window is None:
        return (s - s.mean()) / s.std()
    mu = s.rolling(window).mean()
    sd = s.rolling(window).std()
    return safe_div(s - mu, sd)


# ============================================================
# 时序因子 (原 timeseries/ma.py)
# ============================================================
def compute_ts_mean(df: pd.DataFrame, window: int = 5) -> pd.Series:
    """时序移动平均

    输入: df (含 close)
    输出: pd.Series, 命名 ts_mean_{window}
    """
    return df['close'].rolling(window, min_periods=1).mean().rename(f"ts_mean_{window}")


def compute_ts_std(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """时序标准差

    输出: pd.Series, 命名 ts_std_{window}
    """
    return df['close'].rolling(window, min_periods=1).std().rename(f"ts_std_{window}")


# ============================================================
# 价格因子 (原 price/returns.py + price/position.py)
# ============================================================
def compute_returns(df: pd.DataFrame) -> pd.Series:
    """日收益率 (close-to-close)"""
    return df['close'].pct_change().rename("returns")


def compute_log_returns(df: pd.DataFrame) -> pd.Series:
    """对数日收益率 = log(close_t / close_{t-1})"""
    return np.log(df['close'] / df['close'].shift(1)).rename("log_returns")


def _upper_shadow_pct(df: pd.DataFrame) -> pd.Series:
    """上影线百分比: (high - max(close, open)) / close × 100%"""
    body_top = pd.concat([df["close"], df["open"]], axis=1).max(axis=1)
    return ((df["high"] - body_top) / df["close"] * 100.0).clip(lower=0)


def _day_close_pos(df: pd.DataFrame) -> pd.Series:
    """日内位置: (close - low) / (high - low), clamp [0, 1]"""
    span = (df["high"] - df["low"]).where((df["high"] - df["low"]) != 0)
    return ((df["close"] - df["low"]) / span).clip(0, 1).fillna(0.5)


def _n_day_close_pos(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """N 日区间位置: (close - N日low) / (N日high - N日low), clamp [0, 1]"""
    hi = df["high"].rolling(window).max()
    lo = df["low"].rolling(window).min()
    return ((df["close"] - lo) / (hi - lo)).clip(0, 1).fillna(0.5)


def compute_price_position(df, asof_date: str = None, window: int = 20) -> dict:
    """价格位置因子 — 日内位置 + N 日位置 + 上影线

    输入: df (K 线, 含 close/high/low/open) 或 list-of-dict
    输出: dict 4 个 Series:
      - close_pos_day: 日内位置
      - close_pos_20:  20 日区间位置
      - upper_shadow_pct: 当日上影线百分比
      - upper_shadow_5d_avg: 近 5 日上影线均值
    """
    from tools.factors.utils import asof_slice, df_from_bars

    df = asof_slice(df_from_bars(df), asof_date)
    if df is None or len(df) < 2:
        return {
            "close_pos_day": pd.Series(dtype=float),
            "close_pos_20": pd.Series(dtype=float),
            "upper_shadow_pct": pd.Series(dtype=float),
            "upper_shadow_5d_avg": pd.Series(dtype=float),
        }

    us_pct = _upper_shadow_pct(df)
    return {
        "close_pos_day": _day_close_pos(df),
        "close_pos_20": _n_day_close_pos(df, window),
        "upper_shadow_pct": us_pct,
        "upper_shadow_5d_avg": us_pct.rolling(5, min_periods=1).mean(),
    }


# ============================================================
# 仓位因子 (原 position/three_layer.py)
# ============================================================
def compute_three_layer_position(
    price: float = 0,
    chan_d: dict = None,
    fflow: dict = None,
    peg: float = 0,
    chan_signals: dict = None,
) -> dict:
    """三层仓位策略因子 (逆势/底/中/波动 + 止损目标阶梯)

    输入:
      - price: 当前价
      - chan_d: 日线中枢结果
      - fflow: 主力资金
      - peg: PEG 估值
      - chan_signals: 缠论固化信号
    输出: dict (市况/三级别中枢/位置/4 层仓位/止损阶梯/目标阶梯)
    """
    chan_d = chan_d or {}
    fflow = fflow or {}
    chan_signals = chan_signals or {}

    # 当前 fflow 数据
    fflow_real = (fflow.get("data_columns") or {}).get("real") or []
    fflow_today = fflow_real[0] if fflow_real else {}
    fflow_main = fflow_today.get("main_yi", 0) if fflow_today else 0

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
            "已触发": bot_60,  # 2026-09-09 删 fflow_main > 0 触发
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


# ============================================================
# Alpha 因子 (原 alpha101/alpha_001.py + alpha_ga_001.py)
# ============================================================
def compute_alpha_001(df: pd.DataFrame) -> pd.Series:
    """WorldQuant Alpha #1: 5d 最大涨幅 vs 当日 rank 差 (经典反转)

    公式: rank(ts_argmax(pow(returns, 2), 5)) - rank(returns)
    逻辑: 5 天内涨幅最大那天的排名 - 当日涨幅排名
          差越大 = 前期有大利好但当日涨势减弱 → 下期可能跌
    """
    closes = df['close']
    returns = closes.pct_change()
    # 5 天内最大收益率 (替代 ts_argmax(pow(returns, 2), 5) → 等价 abs returns 的 argmax)
    max_returns_5d = returns.abs().rolling(5, min_periods=1).max()
    rank_max = rank_pct(max_returns_5d)
    rank_now = rank_pct(returns)
    return (rank_max - rank_now).rename("alpha_001")


def compute_alpha_ga_001(df: pd.DataFrame) -> pd.Series:
    """GA 挖掘的 5d 涨跌预测因子 (gplearn, 2026-07-27)

    6 个原子特征:
      - close_pct_rank: 60日 价格分位 (0-1)
      - vol_5_20: 5日均量 / 20日均量
      - ma20_dist: (close - MA20) / MA20
      - rsi: 14日 RSI
      - vol_price_corr: 5日 量价相关系数
      - obv: 累积 OBV (标准化后)

    公式:
      factor = (close_pct_rank - vol_5_20 + ma20_dist + rsi)
             * (vol_5_20 + 0.711 - vol_price_corr - obv_norm)

    性能: |IC| 0.077-0.671 (跨票), 300274 上 0.453 (强信号)
    方向: 多数票反向 (高 factor 值→5d 跌), 002371 正向 (5d 涨)
    """
    closes = df['close']
    volumes = df['volume']

    # X0 = obv (标准化)
    obv_raw = (np.sign(closes.diff()) * volumes).cumsum()
    obv_norm = (obv_raw - obv_raw.rolling(60, min_periods=20).mean()) / \
               (obv_raw.rolling(60, min_periods=20).std() + 1e-9)

    # X3 = vol_5_20
    vol_5_20 = volumes.rolling(5).mean() / (volumes.rolling(20).mean() + 1e-9)

    # X4 = rsi (14)
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rsi = 100 - 100 / (1 + gain / (loss + 1e-9))

    # X7 = close_pct_rank (60日滚动)
    close_pct_rank = closes.rolling(60, min_periods=20).apply(
        lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False
    )

    # X8 = vol_price_corr (5日)
    vol_price_corr = closes.rolling(5).corr(volumes)

    # X9 = ma20_dist
    ma20 = closes.rolling(20).mean()
    ma20_dist = (closes - ma20) / (ma20 + 1e-9)

    # 公式: (X7 - X3 + X9 + X4) * (X3 + 0.711 - X8 - X0)
    part1 = close_pct_rank - vol_5_20 + ma20_dist + rsi / 100
    part2 = vol_5_20 + 0.711 - vol_price_corr - obv_norm
    factor = part1 * part2
    return factor.rename("alpha_ga_001")
