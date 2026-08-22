"""
dump_data.py — 数据生成 (Phase 1) 和报告渲染 (Phase 2) 解耦

问题: 之前我 (LLM) 手动搬运数据 → 格式改就丢
解决: 数据先 dump 到 JSON, 报告从 JSON 渲染, 数据不丢

用法:
  python3 tools/dump_data.py 300274           # 拉阳光电源数据
  python3 tools/dump_data.py 300274 --render  # 拉 + 渲染报告
输出:
  data/dump/{code}.json   # 完整数据
  docs/analyze-{code}-{name}.md  # 报告 (--render 时)
"""
import sys
import os
import json
import argparse
from pathlib import Path
import yaml

# === 项目级配置加载 (2026-07-27 集中管理, 无默认值) ===
def _load_config() -> dict:
    """从 config/project.yaml 加载, 没有就报错 (强制用户配置)

    首次 setup:
      手动创建 config/project.yaml (不在 git 里, 参考 git history 或 docs/AGENT_MEMORY.md)
      编辑 config/project.yaml (改 kline_days/GA 参数等)
    """
    config_path = Path(__file__).parent.parent / "config" / "project.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ 找不到 {config_path}\n"
            f"   首次使用请: 手动创建 config/project.yaml (不在 git 里, 参考 git history 或 docs/AGENT_MEMORY.md)\n"
            f"   然后编辑 project.yaml 填你的配置"
        )
    with open(config_path) as f:
        return yaml.safe_load(f)

_PROJECT_CFG = _load_config()

# ============================================================
# Dump 字段 Schema（唯一真源：字段名 / fetcher / 原始字段 / 单位）
# ============================================================
DUMP_SCHEMA = {
    # ── 标量字段 ──────────────────────────────────────────────
    "code":          {"fetcher": None,               "raw": None,             "unit": "str"},
    "name":          {"fetcher": "get_stock_basic",  "raw": "name",           "unit": "str"},
    "industry":      {"fetcher": "get_stock_basic",  "raw": "industry",       "unit": "str"},
    "list_date":     {"fetcher": "get_stock_basic",  "raw": "list_date",      "unit": "YYYYMMDD"},
    "close":         {"fetcher": "get_daily",        "raw": "close",          "unit": "元",    "note": "最新收盘价，旧名 current_price"},
    "pe_ttm":        {"fetcher": "get_daily_basic",  "raw": "pe_ttm",         "unit": "倍"},
    "pb":            {"fetcher": "get_daily_basic",  "raw": "pb",             "unit": "倍"},
    "total_mv":      {"fetcher": "get_daily_basic",  "raw": "total_mv",       "unit": "亿元",  "conv": "万元÷1e4", "note": "旧名 market_cap_yi"},
    "circ_mv":       {"fetcher": "get_daily_basic",  "raw": "circ_mv",        "unit": "亿元",  "conv": "万元÷1e4", "note": "旧名 circ_mv_yi"},
    "total_share":   {"fetcher": "get_daily_basic",  "raw": "total_share",    "unit": "亿股",  "conv": "万股÷1e4", "note": "旧名 shares_yi"},
    "turnover_rate": {"fetcher": "get_daily_basic",  "raw": "turnover_rate",  "unit": "%"},
    "volume_ratio":  {"fetcher": "get_daily_basic",  "raw": "volume_ratio",   "unit": "倍"},
    # ── K 线数组 ─────────────────────────────────────────────
    "kline": {
        "fetcher": "get_daily", "raw": "daily bars", "unit": "见子字段",
        "fields": {
            "trade_date": {"raw": "trade_date", "unit": "YYYYMMDD",  "note": "旧名 date"},
            "open":       {"raw": "open",        "unit": "元"},
            "close":      {"raw": "close",       "unit": "元"},
            "high":       {"raw": "high",        "unit": "元"},
            "low":        {"raw": "low",         "unit": "元"},
            "volume":     {"raw": "vol",         "unit": "手(100股)", "note": "旧名 vol"},
            "amount":     {"raw": "amount",      "unit": "千元"},
            "pct_chg":    {"raw": "pct_chg",     "unit": "%"},
        },
    },
    "weekly": {
        "fetcher": "get_weekly", "raw": "weekly bars", "unit": "见子字段",
        "fields": {
            "trade_date": {"raw": "trade_date", "unit": "YYYYMMDD", "note": "旧名 date"},
            "open":       {"raw": "open",        "unit": "元"},
            "close":      {"raw": "close",       "unit": "元"},
            "high":       {"raw": "high",        "unit": "元"},
            "low":        {"raw": "low",         "unit": "元"},
            "volume":     {"raw": "vol",         "unit": "手",  "note": "旧名 vol"},
            "amount":     {"raw": "amount",      "unit": "千元"},
            "pct_chg":    {"raw": "pct_chg",     "unit": "%"},
        },
    },
    "fflow": {
        "fetcher": "get_money_flow", "raw": "moneyflow", "unit": "万元",
        "fields": {
            "trade_date": {"raw": "trade_date"},
            "main_net":   {"raw": "buy_lg+buy_elg-sell_lg-sell_elg", "unit": "万元"},
            "small":      {"raw": "buy_sm-sell_sm",   "unit": "万元"},
            "mid":        {"raw": "buy_md-sell_md",   "unit": "万元"},
            "big":        {"raw": "buy_lg-sell_lg",   "unit": "万元"},
            "super_big":  {"raw": "buy_elg-sell_elg", "unit": "万元"},
        },
    },
    "eps_table":  {"fetcher": "datacenter RPT_HSF10_RESPREDICT", "raw": "EPS consensus", "unit": "见子字段"},
    "resonance":  {"fetcher": "resonance_3period",  "raw": "index klines", "unit": "dict"},
    "tushare":    {"fetcher": "multiple tushare APIs", "raw": "multiple",  "unit": "nested dict"},
}

