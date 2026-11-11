#!/usr/bin/env python3
"""tools/storage/sync.py — Mavis 唯一 sync 入口 (2026-09-03 改造)

用途: 同步 Tushare 数据 (K 线 / 估值因子 / 股票基础 / 财务 / 概念板块 / ETF).
      写盘唯一路径, 业务层禁止直连 db/网络.

设计原则:
  1. 唯一入口, 8 个正交 flag, 互不耦合
  2. 默认行为 = --auto: 智能检测 stale flag, 只跑需跑的 (解决"忘记 sync"问题)
  3. 显式传 flag = 强跑那一项 (无论 stale 不 stale)
  4. 范围: --watchlist (watchlist 全部) / --all (全市场, 默认) / --codes 002371 300750 (指定)
  5. 2026-11-04 删: --eps 范围互斥 (EPS 一致预期整档已删, 只剩 Tushare 单一合法源)
  6. 3 个 read-only skill (t-analyze/t-finance-earnings-blowout/t-factor-5y)
     缺数据时直接报"请先 /t-sync-data", 不再偷偷调 sync

正交 flag (8 个, 全部默认关):
  --kline         增量 K 线 + daily_basic + 6 指数 (每天)
  --stk-factor    stk_factor_pro 16 列估值因子 (5 季 1 次, 8 分钟)
  --stock-basic   股票基础信息 (行业/名称, 30 天 1 次)
  --financials    财务 5 季度 (fina_indicator_vip 全市场)
  --cache         signal_cache 缓存 (analysis_cache.db, 跑回测前)
  --etf           ETF 场内基金 (fund_basic 月级 + fund_daily 日增量, 默认随 --auto 跑)
  --sw-index      申万行业指数 (index_classify + sw_daily, 给行业算威科夫 stage, 默认随 --auto 跑)
  --all-data      [一键] --kline --stock-basic --financials 一起跑 (最常用)

2026-11-11 删 --ths (THS 同花顺概念板块):
  - 死段: 2026-10-14 commit dcd0d8e 删了 config/ths_whitelist.json (改成动态推导),
    但 --ths 还在找那个已删的文件 -> 白名单读不到, 什么也不拉
  - 期间 store.py 重构又删了 THS_HISTORY_DIR / _append_records_target -> --ths 直接 ImportError
  - data/history/ths/kline/ 里 18 份概念数据停在 2026-10-08 (纯存档, 不再更新)
  - 需要题材(概念)维度时再用 tushare 的 ths_index/ths_daily (封装还在 sources/tushare.py)

申万行业指数 (2026-11-11 加, --sw-index):
  行业分类: index_classify(src='SW2021', level='L2' → 134 个二级行业) → data/history/sw_index/sw_index.parquet
  行业日线: sw_daily(ts_code='801xxx.SI') → data/history/sw_index/kline/<index_code>.parquet (1 行业 1 文件)
  同步逻辑: 检测缺口 (本地 min/max, 0 网络) → 批量拉 1 次 (不带 ts_code, 服务端给最近 10 交易日 × 全部 439 指数)
            → 校验覆盖 → 没被批量覆盖的按 ts_code 单独补 (1 行业 1 次)
  请求数:   日常增量 1 次 / 首次或大缺口 1+N 次
  已知:     134 个 L2 里 124 个在 sw_daily 批量覆盖内; 10 个要单独拉, 其中 9 个申万没发数据 (记入无数据名单),
            1 个 (801011 林业Ⅱ) 2021-12 就停更 (按"该指数可得上限"判定, 不会反复空拉)

范围 flag (3 选 1, 默认 --all):
  --watchlist / --all / --codes CODE [CODE ...]

行为 flag:
  --auto          [默认行为] 自动检测 stale flag, 只跑需跑的
  --auto-force    自动检测 + 强刷所有 stale flag
  --status        看现状, 不拉任何数据
  --period        财务指定季度 (例: --period 20251231, 跟 --financials 一起用)

ETF (2026-10-09 加, 2026-10-14 接入 auto):
  基金基本信息: fund_basic(market='E') 月级 (30 天缓存) → data/history/etf/etf_basic.parquet
  K 线增量:     fund_daily(trade_date=date) 日级 (1次/天) → data/history/etf/kline/<ts_code>.parquet
  首次 bootstrap (~2 年历史): ~517 次请求, 之后每天 1 次.
  候选池:       全市场 ETF (etf_basic 全表, 2252 只) + fund_type 过滤 → 扫描器自行取用
                (2026-11-06 删 config/etf_whitelist.json 手工白名单).

2026-10-28 删 --fflow (主力资金流整体删除, 详见 docs/fflow-removal-assessment.md)

网络请求统计 (2026-09-18 加):
  每次 sync 末尾自动输出本次实际调用的 tushare API 次数 (按 API 分组, 调用次数降序):
    🌐 网络请求统计 (总计 8 次):
       index_daily                  6 次
       daily                        1 次
       fina_indicator_vip           1 次
  用途: 0 网络排查 + 性能监控 + sync --auto 全 fresh 时应该是 0.

历史 (v6 改造前 sync 散落):
  - tools/sync_watchlist_fresh.py (148 行, dump_one 递归子进程 bug)
  - tools/kline_store.py (sync_incremental/sync_stock_basic/sync_financials)
  - 5 个 batch 脚本偷偷调 sync_incremental
  → 全部走这里, 旧 sync_watchlist_fresh.py 在 v6.0 删除.

用法:
  bash tools/with_venv.sh python -m tools.storage.sync                          # 默认 = --auto (智能检测)
  bash tools/with_venv.sh python -m tools.storage.sync --all-data               # 一键 K 线 + 股票基础 + 财务 (最常用)
  bash tools/with_venv.sh python -m tools.storage.sync --codes 002371 300750    # 指定代码
  bash tools/with_venv.sh python -m tools.storage.sync --status                 # 看现状
  bash tools/with_venv.sh python -m tools.storage.sync --auto-force             # 自动检测 + 强刷
  bash tools/with_venv.sh python -m tools.storage.sync --period 20251231 --financials  # 指定季度财务
"""
import sys
import os
import json
import time
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

WATCHLIST = ROOT / "config" / "watchlist.json"


def _get_codes(codes_arg: list[str], all_market: bool) -> list[str]:
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
# parquet footer 快读 (2026-11-11 加, 申万行业指数缺口检测用)
# ============================================================

