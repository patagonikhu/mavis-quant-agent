"""
tools/kline_store.py — 统一数据访问层 + sync 工具 (合并自 kline_history_backfill, 2026-09-01)

所有代码通过这里访问数据，不直接读文件。

用法:
    # (self import - same file)

    raw = DataStore.get_raw("002371")       # 替代 json.load(dump)
    ctx = DataStore.get_ctx("002371")       # 替代 RawContext.from_dump(dump)
    kline = DataStore.get_kline("002371")   # 单独取 K线

CLI:
    bash tools/with_venv.sh python -m tools.storage.sync --init
    bash tools/with_venv.sh python -m tools.storage.sync            # 增量同步
    bash tools/with_venv.sh python -m tools.storage.sync --date 20260822

存储:
    data/history/daily/YYYYQN.parquet         # K 线 (按季切)
    data/history/daily_basic/YYYYQN.parquet   # PE/PB/市值
    data/history/stock_basic/stock_basic.parquet  # 全市场名称/行业
    data/history/financials/YYYYQN.parquet    # 财务指标 (Magic Formula 用)

内部依赖:
    tools/eps_consensus_cache.py  — 低频缓存 (data/history/eps/, parquet)
    ..sources.tushare    — Tushare 接口
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

# v6.2.4 改: 路径名 daily_basic → stk_factor
# 实际数据是 stk_factor_pro 拉的 (16 列, 含 ps/dv_ratio/float_share/turnover_rate_f)
# 保留 DAILY_BASIC_DIR 别名给老代码用, 优先用 STK_FACTOR_DIR
STK_FACTOR_DIR = Path("data/history/stk_factor")
STK_FACTOR_DIR.mkdir(parents=True, exist_ok=True)
DAILY_BASIC_DIR = STK_FACTOR_DIR  # v6.2.4 兼容别名, 后续可删

STOCK_BASIC_DIR = Path("data/history/stock_basic")
STOCK_BASIC_DIR.mkdir(parents=True, exist_ok=True)
STOCK_BASIC_PARQUET = STOCK_BASIC_DIR / "stock_basic.parquet"

FIN_DIR = Path("data/history/financials")
FIN_DIR.mkdir(parents=True, exist_ok=True)

# v6.2.5 加: 主力资金历史 (按天拉全市场, 按季存 parquet, 跟 stk_factor 平行)
FFLOW_HISTORY_DIR = Path("data/history/fflow_history")
FFLOW_HISTORY_DIR.mkdir(parents=True, exist_ok=True)

# 2026-09-17 加: THS 同花顺概念板块 (跟 daily 个股完全隔离, 1 概念 1 parquet)
THS_HISTORY_DIR = Path("data/history/ths")
THS_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
(THS_HISTORY_DIR / "kline").mkdir(parents=True, exist_ok=True)


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
    "930955": "中证红利低波100",   # 2026-09-17 加注释: 高股息+低波动 选股指数
    "000922": "中证红利",
}


def _to_ts_code(code: str) -> str:
    """000725 → 000725.SZ / 600000 → 600000.SH / 920045 → 920045.BJ (北交所) / 000300 → 000300.SH (指数优先)

    2026-09-09 修: 加 .BJ 后缀映射 (北交所 920xxx 代码), 之前错误映射成 .SH 导致 K 线查不到
    """
    if "." in code:
        return code
    c = code.strip()
    if c in _INDEX_SUFFIX:
        return f"{c}.{_INDEX_SUFFIX[c]}"
    # 北交所代码段: 920xxx / 430xxx (老三板转板); 老三板 8xxxxx 已退市不处理
    if c.startswith(("920", "430", "831", "832", "833", "834", "835", "836", "837", "838", "839")):
        return f"{c}.BJ"
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

def _append_records_target(records: list[dict], target_dir: Path) -> int:
    """THS 概念板块专用写盘 (1 概念 1 parquet, 不分季度)

    跟 _append_records 区别:
      - 不分 year/quarter
      - 直接拼出 parquet 路径: target_dir/{ts_code}.parquet
      - ts_code 字段做合并主键 (跟 daily 兼容)
    """
    if not records:
        return 0

    import duckdb
    import pandas as pd

    df = pd.DataFrame(records)
    if df.empty:
        return 0
    df["trade_date"] = df["trade_date"].astype(str)
    for col in ["open", "high", "low", "close", "pre_close", "pct_chg", "avg_price", "change", "turnover_rate"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["vol", "amount"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    target_dir.mkdir(parents=True, exist_ok=True)
    total = 0

    # 按 ts_code 分组, 每概念 1 文件
    for ts_code, group in df.groupby("ts_code"):
        path = target_dir / f"{ts_code}.parquet"
        if path.exists():
            old_df = _conn().execute(f"SELECT * FROM read_parquet('{path}')").df()
            # 老 df 可能没新字段, 加空列对齐
            for col in df.columns:
                if col not in old_df.columns:
                    old_df[col] = None
            # 反之同理
            for col in old_df.columns:
                if col not in df.columns:
                    df[col] = None
            combined = pd.concat([old_df, group], ignore_index=True)
            combined = combined.drop_duplicates(subset=["trade_date"], keep="last")
            combined = combined.sort_values(["trade_date"])
        else:
            combined = group.sort_values(["trade_date"])

        _conn().execute(
            f"COPY (SELECT * FROM combined) TO '{path}' (FORMAT PARQUET)"
        )
        total += len(group)

    return total


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
            old_df = _conn().execute(f"SELECT * FROM read_parquet('{path}')").df()
            combined = pd.concat([old_df, group], ignore_index=True)
            combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
            combined = combined.sort_values(["trade_date", "ts_code"])
        else:
            combined = group.sort_values(["trade_date", "ts_code"])

        _conn().execute(
            f"COPY (SELECT * FROM combined) TO '{path}' (FORMAT PARQUET)"
        )
        total += len(group)

    return total


def _read_kline(ts_code: str, start_date: str = "", end_date: str = "", limit: int = 0) -> list[dict]:
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
        print(f"  ⚠️ _read_kline {ts_code} 失败: {e}", file=sys.stderr)
        return []


def has_data_for_date(trade_date: str) -> bool:
    """检查本地是否已有某交易日的数据。"""
    try:
        import duckdb
        year = trade_date[:4]
        path = _parquet_path(int(year))
        if not path.exists():
            return False
        result = _conn().execute(
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
        result = _conn().execute(
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
        result = _conn().execute(
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
        result = _conn().execute(
            f"SELECT MAX(trade_date) FROM read_parquet('{glob_expr}') WHERE ts_code IN ({codes})"
        ).fetchone()
        return result[0] if result and result[0] else None
    except Exception:
        return None


def sync_incremental(target_date: str | None = None) -> int:
    """增量同步：只拉本地缺失的交易日。

    Returns: 新增的 bar 数量

    2026-08-26: 加跨进程单次保护 (文件锁), 避免 4 worker 各跑 1 次全市场补齐
    之前 bug: 4 worker → 4 次 sync_incremental → 重复拉数据
    修法: flock 跨进程互斥 + 进程内标志, 重复调用秒返回
    """
    from .sources.tushare import get_daily_by_date, get_index_daily

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
    from .sources.tushare import get_daily_by_date, get_index_daily

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
                    print(f"  ⚠️ API 频次超限 ({status})，已拉数据已写盘")
                    sys.exit(0)
                print(f"    跳过 {date} (状态: {status}, 可能是节假日)")
                continue
            all_records.extend(records)
            print(f"    ✅ {date} daily: {len(records)} 只")
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
    # v6.2.4 修: sync_daily_basic 不再被 sync_incremental 调, 改由 --stk-factor 接管
    # 之前 sync_daily_basic 写 11 列 (缺 ps/dv/float_share/turnover_rate_f)
    # 现在 daily_basic 走 stk_factor_pro 16 列, 单独由 action_stk_factor 写
    # sync_daily_basic(target_date)  # ⚠️ v6.2.4 弃用, 走 --stk-factor

    # 同步 stock_basic（名称/行业，30天内不重拉）
    sync_stock_basic()

    # 同步 financials (财务指标, Magic Formula 用, 按季切)
    sync_financials(_date_to_financial_period(today))

    return 0


def sync_init(start_year: int = 2020) -> int:
    """首次建档：按天拉全市场K线，按月批量写入。

    - 每天调一次 daily(trade_date=date)，约5000行，不超 Tushare 6000行限制
    - 每月积攒完写一次文件（减少 IO）
    - 已有日期自动跳过（幂等），API 频次超限时先写盘再退出
    """
    from .sources.tushare import get_daily_by_date
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
                # API 频次超限：把已积攒的写盘再退出
                for ym, recs in month_buf.items():
                    if recs:
                        print(f"    💾 写入 {ym}: {len(recs)} 条")
                        _append_records(recs)
                print(f"  ⚠️ API 频次超限退出 ({status})，下次跑继续")
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
        result = _conn().execute(
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
            old = _conn().execute(f"SELECT * FROM read_parquet('{path}')").df()
            qdf = pd.concat([old, qdf]).drop_duplicates(
                subset=["ts_code", "trade_date"], keep="last"
            ).sort_values(["trade_date", "ts_code"])
        else:
            qdf = qdf.sort_values(["trade_date", "ts_code"])
        _conn().execute(f"COPY (SELECT * FROM qdf) TO '{path}' (FORMAT PARQUET)")


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
    from .sources.tushare import get_daily_basic_by_date

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


def batch_load_daily_basic(code: str, dates: list[str]) -> dict[str, dict]:
    """批量读 daily_basic, 1 次 SQL 拿 N 天数据, 按 trade_date 索引

    性能: 1 只票 N 天 ≈ 6ms (duckdb 全扫 + 内存 dict)
    vs 单点 N 次 SQL ≈ N * 280ms (慢 50 倍)

    Args:
        code:   6 位股票代码
        dates:  ['20260831', '20260901', ...] 8 字符 trade_date 列表

    Returns:
        dict[date, dict] 例: {'20260901': {total_mv, close, pe_ttm, ...}, ...}
        没数据的 date 不在 dict 里
    """
    if not dates:
        return {}
    try:
        import duckdb
        ts_code = _to_ts_code(code)
        # IN 列表
        dates_clean = [d.replace("-", "")[:8] for d in dates]
        dates_str = ",".join(f"'{d}'" for d in dates_clean)
        df = _conn().execute(
            f"""
            SELECT trade_date, total_mv, close, pe, pe_ttm, pb
            FROM read_parquet('{DAILY_BASIC_DIR}/*.parquet')
            WHERE ts_code = '{ts_code}'
              AND trade_date IN ({dates_str})
            """,
        ).df()
        return {row["trade_date"]: row.to_dict() for _, row in df.iterrows()}
    except Exception:
        return {}





def read_daily_basic(ts_code: str) -> dict:
    """读取单只股票最新一天的 daily_basic。"""
    try:
        import duckdb
        files = sorted(DAILY_BASIC_DIR.glob("*.parquet"))
        if not files:
            return {}
        recent = [str(f) for f in files[-2:]]
        glob_expr = "', '".join(recent)
        result = _conn().execute(f"""
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

    v6.2.4 修: 跨积分档 stock_basic 都不返 total_share/float_share, 落盘后读出 0
    解决: 落盘前用 stk_factor_pro (按 trade_date=最近 1 日) 补 total_share/float_share
    """
    import pandas as pd

    if STOCK_BASIC_PARQUET.exists():
        age = time.time() - STOCK_BASIC_PARQUET.stat().st_mtime
        if age < 30 * 24 * 3600:
            print(f"  ✅ stock_basic 已是最新 (上次更新 {age/86400:.0f} 天前)")
            return 0

    print("  📥 stock_basic 全市场建档...")
    from .sources.tushare import _safe_call
    data, status = _safe_call(
        "stock_basic", exchange="", list_status="L",
        fields="ts_code,name,industry,list_date,market,total_share,float_share",
    )
    if not data:
        print(f"  ⚠️ stock_basic 拉取失败: {status}")
        return 0

    df = pd.DataFrame(data)
    df["code"] = df["ts_code"].str.split(".").str[0]

    # 2026-09-04 修: 跨积分档 stock_basic 都不返 total_share, 用本地 stk_factor_pro parquet 兜底
    # 注意: 不能用 _latest_trade_date, 今天 (盘后) stk_factor_pro 还没出
    # 用本地 parquet 最新一天 (一定有数据, 除非全新 sync 第一次)
    try:
        import duckdb as _dd
        # 从本地 daily_basic 找最新一天 (1 次 SQL, 0 网络)
        local_date = _dd.execute("""
            SELECT MAX(trade_date) FROM read_parquet('data/history/daily_basic/*.parquet')
        """).fetchone()[0]
        if not local_date:
            raise RuntimeError("本地 daily_basic parquet 空")
        print(f"  📊 用本地 daily_basic@{local_date} 补 total_share/circ_share...")
        db_df = _dd.execute(f"""
            SELECT ts_code, total_share, circ_mv
            FROM read_parquet('data/history/daily_basic/*.parquet')
            WHERE trade_date = '{local_date}'
        """).df()
        db_df["code"] = db_df["ts_code"].str.split(".").str[0]
        # circ_mv 是流通市值 (万元), 股价 (元) → 流通股数 (万股)
        # 万股 = 万元 / 元 (1万 = 1元 × 1万股 = 1万元市值)
        # 示例: 25244873 万 / 159.02 元 = 158753 万股 = 15.88 亿股 ✅
        price_df = _dd.execute(f"""
            SELECT ts_code, close FROM read_parquet('data/history/daily_basic/*.parquet')
            WHERE trade_date = '{local_date}'
        """).df()
        db_df = db_df.merge(price_df, on="ts_code", how="left")
        db_df["float_share"] = (db_df["circ_mv"] / db_df["close"]).round(2)  # 万股
        db_df = db_df.drop(columns=["circ_mv", "close"], errors="ignore")
        # merge: 覆盖 stock_basic 的 total_share/float_share (NaN 来自跨积分档都缺)
        df = df.drop(columns=["total_share", "float_share"], errors="ignore")
        df = df.merge(db_df[["code", "total_share", "float_share"]], on="code", how="left")
        n_filled = df["total_share"].notna().sum()
        print(f"  ✅ 补 {n_filled}/{len(df)} 只股本")
    except Exception as e:
        print(f"  ⚠️ daily_basic 兜底失败: {e}")

    import duckdb
    _conn().execute(f"COPY (SELECT * FROM df) TO '{STOCK_BASIC_PARQUET}' (FORMAT PARQUET)")
    print(f"  ✅ stock_basic 建档完成: {len(df)} 只 → {STOCK_BASIC_PARQUET}")
    return len(df)


def read_stock_basic(code: str) -> dict:
    """读取单只股票的 stock_basic（从 parquet）。

    兼容 6位 (300613) 和带后缀 (300613.SZ) 两种 code 格式
    """
    try:
        import duckdb
        if not STOCK_BASIC_PARQUET.exists():
            return {}
        # 兼容: 6位 / 6位+后缀
        code6 = code.split(".")[0] if "." in code else code
        result = _conn().execute(f"""
            SELECT * FROM read_parquet('{STOCK_BASIC_PARQUET}')
            WHERE code = '{code6}'
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
#       interestdebt, netdebt, industry, fetch_status
# v6.2.5 删: eps_period / fetched_at / error_msg (无用列)
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
        df = _conn().execute(f"SELECT * FROM read_parquet('{path}')").df()
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
        df = _conn().execute(
            f"""
            SELECT code, industry FROM read_parquet('{STOCK_BASIC_PARQUET}')
            WHERE code IN ({codes_str})
            """
        ).df()
        return {str(r["code"]): str(r.get("industry", "") or "") for _, r in df.iterrows()}
    except Exception:
        return {}