# 自动检查依赖 (2026-07-24 固化, 解决 "No module named tushare" 反复忘装问题)
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from check_deps import ensure as _ensure_deps
    _ensure_deps(verbose=False)
except Exception:
    pass
from pathlib import Path
from datetime import datetime


def _fetch_tushare_forecast(code: str) -> list:
    """拉取业绩预告 (2000 积分档可用)

    Returns:
        业绩预告列表 (按 ann_date 倒序, 最多 4 条) 或 None (失败)
    """
    try:
        from tools.fetch.tushare_fetcher import get_forecast
        data, status = get_forecast(code, recent_n=4)
        return data if data else []
    except Exception as e:
        return []


def _fetch_tushare_extended(code: str, weekly: list = None) -> dict:
    """拉取 weekly/north_flow/margin/top_list/dividend/fina_rows
    (2026-07-28 砍 monthly: 5方法×3周期 = 周/日/60分, 无月线)
    并发化：6个独立请求 4 并发, weekly 复用 fetch_all 结果
    2026-07-28 v5.5: weekly limit 60→156→250 (跟 fetch_all / project.yaml 同步, 5方法×3周期 周线用)
    2026-07-28 v5.5: with 块包住 .result() (修 ThreadPoolExecutor 提前关闭 bug)
    2026-07-28 v5.5 fix: 接受 weekly 参数 (复用 fetch_all 的结果, 避免重复拉 weekly)
    """
    from concurrent.futures import ThreadPoolExecutor
    result = {}
    try:
        from tools.fetch.tushare_fetcher import (
            get_weekly, get_north_flow, get_margin,
            get_top_list, get_dividend, get_fina_indicator, _code_to_ts, _safe_call,
            _latest_trade_date,
        )
        from datetime import datetime, timedelta
        trade_date = _latest_trade_date()

        # 2026-07-28 v5.5: weekly_limit 强制走 config.project.yaml (硬编码已废, 读不到报错)
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

        # 2026-07-28 v5.5 修: weekly 优先复用 fetch_all 已拉的 (避免重复)
        # 2026-07-29 改: 注释里"限流"实际指 Tushare weekly 接口单接口频控 (单接口 100/分)
        # 实测: weekly 跟 4 路并发同时调会撞单接口频控, weekly 返 0 根 (空表跟真没数据混淆)
        # 修: weekly 优先用 fetch_all 已拉的, fallback 单独串行, 其他 4 段并发
        if weekly is not None:
            # 复用 fetch_all 的 weekly (避免重复拉 weekly)
            result["weekly"] = weekly
        else:
            # fallback: 单独串行调 (跟其他 4 段并发分离, 避开 weekly 频控撞墙)
            # 2026-07-28 修: 用 keyword limit=, 不要 positional (get_weekly 第 2 个参数是 start_date)
            wk, _ = get_weekly(code, limit=weekly_limit); result["weekly"] = wk or []

        # 2026-07-28 砍: monthly K 线分析不用, 浪费 Tushare 调用
        # 5方法×3周期 = 周/日/60分, 没月线维度
        # 之前 24 根 × 57 只 = 1368 次 Tushare monthly 调用, 纯浪费
        result["monthly"] = []

        # 其他 4 段并发 (不带 weekly, 跟 weekly 接口分开避免单接口频控撞墙)
        with ThreadPoolExecutor(max_workers=4) as ex:
            fut_nf = ex.submit(get_north_flow)
            fut_mg = ex.submit(get_margin, code)
            fut_tl = ex.submit(get_top_list, code, trade_date)
            fut_dv = ex.submit(get_dividend, code, 10)
            fut_fi = ex.submit(get_fina_indicator, code)

            nf, _ = fut_nf.result(); result["north_flow"]= nf or []
            mg, _ = fut_mg.result(); result["margin"]    = mg if isinstance(mg, list) else ([mg] if mg else [])
            tl, _ = fut_tl.result(); result["top_list"]  = tl or []
            dv, _ = fut_dv.result(); result["dividend"]  = dv or []
            fi, _ = fut_fi.result(); result["fina_rows"] = ([fi] if isinstance(fi, dict) else fi) or []

    except Exception as e:
        result["_extended_error"] = str(e)
    return result


