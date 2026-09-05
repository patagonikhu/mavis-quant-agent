#!/usr/bin/env python3
"""
tools/sync_data.py — 唯一 sync 入口 (2026-09-03 改造)

设计原则:
  1. 唯一入口, 7 个 flag 正交控制, 互不耦合
  2. 默认只跑 --kline (增量 K 线, 最常用)
  3. 其他 (financials/eps/fflow/cache/stock-basic/meta) 都要显式开
  4. 范围: --watchlist (默认) / --all / --codes 002371 300750
  5. 5 个分析 skill (t-analyze/t-bb-obv/t-near-low/t-magic/t-backtest) 全改"只读",
     缺数据时直接报"请先 /t-sync", 不再偷偷调 sync

历史: 之前 sync 逻辑散落在 5+ 文件:
  - tools/sync_watchlist_fresh.py (148 行, dump_one 有递归子进程 bug)
  - tools/kline_store.py (sync_incremental/sync_stock_basic/sync_financials)
  - tools.storage.sync --cache
  - 5 个 batch 脚本里偷偷调 sync_incremental
现在全部走这里, 旧 sync_watchlist_fresh.py 在 v6.0 删除。

用法:
  bash tools/with_venv.sh python -m tools.sync                       # 默认: watchlist K 线
  bash tools/with_venv.sh python -m tools.sync --all                 # 全市场 K 线
  bash tools/with_venv.sh python -m tools.sync --codes 002371 300750 # 指定
  bash tools/with_venv.sh python -m tools.sync --financials          # + 5 季度财务
  bash tools/with_venv.sh python -m tools.sync --eps                 # + EPS 机构预期
  bash tools/with_venv.sh python -m tools.sync --fflow               # + 主力资金流
  bash tools/with_venv.sh python -m tools.sync --cache               # + signal_cache 缓存
  bash tools/with_venv.sh python -m tools.sync --stock-basic         # + 股票基础信息
  bash tools/with_venv.sh python -m tools.sync --meta                # + 板块/事件
  bash tools/with_venv.sh python -m tools.sync --status              # 看现状, 不拉
"""
import sys
import os
import json
import time
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

WATCHLIST = ROOT / "data" / "watchlist.json"


def _get_codes(scope: str, codes_arg: list[str], all_market: bool) -> list[str]:
    """解析 --codes / --watchlist / --all, 返 code 列表 (纯数字, 不带后缀)"""
    if codes_arg:
        return [c.zfill(6) for c in codes_arg]
    if all_market:
        import pandas as pd
        from .store import STOCK_BASIC_PARQUET
        if not STOCK_BASIC_PARQUET.exists():
            print(f"  ⚠️ stock_basic.parquet 不存在, 先跑 --stock-basic")
            return []
        df = pd.read_parquet(STOCK_BASIC_PARQUET)
        return df["ts_code"].str.split(".").str[0].tolist()
    # default: watchlist
    if not WATCHLIST.exists():
        return []
    wl = json.load(open(WATCHLIST))
    return [s["code"] for s in wl.get("stocks", [])]


# ============================================================
# 7 个正交行为 (按 flag 调用)
# ============================================================

def action_kline(codes: list[str], target_date: str | None = None) -> int:
    """增量 K 线 (含 6 个指数) — sync_incremental 是全局操作, 不按 codes 过滤"""
    from .store import sync_incremental
    n = sync_incremental(target_date=target_date)
    print(f"  ✅ K 线: {n} 条新增")
    return n


# v6.2.4 加: stk_factor_pro 替代 daily_basic, 16 列 (含 ps/dv_ratio/free_float_turnover)
STK_FACTOR_PROGRESS = Path("data/history/.stk_factor_progress.json")
STK_FACTOR_FIELDS = (
    "ts_code,trade_date,close,"
    "pe,pe_ttm,pb,ps,ps_ttm,"
    "dv_ratio,dv_ttm,"
    "total_mv,circ_mv,"
    "turnover_rate,turnover_rate_f,volume_ratio,"
    "total_share,float_share"
)


