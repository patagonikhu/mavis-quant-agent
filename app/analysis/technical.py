"""技术指标计算

纯 numpy/pandas 实现常用 A 股技术指标，无需 TA-Lib 依赖。
所有函数接收 KlineBar 列表，返回带有指标值的 DataFrame。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.data.models import KlineBar

logger = logging.getLogger(__name__)


# ---- K线转 DataFrame 工具 ----

def kline_to_df(bars: list[KlineBar]) -> pd.DataFrame:
    """将 KlineBar 列表转为 DataFrame (按日期升序)"""
    if not bars:
        return pd.DataFrame()
    data = [
        {
            "date": b.trade_date,
            "open": b.open_price,
            "high": b.high_price,
            "low": b.low_price,
            "close": b.close_price,
            "volume": b.volume,
            "amount": b.amount,
            "change_pct": b.change_pct,
            "turnover_rate": b.turnover_rate,
        }
        for b in bars
    ]
    df = pd.DataFrame(data)
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


# ---- 技术指标函数 ----

def calc_ma(closes: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
    """移动平均线 (MA)

    Args:
        closes: 收盘价序列
        periods: 均线周期列表, 默认 [5, 10, 20, 60]
    """
    if periods is None:
        periods = [5, 10, 20, 60]
    result = pd.DataFrame(index=closes.index)
    for p in periods:
        result[f"ma{p}"] = closes.rolling(window=p, min_periods=1).mean().round(2)
    return result


def calc_ema(closes: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
    """指数移动平均线 (EMA)"""
    if periods is None:
        periods = [12, 26]
    result = pd.DataFrame(index=closes.index)
    for p in periods:
        result[f"ema{p}"] = closes.ewm(span=p, adjust=False).mean().round(2)
    return result


def calc_macd(
    closes: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD (异同移动平均线)

    Returns:
        DataFrame with columns: dif, dea, macd_hist
        - DIF = EMA(fast) - EMA(slow)
        - DEA = EMA(DIF, signal)
        - MACD柱 = (DIF - DEA) * 2
    """
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()

    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    macd_hist = (dif - dea) * 2

    return pd.DataFrame({
        "dif": dif.round(4),
        "dea": dea.round(4),
        "macd_hist": macd_hist.round(4),
    })


