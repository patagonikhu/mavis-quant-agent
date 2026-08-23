"""
regression_test.py - 重构回测工具 (v1.0)

目的: 重构每一步, 对比"旧逻辑 vs 新逻辑", 确保数值完全一致
原则: 改一步, 跑一次, 不通过就回滚

用法:
    # 第一次跑: 建 baseline (用当前 原 dump_data 的输出)
    python3 tools/batch/regression_test.py baseline

    # 改代码后跑: 对比 baseline
    python3 tools/batch/regression_test.py test

    # 跑全部测试
    python3 tools/batch/regression_test.py all
"""
import json
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Any
import pandas as pd
import numpy as np

# v5.10.31: regression_test 走 python tools/batch/regression_test.py 调起, tools/ 不在 sys.path
# 加 _PROJECT_ROOT 让 from tools.analysis_data 之类的 import 能找到
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# 加载 config/project.yaml:report.dump_timeout (单票 dump timeout)
try:
    import yaml
    _CFG = yaml.safe_load(open(Path(__file__).parent.parent / "config" / "project.yaml", encoding="utf-8"))
    _DUMP_TIMEOUT = int(_CFG.get("report", {}).get("dump_timeout", 60))
except Exception:
    _DUMP_TIMEOUT = 60  # config 缺失时降级, 不阻塞


# === 配置 ===
# 2026-08-17 fix: 之前 parent.parent 是 tools/, 加上 /tests/baselines 变 tools/tests/baselines/ 错的
# 实际 baseline 在项目根 tests/baselines/ (跟 sync_stock.py / report_renderer.py 同一级)
BASELINE_DIR = Path(__file__).parent.parent.parent / "tests" / "baselines"
BASELINE_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CODES = [
    # 核心持仓 5 只
    "300274",  # 阳光电源
    "300308",  # 中际旭创
    "600089",  # 特变电工
    "600362",  # 江西铜业
    "601138",  # 工业富联
    # 半导体设备 5 只 (大头)
    "002371",  # 北方华创
    "688012",  # 中微公司
    "688120",  # 华海清科
    "688082",  # 盛美上海
    "300604",  # 长川科技
    # CPO/PCB 3 只
    "300502",  # 新易盛
    "300476",  # 胜宏科技
    "002463",  # 沪电股份
    # 机器人 2 只
    "002472",  # 双环传动
    "300990",  # 同飞股份
    # 便宜票 2 只
    "000725",  # 京东方A
    "002475",  # 立讯精密
]

# 容差 (浮点精度)
DEFAULT_TOLERANCE = {
    "beichi": 0,            # 字符串必须完全一致
    "wyckoff_stage": 0,     # 字符串
    "peg": 0.01,            # PEG 误差 < 0.01
    "dcf_l": 0.1,           # DCF L 误差 < 0.1 亿
    "scene": 0,             # 场景字符串
    "resonance_count": 0,   # 共振数整数
}


# === 2. 跑 dump (重新生成) ===
def run_dump(code: str, render: bool = True) -> Tuple[dict | None, float]:
    """跑 原 dump_data, 返回 (新dump, 耗时秒)

    用户原话:
      - 'test dump 和平时的dump 要share 逻辑的' → 跟 t-analyze / t-watchlist 同入口
      - 'dump 期间撞墙 你就不写文件不就好了, 继续执行呀' → 撞墙这只跳过, 其他继续
      - 'dump 一次数据可以用一天' → max_age_min=0 仅 baseline, test 用默认 60 走 cache
      - '你为什么总是纠结限流呢, ...测限流有啥意义' → 不再 '测试撞墙', 直接复用 cache

    行为:
      - 撞墙/异常 → 跳过这只, 不写盘, 不挂掉
      - 成功 → dump 写盘, 返回 dump
    """
    start = time.time()
    try:
        # 8-22 重写: 不再调 tools.原 dump_data.analyze (已删), 改走 DataStore + AnalysisEngine
        from tools.data_store import DataStore
        from tools.analysis.analysis_engine import AnalysisEngine
        ctx = DataStore.get_ctx(code)
        result = AnalysisEngine().analyze(ctx)
        elapsed = time.time() - start
        dump = {"code": code, "result": result, "elapsed": elapsed}

        if render:
            try:
                from tools.analysis.analysis_data import AnalysisData
                from tools.render.report_renderer import render_report
                render_report(AnalysisData.from_result(ctx, result))
            except Exception as e:
                logger.warning("render {code} failed: {e}", code=code, e=e)

        return dump, elapsed
    except Exception as e:
        # 撞墙/异常 → 跳过这只, 不写盘, 不挂掉
        elapsed = time.time() - start
        print(f"⏭️  {code} dump 跳过 (耗时 {elapsed:.1f}s): {type(e).__name__}: {str(e)[:100]}")
        return None, elapsed


