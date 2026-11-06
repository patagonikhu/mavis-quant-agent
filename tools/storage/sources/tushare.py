"""
tushare_fetcher.py — Tushare Pro 数据拉取 (2026-07-22 接入, 2026-09-04 升级 5000 积分档)

数据源: https://tushare.pro (5000 积分档, 解锁 20+ 项核心数据)
Token: 从 os.environ["TUSHARE_TOKEN"] 读 (.env 文件, 不进 git)

本模块有独立封装的接口:
  1. daily         - 日 K 线 (按交易日拉全市场, 24h 可用, 不 WAF)
  2. stk_factor_pro - PE/PB/PS/PS-TTM/股息率/自由流通股换手率/总股本/流通股本 (5000 积分档 pro, v6.2.4 替代 daily_basic)
  3. index_daily   - 指数 K 线 (指数要走 index_daily, daily 只返个股)
  4. fund_basic / fund_daily - ETF 场内基金

其余接口 (stock_basic / fina_indicator_vip / forecast ...) 由调用方直接用 _safe_call 拉。

设计原则:
  - 永远不 raise (每个接口返回 (data, status), status ∈ {OK/EMPTY/PERM_DENIED/EXCEPTION_xxx})
  - Tushare 5000 积分档: 全接口 200 次/分, 单接口 200 次/分
    2026-11-03 实测: 连续调 stk_factor_pro 680 次 (峰值 196/分) 零限频
    ⚠️ doc_id=328 接口页写的 "5000积分 30次/分" 对本账号**不成立** (官方两页自相矛盾,
       以 doc_id=290 积分频次对应表 + 实测为准)。别再按 30/分 设 sleep。
  - 网络问题直接 fallback 到空, 不影响其他段
  - 重复行 (tushare 已知 bug) 用 drop_duplicates 去重

v6.2.4 升级要点:
  - 5000 积分档: 解锁 stk_factor_pro / fina_indicator_vip (本模块只封装 stk_factor_pro)
  - daily_basic 被 stk_factor_pro 替代 (16 列, 多了 ps/dv_ratio/float_share/turnover_rate_f)
  - stock_basic 仍缺 total_share (跨积分档都缺, 走 stk_factor_pro 兜底)
"""

from __future__ import annotations

import os
import logging

logger = logging.getLogger(__name__)


def _load_dotenv(path: str = ".env") -> None:
    """自动加载 .env 文件 (项目根), 简单实现, 不依赖 python-dotenv

    格式: KEY=value (注释 # 开头, 空行跳过)
    """
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # 不覆盖已存在的环境变量 (用户 shell 设的优先)
                if k not in os.environ and v:
                    os.environ[k] = v
    except Exception as e:
        logger.warning("load .env fail: %s", e)


# 启动时自动加载
_load_dotenv()

# ============================================================
# 初始化
# ============================================================

_TOKEN = os.environ.get("TUSHARE_TOKEN", "").strip()
_PRO = None  # lazy init

# 2026-09-18 加: tushare API 调用统计 (按 API 名分组, sync 末尾打印)
# 用途: sync summary 展示每接口调用次数, 0 网络排查 + 性能监控
_API_CALL_STATS: dict[str, int] = {}


def reset_api_stats() -> None:
    """sync 入口重置, 每次跑独立统计"""
    global _API_CALL_STATS
    _API_CALL_STATS = {}


def get_api_stats() -> dict[str, int]:
    """读当前调用统计"""
    return dict(_API_CALL_STATS)


def _record_call(api_name: str) -> None:
    """_safe_call 内部调用时 +1"""
    _API_CALL_STATS[api_name] = _API_CALL_STATS.get(api_name, 0) + 1


if _TOKEN:
    try:
        import tushare as ts
        ts.set_token(_TOKEN)
        _PRO = ts.pro_api()
        logger.info("tushare pro init OK (token %s...)", _TOKEN[:8])
    except Exception as e:
        logger.warning("tushare init fail: %s", e)
        _PRO = None
else:
    logger.warning("TUSHARE_TOKEN not set in env (.env 缺失), tushare 接口全部返 None")


# ============================================================
# 内存缓存 (2026-07-22 性能优化)
# tushare 单接口 4s/次 是真实频控, daily K线 3500+ 行传输慢
# 单进程内同 (api_name, kwargs) 1 小时复用, 避免重复拉
# ============================================================

import time as _time
_CACHE: dict = {}  # key=(api_name, frozenset(kwargs)) -> (timestamp, data)


def _cache_get(api_name: str, kwargs: dict):
    key = (api_name, tuple(sorted(kwargs.items())))
    item = _CACHE.get(key)
    if item is None:
        return None
    ts, data = item
    # 默认 1 小时过期 (tushare 日级数据当日不变)
    if _time.time() - ts > 3600:
        _CACHE.pop(key, None)
        return None
    return data


def _cache_put(api_name: str, kwargs: dict, data):
    key = (api_name, tuple(sorted(kwargs.items())))
    _CACHE[key] = (_time.time(), data)


