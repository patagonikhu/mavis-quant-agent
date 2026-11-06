"""
analysis_cache.py — AnalysisResult SQLite 缓存

Schema (2026-10-19 v3 改: 加 13 列威科夫字段, 回测威科夫 A→M 胜率):
  CREATE (15) + ALTER (18, 累计 33 列):
    code / date_str / kline_hash / updated_at (4)
    MA 斜率 (1: ma20_slope 5日斜率%)
    动量 (4: rsi6/macd_dif/macd_dea/macd_bar_delta)
    量能 (1: vol_ratio 当日量/MA5)
    估值 (2 ALTER, 2026-09-02 加): roc / ey
          2026-11-02 删 peg / dcf_l (PEG/DCF 整档已删 — PEG 是假指标)
          2026-11-06 再删物理列 (代码早不写不读, 275 万行 db 里白占空间)
    威科夫 (13 ALTER, 2026-10-19 加, A→M 胜率回测用):
      wy_stage (Accumulation/Markup/Distribution/?)
      wy_score (1.0/0.6/-0.6/0.0)
      wy_sub_event_count (近 5 根 K 线内触发数)
      wy_30d_pct (30 日涨幅%)
      wy_has_{spring,lps,evr,sos,compression,trend_pullback,markup_entry,distribution_start,utad}
      (9 个 0/1, 近 5 根 K 线内有触发 = 1)
  历史: 2026-09-22 删 26 列 (10 wy_* + 11 信号列 + 3 ma_dev + 2 boll_*)
        2026-10-19 用户做威科夫胜率回测, 加回 13 列 (sub_event 9 + 主 stage/score/count/30d)
        2026-11-06 DROP 5 个死列: obv / obv5 / obv_trend (10-27 删 OBV) +
          peg / dcf_l (11-02 删 PEG/DCF) —— 代码早已不写不读, 物理列留在
          275 万行的 db 里纯占空间 + 给未来 query 挖坑
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent.parent.parent
_DB = _ROOT / "data" / "analysis_cache.db"
# 2026-09-22 修复 path bug: 之前 .parent.parent.parent 是 3 层 (= tools/), 正确应为 4 层 (项目根 mavis-quant-agent/)
# 2026-11-06: 唯一 db 就是项目根 data/analysis_cache.db (275 万行 / 442MB / 25 列)
#   注: tools/data/analysis_cache.db 是 500 行 sandbox 残留 (带已下线的缠论 chan_* 列), 0 引用,
#     但 safe_delete 的 PROTECTED_PATHS.endswith 规则把任意层级的 data/analysis_cache.db 都护住了 → 清不掉

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    code            TEXT NOT NULL,
    date_str        TEXT NOT NULL,
    kline_hash      TEXT NOT NULL,

    -- 2026-09-22 删: wy_* (10列), ma_dev (3列), boll_* (2列)
    -- 原因: 真值由 AnalysisEngine.analyze() 实时算, cache 是镜像, 删除减少 26 列空白存储
    -- 已删: wy_stage, wy_markup_entry/spring/lps/evr/sos/compression/trendpullback/distributionstart/utad
    --       ma5_dev/ma20_dev/ma60_dev
    --       boll_bpct/boll_bwidth

    -- MA 斜率
    ma20_slope      REAL,        -- MA20 5日斜率 % = (ma0 - ma5ago) / ma5ago / 5 * 100

    -- 动量
    rsi6            REAL,        -- 6日 Wilder RSI (0-100)
    macd_dif        REAL,        -- MACD DIF (EMA12-EMA26)
    macd_dea        REAL,        -- MACD DEA (EMA9 of DIF)
    macd_bar_delta  REAL,        -- MACD 红/绿柱日变化 = bar_t - bar_{t-1}

    -- 量能
    vol_ratio       REAL,        -- 当日量 / MA5 量 (1.0=平, >1.5=放量)


    -- 2026-10-19 加: 威科夫 (12 列) — /t-sync-data --cache 写入
    -- 用途: 威科夫 A→M 胜率回测 (V11 字面版验证)
    -- sub_event 字段: 当日及最近 5 根 K 线 (近 5 日窗口) 触发 = 1, 否则 = 0
    wy_stage            TEXT,        -- 主 stage (Accumulation / Markup / Distribution / ?)
    wy_score            REAL,        -- 1.0 (Markup) / 0.6 (Accumulation) / -0.6 (Distribution) / 0.0 (?)
    wy_sub_event_count  INTEGER,     -- 当日触发 sub_event 数量 (level=daily)
    wy_30d_pct          REAL,        -- 30 日价格涨幅 %
    wy_has_spring           INTEGER, -- 最近 5 根 K 线内有 Spring (Accumulation 信号)
    wy_has_lps              INTEGER, -- LPS (Accumulation 二次探底)
    wy_has_evr              INTEGER, -- EVR (Accumulation Effort vs Result)
    wy_has_sos              INTEGER, -- SOS (Markup Sign of Strength)
    wy_has_compression      INTEGER, -- Compression (Markup 蓄势)
    wy_has_trend_pullback   INTEGER, -- TrendPullback (Markup 趋势回踩)
    wy_has_markup_entry     INTEGER, -- MarkupEntry (Markup 入场)
    wy_has_distribution_start INTEGER, -- DistributionStart (顶部派发开始)
    wy_has_utad             INTEGER  -- UTAD (顶部派发后反弹测试)

    -- meta
    updated_at      REAL,

    PRIMARY KEY (code, date_str)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_code_date ON analysis_cache(code, date_str);
-- 2026-09-22 删: idx_wy_stage (随 wy_stage 列删除)
"""

