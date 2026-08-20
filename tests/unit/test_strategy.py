"""策略/信号层单元测试"""

import datetime

import pytest

from app.data.models import KlineBar
from app.analysis.technical import compute_all_indicators
from app.strategy.models import Direction, Signal, SignalReport
from app.strategy.builtin import (
    MACDCrossStrategy,
    KDJStrategy,
    MATrendStrategy,
    BollingerStrategy,
    VolumeSurgeStrategy,
    BUILTIN_STRATEGIES,
)
from app.strategy.engine import SignalEngine, generate_signal


# ---- 辅助函数 ----

def make_bars(count: int = 60, base_price: float = 100.0, pattern: str = "up") -> list[KlineBar]:
    """生成模拟K线数据"""
    bars = []
    price = base_price
    for i in range(count):
        if pattern == "up":
            change = 0.3 + (i % 3) * 0.1
        elif pattern == "down":
            change = -(0.3 + (i % 3) * 0.1)
        elif pattern == "golden_cross":
            # 先跌后涨，模拟金叉
            if i < 30:
                change = -0.2
            else:
                change = 0.5
        else:
            change = (1 if i % 2 == 0 else -1) * 0.2

        open_p = price
        close_p = price + change
        high_p = max(open_p, close_p) + abs(change) * 0.3
        low_p = min(open_p, close_p) - abs(change) * 0.3

        bars.append(KlineBar(
            trade_date=datetime.date(2026, 1, 1) + datetime.timedelta(days=i),
            open_price=round(open_p, 2),
            high_price=round(high_p, 2),
            low_price=round(low_p, 2),
            close_price=round(close_p, 2),
            volume=100000 + i * 1000,
            amount=10000000 + i * 100000,
            change_pct=round(change / open_p * 100, 2),
            turnover_rate=round(0.5 + i * 0.01, 2),
        ))
        price = close_p
    return bars


# ---- 信号模型测试 ----

class TestSignal:
    def test_create(self):
        s = Signal(
            symbol="600519", name="贵州茅台",
            direction=Direction.BUY, confidence=0.75,
            strategy="MACD交叉", reason="MACD金叉",
        )
        assert s.direction == Direction.BUY
        assert s.confidence == 0.75

    def test_direction_enum(self):
        assert Direction.BUY.value == "buy"
        assert Direction.SELL.value == "sell"
        assert Direction.HOLD.value == "hold"


class TestSignalReport:
    def test_create(self):
        report = SignalReport(symbol="600519", name="贵州茅台")
        assert report.direction == Direction.HOLD
        assert report.disclaimer != ""

    def test_buy_sell_signals(self):
        signals = [
            Signal(symbol="600519", direction=Direction.BUY, confidence=0.7,
                   strategy="MACD", reason="金叉"),
            Signal(symbol="600519", direction=Direction.SELL, confidence=0.6,
                   strategy="KDJ", reason="超买"),
            Signal(symbol="600519", direction=Direction.BUY, confidence=0.5,
                   strategy="均线", reason="多头"),
        ]
        report = SignalReport(symbol="600519", signals=signals)
        assert len(report.buy_signals()) == 2
        assert len(report.sell_signals()) == 1

    def test_summary_text(self):
        signals = [
            Signal(symbol="600519", direction=Direction.BUY, confidence=0.7,
                   strategy="MACD", reason="金叉"),
        ]
        report = SignalReport(symbol="600519", name="贵州茅台", signals=signals)
        text = report.summary_text()
        assert "600519" in text
        assert "贵州茅台" in text
        assert "免责声明" in text or "不构成投资建议" in text


# ---- 内置策略测试 ----

class TestMACDCrossStrategy:
    def test_returns_signal_or_none(self):
        strategy = MACDCrossStrategy()
        bars = make_bars(60, pattern="up")
        indicators = compute_all_indicators(bars)
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        assert result is None or isinstance(result, Signal)

    def test_insufficient_data(self):
        strategy = MACDCrossStrategy()
        indicators = compute_all_indicators([])
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        assert result is None

    def test_name(self):
        assert MACDCrossStrategy().name == "MACD交叉"