def _normalize_weekly_for_top(tushare_weekly: list) -> list:
    """
    2026-07-29 v5.6 加: 把 tushare.weekly 格式 (trade_date/...) 转换顶层 weekly 格式 (date/...)
    跟 kline 字段对齐: date/open/close/high/low/vol/amount/pct_chg (8 字段)
    返回 [] 当 tushare_weekly 为空
    """
    if not tushare_weekly:
        return []
    out = []
    for r in tushare_weekly:
        try:
            out.append({
                "trade_date": r.get("trade_date", ""),
                "open": float(r.get("open", 0) or 0),
                "close": float(r.get("close", 0) or 0),
                "high": float(r.get("high", 0) or 0),
                "low": float(r.get("low", 0) or 0),
                "volume": float(r.get("vol", 0) or 0),
                "amount": float(r.get("amount", 0) or 0),
                "pct_chg": float(r.get("pct_chg", 0) or 0),
            })
        except (TypeError, ValueError):
            continue
    return out


def _build_raw_only(code: str, raw: dict) -> dict:
    """
    2026-07-29 v5.6 加: 构造只含 raw 字段的 dict (Phase 1 拉数据完成后, 不跑分析)
    包含: code/name/as_of/close/pe_ttm/pb/total_mv/...
          kline/weekly/eps_table/tushare/fflow
    字段对齐完整 dump (除 analysis 相关: chan/factor/buy_sell_points/...
                                                three_layer_position/exit_signals/...
                                                stop_profit_loss/monitor_triggers/...
                                                peg/dcf/sector_overheat/five_categories)
    v5.10.16 修: daily_basic_long 从 raw 复用 (fetch_all 已拉 5296 行), 删 _fetch_daily_basic_long 死调
    v5.10.23 修: 删 daily_basic_long 字段 (0 consumer, 22 年历史 1253 KB/dump 是死字段)
    """
    eps_table = raw.get("eps_table") or []
    fflow = _get_fund_flow_raw(code, moneyflow_list=raw.get("moneyflow"))  # v5.10.17: 复用 raw, 0 重拉
    return {
        "code": code,
        "name": raw.get("name", ""),
        "as_of": _now_iso(),
        "close": raw.get("close"),
        "pe_ttm": raw.get("pe_ttm"),
        "pb": raw.get("pb"),
        "total_mv": raw.get("total_mv"),
        "circ_mv": raw.get("circ_mv"),
        "total_share": raw.get("total_share"),
        "turnover_rate": raw.get("turnover_rate"),
        "volume_ratio": raw.get("volume_ratio"),
        "industry": raw.get("industry", ""),
        "list_date": raw.get("list_date", ""),
        "kline": raw.get("kline") or [],
        "weekly": _normalize_weekly_for_top(raw.get("weekly") or []),
        "fflow": fflow,
        "eps_table": eps_table,
        "tushare": _build_tushare_section(code, raw, eps_table, fflow),
        "_section": "raw",  # 标记 Phase 1 完成, Phase 2 读时识别
    }


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


def _get_fund_flow_raw(code: str, moneyflow_list: list | None = None) -> dict:
    """
    v5.10.17 改: 接受 moneyflow_list (fetch_all 已拉的 moneyflow 60 天)
    - 不传: 内部独立调 get_fund_flow_combined (老 CLI 兼容)
    - 传 list: 复用 fetch_all 已拉数据, 0 重复拉取
    """
    try:
        from tools.fetch.tushare_fetcher import get_fund_flow_combined
        return get_fund_flow_combined(code, days=60, moneyflow_list=moneyflow_list)
    except Exception:
        return {"fflow_available": False, "verdict": "数据缺失", "data_columns": {"real": [], "derived": []}}