# ── 工具 ──────────────────────────────────────────────────

def _conn() -> sqlite3.Connection:
    os.makedirs(_DB.parent, exist_ok=True)
    c = sqlite3.connect(str(_DB), check_same_thread=False)
    c.execute("PRAGMA journal_mode=WAL")
    # 大 DB 性能优化
    c.execute("PRAGMA cache_size=-200000")      # 200MB page cache
    c.execute("PRAGMA temp_store=MEMORY")       # temp table 内存
    c.execute("PRAGMA mmap_size=268435456")     # 256MB mmap (大文件读快)
    # 2026-09-22 改: synchronous=OFF 替换 NORMAL (Phase 2 写快 5x, WAL 模式下安全)
    #   WAL + OFF = 不会损坏 db, 但掉电丢最后 1-2 个事务 (缓存可重建, 接受)
    #   OFF 适合 warmup_cache/backfill 这类"重新跑成本低"的写场景
    c.execute("PRAGMA synchronous=OFF")
    return c


def _init():
    with _conn() as c:
        c.executescript(_SCHEMA)
        # 自动迁移：加新列（若已存在则忽略）
        existing = {r[1] for r in c.execute("PRAGMA table_info(analysis_cache)").fetchall()}
        for col, typedef in [
                             # 2026-09-22 删: boll_bwidth (随 26 列一并删)
                             # 2026-09-02 加: 估值 4 列 (Magic Formula 回测用)
                             # 2026-11-02 删: peg / dcf_l (PEG/DCF 整档已删 — PEG 是假指标)
                             ("roc", "REAL"),       # Greenblatt ROC % (TTM)
                             ("ey", "REAL"),        # Greenblatt EY % (TTM)
                             # 2026-09-22 加: 技术指标 9 列 (RSI6/MACD/量比/MA20 斜率, backfill/rsi6_tech 用)
                             ("ma20_slope", "REAL"),    # MA20 5日斜率 %
                             ("rsi6",          "REAL"), # 6日 Wilder RSI
                             ("macd_dif",      "REAL"), # MACD DIF
                             ("macd_dea",      "REAL"), # MACD DEA
                             ("macd_bar_delta","REAL"), # MACD 柱日变化
                             ("vol_ratio",     "REAL"), # 当日量 / MA5 量
                             # 2026-10-19 加: 威科夫 13 列 (/t-sync-data --cache + /t-wyckoff-a2m 胜率回测)
                             ("wy_stage",                  "TEXT"),
                             ("wy_score",                  "REAL"),
                             ("wy_sub_event_count",        "INTEGER"),
                             ("wy_30d_pct",                "REAL"),
                             ("wy_has_spring",             "INTEGER"),
                             ("wy_has_lps",                "INTEGER"),
                             ("wy_has_evr",                "INTEGER"),
                             ("wy_has_sos",                "INTEGER"),
                             ("wy_has_compression",        "INTEGER"),
                             ("wy_has_trend_pullback",     "INTEGER"),
                             ("wy_has_markup_entry",       "INTEGER"),
                             ("wy_has_distribution_start", "INTEGER"),
                             ("wy_has_utad",               "INTEGER"),
                             ]:
            if col not in existing:
                c.execute(f"ALTER TABLE analysis_cache ADD COLUMN {col} {typedef}")

        # 自动迁移: 删死列 (若还在)
        # 2026-11-06: OBV (10-27 删) / PEG·DCF (11-02 删) —— 代码早已不写不读,
        #   物理列却还留在 275 万行的 db 里: 白占空间 + 给未来 query 挖坑
        for col in ("obv", "obv5", "obv_trend", "peg", "dcf_l"):
            if col in existing:
                c.execute(f"ALTER TABLE analysis_cache DROP COLUMN {col}")