# ============================================================
# 工具函数
# ============================================================


def _safe_call(api_name: str, **kwargs) -> tuple[list[dict] | None, str]:
    """统一 tushare 调用, 永不 raise, 返回 (data_list, status)

    2026-07-22 升级: 加 1 小时内存缓存, 重接口 (4s/次) 复用后 0s
    2026-09-18 升级: **0 retry** — 1 次失败直接报 (5000 积分档不限流, retry 是浪费)
    2026-09-04 v6.2.4: 升级 5000 积分档

    ⚠️ 需要"EMPTY 再试一次"的地方, 由**调用方**自己做 (例: sync.action_stk_factor
    循环里 EMPTY → sleep 5s → 重试 1 次, 治"限流假阳导致该日永久缺失")。
    """
    if not _PRO:
        return None, "TOKEN_MISSING"

    # 1. 查缓存
    cached = _cache_get(api_name, kwargs)
    if cached is not None:
        return cached, "OK_CACHED"

    # 2. 实际调 tushare (v5.10.18 改: 0 retry, 1 次失败直接报)
    # 2026-09-18 加: 每次实际调用都 +1, 给 sync summary 统计用
    _record_call(api_name)
    # 用户原话: "现在还需要个毛的retry" — 5000 积分档不限流, retry 是浪费 1+2=3s/只
    # 真撞频控/异常 1 次立刻报, 让人看 log 定位, 别假装 retry 3 次骗自己
    try:
        method = getattr(_PRO, api_name)
        df = method(**kwargs)
        if df is None or len(df) == 0:
            # 接口返空 → EMPTY (不重试)
            logger.warning("tushare.%s 返空 (待确认原因, 不 retry)", api_name)
            return None, "EMPTY"
        df = df.drop_duplicates()
        result = df.to_dict("records"), "OK"
        # 写缓存
        _cache_put(api_name, kwargs, result[0])
        return result
    except Exception as e:
        msg = str(e)
        # tushare client.py: code != 0 → raise Exception(result['msg'])
        # 从 msg 文本中识别具体错误类型，便于定位是限流/权限/网络
        if any(k in msg for k in ("每分钟最多", "访问频率", "rate limit", "2002")):
            status = "RATE_LIMITED"
        elif any(k in msg for k in ("权限", "permission", "2001", "未授权", "token")):
            status = "PERM_DENIED"
        elif any(k in msg for k in ("timeout", "timed out", "TimeoutError", "ReadTimeout")):
            status = "TIMEOUT"
        elif any(k in msg for k in ("ConnectionError", "连接", "network", "Network")):
            status = "NETWORK_ERROR"
        else:
            status = f"EXCEPTION_{type(e).__name__}"
        logger.error("tushare.%s(%s) 调用失败 [%s]: %s", api_name, kwargs, status, msg)
        return None, status


# ============================================================
# 1. 日 K 线 daily + stk_factor_pro (24h 可用, 不 WAF)
# ============================================================

def get_daily_by_date(trade_date: str) -> tuple[list[dict] | None, str]:
    """按交易日拉全市场日K线，一次返回当天所有股票。

    trade_date: YYYYMMDD
    返回: [{ts_code, trade_date, open, high, low, close, vol, amount, pct_chg}, ...]
    """
    data, status = _safe_call(
        "daily",
        trade_date=trade_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount,pct_chg",
    )
    return data, status


def get_stk_factor_by_date(trade_date: str) -> tuple[list[dict] | None, str]:
    """按交易日拉全市场 stk_factor_pro (v6.2.4 起替代 daily_basic)。

    trade_date: YYYYMMDD
    返回 16 列: [{ts_code, trade_date, close, pe, pe_ttm, pb, total_mv, circ_mv,
                 turnover_rate, turnover_rate_f, volume_ratio, total_share, float_share,
                 ps, ps_ttm, dv_ratio, dv_ttm}, ...]

    1 次拿全市场 5548 只 1 天 (按 trade_date 1 次 API)
    2026-11-03 实测: 本账号上限 ≥ 196/分 (连打 680 次零限频), 官方 doc_id=290 标 200/分
    sync 侧 sleep 0.3s (=200/分) 调用。限流是**抛异常**, 返空 = 非交易日。

    字段 (5000 积分档 pro):
      - 基础: ts_code, trade_date, close
      - 估值: pe, pe_ttm, pb, ps, ps_ttm  ← ps/ps_ttm 是 daily_basic 没有的
      - 股息: dv_ratio, dv_ttm
      - 股本: total_share, float_share  ← float_share 替代了用 circ_mv/close 推算
      - 流通: total_mv, circ_mv
      - 换手: turnover_rate, turnover_rate_f (自由流通股换手), volume_ratio
    """
    data, status = _safe_call(
        "stk_factor_pro",
        trade_date=trade_date,
        fields=(
            "ts_code,trade_date,close,"
            "pe,pe_ttm,pb,ps,ps_ttm,"
            "dv_ratio,dv_ttm,"
            "total_mv,circ_mv,"
            "turnover_rate,turnover_rate_f,volume_ratio,"
            "total_share,float_share"
        ),
    )
    return data, status