class TestKDJStrategy:
    def test_returns_signal_or_none(self):
        strategy = KDJStrategy()
        bars = make_bars(60, pattern="up")
        indicators = compute_all_indicators(bars)
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        assert result is None or isinstance(result, Signal)

    def test_name(self):
        assert KDJStrategy().name == "KDJ超买超卖"


class TestMATrendStrategy:
    def test_uptrend_gives_buy(self):
        strategy = MATrendStrategy()
        bars = make_bars(60, pattern="up")
        indicators = compute_all_indicators(bars)
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        # 上涨趋势应产生买入信号
        if result:
            assert result.direction == Direction.BUY

    def test_downtrend_gives_sell(self):
        strategy = MATrendStrategy()
        bars = make_bars(60, base_price=200, pattern="down")
        indicators = compute_all_indicators(bars)
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        if result:
            assert result.direction == Direction.SELL

    def test_name(self):
        assert MATrendStrategy().name == "均线趋势"


class TestBollingerStrategy:
    def test_returns_signal_or_none(self):
        strategy = BollingerStrategy()
        bars = make_bars(60)
        indicators = compute_all_indicators(bars)
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        assert result is None or isinstance(result, Signal)


class TestVolumeSurgeStrategy:
    def test_returns_signal_or_none(self):
        strategy = VolumeSurgeStrategy()
        bars = make_bars(60)
        indicators = compute_all_indicators(bars)
        result = strategy.evaluate("600519", "贵州茅台", indicators)
        assert result is None or isinstance(result, Signal)


class TestBuiltinStrategies:
    def test_all_registered(self):
        assert len(BUILTIN_STRATEGIES) == 5
        names = [s.name for s in BUILTIN_STRATEGIES]
        assert "MACD交叉" in names
        assert "KDJ超买超卖" in names
        assert "均线趋势" in names
        assert "布林带" in names
        assert "放量突破" in names


# ---- 信号引擎测试 ----

class TestSignalEngine:
    def test_evaluate_empty_bars(self):
        engine = SignalEngine()
        report = engine.evaluate("600519", "贵州茅台", [])
        assert report.symbol == "600519"
        assert report.direction == Direction.HOLD
        assert len(report.signals) == 0

    def test_evaluate_with_bars(self):
        engine = SignalEngine()
        bars = make_bars(60, pattern="up")
        report = engine.evaluate("600519", "贵州茅台", bars)
        assert report.symbol == "600519"
        assert report.disclaimer != ""
        assert report.risk_level in ("low", "medium", "high")

    def test_aggregate_no_signals(self):
        engine = SignalEngine()
        direction, confidence = engine._aggregate([])
        assert direction == Direction.HOLD
        assert confidence == 0.0

    def test_aggregate_buy_wins(self):
        engine = SignalEngine()
        signals = [
            Signal(symbol="600519", direction=Direction.BUY, confidence=0.8,
                   strategy="s1", reason="r1"),
            Signal(symbol="600519", direction=Direction.BUY, confidence=0.6,
                   strategy="s2", reason="r2"),
            Signal(symbol="600519", direction=Direction.SELL, confidence=0.4,
                   strategy="s3", reason="r3"),
        ]
        direction, confidence = engine._aggregate(signals)
        assert direction == Direction.BUY
        assert confidence > 0

    def test_aggregate_sell_wins(self):
        engine = SignalEngine()
        signals = [
            Signal(symbol="600519", direction=Direction.SELL, confidence=0.9,
                   strategy="s1", reason="r1"),
            Signal(symbol="600519", direction=Direction.SELL, confidence=0.7,
                   strategy="s2", reason="r2"),
            Signal(symbol="600519", direction=Direction.BUY, confidence=0.3,
                   strategy="s3", reason="r3"),
        ]
        direction, confidence = engine._aggregate(signals)
        assert direction == Direction.SELL

    def test_custom_strategies(self):
        engine = SignalEngine(strategies=[MATrendStrategy()])
        bars = make_bars(60, pattern="up")
        report = engine.evaluate("600519", "贵州茅台", bars)
        # 所有信号都应来自均线趋势策略
        for s in report.signals:
            assert s.strategy == "均线趋势"


class TestGenerateSignal:
    def test_convenience_function(self):
        bars = make_bars(60)
        report = generate_signal("600519", "贵州茅台", bars)
        assert isinstance(report, SignalReport)
        assert report.symbol == "600519"