def action_stk_factor(force: bool = False) -> int:
    """重拉 5 季 stk_factor_pro (16 列, 写到 data/history/stk_factor/)

    行为:
      1. 删旧 5 季 daily_basic parquet (force=True 强制, False 智能)
      2. 拉 240 trade_date x 1 API = 8 分钟 (30/分限频, sleep 2.0秒)
      3. 按 trade_date 写季度 parquet (5 季: 25Q3 25Q4 26Q1 26Q2 26Q3)
      4. 进度文件: data/history/.stk_factor_progress.json (断点续跑)

    默认 force=False: 已完成的 trade_date 跳过, 支持中断后接着跑
    """
    from .sources.tushare import get_stk_factor_by_date
    from .store import HISTORY_DIR
    import json, time
    from datetime import datetime, timedelta

    # 1) 加载进度
    progress = {}
    if STK_FACTOR_PROGRESS.exists():
        try:
            progress = json.loads(STK_FACTOR_PROGRESS.read_text())
            print(f"  📋 续跑: 已完成 {len(progress.get('done', []))} 个 trade_date")
        except Exception:
            progress = {}

    # 2) 算要拉的 trade_date 列表 (5 季, 2025Q3 ~ 2026Q3)
    #    从 2025-07-01 (2025Q3 第一天) 到今天
    start_date = datetime(2025, 7, 1)
    today = datetime.now()
    # 用 Tushare trade_cal 拿交易日 (避免节假日)
    from .sources.tushare import _safe_call
    cal_data, _ = _safe_call(
        "trade_cal", exchange="SSE", is_open="1",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=today.strftime("%Y%m%d"),
        fields="cal_date",
    )
    all_dates = sorted([c["cal_date"] for c in (cal_data or [])])
    done = set(progress.get("done", []))
    pending = [d for d in all_dates if d not in done]
    print(f"  📅 总交易日: {len(all_dates)}  已完成: {len(done)}  待拉: {len(pending)}")

    if not pending and not force:
        print(f"  ✅ 全部完成, 无需重拉")
        return 0

    # 3) 删旧 stk_factor parquet (5 季) — 写 STK_FACTOR_DIR, 不动 HISTORY_DIR (K 线!)
    # v6.2.4 修: 之前用错 HISTORY_DIR 覆盖了 K 线 5 季 (大事故)
    from .store import STK_FACTOR_DIR
    STK_FACTOR_DIR.mkdir(parents=True, exist_ok=True)
    for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]:
        old = STK_FACTOR_DIR / f"{q}.parquet"
        if old.exists():
            old.unlink()
            print(f"  🗑️  删旧 stk_factor/{old.name}")

    # 4) 按 trade_date 逐个拉 (30/分限频 → sleep 2s)
    #    按季写盘
    quarter_of = lambda d: (
        "2025Q3" if "202507" <= d[:6] <= "202509" else
        "2025Q4" if "202510" <= d[:6] <= "202512" else
        "2026Q1" if "202601" <= d[:6] <= "202603" else
        "2026Q2" if "202604" <= d[:6] <= "202606" else
        "2026Q3"
    )

    quarter_data: dict[str, list[dict]] = {q: [] for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]}
    t0 = time.time()
    n_total = 0
    for i, d in enumerate(pending, 1):
        data, status = get_stk_factor_by_date(d)
        if not data:
            print(f"  ⚠️ {d}: 拉取失败 ({status})")
            continue
        q = quarter_of(d)
        quarter_data[q].extend(data)
        done.add(d)
        n_total += len(data)
        # 进度
        if i % 10 == 0 or i == len(pending):
            elapsed = time.time() - t0
            speed = i / elapsed if elapsed > 0 else 0
            eta = (len(pending) - i) / speed if speed > 0 else 0
            print(f"  📡 [{i}/{len(pending)}] {d}: {len(data)} 只  "
                  f"速度 {speed:.2f}/s  ETA {eta/60:.1f} 分钟")
            # 写进度
            STK_FACTOR_PROGRESS.write_text(json.dumps({
                "done": sorted(done),
                "updated_at": datetime.now().isoformat(),
            }))
        time.sleep(2.0)  # 30/分限频

    # 5) 按季写 parquet (写到 STK_FACTOR_DIR, 不再写 HISTORY_DIR)
    import pandas as pd
    for q, rows in quarter_data.items():
        if not rows:
            continue
        df = pd.DataFrame(rows)
        # 强制 16 列 schema (即使某列全是 None)
        for col in STK_FACTOR_FIELDS.split(","):
            if col not in df.columns:
                df[col] = None
        df = df[STK_FACTOR_FIELDS.split(",")]
        # 去重 (同一 trade_date 不应该出现多次)
        df = df.drop_duplicates(subset=["ts_code", "trade_date"])
        out = STK_FACTOR_DIR / f"{q}.parquet"
        df.to_parquet(out, index=False)
        print(f"  ✅ 写 stk_factor/{q}: {len(df)} 行, 16 列")

    print(f"  🎉 完成: {n_total} 行 总耗时 {(time.time()-t0)/60:.1f} 分钟")
    return n_total


