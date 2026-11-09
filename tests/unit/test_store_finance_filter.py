"""tools/storage/store.py —— finance 过滤 (filter_codes_by_finance) 单元测试

2026-11-09 加: 修 Bug2 (--with-finance 承诺"剔除亏损股"但实际没生效) +
Bug3 (md 表格 PE 列全空) 时补的回归测试。

盯的是两件事:
  1. 判定口径 —— 亏损股在 stk_factor 里是 **pe/pe_ttm = NULL** (pandas 读成 NaN),
     不是 pe <= 0 (全库 5588 只 PE 恒为正, 没有一只 <= 0)。
  2. NaN 陷阱 —— parquet 的 NULL 读进 pandas 是 NaN 不是 None,
     `v is None` 判缺值会全部失效 (漏剔 1734 只亏损股)。
"""
import math

import pytest

from tools.storage.store import (
    DataStore,
    _is_num,
    _pe_finance_ok,
)


# ----- _is_num: NaN vs None 判定 -----

def test_is_num_rejects_none_and_nan():
    """None 和 NaN 都算缺值 —— 这是 NaN 陷阱的根因"""
    assert _is_num(None) is False
    assert _is_num(float("nan")) is False
    assert _is_num(math.nan) is False


def test_is_num_accepts_real_numbers():
    assert _is_num(20.5) is True
    assert _is_num(0) is True
    assert _is_num(-3.2) is True


# ----- _pe_finance_ok: 单只票判定 -----

@pytest.mark.parametrize("pe,pe_ttm,expected", [
    # pe_ttm 缺失 = TTM 算不出 = 亏损股 → 剔
    (None,    None,   False),
    (20.0,    None,   False),
    # pe_ttm <= 0 = 亏损 → 剔
    (None,    0.0,    False),
    (None,    -5.0,   False),
    # pe_ttm 过高 = 估值异常 → 剔
    (None,    501.0,  False),
    (None,    500.0,  True),      # 边界: 正好等于上限保留
    # pe 亏损 / 过高 → 剔
    (-1.0,    20.0,   False),
    (0.0,     20.0,   False),
    (600.0,   20.0,   False),
    (500.0,   20.0,   True),      # 边界
    # pe 缺失但 pe_ttm 有效 → 保留 (dynamic PE 算不出, TTM 盈利)
    (None,    20.0,   True),
    # 正常
    (30.0,    25.0,   True),
])
def test_pe_finance_ok(pe, pe_ttm, expected):
    assert _pe_finance_ok(pe, pe_ttm, 500.0) is expected


# ----- filter_codes_by_finance: 真实数据不变量 -----

def test_filter_codes_by_finance_invariants():
    """跑真实 stk_factor, 查不变量 (不写死具体 code, 避免数据更新就挂)"""
    kept, pe_map = DataStore.filter_codes_by_finance([], pe_max=500.0)
    assert kept == [] and pe_map == {}

    # 取一批真实 code (从 stk_factor 里现取前 800 只, 控制查询体积)
    from tools.storage.store import STK_FACTOR_DIR, _conn
    files = sorted(STK_FACTOR_DIR.glob("*.parquet"))
    if not files:
        pytest.skip("本机没有 stk_factor 数据")
    glob_expr = "[" + ", ".join(f"'{f}'" for f in files[-2:]) + "]"
    codes = _conn().execute(f"""
        SELECT DISTINCT ts_code FROM read_parquet({glob_expr})
    """).df()["ts_code"].str.split(".").str[0].tolist()[:800]
    if not codes:
        pytest.skip("stk_factor 没数据")

    kept, pe_map = DataStore.filter_codes_by_finance(codes, pe_max=500.0)

    # 1) 保留的票 pe_ttm 必须有值且在 (0, pe_max] 区间 —— NaN 陷阱的守门
    for c in kept:
        info = pe_map.get(c)
        assert info is not None, f"{c} 保留但 pe_map 无条目"
        assert isinstance(info["pe_ttm"], (int, float)), f"{c} pe_ttm 缺值: {info['pe_ttm']!r}"
        assert 0 < info["pe_ttm"] <= 500.0, f"{c} pe_ttm 越界: {info['pe_ttm']}"
        # pe 缺失是允许的 (dynamic PE 算不出), 但不能是 NaN
        if info["pe"] is not None:
            assert _is_num(info["pe"]), f"{c} pe 是 NaN: {info['pe']!r}"

    # 2) pe_map 里不能残留 NaN (下游 md 渲染 isinstance(x, float) 会出 "nan")
    for c, info in pe_map.items():
        for k in ("pe", "pe_ttm"):
            v = info[k]
            assert v is None or _is_num(v), f"{c}.{k} 残留 NaN: {v!r}"

    # 3) 剔除的票要么是 pe_ttm 缺失, 要么是 pe/pe_ttm 越界
    for c in set(codes) - set(kept):
        info = pe_map.get(c)
        if info is None:
            continue
        if info["pe_ttm"] is None:
            continue
        assert not _pe_finance_ok(info["pe"], info["pe_ttm"], 500.0), \
            f"{c} 被剔但判定逻辑说它该保留"
