#!/usr/bin/env python3
"""
tools/sync_data.py — 唯一 sync 入口 (2026-09-03 改造)

设计原则:
  1. 唯一入口, 7 个 flag 正交控制, 互不耦合
  2. 默认只跑 --kline (增量 K 线, 最常用)
  3. 其他 (financials/eps/fflow/cache/stock-basic/meta) 都要显式开
  4. 范围: --watchlist (默认) / --all / --codes 002371 300750
  5. 5 个分析 skill (t-analyze/t-bb-obv/t-near-low/t-roc-ey/t-backtest) 全改"只读",
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

WATCHLIST = ROOT / "config" / "watchlist.json"


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
    print(f"  ✅ K线: {n} 条新增")
    return n


def action_ths(years_back: int = 3) -> int:
    """同步 THS 同花顺概念板块 (按 watchlist.ths_whitelist, 默认 3 年历史)

    2026-09-17 加: --ths flag 调用入口.

    流程:
      1. 拉 ths_index() 全量 → 写 data/history/ths/ths_index.parquet (1 次 API)
      2. 对每个 ths_whitelist 条目:
           a. 模糊匹配 ths_index 找 ts_code (找不到 warn)
           b. 看 data/history/ths/kline/<code>.parquet 最大 trade_date
           c. start_date = (没有 → today - years_back; 有 → max_local+1)
           d. ths_daily(ts_code, start, today) → 拉增量
           e. 写盘 (1 概念 1 parquet, 字段: OHLCV + avg_price + change + pct_change + turnover_rate)
    """
    import sys
    import time
    import pandas as pd
    from datetime import datetime, timedelta
    from .store import THS_HISTORY_DIR, _append_records_target
    from .sources.tushare import get_ths_index, get_ths_daily

    today = datetime.now().strftime("%Y%m%d")
    full_backfill_start = (datetime.now() - timedelta(days=365 * years_back)).strftime("%Y%m%d")

    # 1. ths_index 30 天缓存 (ths_index() 概念上线几乎不变, 避免重复网络)
    index_path = THS_HISTORY_DIR / "ths_index.parquet"
    index_rows = None
    if index_path.exists():
        age_days = (time.time() - index_path.stat().st_mtime) / 86400
        if age_days < 30:
            print(f"  ⏭ ths_index {age_days:.1f} 天内已刷, 用本地缓存 (避免重复网络)")
            index_df = pd.read_parquet(index_path)
            index_rows = index_df.to_dict("records")
    if index_rows is None:
        print("  📋 拉 ths_index 名码映射 (距上次 > 30 天 或 首次)")
        index_rows, status = get_ths_index()
        if not index_rows:
            print(f"  ⚠️ ths_index 失败: {status}, 跳过")
            return 0
        index_df = pd.DataFrame(index_rows)
        index_df["sync_date"] = today
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_df.to_parquet(index_path, index=False)
        print(f"  ✅ ths_index: {len(index_rows)} 行, status=OK")

    # 2. 读 ths_whitelist.json (2026-09-17 拆出独立文件)
    wl_file = Path("config/ths_whitelist.json")
    if not wl_file.exists():
        # 兼容老路径 watchlist.json ths_whitelist 字段
        fallback = Path("config/watchlist.json")
        if fallback.exists():
            wl = json.loads(fallback.read_text(encoding="utf-8"))
            whitelist = wl.get("ths_whitelist", [])
            if whitelist:
                print(f"  ⚠️ ths_whitelist.json 不存在, 暂时从 watchlist.json 读 (老路径兼容)")
            else:
                whitelist = []
        else:
            whitelist = []
    else:
        wl = json.loads(wl_file.read_text(encoding="utf-8"))
        # 字段从 ths_whitelist → concepts
        whitelist = wl.get("concepts") or wl.get("ths_whitelist", [])

    if not whitelist:
        print(f"  ⚠️ ths_whitelist.json 没有 concepts 字段, 跳过")
        return 0
    print(f"  📌 白名单: {len(whitelist)} 个 THS 概念 (config/ths_whitelist.json)")

    # 3. 反查 + 增量续存
    total_pulled = 0
    code_map = {}  # 本次 session 内 ths_index 反查缓存
    for item in whitelist:
        match_name = item.get("match", "")
        if not match_name:
            continue

        # 模糊匹配 ths_index
        if match_name not in code_map:
            candidates = [r for r in index_rows if match_name in str(r.get("name", ""))]
            if not candidates:
                print(f"    ⚠️ 未找到: {match_name} (在 ths_index 里)")
                code_map[match_name] = None
                continue
            # 优先 type=N (普通概念), 退而求其次 type=S 风格
            prefer_n = [c for c in candidates if c.get("type") == "N"]
            chosen = (prefer_n or candidates)[0]
            code_map[match_name] = chosen["ts_code"]
            print(f"    ✅ {match_name} → {chosen['ts_code']} ({chosen['name']}, type={chosen.get('type')})")

        ts_code = code_map[match_name]
        if not ts_code:
            continue

        # 看本地最大日期
        kline_path = THS_HISTORY_DIR / "kline" / f"{ts_code}.parquet"
        if kline_path.exists():
            try:
                local_df = pd.read_parquet(kline_path)
                local_max = local_df["trade_date"].max()
            except Exception:
                local_max = None
        else:
            local_max = None

        start_date = str(int(local_max) + 1) if local_max else full_backfill_start

        # 关键: 提前判断, 无交易缺就不发网络
        if int(start_date) > int(today):
            print(f"    ⏭ {ts_code}: 本地最新 {local_max}, 无交易日缺, 跳过网络")
            continue

        records, _status = get_ths_daily(ts_code, start_date=start_date, end_date=today)
        if not records:
            print(f"    ⏭ {ts_code}: API 返空 (start={start_date}, end={today}, 无新数据)")
            continue

        # rename: pct_change → pct_chg (对齐 daily schema)
        for r in records:
            if "pct_change" in r:
                r["pct_chg"] = r.pop("pct_change")

        _append_records_target(records, THS_HISTORY_DIR / "kline")
        total_pulled += len(records)
        print(f"    ✅ {ts_code}: {len(records)} 根 (start={start_date}, end={today})")
        time.sleep(0.3)  # 限频 200ms

    print(f"  ✅ THS: {total_pulled} 条新增 (落盘 {len(list((THS_HISTORY_DIR / 'kline').glob('*.parquet')))} 个概念)")
    return total_pulled


# v6.2.4 加: stk_factor_pro 替代 daily_basic, 17 列 (含 ps/dv_ratio/free_float_turnover)
# v6.2.5 改造: done 进度塞 parquet metadata (跟 fflow 平行), 不再单独维护 .stk_factor_progress.json
# STK_FACTOR_FIELDS 搬到 caches/stk_factor_history.py, 这里只保留兼容性 import alias (供 action_status 读 max date)


def action_stk_factor(force: bool = False) -> int:
    """重拉 5 季 stk_factor_pro (17 列, 写到 data/history/stk_factor/)

    行为 (v6.2.5 改造: 跟 action_fflow 一样, done 进度塞 parquet metadata, 不用 json):
      1. 删旧 5 季 stk_factor parquet (force=True 强制, False 智能)
      2. 拉 5 季 trade_date x 1 API = ~8 分钟 (30/分限频, sleep 2.0秒)
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
    print(f"  📅 总交易日: {len(all_dates)}  已完成: {len(done)}  待拉: {len(pending)}")

    if not pending and not force:
        print(f"  ✅ 全部完成, 无需重拉")
        return 0

    # 3) force 模式才删旧 5 季 parquet
    if force:
        STK_FACTOR_DIR.mkdir(parents=True, exist_ok=True)
        for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]:
            old = STK_FACTOR_DIR / f"{q}.parquet"
            if old.exists():
                old.unlink()
                print(f"  🗑️  删旧 stk_factor/{old.name}")
        done = set()  # force 重置

    # 4) 按 trade_date 逐个拉, 按季累积
    from .caches.fflow_history import _quarter_of as _q_of  # 复用同一函数
    quarter_data: dict[str, list[dict]] = {q: [] for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]}
    quarter_done: dict[str, set[str]] = {q: set() for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]}
    t0 = time.time()
    n_total = 0
    for i, d in enumerate(pending, 1):
        data, status = get_stk_factor_by_date(d)
        if not data:
            print(f"  ⚠️ {d}: 拉取失败 ({status})")
            continue
        q = _q_of(d)
        quarter_data[q].extend(data)
        quarter_done[q].add(d)
        done.add(d)
        n_total += len(data)
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
        time.sleep(2.0)  # 30/分限频

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
            qend, deadline = _quarter_end_and_deadline(yr, q)
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


