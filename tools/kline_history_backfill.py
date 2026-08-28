"""
tools/kline_history_backfill.py — 本地 K 线历史库增量同步

存储结构:
  data/history/daily/
    2021.parquet   ← 全市场当年所有交易日K线
    2022.parquet
    ...
    2026.parquet   ← 当年文件，每天增量追加

用法:
  # 首次建档 (拉5年历史, 约60次调用)
  tools/with_venv.sh python -m tools.kline_history_backfill --init

  # 日常增量 (只补缺失交易日, 通常1次调用)
  tools/with_venv.sh python -m tools.kline_history_backfill

  # 指定日期
  tools/with_venv.sh python -m tools.kline_history_backfill --date 20260822
"""

import argparse
import json
import sys
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path

HISTORY_DIR = Path("data/history/daily")
HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 2026-08-26: 进程内 sync_incremental 单次保护
# refresh_all.sh 4 worker 各自调 sync_stock → 各自调 sync_incremental → 全市场补齐跑 4 次
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
# 1. 工具函数
# ============================================================

_INDEX_SUFFIX = {
    "000001": "SH", "000300": "SH", "000905": "SH", "000016": "SH",
    "399001": "SZ", "399006": "SZ", "399808": "SZ",
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


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


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
# 2. 写入 parquet
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


# ============================================================
# 3. 读取接口（供 sync_stock 调用）
# ============================================================

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


# ============================================================
# 4. 增量同步
# ============================================================

INDEX_CODES = ["000001.SH", "000300.SH", "000688.SH", "399001.SZ", "399006.SZ"]


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
    之前 bug: refresh_all.sh 4 worker → 4 次 sync_incremental → Tushare 限流 + 重复拉数据
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

    return total


# ============================================================
# 5. daily_basic 增量同步（PE/PB/市值，按季度分片，保留近1年）
# ============================================================

DAILY_BASIC_DIR = Path("data/history/daily_basic")
DAILY_BASIC_DIR.mkdir(parents=True, exist_ok=True)


def _db_parquet_path(quarter: str) -> Path:
    return DAILY_BASIC_DIR / f"{quarter}.parquet"


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
# 6. stock_basic 全市场缓存（名称/行业，一次拉全市场，存 parquet）
# ============================================================

STOCK_BASIC_DIR = Path("data/history/stock_basic")
STOCK_BASIC_DIR.mkdir(parents=True, exist_ok=True)
STOCK_BASIC_PARQUET = STOCK_BASIC_DIR / "stock_basic.parquet"


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

    # 显示本地库状态
    try:
        import duckdb
        files = list(HISTORY_DIR.glob("*.parquet"))
        if files:
            total = duckdb.execute(
                f"SELECT COUNT(*), MIN(trade_date), MAX(trade_date) FROM read_parquet('{HISTORY_DIR}/*.parquet')"
            ).fetchone()
            print(f"📊 本地库: {total[0]:,} 条 | {total[1]} ~ {total[2]} | {len(files)} 个年份文件")
    except Exception:
        pass


if __name__ == "__main__":
    main()