def _kline_hash(kline: list[dict]) -> str:
    if not kline:
        return "empty"
    closes = [k.get("close", 0) for k in kline[-5:]]
    return hashlib.md5(f"{closes}_{len(kline)}".encode()).hexdigest()[:12]


# ── result → row ──────────────────────────────────────────

# 2026-09-22 删: _ma_dev, _boll_bpct, _boll_bwidth (随 wy/chan/boll 列一并删除)
#   get_boll_bpct / get_boll_bpct_all 同步删 (下游已替换为实时算)


def _ma20_slope(kline: list[dict]) -> float | None:
    """MA20 5日斜率 % = (MA20[-1] - MA20[-6]) / MA20[-6] / 5 * 100

    2026-09-22 加: 同算法与 analysis_result_signals.py:114-134 一致
    (MA20 通过 close / (1 + 0) 即直接拿 close[-(i+20)] 的均值估)
    不足 25 根 K 线返回 None (需 20 根算 MA20 + 5 根往前)
    """
    if not kline or len(kline) < 25:
        return None
    closes = [k.get("close", 0) for k in kline if k.get("close")]
    if len(closes) < 25:
        return None
    ma0 = sum(closes[-20:]) / 20
    ma5 = sum(closes[-25:-5]) / 20
    if ma5 <= 0:
        return None
    return round((ma0 - ma5) / ma5 / 5 * 100, 4)


# 2026-10-19 加: 威科夫 13 列提取 (wyckoff_result 来自 analysis_engine)
# raw['wyckoff'] 结构: {stage, score, signals, summary, sub_event_count, verdict, 趋势_30日_pct,
#                       sub_events_by_period={daily: [{name,date,idx,...}], weekly: [...]}, 3period: {...}}
# 9 种 sub_event: Spring / LPS / EVR / SOS / Compression / TrendPullback / MarkupEntry / DistributionStart / UTAD
# cache column 名: 跟 raw['sub_events'][i]['name'] 不一样 (有下划线), 映射表 (raw_name → cache_col_suffix)
_WY_SUB_EVENTS = [
    ("Spring",            "spring"),
    ("LPS",               "lps"),
    ("EVR",               "evr"),
    ("SOS",               "sos"),
    ("Compression",       "compression"),
    ("TrendPullback",     "trend_pullback"),  # raw 用 CamelCase, cache col 用 snake_case
    ("MarkupEntry",       "markup_entry"),
    ("DistributionStart", "distribution_start"),
    ("UTAD",              "utad"),
]


