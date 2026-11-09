"""
tools/batch/blowout_markup_screen.py — blowout 候选池的威科夫 M 阶段筛选 (2026-11-09)

背景:
  `/t-analyze --all` 原来只跑 持仓 + 自选 两段 (blowout 候选池整段排除, 2026-11-06)。
  用户要求: blowout 池里**当前威科夫阶段是 M (Markup)** 的票也要出详报,
  落到 `docs/blowout/` 目录。

口径 (跟 /t-analyze 详报 100% 同源, **不自己重算 stage**):
  - 判定值 = 报告「因子历史走势」表最后一行的 `wyckoff_daily`
    → 生产侧真源: `AnalysisEngine.analyze_history(ctx, [last_date])`
      → `result.raw["wyckoff"]["3period"]["daily"]["stage"]`
  - `analyze_history` 对**给定日期**的判定跟同批其它日期无关 (sub_events 预扫一次 +
    per-date O(1) judge), 所以这里只算最后一天 = 报告末行 = 同一个值, 但省掉 120 天历史开销。
  - 主阶段 = **日线** (analysis_engine.py: "主阶段用日线") —— 跟 a2m 扫描器 ① 同口径。
  - 只跑 WyckoffStrategy (不跑 technical / finance): 威科夫判定与其它 strategy 相互独立,
    单独跑得到的 3period 与全 engine 跑**逐字节相同**。

用法:
    from tools.batch.blowout_markup_screen import screen_markup_stocks
    hits = screen_markup_stocks(["002149", "300613"], workers=4)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from tools.analysis.analysis_engine import AnalysisEngine, WyckoffStrategy
from tools.storage.store import DataStore


def _stage_of(code: str) -> dict | None:
    """单只票的当前威科夫阶段 (日线主阶段 + 周线参考)

    Returns:
        {"code", "stage_daily", "stage_weekly", "current_price"} | None (无 K 线)
        出错时返回 {"code", "error": "..."} —— 调用方决定要不要跳过
    """
    try:
        ctx = DataStore.get_ctx(code)
    except Exception as e:
        return {"code": code, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    if not ctx.kline:
        return None

    last_date = ctx.kline[-1]["trade_date"].replace("-", "")[:8]
    try:
        # 只跑威科夫 strategy (独立于 technical / finance, 结果与全 engine 相同)
        history = AnalysisEngine(strategies=[WyckoffStrategy]).analyze_history(ctx, [last_date])
    except Exception as e:
        return {"code": code, "error": f"{type(e).__name__}: {str(e)[:120]}"}

    result = history.get(last_date)
    if result is None:
        return {"code": code, "error": "末根无判定结果"}

    period = (result.raw.get("wyckoff") or {}).get("3period") or {}
    return {
        "code": code,
        "stage_daily": (period.get("daily") or {}).get("stage", "?"),
        "stage_weekly": (period.get("weekly") or {}).get("stage", "?"),
        "current_price": ctx.current_price,
    }


def screen_markup_stocks(codes: list[str], workers: int = 4) -> list[dict]:
    """筛出当前威科夫**日线**阶段 == Markup 的票

    Args:
        codes:   6 位股票代码列表
        workers: 并发线程数 (duckdb 用线程局部连接, 线程安全)

    Returns:
        命中列表, 每项 {"code", "stage_daily", "stage_weekly", "current_price"},
        按 code 升序。未命中 / 无数据的票不返回。
    """
    hits = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for res in ex.map(_stage_of, codes):
            if not res:
                continue
            if res.get("error"):
                continue
            if res.get("stage_daily") == "Markup":
                hits.append(res)
    hits.sort(key=lambda r: r["code"])
    return hits