def _fin_load_all_codes() -> list[str]:
    """全市场代码名单 (2026-09-03 改: 不再过滤科技股, VIP 1 次返 6451 全市场, 客户端筛省事)

    从 stock_basic.parquet 读所有 ts_code → 截掉后缀 → code。
    跟 _safe_call 返的 df_all['code'] 字段 (str.split('.').str[0]) 对齐。
    """
    import pandas as pd
    if not STOCK_BASIC_PARQUET.exists():
        return []
    df = pd.read_parquet(STOCK_BASIC_PARQUET)
    return df["ts_code"].str.split(".").str[0].tolist()


def _fin_load_tech_codes() -> list[str]:
    """⚠️ 2026-09-03 废弃: 之前过滤科技股逻辑, 现在 sync_financials 默认走 _fin_load_all_codes

    保留函数防止外部 import 引用崩, 返回全市场代替。
    """
    return _fin_load_all_codes()


def sync_financials(period: str, codes: list[str] = None, force: bool = False) -> int:
    """拉 Tushare fina_indicator 写 parquet (按季切, 跟 sync_stock_basic 同 pattern)

    v3.2: 5000 积分档用 fina_indicator_vip 1 次拿全市场, 客户端筛 + upsert。
    续跑规则: fetch_status='ok' / 'skip' 都跳过 (DB 有数据不 call API);
               'no_data' / 'timeout' 视为待拉; 不在 parquet 里视为待拉。

    Args:
        period: Tushare 原生 '20250630' / '20251231' 格式
        codes:  默认 None = 全市场 (2026-09-03 改, 之前是科技股过滤名单)
        force:  True = 强刷, 全部 to_pull (不查 status), 覆盖已 ok 的

    Returns:
        写入行数 (新增 + 覆盖)
    """
    from .sources.tushare import _safe_call
    import pandas as pd

    if codes is None:
        codes = _fin_load_all_codes()
    if not codes:
        print(f"  ⚠️ sync_financials {period}: 没有 codes")
        return 0

    # Phase 1: 算 to_pull / to_check (DB-only, 不调 API)
    #   to_pull : 本地没有 or 之前失败 → 必须从 API 拿
    #   to_check: 本地已有 ok → 拿到 API 数据后对比 ann_date, 有更新才覆盖
    #   skip    : fetch_status='skip' (VIP 确认没有该票) → 永久不拉
    existing = _fin_load_existing(period)
    if force:
        to_pull = list(codes)
        to_check: list[str] = []
    else:
        to_pull = []
        to_check = []
        for c in codes:
            row = existing.get(c)
            if row is None:
                to_pull.append(c)
            elif row.get("fetch_status") == "skip":
                continue  # VIP 确认无数据 → 永久跳过
            elif row.get("fetch_status") == "ok":
                to_check.append(c)  # 已有数据 → 对比 ann_date 增量更新
            else:
                to_pull.append(c)   # no_data / timeout → 重试

    n_skip_skip = sum(1 for c in codes if existing.get(c, {}).get("fetch_status") == "skip")
    need_api = len(to_pull) + len(to_check)
    if n_skip_skip:
        print(f"  ⏭ financials {period}: {n_skip_skip} skip(无数据), {len(to_pull)} 待补, {len(to_check)} 对比更新")
    if not need_api:
        return len(existing)   # 全部 skip, 0 次 API

    # Phase 2: 1 次 VIP API 拿全市场 (5000 积分档, 不限流)
    # EPS 走 datacenter.eastmoney.com (--eps flag, 独立渠道)
    all_needed = to_pull + to_check
    print(f"  📡 fina_indicator_vip period={period} ({len(to_pull)} 待补 + {len(to_check)} 对比更新)")
    data, status = _safe_call(
        "fina_indicator_vip",
        period=period,
    )
    if not data:
        print(f"  ⚠️ VIP 拉取失败: {status}, {len(to_pull)} 只标 skip")
        industry_map = _fin_load_industry_map(to_pull)
        rows = [
            {
                "ts_code": _to_ts_code(c), "code": c, "end_date": period,
                "industry": industry_map.get(c, ""),
                "fetch_status": "skip",
            }
            for c in to_pull  # to_check 的不动，保留原有 ok 数据
        ]
        n = upsert_financials(period, rows)
        print(f"  ✅ financials {period}: 写 {n} 行 (全 skip)")
        return n

    df_all = pd.DataFrame(data)
    df_all["code"] = df_all["ts_code"].str.split(".").str[0]

    # Phase 3: 客户端筛
    #   to_pull 命中 → 直接写
    #   to_check 命中 → 对比 ann_date, API 侧更新才覆盖
    df_new = df_all[df_all["code"].isin(to_pull)].copy()

    df_check_api = df_all[df_all["code"].isin(to_check)].copy()
    updated_codes: list[str] = []
    skipped_codes: list[str] = []
    if not df_check_api.empty:
        for _, api_row in df_check_api.iterrows():
            c = str(api_row["code"])
            local_ann = str(existing.get(c, {}).get("ann_date") or "")
            api_ann   = str(api_row.get("ann_date") or "")
            if api_ann > local_ann:  # 字符串比较 YYYYMMDD 格式安全
                updated_codes.append(c)
            else:
                skipped_codes.append(c)
        df_new = pd.concat([df_new, df_check_api[df_check_api["code"].isin(updated_codes)]], ignore_index=True)

    # Phase 4: 标记 industry + fetch_status='ok'
    industry_map = _fin_load_industry_map(all_needed)
    df_new["industry"] = df_new["code"].map(industry_map).fillna("")
    df_new["fetch_status"] = "ok"

    # Phase 5: to_pull 里 VIP 没返的票, 标 skip (永久不再拉)
    hit_codes = set(df_new["code"].tolist())
    not_in_vip = [c for c in to_pull if c not in hit_codes]
    skip_rows = [
        {
            "ts_code": _to_ts_code(c), "code": c, "end_date": period,
            "industry": industry_map.get(c, ""),
            "fetch_status": "skip",
        }
        for c in not_in_vip
    ]

    # Phase 6: 1 次 upsert (ok + skip 一起, 保护已有 ok 不被新 skip 覆盖)
    rows = df_new.to_dict("records") + skip_rows
    if not rows:
        return len(existing)
    n = upsert_financials(period, rows)
    n_new = len(df_all[df_all["code"].isin(to_pull)].dropna(subset=["ann_date"]))
    n_updated = len(updated_codes)
    n_unchanged = len(skipped_codes)
    print(f"  ✅ financials {period}: {n_new} 新增 / {n_updated} 更新 / {n_unchanged} 无变化 / {len(not_in_vip)} skip")
    return n


