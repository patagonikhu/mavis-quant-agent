"""
backfill_magic_cache.py — 给 analysis_cache.db 4 列估值 (roc / ey / peg / dcf_l) backfill 1 年

数据源 (全本地, 0 网络):
  - data/history/financials/{period}.parquet  (TTM EBIT / NWC / FA / netdebt)
  - data/history/daily_basic/*.parquet        (每天 PE / market_cap)
  - data/cache/eps/{code}.json                 (datacenter 拉的 EPS 预期)

窗口: 1 年 (2025-08-25 → 2026-09-01, 248 天)
股票池: 1923 科技股 (跟 magic_top20 一致)
写表:   analysis_cache (38 列, 4 列新增)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import sqlite3

_PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT))
os.chdir(_PROJECT)

from tools.analysis.signal_cache import _DB, _init
from tools.kline_store import (
    DataStore, _to_ts_code,
)
# v6.0 改: 不再 import sync_incremental (sync 走 /t-sync, 本脚本 0 网络)
from tools.analysis.valuation import (
    find_full_year_financials, calc_ey_at_date, calc_roc_at_date,
)
from tools.factors.valuation.multi import PegFactor, DcfFactor
from tools.eps_consensus_cache import EPS_DIR


# ============================================================
# 工具函数
# ============================================================

def _load_daily_basic_window(codes: list[str], dates: list[str]) -> dict[str, dict[str, float]]:
    """批量读 1 年 daily_basic (1 次 SQL, 6ms × 1205 票)

    2026-09-03 v6.1.1 改: 走 DataStore.load_all_daily_basic, pandas filter
    不再直接 duckdb.execute + read_parquet

    Returns: {code: {date: {total_mv, pe_ttm}}}
    """
    if not codes or not dates:
        return {}
    try:
        from tools.kline_store import DataStore, _to_ts_code
        codes_ts = {_to_ts_code(c) for c in codes}
        dates_clean = {d.replace("-", "")[:8] for d in dates}
        df = DataStore.load_all_daily_basic()
        if df.empty:
            return {}
        df = df[df["ts_code"].isin(codes_ts) & df["trade_date"].isin(dates_clean)]
        out: dict[str, dict[str, float]] = {}
        for _, r in df.iterrows():
            code = r["ts_code"][:6]  # 600262.SH → 600262
            d = r["trade_date"]
            if isinstance(d, str):
                d = d.replace("-", "")[:8]
            out.setdefault(code, {})[d] = {
                "total_mv": float(r["total_mv"]) if r["total_mv"] is not None else None,
                "pe_ttm":   float(r["pe_ttm"])   if r["pe_ttm"]   is not None else None,
                "close":    float(r["close"])    if r["close"]    is not None else None,
            }
        return out
    except Exception as e:
        print(f"  ⚠️ _load_daily_basic_window 失败: {e}")
        return {}


def _load_financials_window(codes: list[str]) -> dict[str, list[dict]]:
    """批量读 financials parquet (1 次 SQL)

    Returns: {code: list[financial_row]}  按 end_date 升序
    """
    if not codes:
        return {}
    try:
        from tools.kline_store import DataStore, _to_ts_code
        codes_ts = {_to_ts_code(c) for c in codes}
        df = DataStore.load_all_financials()
        if df.empty:
            return {}
        df = df[df["ts_code"].isin(codes_ts) & (df["fetch_status"] == "ok")]
        df = df.sort_values("end_date")
        out: dict[str, list[dict]] = {}
        for _, r in df.iterrows():
            code = r["ts_code"][:6]
            out.setdefault(code, []).append({
                "end_date": r["end_date"],
                "ebit":    float(r["ebit"])    if r["ebit"]    is not None else None,
                "networking_capital": float(r["networking_capital"]) if r["networking_capital"] is not None else None,
                "fixed_assets": float(r["fixed_assets"]) if r["fixed_assets"] is not None else None,
                "netdebt": float(r["netdebt"]) if r["netdebt"] is not None else None,
                "industry": r["industry"] or "",
            })
        return out
    except Exception as e:
        print(f"  ⚠️ _load_financials_window 失败: {e}")
        return {}


def _load_eps_window(codes: list[str]) -> dict[str, list[dict]]:
    """读本地 EPS cache (走 datacenter 已拉的, 没就 None)

    Returns: {code: eps_table (list[dict])}
    """
    out: dict[str, list[dict]] = {}
    import json
    for c in codes:
        path = EPS_DIR / f"{c}.json"
        if path.exists() and path.stat().st_size > 10:
            try:
                out[c] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                out[c] = []
        else:
            out[c] = []
    return out


# ============================================================
# 单只票 1 年估值
# ============================================================

def _calc_4cols_for_code(
    code: str,
    dates: list[str],
    db_window: dict[str, float],     # {date: {total_mv, pe_ttm, close}} (单只票, 1 个 code)
    fin_data: list[dict],          # 跨多季 financials
    eps_table: list[dict],         # EPS 预期
) -> dict[str, tuple]:
    """单只票, 算 dates 范围内每天的 (roc, ey, peg, dcf_l) 4 元组

    Returns: {date: (roc, ey, peg, dcf_l)}  缺数据 = None
    """
    out: dict[str, tuple] = {}
    peg_factor = PegFactor()
    dcf_factor = DcfFactor()
    for d in dates:
        # 找 ≤ d 的最新全年 financials
        fin = find_full_year_financials(fin_data, d) if fin_data else None
        # 取当天 daily_basic (db_window 已是 {date: {total_mv, pe_ttm, close}})
        db_d = db_window.get(d) or {}
        mc_wan = db_d.get("total_mv")

        # 1) ROC (只要 financials, 不需要 daily_basic)
        roc = None
        if fin:
            magic = calc_roc_at_date([fin], d)
            roc = magic.get("roc")

        # 2) EY (要 daily_basic.market_cap)
        ey = None
        if fin and mc_wan:
            magic = calc_ey_at_date([fin], d, mc_wan)
            ey = magic.get("ey")

        # 3) PEG (要 EPS 预期 + 当前价)
        peg = None
        if eps_table and len(eps_table) >= 4 and db_d.get("close"):
            r = peg_factor(df=None, eps_table=eps_table, current_price=db_d["close"]) or {}
            p = r.get("PEG_真实")
            if isinstance(p, (int, float)) and 0 < p < 1000:  # 过滤 PEG=999 (g 缺失)
                peg = p

        # 4) DCF L/E3 (要 EPS 预期 + market_cap_yi)
        dcf_l = None
        if eps_table and len(eps_table) >= 4 and mc_wan:
            market_cap_yi = mc_wan / 1e4
            close = db_d.get("close") or 0
            r = dcf_factor(df=None, eps_table=eps_table, current_price=close, market_cap_yi=market_cap_yi) or {}
            d10 = r.get("r_10%", {})
            if d10.get("L/E3(每share)") is not None and d10["L/E3(每share)"] > 0:
                dcf_l = d10["L/E3(每share)"]

        out[d] = (roc, ey, peg, dcf_l)
    return out


def _safe_get(d: dict, key: str, default=None):
    """dict.get 加 None 检查"""
    v = d.get(key)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    return v


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="analysis_cache 4 列估值 backfill")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--start", default="20250825", help="起始 YYYYMMDD (默认 1 年前 daily_basic)")
    parser.add_argument("--end",   default="20260901", help="结束 YYYYMMDD")
    parser.add_argument("--codes", nargs="*", default=None, help="指定股票 (默认全科技股 1923)")
    args = parser.parse_args()

    _init()  # 触发 schema 迁移

    # 1) 股票池 (默认科技股 1923, 跟 magic_top20 一致, 排除 8 类 EXCLUDED_INDUSTRIES)
    if args.codes:
        codes = list(args.codes)
    else:
        from tools.kline_store import _fin_load_tech_codes
        codes = _fin_load_tech_codes()
    print(f"📊 股票池: {len(codes)} 只 (科技股)")

    # 2) 日期范围
    from tools.kline_store import DataStore
    df = DataStore.load_all_daily_basic()
    if df.empty:
        dates = []
    else:
        mask = (df["trade_date"] >= args.start) & (df["trade_date"] <= args.end)
        dates = sorted(df.loc[mask, "trade_date"].unique().tolist())
        dates = [d.replace("-", "")[:8] for d in dates]
    print(f"📅 日期范围: {dates[0] if dates else 'N/A'} → {dates[-1] if dates else 'N/A'} ({len(dates)} 天)")

    # 3) 预读 daily_basic (1 次 SQL, 估 100ms)
    print(f"🔄 批量读 daily_basic ({len(codes)} 票 × {len(dates)} 天)...")
    t0 = time.time()
    db_window = _load_daily_basic_window(codes, dates)
    print(f"   耗时 {(time.time()-t0):.1f}s, 覆盖 {len(db_window)} 票")

    # 4) 预读 financials (1 次 SQL)
    print(f"🔄 批量读 financials ({len(codes)} 票 × 3 期)...")
    t0 = time.time()
    fin_window = _load_financials_window(codes)
    print(f"   耗时 {(time.time()-t0):.1f}s, 覆盖 {len(fin_window)} 票")

    # 5) 预读 EPS (本地 cache, 没就空)
    print(f"🔄 读 EPS cache (本地 json)...")
    eps_window = _load_eps_window(codes)
    n_eps = sum(1 for v in eps_window.values() if v)
    print(f"   覆盖 {n_eps}/{len(codes)} 票 (没 EPS 是常见, 机构不覆盖小盘)")

    # 6) 检查 db 已有的 (code, date_str) 对, 跳过
    con = sqlite3.connect(str(_DB))
    existing_pairs = set(
        (r[0], r[1]) for r in con.execute(
            "SELECT code, date_str FROM analysis_cache "
            "WHERE roc IS NOT NULL OR ey IS NOT NULL OR peg IS NOT NULL OR dcf_l IS NOT NULL"
        ).fetchall()
    )
    print(f"📂 db 已有 4 列非空: {len(existing_pairs):,} 对 (跳过重算)")

    # 7) 算所有 (code, date) 对
    todo = []
    for code in codes:
        for d in dates:
            if (code, d) in existing_pairs:
                continue
            todo.append((code, d))
    print(f"🔢 待算: {len(todo):,} 对")

    if not todo:
        print("✅ 全部已算完, 退出")
        return 0

    # 8) 并发算
    print(f"⚙️  {args.workers} worker 并发算 4 列...")
    t_start = time.time()
    done = 0
    write_batch: list[tuple] = []
    WRITE_EVERY = 5000

    def process_one(item):
        code, d = item
        # 拿单只票单天的 daily_basic (db_window 已经是 {code: {date: {total_mv,...}}})
        db_d = (db_window.get(code) or {}).get(d) or {}
        fin = fin_window.get(code, [])
        eps = eps_window.get(code, [])
        return (code, d, _calc_4cols_for_code(code, [d], {d: db_d}, fin, eps).get(d))

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(process_one, item): item for item in todo}
        for fut in as_completed(futs):
            code, d, vals = fut.result()
            if vals is None:
                continue
            roc, ey, peg, dcf_l = vals
            write_batch.append((code, d, roc, ey, peg, dcf_l))
            done += 1
            if done % 5000 == 0:
                _flush(con, write_batch)
                write_batch = []
                elapsed = time.time() - t_start
                rate = done / elapsed
                eta = (len(todo) - done) / rate if rate > 0 else 0
                print(f"   {done:,}/{len(todo):,} ({done/len(todo)*100:.0f}%) | "
                      f"{elapsed:.0f}s elapsed | {rate:.0f}/s | ETA {eta:.0f}s", flush=True)

    if write_batch:
        _flush(con, write_batch)
    con.commit()
    con.close()

    elapsed = time.time() - t_start
    print(f"✅ 完成: {done:,} 对, 耗时 {elapsed:.0f}s ({done/elapsed:.0f}/s)")

    # 9) 统计
    con = sqlite3.connect(str(_DB))
    n_roc = con.execute("SELECT COUNT(*) FROM analysis_cache WHERE roc IS NOT NULL").fetchone()[0]
    n_ey  = con.execute("SELECT COUNT(*) FROM analysis_cache WHERE ey  IS NOT NULL").fetchone()[0]
    n_peg = con.execute("SELECT COUNT(*) FROM analysis_cache WHERE peg IS NOT NULL").fetchone()[0]
    n_dcf = con.execute("SELECT COUNT(*) FROM analysis_cache WHERE dcf_l IS NOT NULL").fetchone()[0]
    print(f"📊 覆盖率: roc={n_roc:,} ey={n_ey:,} peg={n_peg:,} dcf_l={n_dcf:,}")
    con.close()
    return 0


def _flush(con, batch: list[tuple]):
    """批量 UPDATE 4 列到 analysis_cache"""
    con.executemany("""
        UPDATE analysis_cache
        SET roc = ?, ey = ?, peg = ?, dcf_l = ?
        WHERE code = ? AND date_str = ?
    """, [(roc, ey, peg, dcf_l, code, d) for (code, d, roc, ey, peg, dcf_l) in batch])
    con.commit()


if __name__ == "__main__":
    import os
    sys.exit(main())