# ============================================================
# 2. 指数 K 线 index_daily
# ============================================================


def get_index_daily(code: str, start_date: str = "", end_date: str = "", limit: int = 60) -> tuple[list[dict] | None, str]:
    """指数 K 线 (Tushare.index_daily, 跟 get_daily 区别: 用 index_daily 接口拉指数而非个股)

    2026-07-29 v5.10.2 加: 之前 resonance.py 用 get_daily 拉指数 (399006/000300/399808) 返空
    真实原因: Tushare.daily 只返个股 K 线, 指数要走 index_daily
    ts_code 格式: '399006.SZ' / '000300.SH' (带点, 不是 'sz399006' 前缀)
    """
    # 指数代码转 Tushare 格式 (399006.SZ / 000300.SH)
    if "." not in code:
        code_clean = code.replace("sh", "").replace("sz", "")
        if code_clean.startswith("3") or code_clean.startswith("0"):
            suffix = ".SZ" if code_clean.startswith(("3", "0")) and not code_clean.startswith(("000", "001", "002", "003", "300")) else ".SH"
            # 简化: 3 开头 = 深证 (.SZ), 6 开头 = 上证 (.SH) - 但 0 开头多数是上证, 3 开头是深证
            if code_clean.startswith("3"):
                suffix = ".SZ"
            elif code_clean.startswith(("0", "9")):
                suffix = ".SH"
            else:
                suffix = ".SH"
            code = f"{code_clean}{suffix}"
    kwargs = {"ts_code": code, "fields": "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    data, status = _safe_call("index_daily", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["trade_date"])
    if limit and not start_date:
        data = data[-limit:]
    return data, "OK"


# ============================================================
# 3. ETF 场内基金 (2026-10-09 加, 走 tushare fund_basic / fund_daily / fund_nav)
#   - 字段名跟 daily 股票 100% 重合 (OHLCV), AnalysisEngine.analyze_history 无需改
#   - fund_basic 一次性全量, 落 data/history/etf/etf_basic.parquet
# ============================================================

def get_fund_basic(market: str = "E", status: str = "L", fields: str = "ts_code,name,management,fund_type,list_date,delist_date,m_fee,c_fee,issue_amount") -> tuple[list[dict] | None, str]:
    """公募基金列表 (Tushare.fund_basic)。

    2026-10-09 加: ETF 数据层入口。默认 market='E' 场内 + status='L' 上市中。

    积分要求: 2000 (5000+ 频次更高)。
    单次最大: 15000 条, 全市场场内 ETF ≈ 600 只, 一次拿完。

    常用 fields:
      ts_code, name, management, custodian, fund_type, found_date, due_date,
      list_date, delist_date, issue_date, issue_amount,
      m_fee (管理费%), c_fee (托管费%), duration_year, p_value,
      min_amount, exp_return, benchmark, status, invest_type, type, market

    Args:
        market: 'E' 场内 / 'O' 场外 (默认 'E')
        status: 'L' 上市中 / 'D' 摘牌 / 'I' 发行 (默认 'L')
        fields: 取哪些列 (默认核心 9 列, 需更多传 '' 拿全量)
    """
    kwargs = {"market": market, "status": status}
    if fields:
        kwargs["fields"] = fields
    data, status_str = _safe_call("fund_basic", **kwargs)
    return data, status_str


def get_fund_daily(trade_date: str = "", ts_code: str = "", start_date: str = "", end_date: str = "") -> tuple[list[dict] | None, str]:
    """ETF 日线行情 (Tushare.fund_daily)。

    2026-10-09 加: 4 种用法, 跟 tushare 文档一致:
      1) 全市场某天 (默认推荐, 单次 600 行, 不超 5000 限): trade_date='20261009'
      2) 全市场最近 1 天: 不传参
      3) 单只某天: ts_code='513330.SH', trade_date='20261009'
      4) 单只区间: ts_code='513330.SH', start_date='20210101', end_date='20261009'

    字段 (实测 10 列):
      ts_code, trade_date, open, high, low, close, pre_close,
      change, pct_chg, vol, amount

    注意:
      - 字段名跟 daily 个股 100% 重合 → AnalysisEngine.analyze_history 不需改
      - vol 单位: 手 (跟 daily 个股一致, 不是股)
      - amount 单位: 千元 (跟 daily 个股一致)

    积分: 5000+ 起步。
    """
    if not ts_code and not trade_date and not start_date and not end_date:
        # 拉最近 1 个交易日全市场
        kwargs = {}
    elif trade_date and not ts_code:
        # 全市场某天 (单次 ≈600 行)
        kwargs = {"trade_date": trade_date}
    else:
        kwargs = {"ts_code": ts_code} if ts_code else {}
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if trade_date:
            kwargs["trade_date"] = trade_date
    data, status = _safe_call("fund_daily", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x.get("trade_date", ""))
    return data, "OK"