def _build_tushare_section(code: str, raw: dict, eps_table: list, fflow: dict) -> dict:
    """构造 tushare 段 (跟原 dump_data 末尾的 tushare 字段对齐)"""
    return {
        "stock_basic": {"ts_code": code, "name": raw.get("name", ""), "industry": raw.get("industry", ""), "list_date": raw.get("list_date", ""), "market": "A股"},
        "daily_basic": [{
            "trade_date": str(_now_iso()[:10]).replace("-", ""),
            "close": raw.get("close"),
            "pe_ttm": raw.get("pe_ttm"),
            "pb": raw.get("pb"),
            "total_mv": (raw.get("total_mv") or 0) * 1e4,
            "circ_mv": (raw.get("circ_mv") or 0) * 1e4,
            "turnover_rate": raw.get("turnover_rate"),
        }] if raw.get("close") else [],
        "fina_indicator": {
            "roe": eps_table[0].get("roe", 0) if eps_table else 0,
            "eps": eps_table[0].get("eps", 0) if eps_table else 0,
        } if eps_table else {},
        "money_flow": fflow.get("data_columns", {}).get("real", []),
        "forecast": _fetch_tushare_forecast(code),
        **_fetch_tushare_extended(code, weekly=raw.get("weekly")),
        "statuses": {
            "stock_basic": "OK" if raw.get("name") else "EMPTY",
            "daily_basic": "OK" if raw.get("close") else "EMPTY",
            "fflow": "OK" if fflow.get("fflow_available") else "EMPTY",
            "eps": "OK" if eps_table else "EMPTY",
            "fina_indicator": "OK" if eps_table else "EMPTY",
            "forecast": "OK" if _fetch_tushare_forecast(code) else "EMPTY",
        },
    }


def _fetch_tushare_safe(code: str, eps_table: list = None) -> dict:
    """安全拉 tushare 数据, 永不 raise

    失败时返回空 dict {statuses: all_error}, 不影响其他段
    成功时返回 9 段: stock_basic / daily_basic / weekly / monthly /
                   north_flow / margin / top_list / income / fina_indicator / dividend
    """
    try:
        from tools.fetch.tushare_fetcher import fetch_all_tushare
        result = fetch_all_tushare(code)
        # 顺手从 fina_indicator 更新 eps_table (tushare 的 ROE/EPS 比 datacenter 准)
        if eps_table is not None and result.get("fina_indicator"):
            fi = result["fina_indicator"]
            end = fi.get("end_date", "")
            if end and len(end) == 8:
                period = f"{end[:4]}A" if end[4:8] == "1231" else f"{end[:4]}Q{(int(end[4:6]) + 2) // 3}"
                roe = fi.get("roe")
                eps = fi.get("eps")
                if eps is not None and roe is not None:
                    # 替换或追加
                    found = False
                    for e in eps_table:
                        if e.get("period") == period:
                            e["eps"] = float(eps)
                            e["roe"] = float(roe)
                            found = True
                            break
                    if not found:
                        eps_table.append({
                            "period": period,
                            "eps": float(eps),
                            "roe": float(roe),
                        })
        return result
    except Exception as e:
        return {
            "ts_code": "",
            "statuses": {"_global": f"EXCEPTION_{type(e).__name__}: {e}"},
        }