def _extract_wyckoff(result, lookback_sub_events: int = 5) -> dict:
    """从 AnalysisResult.raw 提 13 列 cache 字段

    Args:
        result: AnalysisResult 对象 (有 .raw 属性)
        lookback_sub_events: sub_event 几根 K 线内算 'has' (默认 5 = 近 5 日)
    """
    raw = getattr(result, "raw", None) or (result.get("raw", {}) if isinstance(result, dict) else {})
    wy = raw.get("wyckoff", {}) or {}

    # 1) stage + score + 30d + count (日线, 跟 /t-wyckoff-a2m 一致)
    stage    = wy.get("stage", "?")
    score    = wy.get("score", 0.0)
    pct_30d  = wy.get("趋势_30日_pct", None)
    sub_n    = wy.get("sub_event_count", 0)

    # 2) 9 种 sub_event 在最近 N 根 K 线内 (用 3period.daily.sub_events)
    #    sub_events 结构: list[{name, date, idx, price, vol}] (idx 是 K 线偏移)
    daily = wy.get("3period", {}).get("daily", {}) or {}
    sub_events_full = daily.get("sub_events", []) or []
    # 近 N 根 = idx 倒序 N 个
    recent_names = {e.get("name") for e in sub_events_full[-lookback_sub_events:] if e.get("name")}

    # raw 用 CamelCase, cache col 用 snake_case (跟 _SCHEMA 里的 wy_has_xxx_xxx 一致)
    has_flags = {f"wy_has_{col_suffix}": (1 if raw_name in recent_names else 0)
                 for raw_name, col_suffix in _WY_SUB_EVENTS}

    return {
        "wy_stage":           stage,
        "wy_score":           score,
        "wy_sub_event_count": sub_n,
        "wy_30d_pct":         pct_30d,
        **has_flags,
    }


def _result_to_row(code: str, date_str: str,
                   kline: list[dict], result) -> dict[str, Any]:
    # 2026-09-22 改: 删 wy_* / boll_* / ma_dev 全部赋值
    # 原因: 真值由 AnalysisEngine.analyze() 实时算, cache 只存技术指标 + 估值
    # 2026-10-19 改: 重新加 wy_* 13 列 (用户回测威科夫 A→M 胜率, cache 必须能查)
    raw = getattr(result, "raw", None) or (result.get("raw", {}) if isinstance(result, dict) else {})

    # 技术指标 (来自 TechnicalStrategy, 已写入 raw['technical'])
    tech = raw.get("technical", {}) or {}

    # 2026-10-19 加: 威科夫 13 列
    wy = _extract_wyckoff(result)

    return {
        "code": code,
        "date_str": date_str,
        "kline_hash": _kline_hash(kline),
        # MA 斜率 (仅存的 MA 列)
        "ma20_slope":  _ma20_slope(kline),
        # 动量 (2026-09-22 加 4 列, 从 raw['technical'] 取; 红/绿柱从 bar_delta 推导)
        "rsi6":          tech.get("rsi6"),
        "macd_dif":      tech.get("macd_dif"),
        "macd_dea":      tech.get("macd_dea"),
        "macd_bar_delta":tech.get("macd_bar_delta"),
        # 量能 (2026-09-22 加)
        "vol_ratio":     tech.get("vol_ratio"),
        # 威科夫 (2026-10-19 加 13 列, /t-wyckoff-a2m 胜率回测用)
        **wy,
        "updated_at": time.time(),
    }


# ── API ───────────────────────────────────────────────────

def _query(sql: str, params: tuple = ()) -> list[dict]:
    """通用 SELECT, 返 list[dict] (列名映射, 2026-09-03 v6.2.3 合并加)"""
    conn = _conn()
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def get_cached(code: str, dates: list[str]) -> dict[str, dict]:
    """返回 {date_str: row}，只返回命中的"""
    if not dates:
        return {}
    conn = _conn()
    try:
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT * FROM analysis_cache WHERE code=? AND date_str IN ({placeholders})",
            [code] + dates
        ).fetchall()
        # 按名字映射，不用列顺序
        col_map = {d[1]: i for i, d in enumerate(
            conn.execute("PRAGMA table_info(analysis_cache)").fetchall())}
    finally:
        conn.close()
    return {r[col_map["date_str"]]: {c: r[i] for c, i in col_map.items()} for r in rows}


