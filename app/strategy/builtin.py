"""内置交易策略

每个策略接收 TechnicalIndicators，返回 Signal 或 None。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.analysis.technical import TechnicalIndicators
from app.strategy.models import Direction, Signal

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """策略基类"""

    name: str = ""

    @abstractmethod
    def evaluate(self, symbol: str, name: str, indicators: TechnicalIndicators) -> Signal | None:
        """评估信号，无信号返回 None"""


# ---- MACD 策略 ----

class MACDCrossStrategy(BaseStrategy):
    """MACD 金叉/死叉策略

    买入: DIF 上穿 DEA (金叉)
    卖出: DIF 下穿 DEA (死叉)
    """

    name = "MACD交叉"

    def evaluate(self, symbol: str, name: str, indicators: TechnicalIndicators) -> Signal | None:
        if indicators.macd.empty or len(indicators.macd) < 2:
            return None

        curr = indicators.macd.iloc[-1]
        prev = indicators.macd.iloc[-2]

        dif_now, dea_now = curr["dif"], curr["dea"]
        dif_prev, dea_prev = prev["dif"], prev["dea"]

        # 金叉: 前一日 DIF <= DEA, 当日 DIF > DEA
        if dif_prev <= dea_prev and dif_now > dea_now:
            confidence = 0.6
            reason_extra = ""
            if dif_now > 0:
                confidence = 0.75
                reason_extra = "，零轴上方金叉更强"
            return Signal(
                symbol=symbol, name=name, direction=Direction.BUY,
                confidence=confidence, strategy=self.name,
                reason=f"MACD 金叉 (DIF={dif_now:.3f}, DEA={dea_now:.3f}){reason_extra}",
                indicators={"dif": dif_now, "dea": dea_now, "macd_hist": curr["macd_hist"]},
            )

        # 死叉: 前一日 DIF >= DEA, 当日 DIF < DEA
        if dif_prev >= dea_prev and dif_now < dea_now:
            confidence = 0.6
            reason_extra = ""
            if dif_now < 0:
                confidence = 0.75
                reason_extra = "，零轴下方死叉更弱"
            return Signal(
                symbol=symbol, name=name, direction=Direction.SELL,
                confidence=confidence, strategy=self.name,
                reason=f"MACD 死叉 (DIF={dif_now:.3f}, DEA={dea_now:.3f}){reason_extra}",
                indicators={"dif": dif_now, "dea": dea_now, "macd_hist": curr["macd_hist"]},
            )

        return None


# ---- KDJ 策略 ----

class KDJStrategy(BaseStrategy):
    """KDJ 超买超卖策略

    买入: J < 20 且 K 上穿 D
    卖出: J > 80 且 K 下穿 D
    """

    name = "KDJ超买超卖"

    def evaluate(self, symbol: str, name: str, indicators: TechnicalIndicators) -> Signal | None:
        if indicators.kdj.empty or len(indicators.kdj) < 2:
            return None

        curr = indicators.kdj.iloc[-1]
        prev = indicators.kdj.iloc[-2]

        k_now, d_now, j_now = curr["k"], curr["d"], curr["j"]
        k_prev, d_prev = prev["k"], prev["d"]

        # 超卖区金叉
        if j_now < 20 and k_prev <= d_prev and k_now > d_now:
            return Signal(
                symbol=symbol, name=name, direction=Direction.BUY,
                confidence=0.65, strategy=self.name,
                reason=f"KDJ 超卖区金叉 (K={k_now:.1f}, D={d_now:.1f}, J={j_now:.1f})",
                indicators={"k": k_now, "d": d_now, "j": j_now},
            )

        # 超买区死叉
        if j_now > 80 and k_prev >= d_prev and k_now < d_now:
            return Signal(
                symbol=symbol, name=name, direction=Direction.SELL,
                confidence=0.65, strategy=self.name,
                reason=f"KDJ 超买区死叉 (K={k_now:.1f}, D={d_now:.1f}, J={j_now:.1f})",
                indicators={"k": k_now, "d": d_now, "j": j_now},
            )

        return None


# ---- 均线趋势策略 ----

class MATrendStrategy(BaseStrategy):
    """均线多头/空头排列策略

    买入: MA5 > MA10 > MA20 (多头排列)
    卖出: MA5 < MA10 < MA20 (空头排列)
    """

    name = "均线趋势"

    def evaluate(self, symbol: str, name: str, indicators: TechnicalIndicators) -> Signal | None:
        if indicators.ma.empty:
            return None

        latest = indicators.ma.iloc[-1]
        ma5 = latest.get("ma5")
        ma10 = latest.get("ma10")
        ma20 = latest.get("ma20")

        if ma5 is None or ma10 is None or ma20 is None:
            return None

        import math
        if any(math.isnan(v) for v in [ma5, ma10, ma20]):
            return None

        # 多头排列
        if ma5 > ma10 > ma20:
            return Signal(
                symbol=symbol, name=name, direction=Direction.BUY,
                confidence=0.6, strategy=self.name,
                reason=f"均线多头排列 (MA5={ma5:.2f} > MA10={ma10:.2f} > MA20={ma20:.2f})",
                indicators={"ma5": ma5, "ma10": ma10, "ma20": ma20},
            )

        # 空头排列
        if ma5 < ma10 < ma20:
            return Signal(
                symbol=symbol, name=name, direction=Direction.SELL,
                confidence=0.6, strategy=self.name,
                reason=f"均线空头排列 (MA5={ma5:.2f} < MA10={ma10:.2f} < MA20={ma20:.2f})",
                indicators={"ma5": ma5, "ma10": ma10, "ma20": ma20},
            )

        return None


# ---- 布林带策略 ----

class BollingerStrategy(BaseStrategy):
    """布林带突破策略

    买入: 价格触及下轨后反弹 (收盘 > 下轨 且 前一日收盘 <= 下轨)
    卖出: 价格触及上轨后回落 (收盘 < 上轨 且 前一日收盘 >= 上轨)
    """

    name = "布林带"

    def evaluate(self, symbol: str, name: str, indicators: TechnicalIndicators) -> Signal | None:
        if indicators.boll.empty or len(indicators.boll) < 2 or indicators.df.empty:
            return None

        curr_boll = indicators.boll.iloc[-1]
        prev_boll = indicators.boll.iloc[-2]
        curr_close = indicators.df.iloc[-1]["close"]
        prev_close = indicators.df.iloc[-2]["close"]

        upper = curr_boll["boll_upper"]
        lower = curr_boll["boll_lower"]
        prev_lower = prev_boll["boll_lower"]
        prev_upper = prev_boll["boll_upper"]

        import math
        if any(math.isnan(v) for v in [upper, lower, prev_lower, prev_upper]):
            return None

        # 下轨反弹
        if prev_close <= prev_lower and curr_close > lower:
            return Signal(
                symbol=symbol, name=name, direction=Direction.BUY,
                confidence=0.55, strategy=self.name,
                reason=f"价格触及布林带下轨后反弹 (收盘{curr_close:.2f}, 下轨{lower:.2f})",
                indicators={"close": curr_close, "boll_lower": lower, "boll_upper": upper},
            )

        # 上轨回落
        if prev_close >= prev_upper and curr_close < upper:
            return Signal(
                symbol=symbol, name=name, direction=Direction.SELL,
                confidence=0.55, strategy=self.name,
                reason=f"价格触及布林带上轨后回落 (收盘{curr_close:.2f}, 上轨{upper:.2f})",
                indicators={"close": curr_close, "boll_lower": lower, "boll_upper": upper},
            )

        return None


# ---- 放量突破策略 ----

class VolumeSurgeStrategy(BaseStrategy):
    """放量突破策略

    买入: 量比 > 2 且 涨幅 > 0 (放量上涨)
    卖出: 量比 > 2 且 涨幅 < 0 (放量下跌)
    """

    name = "放量突破"

    def evaluate(self, symbol: str, name: str, indicators: TechnicalIndicators) -> Signal | None:
        if indicators.volume_ratio.empty or indicators.df.empty:
            return None

        vol_ratio = indicators.volume_ratio.iloc[-1]
        change_pct = indicators.df.iloc[-1].get("change_pct", 0)

        if vol_ratio is None or (isinstance(vol_ratio, float) and __import__("math").isnan(vol_ratio)):
            return None

        if vol_ratio > 2.0 and change_pct > 1.0:
            return Signal(
                symbol=symbol, name=name, direction=Direction.BUY,
                confidence=min(0.7, 0.5 + (vol_ratio - 2) * 0.05),
                strategy=self.name,
                reason=f"放量上涨 (量比={vol_ratio:.1f}, 涨幅={change_pct:.1f}%)",
                indicators={"volume_ratio": vol_ratio, "change_pct": change_pct},
            )

        if vol_ratio > 2.0 and change_pct < -1.0:
            return Signal(
                symbol=symbol, name=name, direction=Direction.SELL,
                confidence=min(0.7, 0.5 + (vol_ratio - 2) * 0.05),
                strategy=self.name,
                reason=f"放量下跌 (量比={vol_ratio:.1f}, 跌幅={change_pct:.1f}%)",
                indicators={"volume_ratio": vol_ratio, "change_pct": change_pct},
            )

        return None


# ---- 策略注册表 ----

BUILTIN_STRATEGIES: list[BaseStrategy] = [
    MACDCrossStrategy(),
    KDJStrategy(),
    MATrendStrategy(),
    BollingerStrategy(),
    VolumeSurgeStrategy(),
]
