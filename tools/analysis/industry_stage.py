"""
tools/analysis/industry_stage.py — 行业威科夫 stage (2026-11-11 加)

给「因子历史走势」表的每一行补上**所在行业**的威科夫信息 (跟个股一样的 3 列)。

复用度 (已实测):
  行业指数 K 线 (OHLCV) → 拼 RawContext → **直接调** AnalysisEngine().analyze_history()
  → 跟个股**同一套** WyckoffStrategy, **零改动**。
  耗时: 120 日 0.28s / 全历史 1696 日 0.24s。

3 个必须记住的坑:
  1. **vol → volume**: store.get_sw_index_bars 已在 DAO 层做别名 (跟个股同口径);
     不转的话威科夫读到的成交量恒为 0, 量能类判定全废 (静默出错!)
  2. **行业映射**: stock_basic.industry 已删 (申万**旧版** 110 个, 跟 SW2021 对不上)
     → 用 index_member_all 落的 industry_member.parquet (唯一可靠来源)
  3. **cache 有效性**: 判定是因果的 (build_kline_features 全回看型滚动窗口)
     → 某日结果只依赖该日之前的数据 → 可按 (行业, 日期) 永久缓存, 新数据不影响旧行

架构 (规则下沉):
  本模块 = 生产侧, 算好 stage/strength/channel/sub_event 塞进行数据
  渲染器只读不算 (渲染器不 import 本模块, 只读 row 里的字段)

数据访问: 全部走 DataStore (tools/storage/store.py) —— 业务层禁直读 parquet (守门员规则)
"""
from __future__ import annotations


# ============================================================
# 行业 K 线 / 行业归属 (走 DataStore)
# ============================================================

def industry_bars(ts_code: str) -> list[dict]:
    """行业日线 bars (字段跟个股 ctx 一致: vol→volume 已在 store 层做完)"""
    from tools.storage.store import DataStore
    return DataStore.get_sw_index_bars(ts_code)


def industry_name(ts_code: str) -> str:
    bars = industry_bars(ts_code)
    return str(bars[0].get("name") or "") if bars else ""


def industry_of_stock(ts_code: str) -> str | None:
    """查某只票的 SW2021 二级行业代码 (801xxx.SI), 没有返 None

    ts_code 兼容 '300274' / '300274.SZ' 两种写法。
    """
    if not ts_code:
        return None
    from tools.storage.store import DataStore, _to_ts_code

    code = str(ts_code).strip()
    if "." not in code:
        try:
            code = _to_ts_code(code)
        except Exception:
            code = (f"{code}.SH" if code.startswith(("60", "68")) else f"{code}.SZ")
    return DataStore.get_industry_member_map().get(code)


# ============================================================
# 行业威科夫 (查 cache → 算 → 回写)
# ============================================================

def industry_wyckoff_rows(ts_code: str, dates: list[str]) -> dict[str, dict]:
    """取行业在指定日期上的威科夫判定结果

    流程: 查 cache → 缺的算 (复用个股同一套 analyze_history) → 回写 cache → 返回

    Args:
        ts_code: 行业代码 (801xxx.SI)
        dates:   日期列表 (YYYYMMDD)

    Returns:
        {trade_date: {stage_daily, stage_weekly, strength_daily, ...}}
        没数据 / 没行业 → 返 {} (调用方渲染成 "—")
    """
    if not ts_code or not dates:
        return {}

    from tools.storage.caches import industry_wyckoff as cache

    want = [str(d).replace("-", "")[:8] for d in dates]
    want = [d for d in want if d]

    out: dict[str, dict] = {}
    missing: list[str] = []
    for d in want:
        hit = cache.get_cached(ts_code, d)
        if hit is not None:
            out[d] = hit
        else:
            missing.append(d)

    if missing:
        rows = _compute(ts_code, missing)
        if rows:
            cache.write_rows(rows)
            for r in rows:
                out[str(r["trade_date"])] = r
    return out


def _compute(ts_code: str, dates: list[str]) -> list[dict]:
    """真算 —— 跟个股同一套 AnalysisEngine.analyze_history (零改动复用)"""
    from tools.analysis.analysis_engine import AnalysisEngine, RawContext
    from tools.storage.sources.eastmoney import _synthesize_weekly

    bars = industry_bars(ts_code)
    if len(bars) < 30:
        return []
    # 只算**行业指数真有的交易日** (个股停牌 / 日历差异 → 该日期行业没数据,
    #   硬算会缓存一行 "?" 垃圾, 白占 cache)
    have = {b["trade_date"] for b in bars}
    dates = [d for d in dates if d in have]
    if not dates:
        return []
    weekly = _synthesize_weekly(bars)
    ctx = RawContext(kline=bars, weekly=weekly, code=ts_code, name=industry_name(ts_code))

    results = AnalysisEngine().analyze_history(ctx, dates)

    rows = []
    for d, res in results.items():
        raw = getattr(res, "raw", {}) or {}
        wy = raw.get("wyckoff") or {}
        p3 = wy.get("3period") or {}
        dly = p3.get("daily") or {}
        wk = p3.get("weekly") or {}
        se_by_period = wy.get("sub_events_by_period") or {}
        rows.append({
            "ts_code": ts_code,
            "trade_date": str(d),
            # stage 缺省 "?" (跟个股 _extract_row 同口径: 该周期没判定出来 = "?")
            "stage_daily": dly.get("stage", "?"),
            "stage_weekly": wk.get("stage", "?"),
            "strength_daily": dly.get("strength"),
            "strength_weekly": wk.get("strength"),
            "channel_daily": dly.get("channel"),
            "channel_weekly": wk.get("channel"),
            "sub_event_daily": _today_events(se_by_period.get("daily") or [], str(d)),
            "sub_event_weekly": _today_events(se_by_period.get("weekly") or [], str(d)),
        })
    return rows


def _today_events(events: list[dict], date: str) -> str:
    """当天新触发的 sub_events (跟个股 _today_sub_events 同口径)"""
    if not events:
        return "—"
    names = sorted({e["name"] for e in events
                    if str(e.get("date", "")).replace("-", "")[:8] == date})
    return " ".join(names) if names else "—"
