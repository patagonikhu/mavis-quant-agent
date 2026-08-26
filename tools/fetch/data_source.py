"""
data_source.py — 单一权威数据源入口 (2026-07-24 固化)

设计原则:
  1. 所有 fetch 走本文件, 不在 tools/*.py 里直接调 Tushare / curl
  2. 数据源声明在 data/sources.yaml (git tracked), 改源改 yaml
  3. 统一返回 (data, status) tuple, status 跟 data_fetcher.DataStatus 对齐
  4. 禁用源 (push2/ifzq/qtimg) 在 yaml 里明列, 调用前自动校验
  5. 所有 fetch 记录到 data/fetch_log.jsonl, 跑 stats 看稳定性

使用方式:
    from tools.fetch.data_source import fetch_realtime, fetch_kline_daily, fetch_fund_flow
    data, status = fetch_realtime("300274")
    if status == "OK":
        print(data["price"])
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Optional

import yaml

# ============================================================
# 配置加载
# ============================================================

# 2026-07-31: 修路径 bug, .parent.parent 只到 tools/, 不到项目根
# (跟 tools/valuation/multi.py 之前修的 _load_config 路径 bug 同源)
PROJECT_ROOT = Path(__file__).parent.parent.parent
SOURCES_YAML = PROJECT_ROOT / "data" / "sources.yaml"
FETCH_LOG = PROJECT_ROOT / "data" / "fetch_log.jsonl"

_SOURCES_CONFIG: dict | None = None


def _load_config() -> dict:
    global _SOURCES_CONFIG
    if _SOURCES_CONFIG is None:
        with open(SOURCES_YAML, encoding="utf-8") as f:
            _SOURCES_CONFIG = yaml.safe_load(f)
    return _SOURCES_CONFIG


def _is_banned(source: str) -> bool:
    """检查源是否在禁用列表"""
    cfg = _load_config()
    for b in cfg.get("banned", []):
        if b in source:
            raise ValueError(
                f"❌ 拒绝使用禁用数据源: {source}\n"
                f"   原因: {b} 已被实测 WAF 拦截/已废弃, 改用 data/sources.yaml 中声明的 primary 源\n"
                f"   详见 CLAUDE.md 数据抓取规则"
            )
    return False


def _log_fetch(source: str, code: str, status: str, ms: int, error: str = ""):
    """记录 fetch 到 log (后续可看稳定性)"""
    try:
        with open(FETCH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.time(),
                "source": source,
                "code": code,
                "status": status,
                "ms": ms,
                "error": error[:200] if error else "",
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass  # log 失败不影响主流程


# ============================================================
# Source Registry (2026-07-24 固化)
# 改 data/sources.yaml 即可换源, 不用改代码
# ============================================================

class _TushareAdapter:
    """通用 Tushare 适配器: 调 tushare_fetcher 的具体函数"""
    def __init__(self, func_name: str, **default_kwargs):
        from tools import tushare_fetcher as _ts
        self._func = getattr(_ts, func_name, None)
        self._func_name = func_name
        self._default_kwargs = default_kwargs

    def fetch(self, code: str) -> tuple[Any, str]:
        if not self._func:
            return None, f"FUNC_MISSING:{self._func_name}"
        try:
            return self._func(code, **self._default_kwargs)
        except Exception as e:
            return None, f"EXC_{type(e).__name__}:{e}"


class _Sina60mAdapter:
    pass  # 已废弃，保留占位避免 import 报错


# Registry: source_name → adapter (类), 每个 source 是统一的 fetch(code) 接口
# 2026-07-24 升级: yml 写 source name, 代码里 name → adapter 映射
# 2026-07-28 v5.5 改: 改函数式 (按需构建), 所有 limit 走 config/project.yaml:data.* 段
def _build_source_registry() -> dict[str, Any]:
    """按 config.project.yaml 动态构建 SOURCE_REGISTRY

    改 limit 改 yaml 即可, 不动代码
    """
    cfg = _load_project_config()
    data = cfg.get("data", {})
    return {
        "Tushare.daily":          _TushareAdapter("get_daily", limit=data.get("kline_days", 250)),
        "Tushare.daily_basic":    _TushareAdapter("get_daily_basic", limit=1),
        "Tushare.weekly":         _TushareAdapter("get_weekly", limit=data.get("weekly_limit", 250)),
        "Tushare.moneyflow":      _TushareAdapter("get_money_flow", limit=data.get("fflow_days", 10)),
        "Tushare.stock_basic":    _TushareAdapter("get_stock_basic"),
        "Tushare.fina_indicator": _TushareAdapter("get_fina_indicator"),
        "Tushare.index_daily":    None,  # 特殊: 用 _safe_call, 在 fetch_index_quote 里手写
        "Tushare.pro_bar":        None,  # 暂不开放 (需要 freq/asset 参数)
        "Tushare.stk_mins":       None,  # 2026-07-24 决定不用, 单接口频控 4s/次 (57 只 watchlist × 4s = 228s)
    }


def _load_project_config() -> dict:
    """从 config/project.yaml 读配置 (单一来源)"""
    import yaml as _yaml
    config_path = PROJECT_ROOT / "config" / "project.yaml"
    with open(config_path, encoding="utf-8") as f:
        return _yaml.safe_load(f)


def get_source(name: str):
    """从 SOURCE_REGISTRY 拿 source 适配器 (yml 写 name, 代码拿 adapter)"""
    registry = _build_source_registry()
    if name not in registry:
        raise KeyError(f"未知 source: {name}, 已在 SOURCE_REGISTRY 注册的有: {list(registry.keys())}")
    return registry[name]


def get_sources_for(data_name: str) -> list:
    """从 yml 拿 data_name 的 [primary, fallback, ...] 适配器列表

    用法:
        sources = get_sources_for("kline_daily")
        for src in sources:
            data, status = src.fetch(code)
            if status == "OK":
                return data, "OK"
    """
    cfg = _load_config()
    section = cfg.get(data_name)
    if not section:
        raise KeyError(f"data/sources.yaml 缺 {data_name} 段")
    primary = section["primary"]
    fallbacks = section.get("fallback") or []
    if isinstance(fallbacks, str):
        fallbacks = [fallbacks]
    sources = []
    for name in [primary] + fallbacks:
        # 跳过 "—" 占位
        if name and name != "—":
            try:
                sources.append(get_source(name))
            except KeyError:
                pass
    return sources


def fetch_by_yaml(data_name: str, code: str) -> tuple[Any, str]:
    """完全动态: 读 yml 选 source, 按顺序尝试 primary + fallback
    返回第一个 OK 的结果

    优势: 改 data/sources.yaml 即可换源, 不用改 data_source.py
    """
    sources = get_sources_for(data_name)
    if not sources:
        return None, f"NO_SOURCE_FOR:{data_name}"
    last_status = "NO_SOURCE"
    for src in sources:
        data, status = src.fetch(code)
        if status in ("OK", "OK_CACHED", "OK_FALLBACK") and data:
            return data, "OK"
        last_status = status
    return None, last_status


# ============================================================
# 公开 fetch 入口 (按 data/sources.yaml 声明)
# ============================================================

def fetch_realtime(code: str) -> tuple[dict | None, str]:
    """实时价 + PE/PB (Tushare.daily_basic)"""
    _is_banned("Tushare.daily_basic")  # 反向校验
    from tools.fetch.tushare_fetcher import get_daily_basic
    t0 = time.time()
    try:
        rows, status = get_daily_basic(code, limit=1)
        ms = int((time.time() - t0) * 1000)
        if not rows or "error" in str(status).lower():
            _log_fetch("Tushare.daily_basic", code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        row = rows[0]
        data = {
            "code": code,
            "price": float(row.get("close", 0) or 0),
            "pe_ttm": float(row.get("pe_ttm", 0) or 0),
            "pb": float(row.get("pb", 0) or 0),
            "turnover_rate": float(row.get("turnover_rate", 0) or 0),
            # total_mv / circ_mv 单位是"万元" → 转"亿元"
            "total_mv": float(row.get("total_mv", 0) or 0) / 1e4,
            "circ_mv": float(row.get("circ_mv", 0) or 0) / 1e4,
        }
        _log_fetch("Tushare.daily_basic", code, "OK", ms)
        return data, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.daily_basic", code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


def fetch_kline_daily(code: str, days: int = 250) -> tuple[list[dict] | None, str]:
    """日 K 线 (Tushare.daily)"""
    from tools.fetch.tushare_fetcher import get_daily
    t0 = time.time()
    try:
        rows, status = get_daily(code, limit=days)
        ms = int((time.time() - t0) * 1000)
        if not rows:
            _log_fetch("Tushare.daily", code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        bars = []
        for r in rows:
            bars.append({
                "date": r.get("trade_date", ""),
                "open": float(r.get("open", 0) or 0),
                "high": float(r.get("high", 0) or 0),
                "low": float(r.get("low", 0) or 0),
                "close": float(r.get("close", 0) or 0),
                "vol": float(r.get("vol", 0) or 0),
                "amount": float(r.get("amount", 0) or 0),
            })
        _log_fetch("Tushare.daily", code, "OK", ms)
        return bars, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.daily", code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


def fetch_kline_weekly(code: str, days: int = 0) -> tuple[list[dict] | None, str]:  # v5.5: days=0 自动走 config.weekly_limit
    """周 K 线 (Tushare.weekly, days=0 时走 config.project.yaml:data.weekly_limit)"""
    from tools.fetch.tushare_fetcher import get_weekly
    t0 = time.time()
    try:
        # 2026-07-28 v5.5: days=0 自动走 config (避免硬编码)
        if days == 0:
            cfg = _load_project_config()
            days = cfg.get("data", {}).get("weekly_limit", 250)
        rows, status = get_weekly(code, limit=days)
        ms = int((time.time() - t0) * 1000)
        if not rows:
            _log_fetch("Tushare.weekly", code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        bars = []
        for r in rows:
            bars.append({
                "date": r.get("trade_date", ""),
                "close": float(r.get("close", 0) or 0),
                "high": float(r.get("high", 0) or 0),
                "low": float(r.get("low", 0) or 0),
            })
        _log_fetch("Tushare.weekly", code, "OK", ms)
        return bars, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.weekly", code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


def fetch_fund_flow(code: str, days: int = 10) -> tuple[list[dict] | None, str]:
    """
    主力资金 (Tushare.moneyflow, 单位 = 万元)
    ⚠️ 2026-07-24 修复: 之前误认为亿元, 数值错 1 万倍
    返回: list[dict] 字段 date / main_net(万) / small(万) / mid(万) / big(万) / super_big(万)
    """
    from tools.fetch.tushare_fetcher import get_money_flow as _tff
    t0 = time.time()
    try:
        rows, status = _tff(code, limit=days)
        ms = int((time.time() - t0) * 1000)
        if not rows:
            _log_fetch("Tushare.moneyflow", code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        result = []
        for r in rows:
            # 2026-07-24 修复: Tushare.moneyflow 字段映射
            # sm=小单, md=中单, lg=大单, elg=超大单 (注意 lg != elg)
            result.append({
                "date": r.get("trade_date", ""),
                "main_net": float(r.get("net_mf_amount", 0) or 0),  # 万元
                "small": float(r.get("buy_sm_amount", 0) or 0) - float(r.get("sell_sm_amount", 0) or 0),
                "mid": float(r.get("buy_md_amount", 0) or 0) - float(r.get("sell_md_amount", 0) or 0),
                "big": float(r.get("buy_lg_amount", 0) or 0) - float(r.get("sell_lg_amount", 0) or 0),
                "super_big": float(r.get("buy_elg_amount", 0) or 0) - float(r.get("sell_elg_amount", 0) or 0),
                "unit": "万元",
            })
        _log_fetch("Tushare.moneyflow", code, "OK", ms)
        return result, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.moneyflow", code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


def fetch_stock_info(code: str) -> tuple[dict | None, str]:
    """股票基础信息: 名称/行业/上市日期/总股本/流通股本
    Tushare.stock_basic (替代 qtimg 解析)
    """
    from tools.fetch.tushare_fetcher import get_stock_basic
    t0 = time.time()
    try:
        row, status = get_stock_basic(code)
        ms = int((time.time() - t0) * 1000)
        if not row:
            _log_fetch("Tushare.stock_basic", code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        # total_share / float_share 单位是"万股" → 转"股"
        data = {
            "code": code,
            "name": row.get("name", ""),
            "industry": row.get("industry", ""),
            "list_date": row.get("list_date", ""),
            "total_share": float(row.get("total_share", 0) or 0) * 1e4,  # 万股 → 股
            "float_share": float(row.get("float_share", 0) or 0) * 1e4,
            "market": row.get("market", ""),
        }
        _log_fetch("Tushare.stock_basic", code, "OK", ms)
        return data, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.stock_basic", code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


def fetch_index_quote(ts_code: str) -> tuple[dict | None, str]:
    """指数实时价 (Tushare.index_daily, 替代 qtimg 拉指数)
    ts_code: '000001.SH' (上证), '000300.SH' (沪深300), '399006.SZ' (创业板)
    返回: {name, price, prev_close, change_pct} 或 None
    """
    from tools.fetch.tushare_fetcher import _safe_call
    t0 = time.time()
    try:
        rows, status = _safe_call("index_daily", ts_code=ts_code, limit=2)
        ms = int((time.time() - t0) * 1000)
        if not rows or len(rows) < 1:
            _log_fetch("Tushare.index_daily", ts_code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        cur = rows[0] if rows else {}
        prev = rows[1] if len(rows) > 1 else {}
        cur_close = float(cur.get("close", 0) or 0)
        prev_close = float(prev.get("close", 0) or cur.get("pre_close", 0) or 0)
        change_pct = ((cur_close / prev_close - 1) * 100) if prev_close else 0
        data = {
            "name": ts_code,
            "price": cur_close,
            "prev_close": prev_close,
            "change_pct": change_pct,
            "source": "Tushare.index_daily",
        }
        _log_fetch("Tushare.index_daily", ts_code, "OK", ms)
        return data, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.index_daily", ts_code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


def fetch_eps_table(code: str, years: int = 4) -> tuple[list[dict] | None, str]:
    """EPS / 财务指标 (Tushare.fina_indicator)"""
    from tools.fetch.tushare_fetcher import get_fina_indicator
    t0 = time.time()
    try:
        rows, status = get_fina_indicator(code)
        ms = int((time.time() - t0) * 1000)
        if not rows:
            _log_fetch("Tushare.fina_indicator", code, status or "EMPTY", ms)
            return None, status or "EMPTY"
        _log_fetch("Tushare.fina_indicator", code, "OK", ms)
        return rows, "OK"
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        _log_fetch("Tushare.fina_indicator", code, f"EXC_{type(e).__name__}", ms, str(e))
        return None, f"EXC_{type(e).__name__}"


# ============================================================
# 稳定性统计
# ============================================================

def fetch_stats() -> dict:
    """统计 fetch_log.jsonl 里的源稳定性"""
    if not FETCH_LOG.exists():
        return {"error": "无 fetch log, 跑一次 fetch 才有数据"}
    cfg = _load_config()
    log = []
    with open(FETCH_LOG, encoding="utf-8") as f:
        for line in f:
            try:
                log.append(json.loads(line))
            except Exception:
                continue
    by_source = {}
    for r in log:
        s = r.get("source", "UNKNOWN")
        if s not in by_source:
            by_source[s] = {"total": 0, "ok": 0, "ms_total": 0, "errors": {}}
        by_source[s]["total"] += 1
        if r.get("status") == "OK":
            by_source[s]["ok"] += 1
        else:
            err = r.get("status", "UNKNOWN")
            by_source[s]["errors"][err] = by_source[s]["errors"].get(err, 0) + 1
        by_source[s]["ms_total"] += r.get("ms", 0)
    return {
        s: {
            "total": v["total"],
            "ok_rate": f"{v['ok']/v['total']*100:.1f}%" if v["total"] else "0%",
            "avg_ms": int(v["ms_total"] / v["total"]) if v["total"] else 0,
            "errors": v["errors"],
        }
        for s, v in by_source.items()
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        import json as _j
        print(_j.dumps(fetch_stats(), ensure_ascii=False, indent=2))
    else:
        print(f"data_source.py v1.0, 配置: {SOURCES_YAML}")
        print(f"禁用源: {_load_config().get('banned', [])}")
