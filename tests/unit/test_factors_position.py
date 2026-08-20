"""factors/price/position.py 单元测试"""
import pytest
import pandas as pd
from tools.factors.price.position import PricePositionFactor
from tools.factors.utils import df_from_bars


def _make_dict_bars(n: int = 60, start_price: float = 100.0, trend: str = "up") -> list[dict]:
    """构造 K 线,trend ∈ {up, down, side, hammer}"""
    rows = []
    price = start_price
    for i in range(n):
        date = f"2026{(i // 30 + 1) % 12 + 1:02d}{(i % 30) + 1:02d}"
        if trend == "up":
            open_p = price
            close_p = price + 0.5
            high_p = max(open_p, close_p) + 0.2
            low_p = min(open_p, close_p) - 0.3
        elif trend == "down":
            open_p = price
            close_p = price - 0.5
            high_p = max(open_p, close_p) + 0.3
            low_p = min(open_p, close_p) - 0.2
        elif trend == "hammer":
            # 长下影 + 短上影
            open_p = price
            close_p = price + 0.1
            high_p = close_p + 0.05  # 短上影
            low_p = open_p - 1.0     # 长下影
        else:
            open_p = close_p = price
            high_p = price + 0.1
            low_p = price - 0.1
        rows.append({
            "trade_date": date,
            "open": open_p, "close": close_p,
            "high": high_p, "low": low_p,
            "volume": 1_000_000, "amount": 100_000_000,
        })
        price = close_p
    return rows


class TestPricePositionFactor:
    def test_output_structure(self):
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60))
        result = f(df)
        assert isinstance(result, dict)
        assert set(result.keys()) == {
            "close_pos_day", "close_pos_20",
            "upper_shadow_pct", "upper_shadow_5d_avg",
        }
        # 4 个 series 长度等于 K 线数
        for k, v in result.items():
            assert isinstance(v, pd.Series)
            assert len(v) == 60

    def test_close_pos_day_in_range(self):
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60))
        result = f(df)
        cpd = result["close_pos_day"]
        # 所有值应在 [0, 1]
        assert (cpd >= 0).all()
        assert (cpd <= 1).all()

    def test_close_pos_20_in_range(self):
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60))
        result = f(df)
        cp20 = result["close_pos_20"]
        assert (cp20 >= 0).all()
        assert (cp20 <= 1).all()

    def test_upper_shadow_non_negative(self):
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60))
        result = f(df)
        # 上影线 ≥ 0
        assert (result["upper_shadow_pct"] >= 0).all()

    def test_uptrend_high_close_pos_20(self):
        """持续上涨后,close_pos_20 应接近 1(新高)"""
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60, trend="up"))
        result = f(df)
        assert float(result["close_pos_20"].iloc[-1]) > 0.8

    def test_downtrend_low_close_pos_20(self):
        """持续下跌后,close_pos_20 应接近 0(新低)"""
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60, trend="down"))
        result = f(df)
        assert float(result["close_pos_20"].iloc[-1]) < 0.2

    def test_asof_slice_works(self):
        """asof 切片: 60 根切到前 30 根"""
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(60))
        full = f(df)
        sliced = f(df, asof_date="20260130")
        # 切片后只剩 30 根(20260101 ~ 20260130)
        assert len(sliced["close_pos_day"]) < len(full["close_pos_day"])

    def test_too_short_returns_empty(self):
        """K 线不足 2 根时返回空 series"""
        f = PricePositionFactor()
        df = df_from_bars(_make_dict_bars(1))
        result = f(df)
        for v in result.values():
            assert len(v) == 0

    def test_accepts_dataframe_directly(self):
        """直接接受 DataFrame 输入"""
        f = PricePositionFactor()
        df = pd.DataFrame({
            "trade_date": [f"2026010{i}" for i in range(1, 6)],
            "open": [100, 101, 102, 103, 104],
            "close": [101, 102, 103, 104, 105],
            "high": [102, 103, 104, 105, 106],
            "low": [99, 100, 101, 102, 103],
            "volume": [1_000_000] * 5,
        })
        result = f(df)
        assert len(result["close_pos_day"]) == 5