def _local_date_range(path: Path) -> tuple[str | None, str | None]:
    """从 parquet footer 读 (min_trade_date, max_trade_date), 毫秒级.

    只读 metadata 不读数据页 → 比 pd.read_parquet 快 100 倍以上.
    文件不存在 / 没有 statistics / 读不了 → (None, None)
    """
    try:
        import pyarrow.parquet as pq
        meta = pq.read_metadata(path)
        mins: list[str] = []
        maxes: list[str] = []
        for rg_idx in range(meta.num_row_groups):
            rg = meta.row_group(rg_idx)
            for c_idx in range(rg.num_columns):
                col = rg.column(c_idx)
                if col.path_in_schema != "trade_date":
                    continue
                if col.statistics is None:
                    return None, None   # statistics disabled → 读不了
                mins.append(str(col.statistics.min))
                maxes.append(str(col.statistics.max))
                break
        if not mins:
            return None, None
        return min(mins), max(maxes)
    except Exception:
        return None, None


def _local_max_trade_date(path: Path) -> str | None:
    """本地 parquet 的 max(trade_date), 毫秒级 (只读 footer, 不读数据页)"""
    return _local_date_range(path)[1]


# ============================================================
# 正交行为 (按 flag 调用)
# ============================================================

def action_kline(target_date: str | None = None) -> int:
    """增量 K 线 (含 6 个指数) — sync_incremental 是全局操作, 不按 codes 过滤"""
    from .store import sync_incremental
    n = sync_incremental(target_date=target_date)
    print(f"  ✅ K线: {n} 条新增")
    return n


# v6.2.4 加: stk_factor_pro 替代 daily_basic, 17 列 (含 ps/dv_ratio/free_float_turnover)
# v6.2.5 改造: done 进度塞 parquet metadata, 不再单独维护 .stk_factor_progress.json
# STK_FACTOR_FIELDS 搬到 caches/stk_factor_history.py, 这里只保留兼容性 import alias (供 action_status 读 max date)