def write_batch(code: str, kline: list[dict], results: dict[str, dict]):
    """批量写入 (事务, executemany)"""
    if not results:
        return
    date_idx = {k["trade_date"].replace("-", "")[:8]: i for i, k in enumerate(kline)}
    conn = _conn()
    try:
        # 一次性准备所有 row + sql
        rows = []
        cols = None
        ph = None
        for ds, result in results.items():
            kl = kline[:date_idx.get(ds, -1) + 1]
            row = _result_to_row(code, ds, kl, result)
            if cols is None:
                cols = list(row)
                ph = ",".join(["?"] * len(cols))
            rows.append([row[c] for c in cols])
        if not rows: return
        conn.executemany(
            f"INSERT OR REPLACE INTO analysis_cache ({','.join(cols)}) VALUES ({ph})",
            rows
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def get_cached_codes() -> set[str]:
    """返所有已 cache 的 code"""
    return {r["code"] for r in _query("SELECT DISTINCT code FROM analysis_cache")}
def get_existing_valuation_pairs() -> set[tuple[str, str]]:
    """返 (code, date) 已有 valuation 的对 (roc/ey 任一非空)

    2026-11-02: 删 peg / dcf_l (PEG/DCF 整档已删)
    """
    rows = _query(
        "SELECT code, date_str FROM analysis_cache "
        "WHERE roc IS NOT NULL OR ey IS NOT NULL"
    )
    return {(r["code"], r["date_str"]) for r in rows}


def get_valuation_coverage() -> dict:
    """返 2 个 valuation 字段覆盖率 {col: count} (2026-11-02: 删 peg / dcf_l)"""
    out = {"roc": 0, "ey": 0}
    for col in out:
        rows = _query(f"SELECT COUNT(*) AS n FROM analysis_cache WHERE {col} IS NOT NULL")
        out[col] = rows[0]["n"] if rows else 0
    return out


def check_stale_batch(code: str, dates: list[str],
                       kline: list[dict]) -> dict[str, bool]:
    """批量检查 {date_str: is_stale}，一次性查所有 dates"""
    if not dates:
        return {}
    date_idx = {k["trade_date"].replace("-", "")[:8]: i for i, k in enumerate(kline)}
    conn = _conn()
    try:
        placeholders = ",".join("?" * len(dates))
        rows = conn.execute(
            f"SELECT date_str, kline_hash FROM analysis_cache WHERE code=? AND date_str IN ({placeholders})",
            [code] + dates
        ).fetchall()
        stored = {r[0]: r[1] for r in rows}
    finally:
        conn.close()
    result = {}
    for d in dates:
        idx = date_idx.get(d, -1)
        kl = kline[:idx+1] if idx >= 0 else kline
        stored_hash = stored.get(d)
        result[d] = (stored_hash is None) or (stored_hash != _kline_hash(kl))
    return result


def get_stats() -> dict:
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0]
        codes = conn.execute("SELECT COUNT(DISTINCT code) FROM analysis_cache").fetchone()[0]
        size  = _DB.stat().st_size / 1024 / 1024 if _DB.exists() else 0
    finally:
        conn.close()
    return {"rows": total, "codes": codes, "size_mb": round(size, 2)}


# ============================================================
# warmup_cache — sync_data --cache 唯一入口 (2026-09-03 v6.2.1 合并)
# 之前在 tools/batch/signal_cache_warmup.py, 删 batch 文件, 搬这里
# ============================================================

