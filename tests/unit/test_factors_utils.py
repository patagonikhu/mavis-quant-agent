"""factors/utils.py 单元测试

2026-11-09 瘦身: 22 → 9 个用例 (同类断言合并, 覆盖面不减)。
删的是「拆得太碎」的重复用例, 留的是**每个工具的核心行为 + 关键边界** ——
asof 日期对齐写错会让因子静默算错 (不崩, 但结果是错的), 这是最该守住的一块。

测的代码: tools/factors/utils.py —— 所有因子共用的底层工具, 生产链路。
"""
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


# ----- df_from_bars -----

def test_df_from_dict_list():
    """dict 列表 → df (行数 + 关键列名)"""
    df = df_from_bars(_make_dict_bars(5))
    assert len(df) == 5
    assert "close" in df.columns
    assert "trade_date" in df.columns


def test_df_from_bars_include_filter():
    """include 只保留指定列, 且列序按 include 顺序"""
    df = df_from_bars(_make_dict_bars(3), include=("close", "high"))
    assert list(df.columns) == ["close", "high"]
    assert len(df) == 3


# ----- asof_slice (日期对齐 = 静默算错的高发点) -----

def test_asof_slice_yyyymmdd():
    """YYYYMMDD 输入 → 截到 asof 当天之前最后一根"""
    df = pd.DataFrame({
        "trade_date": ["20260101", "20260201", "20260301"],
        "close": [10, 20, 30],
    })
    out = asof_slice(df, "20260215")
    assert len(out) == 2
    assert out["close"].iloc[-1] == 20


def test_asof_slice_dash_and_date_column_fallback():
    """dash 格式 + 日期列名回退 (trade_date → date) 两种输入都对齐"""
    dash = pd.DataFrame({
        "trade_date": ["2026-01-01", "2026-02-01", "2026-03-01"],
        "close": [10, 20, 30],
    })
    assert len(asof_slice(dash, "2026-02-15")) == 2

    fallback = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-03-01"]),
        "close": [10, 20, 30],
    })
    assert len(asof_slice(fallback, "20260215")) == 2


def test_asof_slice_invalid_passthrough():
    """asof 为 None / 非法 → 原样返回 (不截断, 不报错)"""
    df = pd.DataFrame({"trade_date": ["20260101"], "close": [1]})
    assert asof_slice(df, None) is df
    assert asof_slice(df, "garbage") is df


# ----- normalize_asof -----

def test_normalize_asof_formats():
    """各种日期输入统一成 YYYYMMDD; 非法 → None"""
    assert normalize_asof("20260215") == "20260215"
    assert normalize_asof("2026-02-15") == "20260215"
    assert normalize_asof("2026/02/15") == "20260215"
    # 带时间戳的只取前 8 位
    assert normalize_asof("2026-02-15T10:30:00") == "20260215"
    assert normalize_asof("abc") is None
    assert normalize_asof(None) is None


# ----- clamp -----

def test_clamp_bounds():
    """clamp 默认区间 + 自定义区间 + 非法值兜底"""
    assert clamp(0.5) == 0.5
    assert clamp(1.5) == 1.0
    assert clamp(-0.3) == 0.0
    assert clamp(15, lo=10, hi=20) == 15
    assert clamp(25, lo=10, hi=20) == 20
    assert clamp(5, lo=10, hi=20) == 10
    assert clamp("abc") == 0.0


# ----- _amount_proxy -----

def test_amount_proxy_use_existing():
    """已有 amount 列且非空 → 直接用"""
    df = pd.DataFrame({
        "volume": [100, 200, 300],
        "close": [10, 20, 30],
        "amount": [1_000_000, 2_000_000, 3_000_000],
    })
    amt = _amount_proxy(df)
    assert float(amt.iloc[0]) == 1_000_000


def test_amount_proxy_fallback():
    """amount 全 0 → 兜底 volume × close / 1000; amount 列缺失 → 同兜底"""
    zero = pd.DataFrame({
        "volume": [100, 200],
        "close": [10, 20],
        "amount": [0.0, 0.0],
    })
    amt = _amount_proxy(zero)
    assert float(amt.iloc[0]) == 100 * 10 / 1000
    assert float(amt.iloc[1]) == 200 * 20 / 1000

    missing = pd.DataFrame({
        "volume": [100, 200],
        "close": [10, 20],
    })
    amt2 = _amount_proxy(missing)
    assert len(amt2) == 2
    assert float(amt2.iloc[0]) == 100 * 10 / 1000