def action_stk_factor(force: bool = False) -> int:
    """重拉 5 季 stk_factor_pro (17 列, 写到 data/history/stk_factor/)

    行为 (v6.2.5 改造: done 进度塞 parquet metadata, 不用 json):
      1. 删旧 5 季 stk_factor parquet (force=True 强制, False 智能)
      2. 拉 5 季 trade_date x 1 API = ~5.5 分钟 (200/分限频, sleep 0.3秒; 实测峰值 196/分无限频)
      3. **每 10 个 trade_date 就把当季累积的 rows 写盘 + 更新 metadata** (崩了不丢数据)
      4. 5 季按 trade_date 写季度 parquet: 25Q3 25Q4 26Q1 26Q2 26Q3
         每季文件 metadata.b'done_dates' = JSON 数组 of YYYYMMDD (本季已完成)
         (老 .stk_factor_progress.json 进度文件已删)

    默认 force=False: 已完成的 trade_date 跳过, 支持中断后接着跑
    """
    from .sources.tushare import get_stk_factor_by_date
    from .store import STK_FACTOR_DIR
    from .caches.stk_factor_history import (
        write_stk_factor_quarter,
        read_all_done_dates,
        STK_FACTOR_FIELDS,
    )
    import time
    from datetime import datetime

    # 1) 加载进度 (从 5 季 parquet metadata 合并, 0 外部文件)
    done = read_all_done_dates()
    # 2026-11-02 加: 每个 trade_date 实际有多少只代码在本地 (修 "按日期记 done 导致代码永久缺失")
    try:
        from .caches.stk_factor_history import read_code_count_by_date
        code_counts = read_code_count_by_date()
    except Exception:
        code_counts = {}
    print(f"  📋 续跑: 已完成 {len(done)} 个 trade_date (从 parquet metadata)")

    # 2) 算要拉的 trade_date 列表 (5 季, 2025Q3 ~ 2026Q3)
    start_date = datetime(2025, 7, 1)
    today = datetime.now()
    from .sources.tushare import _safe_call
    cal_data, _ = _safe_call(
        "trade_cal", exchange="SSE", is_open="1",
        start_date=start_date.strftime("%Y%m%d"),
        end_date=today.strftime("%Y%m%d"),
        fields="cal_date",
    )
    all_dates = sorted([c["cal_date"] for c in (cal_data or [])])
    pending = [d for d in all_dates if d not in done]

    # 2026-11-02 加: 覆盖度体检 —— 找出 "标了 done 但代码数明显偏少" 的日期
    # 场景: Tushare 某天只先发沪市 (实测 20261102 全市场只返 2504 行: 沪1904/深246/北354),
    #       旧逻辑 done.add(date) 把整日标完成 → 深市代码永远补不上, 市值/PE 长期用旧价。
    # 判定: 用已拉日期的代码数中位数作基准, 明显低于基准 (低于 70%) 的日期标记为 "半拉", 重新拉。
    partial_dates: set[str] = set()
    if code_counts:
        counts = sorted(code_counts.values())
        median = counts[len(counts) // 2] if counts else 0
        if median:
            for d, n in code_counts.items():
                if d in done and n < median * 0.7:
                    partial_dates.add(d)
            if partial_dates:
                pending = [d for d in all_dates if d not in done or d in partial_dates]
                print(f"  ⚠️ 检出 {len(partial_dates)} 个「半拉」日期 (代码数 < 中位数 70%), 重新拉: "
                      f"{sorted(partial_dates)[:5]}{'...' if len(partial_dates) > 5 else ''}")

    print(f"  📅 总交易日: {len(all_dates)}  已完成: {len(done)}  待拉: {len(pending)}")

    if not pending and not force:
        print(f"  ✅ 全部完成, 无需重拉")
        return 0

    # 3) force 模式才删旧 5 季 parquet
    # 2026-10-14 改: 季度列表由 quarters_since(start_date, today) 动态算, 季度切换 0 维护
    from .caches.quarter_utils import quarters_since
    all_quarters = quarters_since(start_date.strftime("%Y%m%d"), today.strftime("%Y%m%d"))
    if force:
        STK_FACTOR_DIR.mkdir(parents=True, exist_ok=True)
        for q in all_quarters:
            old = STK_FACTOR_DIR / f"{q}.parquet"
            if old.exists():
                old.unlink()
                print(f"  🗑️  删旧 stk_factor/{old.name}")
        done = set()  # force 重置

    # 4) 按 trade_date 逐个拉, 按季累积
    from .caches.quarter_utils import _quarter_of as _q_of  # 复用同一函数
    from .caches.stk_factor_history import read_quarter_done_dates
    quarter_data: dict[str, list[dict]] = {q: [] for q in all_quarters}
    # 修 bug: 先读旧 parquet metadata, 避免覆盖历史 done_dates
    # (原代码 {q: set() for q in ...} 导致每次 sync 写 metadata 只含本次增量, 下次重复拉)
    quarter_done: dict[str, set[str]] = {
        q: read_quarter_done_dates(q) for q in all_quarters
    }
    t0 = time.time()
    n_total = 0
    # 2026-11-02 加: 覆盖度基准 (本次拉到的最大代码数), 用来判断某天是不是只拉到一部分
    max_codes_seen = max(code_counts.values()) if code_counts else 0
    for i, d in enumerate(pending, 1):
        data, status = get_stk_factor_by_date(d)
        # 修 bug: EMPTY 可能因 tushare 限流假阳, 重试 1 次 (睡 5s)
        if not data and status == "EMPTY":
            time.sleep(5)
            data, status = get_stk_factor_by_date(d)
        if not data:
            print(f"  ⚠️ {d}: 拉取失败 ({status})")
            # 2026-11-02 改: **不再** done.add(d)
            # 原逻辑把拉取失败的日期标成 done → 永久不再重试, 该日所有代码都缺失。
            # 现在不写 done, 下次 sync 会重试。
            continue
        q = _q_of(d)
        quarter_data[q].extend(data)
        n_total += len(data)
        # 2026-11-02 加: 只有覆盖度达标 (>= 基准 70%) 才算这天完成, 否则留着下次补
        # 场景: Tushare 某天只先发沪市, 深市后发 —— 这时不能标 done, 否则深市永远缺失
        if max_codes_seen and len(data) < max_codes_seen * 0.7:
            print(f"  ⚠️ {d}: 只拉到 {len(data)} 只 (基准 {max_codes_seen}), 判定为半拉, 不标 done (下次补)")
            continue
        quarter_done[q].add(d)
        done.add(d)
        # 进度: 每 10 个或最后一个, 把所有非空季落盘 (崩了不丢)
        if i % 10 == 0 or i == len(pending):
            elapsed = time.time() - t0
            speed = i / elapsed if elapsed > 0 else 0
            eta = (len(pending) - i) / speed if speed > 0 else 0
            print(f"  📡 [{i}/{len(pending)}] {d}: {len(data)} 只  "
                  f"速度 {speed:.2f}/s  ETA {eta/60:.1f} 分钟")
            # merge 写盘 (含 metadata)
            for q, rows in quarter_data.items():
                if not rows:
                    continue
                write_stk_factor_quarter(q, rows, done_dates=sorted(quarter_done[q]))
        # 2026-11-03 改: sleep 2.0 → 0.3 (200/分)
        # 旧值 2.0s 是按 doc_id=328 的 "5000积分 30次/分" 设的, 但实测本账号跑 680 次
        # (峰值 196/分) 零限频 —— 账号实际在 doc_id=290 的 200/分 档。
        # 0.3s = 200/分, 与项目其它 tushare 调用点 (store.py) 一致, 留 1.5x 余量。
        # 注意: 限流表现为**抛异常** (msg 含 "每分钟最多"), 不是返空; 返空 = 非交易日。
        time.sleep(0.3)

    # 5) 最终再写一次 (保险)
    for q, rows in quarter_data.items():
        if not rows:
            continue
        write_stk_factor_quarter(q, rows, done_dates=sorted(quarter_done[q]))
        print(f"  ✅ 写 stk_factor/{q}: {len(rows)} 行, "
              f"done={len(quarter_done[q])}, "
              f"{len(STK_FACTOR_FIELDS.split(','))} 列")

    print(f"  🎉 完成: {n_total} 行 总耗时 {(time.time()-t0)/60:.1f} 分钟")
    return n_total


def action_stock_basic() -> int:
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

    from datetime import datetime, date
    from pathlib import Path as _Path

    today = date.today()

    # 季报截止日 (ann_date 最晚): Q1=4/30, Q2=8/31, Q3=10/31, Q4=次年4/30
    # 披露窗口: 季末后 ~120 天内仍可能有修订版，超过则定稿
    WINDOW_DAYS = 120

    def _quarter_end_and_deadline(year: int, q: int) -> tuple[date, date]:
        """返回 (季末日, 披露截止日)"""
        if q == 1:
            return date(year, 3, 31),  date(year, 4, 30)
        elif q == 2:
            return date(year, 6, 30),  date(year, 8, 31)
        elif q == 3:
            return date(year, 9, 30),  date(year, 10, 31)
        else:
            return date(year, 12, 31), date(year + 1, 4, 30)

    def _period_str(year: int, q: int) -> str:
        return {1: f"{year}0331", 2: f"{year}0630",
                3: f"{year}0930", 4: f"{year}1231"}[q]

    # 枚举过去 5 年所有季度 (20 季), 只取"已到披露截止日"的
    fin_dir = _Path("data/history/financials")
    local_files = {f.stem for f in fin_dir.glob("*.parquet")} if fin_dir.exists() else set()

    def _quarter_label(year: int, q: int) -> str:
        return f"{year}Q{q}"

    to_run: list[tuple[str, str]] = []   # [(period, reason)]
    for yr in range(today.year - 5, today.year + 1):
        for q in range(1, 5):
            _, deadline = _quarter_end_and_deadline(yr, q)
            if deadline > today:
                continue  # 还没到截止日, 数据未完整, 跳过
            label = _quarter_label(yr, q)
            period_s = _period_str(yr, q)
            days_since_deadline = (today - deadline).days
            if label not in local_files:
                to_run.append((period_s, "缺失"))
            elif days_since_deadline <= WINDOW_DAYS:
                to_run.append((period_s, "窗口期增量"))
            # else: 超出窗口期且本地已有 → 跳过

    if not to_run:
        print("  ✅ financials: 所有季度已是最新, 跳过")
        return 0

    print(f"  📋 financials 待跑 {len(to_run)} 个季度:")
    for p, reason in to_run:
        print(f"     {p} ({reason})")

    total = 0
    for p, _ in to_run:
        n = sync_financials(p, codes=codes if codes else None)
        total += n
        print(f"  ✅ financials {p}: {n} 行")
    return total


def action_cache(codes: list[str]) -> int:
    """signal_cache 缓存 (analysis_cache.db) — 分析结果缓存, 跑回测前必须先有

    2026-09-03 v6.2.1 改: 走 tools.storage.caches.analysis.warmup_cache
    之前 subprocess 调 tools.storage.sync --cache (已删, 合并到 caches/)
    """
    from tools.storage.caches.analysis import warmup_cache
    # 2026-09-22 改: 默认 scope='all' (回测场景需要全市场, tech 子集不全)
    scope = "codes" if codes else "all"
    warmup_cache(codes=codes, scope=scope)
    return 0


# ============================================================
# 申万行业指数 (2026-11-11 加) —— --sw-index
# ============================================================

# 行业指数历史起点 (7 年, 跟项目其他数据源对齐)
SW_INDEX_HISTORY_START = "20191113"
# 申万没发数据的行业: 记入名单后 N 天内不重试 (省网络)
SW_NO_DATA_RETRY_DAYS = 30
# 指数停更判定: 本地最新日期落后今天超过 N 天 → 认定停更, 记 "可得上限"
SW_STOPPED_DAYS = 30


def _sw_meta_path() -> Path:
    return Path("data/history/sw_index/sw_index_meta.json")


def _sw_meta_load() -> dict:
    """读 sw_index_meta.json → {no_data, history_start, history_max}

    - no_data:       申万没发数据的行业 (记发现日期, 冷却期内不重试)
    - history_start: 每个行业的"历史地板" = 想要/实际能拿到的最早日期 (缺口检测的下界)
    - history_max:   停更指数的可得上限 (缺口检测的"天花板", 没有就是今天)
    """
    meta = {"no_data": {}, "history_start": {}, "history_max": {}}
    p = _sw_meta_path()
    if not p.exists():
        return meta
    try:
        loaded = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            for k in meta:
                if isinstance(loaded.get(k), dict):
                    meta[k] = loaded[k]
    except Exception:
        pass
    return meta


def _sw_meta_save(meta: dict) -> None:
    p = _sw_meta_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def action_sw_index(level: str = "L2") -> int:
    """申万行业指数 (index_classify + sw_daily) —— 行业威科夫 stage 的数据源

    2026-11-11 加. **单一逻辑, 没有"首次/以后"两套** (用户明确要求):
      1. 行业分类: index_classify(SW2021, level) → L2 134 个二级行业 (30 天缓存)
      2. 检测缺口 (本地 min/max, 0 网络) → 每个行业缺哪段
      3. 批量拉 1 次 (sw_daily 不带 ts_code → 服务端给最近 10 交易日 × 全部 439 指数)
      4. 重新算缺口 → 批量没覆盖的 (历史没拉全 / 服务端缺 / 停更) 按 ts_code 单独补
      5. 落盘 meta (no_data / history_start / history_max)

    请求数: 日常增量 1 次 / 首次或大缺口 1+N 次
    落盘:   data/history/sw_index/sw_index.parquet (行业分类)
            data/history/sw_index/kline/<index_code>.parquet (1 行业 1 文件)
    """
    import pandas as pd
    from datetime import datetime
    from .store import (SW_INDEX_HISTORY_DIR, SW_INDEX_KLINE_DIR,
                        _append_records_target)
    from .sources.tushare import get_sw_index, get_sw_daily

    today = datetime.now().strftime("%Y%m%d")
    meta = _sw_meta_load()
    idx_path = SW_INDEX_HISTORY_DIR / "sw_index.parquet"

    # ---------- 1. 行业分类 (30 天缓存) ----------
    need_classify = True
    if idx_path.exists():
        age_days = (time.time() - idx_path.stat().st_mtime) / 86400
        if age_days < 30:
            try:
                local_level = str(
                    pd.read_parquet(idx_path, columns=["level"])["level"].iloc[0])
                need_classify = (local_level != level)
            except Exception:
                need_classify = True
    if need_classify:
        print(f"  📋 拉申万行业分类 (SW2021, level={level})")
        rows, status = get_sw_index(level=level)
        if not rows:
            print(f"  ⚠️ index_classify 失败: {status}")
            if not idx_path.exists():
                return 0
        else:
            df = pd.DataFrame(rows)
            df["sync_date"] = today
            idx_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(idx_path, index=False)
            print(f"  ✅ 行业分类: {len(df)} 个 (level={level})")

    idx_df = pd.read_parquet(idx_path)
    codes = idx_df["index_code"].astype(str).tolist()
    code_set = set(codes)
    print(f"  📊 申万 {level} 行业: {len(codes)} 个")

    # ---------- 2. 检测缺口 (0 网络) ----------
    local_range: dict[str, tuple[str | None, str | None]] = {}
    for code in codes:
        rng = _local_date_range(SW_INDEX_KLINE_DIR / f"{code}.parquet")
        if rng[0] or rng[1]:
            local_range[code] = rng

    # 自愈: 名单里但本地已有数据 → 移出 (801011 林业Ⅱ 曾被误标成"无数据")
    for code in list(meta["no_data"]):
        if code in local_range:
            del meta["no_data"][code]

    # meta 迁移 (2026-11-11): 早先版本把"本地最早日期"写进了 history_start,
    #   导致头部缺口 (历史没拉全) 永远检测不到 → 拉回策略地板.
    #   若该指数 API 真的没有更早的数据, 下一次全量拉会用真实起点覆盖回来 (只多 1 次请求).
    for code, v in list(meta["history_start"].items()):
        if v and str(v) > SW_INDEX_HISTORY_START:
            meta["history_start"][code] = SW_INDEX_HISTORY_START

    gaps: dict[str, tuple[str, str]] = {}   # code -> (start, end)
    skipped = 0
    for code in codes:
        nd = meta["no_data"].get(code)
        if nd:
            try:
                age = (datetime.now() - datetime.strptime(str(nd), "%Y%m%d")).days
            except Exception:
                age = SW_NO_DATA_RETRY_DAYS
            if age < SW_NO_DATA_RETRY_DAYS:
                skipped += 1
                continue
            meta["no_data"].pop(code, None)    # 过了冷却期 → 重试

        ceiling = meta["history_max"].get(code)      # 停更指数的可得上限
        end = ceiling or today
        floor = meta["history_start"].get(code) or SW_INDEX_HISTORY_START
        rng = local_range.get(code)
        if not rng or not rng[1]:
            gaps[code] = (floor, end)                # 全量
            continue
        lmin, lmax = rng
        if lmax >= end and lmin <= floor:
            continue                                  # 覆盖完整, 无缺口
        start = floor if lmin > floor else str(int(lmax) + 1)
        gaps[code] = (start, end)

    if skipped:
        print(f"  ⏭ {skipped} 个行业在无数据名单里 (冷却 {SW_NO_DATA_RETRY_DAYS} 天内), 跳过")

    if not gaps:
        print(f"  ✅ 申万行业指数已是最新 ({len(codes)} 个), 无缺口")
        _sw_meta_save(meta)
        return 0

    print(f"  🔎 检出 {len(gaps)} 个行业有缺口:")
    for code, (s, e) in list(gaps.items())[:8]:
        print(f"     {code}: 缺 [{s} ~ {e}]")
    if len(gaps) > 8:
        print(f"     ... 还有 {len(gaps) - 8} 个")

    # ---------- 3. 批量拉 1 次 (最近 10 交易日 × 全部指数) ----------
    print("  📡 批量拉 sw_daily (1 次, 服务端给最近 10 交易日 × 全部指数)")
    batch, bstatus = get_sw_daily()
    batch_max = ""
    n_batch = 0
    if batch:
        rows = []
        for r in batch:
            if r.get("ts_code") not in code_set:
                continue
            if "pct_change" in r:
                r["pct_chg"] = r.pop("pct_change")
            rows.append(r)
        if rows:
            n_batch = _append_records_target(rows, SW_INDEX_KLINE_DIR)
            batch_max = max(r["trade_date"] for r in rows)
            n_codes = len({r["ts_code"] for r in rows})
            print(f"  ✅ 批量落盘 {n_batch} 行 (覆盖 {n_codes} 个行业, 最新 {batch_max})")
    else:
        print(f"  ⚠️ 批量拉取返空/失败: {bstatus}")

    # ---------- 4. 批量没覆盖的 → 按 ts_code 单独补 ----------
    n_single = 0
    for code, (s, e) in gaps.items():
        rng = _local_date_range(SW_INDEX_KLINE_DIR / f"{code}.parquet")
        lmin, lmax = rng if rng else (None, None)
        floor = meta["history_start"].get(code) or SW_INDEX_HISTORY_START
        target = meta["history_max"].get(code) or (batch_max or e)
        # 批量只能补最近 10 个交易日 → 头部缺口 (历史没拉全) 仍要单独拉
        tail_ok = lmax is not None and lmax >= target
        head_ok = lmin is not None and lmin <= floor
        if tail_ok and head_ok:
            continue                                  # 批量已补齐
        start = s or SW_INDEX_HISTORY_START
        rows, status = get_sw_daily(ts_code=code, start_date=start)
        if not rows:
            # start_date 过滤太狠 (常见于停更指数) → 退回全量再试一次
            rows, status = get_sw_daily(ts_code=code)
            if not rows:
                print(f"    ⚠️ {code} 无数据 ({status}) → 记入无数据名单")
                meta["no_data"][code] = today
                continue
        for r in rows:
            if "pct_change" in r:
                r["pct_chg"] = r.pop("pct_change")
        n = _append_records_target(rows, SW_INDEX_KLINE_DIR)
        n_single += n
        new_max = max(r["trade_date"] for r in rows)
        # 全量拉 (从策略地板起) → 用 API 真实起点校准 history_start
        #   (若该指数本身没有更早的数据, 这里记的就是它的真实起点, 以后不再反复空拉)
        if start <= SW_INDEX_HISTORY_START:
            meta["history_start"][code] = min(r["trade_date"] for r in rows)
        try:
            stale_days = (datetime.now() - datetime.strptime(new_max, "%Y%m%d")).days
        except Exception:
            stale_days = 0
        if stale_days > SW_STOPPED_DAYS:
            # 指数已停更 → 记可得上限, 以后缺口检测按它算, 不再白拉
            meta["history_max"][code] = new_max
            print(f"    ⛔ {code} 停更 (最新 {new_max}, 落后 {stale_days} 天) → 记可得上限")
        else:
            print(f"    ✅ {code}: {n} 行 (最新 {new_max})")
        time.sleep(0.3)                              # 限频 200ms

    # ---------- 5. 落盘 meta ----------
    _sw_meta_save(meta)

    n_files = len(list(SW_INDEX_KLINE_DIR.glob("*.parquet")))
    print(f"  ✅ 申万行业指数: 批量 {n_batch} 行 + 单拉 {n_single} 行 "
          f"({n_files} 个行业文件, {len(meta['no_data'])} 个无数据, "
          f"{len(meta['history_max'])} 个停更)")
    return n_single


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

    # K 线 / daily_basic 走 parquet, financials 走 max end_date, stock_basic 走 mtime
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

    def _max_date_all_files(path: Path) -> str:
        """目录里**所有** parquet 的 max(trade_date) (多文件目录用, 如 sw_index/kline)"""
        if not path.exists():
            return "—"
        files = list(path.glob("*.parquet"))
        if not files:
            return "—"
        try:
            import duckdb
            expr = ", ".join(f"'{f}'" for f in files)
            df = duckdb.execute(
                f"SELECT MAX(trade_date) FROM read_parquet([{expr}])"
            ).fetchone()
            return str(df[0]) if df and df[0] else "—"
        except Exception as e:
            return f"❌ {type(e).__name__}"

    print("\n📊 本地数据新鲜度 (最新一天):")
    print(f"  K线 (OHLCV)        : {_max_date(HISTORY_DIR)}")
    print(f"  stk_factor (估值)  : {_max_date(STK_FACTOR_DIR)}")
    print(f"  financials (季报)  : {_max_end_date(FIN_DIR)}")
    # 2026-11-11 加: 申万行业指数 (给行业算威科夫 stage 用)
    #   注意不能用 _max_date (它对目录只读 mtime 最新的 1 个文件);
    #   这里是 134 个行业各 1 文件 → 必须全扫
    print(f"  申万行业指数        : {_max_date_all_files(Path('data/history/sw_index/kline'))}")
    # stock_basic 特殊: 没有 trade_date, 看 mtime
    sb_path = Path("data/history/stock_basic/stock_basic.parquet")
    print(f"  stock_basic (静态) : mtime {_file_mtime(sb_path)}")
    return 0


def _financials_ok_count(parquet_file) -> int:
    """数某个季文件里 fetch_status='ok' 的行数 (0 = 全 skip, 该重拉)

    2026-11-02 加: 季报披露截止日前跑过一次, VIP 返空 → 全标 skip。
    之后报告陆续发布, 但 skip 被当成"永久无数据" → 永远不重拉。
    这个 helper 让 stale 检测能识别这种"文件在但没数据"的状态。
    """
    try:
        import duckdb
        return duckdb.execute(
            f"SELECT count(*) FROM read_parquet('{parquet_file}') "
            f"WHERE fetch_status = 'ok'"
        ).fetchone()[0]
    except Exception:
        return -1  # 读不了 → 不判定 (保守: 不标 stale)


def detect_stale_flags() -> dict[str, bool]:
    """自动检测哪些 flag 该跑 (2026-09-03 改造, 解决"用户忘记跑 sync"问题)

    返回: {flag_name: True/False} — True 表示该跑
    """
    import duckdb
    from datetime import datetime, timedelta
    from pathlib import Path
    from .store import HISTORY_DIR, STK_FACTOR_DIR, FIN_DIR, STOCK_BASIC_PARQUET

    today = datetime.now()
    # --auto 检测范围: 盯这 6 个 flag
    # cache / meta 不在 --auto 检测范围 (按需手动 sync), 不进 flags dict
    # 2026-10-14 加 etf: 之前 SKILL.md 写"auto 含 etf"但实际没接, 导致 ETF 段天天需要手动 sync
    flags = {
        "kline": False,         # K线距今天 > 1 天
        "stock_basic": False,   # 距上次 > 30 天
        "financials": False,    # 缺最新季
        "stk_factor": False,    # stk_factor 距今天 > 1 天 (2026-09-22 修 v6.2.4 重构漏接 auto)
        "etf": False,           # ETF K线距今天 > 1 天 (2026-10-14 加, SKILL.md 描述早就有但 sync.py 没接)
        "sw_index": False,      # 申万行业指数距今天 > 1 天 (2026-11-11 加)
    }

    # 1. K线 (每天必跑, 距今天 > 1 天就拉)
    # 改: 不扫 7.5M 行, 读最新季度 parquet footer 拿 max(trade_date) (0.3ms)
    # fallback: statistics disabled / IO 异常 → 保守标 stale (宁多拉不错过)
    def _max_trade_date_from_parquet(parquet_file) -> str | None:
        """从 parquet footer 读 max(trade_date), 毫秒级.
        
        Returns:
            str YYYYMMDD, or None (statistics disabled / 异常)
        """
        try:
            import pyarrow.parquet as pq
            meta = pq.read_metadata(parquet_file)
            maxes = []
            for rg_idx in range(meta.num_row_groups):
                rg = meta.row_group(rg_idx)
                for c_idx in range(rg.num_columns):
                    col = rg.column(c_idx)
                    if col.path_in_schema == "trade_date":
                        if col.statistics is None:
                            return None  # statistics disabled
                        maxes.append(str(col.statistics.max))
                        break  # 找到 trade_date 列就退出
            return max(maxes) if maxes else None
        except Exception:
            return None

    try:
        files = list(HISTORY_DIR.glob("*.parquet"))
        if files:
            latest_file = sorted(files)[-1]  # 最新季度 parquet
            max_d = _max_trade_date_from_parquet(latest_file)
            if max_d is None:
                # fallback: 保守标 stale (怕错失真实 stale)
                flags["kline"] = True
            else:
                max_d_clean = max_d.replace("-", "")
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
            # 最新季文件 stem 形如 YYYYQN (e.g. 2026Q2)
            q_label = latest.stem
            year, q = q_label[:4], q_label[5:]
            # 季末
            # 2026-11-02 修 (真 bug): 原来 q = q_label[5:] = "3", 但字典 key 是 "Q3"
            #   → KeyError → 被外层 except 吞掉 → financials stale 检测从来没生效过
            quarter_end = {
                "1": "0331", "2": "0630", "3": "0930", "4": "1231",
            }.get(q)
            if quarter_end is None:
                raise ValueError(f"无法解析季度标签: {q_label}")
            end_dt = datetime.strptime(f"{year}{quarter_end}", "%Y%m%d")
            if (today - end_dt).days > 100:  # 100 天没新季
                flags["financials"] = True
            # 2026-11-02 修: 最新季文件存在但 0 行 ok (全 skip) → 也是 stale
            #   背景: 季报披露截止日前跑过一次, VIP 返空 → 全标 skip。
            #         之后报告陆续发布, 但 skip 被当成"永久无数据", 永远不重拉。
            #   判定: 最新季 ok 行数 == 0 → 标 stale (让 action_financials 重拉)
            elif _financials_ok_count(latest) == 0:
                flags["financials"] = True
    except Exception as e:
        # 2026-11-02 修: 原来 except Exception: pass 把 KeyError 静默吞了,
        #   导致 financials 的 stale 检测从来没生效 (Q3 全 skip 也检测不到).
        #   现在: 出错时**保守标 stale** (宁多拉不错过) + 打警告, 不再静默.
        print(f"  ⚠️ financials stale 检测异常 ({type(e).__name__}: {e}) → 保守标 stale")
        flags["financials"] = True

    # 4. stk_factor (每天必跑, 距今天 > 1 天就拉, 2026-09-22 修 v6.2.4 重构漏接 auto)
    #    读 STK_FACTOR_DIR/{YYYYQN}.parquet metadata.done_dates 合并集
    #    注意: 周末/节假日 done_dates 不补, 用 (today - last).days >= 1 判断 (跟 kline 一致)
    # 2026-11-02 加: 光看日期不够 —— 某天 Tushare 只先发沪市时, 那天的 done_dates 里
    #    仍有这个日期, 但深市代码永远缺失 (实测 20261102 全市场只返 2504 行)。
    #    所以额外查「最新日期的代码覆盖数」, 明显偏少 → 标 stale, 让 sync 重试补齐。
    try:
        if not STK_FACTOR_DIR.exists() or not list(STK_FACTOR_DIR.glob("*.parquet")):
            flags["stk_factor"] = True  # 没数据, 必拉
        else:
            from .caches.stk_factor_history import read_all_done_dates
            done = read_all_done_dates()
            if not done:
                flags["stk_factor"] = True  # 空, 必拉
            else:
                last = max(done)  # YYYYMMDD
                # trade_date 是 str, 转 datetime 比较
                last_dt = datetime.strptime(last, "%Y%m%d")
                gap = (today - last_dt).days
                if gap >= 1:
                    flags["stk_factor"] = True
                else:
                    # 日期是最新的, 但可能只拉到一部分 → 查覆盖数
                    try:
                        from .caches.stk_factor_history import read_code_count_by_date
                        cc = read_code_count_by_date()
                        if cc:
                            counts = sorted(cc.values())
                            median = counts[len(counts) // 2]
                            n_last = cc.get(last, 0)
                            if median and n_last < median * 0.7:
                                flags["stk_factor"] = True
                                print(f"  ⚠️ stk_factor 最新日期 {last} 只有 {n_last} 只 "
                                      f"(基准 {median}), 判定半拉 → 需重拉补齐")
                    except Exception:
                        pass
    except Exception:
        flags["stk_factor"] = True  # 异常保守拉

    # 5. ETF K线 (2026-10-14 加, 跟 kline 同节奏: 距今天 > 1 天就拉)
    try:
        from .store import _get_etf_local_max_date
        last_etf = _get_etf_local_max_date()
        if not last_etf:
            flags["etf"] = True  # 没数据, 必拉
        else:
            last_dt = datetime.strptime(last_etf, "%Y%m%d")
            gap = (today - last_dt).days
            if gap >= 1:
                flags["etf"] = True
    except Exception:
        flags["etf"] = True  # 异常保守拉

    # 6. 申万行业指数 (2026-11-11 加, 跟 etf 同节奏: 距今天 > 1 天就拉)
    #    额外查覆盖: 最新日期只有少数行业有数 → 半拉 → 仍要拉
    try:
        from .store import SW_INDEX_KLINE_DIR
        files = list(SW_INDEX_KLINE_DIR.glob("*.parquet")) if SW_INDEX_KLINE_DIR.exists() else []
        if not files:
            flags["sw_index"] = True  # 没数据, 必拉
        else:
            maxes = []
            for f in files:
                m = _local_max_trade_date(f)
                if m:
                    maxes.append(str(m))
            if not maxes:
                flags["sw_index"] = True
            else:
                last = max(maxes)
                last_dt = datetime.strptime(last, "%Y%m%d")
                if (today - last_dt).days >= 1:
                    flags["sw_index"] = True
                else:
                    n_last = sum(1 for m in maxes if m == last)
                    if n_last < len(files) * 0.7:
                        flags["sw_index"] = True
                        print(f"  ⚠️ sw_index 最新日期 {last} 只有 {n_last}/{len(files)} 个行业有数 "
                              f"→ 判定半拉 → 需重拉补齐")
    except Exception:
        flags["sw_index"] = True  # 异常保守拉

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
            print(f"  ⚠️  --force, 全部 {len(flags)} 个 flag 强刷 (cache/meta 仍需手动)")
    if quiet:
        # dry-run 模式: 也要打印结果 (这是 dry 的全部意义)
        print("\n🔍 自动检测 (dry-run, 不真跑):")
        for k, v in flags.items():
            mark = "🟡 会跑" if v else "✅ 跳过"
            print(f"  {mark}  --{k}")
        print_data_freshness_summary()
        _print_api_call_stats()
        return 0
    print("\n🔍 自动检测结果:")
    for k, v in flags.items():
        mark = "🟡 需跑" if v else "✅ 跳过"
        print(f"  {mark}  --{k}")
    if not any(flags.values()):
        print("\n✨ 全 fresh, 不用 sync")
        print_data_freshness_summary()
        _print_api_call_stats()
        return 0
    # 真跑 (复用 main() 的 flag 逻辑)
    print("\n🚀 跑 stale 的 flag:")
    if flags["kline"]:
        print("  [1/7] --kline")
        action_kline()
    if flags["stk_factor"]:
        # 2026-09-22 加: v6.2.4 重构漏接 auto 链路, 今天补上
        print("  [1.5/7] --stk-factor (auto 检测到 stale)")
        action_stk_factor(force=False)
    if flags["stock_basic"]:
        print("  [2/7] --stock-basic")
        action_stock_basic()
    if flags["financials"]:
        print("  [3/7] --financials")
        action_financials(_last_codes)
    if flags["etf"]:
        # 2026-10-14 加: ETF 段 auto 接入, 解决"每天手动 sync ETF"问题
        print("  [4/7] --etf (auto 检测到 stale)")
        from .store import sync_etf_basic, sync_etf_incremental
        sync_etf_basic()
        sync_etf_incremental()
    if flags["sw_index"]:
        # 2026-11-11 加: 申万行业指数段 auto 接入 (行业 stage 每天都要更新)
        print("  [5/7] --sw-index (auto 检测到 stale)")
        action_sw_index()
    print_data_freshness_summary()
    _print_api_call_stats()
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
  python -m tools.sync --codes 002371        # 单只 K 线
  python -m tools.sync --status              # 看现状
        """,
    )

    # Scope (3 选 1, 默认 --all, 2026-09-14 改)
    scope = parser.add_argument_group("范围 (3 选 1, 默认 --all)")
    scope.add_argument("--watchlist", action="store_true", default=False,
                       help="watchlist 全部 (55 只, 显式 --watchlist)")
    scope.add_argument("--all", action="store_true", default=True,
                       help="全市场 (5555 只, 默认)")
    scope.add_argument("--codes", nargs="+", metavar="CODE",
                       help="指定代码列表 (例: --codes 002371 300750)")

    # 8 个正交 flag (全部默认关, 显式开才跑, 符合"正交控制"原则)
    # 但: 不传任何 sync flag → 默认走 --auto (智能检测 stale, 只跑需跑的)
    actions = parser.add_argument_group("Sync 行为 (8 个正交, 全部默认关; 不传任何 flag → 自动检测)")
    actions.add_argument("--kline", action="store_true",
                         help="增量 K 线 + daily_basic + 6 指数")
    actions.add_argument("--stk-factor", action="store_true",
                         help="[v6.2.4 重构] 重拉 stk_factor_pro 16 列 (替代旧 daily_basic, 8 分钟)")
    actions.add_argument("--stock-basic", action="store_true",
                         help="股票基础信息 (行业/名称)")
    actions.add_argument("--financials", action="store_true",
                         help="财务 5 季度 (fina_indicator_vip 全市场)")
    actions.add_argument("--cache", action="store_true",
                         help="signal_cache 缓存 (analysis_cache.db)")
    actions.add_argument("--etf", action="store_true",
                         help="[2026-10-14 加] ETF 场内基金 (fund_basic 月级 + fund_daily 日增量, 默认随 --auto 跑)")
    actions.add_argument("--sw-index", action="store_true",
                         help="[2026-11-11 加] 申万行业指数 (index_classify + sw_daily, 给行业算威科夫 stage, 默认随 --auto 跑)")
    parser.add_argument("--sw-index-level", default="L2",
                        help="申万行业层级: L1(31) / L2(134, 默认) / L3(346)")
    # 2026-09-09 删: --meta (板块/事件元数据, 引用不存在的 refresh_sectors, 死代码 + broken)
    actions.add_argument("--all-data", action="store_true",
                         help="[一键] --kline --stock-basic --financials 一起跑 (最常用)")

    # Misc
    parser.add_argument("--status", action="store_true",
                        help="看现状, 不拉任何数据")
    parser.add_argument("--auto", action="store_true", default=False,
                        help="[默认行为] 自动检测 stale flag, 只跑需跑的 (解决'忘记 sync'问题)")
    parser.add_argument("--auto-force", action="store_true",
                        help="自动检测 + 强刷所有 stale flag")
    parser.add_argument("--period", help="财务指定季度 (例: 20251231, 跟 --financials 一起用)")

    args = parser.parse_args()

    # 缓存 codes 给 action_auto
    global _last_codes

    # Status 短路
    if args.status:
        return action_status()

    # --auto / --auto-force 短路 (忽略其他 flag)
    if args.auto or args.auto_force:
        # 先解析 codes
        if args.codes:
            _last_codes = [c.zfill(6) for c in args.codes]
        elif args.all:
            _last_codes = _get_codes(None, all_market=True)
        else:
            _last_codes = _get_codes(None, all_market=False)
        scope = f"watchlist {len(_last_codes)} 只" if not (args.codes or args.all) else (
            f"指定 {len(_last_codes)} 只" if args.codes else f"全市场 {len(_last_codes)} 只"
        )
        mode = "auto-force" if args.auto_force else "auto"
        print(f"=== Mavis sync_data (scope: {scope}) [{mode}] ===")
        return action_auto(
            force=args.auto_force,
            quiet=False,
        )

    # Scope 解析
    # 2026-09-18 修: --all default=True 是历史包袱; --codes / --watchlist 显式传入时优先
    # (下面是真正的优先级解析: codes > all > watchlist)
    if args.codes:
        codes = [c.zfill(6) for c in args.codes]
        scope_label = f"指定 {len(codes)} 只"
    elif args.all:
        codes = _get_codes(None, all_market=True)
        scope_label = f"全市场 {len(codes)} 只"
    else:
        codes = _get_codes(None, all_market=False)
        scope_label = f"watchlist {len(codes)} 只"
    _last_codes = codes

    # 2026-09-18 加: 重置 tushare API 统计 (跟 EPS 守门员放一起)
    try:
        from .sources.tushare import reset_api_stats
        reset_api_stats()
    except ImportError:
        pass

    # 没传任何 sync flag + 不是 --status / --auto → 默认走 --auto (智能检测)
    any_sync_flag = any([
        args.kline, args.stk_factor, args.stock_basic, args.financials,
        args.cache, args.all_data, args.etf, args.sw_index,
    ])
    if not any_sync_flag:
        print(f"=== Mavis sync_data (scope: {scope_label}) [默认 --auto 智能检测] ===")
        # action_auto 内部已打网络统计 (2026-11-02 修: 原来这里又打一次, 第二次恒为 0)
        return action_auto(force=False, quiet=False)

    print(f"=== Mavis sync_data (scope: {scope_label}) ===")
    start = time.time()

    # --all-data 是个 alias, 相当于 --kline --stock-basic --financials
    if args.all_data:
        args.kline = True
        args.stock_basic = True
        args.financials = True

    # 按 flag 调用 (顺序: kline → stock_basic → financials → cache → meta)
    if args.kline:
        print("\n[1/7] --kline (增量 K 线)")
        action_kline()
    if args.stk_factor:
        # v6.2.4 重构: 替代 daily_basic, 16 列, 8 分钟重拉
        print("\n[1.5/7] --stk-factor (重拉 17 列 stk_factor_pro, 替代 daily_basic)")
        action_stk_factor(force=False)
    if args.stock_basic:
        print("\n[2/7] --stock-basic (股票基础)")
        action_stock_basic()
    if args.financials:
        print("\n[3/7] --financials (财务 5 季度)")
        # v6.2.4 改: financials 强制全市场 (传 None, action_financials 内部会拉 5555 只)
        # 只有用户显式 --codes 时才用 codes (小批调试)
        fin_codes = codes if args.codes else None
        action_financials(fin_codes, period=args.period)
    if args.cache:
        print("\n[6/7] --cache (signal_cache)")
        action_cache(codes)

    # 2026-10-14 加: --etf (ETF 场内基金)
    if args.etf:
        print(f"\n[8/7] --etf (ETF 场内基金: basic 月级 + 日 K 增量)")
        from .store import sync_etf_basic, sync_etf_incremental
        sync_etf_basic()
        sync_etf_incremental()

    # 2026-11-11 加: --sw-index (申万行业指数, 给行业算威科夫 stage)
    if args.sw_index:
        print(f"\n[9/7] --sw-index (申万行业指数 level={args.sw_index_level})")
        action_sw_index(level=args.sw_index_level)

    # 全部 flag 都没开 + 也不是 --status → 给个友好提示
    if not any([args.kline, args.stock_basic, args.financials,
                args.cache, args.etf, args.sw_index]):
        print("\n💡 没指定任何行为, 看 --help 选 flag")
        print("   最常用: python -m tools.sync --all-data")

    elapsed = time.time() - start
    print(f"\n=== 完成, 耗时 {elapsed:.1f} 秒 ===")
    _print_api_call_stats()
    print_data_freshness_summary()
    return 0


def _print_api_call_stats() -> None:
    """2026-09-18 加: 打印 tushare API 调用统计 (按 API 分组)

    输出示例:
      网络请求统计:
        daily:                  1 次
        index_daily:            0 次
        fina_indicator_vip:     0 次
        ──────────────
        总计:                    1 次
    """
    try:
        from .sources.tushare import get_api_stats, reset_api_stats
    except ImportError:
        return

    stats = get_api_stats()
    if not stats:
        print("\n🌐 网络请求统计: 0 次 (全部走本地缓存或已最新)")
        return

    total = sum(stats.values())
    print(f"\n🌐 网络请求统计 (总计 {total} 次):")
    # 按调用次数降序, 0 次的不显示
    for api_name in sorted(stats.keys(), key=lambda x: (-stats[x], x)):
        cnt = stats[api_name]
        if cnt > 0:
            print(f"   {api_name:25s} {cnt:>4d} 次")

    # 重置为下次跑
    reset_api_stats()


if __name__ == "__main__":
    sys.exit(main())