def _load_tech_codes() -> list[str]:
    """从 stock_basic.parquet 取申万科技行业股票，与本地 parquet 取交集。

    2026-09-03 v6.2.1 改: 走 DataStore.load_stock_basic, 不直读 parquet
    """
    try:
        from tools.storage.store import DataStore
        df = DataStore.load_stock_basic()
        if df.empty:
            return []
        TECH_KW = [
            "半导体", "软件服务", "通信设备", "电子元件", "电子信息",
            "计算机设备", "电气设备", "电器仪表", "光学光电子",
            "互联网", "军工", "航天", "航空", "汽车电子", "机器人", "新能源",
            "元器件", "专用机械", "IT设备", "新型电力",
        ]
        mask = df["industry"].fillna("").apply(
            lambda x: any(kw in x for kw in TECH_KW)
        )
        tech_codes = set(df[mask]["ts_code"].apply(lambda c: c.split(".")[0]))
        all_local = set(DataStore.list_codes())
        return sorted(tech_codes & all_local)
    except Exception as exc:
        print(f"⚠️  科技股过滤失败: {exc}，降级到 watchlist")
        from tools.storage.store import DataStore
        wl = DataStore.load_watchlist()["stocks"]
        return [s["code"] for s in wl]


def _calc_signals_for_code(code: str, full: bool, batch_size: int):
    """Phase1: 找最老的缺失段（最多 batch_size 根），只算那一段。

    策略：
    - 扫全量 K 线（5年），找所有 stale 日期
    - 取最老的连续缺失段，最多 batch_size 根
    - 计算时往前加 120 根上下文缓冲
    """
    import time as _t
    t0 = _t.time()
    try:
        from tools.storage.store import DataStore
        # 2026-09-22 改: kline_only=True 跳过 eps 拉取
        #   只跑 Technical, 不需要额外数据源
        #   eps 也无需 (估值 4 列由 backfill_roc_ey_cache.py 单写)
        ctx = DataStore.get_ctx(code, kline_only=True)
        if not ctx.kline:
            return code, None, None, 0, _t.time() - t0

        kline = ctx.kline
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]

        if full:
            stale_dates = all_dates[:batch_size] if batch_size < len(all_dates) else all_dates
            skipped = 0
        else:
            stale_map = check_stale_batch(code, all_dates, kline)
            all_stale = [d for d in all_dates if stale_map.get(d, True)]
            skipped = len(all_dates) - len(all_stale)
            if not all_stale:
                return code, {}, kline, skipped, _t.time() - t0
            stale_dates = all_stale[:batch_size]

        if not stale_dates:
            return code, {}, kline, skipped, _t.time() - t0

        first_stale_idx = next((i for i, d in enumerate(all_dates) if d == stale_dates[0]), 0)
        last_stale_idx  = next((i for i, d in enumerate(all_dates) if d == stale_dates[-1]), first_stale_idx)
        buf_start = max(0, first_stale_idx - 120)
        compute_dates = all_dates[buf_start : last_stale_idx + 1]

        from tools.analysis.analysis_engine import (
            AnalysisEngine, TechnicalStrategy,
        )
        # 2026-09-22 改: 只跑真存 db 的 strategy
        #   Wyckoff/Chan/Structure/Fflow/Finance 字段已 9-22 删列
        #   (roc/ey/peg/dcf_l 由 backfill_roc_ey_cache.py 单写, 不走 warmup)
        #   跳过 5 个 strategy 省 60-70% 时间
        # 2026-10-27: OBV strategy 已删除, 只剩 TechnicalStrategy
        history = AnalysisEngine(strategies=[TechnicalStrategy]) \
                          .analyze_history(ctx, compute_dates)
        stale_set = set(stale_dates)
        to_write = {}
        for d in compute_dates:
            result = history.pop(d, None)
            if d not in stale_set or result is None:
                continue
            to_write[d] = result
        return code, to_write, kline, skipped, _t.time() - t0
    except Exception:
        return code, None, None, 0, _t.time() - t0


