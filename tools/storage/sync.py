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
    """增量 K 线 (含 daily_basic + 6 个指数) — sync_incremental 是全局操作, 不按 codes 过滤"""
    from .store import sync_incremental
    n = sync_incremental(target_date=target_date)
    print(f"  ✅ K 线: {n} 条新增")
    return n


def action_stock_basic(codes: list[str]) -> int:
    """股票基础信息 (行业/名称) — 一次性, 每月跑 1 次"""
    from .store import sync_stock_basic
    n = sync_stock_basic()
    print(f"  ✅ stock_basic: {n} 行")
    return n


def action_financials(codes: list[str], period: str | None = None) -> int:
    """财务 (5 季度全市场) — 季报出后跑 1 次"""
    from .store import sync_financials
    if period:
        n = sync_financials(period, codes=codes if codes else None)
        print(f"  ✅ financials {period}: {n} 行")
        return n
    # 默认拉最近 5 季度
    from datetime import datetime
    today = datetime.now()
    quarters = []
    # 季报出表规则: Q1 4月底, Q2 8月底, Q3 10月底, Q4 4月底次年
    candidates = [
        (today.year - 1, "1231"),
        (today.year - 1, "0630"),
        (today.year, "0331"),
        (today.year - 2, "1231"),
        (today.year - 2, "0630"),
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
    """
    from ..sources.eastmoney import _build_eps_table
    ok = 0
    sources = {"datacenter_consensus": 0, "tushare_built_ntm": 0, "EMPTY": 0}
    for c in codes:
        try:
            data, source = _build_eps_table(c)
            sources[source] = sources.get(source, 0) + 1
            if data:
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
    2000 积分档可用 (Tushare 官方)
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
    return 0


def detect_stale_flags() -> dict[str, bool]:
    """自动检测哪些 flag 该跑 (2026-09-03 改造, 解决"用户忘记跑 sync"问题)

    返回: {flag_name: True/False} — True 表示该跑
    """
    import duckdb
    from datetime import datetime, timedelta
    from pathlib import Path
    from .store import HISTORY_DIR, DAILY_BASIC_DIR, FIN_DIR, STOCK_BASIC_PARQUET

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
                # 距今天 > 1 天 (考虑周末, 距 2 个工作日仍 OK)
                if gap > 1:
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
        return 0
    print("\n🔍 自动检测结果:")
    for k, v in flags.items():
        mark = "🟡 需跑" if v else "✅ 跳过"
        print(f"  {mark}  --{k}")
    if not any(flags.values()):
        print("\n✨ 全 fresh, 不用 sync")
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
        args.kline, args.stock_basic, args.financials,
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
    if args.stock_basic:
        print("\n[2/7] --stock-basic (股票基础)")
        action_stock_basic(codes)
    if args.financials:
        print("\n[3/7] --financials (财务 5 季度)")
        action_financials(codes, period=args.period)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