def dump_code(code: str, pull_only: bool = False, analyze_only: bool = False) -> dict:
    """
    拉取单只股票的所有数据 + 跑分析, dump 到 JSON 结构

    2026-07-29 v5.6 加: 拆分 Phase 1 / Phase 2
      - pull_only=True: 只拉数据, 不跑分析, 不写 JSON
      - analyze_only=True: 读 data/dump/{code}.json, 跑分析, 写回
      - 默认 (False/False): pull + analyze 串行 (backward compat)
    """
    from tools.fetch.data_fetcher import fetch_all, fetch_from_local
    from tools.fetch.tushare_fetcher import get_fund_flow_combined

    # === Phase 1: 拉数据 ===
    if not analyze_only:
        from tools.history_sync import sync_incremental, read_kline

        # 幂等补缺失交易日：有缺口才拉，无缺口秒返回
        # 历史库未建档时 sync_incremental 会提示 --init，不报错
        sync_incremental()

        # 检查本地是否有足够K线（至少30根才有意义）
        ts_code = code + ".SZ" if code.startswith(("0", "3")) else code + ".SH"
        _local_bars = read_kline(ts_code, limit=30)
        if len(_local_bars) >= 30:
            raw = fetch_from_local(code, kline_days=_PROJECT_CFG["data"]["kline_days"])
        else:
            # 本地数据不足（未建档或历史太短）→ 走网络拉取兜底
            raw = fetch_all(code, kline_days=_PROJECT_CFG["data"]["kline_days"])
    else:
        # analyze_only: 从 disk 读 raw
        import json as _j
        with open(f"data/dump/{code}.json", encoding="utf-8") as _f:
            raw = _j.load(_f)

    if pull_only:
        # 只返回 raw 字段 (不跑分析)
        return _build_raw_only(code, raw)

    # 读 events.json (项目级)
    events = []
    try:
        import json as _json
        with open("data/events.json", "r", encoding="utf-8") as f:
            events_data = _json.load(f)
            events = events_data.get("events", [])
    except Exception:
        pass
    kd_day = raw.get("kline", [])


    # 1. fflow 组合
    fflow = get_fund_flow_combined(code, days=60, moneyflow_list=raw.get("moneyflow"))

    # 1.5 多市场共振 (需要网络拉指数K线，属于原始数据层，存入dump)
    resonance = {}
    try:
        from tools.factors.macro.resonance import resonance_3period as _resonance_fn
        res_3p = _resonance_fn(code, periods=(1, 5, 20))
        res_5d = res_3p.get(5, {})
        resonance = {
            "1d": res_3p.get(1, {}),
            "5d": res_5d,
            "20d": res_3p.get(20, {}),
        }
    except Exception:
        pass

    # 2.5 拉 EPS 表 (用于 PEG 计算)
    eps_table = raw.get("eps_table", [])
    if not eps_table:
        # 2026-07-22: data_fetcher 走 tushare, 这里兜底用 tushare fina_indicator 直接补
        try:
            from tools.fetch.tushare_fetcher import get_fina_indicator
            fi, _ = get_fina_indicator(code)
            if fi and fi.get("eps") is not None:
                end = fi.get("end_date", "")
                period = f"{end[:4]}A" if len(end) == 8 and end[4:6] == "12" else f"{end[:4]}Q1"
                eps_table = [{
                    "year": period,
                    "year_mark": "A" if "A" in period else "Q1",
                    "eps": float(fi.get("eps", 0) or 0),
                    "net_profit_yi": 0.0,
                    "revenue_yi": 0.0,
                    "roe": float(fi.get("roe", 0) or 0),
                }]
        except Exception:
            eps_table = []

    # 3. 实时价/EPS/技术指标 (来自 raw)
    return {
        "code": code,
        "name": raw.get("name", ""),
        "as_of": datetime.now().isoformat(),
        "close": raw.get("close"),
        "pe_ttm": raw.get("pe_ttm"),
        "pb": raw.get("pb"),
        "total_mv": raw.get("total_mv"),
        "circ_mv": raw.get("circ_mv"),
        "total_share": raw.get("total_share"),
        "turnover_rate": raw.get("turnover_rate"),
        "volume_ratio": raw.get("volume_ratio"),
        "industry": raw.get("industry", ""),
        "list_date": raw.get("list_date", ""),

        # K线 (原始数据，factor 由 AnalysisEngine 实时计算)
        "kline": kd_day[-250:] if kd_day else [],

        # fflow 组合方案
        "fflow": fflow,

        # 多市场共振 (指数K线，网络数据，存dump供 Engine 读)
        "resonance": resonance,

        # EPS / 财报
        "eps_table": raw.get("eps_table", []),

        # 2026-07-25 加: 60 分 K 线 (sina, 给 5 合 1 顶部预警)
        # v5.6 fix: analyze_only 模式从 raw 取 (避免重复拉 sina)
        # v5.10.9 修: 非 analyze_only 模式也直接从 raw 取 (fetch_all 并发 7 段已拉), 0 重复拉 Sina
        # 2026-07-29 v5.6 加: 顶层 weekly K 线 (从 raw 提, 之前只在 tushare.weekly 段)
        # 字段对齐 kline 格式 (date/open/close/high/low/vol/amount/pct_chg)
        "weekly": (raw.get("weekly") if analyze_only and raw.get("weekly") is not None else _normalize_weekly_for_top(raw.get("weekly") or [])),

        # v5.10.23 删: daily_basic_long (22 年历史 1253 KB/dump, 0 consumer, 死字段)
        # 之前 v5.6/v5.10.16: 写 dump 顶层 + fetch_all 串行拉, 0 consumer (TrendPullback 从 total_mv 顶层读)
        # 顶层 pe_ttm/pb/total_mv/... 仍存 (从 db[-1] 取, fetch_all 改 limit=1)

        # 2.4 tushare 段
        # schema 兼容 render_report: stock_basic/fina_indicator 是 dict, daily_basic 是 list
        # v5.6 fix: analyze_only 模式从 raw.tushare 直接用 (Phase 1 已写好), 不重新构造避免再调 Tushare
        "tushare": (raw.get("tushare") if analyze_only and raw.get("tushare") else {
            "stock_basic": {"ts_code": code, "name": raw.get("name", ""), "industry": raw.get("industry", ""), "list_date": raw.get("list_date", ""), "market": "A股"},
            "daily_basic": [{
                "trade_date": str(datetime.now().date()),
                "close": raw.get("close"),
                "pe_ttm": raw.get("pe_ttm"),
                "pb": raw.get("pb"),
                "total_mv": (raw.get("total_mv") or 0) * 1e4,  # 亿 → 万
                "circ_mv": (raw.get("circ_mv") or 0) * 1e4,
                "turnover_rate": raw.get("turnover_rate"),
            }] if raw.get("close") else [],
            "fina_indicator": {
                "roe": eps_table[0].get("roe", 0) if eps_table else 0,
                "eps": eps_table[0].get("eps", 0) if eps_table else 0,
            } if eps_table else {},
            "money_flow": fflow.get("data_columns", {}).get("real", []),
            "forecast": _fetch_tushare_forecast(code),
            # 扩展段: 幂等性要求存入 dump，不在 renderer 实时拉取
            **_fetch_tushare_extended(code, weekly=raw.get("weekly")),  # v5.5 fix: 复用 fetch_all weekly, 避免重复拉 weekly
            "statuses": {
                "stock_basic": "OK" if raw.get("name") else "EMPTY",
                "daily_basic": "OK" if raw.get("close") else "EMPTY",
                "fflow": "OK" if fflow.get("fflow_available") else "EMPTY",
                "eps": "OK" if eps_table else "EMPTY",
                "fina_indicator": "OK" if eps_table else "EMPTY",
                "forecast": "OK" if _fetch_tushare_forecast(code) else "EMPTY",
            },
        }),

        # 注: 其他数据 (PEG/DCF/四问/T 框架) 在 Phase 2 由 LLM 算
    }