def action_stock_basic(codes: list[str]) -> int:
    """股票基础信息 (行业/名称) — 一次性, 每月跑 1 次"""
    from .store import sync_stock_basic
    n = sync_stock_basic()
    print(f"  ✅ stock_basic: {n} 行")
    return n


def action_financials(codes: list[str], period: str | None = None) -> int:
    """财务 (5 季度全市场) — 季报出后跑 1 次

    v6.2.4 改: 默认 scope 强制全市场 (codes=空时拉 5555 只)
    原因: Magic 排名需要全市场 5555 只财务, 之前默认 watchlist=121 只缺 95% 数据
    """
    from .store import sync_financials, _fin_load_all_codes
    # financials 强制全市场 (忽略 --watchlist, 除非用户显式 --codes)
    # 但尊重用户传的 --codes (小批调试用)
    if not codes:
        all_codes = _fin_load_all_codes()
        if all_codes:
            codes = all_codes
            print(f"  🔄 financials 默认全市场: {len(codes)} 只 (覆盖 --watchlist 默认)")
    if period:
        n = sync_financials(period, codes=codes if codes else None)
        print(f"  ✅ financials {period}: {n} 行")
        return n
    # 默认拉最近 5 年 (10 季: 5 H1 + 5 全年) — Magic 回测需要
    from datetime import datetime
    today = datetime.now()
    quarters = []
    # 季报出表规则: Q1 4月底, Q2 8月底, Q3 10月底, Q4 4月底次年
    # 5 年回测需要每年的 H1 + 全年 (10 季, 跨 6 个自然年)
    candidates = [
        (today.year - 1, "1231"),
        (today.year - 1, "0630"),
        (today.year, "0331"),
        (today.year - 2, "1231"),
        (today.year - 2, "0630"),
        (today.year - 3, "1231"),
        (today.year - 3, "0630"),
        (today.year - 4, "1231"),
        (today.year - 4, "0630"),
        (today.year - 5, "1231"),
    ]
    # 去重
    seen = set()
    for y, m in candidates:
        p = f"{y}{m}"
        if p not in seen:
            quarters.append(p)
            seen.add(p)
    total = 0
    for p in quarters:
        n = sync_financials(p, codes=codes if codes else None)
        total += n
        print(f"  ✅ financials {p}: {n} 行")
    return total


def action_eps(codes: list[str]) -> int:
    """EPS 机构预期 (东方财富 datacenter RPT_HSF10_RESPREDICT_COUNTSTATISTICS)

    真实接口: tools/fetch/data_fetcher.py::_build_eps_table
    走 datacenter.eastmoney.com (主) → Tushare 自建 NTM (备) → EMPTY

    v6.2.4 改: parquet 写入 data/history/eps/{code}.parquet (跟 financials 同目录)
    """
    from .sources.eastmoney import _build_eps_table
    from .caches.eps import EPS_DIR, _write_parquet
    ok = 0
    sources = {"datacenter_consensus": 0, "tushare_built_ntm": 0, "EMPTY": 0}
    for c in codes:
        try:
            data, source = _build_eps_table(c)
            sources[source] = sources.get(source, 0) + 1
            if data:
                out = EPS_DIR / f"{c}.parquet"
                out.parent.mkdir(parents=True, exist_ok=True)
                _write_parquet(out, c, data)
                ok += 1
        except Exception as e:
            print(f"  ⚠️ {c} EPS 拉取失败: {e}")
    print(f"  ✅ EPS consensus: {ok}/{len(codes)} 只 (datacenter {sources['datacenter_consensus']} / "
          f"tushare 自建 {sources.get('tushare_built_ntm', 0)} / EMPTY {sources['EMPTY']})")
    return ok


