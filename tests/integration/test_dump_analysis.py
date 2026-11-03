"""
tests/integration/test_dump_analysis.py - dump + analysis 集成测试

**目的**: 用户最担心 analysis 层 regression bug (2026-08-17 发现的 4 个 bug 就是这一层),
这套测试覆盖 dump → RenderData → AnalysisEngine 的完整数据流,确保:
  1. dump 字段不丢 (ctx.name / ctx.industry 等)
  2. 核心 Phase1 strategy 都跑出来 (raw dict)
  3. (2026-10-27: OBV strategy 已删除, 相关测试一并移除)
  4. (2026-10-28: fflow 主力资金流维度整体删除, 相关测试一并移除)
  5. signals_active 替代 scene (2026-08-29 删 A/B/C/D/E 硬编码 if-else)
  6. AnalysisEngine.analyze() 单点入口能跑 (2026-08-29 修复)

**v3.6 API 变化 (2026-08-29)**:
- AnalysisResult 顶层: code/name/current_price/raw/signals_active/action (5 字段)
- 删: scene, scene_name, resonance_count, total_score, factor_scores (对象)
- 取而代之: raw dict (6 strategy 各一个 dict, 含 score/signals/verdict)
- signals_active: list[str] (从 raw[*].signals 聚合, LLM 自己判)

**防 regression 的关键断言**:
- ctx.name == dump.name (防 name 字段被默认 "" 覆盖)
- 核心 strategy 都在 result.raw 里

**运行**: pytest tests/integration/test_dump_analysis.py -v
**跳过**: 没 dump 的票会 pytest.skip (避免污染测试结果)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.analysis.render_data import RenderData
from tools.analysis.analysis_engine import AnalysisEngine, PHASE1_STRATEGY_CLASSES


# ----- Fixture: 5 只已同步的票 -----

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 5 只 fixture: 各板块代表 (持仓 + 半导体 + CPO)
TEST_CODES = ["300274", "300308", "600089", "002371", "688012"]

# Phase1 strategy 名单 (2026-08-29 简化: 删 ResonanceStrategy, 删 VolumePriceStrategy 拆分)
# 2026-10-27: OBV strategy 已删除, 现为 5 个
PHASE1_STRATEGY_NAMES = [s.__name__ for s in PHASE1_STRATEGY_CLASSES]


@pytest.fixture(scope="module")
def dumps():
    """加载 5 只票的 ctx (走 DataStore, 跳过无K线的)"""
    from tools.storage.store import DataStore  # 2026-10-27: 模块已改名 (原 tools.kline_store)
    loaded = {}
    for code in TEST_CODES:
        try:
            ctx = DataStore.get_ctx(code)
            if ctx.kline:
                loaded[code] = ctx
        except Exception:
            pass
    if not loaded:
        pytest.skip(f"没找到任何本地K线数据, 先跑: python -m tools.sync_watchlist_fresh {TEST_CODES[0]}")
    return loaded


@pytest.fixture(scope="module")
def analysis_data_per_code(dumps):
    """5 只票的 RenderData (构造 + 跑 AnalysisEngine)"""
    result = {}
    for code, ctx in dumps.items():
        engine = AnalysisEngine()
        ar = engine.analyze(ctx)
        ad = RenderData.from_result(ctx, ar)
        result[code] = {
            "dump": {},  # DataStore 模式无原始 dump dict
            "ad": ad,
            "ctx": ctx,
            "engine_result": ar,
        }
    return result


# =====================================================================
# 1. 数据流通: dump → RenderData (防 ctx 重建覆盖 bug)
# =====================================================================

class TestDataFlow:
    """dump 字段 → RenderData / RawContext 数据流不丢"""

    @pytest.mark.integration
    def test_ctx_name_equals_dump_name(self, analysis_data_per_code):
        """【防 regression】ctx.name == dump.name (防 name 字段被空串覆盖)

        2026-08-29: fixture 走 DataStore 模式 (dump: {}), 改成验证 ctx.name 非空
        """
        for code, data in analysis_data_per_code.items():
            ctx_name = data["ctx"].name
            assert ctx_name, f"{code}: ctx.name 是空字符串 (DataStore 应该填上)"

    @pytest.mark.integration
    def test_ctx_industry_equals_dump_industry(self, analysis_data_per_code):
        """ctx.industry 非空 (DataStore 应填, 等同 dump.industry 防 regression)"""
        for code, data in analysis_data_per_code.items():
            ctx_ind = data["ctx"].industry
            assert ctx_ind, f"{code}: ctx.industry 是空字符串 (DataStore 应该填上)"

    @pytest.mark.integration
    def test_ctx_kline_length_matches_dump(self, analysis_data_per_code):
        """ctx.kline 非空 (DataStore 应填 K 线)"""
        for code, data in analysis_data_per_code.items():
            ctx_k = data["ctx"].kline
            assert len(ctx_k) > 0, f"{code}: ctx.kline 空 (DataStore 应填 K 线)"


# =====================================================================
# 2. AnalysisEngine: 7 个核心 strategy 都跑出来
# =====================================================================

class TestAnalysisEngineStrategies:
    """AnalysisEngine.analyze() 必须把 6 个 Phase1 strategy 全跑出来 (raw dict)"""

    @pytest.mark.integration
    @pytest.mark.parametrize("strategy_class", PHASE1_STRATEGY_CLASSES)
    def test_each_core_strategy_in_raw(
        self, analysis_data_per_code, strategy_class
    ):
        """每个 Phase1 strategy 都在 result.raw 里, score 范围合理"""
        strategy_name = strategy_class.name  # chan/wyckoff/structure
        for code, data in analysis_data_per_code.items():
            raw = data["engine_result"].raw
            assert strategy_name in raw, (
                f"{code}: {strategy_name} 不在 raw (跑了哪几个: {list(raw.keys())})"
            )
            d = raw[strategy_name] or {}
            # score 范围因 strategy 而异:
            score_ranges = {
                "wyckoff":  (-1.5, 1.5),
                "structure": (-1.5, 1.5),
                "chan":     (-1.5, 1.5),
            }
            lo, hi = score_ranges.get(strategy_name, (-1.5, 1.5))
            score = d.get("score", 0) or 0
            assert lo <= score <= hi, (
                f"{code}: {strategy_name}.score = {score} 超出 [{lo}, {hi}] 范围"
            )
            # summary 不空 (有 signals 或 verdict 即可)
            assert d.get("summary") or d.get("verdict"), (
                f"{code}: {strategy_name} summary/verdict 都空"
            )

    @pytest.mark.integration
    def test_all_six_strategies_present(self, analysis_data_per_code):
        """一次检查 6 个 Phase1 strategy 全部在 raw"""
        for code, data in analysis_data_per_code.items():
            raw = data["engine_result"].raw
            for cls in PHASE1_STRATEGY_CLASSES:
                assert cls.name in raw, f"{code}: 缺 {cls.name} strategy"


# =====================================================================
# 5. ctx strategy result dict 都不空
# =====================================================================

class TestCtxResults:
    """Strategy 跑完后, ctx 上的 5 个 result dict 都有值"""

    @pytest.mark.integration
    @pytest.mark.parametrize("strategy_name", [
        "chan", "wyckoff", "structure",
    ])
    def test_raw_strategy_not_empty(self, analysis_data_per_code, strategy_name):
        """2026-08-29: ctx.*_result 字段已删, 改读 raw[strategy_name]"""
        for code, data in analysis_data_per_code.items():
            d = data["engine_result"].raw.get(strategy_name) or {}
            assert isinstance(d, dict), f"{code}: raw[{strategy_name!r}] 不是 dict"
            assert len(d) > 0, f"{code}: raw[{strategy_name!r}] 空 dict"


# =====================================================================
# 6. scene 判定 + total_score 合理
# =====================================================================

class TestSceneAndScore:
    """AnalysisResult 顶层字段 invariant"""

    @pytest.mark.integration
    def test_scene_deleted(self, analysis_data_per_code):
        """2026-08-29: scene/scene_name/resonance_count 已删 (硬编码 if-else 不准)"""
        for code, data in analysis_data_per_code.items():
            ar = data["engine_result"]
            assert not hasattr(ar, "scene"), f"{code}: scene 字段应已删除"
            assert not hasattr(ar, "scene_name"), f"{code}: scene_name 字段应已删除"
            assert not hasattr(ar, "resonance_count"), f"{code}: resonance_count 应已删除"
            # 必有字段
            assert hasattr(ar, "code")
            assert hasattr(ar, "raw")
            assert hasattr(ar, "signals_active")
            assert hasattr(ar, "action")

    @pytest.mark.integration
    def test_signals_active_is_list(self, analysis_data_per_code):
        """2026-08-29: signals_active 替代 scene, 必须是 list"""
        for code, data in analysis_data_per_code.items():
            ar = data["engine_result"]
            assert isinstance(ar.signals_active, list), f"{code}: signals_active 应是 list"
            # 应从 raw 聚合了 strategy 的 signals
            for k, d in (ar.raw or {}).items():
                if isinstance(d, dict) and d.get("signals"):
                    expected = [f"{k}: {s}" for s in d["signals"]]
                    assert all(s in ar.signals_active for s in expected), (
                        f"{code}: raw[{k}].signals 未聚合到 signals_active"
                    )

    @pytest.mark.integration
    def test_action_is_str(self, analysis_data_per_code):
        """action 字段存在, 是字符串 (2026-08-29: 不再硬编码, 默认空字符串, 由 LLM 在 chat 里判)"""
        for code, data in analysis_data_per_code.items():
            action = data["engine_result"].action
            assert isinstance(action, str), f"{code}: action 应是 str, 实际 {type(action)}"


# =====================================================================
# 7. to_dict() 顶层输出: 已删维度不应复活
# =====================================================================

class TestAnalysisDict:
    """AnalysisResult.to_dict() 输出的 analysis dict 字段完整性"""

    @pytest.mark.integration
    def test_to_dict_has_no_deleted_dimensions(self, analysis_data_per_code):
        """已删除维度 (obv 2026-10-27 / fflow 2026-10-28) 不应出现在 to_dict 顶层"""
        for code, data in analysis_data_per_code.items():
            d = data["engine_result"].to_dict(data["ctx"])
            # 2026-10-27: OBV strategy 已删除, raw 不应有 obv
            assert "obv" not in d, f"{code}: to_dict() 不应有 obv 字段"
            # 2026-10-28: 主力资金流维度整体删除
            assert "fflow" not in d, f"{code}: to_dict() 不应有 fflow 字段"
            assert "moneyflow" not in d, f"{code}: to_dict() 不应有 moneyflow 字段"

    @pytest.mark.integration
    def test_to_dict_verdict_matches_raw(self, analysis_data_per_code):
        """【防 regression】to_dict() chan.verdict 必须跟 raw['chan'].verdict 一致"""
        for code, data in analysis_data_per_code.items():
            d = data["engine_result"].to_dict(data["ctx"])
            chan_in_dict = d["chan"]
            chan_in_raw = data["engine_result"].raw["chan"]
            assert chan_in_dict.get("verdict") == chan_in_raw.get("verdict"), (
                f"{code}: to_dict.chan.verdict ({chan_in_dict.get('verdict')!r}) "
                f"!= raw['chan'].verdict ({chan_in_raw.get('verdict')!r}) — "
                f"可能 to_dict 提升时丢了 verdict 字段"
            )


# =====================================================================
# 8. Baseline regression: 跟 v0_baseline.json 对比
# =====================================================================

BASELINE_PATH = PROJECT_ROOT / "tests" / "baselines" / "v0_baseline.json"


@pytest.mark.skipif(not BASELINE_PATH.exists(), reason="v0_baseline.json 不存在, 跑 tools/batch/regression_test baseline")
class TestBaselineRegression:
    """跟 v0_baseline.json 对比关键字段, 防止 strategy 偷偷改了输出但没人发现

    baseline 格式: regression_test.py 抽出 8 个关键字段 (不是 dump 顶层字段)
    - wyckoff_stage / wyckoff_confidence
    - hub_low / hub_high
    - peg_真实
    - dcf_l_10
    - 5method (scene + total_score)
    - _elapsed

    baseline 是 regression_test 抽出的, 不是 dump 字段. 完整对比走
    `python3 tools/batch/regression_test.py test`, 那个工具已经覆盖.
    这里只验证: baseline 8 个字段对应到 dump 的位置 (factor.wyckoff.stage 等)
    """

    @pytest.mark.integration
    def test_baseline_file_exists_and_has_codes(self):
        """baseline 存在 + 含 17 只票 (跟 regression_test baseline 模式对应)"""
        if not BASELINE_PATH.exists():
            pytest.skip("v0_baseline.json 不存在, 跑 regression_test baseline")
        baseline = json.load(open(BASELINE_PATH))
        assert len(baseline) == 17, (
            f"baseline 应该有 17 只票, 实际 {len(baseline)} 只"
        )

    @pytest.mark.integration
    def test_baseline_key_fields_accessible_via_analysis(self, analysis_data_per_code):
        """baseline 字段都能从 AnalysisEngine 算出 (说明算子路径映射对)"""
        if not BASELINE_PATH.exists():
            pytest.skip("v0_baseline.json 不存在")
        baseline = json.load(open(BASELINE_PATH))
        # 字段位置映射: baseline key → AnalysisEngine 算子路径
        # (2026-08-29: 改 raw dict 替代 factor_scores 对象)
        # 2026-11-02: 删 peg_真实 (PEG 是假指标, 整档已删); 改用 Magic roc 替代
        field_paths = {
            "wyckoff_stage":    lambda data: data["engine_result"].raw.get("wyckoff", {}).get("stage", "?"),
            "hub_low":          lambda data: data["engine_result"].raw.get("chan", {}).get("daily", {}).get("hub", {}).get("low"),
            "hub_high":         lambda data: data["engine_result"].raw.get("chan", {}).get("daily", {}).get("hub", {}).get("high"),
        }
        for code, data in analysis_data_per_code.items():
            if code not in baseline:
                continue
            for fname, getter in field_paths.items():
                if fname not in baseline[code]:
                    continue
                # 必须能从 analysis 算出 baseline 这个字段 (不能 None)
                val = getter(data)
                assert val is not None, (
                    f"{code}: analysis 算不出 baseline 字段 {fname} "
                    f"(baseline={baseline[code].get(fname)!r})"
                )
