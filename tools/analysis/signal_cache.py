"""
analysis_cache.py — AnalysisResult SQLite 缓存

Schema (24列):
  code / date_str / kline_hash
  wy_stage + wy_sub_events (9 bool)
  chan hub (4列)
  chan 买卖点 (5 bool)
  MA偏离% (3列)
  Boll% (1列)
  updated_at
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).parent.parent.parent
_DB = _ROOT / "data" / "analysis_cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    code            TEXT NOT NULL,
    date_str        TEXT NOT NULL,
    kline_hash      TEXT NOT NULL,

    -- wy
    wy_stage        TEXT,
    wy_markup_entry INTEGER,
    wy_spring       INTEGER,
    wy_lps          INTEGER,
    wy_evr          INTEGER,
    wy_sos          INTEGER,
    wy_compression  INTEGER,
    wy_trendpullback INTEGER,
    wy_distributionstart INTEGER,
    wy_utad         INTEGER,

    -- chan hub
    chan_daily_hub  TEXT,
    chan_daily_pos  TEXT,
    chan_weekly_hub TEXT,
    chan_weekly_pos TEXT,

    -- chan 买卖点
    chan_1buy       INTEGER,
    chan_2buy       INTEGER,
    chan_3buy       INTEGER,
    chan_1sell      INTEGER,
    chan_2sell      INTEGER,
    chan_3sell      INTEGER,
    chan_bot_div    INTEGER,  -- MACD底背驰
    chan_top_div    INTEGER,  -- MACD顶背驰

    -- MA
    ma5_dev         REAL,
    ma20_dev        REAL,
    ma60_dev        REAL,

    -- Boll
    boll_bpct       REAL,
    boll_bwidth     REAL,        -- BOLL 宽度 % ((upper-lower)/mid * 100)

    -- OBV (实用信号, 不用 60d 段背离)
    obv             REAL,        -- OBV 累计值
    obv5            INTEGER,     -- 5 日价跌 + OBV 涨 (1/0)
    obv_trend       INTEGER,     -- OBV > MA20 (1/0)

    -- meta
    updated_at      REAL,

    PRIMARY KEY (code, date_str)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_code_date ON analysis_cache(code, date_str);
CREATE INDEX IF NOT EXISTS idx_wy_stage ON analysis_cache(code, wy_stage);
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
    c.execute("PRAGMA synchronous=NORMAL")     # WAL 模式下安全的折中
    return c


def _init():
    with _conn() as c:
        c.executescript(_SCHEMA)
        # 自动迁移：加新列（若已存在则忽略）
        existing = {r[1] for r in c.execute("PRAGMA table_info(analysis_cache)").fetchall()}
        for col, typedef in [("chan_bot_div", "INTEGER"), ("chan_top_div", "INTEGER"),
                             ("boll_bwidth", "REAL"),
                             ("obv5", "INTEGER"), ("obv_trend", "INTEGER")]:
            if col not in existing:
                c.execute(f"ALTER TABLE analysis_cache ADD COLUMN {col} {typedef}")


def _kline_hash(kline: list[dict]) -> str:
    if not kline:
        return "empty"
    closes = [k.get("close", 0) for k in kline[-5:]]
    return hashlib.md5(f"{closes}_{len(kline)}".encode()).hexdigest()[:12]


# ── result → row ──────────────────────────────────────────

# ── kline → ma/boll ────────────────────────────────────────

def _ma_dev(kline: list[dict], n: int) -> float | None:
    if not kline or len(kline) < n:
        return None
    closes = [k.get("close", 0) for k in kline[-n:]]
    ma = sum(closes) / len(closes)
    return round((kline[-1]["close"] / ma - 1) * 100, 1) if ma > 0 else None


def _boll_bpct(kline: list[dict]) -> float | None:
    if not kline or len(kline) < 20:
        return None
    closes = [k.get("close", 0) for k in kline[-20:]]
    mid = sum(closes) / len(closes)
    std = (sum((c - mid) ** 2 for c in closes) / len(closes)) ** 0.5
    upper = mid + 2 * std
    lower = mid - 2 * std
    c = kline[-1]["close"]
    if upper <= lower:
        return 50.0
    return round((c - lower) / (upper - lower) * 100, 1)


def _boll_bwidth(kline: list[dict]) -> float | None:
    """BOLL 宽度 % = (upper - lower) / mid * 100"""
    if not kline or len(kline) < 20:
        return None
    closes = [k.get("close", 0) for k in kline[-20:]]
    mid = sum(closes) / len(closes)
    if mid <= 0:
        return None
    std = (sum((c - mid) ** 2 for c in closes) / len(closes)) ** 0.5
    return round(4 * std / mid * 100, 2)   # (upper-lower) = 4*std, /mid*100 = 4*std/mid*100


def _result_to_row(code: str, date_str: str,
                   kline: list[dict], result) -> dict[str, Any]:
    # result 可能是 dict 或 AnalysisResult dataclass
    raw = getattr(result, "raw", None) or (result.get("raw", {}) if isinstance(result, dict) else {})
    wy = raw.get("wyckoff", {}) or {}
    chan = raw.get("chan", {}) or {}

    # wy sub_events → bool
    se = wy.get("sub_events", [])
    se_names = set()
    if isinstance(se, list):
        for e in se:
            if isinstance(e, dict):
                se_names.add(e.get("name"))

    se_cols = {
        "wy_markup_entry": "MarkupEntry",
        "wy_spring":       "Spring",
        "wy_lps":          "LPS",
        "wy_evr":          "EVR",
        "wy_sos":          "SOS",
        "wy_compression":  "Compression",
        "wy_trendpullback": "TrendPullback",
        "wy_distributionstart": "DistributionStart",
        "wy_utad":         "UTAD",
    }

    # chan bsp → bool（买卖点 key 包含关键字即触发）
    bsp_daily = {}
    bsp_src = chan.get("buy_sell_points", {}) or {}
    if isinstance(bsp_src, dict):
        bsp_daily = bsp_src.get("daily", {}) or {}
        if not isinstance(bsp_daily, dict):
            bsp_daily = {}

    def _has_bsp(keyword: str) -> int | None:
        return 1 if any(keyword in k for k in bsp_daily) else None

    bsp_flags = {
        "chan_1buy":  _has_bsp("1买"),
        "chan_2buy":  _has_bsp("2买"),
        "chan_3buy":  _has_bsp("3买"),
        "chan_1sell": _has_bsp("1卖"),
        "chan_2sell": _has_bsp("2卖"),
        "chan_3sell": _has_bsp("3卖"),
    }

    # OBV 段背离 (来自 ObvStrategy, 已写入 raw['obv'])
    obv = raw.get("obv", {}) or {}

    # chan hub
    def _hub_str(h: Any) -> str | None:
        if not h or not isinstance(h, dict):
            return None
        lo, hi = h.get("low"), h.get("high")
        return f"¥{lo:.0f}~{hi:.0f}" if lo and hi else None

    def _hub_pos(h: Any) -> str | None:
        if not h:
            return None
        p = str(h.get("pos", ""))
        for kw in ["下方", "上方", "内部", "跌穿"]:
            if kw in p:
                return kw
        return None

    return {
        "code": code,
        "date_str": date_str,
        "kline_hash": _kline_hash(kline),
        # wy
        "wy_stage": wy.get("stage"),
        **{col: 1 if name in se_names else None for col, name in se_cols.items()},
        # chan hub
        "chan_daily_hub":  _hub_str(chan.get("daily", {}).get("hub")),
        "chan_daily_pos":  _hub_pos(chan.get("daily", {}).get("hub")),
        "chan_weekly_hub": _hub_str(chan.get("weekly", {}).get("hub")),
        "chan_weekly_pos": _hub_pos(chan.get("weekly", {}).get("hub")),
        # chan 买卖点 + 背驰
        **bsp_flags,
        "chan_bot_div": 1 if any('底背' in k for k in bsp_daily) else None,
        "chan_top_div": 1 if any('顶背' in k for k in bsp_daily) else None,
        # MA
        "ma5_dev":  _ma_dev(kline, 5),
        "ma20_dev": _ma_dev(kline, 20),
        "ma60_dev": _ma_dev(kline, 60),
        # Boll
        "boll_bpct": _boll_bpct(kline),
        "boll_bwidth": _boll_bwidth(kline),
        # OBV
        "obv":         obv.get("obv"),
        "obv5":        obv.get("obv5"),
        "obv_trend":   obv.get("obv_trend"),
        "updated_at": time.time(),
    }


# ── API ───────────────────────────────────────────────────

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
    finally:
        conn.close()


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


def check_stale(code: str, date_str: str, kline: list[dict]) -> bool:
    """单条检查（慢，仅用于调试）"""
    conn = _conn()
    try:
        r = conn.execute(
            "SELECT kline_hash FROM analysis_cache WHERE code=? AND date_str=?",
            (code, date_str)
        ).fetchone()
    finally:
        conn.close()
    return not r or r[0] != _kline_hash(kline)


def get_stats() -> dict:
    conn = _conn()
    try:
        total = conn.execute("SELECT COUNT(*) FROM analysis_cache").fetchone()[0]
        codes = conn.execute("SELECT COUNT(DISTINCT code) FROM analysis_cache").fetchone()[0]
        size  = _DB.stat().st_size / 1024 / 1024 if _DB.exists() else 0
    finally:
        conn.close()
    return {"rows": total, "codes": codes, "size_mb": round(size, 2)}


_init()
