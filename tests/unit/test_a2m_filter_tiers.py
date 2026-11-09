"""tools/batch/wyckoff_a2m_scan.py —— 过滤链档位 (standard / loose) 单元测试

2026-11-10 加: 过滤链从「5 步候选池闸」重排成「公共前置 2 步 + 命中后两档分档」。
这套测试守的是三件事:
  1. standard 档的门槛值不能被人悄悄改掉 (改了 = 投资口径变了, 必须走实测)
  2. loose 档必须恒为 standard 的补集 (两档互斥, 不能有第三档)
  3. 细 flag 覆盖机制不漏字段 (漏一个 = 显式传的 flag 静默失效)
"""
from tools.batch.wyckoff_a2m_scan import (
    FILTER_TIERS,
    TIER_OVERRIDE_KEYS,
)


# ----- standard 档门槛值 (改了要改这里, 提醒走实测流程) -----

def test_standard_tier_thresholds_pinned():
    """standard 档门槛是固化口径 —— 数值变了 = 投资口径变了, 必须重跑实测"""
    std = FILTER_TIERS["standard"]
    assert std["exclude_pe"] == 60.0            # PE 上限
    assert std["with_quality"] == "basic"       # 质量档位
    assert std["min_growth"] is None            # None = 用档位默认 yoy 门槛
    assert std["min_roe"] == 5.0                # ROE 下限 %
    assert std["min_gross_margin"] == 20.0      # 毛利率下限 %
    assert std["max_net_debt_ratio"] == 0.5     # 净负债/市值上限


def test_loose_tier_is_pure_complement():
    """loose 档 = standard 的补集, 不带任何自己的门槛"""
    assert FILTER_TIERS["loose"] == {}
    assert set(FILTER_TIERS) == {"standard", "loose"}


def test_override_keys_cover_every_standard_field():
    """细 flag 覆盖键必须覆盖 standard 的每个字段 —— 漏一个 = 那个 flag 静默失效"""
    assert set(TIER_OVERRIDE_KEYS) == set(FILTER_TIERS["standard"])


def test_standard_has_only_known_fields():
    """standard 档不允许出现没被 flag 覆盖的新字段 (否则调了没地方调)"""
    known = set(TIER_OVERRIDE_KEYS)
    assert set(FILTER_TIERS["standard"]) <= known