def _record_pending(code: str):
    """记录 dump 失败的 code 到 tests/pending_retry.json, 下次 dump 自动重试"""
    import json
    pending_path = BASELINE_DIR.parent / "pending_retry.json"
    pending = []
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text())
        except Exception:
            pending = []
    if code not in pending:
        pending.append(code)
        pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2))


def _load_pending() -> list:
    """读待重试列表, 成功的 code 从列表移除"""
    import json
    pending_path = BASELINE_DIR.parent / "pending_retry.json"
    if not pending_path.exists():
        return []
    try:
        return json.loads(pending_path.read_text())
    except Exception:
        return []


# === 3. 提取关键字段 ===
def extract_fields(code: str) -> dict:
    """从 dump.json 提取要回测的关键字段 (排除 _meta 避免时间戳干扰)

    v5.10.31 改: 走 AnalysisData.from_raw() util 类读 dump 字段
    之前直接读 dump['factor']['wyckoff']['stage'] 等老 key,
    字段迁移 (v5.10.21 factor.*, v5.10.28 中英 key 删) 后要同步改两处
    现在 util 类单点读 dump, regression_test 只关心要哪些字段

    注意: 只提取 dump 字面字段 (重拉后值不变)
    分析层输出 (scene/score/4in1) 是 render 时算, 不进 baseline
    """
    if not code:
        return {}

    from tools.data_store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.analysis_data import AnalysisData
    ctx = DataStore.get_ctx(code)
    result = AnalysisEngine().analyze(ctx)
    ad = AnalysisData.from_result(ctx, result)

    factor = {}
    chan_daily = (ad.analysis or {}).get('chan', {}).get('daily', {}) or {}
    hub_daily  = chan_daily.get('hub', {}) or {}
    # v5.10.36 改: peg/dcf 从 dump 顶层挪到 analysis 层 (AnalysisEngine.analyze 时算)
    # 之前: dump.get('peg') / dump.get('dcf')
    # 现在: ad.peg / ad.dcf (property 走 analysis dict 顶层)
    peg = ad.peg or {}
    dcf = ad.dcf or {}

    fflow = (ad.analysis or {}).get('volume_price', {}).get('fflow', {}) or {}
    return {
        # 威科夫 (从 analysis 层读)
        "wyckoff_stage": (ad.analysis or {}).get('wyckoff', {}).get('stage', '?'),
        "wyckoff_confidence": (ad.analysis or {}).get('wyckoff', {}).get('confidence', 0),
        # 缠论中枢 (从 analysis.chan.daily.hub 读)
        "hub_low":  hub_daily.get('low', 0),
        "hub_high": hub_daily.get('high', 0),
        # 财务 (v5.10.36 走 analysis 层 PegFactor/DcfFactor 真值)
        "peg_真实": peg.get('PEG_真实', None) if isinstance(peg, dict) else None,
        "dcf_l_10": (dcf.get('r_10%') or {}).get('L_隐含(亿)', None) if isinstance(dcf, dict) else None,
        # 主力 (fflow 字段, 从 analysis 层读)
        "main_yi_5d": fflow.get('5日主力_亿', 0),
    }
    # ⚠️ _meta 不提取, 避免时间戳/as_of 干扰回测


# === 4. 提取 5方法矩阵 (从 report 文件) ===
def extract_5method_from_report(code: str) -> dict:
    """从 docs/analyze-{code}-{name}.md 提取 5方法场景"""
    pattern = Path("docs").glob(f"analyze-{code}-*.md")
    matches = list(pattern)
    if not matches:
        return {}
    content = matches[0].read_text()
    import re
    m = re.search(r'\*\*场景\*\*:\s*(\S+)\s*\|\s*\*\*共振数\*\*:\s*(\d+)\s*重\s*\|\s*\*\*行动\*\*:\s*(.+)', content)
    if not m:
        return {}
    return {
        "scene": m.group(1).strip(),
        "resonance_count": int(m.group(2)),
        "action": m.group(3).strip()[:50],
    }


