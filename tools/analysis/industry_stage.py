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

⚠️ **本模块只读 cache, 不写** (2026-11-12 用户定调):
  行业威科夫 cache 的唯一写者是 `/t-sync-data` (tools/storage/sync.py)。
  **查不到就不显示** (渲染成「—」), 不要悄悄自己算 ——
  悄悄算的话用户看不出"这列数据其实没有", 空列本身就是"该跑 /t-sync-data"的信号。
  写路径见 tools/storage/caches/industry_wyckoff.py。

架构 (规则下沉):
  本模块 = 生产侧, 算好 stage/strength/channel/sub_event 塞进行数据
  渲染器只读不算 (渲染器不 import 本模块, 只读 row 里的字段)

数据访问: 全部走 DataStore (tools/storage/store.py) —— 业务层禁直读 parquet (守门员规则)
"""
from __future__ import annotations

import sys


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
# 行业威科夫 (**只读 cache**)
# ============================================================

_MISS_WARNED = False


def _warn_miss_once(n_missing: int, ts_code: str) -> None:
    """cache 有缺口 → 每个进程提示一次 (用户要知道数据缺了, 去跑 /t-sync-data)

    ⚠️ 2026-11-12 用户定调: **miss 就不显示**, 不要悄悄自己算 ——
    悄悄算的话用户看不出"这列数据其实没有", 空列本身就是信号。
    """
    global _MISS_WARNED
    if _MISS_WARNED:
        return
    _MISS_WARNED = True
    print(f"  ⚠️ 行业威科夫 cache 缺 {n_missing} 行 (如行业 {ts_code}) → 该列显示「—」; "
          f"跑 /t-sync-data (--sw-index) 补齐", file=sys.stderr)


def industry_wyckoff_rows(ts_code: str, dates: list[str]) -> dict[str, dict]:
    """取行业在指定日期上的威科夫判定结果 (**只读 cache, miss 就不显示**)

    ⚠️ 2026-11-12 改 (用户定调): 原来查不到会**在内存现算**并回写 cache ——
    那既逼出了文件锁 (多进程同时写), 又把"这列数据其实没有"藏起来了。
    现在: **只查表, 没有就没有** → 渲染成「—」, 空列本身就是"cache 没同步"的信号,
    用户看到就去跑 /t-sync-data。

    写 cache 的活归 `/t-sync-data` (tools/storage/sync.py) —— 本模块**永不写**。

    Args:
        ts_code: 行业代码 (801xxx.SI)
        dates:   日期列表 (YYYYMMDD)

    Returns:
        {trade_date: {stage_daily, stage_weekly, strength_daily, ...}}
        **只含 cache 里命中的日期**; 全 miss → 返 {} (调用方渲染成 "—")
    """
    if not ts_code or not dates:
        return {}

    from tools.storage.caches import industry_wyckoff as cache

    want = [str(d).replace("-", "")[:8] for d in dates]
    want = [d for d in want if d]

    out: dict[str, dict] = {}
    n_missing = 0
    for d in want:
        hit = cache.get_cached(ts_code, d)
        if hit is not None:
            out[d] = hit
        else:
            n_missing += 1
    if n_missing:
        _warn_miss_once(n_missing, ts_code)
    return out


def compute_industry_wyckoff_rows(ts_code: str, dates: list[str]) -> list[dict]:
    """真算 —— 跟个股同一套 AnalysisEngine.analyze_history (零改动复用)

    ⚠️ **纯计算**: 不读 cache, 不写 cache。写 cache 的活归 /t-sync-data。
    """
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


# ============================================================
# 行业 Markup 汇总 (2026-11-12 加, /t-analyze 跑完的汇总段)
# ============================================================

def summarize_markup_industries() -> dict:
    """哪些行业当前在威科夫 Markup 阶段 (日线 / 周线)

    **数据源: 行业威科夫 cache (只读)** —— 唯一写者是 `/t-sync-data`。
    这里**不算**, 只查表: 跟个股报告「因子历史走势」表里的「行业威科夫」列**同源同数**,
    两处不会打架。

    "当前" = 每个行业**自己**最新的那一条 cache 记录
    (行业停更的按它自己的末日算, 不跟全市场日期对齐 —— 对齐了它就是没数据)。

    Returns:
        {
          "data_date": 全市场最新交易日 (YYYYMMDD),
          "total_industries": cache 里的行业数,
          "daily_markup":   [{code, name, l1_name, stage_daily, stage_weekly,
                              strength_daily, trade_date, member_count}, ...],
          "weekly_markup":  [同上],       # 周线在 Markup (日线可能还没转)
          "both_markup":    [同上],       # 日线+周线都在 Markup
          "stale":          [{code, name, trade_date}, ...],  # 数据日期落后的行业
        }
    """
    from tools.storage.caches import industry_wyckoff as cache
    from tools.storage.store import DataStore

    dates_by_code = cache.cached_dates_by_code()
    if not dates_by_code:
        return {"data_date": "", "total_industries": 0, "daily_markup": [],
                "weekly_markup": [], "both_markup": [], "stale": []}

    max_date = max((d for ds in dates_by_code.values() for d in ds), default="")

    level_map = DataStore.get_industry_level_map()
    # 成分股数 (一次算全, 别逐行业扫成分表)
    member_count: dict[str, int] = {}
    for rec in DataStore._industry_member_info().values():
        l2 = str(rec.get("l2_code") or "")
        if l2:
            member_count[l2] = member_count.get(l2, 0) + 1

    daily, weekly, both, stale = [], [], [], []
    for code, dates in dates_by_code.items():
        if not dates:
            continue
        d = max(dates)
        row = cache.get_cached(code, d)
        if not row:
            continue
        meta = level_map.get(code, {})
        item = {
            "code": code,
            "name": meta.get("l2_name") or code,
            "l1_name": meta.get("l1_name") or "",
            "stage_daily": row.get("stage_daily") or "?",
            "stage_weekly": row.get("stage_weekly") or "?",
            "strength_daily": row.get("strength_daily") or "—",
            "trade_date": d,
            "member_count": member_count.get(code, 0),
        }
        if item["stage_daily"] == "Markup":
            daily.append(item)
        if item["stage_weekly"] == "Markup":
            weekly.append(item)
            if item["stage_daily"] == "Markup":
                both.append(item)
        if d != max_date:
            stale.append({"code": code, "name": item["name"], "trade_date": d})

    # 排序: 成分股多的 (大行业) 排前面 → 优先看有体量的
    daily.sort(key=lambda x: (-x["member_count"], x["name"]))
    weekly.sort(key=lambda x: (-x["member_count"], x["name"]))
    both.sort(key=lambda x: (-x["member_count"], x["name"]))
    stale.sort(key=lambda x: x["trade_date"])

    return {
        "data_date": max_date,
        "total_industries": len(dates_by_code),
        "daily_markup": daily,
        "weekly_markup": weekly,
        "both_markup": both,
        "stale": stale,
    }


def format_markup_summary(s: dict) -> str:
    """行业 Markup 汇总 → 终端可读文本 (stdout 汇总段用)"""
    if not s or not s.get("total_industries"):
        return ("> ⚠️ 行业威科夫 cache 为空 —— 跑 `/t-sync-data --sw-index` 补齐后再看")

    lines = [
        f"=== 行业威科夫 Markup 汇总 ({len(s['daily_markup'])} 个行业日线在 Markup) ===",
        f"  数据源: 行业威科夫 cache (只读, 唯一写者 /t-sync-data) · "
        f"最新交易日 {s['data_date']} · 共 {s['total_industries']} 个行业",
    ]

    if s["daily_markup"]:
        lines.append("")
        lines.append("| 行业 | 代码 | 一级 | 日线 | 周线 | 强度 | 成分股 | 数据日期 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for it in s["daily_markup"]:
            lines.append(
                f"| {it['name']} | `{it['code'].replace('.SI', '')}` | {it['l1_name']} | "
                f"{it['stage_daily']} | {it['stage_weekly']} | {it['strength_daily']} | "
                f"{it['member_count']} | {it['trade_date']} |")
    else:
        lines.append("")
        lines.append("  (当前没有行业处于日线 Markup)")

    lines.append("")
    lines.append(f"  日线+周线都在 Markup: {len(s['both_markup'])} 个"
                 + (f" → {', '.join(it['name'] for it in s['both_markup'])}"
                    if s["both_markup"] else ""))
    lines.append(f"  周线在 Markup (日线还没转): "
                 f"{len(s['weekly_markup']) - len(s['both_markup'])} 个")

    if s.get("stale"):
        lines.append(f"  ⚠️ 数据日期落后的行业 {len(s['stale'])} 个 "
                     f"(停更或未同步): {', '.join(f'{x['name']}({x['trade_date']})' for x in s['stale'][:6])}"
                     f"{' ...' if len(s['stale']) > 6 else ''}")
    return "\n".join(lines)