def read_financials(code: str, lookback_quarters: int = 4) -> list[dict]:
    """跨多季 parquet 读 (跟 read_stock_basic 同 pattern, duckdb glob)

    只返 fetch_status='ok' 的行, 按 end_date 升序。给分析层用 (RenderData / ROC / EY)。

    v6.2.7 改: 用 UNION ALL BY NAME 替代 glob — glob 在 schema 不一致时
    (Q1/Q3 只有 5 列, Q2/Q4 有 112 列) 会推断错 schema, 返 5 列。
    """
    try:
        import duckdb
        if not FIN_DIR.exists():
            return []
        ts_code = _to_ts_code(code)
        # 列固定, UNION ALL BY NAME — 不用 glob, 避免 schema 推断错
        files = sorted(FIN_DIR.glob("*.parquet"))
        if not files:
            return []
        union_sql = " UNION ALL BY NAME ".join(
            f"SELECT * FROM read_parquet('{f}')" for f in files
        )
        df = _conn().execute(union_sql).df()
        if df.empty:
            return []
        sub = df[(df["ts_code"] == ts_code) & (df["fetch_status"] == "ok")]
        sub = sub.sort_values("end_date", ascending=False).head(lookback_quarters)
        return sub.sort_values("end_date", ascending=True).to_dict("records")
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
        """日线 K线，升序。limit=0 表示全量（默认取 config 里的 kline_days）。"""
        if limit == 0:
            limit = _PROJECT_CFG.get("data", {}).get("kline_days", 1250)
        ts_code = _to_ts_code(code)
        rows = _read_kline(ts_code, limit=limit)

        last_valid_pre_close = None
        for r in rows:
            # 字段别名: vol → volume (raw 股数, 不动)
            if 'vol' in r and 'volume' not in r:
                r['volume'] = r['vol']
            # NaN 兜底 (Tushare 除权日附近偶尔丢 amount/pct_chg)
            # pre_close → 前向填充 (上一交易日有效 close 复用), amount/pct_chg → 0
            pc = r.get('pre_close')
            if pc is None or (isinstance(pc, float) and pc != pc):
                r['pre_close'] = last_valid_pre_close if last_valid_pre_close is not None else (r.get('close') or 0)
            else:
                last_valid_pre_close = r['pre_close']
            amt = r.get('amount')
            if amt is None or (isinstance(amt, float) and amt != amt):
                r['amount'] = 0.0
            pct = r.get('pct_chg')
            if pct is None or (isinstance(pct, float) and pct != pct):
                r['pct_chg'] = 0.0

        return rows

    @classmethod
    def get_weekly(cls, code: str, limit: int = 0) -> list[dict]:
        """周线，从日线聚合，升序。"""
        from .sources.eastmoney import _synthesize_weekly
        kline = cls.get_kline(code, limit=limit * 5 if limit else 0)
        return _synthesize_weekly(kline)

    @classmethod
    def get_latest_financials_map(cls, code6_set: set[str] | None = None) -> dict[str, dict]:
        """每只股票最新一季财务 dict (0 网络, 走 financials parquet)

        2026-09-18 加: 替代 3 处 batch 脚本里散落的 financials 拉取逻辑 (find_near_low,
        finance_earnings_blowout, finance_roc_ey). 一处定义, 3 处复用.

        Args:
            code6_set: 只返回这个集合里的 code (可选, 减少 dict 体积). None = 全市场.
        Returns:
            {code6: {
                'or_yoy':            float|None,   # 营收 yoy %
                'netprofit_yoy':     float|None,   # 净利 yoy %
                'gross_margin':      float|None,   # 毛利率 %
                'roe':               float|None,   # ROE %
                'revenue_yi':        float|None,   # 营收 亿 (op_income / 1e8)
                'np_yi':             float|None,   # 净利 亿 (profit_dedt / 1e8)
                'ebit_yi':           float|None,   # EBIT 亿
                'ebit':              float|None,   # EBIT 原值 (元)
                'roe_yoy':           float|None,   # ROE yoy
                'end_date':          str,           # '20260630'
            }}
            注: NaN 在 dict 中转为 None.
        """
        import pandas as pd
        df = cls.load_all_financials()
        if df.empty:
            return {}
        # 过滤 fetch_status=ok
        df = df[df["fetch_status"] == "ok"].copy()
        if df.empty:
            return {}
        # 6 位 code (ts_code '000858.SZ' → '000858')
        df["code6"] = df["code"].astype(str).str[:6]
        if code6_set is not None:
            df = df[df["code6"].isin(code6_set)]
            if df.empty:
                return {}
        # 取每只股票最新一季 (end_date 最大)
        latest = df.sort_values("end_date").groupby("code6").tail(1)

        def _safe(v, scale=1.0):
            if v is None or (isinstance(v, float) and v != v):
                return None
            try:
                return float(v) * scale
            except (TypeError, ValueError):
                return None

        out: dict[str, dict] = {}
        for _, row in latest.iterrows():
            code6 = row["code6"]
            out[code6] = {
                'or_yoy':         _safe(row.get("or_yoy")),
                'netprofit_yoy':  _safe(row.get("netprofit_yoy")),
                'gross_margin':   _safe(row.get("grossprofit_margin")),
                'roe':            _safe(row.get("roe")),
                'revenue_yi':     _safe(row.get("op_income"), scale=1/1e8),
                'np_yi':          _safe(row.get("profit_dedt"), scale=1/1e8),
                'ebit_yi':        _safe(row.get("ebit"), scale=1/1e8),
                'ebit':           _safe(row.get("ebit")),
                'roe_yoy':        _safe(row.get("roe_yoy")),
                'end_date':       str(row.get("end_date", "")),
            }
        return out

    @classmethod
    def get_ths_index(cls) -> list[dict]:
        """同花顺概念/风格/指数 完整列表 (~2517 行, 0 网络永久缓存).

        字段: ts_code, name, count, exchange, list_date, type
        """
        import pandas as pd
        path = THS_HISTORY_DIR / "ths_index.parquet"
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        return df.to_dict("records")

    @classmethod
    def lookup_ths_code(cls, name: str) -> list[dict]:
        """通过 name 模糊匹配 ths 概念代码。

        Args:
            name: 'CPO' / '机器人' / '高成长股' (支持子串匹配)
        Returns:
            匹配到的概念列表 (含 ts_code, name, type), 多个全返
        """
        rows = cls.get_ths_index()
        return [r for r in rows if name in str(r.get("name", ""))]

    @classmethod
    def get_ths_kline(cls, code_or_name: str, limit: int = 0) -> list[dict]:
        """THS 概念板块 K 线 (升序).

        Args:
            code_or_name: 'CPO' 模糊匹配 ths_index 找代码 / '886033.TI' 直接读
            limit: 0=全量, N=最近 N 根
        Returns:
            跟个股 get_kline 同形态: [{trade_date, open, high, low, close, vol, ...}, ...]
            NaN 兜底跟 get_kline 一致.

        使用前需先 sync --ths 把数据落盘. parquet 路径:
          data/history/ths/kline/{ts_code}.parquet
        """
        ts_code = code_or_name
        if "." not in ts_code:
            # 模糊反查
            matches = cls.lookup_ths_code(ts_code)
            if not matches:
                return []
            # 多匹配: 优先 type=N (概念板块, 一般是想要的)
            n_match = [m for m in matches if m.get("type") == "N"]
            ts_code = (n_match or matches)[0]["ts_code"]

        path = THS_HISTORY_DIR / "kline" / f"{ts_code}.parquet"
        if not path.exists():
            return []

        try:
            df = _conn().execute(f"SELECT * FROM read_parquet('{path}') ORDER BY trade_date").df()
        except Exception as e:
            print(f"  ⚠️ get_ths_kline {ts_code} 失败: {e}", file=sys.stderr)
            return []

        rows = df.to_dict("records")
        if limit and len(rows) > limit:
            rows = rows[-limit:]

        # 字段对齐: pct_change → pct_chg (跟个股 daily schema 同)
        for r in rows:
            if "pct_change" in r and "pct_chg" not in r:
                r["pct_chg"] = r.pop("pct_change")
            # NaN 兜底跟个股一致
            pc = r.get("pre_close")
            if pc is None or (isinstance(pc, float) and pc != pc):
                r["pre_close"] = r.get("close") or 0
            amt = r.get("amount")
            if amt is None or (isinstance(amt, float) and amt != amt):
                r["amount"] = 0.0
            pct = r.get("pct_chg")
            if pct is None or (isinstance(pct, float) and pct != pct):
                r["pct_chg"] = 0.0
        return rows

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
        from .caches.eps import get_eps
        return get_eps(code)

    @classmethod
    def get_ctx(cls, code: str, kline_only: bool = False, limit: int = 0):
        """返回 RawContext（L1 数据层唯一入口）。

        kline_only=True: 只读 K线，跳过 stock_basic/daily_basic/eps/fflow。
        limit: K线条数上限，0=使用 config 默认值（kline_days）。

        v6.2.5 改: moneyflow 走 get_fund_flow (内部优先 DataStore 落盘 parquet)
                       老的 eastmoney.fetch_all 串行拉已删
        """
        from tools.analysis.analysis_engine import RawContext
        from .sources.eastmoney import _synthesize_weekly, get_fund_flow

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

        # total_mv 单位是"万元" (Tushare daily_basic), 转亿
        # 修复: 之前直接 db.get("total_mv") 拿"万" 当"亿" 存, 导致 EY 算成天文数字
        total_mv_yi = (db.get("total_mv") or 0.0) / 1e4

        # v6.2.5: moneyflow 走 get_fund_flow (优先读 5 季落盘 parquet, 0 网络)
        # 取 60 天覆盖 fflow_factor 3/5/10/20/30 周期
        mf, _ = get_fund_flow(code, days=60)

        return RawContext(
            kline=kline, weekly=weekly,
            eps_table=eps, fflow={}, moneyflow=mf or [],
            current_price=db.get("close") or close,
            market_cap_yi=total_mv_yi,
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
    def get_fflow_history(cls, code: str) -> list[dict]:
        """单只票全季 fflow (主力资金) 历史, 按 trade_date 升序 (v6.2.5 加)

        9 字段: ts_code/trade_date + 6 个 amount (sm/lg/elg 买/卖) + net_mf_amount
        数据源: data/history/fflow_history/fflow_{YYYYQN}.parquet
        走 caches/fflow_history.read_fflow_history (跨 5 季 parquet glob)

        Returns:
            list[dict] 按 trade_date 升序, 空 list = 没数据
        """
        from .caches.fflow_history import read_fflow_history
        return read_fflow_history(code)

    @classmethod
    def list_codes(cls) -> list[str]:
        """返回本地历史库里所有有数据的股票代码（6位，不带交易所后缀）。"""
        try:
            import duckdb
            files = list(HISTORY_DIR.glob("*.parquet"))
            if not files:
                return []
            result = _conn().execute(
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
    def load_stock_basic(cls) -> "pd.DataFrame":
        """全市场 stock_basic 1 次 SQL (替代 boll_bbw 等散落)

        Returns:
            DataFrame: columns = [ts_code, name, industry, ...]
        """
        try:
            import duckdb
            import pandas as pd
            if not STOCK_BASIC_PARQUET.exists():
                return pd.DataFrame()
            return _conn().execute(
                f"SELECT * FROM read_parquet('{STOCK_BASIC_PARQUET}')"
            ).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    # ============================================================
    # 4 个 bulk 接口 (2026-09-03 v6.1.1 改造, 解决"读 parquet 散落"问题)
    #
    # 原则: 任何分析代码读数据只能调 DataStore 接口, 不允许自己读 parquet / 调网络
    # 之前: 7 个 batch / research 脚本散落 duckdb.execute + read_parquet, 共 ~15 处违规
    # 现在: 全部走下面 4 个接口
    # ============================================================

    @classmethod
    def load_all_daily_basic_lite(cls) -> "pd.DataFrame":
        """全市场 K 线 1 次 SQL (含 amount, 供流动性过滤用)

        Returns:
            DataFrame: columns = [ts_code, trade_date, close, vol, amount]
            跟 load_all_kline 区别: 这个返回 DataFrame (按需列), 不 groupby
        """
        try:
            import duckdb
            import pandas as pd
            files = list(HISTORY_DIR.glob("*.parquet"))
            if not files:
                return pd.DataFrame()
            return _conn().execute(f"""
                SELECT ts_code, trade_date, close, vol, amount
                FROM read_parquet('{HISTORY_DIR}/*.parquet')
                WHERE trade_date = (SELECT MAX(trade_date) FROM read_parquet('{HISTORY_DIR}/*.parquet'))
            """).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def load_all_kline(cls, years: float = 5.5) -> dict[str, list[dict]]:
        """全市场 K 线 1 次 SQL (替代 find_near_low / boll_bbw 等散落实现)

        Returns:
            dict[ts_code, list[{trade_date, open, high, low, close, vol, ...}]]
            注意: key 是 ts_code (e.g. '300750.SZ'), 不是 6 位 code
        """
        try:
            import duckdb
            files = list(HISTORY_DIR.glob("*.parquet"))
            if not files:
                return {}
            sql = f"""
                WITH max_d AS (
                    SELECT MAX(STRPTIME(trade_date, '%Y%m%d')) AS d
                    FROM read_parquet('{HISTORY_DIR}/*.parquet')
                )
                SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close, d.vol
                FROM read_parquet('{HISTORY_DIR}/*.parquet') d, max_d m
                WHERE STRPTIME(d.trade_date, '%Y%m%d') >= m.d - INTERVAL '{years} year'
                ORDER BY d.ts_code, d.trade_date
            """
            df = _conn().execute(sql).df()
            return {
                code: g[["trade_date", "open", "high", "low", "close", "vol"]].to_dict("records")
                for code, g in df.groupby("ts_code")
            }
        except Exception:
            return {}

    @classmethod
    def load_all_daily_full(cls, years: float = 0.5) -> "pd.DataFrame":
        """全市场 K 线 1 次 SQL, 返全列 (含 pct_chg / amount / pre_close)

        替代 sector_breakout_scan.py 的 pd.read_parquet
        Returns:
            DataFrame: ts_code, trade_date, open, high, low, close, pre_close, vol, amount, pct_chg
        """
        try:
            import duckdb
            import pandas as pd
            files = list(HISTORY_DIR.glob("*.parquet"))
            if not files:
                return pd.DataFrame()
            sql = f"""
                WITH max_d AS (
                    SELECT MAX(STRPTIME(trade_date, '%Y%m%d')) AS d
                    FROM read_parquet('{HISTORY_DIR}/*.parquet', union_by_name=true)
                )
                SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close,
                       d.pre_close, d.vol, d.amount, d.pct_chg
                FROM read_parquet('{HISTORY_DIR}/*.parquet', union_by_name=true) d, max_d m
                WHERE STRPTIME(d.trade_date, '%Y%m%d') >= m.d - INTERVAL '{years} year'
                ORDER BY d.ts_code, d.trade_date
            """
            return duckdb.execute(sql).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def load_all_daily_basic(cls) -> "pd.DataFrame":
        """全市场 daily_basic 1 次 SQL (替代 backfill_magic_cache / bb_obv_scan 散落)

        Returns:
            DataFrame: columns = [ts_code, trade_date, total_mv, close, pe, pe_ttm, pb, ...]
        """
        try:
            import duckdb
            import pandas as pd
            files = list(DAILY_BASIC_DIR.glob("*.parquet"))
            if not files:
                return pd.DataFrame()
            return _conn().execute(
                f"SELECT * FROM read_parquet('{DAILY_BASIC_DIR}/*.parquet')"
            ).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def load_financials_period(cls, period: str) -> "pd.DataFrame":
        """1 季度全市场财务 (替代 roc_ey_top20 散落)

        Args:
            period: '2025Q4' / '2026Q2' 格式 (file stem)

        Returns:
            DataFrame: columns = [ts_code, end_date, ebit, fixed_assets,
                                  networking_capital, interestdebt, netdebt,
                                  code, industry, fetch_status, ...]
        """
        try:
            import duckdb
            import pandas as pd
            path = FIN_DIR / f"{period}.parquet"
            if not path.exists():
                return pd.DataFrame()
            return _conn().execute(
                f"SELECT * FROM read_parquet('{path}')"
            ).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def load_all_financials(cls) -> "pd.DataFrame":
        """5+ 季度全市场财务 (替代 backfill_magic_cache 散落)

        Returns:
            DataFrame: 5 季度合并, ~27745 行, union_by_name=true 解决 schema 不齐
        """
        try:
            import duckdb
            import pandas as pd
            if not FIN_DIR.exists():
                return pd.DataFrame()
            return duckdb.execute(
                f"SELECT * FROM read_parquet('{FIN_DIR}/*.parquet', union_by_name=true)"
            ).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def load_all_fflow_history(cls) -> "pd.DataFrame":
        """5 季全市场 fflow (主力资金) 历史 1 次 SQL (v6.2.5 加)

        Returns:
            DataFrame: 5 季合并, 9 列 (ts_code/trade_date + 6 amount + net_mf_amount)
            空 DataFrame = 没拉过 / --fflow 没跑
        """
        try:
            import duckdb
            import pandas as pd
            if not FFLOW_HISTORY_DIR.exists():
                return pd.DataFrame()
            files = list(FFLOW_HISTORY_DIR.glob("fflow_*.parquet"))
            if not files:
                return pd.DataFrame()
            return _conn().execute(
                f"SELECT * FROM read_parquet('{FFLOW_HISTORY_DIR}/fflow_*.parquet')"
            ).df()
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def get_market_cap_at_date(cls, date_str: str) -> dict[str, float]:
        """某日全市场市值 (单位: 万元, 跟 Tushare daily_basic 一致)

        替代 backtest_roc_ey.py:105 的 duckdb read_parquet
        Args:
            date_str: 'YYYYMMDD' 格式
        Returns:
            dict[code_6digit, total_mv_yi]  (key 不带 .SH/.SZ 后缀)
        """
        try:
            import pandas as pd
            df = cls.load_all_daily_basic()
            if df.empty:
                return {}
            df = df[df["trade_date"] == date_str]
            if df.empty:
                return {}
            return dict(zip(
                df["ts_code"].astype(str).str.split(".").str[0],
                df["total_mv"].astype(float),
            ))
        except Exception:
            import pandas as pd
            return pd.DataFrame() if False else {}

    @classmethod
    def get_stk_factor_latest(cls) -> "pd.DataFrame":
        """每只票最新一日的 stk_factor (PE/PE_TTM/close/total_mv)

        替代 earnings_blowout_scan.py:78 的 duckdb read_parquet
        Returns:
            DataFrame: columns = [ts_code, trade_date, close, pe, pe_ttm, total_mv]
            1 行/票, 取该票 trade_date 最大那日
        """
        try:
            import pandas as pd
            df = cls.load_all_daily_basic()
            if df.empty:
                return pd.DataFrame()
            idx = df.groupby("ts_code")["trade_date"].idxmax()
            return df.loc[idx, ["ts_code", "trade_date", "close", "pe", "pe_ttm", "total_mv"]].reset_index(drop=True)
        except Exception:
            import pandas as pd
            return pd.DataFrame()

    @classmethod
    def get_market_cap_near_dates(cls, code: str, end_dates: list[str], days_after: int = 30) -> dict[str, float]:
        """单只票: 每个 end_date 找其后 [0, days_after] 内最近一个 trade_date 的 total_mv (亿)

        用于 4 季大表 (FinanceStrategy) — 季报披露后市场估值, 不是 end_date 当天 (A 股季末可能停牌)
        Args:
            code: 6 位代码 (例 '300750')
            end_dates: ['20250630', '20251231', '20260331', '20260630'] 格式
            days_after: 找 [end_date, end_date+days_after] 区间, 默认 30 天
        Returns:
            {end_date_str: mc_yi}  找不到返 0.0
        """
        try:
            import pandas as pd
            ts_code = _to_ts_code(code)
            df = cls.load_all_daily_basic()
            if df.empty or not end_dates:
                return {ed: 0.0 for ed in end_dates}
            df = df[df["ts_code"] == ts_code][["trade_date", "total_mv"]].copy()
            if df.empty:
                return {ed: 0.0 for ed in end_dates}
            out = {}
            for ed in end_dates:
                # 找 [ed, ed+days_after] 内最大 trade_date
                ed_int = int(ed[:8])
                upper_int = ed_int + days_after
                # trade_date 是字符串 '20250630', 转 int 比较
                df["td_int"] = df["trade_date"].astype(int)
                window = df[(df["td_int"] >= ed_int) & (df["td_int"] <= upper_int)]
                if window.empty:
                    # 退路: 取 <= end_date 的最近一天 (市场还没看到季报, 用季末前)
                    window = df[df["td_int"] <= ed_int].tail(1)
                if window.empty:
                    out[ed] = 0.0
                else:
                    # 取窗口内 trade_date 最大那行
                    best = window.loc[window["td_int"].idxmax()]
                    out[ed] = float(best["total_mv"]) / 1e4  # 万 → 亿
            return out
        except Exception:
            return {ed: 0.0 for ed in end_dates}

    @classmethod
    def watchlist_codes(cls) -> list[str]:
        """返回 watchlist.json 里的股票代码列表。"""
        try:
            d = json.loads(Path("config/watchlist.json").read_text(encoding="utf-8"))
            return [s["code"] for s in d.get("stocks", [])]
        except Exception:
            return []

    # ============================================================
    # 7 个 schema 接口 (2026-09-03 v6.2 加, 解决 watchlist/sectors/events 散落读)
    # ============================================================

    _WATCHLIST_PATH = Path("config/watchlist.json")

    @classmethod
    def load_watchlist(cls) -> dict:
        """读整个 watchlist.json (顶层含 version/last_updated/stocks/changelog)

        替代 7 处散落 json.load(open("data/watchlist.json"))
        """
        try:
            return json.loads(cls._WATCHLIST_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {"version": "1.0", "stocks": []}

    @classmethod
    def save_watchlist(cls, data: dict) -> bool:
        """原子写回 watchlist.json (写 tmp + rename, 避免半写状态)

        Returns:
            True 成功 / False 失败
        """
        try:
            cls._WATCHLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = cls._WATCHLIST_PATH.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(cls._WATCHLIST_PATH)
            return True
        except Exception as e:
            print(f"  ⚠️ save_watchlist 失败: {e}", file=__import__("sys").stderr)
            return False

    @classmethod
    def add_to_watchlist(cls, code: str, name: str, sector: str = "",
                          list_type: str = "自选", notes: str = "") -> bool:
        """原子加股票到 watchlist (跳过已存在)

        替代 roc_ey_top20.py:353 _add_to_watchlist 散落实现 (原 magic_top20)
        """
        d = cls.load_watchlist()
        existing = {s["code"] for s in d.get("stocks", [])}
        if code in existing:
            return False
        d.setdefault("stocks", []).append({
            "code": code,
            "name": name,
            "sector": sector,
            "list_type": list_type,
            "notes": notes,
        })
        d["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        return cls.save_watchlist(d)

    @classmethod
    def remove_from_watchlist(cls, code: str) -> bool:
        """原子删股票 from watchlist"""
        d = cls.load_watchlist()
        before = len(d.get("stocks", []))
        d["stocks"] = [s for s in d.get("stocks", []) if s.get("code") != code]
        if len(d["stocks"]) == before:
            return False
        d["last_updated"] = datetime.now().strftime("%Y-%m-%d")
        return cls.save_watchlist(d)

    @classmethod
    def update_watchlist_note(cls, code: str, note: str) -> bool:
        """原子更新单只股票 note"""
        d = cls.load_watchlist()
        for s in d.get("stocks", []):
            if s.get("code") == code:
                s["notes"] = note
                d["last_updated"] = datetime.now().strftime("%Y-%m-%d")
                return cls.save_watchlist(d)
        return False


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
            n, min_d, max_d = _conn().execute(
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
            n, min_d, max_d, code_count = _conn().execute(
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
            df = _conn().execute(
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
                n_total, n_ok, n_skip, n_other = _conn().execute(
                    f"SELECT COUNT(*), "
                    f"SUM(CASE WHEN fetch_status='ok' THEN 1 ELSE 0 END), "
                    f"SUM(CASE WHEN fetch_status='skip' THEN 1 ELSE 0 END), "
                    f"SUM(CASE WHEN fetch_status NOT IN ('ok','skip') THEN 1 ELSE 0 END) "
                    f"FROM read_parquet('{latest}')"
                ).fetchone()
                print(f"  financials:  最新 {latest.stem} | {n_total} 只 ({n_ok} ok / {n_skip} skip / {n_other} 失败)")
                # 全部季汇总
                if len(files) > 1:
                    n_all = _conn().execute(
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