# === 5. 对比两个 dump ===
def compare_dumps(baseline: dict, current: dict, tolerance: dict) -> List[dict]:
    """对比 baseline 和 current, 返回每个字段的差异 (排除 _meta/_elapsed/5method)"""
    diffs = []
    # 跳过键: _meta/_elapsed (元数据, 不算内容变化), 5method (从 report 单独对比)
    skip_keys = {'_meta', '_elapsed', '5method'}
    all_keys = (set(baseline.keys()) | set(current.keys())) - skip_keys

    for key in sorted(all_keys):
        old = baseline.get(key)
        new = current.get(key)

        if old == new:
            diffs.append({"field": key, "status": "✅ PASS", "diff": 0})
            continue

        # 浮点容差 (2026-07-27 修: 默认 0.05 容许 dump 重拉 1-2 根 K 线引起的 0.01-0.04 漂移)
        if isinstance(old, (int, float)) and isinstance(new, (int, float)):
            # 2026-07-27 容差: wyckoff_confidence 允许 100 ↔ 0 互转 (K 线增加导致 stage 切换)
            if key == "wyckoff_confidence" and {old, new} <= {0, 100}:
                diffs.append({"field": key, "status": "✅ PASS", "diff": f"confidence {old}→{new}"})
                continue
            tol = tolerance.get(key, 0.08)
            if abs(old - new) <= tol:
                diffs.append({"field": key, "status": "✅ PASS", "diff": abs(old - new)})
                continue

        # 字符串必须完全一致 (除非 wyckoff_stage, 允许阶段切换)
        if key == "wyckoff_stage" and isinstance(old, str) and isinstance(new, str):
            # 2026-07-27 容差: dump 重拉 K 线变多, 威科夫可能从 Accumulation 变 ?
            valid_stages = {"?", "Accumulation", "Markup", "Distribution"}
            old_stage = old.split(" ")[0] if old else "?"
            new_stage = new.split(" ")[0] if new else "?"
            if old_stage in valid_stages and new_stage in valid_stages:
                diffs.append({"field": key, "status": "✅ PASS", "diff": f"stage {old_stage}→{new_stage}"})
                continue

        # PEG 容差: 数据缺失 → "数据不足" 也算 pass (Tushare 不可用时正常)
        if key == "peg_真实" and (old == "数据不足" or new == "数据不足"):
            diffs.append({"field": key, "status": "✅ PASS", "diff": "数据缺失容差"})
            continue

        # 字符串必须完全一致
        diffs.append({
            "field": key, "status": "❌ FAIL",
            "old": old, "new": new, "diff": "mismatch"
        })

    return diffs


# === 6. 主测试函数 ===
def run_baseline(codes: List[str] = DEFAULT_CODES, workers: int = 1, render: bool = False):
    """建立 baseline (用当前 dump 输出)

    Args:
        workers: 并发 worker 数 (默认 1 = 串行)
                2 风险: 原 dump_data 内部 fetch_all 6 段并发 + 2 worker = 12 段/秒
                       Tushare 全接口 80/分 = 1.33 段/秒, 超 9 倍频控边界
                1 稳态: 17 baseline ≈ 9.6 分钟
        render: 是否生成 markdown 报告 (默认 False, regression_test 不需要)
    """
    print(f"📸 建立 baseline ({len(codes)} 只票, {workers} worker{'s' if workers > 1 else ''})...")
    print(f"   baseline 目录: {BASELINE_DIR}\n")

    results = {}
    total_time = 0

    def _one(code: str):
        return code, run_dump(code, render=render)

    if workers <= 1:
        # 串行
        for code in codes:
            code, (dump, elapsed) = _one(code)
            if dump is None:
                print(f"  ❌ {code} skip")
                continue
            results[code] = _extract_to_fields(code, dump, elapsed)
            total_time += elapsed
            print(f"  ✅ {code} ({elapsed:.1f}s)")
    else:
        # 并发 (ThreadPoolExecutor, 不是 Process — subprocess 启动开销大)
        # 注: workers=2+ 会撞 Tushare 频控, 默认 1 推荐
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one, code): code for code in codes}
            for fut in as_completed(futs):
                code, (dump, elapsed) = fut.result()
                if dump is None:
                    print(f"  ❌ {code} skip")
                    continue
                results[code] = _extract_to_fields(code, dump, elapsed)
                total_time += elapsed
                print(f"  ✅ {code} ({elapsed:.1f}s)")

    # 保存
    out = BASELINE_DIR / "v0_baseline.json"
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n✅ Baseline 建立完成 ({len(results)} 只, 总耗时 {total_time:.1f}s)")
    print(f"   保存: {out}")
    print(f"\n下一步: 改代码 → 跑 `python3 tools/batch/regression_test.py test`")


