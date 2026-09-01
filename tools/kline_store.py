"""
tools/kline_store.py — 统一数据访问层 + sync 工具 (合并自 kline_history_backfill, 2026-09-01)

所有代码通过这里访问数据，不直接读文件。

用法:
    from tools.kline_store import DataStore, sync_incremental, read_kline

    raw = DataStore.get_raw("002371")       # 替代 json.load(dump)
    ctx = DataStore.get_ctx("002371")       # 替代 RawContext.from_dump(dump)
    kline = DataStore.get_kline("002371")   # 单独取 K线

CLI:
    bash tools/with_venv.sh python -m tools.kline_store --init
    bash tools/with_venv.sh python -m tools.kline_store            # 增量同步
    bash tools/with_venv.sh python -m tools.kline_store --date 20260822

存储:
    data/history/daily/YYYYQN.parquet         # K 线 (按季切)
    data/history/daily_basic/YYYYQN.parquet   # PE/PB/市值
    data/history/stock_basic/stock_basic.parquet  # 全市场名称/行业
    data/history/financials/YYYYQN.parquet    # 财务指标 (Magic Formula 用)

内部依赖:
    tools/eps_consensus_cache.py  — 低频缓存 (data/cache/eps/)
    tools.fetch.tushare_fetcher    — Tushare 接口
"""

from __future__ import annotations
import argparse
import json
import sys
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

# ============================================================
# 路径常量
# ============================================================

HISTORY_DIR = Path("data/history/daily")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

DAILY_BASIC_DIR = Path("data/history/daily_basic")
DAILY_BASIC_DIR.mkdir(parents=True, exist_ok=True)

STOCK_BASIC_DIR = Path("data/history/stock_basic")
STOCK_BASIC_DIR.mkdir(parents=True, exist_ok=True)
STOCK_BASIC_PARQUET = STOCK_BASIC_DIR / "stock_basic.parquet"

FIN_DIR = Path("data/history/financials")
FIN_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# Config
# ============================================================

def _load_project_cfg() -> dict:
    try:
        import yaml
        p = Path(__file__).parent.parent / "config" / "project.yaml"
        return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


_PROJECT_CFG = _load_project_cfg()


# ============================================================
# 工具函数 (代码转换 / 日期 helper)
# ============================================================

_INDEX_SUFFIX = {
    "000001": "SH", "000300": "SH", "000905": "SH", "000016": "SH",
    "000688": "SH", "930955": "CSI", "000922": "CSI",
    "399001": "SZ", "399006": "SZ", "399808": "SZ",
}

_INDEX_NAMES = {
    "000001": "上证指数",
    "000300": "沪深300",
    "000905": "中证500",
    "000016": "上证50",
    "000688": "科创50",
    "399001": "深证成指",
    "399006": "创业板指",
    "399808": "中证新能源",
}


def _to_ts_code(code: str) -> str:
    """000725 → 000725.SZ / 600000 → 600000.SH / 000300 → 000300.SH (指数优先)"""
    if "." in code:
        return code
    c = code.strip()
    if c in _INDEX_SUFFIX:
        return f"{c}.{_INDEX_SUFFIX[c]}"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    if c.startswith(("6", "9")):
        return f"{c}.SH"
    return f"{c}.SZ"


def _quarter_of(trade_date: str) -> str:
    """YYYYMMDD → 'YYYYQN' 季度标识，如 '20260823' → '2026Q3'"""
    month = int(trade_date[4:6])
    q = (month - 1) // 3 + 1
    return f"{trade_date[:4]}Q{q}"


def _parquet_path(year_or_quarter) -> Path:
    """支持 year（int，向后兼容）或 quarter（str，如 '2026Q3'）"""
    return HISTORY_DIR / f"{year_or_quarter}.parquet"


def _db_parquet_path(quarter: str) -> Path:
    return DAILY_BASIC_DIR / f"{quarter}.parquet"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _get_trading_dates(start: str, end: str) -> list[str]:
    """生成 start~end 之间的交易日列表（排除周末，不排除节假日）。
    节假日 tushare 返回空 df，调用方跳过即可。
    start/end: YYYYMMDD
    """
    dates = []
    d = datetime.strptime(start, "%Y%m%d")
    end_d = datetime.strptime(end, "%Y%m%d")
    while d <= end_d:
        if d.weekday() < 5:  # 0=Mon ~ 4=Fri
            dates.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return dates


def _month_ranges(start_year: int, end_date: str) -> list[tuple[str, str]]:
    """生成按月的 (start, end) 列表，用于首次建档批量拉取。"""
    ranges = []
    end = datetime.strptime(end_date, "%Y%m%d")
    d = datetime(start_year, 1, 1)
    while d <= end:
        month_start = d.strftime("%Y%m%d")
        # 当月最后一天
        if d.month == 12:
            month_end = datetime(d.year + 1, 1, 1) - timedelta(days=1)
        else:
            month_end = datetime(d.year, d.month + 1, 1) - timedelta(days=1)
        month_end = min(month_end, end)
        ranges.append((month_start, month_end.strftime("%Y%m%d")))
        # 下一个月
        if d.month == 12:
            d = datetime(d.year + 1, 1, 1)
        else:
            d = datetime(d.year, d.month + 1, 1)
    return ranges


# ============================================================
# DuckDB 线程本地连接 (避免每次 connect 开销)
# ============================================================

# 2026-08-26: 进程内 sync_incremental 单次保护
# 4 worker 各自调 sync_stock → 各自调 sync_incremental → 全市场补齐跑 4 次
# 用 _synced_in_process 标志, 第一次跑后同进程内直接跳过 (parquet 已经是最新)
import threading as _threading
_sync_lock = _threading.Lock()
_synced_in_process: bool = False

_tl = threading.local()  # thread-local duckdb 连接


