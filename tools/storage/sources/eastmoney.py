"""
data_fetcher.py — 统一数据抓取层 (v4.0, 2026-07-22)

⚠️ 2026-07-22 重大升级: 全部走 Tushare Pro, 单一权威源
  - 历史: v1-v3 用 push2/qtimg/ifzq/datacenter, WAF 频发 + 多源不一致
  - 现在: 5 个 getter 全部委托给 tools.tushare_fetcher, 单一权威源
  - 历史 fallback 链 (push2/qtimg/datacenter) 全部移除, 函数签名保留
    以保 render_report / analysis_data / 老 data 工具 等外部 import 不破坏

数据源矩阵 (v4.0):
  ┌──────────────┬─────────────────────────────────────────┐
  │ 数据         │  唯一源                                     │
  ├──────────────┼─────────────────────────────────────────┤
  │ 实时价       │  Tushare.daily (close) + Tushare.daily_basic (pe_ttm/pb) │
  │ 历史 K 线    │  Tushare.daily (qfq 复权从 amount 字段推)   │
  │ 主力资金     │  Tushare.moneyflow (5000 积分档, 24h 稳定)  │
  │ EPS / 财务   │  Tushare.fina_indicator (ROE/EPS/CAGR 自建) │
  │ 总股本/市值  │  Tushare.stock_basic.total_share + daily_basic.total_mv │
  └──────────────┴─────────────────────────────────────────┘

历史 curl helper (_get_with_retry / _get_session / _secid / _secucode 等)
保留但已无调用, 后续清理。

使用方式:
  from tools.storage.sources.eastmoney import get_realtime_price
  data, status = get_realtime_price("002371")
  if status == "OK":
      print(data["price"])
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any, Optional

# requests 仍 import (老的 fetcher 可能用), 但 _get_with_retry 已改用 curl
try:
    import requests
except ImportError:
    requests = None

logger = logging.getLogger(__name__)

# 加载 config/project.yaml (60分 K线 datalen 从这里读)
import pathlib as _pl
try:
    import yaml as _yaml
    _CFG_PATH = _pl.Path(__file__).parent.parent.parent.parent / "config" / "project.yaml"
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _PROJECT_CFG = _yaml.safe_load(_f) or {}
except FileNotFoundError:
    raise FileNotFoundError(
        f"config/project.yaml 不存在, 请创建 (路径: {_CFG_PATH})\n"
        f"  必需字段: data.kline_60m_count, data.kline_days 等"
    )

# ============================================================
# 基础配置
# ============================================================

DEFAULT_TIMEOUT = 5
DEFAULT_RETRIES = 2
RETRY_BACKOFF = 2

# 🚨 关键: 数据 API 一律直连, 不加 proxies (CLAUDE.md 2026-07-21 修正)
NO_PROXY = {"http": None, "https": None}

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        })
        _session.trust_env = False  # 不读取系统 http_proxy 环境变量
    return _session


def _get_with_retry(url: str, params: dict | None = None, headers: dict | None = None,
                    timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES,
                    parser: str = "json") -> tuple[Any, str]:
    """
    单 URL 重试 + 解析, 返回 (data, status)
    parser: "json" | "text"

    2026-07-21: 改用 curl subprocess 替代 requests
    - 解决 SAP 网络下 SSL/握手问题
    - 不加 --proxy (CLAUDE.md 规则: 数据 API 一律直连)
    """
    # 把 params 拼到 URL (curl 不会自动拼)
    if params:
        from urllib.parse import urlencode
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode(params)}"

    cmd = [
        "curl", "-sL",
        "--max-time", str(timeout),
        # 不让 curl 自己 retry (push2 EMPTY 不是网络抖动, WAF 拒接不可恢复)
        # 我们自己控制重试逻辑, EMPTY 时立即 break
        "-A", "Mozilla/5.0",
        url,
    ]

    last_status = "ALL_FAILED"
    for attempt in range(retries):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=False,  # 拿 bytes, 自己处理编码 (qtimg 是 GBK)
                timeout=timeout + 3,
            )

            if result.returncode != 0:
                stderr = (result.stderr or "").lower()
                if "ssl" in stderr or "certificate" in stderr:
                    last_status = "SSL_ERROR"
                elif "could not connect" in stderr or "refused" in stderr:
                    last_status = "NET_REFUSED"
                elif "timed out" in stderr or "timeout" in stderr:
                    last_status = "TIMEOUT"
                else:
                    last_status = f"CURL_ERR_{result.returncode}"
                logger.warning("curl 失败 (attempt %d): %s", attempt + 1, stderr[:150])
                if attempt < retries - 1:
                    time.sleep(RETRY_BACKOFF ** attempt)
                continue

            raw = result.stdout or b""
            if not raw.strip():
                last_status = "EMPTY"
                # 2026-07-21: Empty reply 几乎都是 WAF 拒接 (push2 系),
                # 不可恢复, 立即跳出 (不再 retry, 留给 fallback 链)
                logger.warning("curl Empty reply (attempt %d, WAF?), 不再重试", attempt + 1)
                break

            # 2026-07-21: 解码兼容 GBK (qtimg) / UTF-8 (push2/ifzq/datacenter)
            for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                last_status = "DECODE_FAIL"
                continue

            if parser == "json":
                try:
                    parsed = json.loads(text)
                    if not parsed:
                        last_status = "EMPTY"
                        continue
                    return parsed, "OK"
                except (json.JSONDecodeError, ValueError):
                    last_status = "PARSE_FAIL"
                    break
            else:  # text
                return text, "OK"

        except subprocess.TimeoutExpired:
            last_status = "TIMEOUT"
        except FileNotFoundError:
            return None, "CURL_NOT_FOUND"
        except Exception as e:
            last_status = f"EXCEPTION_{type(e).__name__}"

        if attempt < retries - 1:
            time.sleep(RETRY_BACKOFF ** attempt)

    return None, last_status


# ============================================================
# secid / secucode 工具
# ============================================================

def _is_sh(code: str) -> bool:
    return code.startswith(("60", "688", "90", "51", "56"))


def _secid(code: str) -> str:
    return f"1.{code}" if _is_sh(code) else f"0.{code}"


def _secucode(code: str) -> str:
    return f"{code}.SH" if _is_sh(code) else f"{code}.SZ"


def _normalize_name(name: str) -> str:
    """归一化股票名 — 修 qtimg 返回全角字符 (京东方Ａ → 京东方A)

    qtimg 腾讯接口对部分股票名用全角字母 (A 股/A 类), 直接拿会:
      1. 文件名冲突: docs/analyze-000725-京东方Ａ.md vs 000725-京东方A.md
      2. 报告里显示不一致

    修法: 全角字母/数字 → 半角 (A-Z 0-9)
    """
    if not name:
        return name
    out = []
    for ch in name:
        code = ord(ch)
        # 全角字母 (A-Z): U+FF21 - U+FF3A
        if 0xFF21 <= code <= 0xFF3A:
            out.append(chr(code - 0xFF21 + 0x41))
        # 全角字母 (a-z): U+FF41 - U+FF5A
        elif 0xFF41 <= code <= 0xFF5A:
            out.append(chr(code - 0xFF41 + 0x61))
        # 全角数字 (0-9): U+FF10 - U+FF19
        elif 0xFF10 <= code <= 0xFF19:
            out.append(chr(code - 0xFF10 + 0x30))
        else:
            out.append(ch)
    return "".join(out)


# ============================================================
# 2. 历史 K 线
# ============================================================

def get_kline(code: str, days: int = 250, use_cache: bool = True) -> tuple[list[dict] | None, str]:
    """
    v4.0 (2026-07-22): 单一源 Tushare.daily
      - 字段: trade_date / open / high / low / close / vol (amount 在 tushare 是成交额)
      - 注: Tushare.daily 默认不复权, 但 daily_basic 已经能算 MA 偏离等,
        真要复权用 Tushare.pro_bar (另需 5000 积分), 暂用不复权

    v5.3 (2026-07-28): 加增量缓存 use_cache=True
      - 读 data/_old_d/{code}.json 找 kline 最后一日
      - 仅拉新数据 (start_date=last_date+1), merge 旧数据
      - 跨数不变 (仍是 days 根), 但 API 调用从 1 次变 N 根新增
      - 节省: 重 dump 全部 57 只从 ~2.4 分钟 → ~30 秒

    历史 (v3.1): web.ifzq 腾讯备源 WAF 频发, 已移除
    """
    try:
        from .tushare import get_daily as _ts_daily
    except Exception as e:
        return None, f"IMPORT_FAIL_{type(e).__name__}"

    rows, status = _ts_daily(code, limit=days)
    if not rows:
        return None, status or "EMPTY"

    result = []
    for r in rows:
        try:
            result.append({
                "trade_date": r.get("trade_date", ""),
                "open":  float(r.get("open",  0) or 0),
                "close": float(r.get("close", 0) or 0),
                "high":  float(r.get("high",  0) or 0),
                "low":   float(r.get("low",   0) or 0),
                "volume":float(r.get("vol",   0) or 0),
            })
        except (TypeError, ValueError):
            continue
    if not result:
        return None, "PARSE_FAIL"

    return result, "OK"


# ============================================================
# 3. 主力资金 (fflow)
# ============================================================

def get_fund_flow(code: str, days: int = 10) -> tuple[list[dict] | None, str]:
    """
    v4.0 (2026-07-22): 单一源 Tushare.moneyflow (5000 积分档, 24h 稳定)
      返回值: list[dict] (字段: date / main_net(万) / small / mid / big / super_big)

    历史: Tushare.money_flow 直接调, 2026-07-22 整合
    """
    try:
        from .tushare import get_money_flow as _ts_mf
    except Exception as e:
        return None, f"IMPORT_FAIL_{type(e).__name__}"

    ts_data, ts_status = _ts_mf(code, limit=days)
    if not ts_data:
        return None, ts_status or "EMPTY"

    rows = []
    for r in ts_data:
        # tushare moneyflow: buy_*_amount - sell_*_amount = 各单净额 (万)
        sm_net = (float(r.get("buy_sm_amount", 0) or 0) - float(r.get("sell_sm_amount", 0) or 0))
        md_net = (float(r.get("buy_md_amount", 0) or 0) - float(r.get("sell_md_amount", 0) or 0))
        lg_net = (float(r.get("buy_lg_amount", 0) or 0) - float(r.get("sell_lg_amount", 0) or 0))
        elg_net = (float(r.get("buy_elg_amount", 0) or 0) - float(r.get("sell_elg_amount", 0) or 0))
        # 主力 (大单+特大单) 净额, 万
        main_net = lg_net + elg_net
        rows.append({
            "trade_date": r.get("trade_date", ""),
            "main_net": main_net,
            "small": sm_net,
            "mid": md_net,
            "big": lg_net,
            "super_big": elg_net,
        })
    if not rows:
        return None, "EMPTY"
    return rows, "OK"


# ============================================================
# 4. EPS 一致预期
# ============================================================


# ============================================================
# 5. 一次性拉全 (给 t-analyze 用)
# ============================================================

def _build_eps_table(code: str) -> tuple[list[dict] | None, str]:
    """
    2026-07-22 修复: 优先 datacenter 机构一致预期, fallback tushare 自建 NTM
    返回: (eps_table, source_label)
      source_label ∈ {"datacenter_consensus", "tushare_built_ntm", "EMPTY"}

    关键: 阳光电源例子
      datacenter: E0=6.49 / E1=7.44 / E2=9.13 / E3=10.81 (真机构预测)
      tushare 自建: E0=1.12 / E1=1.232 / ... (Q1 单季 EPS 推全年, 错 6 倍)
      → 优先 datacenter!
    """
    from .tushare import _code_to_ts as _to_ts_code
    try:
        import urllib.request
        import urllib.parse
        secucode = _to_ts_code(code)
        url = (
            f"https://datacenter.eastmoney.com/securities/api/data/v1/get?"
            f"reportName=RPT_HSF10_RESPREDICT_COUNTSTATISTICS"
            f"&columns=SECUCODE,SECURITY_NAME_ABBR,YEAR,YEAR_MARK,EPS,EPS_LASTMONTHS,"
            f"ROE,PARENT_NETPROFIT,TOTAL_OPERATE_INCOME"
            f"&filter=(SECUCODE%3D%22{urllib.parse.quote(secucode)}%22)"
            f"&pageNumber=1&pageSize=20&sortTypes=1&sortColumns=RANK"
            f"&source=HSF10&client=PC"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            import json as _json
            d = _json.loads(r.read().decode())
        rows = (d.get("result") or {}).get("data") or []
        if rows:
            result_table = []
            for r in rows:
                year = r.get("YEAR", "")
                mark = r.get("YEAR_MARK", "")
                eps = r.get("EPS")
                if eps is None or eps == 0:
                    continue
                try:
                    eps = float(eps)
                except (TypeError, ValueError):
                    continue
                roe = r.get("ROE") or 0
                np_yi = (r.get("PARENT_NETPROFIT") or 0) / 1e8
                rev_yi = (r.get("TOTAL_OPERATE_INCOME") or 0) / 1e8
                result_table.append({
                    "year": f"{year}{mark}",
                    "year_mark": mark,
                    "eps": round(eps, 4),
                    "net_profit_yi": round(np_yi, 2) if np_yi else 0.0,
                    "revenue_yi": round(rev_yi, 2) if rev_yi else 0.0,
                    "roe": round(float(roe), 2) if roe else 0.0,
                })
            result_table.sort(key=lambda x: x["year"])
            result_table = result_table[-4:]
            if result_table:
                return result_table, "datacenter_consensus"
    except Exception as e:
        logger.warning("datacenter EPS 拉取失败 (%s), 返回 EMPTY", type(e).__name__)

    return None, "EMPTY"


def fetch_all(code: str, kline_days: int = 250, sector: str = "") -> dict:
    """
    v4.0 (2026-07-22): 顶层直接调 tushare 6 段 (避免 getter 内部重复拉)
      - stock_basic (1 次)
      - daily K 线 limit=kline_days (1 次)
      - daily_basic limit=30 (1 次, 复用给 price + shares)
      - money_flow limit=10 (1 次, fflow)
      - fina_indicator (1 次, 给 EPS 用)
      - income (1 次, 给 EPS np_yi 用)
    v5.6 (2026-07-29): daily_basic_long 拆出 fetch_all
      - fetch_all 保持 6 段并发 (Tushare 全接口 80/分 内, 实际跑 watchlist 平均 6-7 段/秒)
      - daily_basic_long (250 天 PE/PB/市值/换手率) 由 老 data 工具 顶层另外拉
        1 只票 +1 API call, watchlist 间隔 60s 自然恢复, 不在 fetch_all 内部串行 (会拖累 13s+)

    配合 tushare_fetcher 1 小时内存缓存, 同一只股二次跑 0.5s 内

    Returns:
        {
            "code": str, "name": str, "current_price": float, "pe_ttm": float,
            "kline": list[dict], "fflow": list[dict], "eps_table": list[dict],
            "shares_yi": float, "market_cap_yi": float,
            "statuses": {...}
        }
    """
    from datetime import datetime
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .tushare import (
        get_stock_basic as _ts_sb,
        get_daily as _ts_daily,
        get_daily_basic as _ts_db,
        get_money_flow as _ts_mf,
        get_weekly as _ts_weekly,
    )

    result = {
        "code": code,
        "name": "",
        "sector": sector,
        "close": None,
        "pe_ttm": None,
        "kline": None,
        "moneyflow": None,  # v5.10.17 加: Tushare.moneyflow 原始 list (给 fflow verdict 复用, 0 重拉)
        "fflow": None,
        "eps_table": None,
        "total_share": None,
        "total_mv": None,
        "weekly": None,  # 2026-07-28 v5.5 加 (周线 156 根, 给 5方法×3周期 周线维度用)
        "statuses": {},
        "fetch_time": datetime.now().isoformat(),
    }

    # 2026-07-28 v5.5 加: 周线 250 根 (强制走 config.project.yaml:data.weekly_limit, 读不到报错)
    weekly_limit = _PROJECT_CFG.get("data", {}).get("weekly_limit")
    if weekly_limit is None:
        raise FileNotFoundError(
            "config/project.yaml 缺 data.weekly_limit 字段 (跟 weekly_limit 硬编码已废, 全部走 config)"
        )
    # 2026-07-29 v5.6 加: daily_basic 历史 (PE/PB/市值/换手率), 走 config
    daily_basic_days = _PROJECT_CFG.get("data", {}).get("daily_basic_days", 250)

    # ===== 并发拉 4 段 =====
    import time as _t
    _t0 = _t.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        fut_sb  = ex.submit(_ts_sb,   code)
        fut_day = ex.submit(_ts_daily, code, limit=kline_days)
        fut_mf  = ex.submit(_ts_mf,   code, 30)
        fut_wk  = ex.submit(_ts_weekly, code, limit=weekly_limit)

        sb,  sb_s  = fut_sb.result()
        daily, d_s = fut_day.result()
        mf,  mf_s  = fut_mf.result()
        wk,  wk_s  = fut_wk.result()
    logger.info("[fetch_all] 4 段并发耗时 %.1fs", _t.time() - _t0)

    # daily_basic 改 limit=1 (v5.10.23: 只取最新 1 行给顶层 pe_ttm/pb/total_mv 等)
    # 之前 limit=100 拉 22 年历史 5296 行 = 1253 KB/dump, 0 consumer (TrendPullback 从 market_cap_yi 顶层读)
    # 改 limit=1: dump 从 2052 KB 降到 ~800 KB, 节省 62%
    db, db_s = _ts_db(code, 1)  # 5000 积分档单只 limit=1 (1 行 1 调用, 给顶层字段)

    # 1. stock_basic
    if sb:
        result["name"] = sb.get("name", "")
        result["industry"] = sb.get("industry", "")
        result["list_date"] = sb.get("list_date", "")
    result["statuses"]["stock_basic"] = sb_s

    # 2. daily K 线
    if daily:
        kline = []
        for r in daily:
            try:
                kline.append({
                    "trade_date": r.get("trade_date", ""),
                    "open":       float(r.get("open",    0) or 0),
                    "close":      float(r.get("close",   0) or 0),
                    "high":       float(r.get("high",    0) or 0),
                    "low":        float(r.get("low",     0) or 0),
                    "volume":     float(r.get("vol",     0) or 0),
                    "amount":     float(r.get("amount",  0) or 0),
                    "pct_chg":    float(r.get("pct_chg", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
        result["kline"] = kline
        if kline:
            result["close"] = kline[-1]["close"]
    result["statuses"]["kline"] = d_s

    # 3. daily_basic
    if db:
        # v5.10.23 改: 不存 daily_basic_long (22 年历史 5296 行, 1253 KB/dump, 0 consumer)
        # 之前 v5.6: 存完整历史给 TrendPullback 大市值缩放 / PE 历史趋势
        # 实际 TrendPullback 从 market_cap_yi 顶层读 (line 102 scanner.py), 不读 daily_basic_long
        # 顶层字段 pe_ttm/pb/total_mv/... 从 db[-1] 取 (v5.10.23 db 改 limit=1, 1 行)
        last_db = db[-1]
        if last_db.get("pe_ttm") is not None:
            try:
                result["pe_ttm"] = float(last_db["pe_ttm"])
            except (TypeError, ValueError):
                pass
        if last_db.get("pb") is not None:
            try:
                result["pb"] = float(last_db["pb"])
            except (TypeError, ValueError):
                pass
        if last_db.get("total_mv") is not None:
            try:
                result["total_mv"] = round(float(last_db["total_mv"]) / 1e4, 2)
            except (TypeError, ValueError):
                pass
        if last_db.get("circ_mv") is not None:
            try:
                result["circ_mv"] = round(float(last_db["circ_mv"]) / 1e4, 2)
            except (TypeError, ValueError):
                pass
        if last_db.get("turnover_rate") is not None:
            try:
                result["turnover_rate"] = float(last_db["turnover_rate"])
            except (TypeError, ValueError):
                pass
        if last_db.get("volume_ratio") is not None:
            try:
                result["volume_ratio"] = float(last_db["volume_ratio"])
            except (TypeError, ValueError):
                pass
    result["statuses"]["daily_basic"] = db_s

    # v5.6: 7 段并发改为 6 段并发 + daily_basic 串行, 大幅降低 Tushare 单接口频控撞墙
    # 如果 daily_basic 还返空 (db_s=EMPTY), watchlist 级 dump 跨票间隔会自然恢复, 不再单票内部 sleep
    if not db and db_s == "EMPTY":
        logger.warning("daily_basic 仍 EMPTY, 跨票间隔会自动恢复 (本票不阻塞)")

    # 4. money_flow
    if mf:
        fflow_rows = []
        for r in mf:
            sm_net  = (float(r.get("buy_sm_amount",  0) or 0) - float(r.get("sell_sm_amount",  0) or 0))
            md_net  = (float(r.get("buy_md_amount",  0) or 0) - float(r.get("sell_md_amount",  0) or 0))
            lg_net  = (float(r.get("buy_lg_amount",  0) or 0) - float(r.get("sell_lg_amount",  0) or 0))
            elg_net = (float(r.get("buy_elg_amount", 0) or 0) - float(r.get("sell_elg_amount", 0) or 0))
            fflow_rows.append({
                "trade_date": r.get("trade_date", ""),
                "main_net": lg_net + elg_net,
                "small": sm_net, "mid": md_net,
                "big": lg_net, "super_big": elg_net,
            })
        result["fflow"] = fflow_rows
        # v5.10.17: 存原始 moneyflow list (给 fflow verdict 复用, 0 重复拉取)
        result["moneyflow"] = mf
    result["statuses"]["fflow"] = mf_s


    # 7. weekly 周线 (2026-07-28 v5.5 加, 给 5方法×3周期 周线维度用)
    if wk:
        result["weekly"] = wk
        result["statuses"]["weekly"] = wk_s
    else:
        result["statuses"]["weekly"] = wk_s or "EMPTY"

    # ===== 从 raw 数据组合输出 =====
    # 7. EPS 表 (2026-07-22 修复: 优先 datacenter 机构一致预期, 失败 fallback tushare 自建)
    #   之前 v4.0 改造时砍了 datacenter 路径, 用 tushare fina_indicator 自建 NTM, 但那是 Q1 单季 EPS 推算
    #   导致 PEG 错算 (例: 阳光电源 PEG 0.98→8.09, 8x 偏差). 修.
    eps_table, eps_source = _build_eps_table(code)
    if eps_table:
        result["eps_table"] = eps_table
        result["eps_source"] = eps_source
        result["statuses"]["eps"] = "OK"
    else:
        result["statuses"]["eps"] = "EMPTY"

    # 8. total_share (从 daily_basic.total_mv / close 反推)
    if result.get("total_mv") and result.get("close"):
        try:
            result["total_share"] = round(
                (result["total_mv"] * 1e4) / result["close"] / 1e4, 4
            )  # 市值(万) / 价 / 1e4 = 亿股
            result["statuses"]["shares"] = "OK"
        except (TypeError, ValueError, ZeroDivisionError):
            result["statuses"]["shares"] = "EMPTY"
    else:
        result["statuses"]["shares"] = "EMPTY"

    # 9. price status (复用 daily_basic)
    result["statuses"]["price"] = "OK" if result.get("close") else "EMPTY"

    return result


def fetch_from_local(code: str, kline_days: int = 1250) -> dict:
    """从本地历史库 + 缓存组装 raw dict，0 网络调用。

    依赖:
      - data/history/daily/*.parquet  (由 history_sync 维护)
      - data/cache/daily_basic.json   (由 static_cache 维护)
      - data/cache/stock_basic.json   (由 static_cache 维护)
      - data/history/eps/{code}.parquet   (v6.2.4, 由 static_cache 维护)

    返回格式与 fetch_all 一致，供 老 data 工具 无缝替换。
    """
    from datetime import datetime

    result = {
        "code": code,
        "name": "",
        "sector": "",
        "close": None,
        "pe_ttm": None,
        "pb": None,
        "total_mv": None,
        "circ_mv": None,
        "total_share": None,
        "turnover_rate": None,
        "volume_ratio": None,
        "industry": "",
        "list_date": "",
        "kline": [],
        "weekly": [],
        "moneyflow": [],
        "eps_table": [],
        "fflow": {},
        "statuses": {},
        "fetch_time": datetime.now().isoformat(),
        "_source": "local",
    }

    from ..store import read_kline
    from ..caches.eps import get_daily_basic, get_stock_basic, get_eps

    ts_code = code + ".SZ" if code.startswith(("0", "3")) else code + ".SH"

    # 1. K线（从 parquet 读，升序）
    bars_raw = read_kline(ts_code, limit=kline_days)
    if bars_raw:
        kline = []
        for r in bars_raw:
            try:
                kline.append({
                    "trade_date": str(r.get("trade_date", "")),
                    "open":    float(r.get("open",    0) or 0),
                    "close":   float(r.get("close",   0) or 0),
                    "high":    float(r.get("high",    0) or 0),
                    "low":     float(r.get("low",     0) or 0),
                    "volume":  float(r.get("vol",     r.get("volume", 0)) or 0),
                    "amount":  float(r.get("amount",  0) or 0),
                    "pct_chg": float(r.get("pct_chg", 0) or 0),
                })
            except (TypeError, ValueError):
                continue
        result["kline"] = kline
        result["close"] = kline[-1]["close"] if kline else None
        result["statuses"]["kline"] = "OK"
    else:
        result["statuses"]["kline"] = "EMPTY"

    # 2. 周线（从日线合成，5根日线→1根周线）
    if result["kline"]:
        result["weekly"] = _synthesize_weekly(result["kline"])
        result["statuses"]["weekly"] = "OK" if result["weekly"] else "EMPTY"

    # 3. stock_basic（缓存）
    sb = get_stock_basic(code)
    if sb:
        result["name"]       = sb.get("name", "")
        result["industry"]   = sb.get("industry", "")
        result["list_date"]  = sb.get("list_date", "")
        result["total_share"]= sb.get("total_share", 0)
        result["statuses"]["stock_basic"] = "OK"
    else:
        result["statuses"]["stock_basic"] = "EMPTY"

    # 4. daily_basic（缓存）
    db = get_daily_basic(code)
    if db:
        result["pe_ttm"]        = db.get("pe_ttm")
        result["pb"]            = db.get("pb")
        result["total_mv"]      = db.get("total_mv")
        result["circ_mv"]       = db.get("circ_mv")
        result["turnover_rate"] = db.get("turnover_rate")
        result["volume_ratio"]  = db.get("volume_ratio")
        result["statuses"]["daily_basic"] = "OK"
    else:
        result["statuses"]["daily_basic"] = "EMPTY"

    # 5. EPS（缓存）
    eps = get_eps(code)
    result["eps_table"] = eps or []
    result["statuses"]["eps"] = "OK" if eps else "EMPTY"

    result["statuses"]["price"] = "OK" if result.get("close") else "EMPTY"
    return result


def _synthesize_weekly(kline: list[dict]) -> list[dict]:
    """从日线合成周线（自然周，周一开盘~周五收盘）。"""
    from datetime import datetime
    if not kline:
        return []

    weeks: dict[str, list] = {}
    for bar in kline:
        try:
            d = datetime.strptime(str(bar["trade_date"])[:8], "%Y%m%d")
        except ValueError:
            try:
                d = datetime.strptime(str(bar["trade_date"])[:10], "%Y-%m-%d")
            except ValueError:
                continue
        # ISO 周键：年-周号
        week_key = d.strftime("%G-%V")
        weeks.setdefault(week_key, []).append(bar)

    result = []
    for week_key in sorted(weeks):
        bars = weeks[week_key]
        result.append({
            "trade_date": bars[-1]["trade_date"],  # 周五（最后一个交易日）
            "open":    bars[0]["open"],
            "close":   bars[-1]["close"],
            "high":    max(b["high"] for b in bars),
            "low":     min(b["low"]  for b in bars),
            "volume":  sum(b.get("volume", b.get("vol", 0)) for b in bars),
            "amount":  sum(b.get("amount", 0) for b in bars),
            "pct_chg": round(
                (bars[-1]["close"] / bars[0]["open"] - 1) * 100, 2
            ) if bars[0]["open"] else 0,
        })
    return result


# ============================================================
# 状态展示
# ============================================================

def status_emoji(status: str) -> str:
    """状态 → emoji (用于报告)"""
    if status == "OK":
        return "✅"
    if status.startswith("NET_") or status == "ALL_FAILED":
        return "❌"
    if status == "TIMEOUT":
        return "⏱"
    if status == "EMPTY":
        return "📭"
    if status == "PARSE_FAIL":
        return "🔧"
    if status.startswith("HTTP_"):
        return "🚫"
    if status.startswith("CURL_ERR") or status.startswith("SSL"):
        return "🚫"
    if status.startswith("EXCEPTION"):
        return "❌"
    return "❓"


# ============================================================
# 6. 技术指标计算 (Wilder 标准公式)
# ============================================================

def compute_indicators(kline: list[dict]) -> dict:
    """
    基于 K 线计算 8 个技术指标 (Wilder 1978 标准公式)

    Args:
        kline: [{"date", "open", "close", "high", "low", "vol"}, ...]

    Returns:
        {
            "macd": {"DIF": float, "DEA": float, "BAR": float, "verdict": str},
            "rsi": {"rsi6": float, "rsi12": float, "rsi24": float, "verdict": str},
            "kdj": {"K": float, "D": float, "J": float, "verdict": str},
            "boll": {"mid": float, "upper": float, "lower": float, "verdict": str},
            "atr": {"atr14": float, "verdict": str},
            "vol_ma": {"vol_ma5": float, "vol_ma10": float, "vol_ratio": float, "verdict": str},
            "summary": str,  # 综合判定
        }

    参考: 东方财富 / 同花顺 / 通达信 标准实现
    """
    if not kline or len(kline) < 20:
        return {"error": "K线不足 (需 >= 20 条)"}

    # 2026-08-06 修复: dump 顶层 kline 用 'volume' 字段, 老代码用 'vol', 兼容两种
    def _v(bar):
        return bar.get("volume") if "volume" in bar else bar.get("vol", 0)

    closes = [bar["close"] for bar in kline]
    highs = [bar["high"] for bar in kline]
    lows = [bar["low"] for bar in kline]
    vols = [_v(bar) for bar in kline]
    n = len(closes)
    current = closes[-1]

    result = {}

    # ========== 1. MACD (12, 26, 9) ==========
    # 算完整 EMA12/EMA26 序列, 然后 DIF = EMA12 - EMA26, DEA = EMA9(DIF)
    ema12_series = _ema_series(closes, 12)
    ema26_series = _ema_series(closes, 26)
    # 对齐: EMA12 从第 12 个开始, EMA26 从第 26 个开始
    # 取后 min(len12, len26) 部分
    min_len = min(len(ema12_series), len(ema26_series))
    dif_series = [ema12_series[-min_len + i] - ema26_series[-min_len + i] for i in range(min_len)]
    if len(dif_series) >= 9:
        dea_series = _ema_series(dif_series, 9)
    else:
        dea_series = [sum(dif_series) / len(dif_series)] * len(dif_series) if dif_series else [0]
    dif = dif_series[-1] if dif_series else 0
    dea = dea_series[-1] if dea_series else 0
    bar = (dif - dea) * 2
    if dif > dea and dif > 0:
        macd_verdict = "🟢 金叉多头 (DIF>DEA>0)"
    elif dif > dea and dif < 0:
        macd_verdict = "🟡 金叉弱势 (DIF>DEA 但<0)"
    elif dif < dea and dif < 0:
        macd_verdict = "🔴 死叉空头 (DIF<DEA<0)"
    else:
        macd_verdict = "🟠 死叉强势 (DIF<DEA 但>0)"
    result["macd"] = {
        "DIF": round(dif, 4),
        "DEA": round(dea, 4),
        "BAR": round(bar, 4),
        "verdict": macd_verdict,
    }

    # ========== 2. RSI (6, 12, 24) ==========
    rsi6 = _rsi(closes, 6)
    rsi12 = _rsi(closes, 12)
    rsi24 = _rsi(closes, 24)
    rsi_verdict = _rsi_verdict(rsi6, rsi12, rsi24)
    result["rsi"] = {
        "rsi6": round(rsi6, 2),
        "rsi12": round(rsi12, 2),
        "rsi24": round(rsi24, 2),
        "verdict": rsi_verdict,
    }

    # ========== 3. KDJ (9, 3, 3) ==========
    k, d, j = _kdj(highs, lows, closes, n=9, m1=3, m2=3)
    kdj_verdict = _kdj_verdict(k, d, j)
    result["kdj"] = {
        "K": round(k, 2),
        "D": round(d, 2),
        "J": round(j, 2),
        "verdict": kdj_verdict,
    }

    # ========== 4. BOLL (20, 2) ==========
    if n >= 20:
        mid20 = sum(closes[-20:]) / 20
        # 总体标准差
        variance = sum((c - mid20) ** 2 for c in closes[-20:]) / 20
        std20 = variance ** 0.5
        upper = mid20 + 2 * std20
        lower = mid20 - 2 * std20
        if current > upper:
            boll_verdict = "🟠 突破上轨 (强势但超买)"
        elif current < lower:
            boll_verdict = "🟢 跌破下轨 (弱势可能反弹)"
        elif current > mid20:
            boll_verdict = "🟡 中轨上方 (偏多)"
        else:
            boll_verdict = "⚪ 中轨下方 (偏空)"
        result["boll"] = {
            "mid": round(mid20, 2),
            "upper": round(upper, 2),
            "lower": round(lower, 2),
            "width": round((upper - lower) / mid20 * 100, 2),  # 带宽 %
            "verdict": boll_verdict,
        }
    else:
        result["boll"] = {"error": "K线 < 20"}

    # ========== 5. ATR (14) ==========
    if n >= 15:
        atr14 = _atr(highs, lows, closes, 14)
        # ATR 占价格比 (波动率)
        atr_pct = atr14 / current * 100
        if atr_pct > 5:
            atr_verdict = f"🔴 高波动 ({atr_pct:.1f}%)"
        elif atr_pct > 3:
            atr_verdict = f"🟠 中波动 ({atr_pct:.1f}%)"
        else:
            atr_verdict = f"✅ 低波动 ({atr_pct:.1f}%)"
        result["atr"] = {
            "atr14": round(atr14, 2),
            "atr_pct": round(atr_pct, 2),
            "verdict": atr_verdict,
        }
    else:
        result["atr"] = {"error": "K线 < 15"}

    # ========== 6. 量比 (vol_ratio) ==========
    if n >= 6:
        vol_ma5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 0
        vol_ratio = vols[-1] / vol_ma5 if vol_ma5 > 0 else 0
        if vol_ratio > 2:
            vol_verdict = f"🟠 放量 ({vol_ratio:.2f}x) — 关注"
        elif vol_ratio > 1.2:
            vol_verdict = f"🟡 温和放量 ({vol_ratio:.2f}x)"
        elif vol_ratio < 0.7:
            vol_verdict = f"🟢 缩量 ({vol_ratio:.2f}x)"
        else:
            vol_verdict = f"⚪ 正常 ({vol_ratio:.2f}x)"
        result["vol_ma"] = {
            "vol_ma5": round(vol_ma5, 0),
            "vol_today": round(vols[-1], 0),
            "vol_ratio": round(vol_ratio, 2),
            "verdict": vol_verdict,
        }
    else:
        result["vol_ma"] = {"error": "K线 < 6"}

    # ========== 7. 综合判定 ==========
    signals = []
    if "macd" in result and "verdict" in result["macd"]:
        signals.append(result["macd"]["verdict"])
    if "rsi" in result and "verdict" in result["rsi"]:
        signals.append(result["rsi"]["verdict"])
    if "kdj" in result and "verdict" in result["kdj"]:
        signals.append(result["kdj"]["verdict"])
    if "boll" in result and "verdict" in result["boll"]:
        signals.append(result["boll"]["verdict"])

    bullish = sum(1 for s in signals if any(x in s for x in ["🟢", "🟡金叉多头", "买入", "偏低", "缩量"]))
    bearish = sum(1 for s in signals if any(x in s for x in ["🔴", "死叉空头", "卖出", "超买", "放量"]))
    if bullish >= 3:
        summary = f"🟢 综合偏多 ({bullish}/{len(signals)} 看多)"
    elif bearish >= 3:
        summary = f"🔴 综合偏空 ({bearish}/{len(signals)} 看空)"
    else:
        summary = f"🟡 震荡 ({bullish}多 / {bearish}空)"

    result["summary"] = summary
    return result


# ============================================================
# 指标计算子函数 (Wilder 标准)
# ============================================================

def _ema(data: list[float], period: int) -> float:
    """指数移动平均 (最后一个值)"""
    if len(data) < period:
        return sum(data) / len(data) if data else 0
    k = 2 / (period + 1)
    ema = sum(data[:period]) / period  # 初始 SMA
    for price in data[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def _ema_series(data: list[float], period: int) -> list[float]:
    """EMA 序列"""
    if not data:
        return []
    if len(data) < period:
        return [sum(data) / len(data)] * len(data)
    k = 2 / (period + 1)
    ema = sum(data[:period]) / period
    series = [ema]
    for price in data[period:]:
        ema = price * k + ema * (1 - k)
        series.append(ema)
    return series


def _rsi(closes: list[float], period: int = 14) -> float:
    """Wilder RSI"""
    if len(closes) < period + 1:
        return 50.0  # 数据不足时返回中性
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    # 初始均值
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder 平滑
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _kdj(highs: list[float], lows: list[float], closes: list[float],
         n: int = 9, m1: int = 3, m2: int = 3) -> tuple[float, float, float]:
    """KDJ 指标 (n=9, m1=3, m2=3 标准)"""
    if len(closes) < n:
        return 50.0, 50.0, 50.0
    # 最近 n 日的 H/L/C
    h_n = max(highs[-n:])
    l_n = min(lows[-n:])
    rsv = (closes[-1] - l_n) / (h_n - l_n) * 100 if h_n > l_n else 50.0

    # K = (m1-1)/m1 * K_prev + 1/m1 * RSV (K 初始 50)
    # D = (m2-1)/m2 * D_prev + 1/m2 * K
    # 简化为: 直接从 RSV 算
    K = rsv  # 简化: 第一个 K = RSV
    D = rsv
    for i in range(len(closes) - n - 1, 0, -1):
        if i < 0:
            break
        h_n = max(highs[max(0, i - n + 1):i + 1])
        l_n = min(lows[max(0, i - n + 1):i + 1])
        if h_n > l_n:
            rsv_i = (closes[i] - l_n) / (h_n - l_n) * 100
        else:
            rsv_i = 50
        K = (m1 - 1) / m1 * K + 1 / m1 * rsv_i
        D = (m2 - 1) / m2 * D + 1 / m2 * K

    J = 3 * K - 2 * D
    return K, D, J


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Wilder ATR"""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # 初始 ATR
    atr = sum(trs[:period]) / period
    # Wilder 平滑
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def _rsi_verdict(rsi6: float, rsi12: float, rsi24: float) -> str:
    """RSI 综合判定"""
    avg = (rsi6 + rsi12 + rsi24) / 3
    if rsi6 > 80 and rsi12 > 75:
        return f"🔴 严重超买 (RSI6={rsi6:.0f})"
    if rsi6 > 70:
        return f"🟠 超买 (RSI6={rsi6:.0f})"
    if rsi6 < 20 and rsi12 < 25:
        return f"🟢 严重超卖 (RSI6={rsi6:.0f}) — 反弹机会"
    if rsi6 < 30:
        return f"🟢 超卖 (RSI6={rsi6:.0f})"
    if 40 < avg < 60:
        return f"⚪ 中性 (RSI均值={avg:.0f})"
    return f"🟡 正常 (RSI6={rsi6:.0f}, 12={rsi12:.0f})"


def _kdj_verdict(k: float, d: float, j: float) -> str:
    """KDJ 综合判定"""
    if j < 0:
        return f"🟢 J<0 超卖 (K={k:.0f}, D={d:.0f}, J={j:.0f})"
    if j > 100:
        return f"🔴 J>100 超买 (K={k:.0f}, D={d:.0f}, J={j:.0f})"
    if k > d and k < 30:
        return f"🟢 低位金叉 (K={k:.0f} > D={d:.0f})"
    if k < d and k > 70:
        return f"🔴 高位死叉 (K={k:.0f} < D={d:.0f})"
    if k > d:
        return f"🟡 金叉 (K={k:.0f} > D={d:.0f})"
    return f"🟠 死叉 (K={k:.0f} < D={d:.0f})"


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    test_code = sys.argv[1] if len(sys.argv) > 1 else "002371"
    print(f"\n=== Testing fetcher for {test_code} ===\n")

    result = fetch_all(test_code)
    print(f"代码: {result['code']}  名称: {result['name']}")
    print(f"实时价: {result['close']}  PE_TTM: {result['pe_ttm']}")
    print(f"数据状态:")
    for k, v in result["statuses"].items():
        print(f"  {k}: [{status_emoji(v)} {v}]")
    if result["kline"]:
        print(f"K线: {len(result['kline'])} 条, 最新 close={result['kline'][-1]['close']}")
    if result["fflow"]:
        print(f"fflow: {len(result['fflow'])} 日, 5日净额={sum(d['main_net'] for d in result['fflow'][-5:]) / 1e4:.2f} 亿")
    if result["eps_table"]:
        print(f"EPS: {len(result['eps_table'])} 条")
        for e in result["eps_table"][:3]:
            print(f"  {e['year']}: EPS={e['eps']:.2f}  净利={e['net_profit_yi']:.1f}亿")