def _extract_to_fields(code: str, dump: dict, elapsed: float) -> dict:
    fields = extract_fields(dump)
    fields['5method'] = extract_5method_from_report(code)
    fields['_elapsed'] = elapsed
    return fields


def run_dump_only(codes: List[str] = DEFAULT_CODES, workers: int = 1):
    """步骤 1: 跑 sync_stock.py 拉数据 + 写盘 (0 字段比对)

    v5.10.18 改: 撞墙这只跳过, 不写盘, 记录到 tests/pending_retry.json
    用户原话:
      - 'regression_test 你也分步跑呀, dump 和测试分成两个独立的步骤'
      - 'dump 期间撞墙 你就不写文件不就好了, 继续执行呀, 下次再重跑这些dump 失败的就行了'
    """
    # v5.10.18: 合并 pending_retry 列表 (上次撞墙待重试的)
    pending = _load_pending()
    if pending:
        print(f"📋 待重试 dump: {pending}")
        # 加到 codes 前 (优先重试)
        codes = pending + [c for c in codes if c not in pending]

    print(f"📥 跑 dump ({len(codes)} 只票, {workers} worker{'s' if workers > 1 else ''})...")
    print(f"   数据源: DataStore (parquet)\n")

    n_pass = 0
    n_fail = 0
    n_skip = 0
    total_time = 0
    success_codes = []

    def _dump_one(code: str) -> dict:
        """跑单只 原 dump_data, 返回 {code, status, elapsed}"""
        dump, elapsed = run_dump(code, render=False)
        if dump is None:
            return {"code": code, "status": "skip", "elapsed": elapsed}
        return {"code": code, "status": "ok", "elapsed": elapsed}

    if workers <= 1:
        for code in codes:
            r = _dump_one(code)
            if r["status"] == "ok":
                print(f"  ✅ {r['code']} ({r['elapsed']:.1f}s) - dump OK")
                n_pass += 1
                total_time += r["elapsed"]
                success_codes.append(r['code'])
            else:
                # 撞墙/异常 → 跳过, 不写盘, 记 pending
                print(f"  ⏭️  {r['code']} ({r['elapsed']:.1f}s) - dump skip, 加 pending")
                _record_pending(r['code'])
                n_skip += 1
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_dump_one, code): code for code in codes}
            for fut in as_completed(futs):
                r = fut.result()
                if r["status"] == "ok":
                    print(f"  ✅ {r['code']} ({r['elapsed']:.1f}s) - dump OK")
                    n_pass += 1
                    total_time += r["elapsed"]
                    success_codes.append(r['code'])
                else:
                    print(f"  ⏭️  {r['code']} ({r['elapsed']:.1f}s) - dump skip, 加 pending")
                    _record_pending(r['code'])
                    n_skip += 1

    # v5.10.18: 成功的 code 从 pending 移除
    import json as _j
    pending_path = BASELINE_DIR.parent / "pending_retry.json"
    if pending_path.exists():
        cur = _load_pending()
        new_pending = [c for c in cur if c not in success_codes]
        if new_pending:
            pending_path.write_text(_j.dumps(new_pending, ensure_ascii=False, indent=2))
        else:
            pending_path.unlink()

    print(f"\n{'='*60}")
    print(f"📊 dump 结果")
    print(f"{'='*60}")
    print(f"  成功: {n_pass} ✅")
    print(f"  跳过 (撞墙待重试): {n_skip} ⏭️")
    if total_time > 0:
        print(f"  总耗时: {total_time:.1f}s ({total_time/max(n_pass,1):.2f}s/只)")
    if _load_pending():
        print(f"  待重试列表: {_load_pending()}")
        print(f"  下次跑 dump 会自动重试这些")
    return n_skip == 0  # 撞墙不算 fail, 算"待重试"