def _conn():
    """每线程复用同一个 duckdb 连接，避免每次 connect() 开销。"""
    import duckdb
    if not hasattr(_tl, "con"):
        _tl.con = duckdb.connect()
    return _tl.con


# ============================================================
# 1. K线 写入/读取 (data/history/daily/)
# ============================================================

def _append_records(records: list[dict]):
    """把 records 写入按年分片的 parquet 文件。records 可能跨多年。"""
    if not records:
        return 0

    import duckdb
    import pandas as pd

    df = pd.DataFrame(records)
    # 统一字段类型
    df["trade_date"] = df["trade_date"].astype(str)
    for col in ["open", "high", "low", "close", "pre_close", "pct_chg"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["quarter"] = df["trade_date"].apply(_quarter_of)
    total = 0

    for quarter, group in df.groupby("quarter"):
        group = group.drop(columns=["quarter"])
        path = _parquet_path(quarter)

        if path.exists():
            # 读旧数据，去重后合并写回
            old_df = duckdb.execute(f"SELECT * FROM read_parquet('{path}')").df()
            combined = pd.concat([old_df, group], ignore_index=True)
            combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
            combined = combined.sort_values(["trade_date", "ts_code"])
        else:
            combined = group.sort_values(["trade_date", "ts_code"])

        duckdb.execute(
            f"COPY (SELECT * FROM combined) TO '{path}' (FORMAT PARQUET)"
        )
        total += len(group)

    return total


def read_kline(ts_code: str, start_date: str = "", end_date: str = "", limit: int = 0) -> list[dict]:
    """从本地 parquet 读单只股票的K线，格式与 tushare get_daily 一致。

    Args:
        ts_code: 如 '002371.SZ'
        start_date/end_date: YYYYMMDD，不传则读全量
        limit: 取最近N根，0=全部
    """
    try:
        import duckdb
        from datetime import datetime, timedelta

        all_files = sorted(HISTORY_DIR.glob("*.parquet"))
        if not all_files:
            return []

        # 按 limit 推算需要哪些季度文件（每季度约 63 交易日）
        # 文件名格式：YYYYQN（新）或 YYYY（旧年度文件，迁移期间兼容）
        if limit > 0 and not start_date:
            need_quarters = max(1, (limit // 63) + 2)  # 多加 2 个季度保险
            now = datetime.now()
            cur_q = (now.month - 1) // 3 + 1
            # 往前推 need_quarters 个季度
            y, q = now.year, cur_q
            min_quarter = None
            for _ in range(need_quarters - 1):
                q -= 1
                if q == 0:
                    q = 4
                    y -= 1
            min_quarter = f"{y}Q{q}"
            # 兼容旧年度文件：年度文件 stem 是纯数字
            def _file_ok(f):
                s = f.stem
                if s.isdigit():  # 旧年度文件
                    return int(s) >= y
                return s >= min_quarter  # 季度文件按字典序比较
            files = [f for f in all_files if _file_ok(f)]
            if not files:
                files = all_files
        elif start_date:
            min_quarter = _quarter_of(start_date)
            min_year = int(start_date[:4])
            def _file_ok(f):
                s = f.stem
                if s.isdigit():
                    return int(s) >= min_year
                return s >= min_quarter
            files = [f for f in all_files if _file_ok(f)]
            if not files:
                files = all_files
        else:
            files = all_files

        glob_pattern = "', '".join(str(f) for f in files)
        where = [f"ts_code = '{ts_code}'"]
        if start_date:
            where.append(f"trade_date >= '{start_date}'")
        if end_date:
            where.append(f"trade_date <= '{end_date}'")
        where_sql = " AND ".join(where)
        sql = f"""
            SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, amount, pct_chg
            FROM read_parquet(['{glob_pattern}'])
            WHERE {where_sql}
            ORDER BY trade_date
        """
        if limit:
            sql = f"SELECT * FROM ({sql}) t ORDER BY trade_date DESC LIMIT {limit}"
            sql = f"SELECT * FROM ({sql}) t ORDER BY trade_date"
        df = _conn().execute(sql).df()
        return df.to_dict("records")
    except Exception as e:
        print(f"  ⚠️ read_kline {ts_code} 失败: {e}", file=sys.stderr)
        return []


def has_data_for_date(trade_date: str) -> bool:
    """检查本地是否已有某交易日的数据。"""
    try:
        import duckdb
        year = trade_date[:4]
        path = _parquet_path(int(year))
        if not path.exists():
            return False
        result = duckdb.execute(
            f"SELECT COUNT(*) FROM read_parquet('{path}') WHERE trade_date = '{trade_date}'"
        ).fetchone()
        return result[0] > 0 if result else False
    except Exception:
        return False


def _get_local_min_date() -> str | None:
    """返回本地所有 parquet 里最早的 trade_date (YYYYMMDD)，没有数据返回 None。"""
    try:
        import duckdb
        files = sorted(HISTORY_DIR.glob("*.parquet"))
        if not files:
            return None
        glob_expr = str(HISTORY_DIR / "*.parquet")
        result = duckdb.execute(
            f"SELECT MIN(trade_date) FROM read_parquet('{glob_expr}')"
        ).fetchone()
        return result[0] if result else None
    except Exception:
        return None


def _get_local_max_date() -> str | None:
    """返回本地所有 parquet 里最新的 trade_date (YYYYMMDD)，没有数据返回 None。"""
    try:
        import duckdb
        files = sorted(HISTORY_DIR.glob("*.parquet"))
        if not files:
            return None
        # 只扫最近两个文件（季度或年度），快
        recent = [str(f) for f in files[-2:]]
        glob_expr = "', '".join(recent)
        result = duckdb.execute(
            f"SELECT MAX(trade_date) FROM read_parquet(['{glob_expr}'])"
        ).fetchone()
        return result[0] if result else None
    except Exception as e:
        print(f"  ⚠️ 读本地最新日期失败: {e}", file=sys.stderr)
        return None


# ============================================================
# 2. K线 sync (data/history/daily/)
# ============================================================

INDEX_CODES = ["000001.SH", "000300.SH", "000688.SH", "399001.SZ", "399006.SZ", "930955.CSI"]


def _get_index_max_date() -> str | None:
    """查4个指数在 parquet 里的最新 trade_date。"""
    import duckdb
    glob_expr = str(HISTORY_DIR / "*.parquet")
    files = sorted(HISTORY_DIR.glob("*.parquet"))
    if not files:
        return None
    codes = ", ".join(f"'{c}'" for c in INDEX_CODES)
    try:
        result = duckdb.execute(
            f"SELECT MAX(trade_date) FROM read_parquet('{glob_expr}') WHERE ts_code IN ({codes})"
        ).fetchone()
        return result[0] if result and result[0] else None
    except Exception:
        return None


def sync_incremental(target_date: str | None = None) -> int:
    """增量同步：只拉本地缺失的交易日。

    Returns: 新增的 bar 数量

    2026-08-26: 加跨进程单次保护 (文件锁), 避免 4 worker 各跑 1 次全市场补齐
    之前 bug: 4 worker → 4 次 sync_incremental → Tushare 限流 + 重复拉数据
    修法: flock 跨进程互斥 + 进程内标志, 重复调用秒返回
    """
    from tools.fetch.tushare_fetcher import get_daily_by_date, get_index_daily

    # 进程内单次保护 (同进程内多次调)
    global _synced_in_process
    with _sync_lock:
        if _synced_in_process and target_date is None:
            return 0

    # 跨进程保护 (文件锁, fcntl) — 4 worker 各自独立 Python 进程
    if target_date is None:
        lock_path = Path("/tmp/sync_incremental.lock")
        try:
            import fcntl
            lock_fd = open(lock_path, "w")
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                # 其它 worker 正在跑, 等 0.5s 后让它结束, 再走进程内单次保护
                lock_fd.close()
                import time as _time
                _time.sleep(0.5)
                with _sync_lock:
                    _synced_in_process = True
                return 0
            # 拿到锁, 标记进程内标志
            with _sync_lock:
                _synced_in_process = True
            # 注意: 锁在函数末尾释放
            try:
                return _do_sync_incremental(target_date)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
        except ImportError:
            # fcntl 不可用 (Windows 等), 退回到无锁模式
            with _sync_lock:
                _synced_in_process = True
            return _do_sync_incremental(target_date)
    else:
        return _do_sync_incremental(target_date)


def _do_sync_incremental(target_date: str | None = None) -> int:
    """实际 sync 逻辑 (被 sync_incremental 调用)"""
    from tools.fetch.tushare_fetcher import get_daily_by_date, get_index_daily

    today = target_date or _today()
    max_local = _get_local_max_date()

    if max_local is None:
        print("  ⚠️ 本地无数据，请先运行 --init 建档", file=sys.stderr)
        return 0

    if max_local >= today:
        print(f"  ✅ 已是最新 (本地最新: {max_local})")
        return 0
    # 找缺失的交易日
    next_date = (datetime.strptime(max_local, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    missing = _get_trading_dates(next_date, today)
    # 过滤掉已有的
    missing = [d for d in missing if not has_data_for_date(d)]

    if not missing:
        print(f"  ✅ 无缺失数据 (本地最新: {max_local})")
    else:
        print(f"  📥 需补 {len(missing)} 个交易日: {missing[0]} ~ {missing[-1]}")
        all_records = []
        for date in missing:
            records, status = get_daily_by_date(date)
            if not records:
                if "频率" in str(status) or "超限" in str(status) or "rate" in str(status).lower():
                    if all_records:
                        _append_records(all_records)
                    print(f"  ⚠️ 限流退出 ({status})，已拉数据已写盘")
                    sys.exit(0)
                print(f"    跳过 {date} (状态: {status}, 可能是节假日)")
                continue
            all_records.extend(records)
            print(f"    ✅ {date}: {len(records)} 只")
            time.sleep(0.3)

        # 所有缺失天收集完后一次性写入，避免每天读写一次文件
        if all_records:
            _append_records(all_records)

    # 指数独立补齐（与个股是否有缺口无关，每次都检查）
    # None = 指数从未入库，从个股最早日期开始补全历史
    idx_max = _get_index_max_date() or _get_local_min_date() or "20200101"
    if idx_max < today:
        next_idx = (datetime.strptime(idx_max, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        print(f"  📥 补指数 {next_idx} ~ {today}")
        for idx_code in INDEX_CODES:
            idx_records, status = get_index_daily(idx_code, start_date=next_idx, end_date=today)
            if idx_records:
                _append_records(idx_records)
                print(f"    ✅ {idx_code}: {len(idx_records)} 根")
            else:
                print(f"    ⚠️ {idx_code} 无数据 ({status})")
            time.sleep(0.3)

    # 同步 daily_basic（PE/PB/市值）
    sync_daily_basic(target_date)

    # 同步 stock_basic（名称/行业，30天内不重拉）
    sync_stock_basic()

    # 同步 financials (财务指标, Magic Formula 用, 按季切)
    sync_financials(_date_to_financial_period(today))

    return 0


def sync_init(start_year: int = 2020) -> int:
    """首次建档：按天拉全市场K线，按月批量写入。

    - 每天调一次 daily(trade_date=date)，约5000行，不超 Tushare 6000行限制
    - 每月积攒完写一次文件（减少 IO）
    - 已有日期自动跳过（幂等），限流时先写盘再退出
    """
    from tools.fetch.tushare_fetcher import get_daily_by_date
    from collections import defaultdict

    today = _today()
    start = f"{start_year}0101"
    all_dates = _get_trading_dates(start, today)
    missing = [d for d in all_dates if not has_data_for_date(d)]

    if not missing:
        print(f"  ✅ 已全量建档 ({start_year}-{today})")
        return 0

    total_days = len(all_dates)
    done_days  = total_days - len(missing)
    print(f"  📦 建档 {start_year}-01-01 ~ {today}，共 {total_days} 个交易日，待补 {len(missing)} 天")

    count = 0
    month_buf: dict[str, list] = defaultdict(list)  # "YYYYMM" → records

    for i, date in enumerate(missing):
        records, status = get_daily_by_date(date)
        if not records:
            if "频率" in str(status) or "超限" in str(status) or "rate" in str(status).lower():
                # 限流：把已积攒的写盘再退出
                for ym, recs in month_buf.items():
                    if recs:
                        print(f"    💾 写入 {ym}: {len(recs)} 条")
                        _append_records(recs)
                print(f"  ⚠️ 限流退出 ({status})，下次跑继续")
                sys.exit(0)
            print(f"    跳过 {date} (状态: {status}, 可能是节假日)")
            continue

        ym = date[:6]
        month_buf[ym].extend(records)
        count += len(records)

        # 每月最后一天（或当月最后一条待处理）写入
        next_ym = missing[i + 1][:6] if i + 1 < len(missing) else None
        if next_ym != ym and month_buf[ym]:
            n = _append_records(month_buf[ym])
            pct = (done_days + i + 1) / total_days * 100
            print(f"    💾 {ym}: {n} 条写入 [{pct:.0f}%]")
            month_buf[ym] = []

        time.sleep(0.15)

    return count


# ============================================================
# 3. daily_basic sync + read (data/history/daily_basic/)
# ============================================================

def _get_db_max_date() -> str | None:
    """返回本地 daily_basic 最新 trade_date。"""
    try:
        import duckdb
        files = sorted(DAILY_BASIC_DIR.glob("*.parquet"))
        if not files:
            return None
        recent = [str(f) for f in files[-2:]]
        glob_expr = "', '".join(recent)
        result = duckdb.execute(
            f"SELECT MAX(trade_date) FROM read_parquet(['{glob_expr}'])"
        ).fetchone()
        return result[0] if result else None
    except Exception:
        return None


def _append_daily_basic(records: list[dict]):
    """把 records 按季度写入 daily_basic parquet。"""
    if not records:
        return
    import duckdb
    import pandas as pd
    df = pd.DataFrame(records)
    df["trade_date"] = df["trade_date"].astype(str)
    df["quarter"] = df["trade_date"].apply(_quarter_of)
    for quarter, qdf in df.groupby("quarter"):
        qdf = qdf.drop(columns=["quarter"])
        path = _db_parquet_path(quarter)
        if path.exists():
            old = duckdb.execute(f"SELECT * FROM read_parquet('{path}')").df()
            qdf = pd.concat([old, qdf]).drop_duplicates(
                subset=["ts_code", "trade_date"], keep="last"
            ).sort_values(["trade_date", "ts_code"])
        else:
            qdf = qdf.sort_values(["trade_date", "ts_code"])
        duckdb.execute(f"COPY (SELECT * FROM qdf) TO '{path}' (FORMAT PARQUET)")


def _prune_daily_basic_old(keep_days: int = 365):
    """删除超过 keep_days 天的季度文件（默认保留近1年）。"""
    import duckdb
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y%m%d")
    cutoff_q = _quarter_of(cutoff)
    for f in sorted(DAILY_BASIC_DIR.glob("*.parquet")):
        if f.stem < cutoff_q:
            f.unlink()
            print(f"  🗑️  删除过期: {f.name}")


def sync_daily_basic(target_date: str | None = None) -> int:
    """增量同步 daily_basic：只拉缺失的交易日，保留近1年数据。

    Returns: 新增的 bar 数量
    """
    from tools.fetch.tushare_fetcher import get_daily_basic_by_date

    today = target_date or _today()
    max_local = _get_db_max_date()

    # 首次同步：拉近1年
    if max_local is None:
        start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
        print(f"  🏗️  daily_basic 首次建档，从 {start} 开始...")
        max_local = (datetime.strptime(start, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")

    if max_local >= today:
        print(f"  ✅ daily_basic 已是最新 (本地最新: {max_local})")
        _prune_daily_basic_old()
        return 0

    next_date = (datetime.strptime(max_local, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    missing = _get_trading_dates(next_date, today)
    missing = [d for d in missing if not has_data_for_date(d)]  # 跳过 K 线也没有的日期

    if not missing:
        print(f"  ✅ daily_basic 已是最新 (本地最新: {max_local})")
        return 0

    print(f"  📥 daily_basic 同步 {len(missing)} 天...")
    total = 0
    for date in missing:
        records, status = get_daily_basic_by_date(date)
        if records:
            _append_daily_basic(records)
            total += len(records)
            print(f"    💾 {date}: {len(records)} 条")
        time.sleep(0.2)  # Tushare 频控

    _prune_daily_basic_old()
    return total


def read_daily_basic(ts_code: str) -> dict:
    """读取单只股票最新一天的 daily_basic。"""
    try:
        import duckdb
        files = sorted(DAILY_BASIC_DIR.glob("*.parquet"))
        if not files:
            return {}
        recent = [str(f) for f in files[-2:]]
        glob_expr = "', '".join(recent)
        result = duckdb.execute(f"""
            SELECT * FROM read_parquet(['{glob_expr}'])
            WHERE ts_code = '{ts_code}'
            ORDER BY trade_date DESC
            LIMIT 1
        """).fetchdf()
        if result.empty:
            return {}
        return result.iloc[0].to_dict()
    except Exception:
        return {}


# ============================================================
# 4. stock_basic sync + read (data/history/stock_basic/)
# ============================================================

def sync_stock_basic() -> int:
    """一次拉全市场 stock_basic，存 parquet。30天内不重拉。

    Returns: 股票数量
    """
    import pandas as pd

    if STOCK_BASIC_PARQUET.exists():
        age = time.time() - STOCK_BASIC_PARQUET.stat().st_mtime
        if age < 30 * 24 * 3600:
            print(f"  ✅ stock_basic 已是最新 (上次更新 {age/86400:.0f} 天前)")
            return 0

    print("  📥 stock_basic 全市场建档...")
    from tools.fetch.tushare_fetcher import _safe_call
    data, status = _safe_call(
        "stock_basic", exchange="", list_status="L",
        fields="ts_code,name,industry,list_date,market,total_share,float_share",
    )
    if not data:
        print(f"  ⚠️ stock_basic 拉取失败: {status}")
        return 0

    df = pd.DataFrame(data)
    df["code"] = df["ts_code"].str.split(".").str[0]
    import duckdb
    duckdb.execute(f"COPY (SELECT * FROM df) TO '{STOCK_BASIC_PARQUET}' (FORMAT PARQUET)")
    print(f"  ✅ stock_basic 建档完成: {len(df)} 只 → {STOCK_BASIC_PARQUET}")
    return len(df)


def read_stock_basic(code: str) -> dict:
    """读取单只股票的 stock_basic（从 parquet）。"""
    try:
        import duckdb
        if not STOCK_BASIC_PARQUET.exists():
            return {}
        result = duckdb.execute(f"""
            SELECT * FROM read_parquet('{STOCK_BASIC_PARQUET}')
            WHERE code = '{code}'
            LIMIT 1
        """).fetchdf()
        if result.empty:
            return {}
        row = result.iloc[0].to_dict()
        return {
            "name":        row.get("name", ""),
            "industry":    row.get("industry", ""),
            "list_date":   str(row.get("list_date", "")),
            "market":      row.get("market", ""),
            "total_share": row.get("total_share", 0),
            "float_share": row.get("float_share", 0),
        }
    except Exception:
        return {}


# ============================================================
# 5. financials sync + read (data/history/financials/) — Magic Formula 用
# ============================================================
# 存储: data/history/financials/{YYYYQN}.parquet, 1 季 1 文件
# 字段: ts_code, code, end_date, ebit, fixed_assets, networking_capital,
#       interestdebt, netdebt, eps_period, industry, fetched_at, fetch_status, error_msg
# 续跑规则: fetch_status='ok' 永不再拉 (季报定稿不变)
#           fetch_status!='ok' 下次重试 (不限次数)
# ============================================================


def _fin_period_to_quarter(period: str) -> str:
    """Tushare period '20250630' → '2025Q2' (跟 daily parquet 文件名对齐)

    03-31 → Q1, 06-30 → Q2, 09-30 → Q3, 12-31 → Q4
    """
    m = int(period[4:6])
    return f"{period[:4]}Q{(m - 1) // 3 + 1}"


def _fin_path(period: str) -> Path:
    return FIN_DIR / f"{_fin_period_to_quarter(period)}.parquet"


def _fin_now() -> str:
    """ISO 时间戳, 给 fetched_at 用"""
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _fin_load_existing(period: str) -> dict[str, dict]:
    """读现有 parquet, 返 {code: row_dict}, 包含 status='ok' 和失败的 (sync 用)

    跟 read_financials 的区别: read_financials 只返 status='ok' (分析层用),
    这个返**所有** status (sync 用, 看哪些要补拉)。
    """
    try:
        import duckdb
        path = _fin_path(period)
        if not path.exists():
            return {}
        df = duckdb.execute(f"SELECT * FROM read_parquet('{path}')").df()
        if df.empty:
            return {}
        return {str(r["code"]): r.to_dict() for _, r in df.iterrows()}
    except Exception:
        return {}


def _fin_load_industry_map(codes: list[str]) -> dict[str, str]:
    """从 stock_basic.parquet 取 industry, 返 {code: industry}

    缓存到本次进程 (sync_financials 调用一次, 批量 fetch_one 共享)
    """
    if not codes:
        return {}
    try:
        import duckdb
        if not STOCK_BASIC_PARQUET.exists():
            return {}
        # 取 codes 子集, 减少扫描
        codes_str = ", ".join(f"'{c}'" for c in codes)
        df = duckdb.execute(
            f"""
            SELECT code, industry FROM read_parquet('{STOCK_BASIC_PARQUET}')
            WHERE code IN ({codes_str})
            """
        ).df()
        return {str(r["code"]): str(r.get("industry", "") or "") for _, r in df.iterrows()}
    except Exception:
        return {}


def _fin_load_tech_codes() -> list[str]:
    """科技股名单 (从 stock_basic.parquet 申万行业关键词 match, 跟 signal_cache_warmup 一致)"""
    try:
        from tools.batch.signal_cache_warmup import _load_tech_codes as _wl_tech
        # 直接复用 signal_cache_warmup 的版本 (单一定义)
        return _wl_tech()
    except Exception as exc:
        print(f"  ⚠️ 科技股 filter 失败: {exc}, 降级用 watchlist")
        import json
        wl = json.load(open("data/watchlist.json"))["stocks"]
        return [s["code"] for s in wl]


def sync_financials(period: str, codes: list[str] = None, force: bool = False) -> int:
    """拉 Tushare fina_indicator 写 parquet (按季切, 跟 sync_stock_basic 同 pattern)

    v3.2: 5000 积分档用 fina_indicator_vip 1 次拿全市场, 客户端筛 + upsert。
    续跑规则: fetch_status='ok' / 'skip' 都跳过 (DB 有数据不 call API);
               'no_data' / 'timeout' 视为待拉; 不在 parquet 里视为待拉。

    Args:
        period: Tushare 原生 '20250630' / '20251231' 格式
        codes:  默认 None = 用科技股名单 (从 stock_basic.parquet 关键词 match)
        force:  True = 强刷, 全部 to_pull (不查 status), 覆盖已 ok 的

    Returns:
        写入行数 (新增 + 覆盖)
    """
    from tools.fetch.tushare_fetcher import _safe_call
    import pandas as pd

    if codes is None:
        codes = _fin_load_tech_codes()
    if not codes:
        print(f"  ⚠️ sync_financials {period}: 没有 codes")
        return 0

    # Phase 1: 算 to_pull (DB-only, 不调 API)
    existing = _fin_load_existing(period)
    if force:
        to_pull = list(codes)
    else:
        to_pull = []
        for c in codes:
            row = existing.get(c)
            if row is None:
                to_pull.append(c)  # parquet 里没这行 → 必拉
            elif row.get("fetch_status") in ("ok", "skip"):
                continue            # 已成功 / 已确认没数据 → 永久跳过
            else:
                to_pull.append(c)  # 别的 (no_data / timeout) → 必拉

    n_skip_ok = sum(1 for c in codes if existing.get(c, {}).get("fetch_status") == "ok")
    n_skip_skip = sum(1 for c in codes if existing.get(c, {}).get("fetch_status") == "skip")
    if n_skip_ok or n_skip_skip:
        print(f"  ⏭ financials {period}: {n_skip_ok} ok / {n_skip_skip} skip, 跳过")
    if not to_pull:
        return 0   # DB 全 ok 或全 skip, 0 次 API

    # Phase 2: 1 次 VIP API 拿全市场 (5000 积分档, 不限流)
    print(f"  📡 fina_indicator_vip period={period} (目标 {len(to_pull)} 只)")
    data, status = _safe_call(
        "fina_indicator_vip",
        period=period,
        fields=(
            "ts_code,end_date,ebit,fixed_assets,networking_capital,"
            "interestdebt,netdebt,eps"
        ),
    )
    if not data:
        print(f"  ⚠️ VIP 拉取失败: {status}, {len(to_pull)} 只标 skip")
        industry_map = _fin_load_industry_map(to_pull)
        rows = [
            {
                "ts_code": _to_ts_code(c), "code": c, "end_date": period,
                "ebit": None, "fixed_assets": None, "networking_capital": None,
                "interestdebt": None, "netdebt": None, "eps_period": None,
                "industry": industry_map.get(c, ""),
                "fetched_at": _fin_now(),
                "fetch_status": "skip",
                "error_msg": f"VIP 拉取失败: {status}",
            }
            for c in to_pull
        ]
        n = upsert_financials(period, rows)
        print(f"  ✅ financials {period}: 写 {n} 行 (全 skip)")
        return n

    df_all = pd.DataFrame(data)
    df_all["code"] = df_all["ts_code"].str.split(".").str[0]

    # Phase 3: 客户端筛 (to_pull ∩ VIP 返的行 = 命中)
    df_hit = df_all[df_all["code"].isin(to_pull)].copy()

    # Phase 4: 标记 industry + fetched_at + 改字段名 (Tushare 'eps' → 我们 'eps_period')
    industry_map = _fin_load_industry_map(to_pull)
    df_hit["industry"] = df_hit["code"].map(industry_map).fillna("")
    df_hit["fetched_at"] = _fin_now()
    df_hit["fetch_status"] = "ok"
    df_hit["error_msg"] = None
    df_hit["eps_period"] = df_hit["eps"]
    df_hit = df_hit.drop(columns=["eps"])

    # Phase 5: VIP 没返的票, 标 skip (永久不再拉)
    hit_codes = set(df_hit["code"].tolist())
    not_in_vip = [c for c in to_pull if c not in hit_codes]
    skip_rows = [
        {
            "ts_code": _to_ts_code(c), "code": c, "end_date": period,
            "ebit": None, "fixed_assets": None, "networking_capital": None,
            "interestdebt": None, "netdebt": None, "eps_period": None,
            "industry": industry_map.get(c, ""),
            "fetched_at": _fin_now(),
            "fetch_status": "skip",
            "error_msg": f"VIP 未返 (季报 {period} 未披露 / 公司退市)",
        }
        for c in not_in_vip
    ]

    # Phase 6: 1 次 upsert (ok + skip 一起, 保护已有 ok 不被新 skip 覆盖)
    rows = df_hit.to_dict("records") + skip_rows
    if not rows:
        return 0
    n = upsert_financials(period, rows)
    n_ok = len(df_hit)
    n_skip = len(not_in_vip)
    print(f"  ✅ financials {period}: 写 {n} 行 ({n_ok} ok / {n_skip} skip)")
    return n


def read_financials(code: str, lookback_quarters: int = 4) -> list[dict]:
    """跨多季 parquet 读 (跟 read_stock_basic 同 pattern, duckdb glob)

    只返 fetch_status='ok' 的行, 按 end_date 升序。给分析层用 (RenderData / ROC / EY)。
    """
    try:
        import duckdb
        if not FIN_DIR.exists():
            return []
        ts_code = _to_ts_code(code)
        df = duckdb.execute(
            f"""
            SELECT * FROM read_parquet('{FIN_DIR}/*.parquet')
            WHERE ts_code = ? AND fetch_status = 'ok'
            ORDER BY end_date DESC
            LIMIT ?
            """,
            [ts_code, lookback_quarters],
        ).df()
        if df.empty:
            return []
        return df.sort_values("end_date", ascending=True).to_dict("records")
    except Exception:
        return []


def upsert_financials(period: str, rows: list[dict]) -> int:
    """单季 financials parquet upsert, 按 (code, end_date) 联合主键

    跟 _append_daily_basic 同 pattern, 走 pandas 内存拼 + drop_duplicates。

    Args:
        period: Tushare 原生 '20250630' 格式
        rows:   list[dict], 每行需含 code + end_date + fetch_status
                fetch_status='ok' → 无条件覆盖旧行
                fetch_status!='ok' → 不覆盖已存在的 status='ok' 行 (失败不破坏成功)
                新行 → 追加

    Returns:
        写入后该季 parquet 总行数
    """
    import pandas as pd

    path = _fin_path(period)
    if not rows:
        if path.exists():
            return len(pd.read_parquet(path))
        return 0

    new_df = pd.DataFrame(rows)
    if path.exists():
        old_df = pd.read_parquet(path)
        # 保护逻辑: 失败的行不覆盖成功的
        ok_mask = old_df["fetch_status"] == "ok"
        ok_keys = set(
            zip(
                old_df.loc[ok_mask, "code"].astype(str),
                old_df.loc[ok_mask, "end_date"].astype(str),
            )
        )
        new_ok = new_df[new_df["fetch_status"] == "ok"]
        new_fail = new_df[new_df["fetch_status"] != "ok"]
        # 失败的行只保留不在 ok_keys 里的 (新失败 / 替换旧失败)
        new_fail = new_fail[
            ~new_fail.apply(
                lambda r: (str(r["code"]), str(r["end_date"])) in ok_keys,
                axis=1,
            )
        ]
        merged = pd.concat([old_df, new_ok, new_fail], ignore_index=True)
        merged = merged.drop_duplicates(subset=["code", "end_date"], keep="last")
    else:
        merged = new_df.drop_duplicates(subset=["code", "end_date"], keep="last")

    FIN_DIR.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(path, index=False)
    return len(merged)


def _date_to_financial_period(target_date: str) -> str:
    """任意 YYYYMMDD → 最近季报期 (e.g. 20260901 → 20260630, 20260115 → 20251231)

    季报截止日: 03-31 / 06-30 / 09-30 / 12-31
    从最近的季报往回找, 返第一个 <= target_date 的季 (逆序避免上面 6-30 返 3-31 的 bug)
    """
    from datetime import datetime
    d = datetime.strptime(target_date, "%Y%m%d")
    # 逆序: Q4 → Q3 → H1 → Q1, 找第一个 <= target_date 的
    quarter_ends = [(12, 31), (9, 30), (6, 30), (3, 31)]
    for m, day in quarter_ends:
        if (d.month, d.day) >= (m, day):
            return f"{d.year}{m:02d}{day:02d}"
    # 1-2 月还在, 上年 Q4
    return f"{d.year - 1}1231"


# ============================================================
# 6. DataStore — 统一数据访问入口 (分析层用)
# ============================================================

class DataStore:
    """统一数据访问入口，所有方法均为 classmethod，无需实例化。"""

    @classmethod
    def get_kline(cls, code: str, limit: int = 0) -> list[dict]:
        """日线 K线，升序。limit=0 表示全量（默认取 config 里的 kline_days）。
        统一字段名：vol → volume（parquet 存的是 Tushare 原始 vol，计算层期望 volume）。
        """
        if limit == 0:
            limit = _PROJECT_CFG.get("data", {}).get("kline_days", 1250)
        ts_code = _to_ts_code(code)
        rows = read_kline(ts_code, limit=limit)
        # 统一 vol → volume，保留 vol 做兼容
        for r in rows:
            if 'vol' in r and 'volume' not in r:
                r['volume'] = r['vol']
        return rows

    @classmethod
    def get_weekly(cls, code: str, limit: int = 0) -> list[dict]:
        """周线，从日线聚合，升序。"""
        from tools.fetch.data_fetcher import _synthesize_weekly
        kline = cls.get_kline(code, limit=limit * 5 if limit else 0)
        return _synthesize_weekly(kline)

    @classmethod
    def get_daily_basic(cls, code: str) -> dict:
        """PE/PB/市值等估值快照，从 parquet 读（全市场，近1年）。"""
        return read_daily_basic(_to_ts_code(code))

    @classmethod
    def get_stock_basic(cls, code: str) -> dict:
        """行业/名称/上市日期等静态信息，从 parquet 读（全市场）。"""
        return read_stock_basic(code)

    @classmethod
    def get_eps(cls, code: str) -> list[dict]:
        """EPS 机构一致预期表（每月更新）。"""
        from tools.eps_consensus_cache import get_eps
        return get_eps(code)

    @classmethod
    def get_ctx(cls, code: str, kline_only: bool = False, limit: int = 0):
        """返回 RawContext（L1 数据层唯一入口）。

        kline_only=True: 只读 K线，跳过 stock_basic/daily_basic/eps。
        limit: K线条数上限，0=使用 config 默认值（kline_days）。
        """
        from tools.analysis.analysis_engine import RawContext
        from tools.fetch.data_fetcher import _synthesize_weekly

        kline  = cls.get_kline(code, limit=limit)
        weekly = _synthesize_weekly(kline)
        close  = kline[-1]["close"] if kline else 0.0

        if kline_only:
            return RawContext(
                kline=kline, weekly=weekly,
                eps_table=[], fflow={}, moneyflow=[],
                current_price=close, market_cap_yi=0.0,
                industry="", code=code, name="",
            )

        sb  = cls.get_stock_basic(code)
        db  = cls.get_daily_basic(code)
        eps = cls.get_eps(code)

        return RawContext(
            kline=kline, weekly=weekly,
            eps_table=eps, fflow={}, moneyflow=[],
            current_price=db.get("close") or close,
            market_cap_yi=db.get("total_mv") or 0.0,
            industry=sb.get("industry", ""),
            code=code, name=_INDEX_NAMES.get(code) or sb.get("name", ""),
        )

    @classmethod
    def get_financials(cls, code: str, lookback_quarters: int = 4) -> list[dict]:
        """单只最近 N 季的财务数据, 按 end_date 升序 (跟 get_eps 同 pattern)

        实际逻辑在本文件 read_financials, 这层只是 I/O 入口。
        只返 fetch_status='ok' 的行, 给分析层用 (RenderData / ROC / EY)。

        Args:
            code:              '600519' / '600519.SH' 都行
            lookback_quarters: 取最近几个季度 (默认 4, TTM 拼装用)

        Returns:
            list[dict], 按 end_date 升序
            空 list = 该 code 没数据 / 全部拉取失败
        """
        return read_financials(code, lookback_quarters)

    @classmethod
    def list_codes(cls) -> list[str]:
        """返回本地历史库里所有有数据的股票代码（6位，不带交易所后缀）。"""
        try:
            import duckdb
            files = list(HISTORY_DIR.glob("*.parquet"))
            if not files:
                return []
            result = duckdb.execute(
                "SELECT DISTINCT ts_code FROM read_parquet('data/history/daily/*.parquet')"
            ).fetchall()
            codes = []
            for (ts_code,) in result:
                code = ts_code.split(".")[0]
                codes.append(code)
            return sorted(codes)
        except Exception:
            return []

    @classmethod
    def watchlist_codes(cls) -> list[str]:
        """返回 watchlist.json 里的股票代码列表。"""
        try:
            d = json.loads(Path("data/watchlist.json").read_text(encoding="utf-8"))
            return [s["code"] for s in d.get("stocks", [])]
        except Exception:
            return []


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="K线历史库增量同步")
    parser.add_argument("--init", action="store_true", help="首次建档，拉全量历史")
    parser.add_argument("--start-year", type=int, default=2020, help="建档起始年份 (默认2020)")
    parser.add_argument("--date", help="指定同步到某天 YYYYMMDD (默认今天)")
    args = parser.parse_args()

    t0 = time.time()

    if args.init:
        print(f"🏗️  首次建档模式 (起始年: {args.start_year})")
        n = sync_init(args.start_year)
        print(f"\n✅ 建档完成: {n} 条K线，耗时 {time.time()-t0:.1f}s")
    else:
        print("🔄 增量同步模式")
        n = sync_incremental(args.date)
        if n:
            print(f"\n✅ 同步完成: 新增 {n} 条K线，耗时 {time.time()-t0:.1f}s")

    # 4 块统一状态报告 (K线 / daily_basic / stock_basic / financials)
    print_status_report()


def print_status_report() -> None:
    """sync 完后打印 4 块 parquet 的最新日期 / 行数 / 状态分布"""
    import duckdb

    print("\n📊 本地库状态:")

    # 1. K线
    try:
        files = list(HISTORY_DIR.glob("*.parquet"))
        if files:
            n, min_d, max_d = duckdb.execute(
                f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) "
                f"FROM read_parquet('{HISTORY_DIR}/*.parquet')"
            ).fetchone()
            print(f"  K线:        {n:>9,} 条 | {min_d} ~ {max_d} | {len(files)} 个文件")
        else:
            print(f"  K线:        (空)")
    except Exception as e:
        print(f"  K线:        ⚠️ {e}")

    # 2. daily_basic
    try:
        files = list(DAILY_BASIC_DIR.glob("*.parquet"))
        if files:
            n, min_d, max_d, code_count = duckdb.execute(
                f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date), COUNT(DISTINCT ts_code) "
                f"FROM read_parquet('{DAILY_BASIC_DIR}/*.parquet')"
            ).fetchone()
            print(f"  daily_basic: {n:>9,} 条 | {min_d} ~ {max_d} | {code_count} 只")
        else:
            print(f"  daily_basic: (空)")
    except Exception as e:
        print(f"  daily_basic: ⚠️ {e}")

    # 3. stock_basic
    try:
        if STOCK_BASIC_PARQUET.exists():
            df = duckdb.execute(
                f"SELECT COUNT(*), MAX(industry) FROM read_parquet('{STOCK_BASIC_PARQUET}')"
            ).fetchone()
            n, last_industry = df
            age_days = (time.time() - STOCK_BASIC_PARQUET.stat().st_mtime) / 86400
            print(f"  stock_basic: {n:>9} 只 | 上次更新 {age_days:.1f} 天前 | 行业示例: {last_industry}")
        else:
            print(f"  stock_basic: (空)")
    except Exception as e:
        print(f"  stock_basic: ⚠️ {e}")

    # 4. financials (按 status 分布, 最新季)
    try:
        if FIN_DIR.exists():
            # 最新的季文件
            files = sorted(FIN_DIR.glob("*.parquet"))
            if files:
                latest = files[-1]
                n_total, n_ok, n_skip, n_other = duckdb.execute(
                    f"SELECT COUNT(*), "
                    f"SUM(CASE WHEN fetch_status='ok' THEN 1 ELSE 0 END), "
                    f"SUM(CASE WHEN fetch_status='skip' THEN 1 ELSE 0 END), "
                    f"SUM(CASE WHEN fetch_status NOT IN ('ok','skip') THEN 1 ELSE 0 END) "
                    f"FROM read_parquet('{latest}')"
                ).fetchone()
                print(f"  financials:  最新 {latest.stem} | {n_total} 只 ({n_ok} ok / {n_skip} skip / {n_other} 失败)")
                # 全部季汇总
                if len(files) > 1:
                    n_all = duckdb.execute(
                        f"SELECT COUNT(*) FROM read_parquet('{FIN_DIR}/*.parquet')"
                    ).fetchone()[0]
                    print(f"              总计: {n_all} 只 | {len(files)} 个季文件")
            else:
                print(f"  financials:  (空)")
        else:
            print(f"  financials:  (空)")
    except Exception as e:
        print(f"  financials: ⚠️ {e}")


if __name__ == "__main__":
    main()