def action_fflow(codes: list[str]) -> int:
    """主力资金流 (Tushare money_flow) — 最近 10-20 日

    真实接口: tools/fetch/tushare_fetcher.py::get_money_flow
    字段: buy_lg_*/buy_elg_* (大单/特大单买), sell_lg_*/sell_elg_* (卖), net_mf_amount (净流入, 万元)
    5000 积分档可用 (Tushare 官方)
    """
    from ..sources.tushare import get_money_flow
    ok = 0
    for c in codes:
        try:
            data, status = get_money_flow(c)
            if data:
                ok += 1
        except Exception as e:
            print(f"  ⚠️ {c} fflow 拉取失败: {e}")
    print(f"  ✅ fflow: {ok}/{len(codes)} 只")
    return ok


def action_cache(codes: list[str]) -> int:
    """signal_cache 缓存 (analysis_cache.db) — 分析结果缓存, 跑回测前必须先有

    2026-09-03 v6.2.1 改: 走 tools.storage.caches.analysis.warmup_cache
    之前 subprocess 调 tools.storage.sync --cache (已删, 合并到 caches/)
    """
    from tools.storage.caches.analysis import warmup_cache
    scope = "codes" if codes else "tech"
    warmup_cache(codes=codes, scope=scope)
    return 0


def action_meta(codes: list[str]) -> int:
    """板块 / 事件元数据 — 一次性"""
    from tools.batch import refresh_sectors
    print("  ℹ️  调 refresh_sectors (sectors/events 同步)...")
    try:
        sys.argv = ["refresh_sectors"]
        refresh_sectors.main()
    except (SystemExit, AttributeError) as e:
        print(f"  ⚠️ refresh_sectors 不可用: {e}")
    return 0


# ============================================================
# Status
# ============================================================

def action_status() -> int:
    """看现状, 不拉任何数据"""
    from .store import print_status_report
    print_status_report()


def print_data_freshness_summary() -> None:
    """打印本地各数据源最新一天 (2026-09-04 加, 用户问"最新是哪天")

    一次性 SQL 查 parquet, 0 网络
    """
    import duckdb
    from pathlib import Path
    from .store import HISTORY_DIR, STK_FACTOR_DIR, FIN_DIR
    from .caches.eps import EPS_DIR

    def _max_date(path: Path) -> str:
        if not path.exists():
            return "—"
        try:
            # K 线 / daily_basic / financials 都是 1 次 SQL 拿最新 trade_date
            if path.is_dir():
                files = list(path.glob("*.parquet"))
                if not files:
                    return "—"
                # 取文件 mtime 最新的
                latest_file = max(files, key=lambda p: p.stat().st_mtime)
                df = duckdb.execute(f"SELECT MAX(trade_date) FROM read_parquet('{latest_file}')").fetchone()
            else:
                # 单文件 (e.g. stock_basic.parquet)
                df = duckdb.execute(f"SELECT MAX(trade_date) FROM read_parquet('{path}')").fetchone()
            return str(df[0]) if df and df[0] else "—"
        except Exception as e:
            return f"❌ {type(e).__name__}"

    # K 线 / daily_basic 走 parquet, EPS 走 parquet, financials 走 max end_date, stock_basic 走 mtime
    def _max_end_date(path: Path) -> str:
        if not path.exists():
            return "—"
        try:
            files = list(path.glob("*.parquet")) if path.is_dir() else [path]
            if not files:
                return "—"
            latest_file = max(files, key=lambda p: p.stat().st_mtime)
            # financials 用 end_date 字段 (不是 trade_date)
            df = duckdb.execute(f"SELECT MAX(end_date) FROM read_parquet('{latest_file}')").fetchone()
            return str(df[0]) if df and df[0] else "—"
        except Exception:
            return "—"

    def _file_mtime(path: Path) -> str:
        if not path.exists():
            return "—"
        from datetime import datetime
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")

    print("\n📊 本地数据新鲜度 (最新一天):")
    print(f"  K线 (OHLCV)        : {_max_date(HISTORY_DIR)}")
    print(f"  stk_factor (估值)  : {_max_date(STK_FACTOR_DIR)}")
    print(f"  financials (季报)  : {_max_end_date(FIN_DIR)}")
    # EPS parquet 特殊: 列名是 year/year_mark/eps/..., 没 trade_date
    # 用 fetched_at (Tushare 写入时间) 代替 "最新"
    try:
        if EPS_DIR.exists() and list(EPS_DIR.glob("*.parquet")):
            df = duckdb.execute(f"SELECT MAX(fetched_at) FROM read_parquet('{EPS_DIR}/*.parquet')").fetchone()
            from datetime import datetime
            ts = int(df[0]) if df and df[0] else 0
            if ts > 1e12:  # ms → s
                ts = ts / 1000
            print(f"  EPS (机构预期)     : {datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')}")
        else:
            print(f"  EPS (机构预期)     : 空")
    except Exception as e:
        print(f"  EPS (机构预期)     : ❌ {type(e).__name__}")
    # fflow 走 parquet 但目录结构不一样, 简单写
    fflow_dir = Path("data/cache/fflow")
    if fflow_dir.exists():
        try:
            files = list(fflow_dir.glob("*.parquet"))
            if files:
                latest = max(files, key=lambda p: p.stat().st_mtime).stem
                print(f"  fflow (资金流)     : {latest}")
            else:
                print(f"  fflow (资金流)     : 空")
        except Exception:
            print(f"  fflow (资金流)     : —")
    else:
        print(f"  fflow (资金流)     : 未拉过")
    # stock_basic 特殊: 没有 trade_date, 看 mtime
    sb_path = Path("data/history/stock_basic/stock_basic.parquet")
    print(f"  stock_basic (静态) : mtime {_file_mtime(sb_path)}")
    return 0