def action_eps(codes: list[str]) -> int:
    """EPS 机构预期 (东方财富 datacenter RPT_HSF10_RESPREDICT_COUNTSTATISTICS)

    真实接口: tools/fetch/data_fetcher.py::_build_eps_table
    走 datacenter.eastmoney.com (主) → Tushare 自建 NTM (备) → EMPTY

    v6.2.5 改造: 1 只票 = 1 parquet (4 期 A/E) → 117 票 = 117 文件 (浪费 schema)
                  改成: 全表 = 1 个 parquet, 约 500 行 (117 票 × 4 期)
                  caches.eps.write_eps(code, data) 内部 upsert (删旧 + 加新)
    """
    from .sources.eastmoney import _build_eps_table
    from .caches.eps import write_eps
    ok = 0
    sources = {"datacenter_consensus": 0, "tushare_built_ntm": 0, "EMPTY": 0}
    for c in codes:
        try:
            data, source = _build_eps_table(c)
            sources[source] = sources.get(source, 0) + 1
            if data:
                write_eps(c, data)  # 内部 upsert 到 eps_consensus.parquet
                ok += 1
        except Exception as e:
            print(f"  ⚠️ {c} EPS 拉取失败: {e}")
    print(f"  ✅ EPS consensus: {ok}/{len(codes)} 只 (datacenter {sources['datacenter_consensus']} / "
          f"tushare 自建 {sources.get('tushare_built_ntm', 0)} / EMPTY {sources['EMPTY']})")
    return ok


