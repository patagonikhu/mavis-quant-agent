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
                             ("obv5", "INTEGER"), ("obv_trend", "INTEGER"),
                             # 2026-09-02 加: 估值 4 列 (Magic Formula 回测用)
                             ("roc", "REAL"),       # Greenblatt ROC % (TTM)
                             ("ey", "REAL"),        # Greenblatt EY % (TTM)
                             ("peg", "REAL"),       # Forward PE / CAGR
                             ("dcf_l", "REAL"),     # DCF r=10% 隐含 L/E3
                             ]:
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


def get_codes_with_obv(min_date: str = "") -> list[str]:
    """返有 obv5/obv_trend 的 codes"""
    sql = "SELECT DISTINCT code FROM analysis_cache WHERE obv5 IS NOT NULL"
    if min_date:
        sql += " AND date_str >= ?"
        rows = _query(sql, (min_date,))
    else:
        rows = _query(sql)
    return [r["code"] for r in rows]


def get_latest_cache_date() -> str | None:
    """返 cache 中最新日期"""
    rows = _query("SELECT MAX(date_str) AS d FROM analysis_cache")
    return rows[0]["d"] if rows and rows[0]["d"] else None


def get_existing_valuation_pairs() -> set[tuple[str, str]]:
    """返 (code, date) 已有 valuation 的对 (roc/ey/peg/dcf_l 任一非空)"""
    rows = _query(
        "SELECT code, date_str FROM analysis_cache "
        "WHERE roc IS NOT NULL OR ey IS NOT NULL OR peg IS NOT NULL OR dcf_l IS NOT NULL"
    )
    return {(r["code"], r["date_str"]) for r in rows}


def get_valuation_coverage() -> dict:
    """返 4 个 valuation 字段覆盖率 {col: count}"""
    out = {"roc": 0, "ey": 0, "peg": 0, "dcf_l": 0}
    for col in out:
        rows = _query(f"SELECT COUNT(*) AS n FROM analysis_cache WHERE {col} IS NOT NULL")
        out[col] = rows[0]["n"] if rows else 0
    return out


def get_codes_since(min_date: str) -> list[str]:
    """返 min_date 之后有 cache 的 code"""
    rows = _query(
        "SELECT DISTINCT code FROM analysis_cache WHERE date_str >= ?",
        (min_date,),
    )
    return [r["code"] for r in rows]


def update_obv_batch(rows: list[dict]) -> int:
    """批量更新 obv/obv5/obv_trend 字段 (2026-09-03 v6.2.1 加, 替代 backfill_obv 直连 db)

    Args:
        rows: list of {"code", "date_str", "obv", "obv5", "obv_trend"}
    Returns:
        写入行数
    """
    if not rows:
        return 0
    try:
        conn = _conn()
        conn.executemany(
            "UPDATE analysis_cache SET obv=?, obv5=?, obv_trend=? "
            "WHERE code=? AND date_str=?",
            [(r["obv"], r["obv5"], r["obv_trend"], r["code"], r["date_str"]) for r in rows],
        )
        conn.commit()
        n = conn.total_changes
        conn.close()
        return n
    except Exception as e:
        print(f"  ⚠️ update_obv_batch 失败: {e}", file=__import__("sys").stderr)
        return 0


def get_boll_bpct(code: str, dates: list[str]) -> dict[str, float]:
    """返 code 在指定 dates 里的 boll_bpct"""
    if not dates:
        return {}
    placeholders = ",".join(["?"] * len(dates))
    rows = _query(
        f"SELECT date_str, boll_bpct FROM analysis_cache "
        f"WHERE code=? AND date_str IN ({placeholders})",
        (code, *dates),
    )
    return {r["date_str"]: r["boll_bpct"] for r in rows}


def get_boll_bpct_all() -> list[tuple[str, str, float]]:
    """返所有 (code, date_str, boll_bpct)"""
    rows = _query(
        "SELECT code, date_str, boll_bpct FROM analysis_cache "
        "WHERE boll_bpct IS NOT NULL ORDER BY code, date_str"
    )
    return [(r["code"], r["date_str"], r["boll_bpct"]) for r in rows]


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


def _calc_signals_for_code(code: str, full: bool, batch_size: int, step: int):
    """Phase1: 找最老的缺失段（最多 batch_size 根），只算那一段。

    策略：
    - 扫全量 K 线（5年），找所有 stale 日期
    - 取最老的连续缺失段，最多 batch_size 根
    - 计算时往前加 120 根缠论上下文缓冲
    """
    import time as _t
    t0 = _t.time()
    try:
        from tools.storage.store import DataStore
        ctx = DataStore.get_ctx(code)
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

        from tools.analysis.analysis_engine import AnalysisEngine
        history = AnalysisEngine().analyze_history(ctx, compute_dates)
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
                 scope: str = "tech",  # 'tech' | 'all' | 'portfolio' | 'codes'
                 timeout: int = 600,
                 workers: int = 2,
                 batch_size: int = 250,
                 full: bool = False) -> dict:
    """预热 analysis_cache.db (sync_data --cache 唯一入口)

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
    else:
        CODES = _load_tech_codes()
        print(f"科技股: {len(CODES)} 只 (申万行业筛选 ∩ 本地K线)")

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
        futs = {ex.submit(_calc_signals_for_code, code, full, batch_size, 1): code
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