def detect_stale_flags() -> dict[str, bool]:
    """自动检测哪些 flag 该跑 (2026-09-03 改造, 解决"用户忘记跑 sync"问题)

    返回: {flag_name: True/False} — True 表示该跑
    """
    import duckdb
    from datetime import datetime, timedelta
    from pathlib import Path
    from .store import HISTORY_DIR, STK_FACTOR_DIR, FIN_DIR, STOCK_BASIC_PARQUET

    today = datetime.now()
    today_str = today.strftime("%Y%m%d")
    flags = {
        "kline": False,         # K线距今天 > 1 天
        "stock_basic": False,   # 距上次 > 30 天
        "financials": False,    # 缺最新季
        "eps": False,           # 暂不自动 (用户主动)
        "fflow": False,         # 暂不自动
        "cache": False,         # 暂不自动
        "meta": False,          # 暂不自动
    }

    # 1. K线 (每天必跑, 距今天 > 1 天就拉)
    try:
        files = list(HISTORY_DIR.glob("*.parquet"))
        if files:
            max_d = duckdb.execute(
                f"SELECT MAX(trade_date) FROM read_parquet('{HISTORY_DIR}/*.parquet')"
            ).fetchone()[0]
            max_d_clean = max_d.replace("-", "") if max_d else ""
            if max_d_clean:
                last = datetime.strptime(max_d_clean, "%Y%m%d")
                gap = (today - last).days
                # 距今天 >= 1 天 → 拉 (9/3 vs 9/2 gap=1, 也要拉)
                # 但 daily_basic 取最新一天, gap=0 当天已拉够, 不重跑
                if gap >= 1:
                    flags["kline"] = True
        else:
            flags["kline"] = True  # 没数据
    except Exception:
        flags["kline"] = True  # 异常 → 拉

    # 2. stock_basic (1 月 1 次, 距上次 > 30 天)
    try:
        if not STOCK_BASIC_PARQUET.exists():
            flags["stock_basic"] = True
        else:
            import os
            mtime = datetime.fromtimestamp(STOCK_BASIC_PARQUET.stat().st_mtime)
            if (today - mtime).days > 30:
                flags["stock_basic"] = True
    except Exception:
        pass

    # 3. financials (缺最新季: 季报出后 ~1 周, 取最新季 < 90 天前)
    try:
        files = sorted(FIN_DIR.glob("*.parquet"))
        if not files:
            flags["financials"] = True
        else:
            latest = files[-1]
            # 最新季文件 stem 形如 2026Q2
            q_label = latest.stem  # "2026Q2"
            year, q = q_label[:4], q_label[5:]
            # 季末
            quarter_end = {
                "Q1": "0331", "Q2": "0630", "Q3": "0930", "Q4": "1231",
            }[q]
            end_dt = datetime.strptime(f"{year}{quarter_end}", "%Y%m%d")
            if (today - end_dt).days > 100:  # 100 天没新季
                flags["financials"] = True
    except Exception:
        pass

    return flags


