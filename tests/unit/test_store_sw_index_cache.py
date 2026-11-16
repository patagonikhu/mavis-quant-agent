"""申万行业指数的进程内缓存 —— 写盘后必须自动失效

2026-11-16 bug: 行业 K 线帧只加载一次就永不刷新 → 同一进程里 sync 先写 L2 行业 K 线,
再补算 L3 行业威科夫时读到的是旧帧 → 123 个 L3 行业的威科夫 cache 卡在旧日期。
现在改成「文件指纹」判断 (文件被写过 → 缓存作废重读)。

回归用例:
  1. 帧: 同一进程里写盘后再取 → 必须拿到新行
  2. 单行业 bars: 同一进程里写盘后再取 → 必须拿到新行
  3. 行业成分表: 同一进程里写盘后再取 → 必须拿到新归属
"""
from __future__ import annotations

import pandas as pd
import pytest

from tools.storage import store
from tools.storage.store import DataStore


def _kline_row(trade_date: str, close: float = 10.0) -> dict:
    return {
        "ts_code": "801012.SI",
        "trade_date": trade_date,
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "vol": 1000.0,
        "amount": 10000.0,
        "name": "食品饮料",
    }


def _member_row(ts_code: str, l3_code: str, l3_name: str) -> dict:
    return {
        "ts_code": ts_code,
        "l1_code": "801120.SI",
        "l1_name": "食品饮料",
        "l2_code": "801123.SI",
        "l2_name": "饮料制造",
        "l3_code": l3_code,
        "l3_name": l3_name,
    }


def _write_kline_file(directory, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(directory / "2026.parquet", index=False)


@pytest.fixture(autouse=True)
def _reset_process_cache():
    """每个用例前后都清进程内缓存 (别让用例之间互相污染)"""
    def _clear():
        store._SW_INDEX_FRAME = None
        store._SW_INDEX_FILES_STATE = ()
        store._SW_INDEX_BARS_CACHE.clear()
        store._INDUSTRY_MEMBER_INFO = None
        store._INDUSTRY_MEMBER_FILES_STATE = ()

    _clear()
    yield
    _clear()


def test_sw_index_frame_reloads_after_kline_written(tmp_path, monkeypatch):
    """写盘后帧必须重建 (原来缓存永不失效 → 读到旧帧)"""
    monkeypatch.setattr(store, "SW_INDEX_KLINE_DIR", tmp_path)
    _write_kline_file(tmp_path, [_kline_row("20260102"), _kline_row("20260103")])

    frame = DataStore._sw_index_frame()
    assert len(frame) == 2

    # 模拟 sync 落盘: 同一进程里追加一个新交易日
    _write_kline_file(tmp_path, [_kline_row("20260102"), _kline_row("20260103"),
                                _kline_row("20260104")])

    frame_after = DataStore._sw_index_frame()
    assert len(frame_after) == 3
    assert frame_after["trade_date"].max() == "20260104"


def test_sw_index_bars_reflect_rows_written_in_same_process(tmp_path, monkeypatch):
    """L2 写盘 → L3 补算的同进程读, 必须看到新交易日 (bug 的现场)"""
    monkeypatch.setattr(store, "SW_INDEX_KLINE_DIR", tmp_path)
    _write_kline_file(tmp_path, [_kline_row("20260102"), _kline_row("20260103")])

    bars = DataStore.get_sw_index_bars("801012.SI")
    assert [b["trade_date"] for b in bars] == ["20260102", "20260103"]

    _write_kline_file(tmp_path, [_kline_row("20260102"), _kline_row("20260103"),
                                _kline_row("20260104")])

    bars_after = DataStore.get_sw_index_bars("801012.SI")
    assert [b["trade_date"] for b in bars_after] == ["20260102", "20260103", "20260104"]


def test_sw_index_bars_cache_kept_when_file_unchanged(tmp_path, monkeypatch):
    """文件没动 → 缓存继续用 (别把缓存优化改没了)"""
    monkeypatch.setattr(store, "SW_INDEX_KLINE_DIR", tmp_path)
    _write_kline_file(tmp_path, [_kline_row("20260102")])

    bars = DataStore.get_sw_index_bars("801012.SI")
    assert len(bars) == 1
    assert "801012.SI" in store._SW_INDEX_BARS_CACHE

    bars_again = DataStore.get_sw_index_bars("801012.SI")
    assert bars_again is bars


def test_industry_member_info_reloads_after_member_file_written(tmp_path, monkeypatch):
    """成分表刚落盘 → 同一进程里必须读到新归属 (否则行业威科夫挂错行业)"""
    member_path = tmp_path / "industry_member.parquet"
    monkeypatch.setattr(store, "SW_INDEX_MEMBER_PARQUET", member_path)

    pd.DataFrame([_member_row("300488.SZ", "850111.SI", "金属制品")]).to_parquet(
        member_path, index=False)
    info = DataStore._industry_member_info()
    assert info["300488.SZ"]["l3_name"] == "金属制品"

    pd.DataFrame([_member_row("300488.SZ", "850222.SI", "其他金属制品")]).to_parquet(
        member_path, index=False)
    info_after = DataStore._industry_member_info()
    assert info_after["300488.SZ"]["l3_name"] == "其他金属制品"
