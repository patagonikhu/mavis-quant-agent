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
    ..sources.tushare    — Tushare 接口
"""

from __future__ import annotations
import argparse
import json
import re
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

# 2026-10-09 加: ETF 场内基金 (fund_basic + fund_daily 走 tushare)
ETF_HISTORY_DIR = Path("data/history/etf")
ETF_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
ETF_KLINE_DIR = ETF_HISTORY_DIR / "kline"   # 季度文件: 2026Q3.parquet (跟个股同格式)
ETF_KLINE_DIR.mkdir(parents=True, exist_ok=True)

# 2026-11-11 加: 申万行业指数 (index_classify + sw_daily 走 tushare)
#   用途: 给"行业"算威科夫 stage (行业自身在不在主升浪) —— 行业排名已有 (industry_position.py),
#         行业阶段是新维度。
#   结构: sw_index.parquet = 行业分类目录 (30 天缓存), kline/{YYYY}.parquet = 1 年 1 文件
#   2026-11-11 改: 原先 1 行业 1 文件 (134 个行业 → 125 个文件, 目录太长) → 按年份分 (8 个文件)
SW_INDEX_HISTORY_DIR = Path("data/history/sw_index")
SW_INDEX_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
SW_INDEX_KLINE_DIR = SW_INDEX_HISTORY_DIR / "kline"
SW_INDEX_KLINE_DIR.mkdir(parents=True, exist_ok=True)
# 行业成分表 (index_member_all): 每只票 → SW2021 一/二/三级行业
#   本地 stock_basic.industry 是申万旧版 110 个, 跟 SW2021 对不上 → 这张表是唯一可靠映射来源
SW_INDEX_MEMBER_PARQUET = SW_INDEX_HISTORY_DIR / "industry_member.parquet"

# 进程内缓存: 行业日线 {ts_code: bars} / 行业归属 {ts_code: l2_code}
#   批量分析时同一行业被反复用 (5000 只票 → 134 个行业) → 缓存住, 别反复读盘
#   ⚠️ None = 还没加载 (空 dict 也是"已加载, 但没数据" → 不能拿它当未加载的标记)
_SW_INDEX_BARS_CACHE: dict[str, list[dict]] = {}
_SW_INDEX_FRAME = None            # 全量行业 K 线 DataFrame (按年份文件拼一次)
_INDUSTRY_MEMBER_INFO: dict | None = None   # {ts_code: {l1_code, l2_code, l2_name, ...}}

# 2026-11-11: THS 同花顺概念板块 —— --ths 已删 (白名单源 2026-10-14 起不存在, 死段)
#   data/history/ths/ 里 18 份概念数据停在 2026-10-08, 纯存档不再更新;
#   这个常量只标位置, 将来要读存档时用 (拉数据的封装在 sources/tushare.py 的 get_ths_*)
THS_HISTORY_DIR = Path("data/history/ths")
THS_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


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


def _to_etf_ts_code(code: str) -> str:
    """ETF 代码转 ts_code: 515790 → 515790.SH / 159995 → 159995.SZ"""
    if "." in code:
        return code
    c = code.strip()
    if c.startswith("1"):
        return f"{c}.SZ"
    return f"{c}.SH"


def _to_ts_code(code: str) -> str:
    """000725 → 000725.SZ / 600000 → 600000.SH / 920045 → 920045.BJ (北交所) / 000300 → 000300.SH (指数优先)

    2026-09-09 修: 加 .BJ 后缀映射 (北交所 920xxx 代码), 之前错误映射成 .SH 导致 K 线查不到
    2026-10-19 修: 加 ETF 代码段 (5/15/16/18 开头) → .SH (上交所 ETF) / .SZ (深交所 ETF)
                   510300 (沪深300ETF) → .SH / 159001 (深 ETF) → .SZ
    """
    if "." in code:
        return code
    c = code.strip()
    if c in _INDEX_SUFFIX:
        return f"{c}.{_INDEX_SUFFIX[c]}"
    # 北交所代码段: 920xxx / 430xxx (老三板转板); 老三板 8xxxxx 已退市不处理
    if c.startswith(("920", "430", "831", "832", "833", "834", "835", "836", "837", "838", "839")):
        return f"{c}.BJ"
    # 2026-10-19 加: ETF 代码段 — 5/16/18 开头 → .SH (上交所), 15 开头 → .SZ (深交所)
    if c.startswith(("5", "6", "9")):
        return f"{c}.SH"
    if c.startswith(("15", "16", "18")):
        return f"{c}.SZ"
    if c.startswith(("0", "3")):
        return f"{c}.SZ"
    return f"{c}.SZ"


def _quarter_of(trade_date: str) -> str:
    """YYYYMMDD → 'YYYYQN' 季度标识，如 '20260823' → '2026Q3'"""
    month = int(trade_date[4:6])
    q = (month - 1) // 3 + 1
    return f"{trade_date[:4]}Q{q}"


def _parquet_path(year_or_quarter) -> Path:
    """支持 year（int，向后兼容）或 quarter（str，如 '2026Q3'）"""
    return HISTORY_DIR / f"{year_or_quarter}.parquet"


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


def _is_num(v) -> bool:
    """v 是不是一个有效数值。

    ⚠️ pandas 读 parquet 的 NULL 列拿到的是 **NaN 而不是 None**,
    所以 `v is None` 判缺值会全部失效 (NaN != None)。
    这里用 `v == v` 判 NaN (NaN != NaN), 兼容 python float / numpy 各种浮点类型。
    """
    if v is None:
        return False
    try:
        return bool(v == v)
    except Exception:
        return False


def _pe_finance_ok(pe, pe_ttm, pe_max: float) -> bool:
    """单只票的 finance 判定 (True = 保留)。纯函数, 便于单测。

    pe / pe_ttm 都是**已归一化**的值 (NaN → None), 所以直接跟 None 比。
    口径跟 filter_codes_by_finance 的 docstring 一致, 别改一处漏一处。
    """
    if pe_ttm is None:
        return False                       # TTM 算不出 → 亏损股
    if pe is not None and (pe <= 0 or pe > pe_max):
        return False                       # pe 亏损 / 估值异常
    if pe_ttm <= 0 or pe_ttm > pe_max:
        return False                       # pe_ttm 亏损 / 估值异常
    return True


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


def _append_records_by_year(records: list[dict], target_dir: Path,
                            key: str = "ts_code") -> int:
    """按**年份**分文件写 parquet (1 年 1 文件) —— 申万行业指数用

    2026-11-11 改: 原先按 ts_code 分文件 (1 行业 1 文件 → 125 个文件, 目录太长),
    改成按 trade_date 的年份分 (2019~2026 → 8 个文件)。

    合并规则: 按 (key, trade_date) 去重, 保留后来者 (增量追加不会写坏已有行)。
    排序: 按 (key, trade_date) —— 同一年同一行业的时间序, 便于后续按行业切片。

    Args:
        records:    记录列表 (dict), 必须含 key 列 + trade_date 列
        target_dir: 落盘目录 (不存在会建)
        key:        去重用的键列名 (默认 ts_code)

    Returns:
        写入的记录数
    """
    if not records:
        return 0

    import pandas as pd

    df = pd.DataFrame(records)
    if key not in df.columns:
        raise ValueError(f"_append_records_by_year: 缺列 {key}")
    if "trade_date" not in df.columns:
        raise ValueError("_append_records_by_year: 缺列 trade_date")

    df["trade_date"] = df["trade_date"].astype(str)
    for col in ["open", "high", "low", "close", "pre_close", "pct_chg", "change",
                "vol", "amount", "turnover_rate", "pe", "pb", "avg_price",
                "float_mv", "total_mv"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["_year"] = df["trade_date"].str[:4]
    target_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for year, group in df.groupby("_year"):
        path = target_dir / f"{year}.parquet"
        if path.exists():
            try:
                old_df = pd.read_parquet(path)
                combined = pd.concat([old_df, group], ignore_index=True)
                combined = combined.drop_duplicates(subset=[key, "trade_date"], keep="last")
                combined = combined.sort_values([key, "trade_date"]).reset_index(drop=True)
            except Exception:
                # 旧文件读不了 (损坏/列变了) → 直接覆盖, 别让整段同步挂掉
                combined = group.sort_values([key, "trade_date"]).reset_index(drop=True)
        else:
            combined = group.sort_values([key, "trade_date"]).reset_index(drop=True)
        combined = combined.drop(columns=["_year"], errors="ignore")
        combined.to_parquet(path, index=False)
        total += len(group)
    return total


def migrate_sw_index_kline_to_year(kline_dir: Path) -> tuple[int, int]:
    """旧布局 (1 行业 1 文件) → 新布局 (1 年 1 文件) 幂等迁移

    2026-11-11: 用户反馈 "sw_index/kline 下面文件太多了, 文件按年份来呀, 不要按行业分文件"
    —— 134 个行业 = 125 个 parquet, 目录太长; 改成 2019~2026 共 8 个文件。

    幂等: 没有旧格式文件 (文件名 stem 不是 4 位年份) → 直接返回 (0, 0), 不做任何事。

    Returns:
        (迁移的文件数, 迁移的行数)
    """
    legacy = [f for f in sorted(kline_dir.glob("*.parquet"))
              if not (len(f.stem) == 4 and f.stem.isdigit())]
    if not legacy:
        return 0, 0

    import pandas as pd
    from .safe_fs import safe_delete

    frames = []
    for f in legacy:
        try:
            frames.append(pd.read_parquet(f))
        except Exception:
            continue
    if not frames:
        return 0, 0

    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    df["_year"] = df["trade_date"].str[:4]

    n_rows = len(df)
    for year, group in df.groupby("_year"):
        path = kline_dir / f"{year}.parquet"
        out = group.drop(columns=["_year"], errors="ignore")
        out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        if path.exists():
            old = pd.read_parquet(path)
            out = pd.concat([old, out], ignore_index=True)
            out = out.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
            out = out.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        out.to_parquet(path, index=False)

    for f in legacy:
        safe_delete(f, reason="2026-11-11 sw_index/kline 改按年份分文件, 迁移旧布局")
    return len(legacy), n_rows


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
        # 2026-10-19: 加 (limit and limit > 0) 防 limit=None 时 TypeError
        if limit and limit > 0 and not start_date:
            need_quarters = max(1, (limit // 63) + 2)  # 多加 2 个季度保险
            now = datetime.now()
            cur_q = (now.month - 1) // 3 + 1
            # 往前推 need_quarters 个季度
            y, q = now.year, cur_q
            min_year, min_q = y, q
            for _ in range(need_quarters - 1):
                q -= 1
                if q == 0:
                    q = 4
                    y -= 1
            min_year, min_q = y, q  # 2026-10-08 + limit=1250 算出来是 2021Q3 ~ 2021Q4 附近
            # 兼容旧年度文件：年度文件 stem 是纯数字
            def _file_ok(f):
                s = f.stem
                if s.isdigit():  # 旧年度文件
                    return int(s) >= min_year
                # 2026-10-08 fix: 用 (year, q) 元组比较, 不用字典序
                # 字典序 '2021Q4' > '2022Q1' 是 False ('2' < '1'), 会漏掉 2022 季度
                fy = int(s[:4]); fq = int(s[5])
                return (fy, fq) >= (min_year, min_q)
            files = [f for f in all_files if _file_ok(f)]
            if not files:
                files = all_files
        elif start_date:
            min_year = int(start_date[:4])
            min_q_str = _quarter_of(start_date)
            min_q = int(min_q_str[5])  # 'YYYYQN' → Q number
            def _file_ok(f):
                s = f.stem
                if s.isdigit():
                    return int(s) >= min_year
                # 2026-10-08 fix: 同上, 用 (year, q) 元组比较
                fy = int(s[:4]); fq = int(s[5])
                return (fy, fq) >= (min_year, min_q)
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
        records = df.to_dict("records")
        if not records:
            # 2026-10-19: daily parquet 找不到时 fallback 到 etf/kline (ETF K线)
            # ts_code 形如 '510300.SH' / '159001.SZ' / '513330.SH'
            # 触发条件: 代码以 5/15 开头
            code_prefix = ts_code.split('.')[0]
            is_etf = code_prefix.startswith("5") or code_prefix.startswith("15") or code_prefix.startswith("16") or code_prefix.startswith("18")
            if is_etf:
                # 注意: get_etf_kline 内部按 limit 推算需要的季度文件, 但 ETF 历史短 (1.5 年),
                # limit=1250 会推算出 21 个季度, 但 etf 目录只有 8 个季度文件 → 全过滤掉 → 空.
                # 这里改成: 直接读所有 etf 季度文件 + 在 SQL 里按 limit 截断
                try:
                    files_all = sorted(ETF_KLINE_DIR.glob("*.parquet"))
                    if files_all:
                        glob_pat = "', '".join(str(f) for f in files_all)
                        sql2 = f"""
                            SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, amount, pct_chg
                            FROM read_parquet(['{glob_pat}'])
                            WHERE ts_code = '{ts_code}'
                            {"AND trade_date >= '" + start_date + "'" if start_date else ""}
                            {"AND trade_date <= '" + end_date + "'" if end_date else ""}
                            ORDER BY trade_date
                        """
                        if limit:
                            sql2 = f"SELECT * FROM ({sql2}) t ORDER BY trade_date DESC LIMIT {limit}"
                            sql2 = f"SELECT * FROM ({sql2}) t ORDER BY trade_date"
                        df2 = _conn().execute(sql2).df()
                        return df2.to_dict("records")
                except Exception as e:
                    print(f"  ⚠️ ETF fallback {ts_code} 失败: {e}", file=sys.stderr)
        return records
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

# 2026-09-21 改: 删 000001 上证 / 000300 沪深300 / 399001 深证成指 (用户拍板)
# 保留 000688 科创50 / 399006 创业板指 / 930955 中证红利低波100 (3 个指数 sync)
INDEX_CODES = ["000688.SH", "399006.SZ", "930955.CSI"]


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
    # 统计实际写入条数 (含指数), 供 sync.py 显示真实新增量
    # 2026-10-29 修: 之前硬编码 return 0, 导致 sync 输出永远显示 "K线: 0 条新增",
    # 实际数据是写进去了的 (计数器撒谎, 数据没错)
    written = 0

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
                        written += len(all_records)
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
            written += len(all_records)

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
                written += len(idx_records)
                print(f"    ✅ {idx_code}: {len(idx_records)} 根")
            else:
                print(f"    ⚠️ {idx_code} 无数据 ({status})")
            time.sleep(0.3)

    # 同步 daily_basic（PE/PB/市值）
    # v6.2.4 起: daily_basic 走 stk_factor_pro 16 列, 由 action_stk_factor 写
    # (2026-10-30: 旧的 sync_daily_basic 整条链已删)

    # 同步 stock_basic（名称/行业，30天内不重拉）
    sync_stock_basic()

    # 同步 financials (财务指标, Magic Formula 用, 按季切)
    sync_financials(_date_to_financial_period(today))

    return written


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

def sync_etf_basic() -> int:
    """拉 ETF 基本信息 (fund_basic market=E status=L)，月级缓存。

    写盘: data/history/etf/etf_basic.parquet
    月级判断: 文件 mtime > 30天才重拉 (1次 API)
    Returns: ETF 数量 (0=已缓存跳过)
    """
    import pandas as pd

    path = ETF_HISTORY_DIR / "etf_basic.parquet"
    if path.exists():
        age = time.time() - path.stat().st_mtime
        if age < 30 * 24 * 3600:
            print(f"  ✅ etf_basic 已是最新 (上次更新 {age/86400:.0f} 天前)")
            return 0

    from .sources.tushare import get_fund_basic
    rows, status = get_fund_basic(
        market="E", status="L",
        fields="ts_code,name,management,fund_type,list_date,delist_date,benchmark,status,market",
    )
    if not rows:
        print(f"  ⚠️ etf_basic 拉取失败: {status}")
        return 0

    df = pd.DataFrame(rows)
    ETF_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    print(f"  ✅ etf_basic: {len(df)} 只 → {path}")
    return len(df)


def sync_etf_incremental(start_date: str = "") -> int:
    """增量同步 ETF 日K (fund_daily trade_date 全市场, 1次/天).

    写盘: data/history/etf/kline/<YYYYQN>.parquet (按季度, 跟个股同格式)
    Args:
        start_date: YYYYMMDD，空=自动从本地最大日期+1 开始；
                    首次无数据则从 2 年前开始
    Returns: 新增 bar 数量
    """
    import pandas as pd
    from datetime import datetime, timedelta

    from .sources.tushare import get_fund_daily

    today = _today()
    local_max = _get_etf_local_max_date()

    if start_date:
        pull_from = start_date
    elif local_max:
        pull_from = (datetime.strptime(local_max, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    else:
        pull_from = (datetime.now() - timedelta(days=365 * 2)).strftime("%Y%m%d")

    if pull_from > today:
        print(f"  ✅ ETF K线已是最新 (本地最新: {local_max})")
        return 0

    missing = _get_trading_dates(pull_from, today)
    if not missing:
        print(f"  ✅ ETF K线已是最新 (无缺失交易日)")
        return 0

    print(f"  📥 ETF K线需补 {len(missing)} 个交易日: {missing[0]} ~ {missing[-1]}")

    all_records: list[dict] = []
    for date in missing:
        records, status = get_fund_daily(trade_date=date)
        if not records:
            print(f"    跳过 {date} (状态: {status})")
            continue
        all_records.extend(records)
        print(f"    ✅ {date} fund_daily: {len(records)} 只")
        time.sleep(0.3)

    if not all_records:
        return 0

    new_df = pd.DataFrame(all_records)
    new_df["trade_date"] = new_df["trade_date"].astype(str)
    for col in ["open", "high", "low", "close", "pre_close", "pct_chg", "vol", "amount"]:
        if col in new_df.columns:
            new_df[col] = pd.to_numeric(new_df[col], errors="coerce")

    # 按季度分割写盘 (跟个股同格式: YYYYQN.parquet)
    new_df["quarter"] = new_df["trade_date"].apply(_quarter_of)
    total = 0
    for quarter, group in new_df.groupby("quarter"):
        group = group.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        path = ETF_KLINE_DIR / f"{quarter}.parquet"
        if path.exists():
            old = _conn().execute(f"SELECT * FROM read_parquet('{path}')").df()
            combined = pd.concat([old, group], ignore_index=True)
            combined = combined.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
        else:
            combined = group
        combined = combined.drop(columns=["quarter"], errors="ignore")
        combined = combined.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        _conn().execute(f"COPY (SELECT * FROM combined) TO '{path}' (FORMAT PARQUET)")
        total += len(group)

    print(f"  ✅ ETF K线写盘完成: {total} 条新增 bar ({new_df['ts_code'].nunique()} 只 ETF)")
    return total


def _get_etf_local_max_date() -> str | None:
    """ETF kline 季度文件中的最大 trade_date (或 None)."""
    files = sorted(ETF_KLINE_DIR.glob("*.parquet"))
    if not files:
        return None
    try:
        # 只读最新季度文件即可 (文件名有序, 最后一个最新)
        latest = files[-1]
        result = _conn().execute(
            f"SELECT MAX(trade_date) FROM read_parquet('{latest}')"
        ).fetchone()
        return result[0] if result and result[0] else None
    except Exception:
        return None


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
    # 2026-11-03 修: 原代码读 data/history/daily_basic/*.parquet, 但 v6.2.4 已用
    #   stk_factor_pro 取代 daily_basic, 该目录不存在 → 兜底静默失败,
    #   stock_basic 落盘时丢了 total_share/float_share 两列 →
    #   详报「基础信息」总股本/流通股本全显示 0.00亿股。改读 stk_factor。
    try:
        import duckdb as _dd
        # 从本地 stk_factor_pro 找最新一天 (1 次 SQL, 0 网络)
        local_date = _dd.execute("""
            SELECT MAX(trade_date) FROM read_parquet('data/history/stk_factor/*.parquet')
        """).fetchone()[0]
        if not local_date:
            raise RuntimeError("本地 stk_factor parquet 空")
        print(f"  📊 用本地 stk_factor@{local_date} 补 total_share/float_share...")
        db_df = _dd.execute(f"""
            SELECT ts_code, total_share, float_share
            FROM read_parquet('data/history/stk_factor/*.parquet')
            WHERE trade_date = '{local_date}'
        """).df()
        db_df["code"] = db_df["ts_code"].str.split(".").str[0]
        # merge: 覆盖 stock_basic 的 total_share/float_share (NaN 来自跨积分档都缺)
        df = df.drop(columns=["total_share", "float_share"], errors="ignore")
        df = df.merge(db_df[["code", "total_share", "float_share"]], on="code", how="left")
        n_filled = df["total_share"].notna().sum()
        print(f"  ✅ 补 {n_filled}/{len(df)} 只股本")
    except Exception as e:
        print(f"  ⚠️ stk_factor 兜底失败: {e}")

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


def _past_disclosure_deadline(period: str, buffer_days: int = 15) -> bool:
    """该季度的披露截止日是否已过 (+buffer 天缓冲)

    季报截止日 (ann_date 最晚):
        Q1 = 4/30, Q2 = 8/31, Q3 = 10/31, Q4 = 次年 4/30

    在截止日之前, VIP 可能返空 (报告还没披露齐) → skip 不是"永久无数据",
    只是"暂时没数据", 要重试。过了截止日 (+buffer) 才算定稿。

    Args:
        period:    'YYYYMMDD' 格式 (如 '20260930')
        buffer_days: 截止日后再等几天 (修订版可能更晚, 默认 15 天)

    Returns:
        True = 截止日已过 (skip 可当永久); False = 还在窗口内 (skip 要重试)
    """
    from datetime import date, datetime, timedelta
    try:
        d = datetime.strptime(period, "%Y%m%d").date()
    except (ValueError, AttributeError):
        return True  # period 解析不了 → 保守当已过 (不重拉)

    year, mmdd = d.year, d.strftime("%m%d")
    deadline = {
        "0331": date(year, 4, 30),
        "0630": date(year, 8, 31),
        "0930": date(year, 10, 31),
        "1231": date(year + 1, 4, 30),
    }.get(mmdd)
    if deadline is None:
        return True
    return date.today() > deadline + timedelta(days=buffer_days)


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
    """取 industry, 返 {code: industry}

    2026-11-11 改: 行业口径统一到 **SW2021** —— 优先取 index_member_all 的 l2_name,
    成分表没覆盖的回退到 stock_basic.industry (申万旧版)。
    (这列会显示在报告的估值段「**行业:**」, 跟 🏆行业地位 的行业名必须同口径)

    缓存到本次进程 (sync_financials 调用一次, 批量 fetch_one 共享)
    """
    if not codes:
        return {}
    try:
        import duckdb
        # SW2021 (唯一真源)
        sw_map = {}
        if SW_INDEX_MEMBER_PARQUET.exists():
            try:
                df_m = _conn().execute(
                    f"SELECT ts_code, l2_name FROM read_parquet('{SW_INDEX_MEMBER_PARQUET}')"
                ).df()
                sw_map = {str(r["ts_code"]).split(".")[0]: str(r.get("l2_name") or "")
                          for _, r in df_m.iterrows() if r.get("l2_name")}
            except Exception:
                sw_map = {}
        if not STOCK_BASIC_PARQUET.exists():
            return {c: sw_map.get(c, "") for c in codes}
        # 取 codes 子集, 减少扫描
        codes_str = ", ".join(f"'{c}'" for c in codes)
        df = _conn().execute(
            f"""
            SELECT code, industry FROM read_parquet('{STOCK_BASIC_PARQUET}')
            WHERE code IN ({codes_str})
            """
        ).df()
        out = {}
        for _, r in df.iterrows():
            c = str(r["code"])
            out[c] = sw_map.get(c) or str(r.get("industry", "") or "")
        return out
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
    #   skip    : fetch_status='skip' → **仅在披露截止日后**才当"永久无数据"
    #
    # 2026-11-02 修 (真 bug): 原来 skip 一律永久跳过, 但季报是**陆续披露**的。
    #   10/23 跑 2026Q3 时报告还没出齐 → VIP 返空 → 5580 只全标 skip。
    #   之后报告陆续发布 (截止 10/31), 但 skip 被当永久 → 永远不重拉 → 该季 0 数据。
    #   现在: 披露截止日之前 (含之前 15 天缓冲), skip 视为"待拉", 重新尝试。
    existing = _fin_load_existing(period)
    _skip_is_final = _past_disclosure_deadline(period)
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
                if _skip_is_final:
                    continue  # 截止日已过 → VIP 确认无数据 → 永久跳过
                to_pull.append(c)  # 还在披露窗口内 → 再试一次 (报告陆续发布)
            elif row.get("fetch_status") == "ok":
                to_check.append(c)  # 已有数据 → 对比 ann_date 增量更新
            else:
                to_pull.append(c)   # no_data / timeout → 重试

    n_skip_skip = sum(1 for c in codes if existing.get(c, {}).get("fetch_status") == "skip")
    need_api = len(to_pull) + len(to_check)
    if n_skip_skip:
        _tag = "skip(已过截止日,永久)" if _skip_is_final else "skip(披露窗口内,重试)"
        print(f"  ⏭ financials {period}: {n_skip_skip} {_tag}, {len(to_pull)} 待补, {len(to_check)} 对比更新")
    if not need_api:
        return len(existing)   # 全部 skip, 0 次 API

    # Phase 2: 1 次 VIP API 拿全市场 (5000 积分档, 不限流)
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

        2026-09-18 加: 替代 batch 脚本里散落的 financials 拉取逻辑
        (finance_earnings_blowout 等). 一处定义, 多处复用.

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
    def get_etf_kline(cls, code: str, limit: int = 0) -> list[dict]:
        """ETF 日K (升序), 走 data/history/etf/kline/<ts_code>.parquet.

        Args:
            code: '515790' 或 '515790.SH'
            limit: 0=全量, N=最近 N 根
        Returns:
            跟 get_kline 同形态: [{trade_date, open, high, low, close, vol, ...}, ...]
        使用前需先 sync --etf 落盘.
        """
        ts_code = code if "." in code else _to_etf_ts_code(code)
        files = sorted(ETF_KLINE_DIR.glob("*.parquet"))
        if not files:
            return []
        try:
            # limit → 推算需要几个季度文件 (每季约 63 交易日)
            if limit > 0:
                need_q = max(1, (limit // 63) + 2)
                files = files[-need_q:]
            glob_pat = "', '".join(str(f) for f in files)
            sql = f"""
                SELECT * FROM read_parquet(['{glob_pat}'])
                WHERE ts_code = '{ts_code}'
                ORDER BY trade_date
            """
            if limit:
                sql = f"SELECT * FROM ({sql}) t ORDER BY trade_date DESC LIMIT {limit}"
                sql = f"SELECT * FROM ({sql}) t ORDER BY trade_date"
            df = _conn().execute(sql).df()
        except Exception as e:
            print(f"  ⚠️ get_etf_kline {ts_code} 失败: {e}", file=sys.stderr)
            return []
        rows = df.to_dict("records")
        for r in rows:
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
    def get_etf_basic(cls, code: str = "") -> dict | list:
        """ETF 基本信息 (名称/跟踪指数/管理公司 等).

        Args:
            code: '515790' 或 '515790.SH' → 返回单只 dict (无则 {})
                  '' → 返回全量 list[dict]

        2026-11-05 改: 单只查询下沉到 SQL (原来先全量读 2252 行再 pandas 过滤,
        0.33s/次 → 0.01s/次)。批量场景 (get_ctx 兜底) 反复调用, 这个开销不可接受。
        """
        path = ETF_HISTORY_DIR / "etf_basic.parquet"
        if not path.exists():
            return {} if code else []
        try:
            if code:
                ts_code = code if "." in code else _to_etf_ts_code(code)
                df = _conn().execute(
                    f"SELECT * FROM read_parquet('{path}') WHERE ts_code = '{ts_code}' LIMIT 1"
                ).df()
                if df.empty:
                    return {}
                return df.iloc[0].to_dict()
            df = _conn().execute(f"SELECT * FROM read_parquet('{path}')").df()
        except Exception as e:
            print(f"  ⚠️ get_etf_basic 失败: {e}", file=sys.stderr)
            return {} if code else []
        return df.to_dict("records")

    @classmethod
    def get_etf_klines(cls, codes: list[str], limit: int = 0) -> dict[str, list[dict]]:
        """批量读多只 ETF 日K (1 次 SQL), 返回 {ts_code: [row, ...]}

        比逐只 get_etf_kline 快 ~30x (实测 1992 只:
          - 逐只 1992 × 0.016s ≈ 32s
          - 批量 1 次 SQL ≈ 1.1s)

        Args:
            codes: 6 位代码列表 ('515790' / '515790.SH' 都行)
            limit: 每只取最近 N 根, 0 = 全量
        Returns:
            {ts_code: [row, ...]}, 升序 (trade_date 早→晚); 没有数据的 code 不出现。
            row 字段跟 get_etf_kline 一致 (ts_code/trade_date/open/high/low/close/pre_close/vol/amount/pct_chg)。
        """
        if not codes:
            return {}
        ts_codes = sorted({c if "." in c else _to_etf_ts_code(c) for c in codes})
        files = sorted(ETF_KLINE_DIR.glob("*.parquet"))
        if not files:
            return {}
        in_clause = ",".join(f"'{c}'" for c in ts_codes)
        glob_pat = "', '".join(str(f) for f in files)
        try:
            sql = f"""
                WITH ranked AS (
                    SELECT ts_code, trade_date, pre_close, open, high, low, close,
                           change, pct_chg, vol, amount,
                           ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
                    FROM read_parquet(['{glob_pat}'])
                    WHERE ts_code IN ({in_clause})
                )
                SELECT ts_code, trade_date, pre_close, open, high, low, close,
                       change, pct_chg, vol, amount
                FROM ranked
                WHERE rn <= {limit if limit > 0 else 100000000}
                ORDER BY ts_code, trade_date
            """
            df = _conn().execute(sql).df()
        except Exception as e:
            print(f"  ⚠️ get_etf_klines 失败: {e}", file=sys.stderr)
            return {}
        if df.empty:
            return {}
        out: dict[str, list[dict]] = {}
        for ts_code, sub in df.groupby("ts_code"):
            rows = sub.to_dict("records")
            for r in rows:
                pc = r.get("pre_close")
                if pc is None or (isinstance(pc, float) and pc != pc):
                    r["pre_close"] = r.get("close") or 0
                amt = r.get("amount")
                if amt is None or (isinstance(amt, float) and amt != amt):
                    r["amount"] = 0.0
                pct = r.get("pct_chg")
                if pct is None or (isinstance(pct, float) and pct != pct):
                    r["pct_chg"] = 0.0
            out[str(ts_code)] = rows
        return out

    @classmethod
    def get_daily_basic(cls, code: str) -> dict:
        """PE/PB/市值等估值快照，从 parquet 读（全市场，近1年）。"""
        return read_daily_basic(_to_ts_code(code))

    @classmethod
    def get_stock_basic(cls, code: str) -> dict:
        """行业/名称/上市日期等静态信息，从 parquet 读（全市场）。"""
        return read_stock_basic(code)

    @classmethod
    def get_ctx(cls, code: str, kline_only: bool = False, limit: int = 0):
        """返回 RawContext（L1 数据层唯一入口）。

        kline_only=True: 只读 K线，跳过 stock_basic/daily_basic。
        limit: K线条数上限，0=使用 config 默认值（kline_days）。
        """
        from tools.analysis.analysis_engine import RawContext
        from .sources.eastmoney import _synthesize_weekly

        kline  = cls.get_kline(code, limit=limit)
        weekly = _synthesize_weekly(kline)
        close  = kline[-1]["close"] if kline else 0.0

        if kline_only:
            return RawContext(
                kline=kline, weekly=weekly,
                current_price=close, market_cap_yi=0.0,
                industry="", code=code, name="",
            )

        sb  = cls.get_stock_basic(code)
        db  = cls.get_daily_basic(code)

        # total_mv 单位是"万元" (Tushare daily_basic), 转亿
        # 修复: 之前直接 db.get("total_mv") 拿"万" 当"亿" 存, 导致 EY 算成天文数字
        total_mv_yi = (db.get("total_mv") or 0.0) / 1e4

        # 2026-10-08 删: cyq_perf (胜率/中位成本, 跟 RSI/MA 同步, 没新信息)

        # 2026-11-02 修: current_price 优先用 K线最新收盘, 不再优先 daily_basic.close
        #   背景: stk_factor/daily_basic 落盘比 K线慢 (Tushare 先发沪市后发深市,
        #         11-02 当天只到 2368 只, 002/300/301 全缺)。
        #   旧写法 `db.get("close") or close` → 报告里股价显示昨天的, 跟实际差 10%。
        #   现在: K线是权威源且永远 >= daily_basic 新鲜度, 直接用 K线收盘。
        #   daily_basic 只提供 PE/市值/换手率等 K线没有的估值字段。
        current_price = close or (db.get("close") or 0.0)

        # 2026-11-05 加: ETF 兜底 (name / industry)
        #   背景: ETF 不在 stock_basic 里 → sb 恒为 {} → ETF 详报标题是空的
        #   ("# 512800 — | 2026-11-04"), 板块也是 "—"。
        #   ETF 没有"行业"概念, 但有**跟踪指数** —— 拿它当"板块"显示最贴切
        #   (e.g. "沪深300指数")。
        name = _INDEX_NAMES.get(code) or sb.get("name", "")
        # 2026-11-11 改: 行业口径统一到 **SW2021** (申万新版 134 个)
        #   来源: index_member_all 落的 industry_member.parquet (唯一真源)
        #   之前用 stock_basic.industry = 申万**旧版** 110 个 → 跟 sw_index 指数、
        #   factor-5y 头部、🏆行业地位 的同行分组 口径不一致。
        #   ETF 不在成分表里 → 留空, 下面的 ETF 分支会用跟踪指数名填。
        industry = cls.get_industry_info(code).get("l2_name") or sb.get("industry", "")
        if not name:
            eb = cls.get_etf_basic(code)
            if eb:
                name = str(eb.get("name") or "")
                bench = re.sub(r"×\s*100\s*%", "", str(eb.get("benchmark") or ""))
                industry = bench.replace("收益率", "").strip()

        return RawContext(
            kline=kline, weekly=weekly,
            current_price=current_price,
            market_cap_yi=total_mv_yi,
            industry=industry,
            code=code, name=name,
        )

    @classmethod
    def _sw_index_frame(cls):
        """全量行业 K 线 DataFrame (按年份文件拼一次, 进程内缓存)"""
        global _SW_INDEX_FRAME
        if _SW_INDEX_FRAME is not None:
            return _SW_INDEX_FRAME

        import pandas as pd
        files = sorted(SW_INDEX_KLINE_DIR.glob("*.parquet")) if SW_INDEX_KLINE_DIR.exists() else []
        frames = []
        for f in files:
            try:
                frames.append(pd.read_parquet(f))
            except Exception:
                continue
        if frames:
            df = pd.concat(frames, ignore_index=True)
            df["trade_date"] = df["trade_date"].astype(str)
            df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
        else:
            df = pd.DataFrame()
        _SW_INDEX_FRAME = df
        return _SW_INDEX_FRAME

    @classmethod
    def get_sw_index_bars(cls, ts_code: str) -> list[dict]:
        """申万行业指数日线 (1 个行业), 字段跟个股 ctx 一致

        数据源: data/history/sw_index/kline/{YYYY}.parquet (按年份分文件, 2026-11-11 改)

        ⚠️ vol → volume 别名**在这里做** (跟个股 _read_kline 同口径):
           威科夫扫描器读 k["volume"], 不转的话成交量恒为 0 → 量能判定全废 (静默出错)。

        Args:
            ts_code: 行业代码 (801012.SI)

        Returns:
            list[dict] (按 trade_date 升序), 没数据返 []
        """
        if not ts_code:
            return []
        code = str(ts_code)
        if code in _SW_INDEX_BARS_CACHE:
            return _SW_INDEX_BARS_CACHE[code]

        df = cls._sw_index_frame()
        if df.empty:
            _SW_INDEX_BARS_CACHE[code] = []
            return []
        sub = df[df["ts_code"] == code]
        if sub.empty:
            _SW_INDEX_BARS_CACHE[code] = []
            return []

        bars = []
        for r in sub.to_dict("records"):
            r["trade_date"] = str(r.get("trade_date", "")).replace("-", "")[:8]
            if "vol" in r and "volume" not in r:
                r["volume"] = r.get("vol")
            r["volume"] = r.get("volume") or 0.0
            r["amount"] = r.get("amount") or 0.0
            r["pct_chg"] = r.get("pct_chg") or 0.0
            r["pre_close"] = r.get("close")     # sw_daily 没有 pre_close → 用 close 兜底
            bars.append(r)
        _SW_INDEX_BARS_CACHE[code] = bars
        return bars

    @classmethod
    def _industry_member_info(cls) -> dict:
        """{ts_code: {l1_code, l1_name, l2_code, l2_name, l3_code, l3_name}} —— 进程内缓存"""
        global _INDUSTRY_MEMBER_INFO
        if _INDUSTRY_MEMBER_INFO is not None:
            return _INDUSTRY_MEMBER_INFO

        info: dict = {}
        if SW_INDEX_MEMBER_PARQUET.exists():
            try:
                df = _conn().execute(
                    f"SELECT ts_code, l1_code, l1_name, l2_code, l2_name, l3_code, l3_name "
                    f"FROM read_parquet('{SW_INDEX_MEMBER_PARQUET}')"
                ).df()
                for rec in df.to_dict("records"):
                    code = str(rec.get("ts_code") or "")
                    if code:
                        info[code] = rec
            except Exception as e:
                print(f"  ⚠️ _industry_member_info 失败: {e}", file=sys.stderr)
        _INDUSTRY_MEMBER_INFO = info
        return info

    @classmethod
    def get_industry_member_map(cls) -> dict[str, str]:
        """{ts_code: l2_code} —— 申万 2021 二级行业归属

        数据源: data/history/sw_index/industry_member.parquet (index_member_all, 60 天缓存)

        为什么不用 stock_basic.industry: 那是申万**旧版** 110 个, 跟 SW2021 (134 个)
        只直接对上 25 个 (元器件 vs 元件 / 汽车配件 vs 汽车零部件) → 对不上。
        """
        return {code: str(r.get("l2_code") or "")
                for code, r in cls._industry_member_info().items() if r.get("l2_code")}

    @classmethod
    def get_industry_members_by_l2(cls, l2_code: str) -> list[str]:
        """某个 SW2021 二级行业的全部成员 ts_code

        数据源: index_member_all 落的 industry_member.parquet
        """
        if not l2_code:
            return []
        code = str(l2_code)
        return [ts for ts, rec in cls._industry_member_info().items()
                if str(rec.get("l2_code") or "") == code]

    @classmethod
    def get_industry_info(cls, ts_code: str) -> dict:
        """某只票的 SW2021 行业归属 (一级/二级/三级), 没有返 {}

        Args:
            ts_code: '300274' / '300274.SZ' 都行
        """
        if not ts_code:
            return {}
        code = str(ts_code).strip()
        if "." not in code:
            code = _to_ts_code(code)
        rec = cls._industry_member_info().get(code)
        if not rec:
            return {}
        return {
            "l1_code": str(rec.get("l1_code") or ""),
            "l1_name": str(rec.get("l1_name") or ""),
            "l2_code": str(rec.get("l2_code") or ""),
            "l2_name": str(rec.get("l2_name") or ""),
            "l3_code": str(rec.get("l3_code") or ""),
            "l3_name": str(rec.get("l3_name") or ""),
        }

    @classmethod
    def get_financials(cls, code: str, lookback_quarters: int = 4) -> list[dict]:
        """单只最近 N 季的财务数据, 按 end_date 升序

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

    # 2026-10-08 删: DataStore.get_cyq_perf_history (cyq 整体下线)

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
        """全市场 K 线 1 次 SQL (替代各 batch 脚本里的散落实现)

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
        """全市场 daily_basic 1 次 SQL (替代 backfill_magic_cache 散落)

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
            return {}

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
            if not end_dates:
                return {ed: 0.0 for ed in end_dates}
            # 2026-11-10 性能修: 原来读全市场 daily_basic (635 万行) 再过滤出一只票 ——
            # 每只票的详报都要调一次, 是 /t-analyze 详报阶段的最大单点耗时 (1.6s/只)。
            # 改成 SQL 层按 ts_code 过滤, 读的行数从 635 万 → 该票 ~1500 行。
            df = _conn().execute(
                f"SELECT trade_date, total_mv FROM read_parquet('{DAILY_BASIC_DIR}/*.parquet') "
                f"WHERE ts_code = '{ts_code}'"
            ).df()
            if df.empty:
                return {ed: 0.0 for ed in end_dates}
            df = df.copy()
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
                          list_type: str = "自选") -> bool:
        """原子加股票到 watchlist (跳过已存在)

        替代 roc_ey_top20.py:353 _add_to_watchlist 散落实现 (原 magic_top20)

        2026-11-04 改: 去掉 notes 参数/字段 (铁律: watchlist 不维护 notes,
        只保留 code/name/sector/list_type/tags)
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

    # ── 批量过滤 (全市场扫描用, 0 网络) ────────────────────────────────────

    @classmethod
    def filter_codes_by_pe(cls, codes: list[str], pe_max: float) -> list[str]:
        """剔除 PE > pe_max 的票 (per-code latest stk_factor)。

        PE <= 0 或数据缺失的票放行。pe_max <= 0 直接返回原列表。

        Args:
            codes: 6 位纯数字 code 列表
            pe_max: PE 上限

        Returns:
            过滤后 code 列表 (保留原顺序)
        """
        if not codes or pe_max <= 0:
            return codes
        files = sorted(STK_FACTOR_DIR.glob("*.parquet"))
        if not files:
            return codes
        glob_expr = "[" + ", ".join(f"'{f}'" for f in files[-2:]) + "]"
        in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
        df = _conn().execute(f"""
            WITH latest AS (
                SELECT ts_code, pe,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM read_parquet({glob_expr})
                WHERE ts_code IN ({in_clause})
            )
            SELECT ts_code FROM latest
            WHERE rn = 1 AND pe > {pe_max} AND pe > 0
        """).df()
        expensive = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
        return [c for c in codes if c not in expensive]

    @classmethod
    def filter_codes_by_finance(cls, codes: list[str], pe_max: float = 500.0
                                ) -> tuple[list[str], dict[str, dict]]:
        """剔除亏损股 + 估值异常的票 (per-code latest stk_factor)。

        ⚠️ 跟 filter_codes_by_pe 的语义**不同**, 别混用:
            filter_codes_by_pe     → 只剔 PE > pe_max 的**高估值**票, 亏损股全放行
            filter_codes_by_finance → 剔**亏损股** + 估值异常 (--with-finance 专用)

        2026-11-09 修 Bug2: 原 --with-finance 直接复用 filter_codes_by_pe(pe_max=500),
        那个函数语义是「只剔高估值」, flag 承诺的「剔除亏损股」完全没生效。

        🔴 亏损股怎么认 (实测 stk_factor 全库 5588 只, **PE 恒为正, 没有一只 <= 0**):
            Tushare 对算不出估值的票把 pe/pe_ttm 写成 NULL, 所以亏损股的标记是 **NULL**,
            不是 <= 0。2026-11-09 实测:
                pe_ttm IS NULL  → 1734 只, 其中 78.7% 净利率<0 (真亏损)
                pe  IS NULL     → 1464 只, 其中 70.9% 净利率<0
            全库 A/B 实测 (ground truth = financials 净利率<0, 1502 只亏损股):
                旧 filter_codes_by_pe(500)     → 只剔 38 只,  召回  2.5%
                新 filter_codes_by_finance(500) → 剔 1398 只,   召回 93.1%, 精度 73.8%
            取 pe_ttm 口径 —— 跟本项目「估值全用已审计 TTM 财报」一致, 且召回最高。
            判 pe IS NULL 会误杀 28.7% 的盈利股 (dynamic PE 算不出但 TTM 盈利的票)。

        剔除规则 (pe / pe_ttm 任一命中即剔):
            pe_ttm IS NULL      → TTM 亏损 (算不出 TTM 估值)
            pe <= 0 或 pe_ttm <= 0 → 亏损 (防御性, 当前数据不触发)
            pe > pe_max 或 pe_ttm > pe_max → 估值异常

        stk_factor 压根没这票的记录 → 放行 (不因缺数据误杀)。

        Args:
            codes: 6 位纯数字 code 列表
            pe_max: PE 上限 (超过视为估值异常, 默认 500)

        Returns:
            (保留的 code 列表, {code: {"pe":…, "pe_ttm":…}})
            第二个返回值供报告渲染回填 PE 列 (缺失的票不在表里)
        """
        if not codes:
            return [], {}
        files = sorted(STK_FACTOR_DIR.glob("*.parquet"))
        if not files:
            return list(codes), {}
        glob_expr = "[" + ", ".join(f"'{f}'" for f in files[-2:]) + "]"
        in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
        df = _conn().execute(f"""
            WITH latest AS (
                SELECT ts_code, pe, pe_ttm,
                       ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                FROM read_parquet({glob_expr})
                WHERE ts_code IN ({in_clause})
            )
            SELECT ts_code, pe, pe_ttm FROM latest WHERE rn = 1
        """).df()
        # ⚠️ parquet 的 NULL 读进 pandas 是 NaN 不是 None, 必须归一化,
        #    否则下游 `is None` / `isinstance(x, float)` 全部判错 (md 会渲染出 "nan")
        pe_map = {r['ts_code'].split('.')[0]: {
            "pe": r['pe'] if _is_num(r['pe']) else None,
            "pe_ttm": r['pe_ttm'] if _is_num(r['pe_ttm']) else None,
        } for r in df.to_dict('records')}

        kept = []
        for code in codes:
            info = pe_map.get(code)
            if info is None:
                kept.append(code)          # 无 stk_factor 数据 → 放行
                continue
            pe, pe_ttm = info["pe"], info["pe_ttm"]
            if not _pe_finance_ok(pe, pe_ttm, pe_max):
                continue
            kept.append(code)
        return kept, pe_map

    @classmethod
    def filter_codes_by_quality(cls, codes: list[str],
                                or_yoy_min: float = 0.0,
                                np_yoy_min: float | None = None,
                                roe_min: float | None = None,
                                gm_min: float | None = None) -> list[str]:
        """剔除财务质量不达标的票 (最新季 financials, 上一季兜底)。

        各阈值 None = 不限。全部阈值为 None/0 时直接返回原列表。

        Args:
            codes: 6 位纯数字 code 列表
            or_yoy_min: 营收 yoy 下限 (%)
            np_yoy_min: 净利 yoy 下限 (%, None=不限)
            roe_min: ROE 下限 (%, None=不限)
            gm_min: 毛利率下限 (%, 绝对值, None=不限)

        Returns:
            过滤后 code 列表 (保留原顺序)
        """
        if not codes:
            return []
        files = sorted(FIN_DIR.glob("*.parquet")) if FIN_DIR.exists() else []
        if not files:
            return []
        in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
        q1, q1_label = str(files[-1]), files[-1].stem
        np_clause  = f"AND netprofit_yoy > {np_yoy_min}" if np_yoy_min is not None else ""
        roe_clause = f"AND roe > {roe_min}"              if roe_min  is not None else ""
        gm_clause  = f"AND grossprofit_margin > {gm_min}" if gm_min  is not None else ""
        fallback_sql = ""
        if len(files) > 1:
            q2 = str(files[-2])
            fallback_sql = f"""
                UNION ALL
                SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, roe
                FROM read_parquet('{q2}')
                WHERE ts_code IN ({in_clause})
                  AND ts_code NOT IN (
                      SELECT ts_code FROM read_parquet('{q1}') WHERE fetch_status = 'ok'
                  )
            """
        df = _conn().execute(f"""
            SELECT ts_code FROM (
                SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, roe
                FROM read_parquet('{q1}')
                WHERE ts_code IN ({in_clause}) AND fetch_status = 'ok'
                {fallback_sql}
            )
            WHERE or_yoy > {or_yoy_min}
              {np_clause}
              {roe_clause}
              {gm_clause}
        """).df()
        passed = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
        return [c for c in codes if c in passed]

    @classmethod
    def filter_codes_by_leverage(cls, codes: list[str], max_nd_ratio: float) -> list[str]:
        """剔除净负债/总市值 > max_nd_ratio 的重债票。

        净现金票 (netdebt < 0) 和数据缺失的票放行。max_nd_ratio <= 0 直接返回原列表。

        Args:
            codes: 6 位纯数字 code 列表
            max_nd_ratio: 净负债/市值上限

        Returns:
            过滤后 code 列表 (保留原顺序)
        """
        if not codes or max_nd_ratio <= 0:
            return codes
        files = sorted(FIN_DIR.glob("*.parquet")) if FIN_DIR.exists() else []
        if not files:
            return codes
        sf_files = sorted(STK_FACTOR_DIR.glob("*.parquet"))
        if not sf_files:
            return codes
        in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
        q1 = str(files[-1])
        sf_glob = "[" + ", ".join(f"'{f}'" for f in sf_files[-2:]) + "]"
        fin_sql = f"""
            SELECT ts_code, netdebt FROM read_parquet('{q1}', union_by_name=true)
            WHERE ts_code IN ({in_clause}) AND fetch_status = 'ok'
        """
        if len(files) > 1:
            q2 = str(files[-2])
            fin_sql += f"""
                UNION ALL
                SELECT ts_code, netdebt FROM read_parquet('{q2}', union_by_name=true)
                WHERE ts_code IN ({in_clause})
                  AND ts_code NOT IN (
                      SELECT ts_code FROM read_parquet('{q1}', union_by_name=true)
                      WHERE fetch_status = 'ok'
                  )
            """
        df = _conn().execute(f"""
            WITH sf AS (
                SELECT ts_code, total_mv FROM (
                    SELECT ts_code, total_mv,
                           ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
                    FROM read_parquet({sf_glob})
                ) WHERE rn = 1
            ),
            fin AS ({fin_sql})
            SELECT fin.ts_code
            FROM fin JOIN sf ON sf.ts_code = fin.ts_code
            WHERE fin.netdebt IS NOT NULL AND sf.total_mv > 0
              AND (fin.netdebt / 1e8) / (sf.total_mv / 1e4) > {max_nd_ratio}
        """).df()
        overleveraged = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
        return [c for c in codes if c not in overleveraged]


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
