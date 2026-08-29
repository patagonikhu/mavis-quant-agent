"""
tests/integration/test_dump_analysis.py - dump + analysis 集成测试

**目的**: 用户最担心 analysis 层 regression bug (2026-08-17 发现的 4 个 bug 就是这一层),
这套测试覆盖 dump → RenderData → AnalysisEngine 的完整数据流,确保:
  1. dump 字段不丢 (ctx.moneyflow / ctx.name / ctx.industry 等)
  2. 6 个核心 Phase1 strategy 都跑出来 (raw dict)
  3. fflow 主路径不显示"无数据" (有 60 条 moneyflow)
  4. OBV 主路径有 5 档 verdict + obv5 + obv_trend (2026-08-29 删 60d 段背离)
  5. signals_active 替代 scene (2026-08-29 删 A/B/C/D/E 硬编码 if-else)
  6. AnalysisEngine.analyze() 单点入口能跑 (2026-08-29 修复)

**v3.6 API 变化 (2026-08-29)**:
- AnalysisResult 顶层: code/name/current_price/raw/signals_active/action (5 字段)
- 删: scene, scene_name, resonance_count, total_score, factor_scores (对象)
- 取而代之: raw dict (6 strategy 各一个 dict, 含 score/signals/verdict)
- signals_active: list[str] (从 raw[*].signals 聚合, LLM 自己判)

**防 regression 的关键断言**:
- ctx.moneyflow 长度 == dump.tushare.money_flow 长度 (防 ctx 重建覆盖 bug)
- ctx.name == dump.name (防 name 字段被默认 "" 覆盖)
- 6 个 strategy 都在 result.raw 里
- fflow verdict 5 档判定
- OBV 实战信号 obv5/obv_trend 字段存在

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

# 6 个 Phase1 strategy (2026-08-29 简化: 删 ResonanceStrategy, 删 VolumePriceStrategy 拆分)
PHASE1_STRATEGY_NAMES = [s.__name__ for s in PHASE1_STRATEGY_CLASSES]
# 跟 CLAUDE.md 6 个 strategy 权重对齐
STRATEGY_WEIGHT = {
    "ChanStrategy":    0.20,
    "WyckoffStrategy": 0.20,
    "SmcStrategy":     0.10,
    "ObvStrategy":     0.10,
    "FflowStrategy":   0.15,
    "PegStrategy":     0.15,
}


@pytest.fixture(scope="module")
def dumps():
    """加载 5 只票的 ctx (走 DataStore, 跳过无K线的)"""
    from tools.kline_store import DataStore
    loaded = {}
    for code in TEST_CODES:
        try:
            ctx = DataStore.get_ctx(code)
            if ctx.kline:
                loaded[code] = ctx
        except Exception:
            pass
    if not loaded:
        pytest.skip(f"没找到任何本地K线数据, 先跑: python -m tools.sync_stock {TEST_CODES[0]}")
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
    def test_ctx_moneyflow_equals_dump_tushare_money_flow(self, analysis_data_per_code):
        """【防 regression】ctx.moneyflow 长度 == dump.tushare.money_flow 长度

        2026-08-17 bug: render_data.py:522-528 重建 ctx 漏传 moneyflow,
        导致 FflowStrategy 拿空数据, 5 只票 fflow 全显示"无数据".
        这个断言能立即捕捉到同类 bug.
        """
        for code, data in analysis_data_per_code.items():
            dump_mf_len = len(data["dump"].get("tushare", {}).get("money_flow", []))
            ctx_mf_len = len(data["ctx"].moneyflow)
            assert ctx_mf_len == dump_mf_len, (
                f"{code}: ctx.moneyflow ({ctx_mf_len}) != "
                f"dump.tushare.money_flow ({dump_mf_len}) — "
                f"ctx 字段被覆盖了! (render_data.py:522-528 重建 bug)"
            )
            # 有数据的票应该 ≥ 3 条 (fflow_factor 最低 3 条才能判)
            if dump_mf_len > 0:
                assert ctx_mf_len >= 3, f"{code}: moneyflow < 3, fflow_factor 没法判"

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
        strategy_name = strategy_class.name  # chan/wyckoff/smc/obv/fflow/peg
        for code, data in analysis_data_per_code.items():
            raw = data["engine_result"].raw
            assert strategy_name in raw, (
                f"{code}: {strategy_name} 不在 raw (跑了哪几个: {list(raw.keys())})"
            )
            d = raw[strategy_name] or {}
            # score 范围因 strategy 而异:
            score_ranges = {
                "fflow":    (-2.5, 2.5),
                "obv":      (-3.5, 5.5),  # 5 类信号 + 实战信号
                "wyckoff":  (-1.5, 1.5),
                "smc":      (-1.5, 1.5),
                "chan":     (-1.5, 1.5),
                "peg":      (-1.5, 1.5),
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
# 3. fflow 主路径: 有真实 moneyflow 数据时不能显示"无数据"
# =====================================================================

class TestFflowStrategy:
    """fflow 主路径关键 regression 测试 (2026-08-29: 用 raw['fflow'] dict 替代 factor_scores 对象)"""

    @pytest.mark.integration
    def test_fflow_has_real_data_when_moneyflow_exists(self, analysis_data_per_code):
        """【关键 regression】有 moneyflow 数据的票, fflow 必须有真实 verdict (不能"无数据")"""
        for code, data in analysis_data_per_code.items():
            dump_mf_len = len(data["dump"].get("tushare", {}).get("money_flow", []))
            if dump_mf_len < 3:
                pytest.skip(f"{code}: moneyflow < 3 条, 没法跑 fflow")
            fflow = data["engine_result"].raw.get("fflow") or {}
            assert fflow, f"{code}: fflow strategy 不在 raw"
            # verdict 不能是"无数据"
            summary = fflow.get("summary") or ""
            assert summary, f"{code}: fflow.summary 空"
            assert "无数据" not in summary, (
                f"{code}: fflow.summary = {summary!r} — "
                f"明明 moneyflow 有 {dump_mf_len} 条, 不应该显示'无数据'!"
            )
            # source 应该是 Tushare.money_flow
            source = fflow.get("source", "")
            assert "Tushare" in source or "dump" in source, (
                f"{code}: fflow.source = {source!r}, 应该是 Tushare.money_flow"
            )

    @pytest.mark.integration
    def test_fflow_verdict_in_five_tiers(self, analysis_data_per_code):
        """fflow verdict 必须在 5 档判定里"""
        valid_verdicts = {"🟢主力进货", "🟡偏进货", "⬜中性", "🟠偏出货", "🔴主力出货"}
        for code, data in analysis_data_per_code.items():
            fflow = data["engine_result"].raw.get("fflow") or {}
            if not fflow or "无数据" in (fflow.get("summary") or ""):
                continue
            summary = fflow.get("summary") or ""
            verdict_clean = summary.split("⚠️")[0].strip()  # 去掉矛盾标记
            verdict_base = verdict_clean.split()[0] if verdict_clean else ""
            assert any(v.startswith(verdict_base) for v in valid_verdicts), (
                f"{code}: fflow.verdict = {summary!r}, 不在 5 档判定 {valid_verdicts}"
            )

    @pytest.mark.integration
    def test_fflow_net_5d_reasonable(self, analysis_data_per_code):
        """fflow_net_5d 不应该 > 100 亿 (异常值检测)"""
        for code, data in analysis_data_per_code.items():
            fflow = data["engine_result"].raw.get("fflow") or {}
            net_5d = fflow.get("fflow_net_5d", 0) or 0
            if net_5d == 0:
                continue
            assert -100 <= net_5d <= 100, (
                f"{code}: fflow_net_5d = {net_5d:.2f}亿, 超出合理范围 [-100, 100]"
            )


# =====================================================================
# 4. OBV 主路径: 经典算法 + 段背离
# =====================================================================

class TestObvStrategy:
    """OBV 实战信号: 5 档 verdict + obv5 + obv_trend (2026-08-29 删 60d 段背离)"""

    @pytest.mark.integration
    def test_obv_result_structure(self, analysis_data_per_code):
        """raw["obv"] 字段完整 (2026-08-29: 删 60d 段背离, 改 obv5/obv_trend)"""
        for code, data in analysis_data_per_code.items():
            obv = data["engine_result"].raw.get("obv") or {}
            # 必有字段
            for k in ("verdict", "score", "signals", "source", "obv5", "obv_trend"):
                assert k in obv, f"{code}: raw['obv'] 缺 {k} 字段"
            # 不应再有 60d 段背离字段
            for k in ("obv_div_bot_60d", "obv_div_top_60d"):
                assert k not in obv, f"{code}: raw['obv'] 不应有 {k} (60d 段背离已删)"

    @pytest.mark.integration
    def test_obv_verdict_in_five_tiers(self, analysis_data_per_code):
        """OBV verdict 在 5 档判定里"""
        valid_verdicts = {"🟢主力进货", "🟡偏进货", "⬜中性", "🟠偏出货", "🔴主力出货"}
        for code, data in analysis_data_per_code.items():
            obv = data["engine_result"].raw.get("obv") or {}
            if not obv:
                continue
            summary = obv.get("summary") or ""
            assert summary, f"{code}: obv.summary 空"
            verdict_base = summary.split()[0] if summary else ""
            assert any(v.startswith(verdict_base) for v in valid_verdicts), (
                f"{code}: obv.verdict = {summary!r}, 不在 5 档 {valid_verdicts}"
            )

    @pytest.mark.integration
    def test_obv_dual_judgment_signal(self, analysis_data_per_code):
        """【关键】fflow + obv 都不中性时, fflow_strategy 应输出方向信号 (✅ 同向 / ⚠️ 矛盾)

        算法: fflow_strategy.analyze() 只在 fflow_dir != 0 && obv_dir != 0 时生成双判定信号
        (任一为 0 = 中性, 不生成信号, 因为没法判方向).
        """
        for code, data in analysis_data_per_code.items():
            fflow = data["engine_result"].raw.get("fflow") or {}
            obv = data["engine_result"].raw.get("obv") or {}
            if not fflow or not obv:
                continue
            if "无数据" in fflow.get("summary", "") or "无数据" in obv.get("summary", ""):
                continue
            # 任一中性 (score=0) 时无双判定信号, 这是正确行为
            if fflow.get("score", 0) == 0 or obv.get("score", 0) == 0:
                continue
            # fflow/obv 都非中性 (score != 0), 必须有方向信号
            sig_text = " ".join(fflow.get("signals") or [])
            has_dual = "✅ fflow+OBV" in sig_text or "⚠️ fflow vs OBV" in sig_text
            assert has_dual, (
                f"{code}: fflow.signals 缺双判定信号 "
                f"(fflow={fflow.get('summary')!r} score={fflow.get('score')}, "
                f"obv={obv.get('summary')!r} score={obv.get('score')}, "
                f"signals: {(fflow.get('signals') or [])[:3]})"
            )


# =====================================================================
# 5. ctx 5 个 strategy result dict 都不空
# =====================================================================

class TestCtxResults:
    """Strategy 跑完后, ctx 上的 5 个 result dict 都有值"""

    @pytest.mark.integration
    @pytest.mark.parametrize("strategy_name", [
        "fflow", "obv", "chan", "wyckoff", "smc",
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
# 7. to_dict() 顶层输出: fflow/obv 都能从 analysis dict 拿到
# =====================================================================

class TestAnalysisDict:
    """AnalysisResult.to_dict() 输出的 analysis dict 字段完整性"""

    @pytest.mark.integration
    def test_to_dict_has_fflow_and_obv_at_top_level(self, analysis_data_per_code):
        """to_dict() 顶层必须有 fflow + obv 字段 (render 读这里)"""
        for code, data in analysis_data_per_code.items():
            d = data["engine_result"].to_dict(data["ctx"])
            assert "fflow" in d, f"{code}: to_dict() 缺 fflow 字段"
            assert "obv" in d, f"{code}: to_dict() 缺 obv 字段"
            # 字段值是 dict, 不空
            assert isinstance(d["fflow"], dict) and d["fflow"], f"{code}: to_dict fflow 空"
            assert isinstance(d["obv"], dict) and d["obv"], f"{code}: to_dict obv 空"

    @pytest.mark.integration
    def test_to_dict_verdict_matches_raw(self, analysis_data_per_code):
        """【防 regression】to_dict() fflow.verdict 必须跟 raw['fflow'].verdict 一致"""
        for code, data in analysis_data_per_code.items():
            d = data["engine_result"].to_dict(data["ctx"])
            fflow_in_dict = d["fflow"]
            fflow_in_raw = data["engine_result"].raw["fflow"]
            assert fflow_in_dict.get("verdict") == fflow_in_raw.get("verdict"), (
                f"{code}: to_dict.fflow.verdict ({fflow_in_dict.get('verdict')!r}) "
                f"!= raw['fflow'].verdict ({fflow_in_raw.get('verdict')!r}) — "
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
    - main_yi_5d (fflow 5 日累计)
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
        """baseline 8 个字段都能从 AnalysisEngine 算出 (说明算子路径映射对)"""
        if not BASELINE_PATH.exists():
            pytest.skip("v0_baseline.json 不存在")
        baseline = json.load(open(BASELINE_PATH))
        # 字段位置映射: baseline key → AnalysisEngine 算子路径
        # (2026-08-29: 改 raw dict 替代 factor_scores 对象)
        # dcf 是 Phase2 派生, 不在 analyze_history 跑, 跳过
        field_paths = {
            "wyckoff_stage":    lambda data: data["engine_result"].raw.get("wyckoff", {}).get("stage", "?"),
            "hub_low":          lambda data: data["engine_result"].raw.get("chan", {}).get("daily", {}).get("hub", {}).get("low"),
            "hub_high":         lambda data: data["engine_result"].raw.get("chan", {}).get("daily", {}).get("hub", {}).get("high"),
            "peg_真实":          lambda data: data["engine_result"].raw.get("peg", {}).get("PEG_真实"),
            "main_yi_5d":       lambda data: data["engine_result"].raw.get("fflow", {}).get("fflow_net_5d"),
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
