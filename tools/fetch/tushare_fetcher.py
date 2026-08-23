"""
tushare_fetcher.py — Tushare Pro 数据拉取 (2026-07-22 接入)

数据源: https://tushare.pro (2000 积分档, 解锁 6 项核心数据)
Token: 从 os.environ["TUSHARE_TOKEN"] 读 (.env 文件, 不进 git)

覆盖的 9 个核心接口 (按用户最常看的 6 项排):
  1. daily_basic   - PE_TTM / PB / 市值 (替代 push2 f164, 更准)
  2. weekly/monthly- 周月 K 线 (Sina 60分硬上限的备选, 跨更大周期)
  3. moneyflow_hsgt- 北向资金 (每日沪深股通净买入)
  4. margin_detail - 融资融券 (单只票的融资余额/买入)
  5. top_list      - 龙虎榜 (当日上榜机构+营业部)
  6. income        - 利润表 (营收/净利/同比)
  7. fina_indicator- 财务指标 (ROE/毛利率/净利率/资产负债率)
  8. dividend      - 分红送转 (历史分红)
  9. stock_basic   - 基础信息 (行业/上市日期/总股本)

设计原则:
  - 永远不 raise (每个接口返回 (data, status), status ∈ {OK/EMPTY/PERM_DENIED/EXCEPTION_xxx})
  - Tushare 2000 积分档: 全接口 80 次/分, 单接口 100 次/分 (并发跑 watchlist 时控制 worker 数)
  - 网络问题直接 fallback 到空, 不影响其他段
  - 重复行 (tushare 已知 bug) 用 drop_duplicates 去重
"""

from __future__ import annotations

import os
import logging
from typing import Any

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
# tushare moneyflow 单接口 4s/次 是真实频控, daily K线 3500+ 行传输慢
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

def _latest_trade_date(lookback: int = 7) -> str:
    """返回最近 lookback 天内最后一个交易日（YYYYMMDD）。

    逻辑：从今天往前推，跳过周六(5)和周日(6)，取第一个工作日。
    注意：只排除周末，不排除法定节假日（节假日 tushare 会返回空，调用方再往前一天重试）。
    """
    from datetime import datetime, timedelta
    d = datetime.now()
    for _ in range(lookback):
        if d.weekday() < 5:  # 0=Mon ... 4=Fri
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return datetime.now().strftime("%Y%m%d")  # fallback