def save_dump(data: dict, out_dir: str = None) -> str:
    """保存数据到 JSON (默认到 <project-root>/data/dump/, 与 ensure_fresh.py 一致)"""
    if out_dir is None:
        out_dir = str(Path(__file__).resolve().parent.parent / "data" / "dump")
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    code = data.get("code", "unknown")
    out_path = Path(out_dir) / f"{code}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return str(out_path)


def _derive_events_from_forecast(forecast: list, code: str, name: str) -> list:
    """从 Tushare.forecast 派生 T 框架事件 (2026-07-23, 替代手维护业绩事件)

    8 种 forecast.type 分两类:
      业绩兑现 (正): 预增/略增/扭亏/续盈
      业绩风险 (负): 预减/略减/首亏/续亏

    输出格式: 跟 events.json 一致, 方便 merge
    """
    events = []
    seen = set()  # 去重: (ann_date, end_date, type)
    for f in forecast or []:
        ftype = f.get("type", "")
        pmin = f.get("p_change_min")
        pmax = f.get("p_change_max")
        ann = f.get("ann_date", "")
        end = f.get("end_date", "")
        summary = str(f.get("summary", "") or "")[:60]

        # 去重 (同一 (ann_date, end_date, type) 跳过)
        dedup_key = (ann, end, ftype)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        if ftype in ("预增", "略增", "扭亏", "续盈"):
            impact = "正"
            event_type = "业绩兑现"
            conf = _PROJECT_CFG["forecast"]["default_confidence"]
        elif ftype in ("预减", "略减", "首亏", "续亏"):
            impact = "负"
            event_type = "业绩风险"
            conf = _PROJECT_CFG["forecast"]["default_confidence"]
        else:
            continue

        # p_change 描述
        if pmin is not None and pmax is not None:
            p_str = f"{float(pmin):+.0f}%~{float(pmax):+.0f}%"
        else:
            p_str = "未披露"

        # 报告期简化 (20260630 -> 2026中报)
        if end:
            if end[4:8] == "1231":
                period = f"{end[:4]}年报"
            elif end[4:8] == "0630":
                period = f"{end[:4]}中报"
            elif end[4:8] == "0331":
                period = f"{end[:4]}Q1"
            elif end[4:8] == "0930":
                period = f"{end[:4]}Q3"
            else:
                period = end
        else:
            period = "?"

        events.append({
            "code": code,
            "name": name,
            "sector": "",  # render 时从 watchlist 补
            "event_type": event_type,
            "event_date": ann,
            "description": f"{period} {ftype} {p_str} | {summary}",
            "impact": impact,
            "confidence": conf,
            "source": "Tushare.forecast 自动派生",
        })
    return events


def attach_events_from_forecast(data: dict) -> list:
    """从 dump 顶层 data["tushare"]["forecast"] 派生 events 段, 写到 data["events"]

    Returns: 派生的事件列表
    """
    code = data.get("code", "")
    name = data.get("name", "")
    forecast = (data.get("tushare") or {}).get("forecast") or []
    events = _derive_events_from_forecast(forecast, code, name)
    data["events"] = events
    return events


# ============================================================
# 数据源矩阵 (2026-07-22 固化)
# 收集每段数据的 (类型, 主源, 备源, 状态, 时间戳), 渲染时输出表格
# ============================================================