def calc_kdj(
    df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> pd.DataFrame:
    """KDJ (随机指标)

    Args:
        df: 需包含 high, low, close 列
        n: RSV 周期 (默认 9)
        m1: K 平滑因子 (默认 3)
        m2: D 平滑因子 (默认 3)

    Returns:
        DataFrame with columns: k, d, j
    """
    low_n = df["low"].rolling(window=n, min_periods=1).min()
    high_n = df["high"].rolling(window=n, min_periods=1).max()

    rsv = (df["close"] - low_n) / (high_n - low_n + 1e-10) * 100

    k = rsv.ewm(com=m1 - 1, adjust=False).mean()
    d = k.ewm(com=m2 - 1, adjust=False).mean()
    j = 3 * k - 2 * d

    return pd.DataFrame({
        "k": k.round(2),
        "d": d.round(2),
        "j": j.round(2),
    })


def calc_rsi(closes: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
    """RSI (相对强弱指数)

    Args:
        closes: 收盘价序列
        periods: RSI 周期列表, 默认 [6, 12, 24]
    """
    if periods is None:
        periods = [6, 12, 24]

    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    result = pd.DataFrame(index=closes.index)
    for p in periods:
        avg_gain = gain.rolling(window=p, min_periods=1).mean()
        avg_loss = loss.rolling(window=p, min_periods=1).mean()
        rs = avg_gain / (avg_loss + 1e-10)
        result[f"rsi{p}"] = (100 - 100 / (1 + rs)).round(2)
    return result


def calc_boll(
    closes: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """BOLL (布林带)

    Returns:
        DataFrame with columns: boll_mid, boll_upper, boll_lower
    """
    mid = closes.rolling(window=period, min_periods=1).mean()
    std = closes.rolling(window=period, min_periods=1).std()

    return pd.DataFrame({
        "boll_mid": mid.round(2),
        "boll_upper": (mid + std_dev * std).round(2),
        "boll_lower": (mid - std_dev * std).round(2),
    })


def calc_volume_ma(volumes: pd.Series, periods: list[int] | None = None) -> pd.DataFrame:
    """成交量均线"""
    if periods is None:
        periods = [5, 10, 20]
    result = pd.DataFrame(index=volumes.index)
    for p in periods:
        result[f"vol_ma{p}"] = volumes.rolling(window=p, min_periods=1).mean().round(0)
    return result


def calc_volume_ratio(volumes: pd.Series, period: int = 5) -> pd.Series:
    """量比 = 当日成交量 / 过去N日平均成交量"""
    avg_vol = volumes.rolling(window=period, min_periods=1).mean().shift(1)
    return (volumes / (avg_vol + 1e-10)).round(2)


def calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR (平均真实波幅)

    TR = max(high-low, |high-pre_close|, |low-pre_close|)
    ATR = SMA(TR, period)
    """
    pre_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - pre_close).abs()
    tr3 = (df["low"] - pre_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean().round(4)


# ---- 综合分析接口 ----

@dataclass
class TechnicalIndicators:
    """技术指标汇总结果"""

    # 原始数据
    df: pd.DataFrame = field(default_factory=pd.DataFrame)

    # 均线
    ma: pd.DataFrame = field(default_factory=pd.DataFrame)

    # MACD
    macd: pd.DataFrame = field(default_factory=pd.DataFrame)

    # KDJ
    kdj: pd.DataFrame = field(default_factory=pd.DataFrame)

    # RSI
    rsi: pd.DataFrame = field(default_factory=pd.DataFrame)

    # BOLL
    boll: pd.DataFrame = field(default_factory=pd.DataFrame)

    # 成交量
    vol_ma: pd.DataFrame = field(default_factory=pd.DataFrame)
    volume_ratio: pd.Series = field(default_factory=pd.Series)

    # ATR
    atr: pd.Series = field(default_factory=pd.Series)

    @property
    def latest(self) -> dict:
        """获取最新一根K线的所有指标值"""
        if self.df.empty:
            return {}

        result = {}
        idx = len(self.df) - 1

        # 基础数据
        for col in self.df.columns:
            result[col] = self.df.iloc[idx].get(col)

        # 各指标
        for name, indicator_df in [
            ("ma", self.ma), ("macd", self.macd), ("kdj", self.kdj),
            ("rsi", self.rsi), ("boll", self.boll), ("vol_ma", self.vol_ma),
        ]:
            if not indicator_df.empty and idx < len(indicator_df):
                for col in indicator_df.columns:
                    result[col] = indicator_df.iloc[idx].get(col)

        if not self.volume_ratio.empty and idx < len(self.volume_ratio):
            result["volume_ratio"] = self.volume_ratio.iloc[idx]
        if not self.atr.empty and idx < len(self.atr):
            result["atr"] = self.atr.iloc[idx]

        return result


def compute_all_indicators(bars: list[KlineBar]) -> TechnicalIndicators:
    """一次性计算所有技术指标

    Args:
        bars: K线数据列表 (至少 60 条为佳)

    Returns:
        TechnicalIndicators 汇总对象
    """
    if not bars:
        return TechnicalIndicators()

    df = kline_to_df(bars)
    closes = df["close"]
    volumes = df["volume"]

    return TechnicalIndicators(
        df=df,
        ma=calc_ma(closes),
        macd=calc_macd(closes),
        kdj=calc_kdj(df),
        rsi=calc_rsi(closes),
        boll=calc_boll(closes),
        vol_ma=calc_volume_ma(volumes),
        volume_ratio=calc_volume_ratio(volumes),
        atr=calc_atr(df),
    )


# ---- 指标解读工具 ----

def interpret_indicators(latest: dict) -> list[str]:
    """根据最新指标值生成自然语言解读

    Returns:
        解读文本列表
    """
    signals = []

    # MACD 判断
    dif = latest.get("dif")
    dea = latest.get("dea")
    if dif is not None and dea is not None:
        if dif > dea and dif > 0:
            signals.append("MACD 金叉且在零轴上方，多头趋势")
        elif dif > dea:
            signals.append("MACD 金叉但在零轴下方，短线反弹信号")
        elif dif < dea and dif < 0:
            signals.append("MACD 死叉且在零轴下方，空头趋势")
        elif dif < dea:
            signals.append("MACD 死叉但在零轴上方，短线回调信号")

    # KDJ 判断
    k = latest.get("k")
    d = latest.get("d")
    j = latest.get("j")
    if k is not None and j is not None:
        if j > 100:
            signals.append(f"KDJ J值={j:.1f} 超买区间，注意回调风险")
        elif j < 0:
            signals.append(f"KDJ J值={j:.1f} 超卖区间，可能存在反弹机会")
        elif k > d:
            signals.append("KDJ K线在D线上方，短期偏多")
        else:
            signals.append("KDJ K线在D线下方，短期偏空")

    # RSI 判断
    rsi6 = latest.get("rsi6")
    if rsi6 is not None:
        if rsi6 > 80:
            signals.append(f"RSI6={rsi6:.1f} 超买区间")
        elif rsi6 < 20:
            signals.append(f"RSI6={rsi6:.1f} 超卖区间")
        elif rsi6 > 50:
            signals.append(f"RSI6={rsi6:.1f} 偏强运行")
        else:
            signals.append(f"RSI6={rsi6:.1f} 偏弱运行")

    # BOLL 判断
    close = latest.get("close")
    upper = latest.get("boll_upper")
    lower = latest.get("boll_lower")
    mid = latest.get("boll_mid")
    if close is not None and upper is not None and lower is not None:
        if close >= upper:
            signals.append("价格触及布林带上轨，短期可能承压")
        elif close <= lower:
            signals.append("价格触及布林带下轨，可能存在支撑")
        elif mid is not None and close > mid:
            signals.append("价格在布林带中轨上方，偏强运行")
        else:
            signals.append("价格在布林带中轨下方，偏弱运行")

    # 均线趋势
    ma5 = latest.get("ma5")
    ma10 = latest.get("ma10")
    ma20 = latest.get("ma20")
    if ma5 is not None and ma10 is not None and ma20 is not None:
        if ma5 > ma10 > ma20:
            signals.append("均线多头排列 (MA5>MA10>MA20)")
        elif ma5 < ma10 < ma20:
            signals.append("均线空头排列 (MA5<MA10<MA20)")

    return signals