# v6.2.5 加: fflow 落盘到 data/history/fflow_history/, 跟 stk_factor 平行
# 9 字段精简版 (大单+特大单 amount + net_mf_amount, 不存 vol/中单)
# v6.2.5 优化: done 进度塞 parquet metadata (key=b'done_dates', JSON-encoded),
#              不再单独维护 .fflow_history_progress.json


def action_fflow(force: bool = False) -> int:
    """主力资金流历史 — 按天全市场拉, 按季存 parquet (v6.2.5 改造)

    行为:
      1. 跨 5 季 parquet metadata 加载 done dates (断点续跑, 不依赖外部 json)
      2. 拉 5 季 trade_date x 1 API ≈ 13 分钟 (30/分限频, sleep 2.0秒)
      3. **每 10 个 trade_date 就把当季累积的 rows 写盘 + 更新 metadata** (崩了不丢数据)
      4. 4 季按 trade_date 写季度 parquet: 25Q3 25Q4 26Q1 26Q2 26Q3
         每季文件 metadata.b'done_dates' = JSON 数组 of YYYYMMDD (本季已完成)

    9 字段 (FFLOW_HISTORY_FIELDS, 不存 vol/中单):
      ts_code, trade_date,
      buy_sm_amount, sell_sm_amount,
      buy_lg_amount, sell_lg_amount,
      buy_elg_amount, sell_elg_amount,
      net_mf_amount

    v6.2.5 改造后: 单一入口 (按天全市场), 不再有 --codes 兼容路径
    老的 --codes X --fflow 单只 API 已删 (走 DataStore.get_fflow_history 读 parquet)
    """
    from .sources.tushare import get_money_flow_by_date
    from .caches.fflow_history import (
        write_fflow_quarter,
        read_all_done_dates,
        FFLOW_HISTORY_FIELDS,
        FFLOW_HISTORY_DIR,
    )

    import time
    from datetime import datetime

    # 1) 加载进度 (从 5 季 parquet metadata 合并, 0 外部文件)
    done = read_all_done_dates()
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
    print(f"  📅 总交易日: {len(all_dates)}  已完成: {len(done)}  待拉: {len(pending)}")

    if not pending and not force:
        print(f"  ✅ 全部完成, 无需重拉")
        return 0

    # 3) 删旧 5 季 fflow_history parquet (force 重拉语义)
    if force:
        FFLOW_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]:
            old = FFLOW_HISTORY_DIR / f"fflow_{q}.parquet"
            if old.exists():
                old.unlink()
                print(f"  🗑️  删旧 fflow_history/{old.name}")
        done = set()  # force 重置

    # 4) 按 trade_date 逐个拉, 按季累积
    from .caches.fflow_history import _quarter_of
    quarter_data: dict[str, list[dict]] = {q: [] for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]}
    quarter_done: dict[str, set[str]] = {q: set() for q in ["2025Q3", "2025Q4", "2026Q1", "2026Q2", "2026Q3"]}
    t0 = time.time()
    n_total = 0
    for i, d in enumerate(pending, 1):
        data, status = get_money_flow_by_date(d)
        if not data:
            print(f"  ⚠️ {d}: 拉取失败 ({status})")
            continue
        q = _quarter_of(d)
        quarter_data[q].extend(data)
        quarter_done[q].add(d)
        done.add(d)
        n_total += len(data)
        # 进度: 每 10 个或最后一个, 把当季已完成的 row + done 写盘 (崩了不丢)
        if i % 10 == 0 or i == len(pending):
            elapsed = time.time() - t0
            speed = i / elapsed if elapsed > 0 else 0
            eta = (len(pending) - i) / speed if speed > 0 else 0
            print(f"  📡 [{i}/{len(pending)}] {d}: {len(data)} 只  "
                  f"速度 {speed:.2f}/s  ETA {eta/60:.1f} 分钟")
            # 每 10 个就把所有非空季落盘 (append 模式: 重读旧文件 + merge)
            for q, rows in quarter_data.items():
                if not rows:
                    continue
                # 重读旧 parquet (如果存在), 合并
                p = FFLOW_HISTORY_DIR / f"fflow_{q}.parquet"
                if p.exists():
                    try:
                        old_df = duckdb.execute(
                            f"SELECT * FROM read_parquet('{p}')"
                        ).df()
                        new_df = pd.DataFrame(rows)
                        all_df = pd.concat([old_df, new_df], ignore_index=True)
                        all_df = all_df.drop_duplicates(subset=["ts_code", "trade_date"])
                        rows_to_write = all_df.to_dict("records")
                    except Exception:
                        rows_to_write = rows
                else:
                    rows_to_write = rows
                # 写盘: rows + 当季 done dates
                write_fflow_quarter(q, rows_to_write, done_dates=sorted(quarter_done[q]))
        time.sleep(2.0)  # 30/分限频

    # 5) 最终再写一次 (保险: 确保最新 done 落盘)
    import pandas as pd
    import duckdb
    for q, rows in quarter_data.items():
        if not rows:
            continue
        p = FFLOW_HISTORY_DIR / f"fflow_{q}.parquet"
        if p.exists():
            try:
                old_df = duckdb.execute(f"SELECT * FROM read_parquet('{p}')").df()
                new_df = pd.DataFrame(rows)
                all_df = pd.concat([old_df, new_df], ignore_index=True)
                all_df = all_df.drop_duplicates(subset=["ts_code", "trade_date"])
                rows_to_write = all_df.to_dict("records")
            except Exception:
                rows_to_write = rows
        else:
            rows_to_write = rows
        write_fflow_quarter(q, rows_to_write, done_dates=sorted(quarter_done[q]))
        print(f"  ✅ 写 fflow_history/fflow_{q}: {len(rows_to_write)} 行, "
              f"done={len(quarter_done[q])}, "
              f"{len(FFLOW_HISTORY_FIELDS.split(','))} 列")

    print(f"  🎉 完成: {n_total} 行 总耗时 {(time.time()-t0)/60:.1f} 分钟")
    return n_total


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
    from .store import HISTORY_DIR, STK_FACTOR_DIR, FIN_DIR, FFLOW_HISTORY_DIR
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
    # fflow_history 走 data/history/fflow_history/fflow_*.parquet (v6.2.5 新, 跟 stk_factor 平行)
    try:
        if FFLOW_HISTORY_DIR.exists() and list(FFLOW_HISTORY_DIR.glob("fflow_*.parquet")):
            df = duckdb.execute(
                f"SELECT MAX(trade_date) FROM read_parquet('{FFLOW_HISTORY_DIR}/fflow_*.parquet')"
            ).fetchone()
            print(f"  fflow (资金流)     : {df[0] if df and df[0] else '—'}")
        else:
            print(f"  fflow (资金流)     : 未拉过")
    except Exception as e:
        print(f"  fflow (资金流)     : ❌ {type(e).__name__}")
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

    # Scope (3 选 1, 默认 --all, 2026-09-14 改)
    scope = parser.add_argument_group("范围 (3 选 1, 默认 --all)")
    scope.add_argument("--watchlist", action="store_true", default=False,
                       help="watchlist 全部 (55 只, 显式 --watchlist)")
    scope.add_argument("--all", action="store_true", default=True,
                       help="全市场 (5555 只, 默认)")
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
                         help="主力资金流历史 (按天全市场, 按季存 parquet)")
    actions.add_argument("--cache", action="store_true",
                         help="signal_cache 缓存 (analysis_cache.db)")
    actions.add_argument("--ths", action="store_true",
                         help="[2026-09-17 加] THS 同花顺概念板块 K 线 (按 watchlist.ths_whitelist, 默认回填 3 年)")
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

    # v6.2.9 铁律: --eps 绝不允许全市场 (datacenter 5555 只限频, 拉一次 25 分钟且会撞 WAF)
    # 任何 --eps 路径 (含 --auto 自动检测) 强制缩到 watchlist; --codes 显式指定放行
    if args.eps and args.codes:
        pass  # 显式 --codes 放行
    elif args.eps:
        if args.all:
            print("⚠️  --eps 不允许全市场 (datacenter API 限频), 强制缩到 watchlist")
        args.all = False

    # --auto / --auto-force 短路 (忽略其他 flag)
    if args.auto or args.auto_force:
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
        codes = _get_codes("all", None, all_market=True)
        scope_label = f"全市场 {len(codes)} 只"
    else:
        codes = _get_codes("watchlist", None, all_market=False)
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
        args.eps, args.fflow, args.cache, args.all_data, args.ths,
    ])
    if not any_sync_flag:
        print(f"=== Mavis sync_data (scope: {scope_label}) [默认 --auto 智能检测] ===")
        rc = action_auto(force=False, quiet=False)
        _print_api_call_stats()
        return rc

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
        print("\n[1.5/7] --stk-factor (重拉 17 列 stk_factor_pro, 替代 daily_basic)")
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
        print("\n[5/7] --fflow (主力资金历史, 按天全市场拉+按季存)")
        # v6.2.5 改: 单一入口 (按天全市场), 不再支持 --codes 单只
        # 老的 --codes X --fflow 单只逻辑已删, 走 DataStore.get_fflow_history 读 parquet
        action_fflow(force=False)
    if args.cache:
        print("\n[6/7] --cache (signal_cache)")
        action_cache(codes)

    # 2026-09-17 加: --ths (THS 同花顺概念板块)
    if args.ths:
        print(f"\n[7/7] --ths (THS 概念板块)")
        action_ths(years_back=3)

    # 全部 flag 都没开 + 也不是 --status → 给个友好提示
    if not any([args.kline, args.stock_basic, args.financials,
                args.eps, args.fflow, args.cache, args.ths]):
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