def action_auto(force: bool = False, quiet: bool = False) -> int:
    """自动检测 stale 并跑 (--auto / 默认行为)

    Args:
        force: True = 强刷, 不看检测结果
        quiet: True = 只打印会跑什么, 不真跑
    """
    flags = detect_stale_flags()
    if force:
        flags = {k: True for k in flags}
        if not quiet:
            print("  ⚠️  --force, 全部 7 个 flag 强刷")
    if quiet:
        # dry-run 模式: 也要打印结果 (这是 dry 的全部意义)
        print("\n🔍 自动检测 (dry-run, 不真跑):")
        for k, v in flags.items():
            mark = "🟡 会跑" if v else "✅ 跳过"
            print(f"  {mark}  --{k}")
        print_data_freshness_summary()
        return 0
    print("\n🔍 自动检测结果:")
    for k, v in flags.items():
        mark = "🟡 需跑" if v else "✅ 跳过"
        print(f"  {mark}  --{k}")
    if not any(flags.values()):
        print("\n✨ 全 fresh, 不用 sync")
        print_data_freshness_summary()
        return 0
    # 真跑 (复用 main() 的 flag 逻辑)
    print("\n🚀 跑 stale 的 flag:")
    if flags["kline"]:
        print("  [1/7] --kline")
        action_kline(_last_codes)
    if flags["stock_basic"]:
        print("  [2/7] --stock-basic")
        action_stock_basic(_last_codes)
    if flags["financials"]:
        print("  [3/7] --financials")
        action_financials(_last_codes)
    print_data_freshness_summary()
    return 0