def _code_to_ts(code: str) -> str:
    """A 股代码 → tushare ts_code 格式: 300274 → 300274.SZ / 600519 → 600519.SH"""
    if not code:
        return ""
    code = str(code).strip()
    if "." in code:
        return code  # 已经是 ts_code 格式
    if code.startswith(("60", "68", "90", "51", "56")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _ts_to_code(ts_code: str) -> str:
    """300274.SZ → 300274"""
    return ts_code.split(".")[0] if ts_code else ""


def _safe_call(api_name: str, **kwargs) -> tuple[list[dict] | None, str]:
    """统一 tushare 调用, 永不 raise, 返回 (data_list, status)

    2026-07-22 升级: 加 1 小时内存缓存, moneyflow (4s/次) 复用后 0s
    2026-07-23 升级: 加 3 次重试 (网络抖动兜底, Tushare 官方推荐)
    2026-07-28 升级: EMPTY 时 retry (数据未到/网络抖动兜底, 2s/4s 退避)
    2026-07-30 v5.10.10: 加 2000 积分档白名单 (forecast/top_list/north_flow/margin 等 5000+ 积分档接口
                  100% 永远返空, 1 次失败直接 EMPTY 不 retry, 省 3s/只 × 17 = 51s 浪费)
    """
    if not _PRO:
        return None, "TOKEN_MISSING"

    # v5.10.10 加: 2000 积分档不可用接口白名单 (限量接口, 永久返空, 不 retry)
    # 实测: 17 baseline 中 100% 失败, retry 只是浪费 1+2=3s/只
    # 2026-07-30 v5.10.11 撤回 daily_basic (错判)
    # 2026-07-30 v5.10.12 加回 daily_basic: 不是接口不可用, 是 Tushare 频控 100/分
    # 2026-07-30 v5.10.16 再撤 daily_basic: 实测 002028 单只 _PRO.daily_basic 返 5296 行 (5.2 年历史)
    #   daily_basic 是**常用接口** (2000 积分档可调), 不能跟限量接口混为一谈
    #   真撞频控时 1 次返空, 不在白名单时走 retry 2 次 (1s+2s) 兜底
    #   commit 3bfe7f7 limit=30 → 250 修复"返空"实际是命中 _NO_RETRY_2000 的副作用
    _NO_RETRY_2000 = {
        "forecast",      # 业绩预告 (5000 积分档)
        "top_list",      # 龙虎榜 (5000 积分档)
        "north_flow",    # 北向资金 (10000 积分档)
        "margin",        # 融资融券 (5000 积分档)
        "limit_list",    # 涨跌停 (5000 积分档)
        "forecast_vip",  # 多券商一致预期 (5000 积分档)
    }

    # 1. 查缓存
    cached = _cache_get(api_name, kwargs)
    if cached is not None:
        return cached, "OK_CACHED"

    # 2. 实际调 tushare (v5.10.18 改: 0 retry, 1 次失败直接报)
    # 用户原话: "现在还需要个毛的retry" — 5000 积分档不限流, retry 是浪费 1+2=3s/只
    # 真撞频控/异常 1 次立刻报, 让人看 log 定位, 别假装 retry 3 次骗自己
    try:
        method = getattr(_PRO, api_name)
        df = method(**kwargs)
        if df is None or len(df) == 0:
            # 2000 积分档不可用接口 (限量接口) → EMPTY
            if api_name in _NO_RETRY_2000:
                logger.info(
                    "tushare.%s 2000 积分档不可用 (限量接口), 返空 (不 retry)",
                    api_name,
                )
                return None, "EMPTY_NEED_HIGHER_TIER"
            # 其他接口返空 → EMPTY (不重试)
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
# 1. 基础信息 stock_basic
# ============================================================

def get_stock_basic(code: str) -> tuple[dict | None, str]:
    """单只票基础信息: 行业 / 上市日期 / 总股本

    返回: {ts_code, name, industry, list_date, total_share, float_share, market}
    单位: total_share / float_share = 万股 (跟 daily_basic 一致)

    2026-07-24 修复: 2000 积分档 stock_basic 不返回 total_share/float_share
    改用 daily_basic.total_share 补全 (查最近一个交易日)
    """
    ts_code = _code_to_ts(code)
    data, status = _safe_call(
        "stock_basic",
        ts_code=ts_code,
        fields="ts_code,name,industry,list_date,market",
    )
    if not data:
        return None, status
    row = dict(data[0])
    # 2026-07-24: stock_basic 2000 积分档不返回 total_share，从 daily_basic 补（最近交易日，无重试）
    try:
        trade_date = _latest_trade_date()
        db_data, db_status = _safe_call(
            "daily_basic",
            ts_code=ts_code,
            trade_date=trade_date,
            fields="ts_code,total_share,float_share",
        )
        if db_data and db_data[0].get("total_share"):
            row["total_share"] = db_data[0]["total_share"]  # 万股
            row["float_share"] = db_data[0].get("float_share") or 0
    except Exception:
        pass
    return row, "OK"


# ============================================================
# 2. 日 K 线 daily (24h 可用, 不 WAF)
# ============================================================

def get_daily(code: str, start_date: str = "", end_date: str = "", limit: int = 0) -> tuple[list[dict] | None, str]:
    """日 K 线

    Args:
        code: 6 位代码
        start_date/end_date: 20260715 格式
        limit: 如果给 (且不给 start_date), 取最近 N 天
    """
    ts_code = _code_to_ts(code)
    kwargs = {"ts_code": ts_code, "fields": "ts_code,trade_date,open,high,low,close,pre_close,vol,amount,pct_chg"}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    data, status = _safe_call("daily", **kwargs)
    if not data:
        return None, status
    # tushare 默认倒序 (新→旧), 我们升序 (旧→新), 跟 web.ifzq 一致
    data.sort(key=lambda x: x["trade_date"])
    if limit and not start_date:
        data = data[-limit:]
    return data, "OK"


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


def get_daily_basic_by_date(trade_date: str) -> tuple[list[dict] | None, str]:
    """按交易日拉全市场 daily_basic，一次返回当天所有股票的 PE/PB/市值。

    trade_date: YYYYMMDD
    返回: [{ts_code, trade_date, close, pe_ttm, pb, total_mv, circ_mv, turnover_rate, volume_ratio}, ...]
    """
    data, status = _safe_call(
        "daily_basic",
        trade_date=trade_date,
        fields="ts_code,trade_date,close,pe,pe_ttm,pb,total_mv,circ_mv,turnover_rate,volume_ratio",
    )
    return data, status


def get_daily_range(start_date: str, end_date: str) -> tuple[list[dict] | None, str]:
    """按日期范围拉全市场日K线（不限 ts_code），用于首次建档。

    一次调用返回该区间内全市场所有股票所有日期的数据。
    start_date/end_date: YYYYMMDD
    """
    data, status = _safe_call(
        "daily",
        start_date=start_date,
        end_date=end_date,
        fields="ts_code,trade_date,open,high,low,close,pre_close,vol,amount,pct_chg",
    )
    if data:
        data.sort(key=lambda x: (x["trade_date"], x["ts_code"]))
    return data, status


# ============================================================
# 3. daily_basic — PE/PB/市值 (替代 push2 f164)
# ============================================================

def get_daily_basic(code: str, start_date: str = "", end_date: str = "", limit: int = 30) -> tuple[list[dict] | None, str]:
    """PE_TTM / PB / 总市值 / 流通市值

    替代 push2 f164, 更准 (tushare 是当日收盘后正式值, 不是盘中)
    """
    ts_code = _code_to_ts(code)
    kwargs = {
        "ts_code": ts_code,
        "fields": "ts_code,trade_date,close,pe,pe_ttm,pb,ps,ps_ttm,total_mv,circ_mv,turnover_rate,volume_ratio",
    }
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    data, status = _safe_call("daily_basic", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["trade_date"])
    if limit and not start_date:
        data = data[-limit:]
    return data, "OK"


# ============================================================
# 4. 周线 weekly / 月线 monthly
# ============================================================

def get_weekly(code: str, start_date: str = "", end_date: str = "", limit: int = 60) -> tuple[list[dict] | None, str]:
    """周 K 线 — Sina 60分硬上限的备选, 用于看跨大周期"""
    ts_code = _code_to_ts(code)
    kwargs = {"ts_code": ts_code, "fields": "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    data, status = _safe_call("weekly", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["trade_date"])
    if limit and not start_date:
        data = data[-limit:]
    return data, "OK"


def get_monthly(code: str, start_date: str = "", end_date: str = "", limit: int = 24) -> tuple[list[dict] | None, str]:
    """月 K 线 — 跨更大周期"""
    ts_code = _code_to_ts(code)
    kwargs = {"ts_code": ts_code, "fields": "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"}
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    data, status = _safe_call("monthly", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["trade_date"])
    if limit and not start_date:
        data = data[-limit:]
    return data, "OK"


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
# 5. 北向资金 moneyflow_hsgt
# ============================================================

def get_north_flow(trade_date: str = "") -> tuple[dict | None, str]:
    """北向资金 (沪深股通) — 每日北向净流入

    Args:
        trade_date: 20260721 格式, 留空取最近 1 个完整交易日 (避免今天未收盘)
    """
    if not trade_date:
        from datetime import datetime, timedelta
        # 取最近 30 个交易日, 排除今天 (今天可能未收盘, 数据未出)
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        cal_data, _ = _safe_call(
            "trade_cal",
            exchange="SSE",
            is_open="1",
            start_date=start,
            end_date=end,
            fields="cal_date,is_open",
        )
        if cal_data:
            cal_data.sort(key=lambda x: x["cal_date"], reverse=True)
            # 排除今天 (避免未收盘), 取最近 1 个完整交易日
            today = datetime.now().strftime("%Y%m%d")
            for c in cal_data:
                if c["cal_date"] < today:
                    trade_date = c["cal_date"]
                    break
        if not trade_date:
            return None, "EMPTY_CAL"
    data, status = _safe_call(
        "moneyflow_hsgt",
        trade_date=trade_date,
        fields="trade_date,ggt_ss,ggt_sz,hgt,sgt,south_money,north_money",
    )
    if not data:
        return None, status
    data.sort(key=lambda x: x["trade_date"], reverse=True)
    return data[0], "OK"


# ============================================================
# 6. 融资融券 margin_detail
# ============================================================

def get_margin(code: str, trade_date: str = "") -> tuple[dict | None, str]:
    """单只票融资融券

    字段: rzye=融资余额, rzmre=融资买入, rzche=融资偿还,
          rqye=融券余额, rqmcl=融券卖出, rqchl=融券偿还
    """
    ts_code = _code_to_ts(code)
    kwargs = {
        "ts_code": ts_code,
        "fields": "trade_date,ts_code,rzye,rzmre,rzche,rqye,rqmcl,rqchl",
    }
    if trade_date:
        kwargs["trade_date"] = trade_date
    data, status = _safe_call("margin_detail", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["trade_date"], reverse=True)
    return data[0], "OK"


# ============================================================
# 7. 龙虎榜 top_list
# ============================================================

def get_top_list(code: str, trade_date: str = "") -> tuple[list[dict] | None, str]:
    """龙虎榜 — 哪些机构/营业部门买卖

    Args:
        code: 留空拿当日所有上榜, 给定 code 拿该票所有上榜
        trade_date: 必填, 留空用 trade_cal 拿最近 1 个完整交易日
    """
    if not trade_date:
        from datetime import datetime, timedelta
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
        cal_data, _ = _safe_call(
            "trade_cal",
            exchange="SSE",
            is_open="1",
            start_date=start,
            end_date=end,
            fields="cal_date,is_open",
        )
        if cal_data:
            cal_data.sort(key=lambda x: x["cal_date"], reverse=True)
            today = datetime.now().strftime("%Y%m%d")
            for c in cal_data:
                if c["cal_date"] < today:
                    trade_date = c["cal_date"]
                    break
        if not trade_date:
            return None, "EMPTY_CAL"
    kwargs = {
        "trade_date": trade_date,
        "fields": "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate",
    }
    if code:
        kwargs["ts_code"] = _code_to_ts(code)
    data, status = _safe_call("top_list", **kwargs)
    if not data:
        return None, status
    return data, "OK"


# ============================================================
# 8. 财务三大表 + 财务指标
# ============================================================

def get_income(code: str, period: str = "") -> tuple[dict | None, str]:
    """利润表 — 单期"""
    ts_code = _code_to_ts(code)
    kwargs = {
        "ts_code": ts_code,
        "fields": "ts_code,end_date,total_revenue,operate_profit,n_income,basic_eps,total_cogs,sell_exp,admin_exp,fin_exp",
    }
    if period:
        kwargs["period"] = period
    data, status = _safe_call("income", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["end_date"], reverse=True)
    return data[0], "OK"


def get_fina_indicator(code: str, period: str = "") -> tuple[dict | None, str]:
    """财务指标 — ROE/毛利率/净利率/资产负债率/同比

    period: 20250331 (Q1) / 20250630 (Q2) / 20251231 (年报)
    """
    ts_code = _code_to_ts(code)
    kwargs = {
        "ts_code": ts_code,
        "fields": "ts_code,end_date,roe,roe_eps,gross_margin,netprofit_margin,debt_to_assets,eps,yoy_eps,yoy_tr,yoy_or,assets_yoy,equity_yoy",
    }
    if period:
        kwargs["period"] = period
    data, status = _safe_call("fina_indicator", **kwargs)
    if not data:
        return None, status
    data.sort(key=lambda x: x["end_date"], reverse=True)
    return data[0], "OK"


# ============================================================
# 9. 分红 dividend
# ============================================================

def get_dividend(code: str, limit: int = 10) -> tuple[list[dict] | None, str]:
    """分红送转历史"""
    ts_code = _code_to_ts(code)
    data, status = _safe_call(
        "dividend",
        ts_code=ts_code,
        fields="ts_code,end_date,div_proc,stk_div,cash_div,stk_bo_rate,cash_div_tax,record_date,ex_date,pay_date",
    )
    if not data:
        return None, status
    data.sort(key=lambda x: x["end_date"], reverse=True)
    return data[:limit], "OK"


# ============================================================
# 10. 个股资金流向 pro.moneyflow (真正的 fflow!)
# ============================================================

def get_money_flow(code: str, start_date: str = "", end_date: str = "", limit: int = 10) -> tuple[list[dict] | None, str]:
    """个股资金流向 (小/中/大/特大单买卖 + 净流入)

    字段: buy_sm_vol/buy_sm_amount (小单买入手数/金额)
          buy_md_vol/buy_md_amount (中单)
          buy_lg_vol/buy_lg_amount (大单)
          buy_elg_vol/buy_elg_amount (特大单)
          sell_sm_*/sell_md_*/sell_lg_*/sell_elg_* (对应卖出)
          net_mf_vol / net_mf_amount (净流入, 单位 万元)

    Tushare 2000 积分档可用 (替代 push2delay fflow)

    Args:
        code: 6 位代码
        start_date/end_date: 20260715 格式
        limit: 如果不给 start_date, 取最近 N 天
    """
    ts_code = _code_to_ts(code)
    kwargs = {
        "ts_code": ts_code,
        "fields": "ts_code,trade_date,buy_sm_vol,buy_sm_amount,sell_sm_vol,sell_sm_amount,buy_md_vol,buy_md_amount,sell_md_vol,sell_md_amount,buy_lg_vol,buy_lg_amount,sell_lg_vol,sell_lg_amount,buy_elg_vol,buy_elg_amount,sell_elg_vol,sell_elg_amount,net_mf_vol,net_mf_amount",
    }
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    data, status = _safe_call("moneyflow", **kwargs)
    if not data:
        return None, status
    # 默认倒序 (新→旧), 升序 (旧→新), 跟 web.ifzq 一致
    data.sort(key=lambda x: x["trade_date"])
    if limit and not start_date:
        data = data[-limit:]
    return data, "OK"


# ============================================================
# 11. 业绩预告 pro.forecast (2000 积分档可用, forecast_vip 需 5000 积分)
# ============================================================

def get_forecast(code: str, recent_n: int = 4) -> tuple[list[dict] | None, str]:
    """业绩预告 (业绩预增/预减/扭亏/首亏/续亏/续盈/略增/略减)

    Tushare 2000 积分档可用 (forecast_vip 需 5000 积分, 不在档内)
    4 个披露期: 1/15-1/30, 4/15-4/30, 7/15-7/30, 10/15-10/30

    字段:
      ann_date       公告日期
      end_date       报告期 (20260630 = 中报)
      type           预增/预减/扭亏/首亏/续亏/续盈/略增/略减
      p_change_min   变动幅度下限 (%)
      p_change_max   变动幅度上限 (%)
      net_profit_min 净利润下限 (万元)
      net_profit_max 净利润上限 (万元)
      summary        业绩预告摘要
      change_reason  业绩变动原因

    Returns:
        最近 N 条预告 (按 ann_date 倒序)
    """
    ts_code = _code_to_ts(code)
    # ann_date 拉近 1 年 (4 个披露期足够)
    from datetime import datetime, timedelta
    end_d = datetime.now().strftime("%Y%m%d")
    start_d = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

    data, status = _safe_call(
        "forecast",
        ts_code=ts_code,
        start_date=start_d,
        end_date=end_d,
        fields="ts_code,ann_date,end_date,type,p_change_min,p_change_max,net_profit_min,net_profit_max,summary,change_reason",
    )
    if not data:
        return None, status
    # 按 ann_date 倒序 (新→旧)
    data.sort(key=lambda x: x.get("ann_date", ""), reverse=True)
    return data[:recent_n], "OK"


# ============================================================
# 状态码 → emoji
# ============================================================


# ============================================================
# 状态码 → emoji
# ============================================================

def status_emoji(status: str) -> str:
    if status == "OK":
        return "✅"
    if status == "EMPTY":
        return "⚪"
    if status == "TOKEN_MISSING":
        return "🔴"
    if status.startswith("EXCEPTION"):
        return "🟠"
    if status.startswith("PERM"):
        return "🔒"
    return "❓"


# ============================================================
# 批量拉取 (sync_stock.py 调用这个)
# ============================================================

def fetch_all_tushare(code: str, trade_date: str = "") -> dict[str, Any]:
    """一键拉所有 tushare 段, 返回 dict 给 sync_stock.py 拼装

    段: stock_basic / daily_basic / weekly / north_flow / margin / top_list
        / income / fina_indicator / dividend / money_flow / forecast
    (2026-07-28 砍 monthly: 5方法×3周期 = 周/日/60分, 无月线)

    Args:
        code: 6 位 A 股代码
        trade_date: 用于 margin/top_list (留空取最近 1 日)
    """
    out = {
        "ts_code": _code_to_ts(code),
        "stock_basic": None,
        "daily_basic": None,
        "weekly": None,
        "monthly": None,
        "north_flow": None,
        "margin": None,
        "top_list": None,
        "income": None,
        "fina_indicator": None,
        "dividend": None,
        "money_flow": None,
        "forecast": None,
        "statuses": {},
    }

    # 1. 基础信息
    sb, sb_s = get_stock_basic(code)
    out["stock_basic"] = sb
    out["statuses"]["stock_basic"] = sb_s

    # 2. daily_basic (替代 push2 f164, PE/PB/市值)
    db, db_s = get_daily_basic(code, limit=30)
    out["daily_basic"] = db
    out["statuses"]["daily_basic"] = db_s

    # 3. 周月 K (2026-07-28 v5.5: 强制走 config.project.yaml:data.weekly_limit, 读不到报错)
    try:
        import yaml as _yaml
        from pathlib import Path as _Path
        _cfg = _yaml.safe_load(open(_Path('config/project.yaml'), encoding='utf-8'))
        weekly_limit = _cfg.get('data', {}).get('weekly_limit')
        if weekly_limit is None:
            raise FileNotFoundError(
                "config/project.yaml 缺 data.weekly_limit 字段 (硬编码已废, 全部走 config)"
            )
    except FileNotFoundError:
        raise
    except Exception as e:
        raise FileNotFoundError(f"config/project.yaml 读取失败: {e}")
    wk, wk_s = get_weekly(code, limit=weekly_limit)
    out["weekly"] = wk
    out["statuses"]["weekly"] = wk_s

    # 2026-07-28 砍: monthly K 线分析不用 (5方法×3周期 = 周/日/60分, 无月线)
    out["monthly"] = []
    out["statuses"]["monthly"] = "SKIP"

    # 4. 北向资金 (不需要 code, 全市场 1 个数)
    nf, nf_s = get_north_flow(trade_date=trade_date)
    out["north_flow"] = nf
    out["statuses"]["north_flow"] = nf_s

    # 5. 融资融券
    mg, mg_s = get_margin(code, trade_date=trade_date)
    out["margin"] = mg
    out["statuses"]["margin"] = mg_s

    # 6. 龙虎榜 (可能空, 正常)
    tl, tl_s = get_top_list(code, trade_date=trade_date)
    out["top_list"] = tl
    out["statuses"]["top_list"] = tl_s

    # 7. 利润表 (Q1 已披露, 留空取最近期)
    ic, ic_s = get_income(code)
    out["income"] = ic
    out["statuses"]["income"] = ic_s

    # 8. 财务指标
    fi, fi_s = get_fina_indicator(code)
    out["fina_indicator"] = fi
    out["statuses"]["fina_indicator"] = fi_s

    # 9. 分红
    dv, dv_s = get_dividend(code, limit=10)
    out["dividend"] = dv
    out["statuses"]["dividend"] = dv_s

    # 10. 个股资金流向 (真正的 fflow)
    mf, mf_s = get_money_flow(code, limit=60)
    out["money_flow"] = mf
    out["statuses"]["money_flow"] = mf_s

    # 11. 业绩预告 (2000 积分档可用, 4 条/年)
    fc, fc_s = get_forecast(code, recent_n=4)
    out["forecast"] = fc
    out["statuses"]["forecast"] = fc_s

    return out


# ============================================================
# 11. 组合 fflow 方案 (get_fund_flow_combined)
# 2026-07-22 整合到 tushare_fetcher
# 2026-07-22 升级 v2: 不再用 OBV 派生, 只用 tushare.money_flow
# ============================================================

def get_fund_flow_combined(code: str, days: int = 10, moneyflow_list: list | None = None) -> dict:
    """
    组合方案: Tushare.money_flow 真实数据 (主力=大单+特大单)
    - 主源: Tushare.money_flow API (2000 积分档, 24h 稳定)
    - 备源: 无 (OBV 派生已废弃, 2026-07-22)

    v5.10.17 改: 接受 moneyflow_list 参数 (复用 fetch_all 已拉数据)
      - 不传 moneyflow_list: 内部调 get_money_flow(code, limit=days) 拉数据
      - 传 moneyflow_list: 直接用, 0 重复拉取 (v5.10.16 修 fflow 重复 2 次的 bug)
      - 旧 CLI 兼容: get_fund_flow_combined(code, days=10) 仍可独立调

    返回结构:
      {
        "success": True,
        "source": "🟢 Tushare.money_flow (10 日真实)",
        "data_columns": {
          "real": [{"date": "2026-07-21", "main_yi": +18.28, ...}],
          "derived": []  # 2026-07-22: OBV 派生已废弃, 留空保兼容
        },
        "today_real": {...},
        "verdict": "🟢 主力明显进货 ...",
        "score": +6,
        "data_source_type": "tushare_moneyflow",
        "fflow_available": True
      }
    """
    real_column = []
    today_real = None

    # 1. Tushare.money_flow 真实 (24h 稳定, 2000 积分档)
    # v5.10.17: 优先用传入的 moneyflow_list, 0 重复拉取
    if moneyflow_list is None:
        try:
            moneyflow_list, ts_status = get_money_flow(code, limit=days)
        except Exception as e:
            logger.warning("get_fund_flow_combined(Tushare) fail: %s", e)
            moneyflow_list = []

    if moneyflow_list and len(moneyflow_list) > 0:
        for row in moneyflow_list:
            # 各单净额 (万元) = 买 - 卖
            sm_net_wan = (float(row.get("buy_sm_amount", 0) or 0) - float(row.get("sell_sm_amount", 0) or 0))
            md_net_wan = (float(row.get("buy_md_amount", 0) or 0) - float(row.get("sell_md_amount", 0) or 0))
            lg_net_wan = (float(row.get("buy_lg_amount", 0) or 0) - float(row.get("sell_lg_amount", 0) or 0))
            elg_net_wan = (float(row.get("buy_elg_amount", 0) or 0) - float(row.get("sell_elg_amount", 0) or 0))
            # 转亿 (万 → 亿, /10000)
            real_column.append({
                "date": row.get("trade_date"),
                "main_yi": (lg_net_wan + elg_net_wan) / 1e4,    # 主力 = 大单+特大单 (亿)
                "large_yi": lg_net_wan / 1e4,
                "xlarge_yi": elg_net_wan / 1e4,
                "small_yi": sm_net_wan / 1e4,
                "medium_yi": md_net_wan / 1e4,
                "net_mf_amount": float(row.get("net_mf_amount", 0) or 0) / 1e4,
                "source": "tushare_moneyflow",
            })
        today_real = real_column[-1]

    # 2. 评分 + 判定 (基于 Tushare 真实数据)
    score = 0
    signals = []
    if today_real:
        main_today = today_real.get("main_yi", 0)
        xlarge_today = today_real.get("xlarge_yi", 0)
        if main_today > 3:
            signals.append(f"✅ 当日主力真实净流入 +{main_today:.2f}亿 (Tushare.money_flow)")
            score += 3
        elif main_today > 0:
            signals.append(f"🟡 当日主力轻微流入 +{main_today:.2f}亿")
            score += 1
        elif main_today < -3:
            signals.append(f"🔴 当日主力真实净流出 {main_today:.2f}亿")
            score -= 3
        if xlarge_today > 2:
            signals.append(f"✅ 当日超大单 +{xlarge_today:.2f}亿 (机构买入)")
            score += 2
        elif xlarge_today < -2:
            signals.append(f"⚠️ 当日超大单 {xlarge_today:.2f}亿 (机构出货)")
            score -= 2

    # 整体判定
    main_str = f"今日真实 {today_real.get('main_yi', 0):.2f}亿" if today_real else "无数据"
    if score >= 4:    verdict = f"🟢 主力明显进货 ({main_str})"
    elif score >= 1:  verdict = f"🟡 主力轻微流入 ({main_str})"
    elif score == 0:  verdict = "⬜ 中性震荡"
    elif score >= -2: verdict = f"🟠 主力偏流出 ({main_str})"
    else:             verdict = f"🔴 主力明显流出 ({main_str})"

    return {
        "success": today_real is not None,
        "source": "🟢 Tushare.money_flow (10 日真实, 已废弃 OBV 派生)",
        "data_columns": {
            "real": real_column,
            "derived": [],  # 2026-07-22: OBV 派生已废弃
        },
        "data": real_column,
        "today_real": today_real,
        "history_obv": [],
        "verdict": verdict,
        "signals": signals,
        "score": score,
        "fallback_chain": ["tushare.moneyflow"],
        "data_as_of": today_real["date"] if today_real else None,
        "data_source_type": "tushare_moneyflow",
        "fflow_available": today_real is not None,
        "always_available": True,
        "next_real_update": "Tushare 每日 ~18:00 更新昨日数据",
        "note": "Tushare.money_flow 真实 (亿元). OBV 派生已废弃, Tushare 不可用时 fflow 段直接空",
    }


# ============================================================
# 演示
# ============================================================

if __name__ == "__main__":
    from pprint import pprint
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "300274"
    print(f"=== tushare fetch_all_tushare({code}) ===")
    result = fetch_all_tushare(code)
    print("\n--- 段状态 ---")
    for k, v in result["statuses"].items():
        print(f"  {k:20s} {status_emoji(v)} {v}")
    print("\n--- 关键数据 (前 200 字符) ---")
    for k in ["stock_basic", "daily_basic", "north_flow", "margin", "fina_indicator"]:
        v = result[k]
        if v is None:
            print(f"  {k}: None")
        elif isinstance(v, list):
            print(f"  {k}: {len(v)} 行")
        else:
            print(f"  {k}: {str(v)[:200]}")