def collect_data_sources(data: dict) -> dict:
    """从已 dump 的 data 字典里, 提取每段数据的来源信息

    返回: {
        "as_of": "2026-07-22T08:40:24",  # dump 时间
        "sources": [
            {"section": "实时价", "type": "实时", "primary": "push2.eastmoney", "fallback": "qtimg", "status": "OK", "key": "close"},
            ...
        ],
        "summary": {"total": N, "ok": X, "empty": Y, "error": Z}
    }
    """
    from datetime import datetime
    sources = []

    # 1. 实时价
    sources.append({
        "section": "实时价",
        "type": "实时",
        "primary": "push2.eastmoney.com (f43 盘中/f60 前收)",
        "fallback": "qtimg (腾讯) — push2 WAF 时降级",
        "status": "OK" if data.get("close") else "EMPTY",
        "key": "close",
        "value": f"¥{data.get('close', 0):.2f}" if data.get("close") else "—",
    })

    # 2. 日 K 线
    kline = data.get("kline") or []
    sources.append({
        "section": "日 K 线",
        "type": "历史 (60 根)",
        "primary": "Tushare.daily (K线, 2000 积分档 24h 稳定)",
        "fallback": "— (ifzq 已被 WAF 拦截, 单一 Tushare)",
        "status": "OK" if len(kline) >= 30 else "EMPTY",
        "key": "kline",
        "value": f"{len(kline)} 根",
    })

    # 3. EPS 预测
    eps_table = data.get("eps_table") or []
    sources.append({
        "section": "EPS 预测",
        "type": "机构预测",
        "primary": "datacenter.eastmoney.com (RPT_HSF10_RESPREDICT)",
        "fallback": "— (无备源, 缺失则 PEG/DCFL 用 NTM 反推)",
        "status": "OK" if len(eps_table) >= 4 else "EMPTY",
        "key": "eps_table",
        "value": f"{len(eps_table)} 期",
    })

    # 4. 缠论分析
    chan = data.get("chan") or {}
    has_chan = bool(chan.get("daily") or chan.get("weekly"))
    sources.append({
        "section": "缠论二级别",
        "type": "本地算法",
        "primary": "tools/chan_analysis.analyze_three_levels",
        "fallback": "— (无备源, 算法稳定)",
        "status": "OK" if has_chan else "EMPTY",
        "key": "chan",
        "value": f"周/日" if has_chan else "—",
    })

    # 5. 主力分析 (fflow)
    fflow = data.get("fflow") or {}
    fflow_source = fflow.get("source", "—")
    sources.append({
        "section": "主力分析 (fflow)",
        "type": "实时 + 派生",
        "primary": "Tushare.money_flow API (2000 积分档, 24h 稳定, 10 日真实)",
        "fallback": "OBV 派生 (本地 K 线, 万手方向, 仅参考) — push2 系列已全部移除",
        "status": "OK" if fflow.get("success") else "EMPTY",
        "key": "fflow",
        "value": fflow_source,
    })

    # 6. PEG / DCF L
    peg = data.get("peg") or {}
    sources.append({
        "section": "PEG / DCF L",
        "type": "计算 (本地算法)",
        "primary": "_calc_peg + _calc_dcf (从 EPS 反推)",
        "fallback": "— (无备源, 算法一致)",
        "status": "OK" if peg.get("PEG_真实") else "EMPTY",
        "key": "peg",
        "value": f"PEG {peg.get('PEG_真实', '—')}",
    })

    # 7. 板块过热
    so = data.get("sector_overheat") or {}
    sources.append({
        "section": "板块过热",
        "type": "估算 (本地)",
        "primary": "从个股价格涨幅估算",
        "fallback": "Tushare 申万行业指数 (待接入)",
        "status": "OK" if so.get("verdict") else "EMPTY",
        "key": "sector_overheat",
        "value": so.get("verdict", "—"),
    })

    # 8. 5 类 14 子信号
    fc = data.get("five_categories") or {}
    sources.append({
        "section": "5 类 14 子信号",
        "type": "规则引擎 (本地)",
        "primary": "_calc_five_categories (fflow + EPS + 价)",
        "fallback": "—",
        "status": "OK" if fc.get("verdict") else "EMPTY",
        "key": "five_categories",
        "value": fc.get("verdict", "—"),
    })

    # 9. Tushare 12 段 (含 money_flow 真正的 fflow + forecast 业绩预告)
    ts = data.get("tushare") or {}
    ts_statuses = ts.get("statuses") or {}
    for seg_key, seg_name in [
        ("stock_basic", "Tushare.基础信息"),
        ("daily_basic", "Tushare.daily_basic (PE/PB/市值)"),
        ("weekly", "Tushare.周 K"),
        ("monthly", "Tushare.月 K"),
        ("north_flow", "Tushare.北向资金"),
        ("margin", "Tushare.融资融券"),
        ("top_list", "Tushare.龙虎榜"),
        ("income", "Tushare.利润表"),
        ("fina_indicator", "Tushare.财务指标"),
        ("dividend", "Tushare.分红"),
        ("money_flow", "Tushare.money_flow (真 fflow)"),
        ("forecast", "Tushare.forecast (业绩预告)"),
    ]:
        # 优先用 statuses 字段; 缺失则用 data["tushare"][seg_key] 是否有值判断
        st = ts_statuses.get(seg_key)
        if st is None:
            v = ts.get(seg_key)
            if v is None or (isinstance(v, (list, dict)) and len(v) == 0):
                st = "EMPTY"
            else:
                st = "OK"
        sources.append({
            "section": seg_name,
            "type": "机构数据 (Tushare Pro)",
            "primary": f"pro.{seg_key}",
            "fallback": "— (Tushare 2000 积分档唯一源)",
            "status": st,
            "key": f"tushare.{seg_key}",
            "value": "—",
        })

    # 汇总
    total = len(sources)
    ok = sum(1 for s in sources if s["status"] == "OK")
    empty = sum(1 for s in sources if s["status"] == "EMPTY")
    error = sum(1 for s in sources if s["status"] not in ("OK", "EMPTY"))
    summary = {"total": total, "ok": ok, "empty": empty, "error": error}

    return {
        "as_of": data.get("as_of", datetime.now().isoformat()),
        "sources": sources,
        "summary": summary,
    }


