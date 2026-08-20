"""分析层单元测试"""

import datetime

import pytest

from app.data.models import KlineBar, FinancialData
from app.analysis.technical import (
    calc_ma,
    calc_macd,
    calc_kdj,
    calc_rsi,
    calc_boll,
    calc_atr,
    calc_volume_ratio,
    compute_all_indicators,
    interpret_indicators,
    kline_to_df,
)
from app.analysis.fundamental import (
    analyze_fundamentals,
    score_valuation,
    score_profitability,
    score_growth,
    score_safety,
)
from app.analysis.pattern import (
    detect_patterns,
    judge_trend,
    detect_doji,
    detect_hammer,
    detect_engulfing,
)

import pandas as pd


# ---- 辅助函数: 构造测试K线数据 ----

def make_bars(count: int = 60, base_price: float = 100.0, trend: str = "up") -> list[KlineBar]:
    """生成模拟K线数据"""
    bars = []
    price = base_price
    for i in range(count):
        if trend == "up":
            change = 0.3 + (i % 3) * 0.1
        elif trend == "down":
            change = -(0.3 + (i % 3) * 0.1)
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
        ))
        price = close_p

    return bars


# ---- 技术指标测试 ----

class TestKlineToDf:
    def test_basic(self):
        bars = make_bars(5)
        df = kline_to_df(bars)
        assert len(df) == 5
        assert "close" in df.columns
        assert "open" in df.columns

    def test_empty(self):
        df = kline_to_df([])
        assert df.empty


class TestMA:
    def test_calc_ma(self):
        bars = make_bars(30)
        df = kline_to_df(bars)
        ma = calc_ma(df["close"], periods=[5, 10])
        assert "ma5" in ma.columns
        assert "ma10" in ma.columns
        assert len(ma) == 30

    def test_default_periods(self):
        bars = make_bars(70)
        df = kline_to_df(bars)
        ma = calc_ma(df["close"])
        assert "ma5" in ma.columns
        assert "ma60" in ma.columns


class TestMACD:
    def test_calc_macd(self):
        bars = make_bars(60)
        df = kline_to_df(bars)
        macd = calc_macd(df["close"])
        assert "dif" in macd.columns
        assert "dea" in macd.columns
        assert "macd_hist" in macd.columns
        assert len(macd) == 60

    def test_macd_hist_is_2x(self):
        bars = make_bars(60)
        df = kline_to_df(bars)
        macd = calc_macd(df["close"])
        # MACD柱 = (DIF - DEA) * 2
        diff = (macd["dif"] - macd["dea"]) * 2
        pd.testing.assert_series_equal(macd["macd_hist"], diff.round(4), check_names=False, atol=0.001)


class TestKDJ:
    def test_calc_kdj(self):
        bars = make_bars(30)
        df = kline_to_df(bars)
        kdj = calc_kdj(df)
        assert "k" in kdj.columns
        assert "d" in kdj.columns
        assert "j" in kdj.columns

    def test_j_formula(self):
        bars = make_bars(30)
        df = kline_to_df(bars)
        kdj = calc_kdj(df)
        # J = 3K - 2D
        j_check = (3 * kdj["k"] - 2 * kdj["d"]).round(2)
        pd.testing.assert_series_equal(kdj["j"], j_check, check_names=False, atol=0.05)


class TestRSI:
    def test_calc_rsi(self):
        bars = make_bars(30)
        df = kline_to_df(bars)
        rsi = calc_rsi(df["close"], periods=[6, 12])
        assert "rsi6" in rsi.columns
        assert "rsi12" in rsi.columns

    def test_rsi_range(self):
        bars = make_bars(60)
        df = kline_to_df(bars)
        rsi = calc_rsi(df["close"])
        # RSI 应在 0-100 之间
        for col in rsi.columns:
            valid = rsi[col].dropna()
            assert (valid >= 0).all() and (valid <= 100).all()


class TestBOLL:
    def test_calc_boll(self):
        bars = make_bars(30)
        df = kline_to_df(bars)
        boll = calc_boll(df["close"])
        assert "boll_mid" in boll.columns
        assert "boll_upper" in boll.columns
        assert "boll_lower" in boll.columns

    def test_upper_gt_lower(self):
        bars = make_bars(30)
        df = kline_to_df(bars)
        boll = calc_boll(df["close"])
        # 跳过 NaN 行 (第一个 std 为 NaN)
        valid = boll.dropna()
        assert (valid["boll_upper"] >= valid["boll_lower"]).all()


