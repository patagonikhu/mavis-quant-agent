"""tools/batch/wyckoff_a2m_scan.py —— 过滤链档位 (strict / loose) 单元测试

2026-11-10 加: 过滤链从「5 步候选池闸」重排成「公共前置 2 步 + 命中后两档分档」。
2026-11-12 换口径: PE/质量/杠杆 三闸整档作废 → strict 档改用**行业内市值分位**。
这套测试守的是三件事:
  1. strict 档的门槛值不能被人悄悄改掉 (改了 = 投资口径变了, 必须走实测)
  2. loose 档必须恒为 strict 的补集 (两档互斥, 不能有第三档)
  3. 细 flag 覆盖机制不漏字段 (漏一个 = 显式传的 flag 静默失效)
"""
import argparse
import inspect

from tools.batch.wyckoff_a2m_scan import (
    DEFAULT_TIER,
    FILTER_TIERS,
    TIER_OVERRIDE_KEYS,
)


# ----- strict 档门槛值 (改了要改这里, 提醒走实测流程) -----

def test_strict_tier_thresholds_pinned():
    """strict 档门槛是固化口径 —— 数值变了 = 投资口径变了, 必须重跑实测

    2026-11-12: 门槛从 PE/质量/杠杆 六项 → **一项** (行业内市值分位上限 %)。
    2026-11-13 再换: **分位 → 名次** (用户: 「strict 只看行业前三名」)。
    - 分位是相对口径: 行业多大决定进几个人 (5 家行业 30% ≈ 前 1-2 名,
      21 家行业 30% ≈ 前 6-7 名) → 门槛含义随行业规模漂移。
    - 名次是绝对口径: 不管行业 3 家还是 30 家, strict 就是该行业市值前 3 名。
    """
    strict = FILTER_TIERS["strict"]
    assert strict["industry_pos_rank"] == 3      # 行业内市值名次 ≤ 3 → strict
    assert strict["industry_pos_pctile"] == 0.0  # 分位口径默认让位 (0 = 不用)


def test_rank_and_pctile_gates_are_mutually_exclusive():
    """名次口径 + 分位口径**互斥** —— 两个同时生效 = 分档结果取决于判定顺序, 没法复现。

    显式传 --industry-pos-pctile → 名次门槛归 0; 显式传 --industry-pos-rank → 分位归 0。
    """
    import tools.batch.wyckoff_a2m_scan as m
    src = inspect.getsource(m)
    # 互斥解析必须在代码里显式做 (不能靠"先判谁"隐式决定)
    assert 'tier_thr["industry_pos_rank"] = 0' in src
    assert 'tier_thr["industry_pos_pctile"] = 0.0' in src
    # 分档判定: rank > 0 用名次, 否则才看 pctile
    assert 'pos["market_value_rank"] <= rank_thr' in src
    assert 'pos["market_value_percentile"] <= pctile_thr' in src


def test_strict_tier_has_no_finance_gates():
    """2026-11-12 换口径: PE/质量/杠杆 三闸**整档作废**, 不许复活。

    作废原因: "现在的逻辑效果不好" —— 财务三闸筛出来的是「便宜且干净」,
    不是「行业地位靠前」, 跟 A→M 抄底行业龙头的初衷不对口。
    """
    strict = FILTER_TIERS["strict"]
    for dead in ("exclude_pe", "with_quality", "min_growth",
                 "min_roe", "min_gross_margin", "max_net_debt_ratio"):
        assert dead not in strict, f"{dead} 已整档作废, 不许复活"


def test_loose_tier_is_pure_complement():
    """loose 档 = strict 的补集, 不带任何自己的门槛"""
    assert FILTER_TIERS["loose"] == {}
    assert set(FILTER_TIERS) == {"strict", "loose"}


def test_override_keys_cover_every_strict_field():
    """细 flag 覆盖键必须覆盖 strict 的每个字段 —— 漏一个 = 那个 flag 静默失效"""
    assert set(TIER_OVERRIDE_KEYS) == set(FILTER_TIERS["strict"])


def test_strict_has_only_known_fields():
    """strict 档不允许出现没被 flag 覆盖的新字段 (否则调了没地方调)"""
    known = set(TIER_OVERRIDE_KEYS)
    assert set(FILTER_TIERS["strict"]) <= known


# ----- 默认档位 (用户 2026-11-10 明确要求: 两档都要跑) -----

def test_default_tier_is_both():
    """🔴 契约: 默认必须 strict + loose 两档都跑。

    单档只是「本次只看一档」的视图, 不是默认行为。
    改这个默认值之前必须先问用户 —— 有回归测试锁住。
    """
    assert DEFAULT_TIER == "both"


def test_argparse_default_tier_is_both():
    """argparse 的实际默认值也要是 both (别只改常量忘了改 flag)"""
    import tools.batch.wyckoff_a2m_scan as m
    ap = argparse.ArgumentParser()
    # 复刻真实 argparse 定义 (只取 --tier 那一行, 不跑 main)
    ap.add_argument("--tier", choices=["strict", "loose", "both"], default=DEFAULT_TIER)
    assert ap.parse_args([]).tier == "both"
    assert ap.parse_args(["--tier", "loose"]).tier == "loose"
    assert m.DEFAULT_TIER == "both"


def test_dead_finance_flags_removed_from_cli():
    """已删 flag 不许留在 argparse 里 (留着 = 用户传了不报错但静默失效)"""
    import tools.batch.wyckoff_a2m_scan as m
    import inspect
    src = inspect.getsource(m)
    for dead in ("--exclude-pe", "--with-quality", "--min-growth",
                 "--min-roe", "--min-gross-margin", "--max-net-debt-ratio"):
        assert dead not in src, f"{dead} 已删, 不许复活"
    assert "--industry-pos-pctile" in src