def attach_data_sources(json_path: str) -> dict:
    """给已存的 JSON dump 补 data_sources 字段, 写回

    返回: data_sources dict (供 main 打印)
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["data_sources"] = collect_data_sources(data)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    return data["data_sources"]


def main():
    """dump_data.py CLI 入口 (2026-07-29 C 方案 v1.0 thin wrapper)

    全部走 AgentData 类, 老 CLI 参数保持兼容:
      python -m tools.dump_data 002028              # 默认 max_age_min=60
      python -m tools.dump_data 002028 --render     # 拉 + 渲染
      python -m tools.dump_data 002028 --analyze-only  # 永不重拉
      python -m tools.dump_data 002028 --force      # 永远重拉
    """
    from tools.batch.agent_data import AgentData

    parser = argparse.ArgumentParser()
    parser.add_argument("code", help="股票代码 (如 300274)")
    parser.add_argument("--render", action="store_true", help="渲染报告 (Phase 2)")
    parser.add_argument("--analyze-only", action="store_true", help="只读 data/dump/{code}.json 跑分析+渲染, 不重拉数据")
    parser.add_argument("--force", action="store_true", help="强制重拉 (跳过缓存)")
    parser.add_argument("--age", type=int, default=60, help="max_age_min (默认 60)")
    parser.add_argument("--out", default=None, help="输出目录 (默认 <project-root>/data/dump/)")
    args = parser.parse_args()

    # CLI 参数 → max_age_min
    if args.analyze_only:
        max_age = 999999  # 永不重拉
    elif args.force:
        max_age = 0  # 永远重拉
    else:
        max_age = args.age  # 默认 60

    print(f"📥 拉数据: {args.code} (max_age_min={max_age})")
    ad = AgentData(args.code, max_age_min=max_age)
    print(f"  - {ad}")
    data = ad.raw()
    print(f"  - 价: ¥{data.get('close')}")
    print(f"  - K线: {len(data.get('kline', []))} 根")
    chan = data.get('chan') or {}
    print(f"  - 周线段: {len((chan.get('weekly') or {}).get('segs', []))}")
    print(f"  - 日线段: {len((chan.get('daily') or {}).get('segs', []))}")
    print(f"  - 日线中枢: {(chan.get('daily') or {}).get('hub', {}).get('valid', False)}")
    fflow = data.get('fflow') or {}
    print(f"  - fflow verdict: {fflow.get('verdict', 'N/A')}")
    # 注: data['factor'] (wyckoff/smc 算子原始输出) 字段历史上由 AnalysisEngine 实时算,
    #     dump_data 不写这个 key, 这里不 print 避免误导. factor 算子在 AnalysisEngine.analyze 里跑.

    out = save_dump(data, args.out)
    # 固化数据源矩阵到 dump (2026-07-22, 不丢)
    ds = attach_data_sources(out)
    # 从 forecast 派生 T 框架事件 (2026-07-23, 替代手维护业绩事件)
    n_events = attach_events_from_forecast(data)  # in-place, 写到 data["events"]
    if n_events:
        # 重写文件 (因为 attach_events_from_forecast 改了 data)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    n_sources = len(ds.get("sources", []))
    n_ok = ds.get("summary", {}).get("ok", 0)
    n_empty = ds.get("summary", {}).get("empty", 0)
    print(f"  - 数据源矩阵: {n_sources} 段 ({n_ok} OK / {n_empty} EMPTY)")
    print(f"✅ 数据已存: {out}")

    if args.render:
        print(f"\n🎨 渲染报告...")
        # 2026-07-29 C 方案: 渲染走 AgentData.render() 统一入口
        md = ad.render()
        name = data.get("name", "")
        report_path = Path(__file__).parent.parent / "docs" / f"analyze-{args.code}-{name}.md"
        print(f"✅ 报告已存: {report_path} ({len(md)} chars)")


if __name__ == "__main__":
    main()