# 全局保存 codes 供 action_auto 用
_last_codes: list[str] = []


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Mavis 唯一 sync 入口 (2026-09-03 改造)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m tools.sync                       # 默认 watchlist K 线
  python -m tools.sync --all --financials    # 全市场 K 线 + 5 季度财务
  python -m tools.sync --codes 002371 --eps  # 单只 + EPS
  python -m tools.sync --status              # 看现状
        """,
    )

    # Scope (3 选 1, 默认 --watchlist)
    scope = parser.add_argument_group("范围 (3 选 1, 默认 --watchlist)")
    scope.add_argument("--watchlist", action="store_true", default=True,
                       help="watchlist 全部 (默认)")
    scope.add_argument("--all", action="store_true",
                       help="全市场 (5549 只, 慢)")
    scope.add_argument("--codes", nargs="+", metavar="CODE",
                       help="指定代码列表 (例: --codes 002371 300750)")

    # 7 个正交 flag (全部默认关, 显式开才跑, 符合"正交控制"原则)
    # 但: 不传任何 sync flag → 默认走 --auto (智能检测 stale, 只跑需跑的)
    actions = parser.add_argument_group("Sync 行为 (7 个正交, 全部默认关; 不传任何 flag → 自动检测)")
    actions.add_argument("--kline", action="store_true",
                         help="增量 K 线 + daily_basic + 6 指数")
    actions.add_argument("--stk-factor", action="store_true",
                         help="[v6.2.4 重构] 重拉 stk_factor_pro 16 列 (替代旧 daily_basic, 8 分钟)")
    actions.add_argument("--stock-basic", action="store_true",
                         help="股票基础信息 (行业/名称)")
    actions.add_argument("--financials", action="store_true",
                         help="财务 5 季度 (fina_indicator_vip 全市场)")
    actions.add_argument("--eps", action="store_true",
                         help="EPS 机构预期 (datacenter.consensus)")
    actions.add_argument("--fflow", action="store_true",
                         help="主力资金流 (Tushare.money_flow)")
    actions.add_argument("--cache", action="store_true",
                         help="signal_cache 缓存 (analysis_cache.db)")
    actions.add_argument("--meta", action="store_true",
                         help="板块 / 事件元数据")
    actions.add_argument("--all-data", action="store_true",
                         help="[一键] --kline --stock-basic --financials 一起跑 (最常用)")

    # Misc
    parser.add_argument("--status", action="store_true",
                        help="看现状, 不拉任何数据")
    parser.add_argument("--auto", action="store_true", default=False,
                        help="[默认行为] 自动检测 stale flag, 只跑需跑的 (解决'忘记 sync'问题)")
    parser.add_argument("--auto-force", action="store_true",
                        help="自动检测 + 强刷所有 stale flag")
    parser.add_argument("--auto-dry", action="store_true",
                        help="只打印会跑什么, 不真跑 (--auto + 试运行)")
    parser.add_argument("--period", help="财务指定季度 (例: 20251231, 跟 --financials 一起用)")

    args = parser.parse_args()

    # 缓存 codes 给 action_auto
    global _last_codes

    # Status 短路
    if args.status:
        return action_status()

    # --auto / --auto-force / --auto-dry 短路 (忽略其他 flag)
    if args.auto or args.auto_force or args.auto_dry:
        # 先解析 codes
        if args.codes:
            _last_codes = [c.zfill(6) for c in args.codes]
        elif args.all:
            _last_codes = _get_codes("all", None, all_market=True)
        else:
            _last_codes = _get_codes("watchlist", None, all_market=False)
        scope = f"watchlist {len(_last_codes)} 只" if not (args.codes or args.all) else (
            f"指定 {len(_last_codes)} 只" if args.codes else f"全市场 {len(_last_codes)} 只"
        )
        mode = "auto-force" if args.auto_force else ("auto-dry" if args.auto_dry else "auto")
        print(f"=== Mavis sync_data (scope: {scope}) [{mode}] ===")
        return action_auto(
            force=args.auto_force,
            quiet=args.auto_dry,
        )

    # Scope 解析
    if args.codes and args.all:
        print("❌ --codes 跟 --all 互斥, 二选一")
        return 1
    if args.codes:
        codes = [c.zfill(6) for c in args.codes]
        scope_label = f"指定 {len(codes)} 只"
    elif args.all:
        codes = _get_codes("all", None, all_market=True)
        scope_label = f"全市场 {len(codes)} 只"
    else:
        codes = _get_codes("watchlist", None, all_market=False)
        scope_label = f"watchlist {len(codes)} 只"
    _last_codes = codes

    # 没传任何 sync flag + 不是 --status / --auto → 默认走 --auto (智能检测)
    any_sync_flag = any([
        args.kline, args.stk_factor, args.stock_basic, args.financials,
        args.eps, args.fflow, args.cache, args.meta, args.all_data,
    ])
    if not any_sync_flag:
        print(f"=== Mavis sync_data (scope: {scope_label}) [默认 --auto 智能检测] ===")
        return action_auto(force=False, quiet=False)

    print(f"=== Mavis sync_data (scope: {scope_label}) ===")
    start = time.time()

    # --all-data 是个 alias, 相当于 --kline --stock-basic --financials
    if args.all_data:
        args.kline = True
        args.stock_basic = True
        args.financials = True

    # 按 7 个 flag 调用 (顺序: kline → stock_basic → financials → eps → fflow → cache → meta)
    if args.kline:
        print("\n[1/7] --kline (增量 K 线)")
        action_kline(codes)
    if args.stk_factor:
        # v6.2.4 重构: 替代 daily_basic, 16 列, 8 分钟重拉
        print("\n[1.5/7] --stk-factor (重拉 16 列 stk_factor_pro, 替代 daily_basic)")
        action_stk_factor(force=False)
    if args.stock_basic:
        print("\n[2/7] --stock-basic (股票基础)")
        action_stock_basic(codes)
    if args.financials:
        print("\n[3/7] --financials (财务 5 季度)")
        # v6.2.4 改: financials 强制全市场 (传 None, action_financials 内部会拉 5555 只)
        # 只有用户显式 --codes 时才用 codes (小批调试)
        fin_codes = codes if args.codes else None
        action_financials(fin_codes, period=args.period)
    if args.eps:
        print("\n[4/7] --eps (机构预期)")
        action_eps(codes)
    if args.fflow:
        print("\n[5/7] --fflow (主力资金)")
        action_fflow(codes)
    if args.cache:
        print("\n[6/7] --cache (signal_cache)")
        action_cache(codes)
    if args.meta:
        print("\n[7/7] --meta (板块/事件)")
        action_meta(codes)

    # 全部 flag 都没开 + 也不是 --status → 给个友好提示
    if not any([args.kline, args.stock_basic, args.financials,
                args.eps, args.fflow, args.cache, args.meta]):
        print("\n💡 没指定任何行为, 看 --help 选 flag")
        print("   最常用: python -m tools.sync --all-data")

    elapsed = time.time() - start
    print(f"\n=== 完成, 耗时 {elapsed:.1f} 秒 ===")
    print_data_freshness_summary()
    return 0


if __name__ == "__main__":
    sys.exit(main())
