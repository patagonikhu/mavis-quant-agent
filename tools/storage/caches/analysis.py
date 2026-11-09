"""
analysis_cache.py — 威科夫阶段 SQLite 缓存

Schema (2026-11-09 瘦身: 25 列 → 5 列, 442MB → 6.7MB):
  保留 (5):
    code / date_str            (2: 主键)
    kline_hash                 (1: 完整性哈希, 复权/数据修正后能识别过期)
    wy_stage                   (1: 威科夫阶段 — 唯一有价值的内容)
    updated_at                 (1: meta)

  **cache 只为威科夫而存在** —— 用户 2026-11-09 明示「只关心威科夫和简单的财务信息」。
  (财务不在本 db: 走 data/history/financials/*.parquet + DataStore, 与 cache 无关)

  2026-11-09 删 20 列 (全仓 0 引用 / 数据恒空):
    威科夫 12 列: wy_score / wy_sub_event_count / wy_30d_pct / wy_has_{spring,lps,evr,sos,
      compression,trend_pullback,markup_entry,distribution_start,utad}
      → 唯一读方 batch_backtest 的子事件信号依赖它们, 已一并下线 (传参即报错)
    估值 2 列: roc / ey → 全表 276 万行 0 非空, 唯一读方 tools/btf/ 已删
    技术指标 6 列: ma20_slope / rsi6 / macd_dif / macd_dea / macd_bar_delta / vol_ratio
      → 全仓 0 引用: 报告里的这些值走实时 compute_factor_history, 不读 cache;
        cache 唯一消费方 batch_backtest 只读 wy_stage

  另删冗余索引 uq_code_date (PRIMARY KEY (code,date_str) 已自动建同列唯一索引)

  **warmup 改跑 WyckoffStrategy** (2026-11-09): 之前只跑 TechnicalStrategy →
  raw 里没有 wyckoff key → wy_stage 恒写 '?' (实测 96.8% 行是垃圾, 只有 87,830 行真值,
  冻结在 2024-12~2026-10)。改跑威科夫后每行都有真 stage。
  实测单票 1204 根 K 线: Wyckoff 0.09s vs Technical 0.01s; 全市场 ≈ 8.4 分钟单线程。

  历史: 2026-09-22 删 26 列 (10 wy_* + 11 信号列 + 3 ma_dev + 2 boll_*)
        2026-10-19 用户做威科夫胜率回测, 加回 13 列 (sub_event 9 + 主 stage/score/count/30d)
        2026-11-06 DROP 5 个死列: obv / obv5 / obv_trend + peg / dcf_l
        2026-11-09 DROP 20 列 + 冗余索引 (见上), 442MB → 324MB → 6.7MB
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
# 2026-11-06: 唯一 db 就是项目根 data/analysis_cache.db
# 2026-11-09: 孤儿 tools/data/analysis_cache.db (500 行 / 44 列, 带已下线的缠论 chan_1buy/chan_3buy
#   等死列) 已删 —— safe_fs._is_protected 改成按项目根相对路径匹配后不再误护 (0 引用, 纯残留)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analysis_cache (
    code            TEXT NOT NULL,
    date_str        TEXT NOT NULL,
    kline_hash      TEXT NOT NULL,   -- 完整性哈希 (复权/数据修正后能识别过期)

    -- 威科夫阶段 (2026-10-19 加, 2026-11-09 只留这一列)
    -- 用途: 威科夫阶段回测 (batch_backtest.py 唯一消费方)
    wy_stage            TEXT,        -- 主 stage (Accumulation / Markup / Distribution / ?)

    -- meta
    updated_at      REAL,

    PRIMARY KEY (code, date_str)
);

-- 2026-11-09 删: uq_code_date 索引
--   PRIMARY KEY (code, date_str) 已自动建同列唯一索引 (sqlite_autoindex_*),
--   这个 UNIQUE INDEX 是完全重复的 (63.5MB 白占) —— 查询计划走 autoindex, 性能不变
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
                             # 2026-10-19 加: 威科夫阶段列 (2026-11-09 只留这一列)
                             ("wy_stage",                  "TEXT"),
                             ]:
            if col not in existing:
                c.execute(f"ALTER TABLE analysis_cache ADD COLUMN {col} {typedef}")

        # 自动迁移: 删死列 (若还在)
        # 2026-11-06: OBV (10-27 删) / PEG·DCF (11-02 删) —— 代码早已不写不读,
        #   物理列却还留在 275 万行的 db 里: 白占空间 + 给未来 query 挖坑
        # 2026-11-09: 再删 14 列 (威科夫 12 列 + 估值 2 列) + 冗余索引 uq_code_date
        #   威科夫 12 列: 全仓 0 引用, 且 warmup 只跑 TechnicalStrategy → 写入值恒 '?'/0
        #   估值 2 列: 全表 276 万行 0 非空 (回填脚本从没成功跑过), 唯一读方 tools/btf/ 已删
        # 2026-11-09 再删 6 列技术指标 (ma20_slope/rsi6/macd_dif/macd_dea/
        #   macd_bar_delta/vol_ratio): 全仓 0 引用 —— 报告走实时 compute_factor_history,
        #   cache 唯一消费方 batch_backtest 只读 wy_stage。删完 cache 只剩「威科夫 + 哈希」
        _dead_columns = (
            "obv", "obv5", "obv_trend", "peg", "dcf_l",
            "roc", "ey",
            "wy_score", "wy_sub_event_count", "wy_30d_pct",
            "wy_has_spring", "wy_has_lps", "wy_has_evr", "wy_has_sos",
            "wy_has_compression", "wy_has_trend_pullback", "wy_has_markup_entry",
            "wy_has_distribution_start", "wy_has_utad",
            "ma20_slope", "rsi6", "macd_dif", "macd_dea",
            "macd_bar_delta", "vol_ratio",
        )
        for col in _dead_columns:
            if col in existing:
                c.execute(f"ALTER TABLE analysis_cache DROP COLUMN {col}")

        # 冗余索引: PRIMARY KEY (code, date_str) 已自动建同列唯一索引
        # 2026-11-09 删 (63.5MB 白占, 查询计划走 autoindex 性能不变)
        c.execute("DROP INDEX IF EXISTS uq_code_date")


def _kline_hash(kline: list[dict]) -> str:
    if not kline:
        return "empty"
    closes = [k.get("close", 0) for k in kline[-5:]]
    return hashlib.md5(f"{closes}_{len(kline)}".encode()).hexdigest()[:12]


# ── result → row ──────────────────────────────────────────

# 2026-09-22 删: _ma_dev, _boll_bpct, _boll_bwidth (随 wy/chan/boll 列一并删除)
#   get_boll_bpct / get_boll_bpct_all 同步删 (下游已替换为实时算)


# 2026-11-09 删: 威科夫 12 列提取 (score / sub_event_count / 30d_pct / 9 个 has_* flag)
#   原因: 全仓 0 引用; 且 warmup 只跑 TechnicalStrategy → raw 里没有 wyckoff key,
#         写入值恒为 '?'/0 (实测 96.8% 行是垃圾值) —— 属于「恒定坏档」该删不是该修
#   只留 wy_stage (batch_backtest.py 的阶段回测唯一消费方)


def _extract_wyckoff(result) -> dict:
    """从分析结果提威科夫阶段 (只留 wy_stage)

    **两种结果形状都要认** (2026-11-09 修):
    - `analyze()` 返回 AnalysisResult 对象 → 阶段在 `result.raw["wyckoff"]["stage"]`
    - `analyze_history()` 返回**普通 dict** → 阶段在**顶层** `result["stage"]`
      (见 analysis_engine.py:287-294: `results[date] = {score, signals, summary, **daily_out, ...}`,
       daily_out 就是日线 wyckoff_judge 的输出, stage 在最外层, **没有 wyckoff 嵌套层**)

    之前只认 .raw → analyze_history 的结果全被当成 "?" (实测 96.8% 行是垃圾值)。
    判定仍在生产侧算 (wyckoff_judge), 这里只读不算。
    """
    # 1) analyze_history 的 dict 形状: 顶层 stage
    if isinstance(result, dict) and result.get("stage"):
        return {"wy_stage": result["stage"]}
    # 2) AnalysisResult 对象: raw["wyckoff"]["stage"]
    raw = getattr(result, "raw", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("raw")
    wy = (raw or {}).get("wyckoff", {}) or {}
    # stage (日线, 跟 /t-wyckoff-a2m 一致); raw 无 wyckoff key 时为 "?"
    return {"wy_stage": wy.get("stage", "?")}


def _result_to_row(code: str, date_str: str,
                   kline: list[dict], result) -> dict[str, Any]:
    # 2026-09-22 改: 删 wy_* / boll_* / ma_dev 全部赋值
    # 原因: 真值由 AnalysisEngine.analyze() 实时算, cache 只存技术指标 + 估值
    # 2026-10-19 改: 重新加 wy_* 13 列 (用户回测威科夫 A→M 胜率, cache 必须能查)
    # 2026-11-09 改: 只留 wy_stage 一列 (其余 12 列全仓 0 引用 + 写入值恒垃圾)
    # 2026-11-09 再改: 删 6 个技术指标列 (ma20_slope/rsi6/macd_*/vol_ratio)
    #   → 全仓 0 引用: 报告里的这些值走实时 compute_factor_history, 不读 cache;
    #     cache 唯一消费方 batch_backtest 只读 wy_stage。删完 cache 只剩「威科夫 + 完整性哈希」
    wy = _extract_wyckoff(result)

    return {
        "code": code,
        "date_str": date_str,
        "kline_hash": _kline_hash(kline),
        # 威科夫阶段 (batch_backtest 阶段回测用)
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


# 2026-11-09 删: get_existing_valuation_pairs / get_valuation_coverage
#   两函数只服务 roc/ey 两列 (唯一读方 backfill_roc_ey_cache.py + tools/btf/ 已一并删除)
#   roc/ey 全表 276 万行 0 非空, 回填脚本从没成功跑过 → 恒空档, 删


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
        #   eps 也无需 (估值列 2026-11-09 已整档删除)
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
            AnalysisEngine, WyckoffStrategy,
        )
        # 2026-09-22 改: 只跑真存 db 的 strategy (跳过 5 个省 60-70% 时间)
        # 2026-10-27: OBV strategy 已删除
        # 2026-11-09 改: 改成**只跑 WyckoffStrategy**
        #   原因: cache 只剩 wy_stage 一列有用 (技术指标列已删), 而 TechnicalStrategy
        #   不产 wyckoff raw → 之前写的 wy_stage 恒 '?' (实测 96.8% 行是垃圾)。
        #   实测单票 1204 根 K 线: Wyckoff 0.09s / Technical 0.01s,
        #   全市场 5591 只 ≈ 8.4 分钟单线程 (4 并发 ~2-3 分钟) —— 换真数据完全值得。
        #   判定口径与生产一致 (raw['wyckoff']['stage'] = 日线阶段), 规则下沉。
        history = AnalysisEngine(strategies=[WyckoffStrategy]) \
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
