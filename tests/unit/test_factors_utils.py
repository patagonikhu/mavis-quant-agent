"""factors/utils.py 单元测试"""
import pytest
import pandas as pd
from tools.factors.utils import (
    df_from_bars,
    asof_slice,
    normalize_asof,
    clamp,
    _amount_proxy,
)


# ----- 测试数据构造 -----

def _make_dict_bars(n: int = 60, start_price: float = 100.0) -> list[dict]:
    """构造 dict 形式的 K 线 (跟 dump_data 字段一致)"""
    rows = []
    price = start_price
    for i in range(n):
        date = f"2026{(i // 30 + 1) % 12 + 1:02d}{(i % 30) + 1:02d}"
        open_p = price
        close_p = price + 0.5
        high_p = max(open_p, close_p) + 0.3
        low_p = min(open_p, close_p) - 0.3
        rows.append({
            "trade_date": date,
            "open": open_p, "close": close_p,
            "high": high_p, "low": low_p,
            "volume": 1_000_000 + i * 1000,
            "amount": 100_000_000 + i * 100_000,
            "pct_chg": 0.5,
        })
        price = close_p
    return rows


# ----- df_from_bars 测试 -----

class TestDfFromBars:
    def test_from_dict_list(self):
        bars = _make_dict_bars(5)
        df = df_from_bars(bars)
        assert len(df) == 5
        assert "close" in df.columns
        assert "trade_date" in df.columns

    def test_from_dataframe_passthrough(self):
        original = pd.DataFrame({
            "close": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "open": [1.1, 2.1],
            "trade_date": ["20260101", "20260102"],
        })
        df = df_from_bars(original)
        assert len(df) == 2
        assert df["close"].iloc[0] == 1.0

    def test_include_filter(self):
        bars = _make_dict_bars(3)
        df = df_from_bars(bars, include=("close", "high"))
        assert list(df.columns) == ["close", "high"]
        assert len(df) == 3


# ----- asof_slice 测试 -----

class TestAsofSlice:
    def test_none_passthrough(self):
        df = pd.DataFrame({"trade_date": ["20260101", "20260102"], "close": [1, 2]})
        out = asof_slice(df, None)
        assert out is df

    def test_yyyymmdd_format(self):
        df = pd.DataFrame({
            "trade_date": ["20260101", "20260201", "20260301"],
            "close": [10, 20, 30],
        })
        out = asof_slice(df, "20260215")
        assert len(out) == 2
        assert out["close"].iloc[-1] == 20

    def test_dash_format(self):
        df = pd.DataFrame({
            "trade_date": ["2026-01-01", "2026-02-01", "2026-03-01"],
            "close": [10, 20, 30],
        })
        out = asof_slice(df, "2026-02-15")
        assert len(out) == 2

    def test_date_column_fallback(self):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
            "close": [10, 20, 30],
        })
        out = asof_slice(df, "20260215")
        assert len(out) == 2

    def test_invalid_asof_passthrough(self):
        df = pd.DataFrame({"trade_date": ["20260101"], "close": [1]})
        out = asof_slice(df, "garbage")
        assert out is df


class TestNormalizeAsof:
    def test_none(self):
        assert normalize_asof(None) is None

    def test_yyyymmdd(self):
        assert normalize_asof("20260215") == "20260215"

    def test_dash(self):
        assert normalize_asof("2026-02-15") == "20260215"

    def test_slash(self):
        assert normalize_asof("2026/02/15") == "20260215"

    def test_truncate(self):
        # 只取前 8 位
        assert normalize_asof("2026-02-15T10:30:00") == "20260215"

    def test_invalid_returns_none(self):
        assert normalize_asof("abc") is None


# ----- clamp 测试 -----

class TestClamp:
    def test_in_range(self):
        assert clamp(0.5) == 0.5

    def test_above_max(self):
        assert clamp(1.5) == 1.0

    def test_below_min(self):
        assert clamp(-0.3) == 0.0

    def test_custom_range(self):
        assert clamp(15, lo=10, hi=20) == 15
        assert clamp(25, lo=10, hi=20) == 20
        assert clamp(5, lo=10, hi=20) == 10

    def test_invalid_value(self):
        assert clamp("abc") == 0.0


# ----- _amount_proxy 测试 -----

class TestAmountProxy:
    def test_use_existing_amount(self):
        df = pd.DataFrame({
            "volume": [100, 200, 300],
            "close": [10, 20, 30],
            "amount": [1_000_000, 2_000_000, 3_000_000],
        })
        amt = _amount_proxy(df)
        # 现有 amount 列有数据,应直接用
        assert float(amt.iloc[0]) == 1_000_000

    def test_fallback_when_amount_zero(self):
        df = pd.DataFrame({
            "volume": [100, 200, 300],
            "close": [10, 20, 30],
            "amount": [0.0, 0.0, 0.0],
        })
        amt = _amount_proxy(df)
        # 兜底: volume × close / 1000 (千元)
        assert float(amt.iloc[0]) == 100 * 10 / 1000  # = 1.0
        assert float(amt.iloc[1]) == 200 * 20 / 1000  # = 4.0

    def test_missing_amount_column(self):
        df = pd.DataFrame({
            "volume": [100, 200],
            "close": [10, 20],
        })
        amt = _amount_proxy(df)
        assert len(amt) == 2
        assert float(amt.iloc[0]) == 100 * 10 / 1000