def warmup_cache(codes: list[str] | None = None,
                 scope: str = "all",  # 'all' (默认,全市场) | 'tech' | 'portfolio' | 'codes'
                 timeout: int = 600,
                 workers: int = 2,
                 batch_size: int = 250,
                 full: bool = False) -> dict:
    """预热 analysis_cache.db (sync_data --cache 唯一入口)

    2026-09-22 改: 默认 scope='all' (回测场景需要全市场 5555 只)
        之前默认 'tech' = 申万科技子集 (~1923), 缺金融/消费/医药, 回测不全

    Args:
        codes: 显式 codes 列表 (scope='codes' 时用)
        scope: 股票池选择
        timeout: 超时秒数
        workers: 并发数
        batch_size: 每次每只股票最多补多少根K线
        full: 强制重算最老段 (不判断 stale)

    Returns:
        {"written": int, "skipped": int, "elapsed": float}
    """
    import time as _t
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tools.storage.store import DataStore

    # ── 收集 codes ──
    if scope == "all":
        CODES = DataStore.list_codes()
        print(f"全市场: {len(CODES)} 只")
    elif scope == "portfolio":
        wl = DataStore.load_watchlist()["stocks"]
        CODES = [s["code"] for s in wl if s.get("list_type") == "持仓"]
        print(f"持仓: {len(CODES)} 只")
    elif scope == "codes" and codes:
        CODES = codes
    elif scope == "tech":
        # 兼容老调用 (deprecated): 申万科技子集, 回测不全
        CODES = _load_tech_codes()
        print(f"科技股: {len(CODES)} 只 (申万行业筛选 ∩ 本地K线, [deprecated] 改用 scope='all')")
    else:
        # 未知 scope: 不静默 fallback, 报错
        raise ValueError(f"warmup_cache: 未知 scope={scope}, 期待 'all'|'tech'|'portfolio'|'codes'")

    mode = "全量重算最老段" if full else "增量(从最老缺口补)"
    print(f"预热 {len(CODES)} 只 | batch_size={batch_size}根/只 | {workers}并发 | {mode} | timeout={timeout}s")
    print(f"初始缓存: {get_stats()}")
    t0 = _t.time()

    # ── Phase1: 并发算 (不写 DB) ──
    results_map: dict[str, tuple] = {}
    done = 0
    total = len(CODES)
    recent_times: list[float] = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_calc_signals_for_code, code, full, batch_size): code
                for code in CODES}
        for fut in as_completed(futs):
            code = futs[fut]
            _, to_write, kline, skipped, elapsed = fut.result()
            done += 1
            results_map[code] = (to_write, kline, skipped, elapsed)
            recent_times.append(elapsed)
            if len(recent_times) > 20:
                recent_times.pop(0)
            avg = sum(recent_times) / len(recent_times)
            remaining = total - done
            eta = remaining * avg / workers
            eta_str = f"{int(eta//60)}m{int(eta%60):02d}s" if eta < 9999 else "--"
            tag = "⏭" if to_write == {} else ("❌" if to_write is None else f"+{len(to_write or {})}")
            if done % 10 == 0 or to_write is None or (to_write and len(to_write) > 0):
                pct = done / total * 100
                print(f"  [{done:4d}/{total}] {pct:5.1f}%  ETA {eta_str}  {tag:>4} {code} {elapsed:.1f}s", flush=True)
            if _t.time() - t0 >= timeout:
                print(f"⏰ timeout {timeout}s 到，取消剩余 {remaining} 个任务，写已完成结果...")
                for f in futs:
                    f.cancel()
                break

    # ── Phase2: 串行写 (主线程, 无锁竞争) ──
    print("\n── Phase2: 写缓存 ──")
    total_written = total_skipped = 0
    for code in CODES:
        to_write, kline, skipped, elapsed = results_map.get(code, (None, None, 0, 0))
        if to_write is None:
            print(f"  ❌ {code}: 无数据")
            continue
        if to_write:
            write_batch(code, kline, to_write)
        total_written += len(to_write)
        total_skipped += skipped

    elapsed_total = _t.time() - t0
    print(f"\n完成: 写{total_written:,}行 / 跳{total_skipped:,}行 / {done}只 / {elapsed_total:.0f}s")
    final = get_stats()
    print(f"缓存总: {final['rows']:,} 行 | {final['codes']} 只 | {final['size_mb']:.1f}MB")
    return {"written": total_written, "skipped": total_skipped, "elapsed": elapsed_total}


_init()