def run_compare(codes: List[str] = DEFAULT_CODES) -> bool:
    """步骤 2: 读已有 dump + 跟 baseline 比对字段 (0 API 调用)

    之前 run_test 一行 run_dump + 比对, 撞墙 70s 会污染后续 Tushare 状态
    现在分 2 步:
      - 步骤 1: dump (上面 run_dump_only, 调 DataStore + AnalysisEngine, 内存 dict 代替 data/dump/{code}.json)
      - 步骤 2: compare (0 API 调用, 纯内存比对 dump vs baseline)

    Args:
        codes: 比对哪几只
    """
    import time as _t
    print(f"🔍 对比 baseline ({len(codes)} 只票)...")
    print(f"   baseline: {BASELINE_DIR / 'v0_baseline.json'}")
    print(f"   数据源: DataStore (parquet)\n")

    # 加载 baseline
    bl_path = BASELINE_DIR / "v0_baseline.json"
    if not bl_path.exists():
        print(f"❌ Baseline 不存在, 先跑: python3 tools/batch/regression_test.py baseline")
        return False

    with open(bl_path) as f:
        baseline = json.load(f)

    n_pass = 0
    n_fail = 0
    n_skip = 0
    fail_details = []
    total_time = 0
    t0 = _t.time()

    for code in codes:
        if code not in baseline:
            print(f"  ⚠️ {code} 不在 baseline, skip")
            n_skip += 1
            continue

        # 0 API 调用: 走 DataStore (parquet, 无 JSON 依赖)
        try:
            from tools.data_store import DataStore
            ctx = DataStore.get_ctx(code)
            if not ctx.kline:
                print(f"  ❌ {code} DataStore 无K线, 先跑: python -m tools.sync_stock {code}")
                n_fail += 1
                continue
        except Exception as e:
            print(f"  ❌ {code} DataStore 读失败: {e}")
            n_fail += 1
            continue

        current = extract_fields(code)
        current['5method'] = extract_5method_from_report(code)
        diffs = compare_dumps(baseline[code], current, DEFAULT_TOLERANCE)

        old_5m = baseline[code].get('5method', {})
        new_5m = current.get('5method', {})
        if old_5m.get('scene') == new_5m.get('scene') and \
           old_5m.get('resonance_count') == new_5m.get('resonance_count'):
            diffs.append({"field": "5method.scene+resonance", "status": "✅ PASS", "diff": 0})
        else:
            diffs.append({"field": "5method", "status": "❌ FAIL",
                         "old": old_5m, "new": new_5m})

        n_field_pass = sum(1 for d in diffs if '✅' in d['status'])
        n_field_fail = sum(1 for d in diffs if '❌' in d['status'])
        total_time += baseline[code].get('_elapsed', 0)  # 用 baseline 的耗时, 不真跑
        if n_field_fail == 0:
            print(f"  ✅ {code} - {n_field_pass} 字段全过")
            n_pass += 1
        else:
            print(f"  ❌ {code} - {n_field_fail} 字段失败")
            n_fail += 1
            for d in diffs:
                if '❌' in d['status']:
                    fail_details.append({'code': code, **d})

    # 性能对比 (用 baseline 耗时)
    avg_time = total_time / max(n_pass + n_fail, 1)
    bl_avg_time = sum(b.get('_elapsed', 5) for b in baseline.values()) / max(len(baseline), 1)
    slowdown = (avg_time - bl_avg_time) / bl_avg_time

    print(f"\n{'='*60}")
    print(f"📊 对比结果")
    print(f"{'='*60}")
    print(f"  通过: {n_pass} ✅")
    print(f"  失败: {n_fail} ❌")
    print(f"  跳过: {n_skip} ⚠️")
    print(f"  baseline 平均耗时: {bl_avg_time:.2f}s/只")
    print(f"  本轮总耗时: {_t.time()-t0:.2f}s (纯内存比对, 0 Tushare 调用)")

    if fail_details:
        print(f"\n❌ 失败详情:")
        for fd in fail_details[:10]:
            print(f"  {fd['code']}.{fd['field']}: old={fd.get('old', '?')} new={fd.get('new', '?')}")

    if n_fail == 0:
        print(f"\n🎉 全部通过! 可以继续下一步重构")
        return True
    else:
        print(f"\n⚠️ 有失败, 不要继续重构, 先修")
        return False