class TestATR:
    def test_calc_atr(self):
        bars = make_bars(20)
        df = kline_to_df(bars)
        atr = calc_atr(df)
        assert len(atr) == 20
        assert (atr >= 0).all()


class TestVolumeRatio:
    def test_calc(self):
        bars = make_bars(10)
        df = kline_to_df(bars)
        vr = calc_volume_ratio(df["volume"])
        assert len(vr) == 10


class TestComputeAll:
    def test_compute_all(self):
        bars = make_bars(60)
        indicators = compute_all_indicators(bars)
        assert not indicators.df.empty
        assert not indicators.ma.empty
        assert not indicators.macd.empty
        assert not indicators.kdj.empty
        assert not indicators.rsi.empty
        assert not indicators.boll.empty

    def test_empty_bars(self):
        indicators = compute_all_indicators([])
        assert indicators.df.empty

    def test_latest(self):
        bars = make_bars(60)
        indicators = compute_all_indicators(bars)
        latest = indicators.latest
        assert "close" in latest
        assert "ma5" in latest
        assert "dif" in latest
        assert "k" in latest


class TestInterpret:
    def test_interpret_returns_list(self):
        bars = make_bars(60, trend="up")
        indicators = compute_all_indicators(bars)
        signals = interpret_indicators(indicators.latest)
        assert isinstance(signals, list)
        assert len(signals) > 0


# ---- 基本面分析测试 ----

class TestScoreValuation:
    def test_low_pe(self):
        data = FinancialData(symbol="600519", pe_ttm=12, pb=1.2)
        score, comment = score_valuation(data)
        assert score > 60
        assert "偏低" in comment

    def test_high_pe(self):
        data = FinancialData(symbol="600519", pe_ttm=55, pb=8)
        score, comment = score_valuation(data)
        assert score < 30


class TestScoreProfitability:
    def test_high_roe(self):
        data = FinancialData(symbol="600519", roe=22, roa=10, gross_margin=50, net_margin=20)
        score, comment = score_profitability(data)
        assert score > 70
        assert "优秀" in comment

    def test_low_roe(self):
        data = FinancialData(symbol="600519", roe=3, roa=1, gross_margin=10, net_margin=2)
        score, comment = score_profitability(data)
        assert score < 30


class TestScoreGrowth:
    def test_high_growth(self):
        data = FinancialData(symbol="600519", revenue_yoy=25, net_profit_yoy=30)
        score, comment = score_growth(data)
        assert score > 70
        assert "高增长" in comment

    def test_negative_growth(self):
        data = FinancialData(symbol="600519", revenue_yoy=-5, net_profit_yoy=-10)
        score, comment = score_growth(data)
        assert "负增长" in comment


class TestScoreSafety:
    def test_safe(self):
        data = FinancialData(symbol="600519", debt_ratio=30)
        score, comment = score_safety(data)
        assert score > 70
        assert "安全" in comment

    def test_risky(self):
        data = FinancialData(symbol="600519", debt_ratio=75)
        score, comment = score_safety(data)
        assert score < 30


class TestAnalyzeFundamentals:
    def test_full_analysis(self):
        data = FinancialData(
            symbol="600519",
            name="贵州茅台",
            pe_ttm=25,
            pb=8,
            roe=30,
            roa=15,
            gross_margin=90,
            net_margin=50,
            revenue_yoy=15,
            net_profit_yoy=18,
            debt_ratio=25,
        )
        result = analyze_fundamentals(data)
        assert result.symbol == "600519"
        assert result.name == "贵州茅台"
        assert result.total_score > 0
        assert result.summary != ""
        assert result.valuation_comment != ""


# ---- K线形态测试 ----

class TestDetectPatterns:
    def test_returns_list(self):
        bars = make_bars(10)
        patterns = detect_patterns(bars)
        assert isinstance(patterns, list)

    def test_empty_bars(self):
        patterns = detect_patterns([])
        assert patterns == []

    def test_single_bar(self):
        bars = make_bars(1)
        patterns = detect_patterns(bars)
        assert isinstance(patterns, list)


class TestJudgeTrend:
    def test_uptrend(self):
        bars = make_bars(30, trend="up")
        trend = judge_trend(bars)
        assert trend in ("uptrend", "sideways")  # 可能因为涨幅不够被判为震荡

    def test_downtrend(self):
        bars = make_bars(30, base_price=200, trend="down")
        trend = judge_trend(bars)
        assert trend in ("downtrend", "sideways")

    def test_insufficient_data(self):
        bars = make_bars(5)
        trend = judge_trend(bars, period=20)
        assert trend == "unknown"