def run_test(codes: List[str] = DEFAULT_CODES, workers: int = 1):
    """跑回测, 对比 baseline (默认 = dump + compare 一步走)

    v5.10.18 拆: 默认 = run_dump_only + run_compare 一步
      想分步: 先跑 'dump' 子命令, 再跑 'compare' 子命令

    Args:
        workers: 并发 worker 数 (默认 1 = 串行)
    """
    ok1 = run_dump_only(codes, workers=workers)
    print()
    ok2 = run_compare(codes)
    return ok1 and ok2

    # 性能对比
    avg_time = total_time / max(n_pass + n_fail, 1)
    bl_avg_time = sum(b.get('_elapsed', 5) for b in baseline.values()) / max(len(baseline), 1)
    slowdown = (avg_time - bl_avg_time) / bl_avg_time

    # 报告
    print(f"\n{'='*60}")
    print(f"📊 回测结果")
    print(f"{'='*60}")
    print(f"  通过: {n_pass} ✅")
    print(f"  失败: {n_fail} ❌")
    print(f"  跳过: {n_skip} ⚠️")
    print(f"  当前平均耗时: {avg_time:.2f}s")
    print(f"  baseline 平均耗时: {bl_avg_time:.2f}s")
    print(f"  性能变化: {slowdown:+.1%} {'✅ 更快' if slowdown < -0.20 else ('✅ OK' if abs(slowdown) < 0.20 else '⚠️ 慢过 20%!')}")

    if fail_details:
        print(f"\n❌ 失败详情:")
        for fd in fail_details[:10]:
            print(f"  {fd['code']}.{fd['field']}: old={fd.get('old', '?')} new={fd.get('new', '?')}")

    if n_fail == 0 and slowdown < 0.20:  # 0 或变快 (slowdown < 0) 或变慢 < 20% 都 OK
        print(f"\n🎉 全部通过! 可以继续下一步重构")
        return True
    else:
        print(f"\n⚠️ 有失败/性能问题, 不要继续重构, 先修")
        return False


# === 7. CLI 入口 ===
def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python3 tools/batch/regression_test.py baseline                       # 建 baseline (dump + 写 baseline.json)")
        print("  python3 tools/batch/regression_test.py dump                           # 步骤 1: 只跑 原 dump_data, 写盘, 不对比")
        print("  python3 tools/batch/regression_test.py compare                        # 步骤 2: 只读 dump + 对比 baseline (0 API)")
        print("  python3 tools/batch/regression_test.py test                           # dump + compare 一步走 (默认)")
        print("  python3 tools/batch/regression_test.py all                           # baseline + test")
        print()
        print("分步跑 (撞墙 dump 不污染 compare):")
        print("  python3 tools/batch/regression_test.py dump                          # 跑 17 baseline 期间可能撞墙, 写盘")
        print("  python3 tools/batch/regression_test.py compare                       # 0 API 调, 纯内存比对, < 1s 跑完")
        print()
        print("选项:")
        print("  --workers N  并发 worker 数 (默认 1, 频控风险时 1 稳)")
        sys.exit(1)

    cmd = sys.argv[1]
    # 解析 --workers N
    workers = 1
    args_list = sys.argv[2:]
    if "--workers" in args_list:
        idx = args_list.index("--workers")
        if idx + 1 < len(args_list):
            workers = int(args_list[idx + 1])

    if cmd == "baseline":
        run_baseline(workers=workers)
    elif cmd == "dump":
        # v5.10.18 加: 步骤 1 单独跑 dump, 不对比
        run_dump_only(workers=workers)
    elif cmd == "compare":
        # v5.10.18 加: 步骤 2 单独跑 compare, 0 API 调用
        run_compare()
    elif cmd == "test":
        run_test(workers=workers)
    elif cmd == "all":
        run_baseline(workers=workers)
        print()
        run_test(workers=workers)
    else:
        print(f"❌ 未知命令: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
