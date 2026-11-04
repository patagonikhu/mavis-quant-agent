"""
tools/batch/etf_macd_scan.py — ETF 场内基金 MACD 因子扫描 (v2, 2026-11-06)

输出:
  docs/etf-macd/<ts_code>-<name>.md   命中且同指数合并后的代表, 每只 1 个 md

v2 改动 (2026-11-06):
  ① **删掉 config/etf_whitelist.json 依赖** (18 只手工维护名单, 早已过期)
     → 默认候选池 = DataStore.get_etf_basic() 全市场 ETF + fund_type 过滤
       (口径跟 /t-etf-wyckoff-a2m 同源, 单一真源见 tools/batch/etf_universe.py)
  ② **两段式扫描** (全市场 1898 只可算; 逐只直接跑完整分析要 ~16 分钟):
     - 段 1 预筛: 批量读 K 线 (1 次 SQL, 0.8s) + compute_indicators 算 RSI6/MACD
       (0.0008s/只 → 全市场 1.5s) → 只留命中信号的
     - 段 2 深扫: 只对命中的跑 AnalysisEngine.analyze_history + 因子历史表
  ③ 因子历史回看数走真源 factor_history_lookback() (ETF 200 个交易日, 跟 /t-analyze 一致)
  ④ **命中口径** (两条任一):
       超卖     — RSI6 < 30
       金叉多头 — DIF > DEA 且 DIF > 0
     同一跟踪指数的 ETF **合并成一只** (口径跟 /t-etf-wyckoff-a2m 同源:
     归一化 benchmark 相同 或 60 日收益率相关性 ≥ 0.95), 每组留流动性最好的代表。
     实测: 200 只命中 → 60 个不同标的 (银行/红利/消费等同一指数各 4~6 只)。

用法:
  bash tools/with_venv.sh python -m tools.batch.etf_macd_scan
  ... --etfs 515790 159995              # 指定代码 (6位), 全量出 md (不设命中门槛)
  ... --lookback 200                    # 回看交易日数 (默认 0 = 用真源, ETF 200)
  ... --output-dir docs/etf-macd
  ... --no-summary
  ... --workers 4

0 网络: 走 DataStore (get_etf_basic / get_etf_klines)
跑前必须先 sync --etf 落盘
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import builtins as _b
_orig_print = _b.print
def _flushing_print(*a, **kw):
    kw['flush'] = True
    _orig_print(*a, **kw)
print = _flushing_print


# ============================================================
# 常量 (口径都写在这里, 别散落)
# ============================================================

# RSI6 < 30 = 超卖 (跟股票版 wyckoff_a2m_scan 的 rsi6_max=30 同档)
OVERSOLD_RSI6 = 30

# MACD(12,26,9) + RSI6 warmup: 26 + 9 = 35 根够算最新值
MIN_BARS = 35


# ============================================================
# 候选池 + 命中判定
# ============================================================

def _classify_signal(rsi6, dif, dea) -> str:
    """命中信号分类 (基于最新一根 K 线)

    Returns:
        '超卖'     — RSI6 < 30, 技术性低吸区
        '金叉多头' — DIF > DEA 且 DIF > 0, 趋势转多
        '—'        — 无命中
    """
    if rsi6 is None or dif is None or dea is None:
        return "—"
    if rsi6 < OVERSOLD_RSI6:
        return "超卖"
    if dif > dea and dif > 0:
        return "金叉多头"
    return "—"


def _latest_indicators(rows: list[dict]) -> dict | None:
    """最新一根 K 线的 RSI6 / DIF / DEA — 跟正式分析同一个 compute_indicators

    预筛和深扫必须用同一个指标函数 + 同一个 K 线窗口, 否则会出现
    "预筛说命中, 深扫算出来不是" 的口径漂移。
    """
    from tools.storage.sources.eastmoney import compute_indicators
    bars = [{"close": r.get("close"), "high": r.get("high"), "low": r.get("low"),
             "vol": r.get("vol"), "date": r.get("trade_date")} for r in rows]
    ind = compute_indicators(bars)
    if "error" in ind:
        return None
    return {"rsi6": ind["rsi"]["rsi6"],
            "macd_dif": ind["macd"]["DIF"],
            "macd_dea": ind["macd"]["DEA"]}


def _prefilter_signals(universe: list[dict],
                       history_days: int) -> tuple[list[dict], dict, dict]:
    """段 1 预筛: 全市场 → 命中信号清单

    Args:
        universe:    [{code, ts_code, name, benchmark, fund_type}, ...]
        history_days: 每只取最近多少根 K 线 (跟深扫同窗口)
    Returns:
        (hits, stats, klines) — klines 供段 1.5 同指数合并算流动性 / 相关性
    """
    from tools.batch.etf_universe import load_etf_klines

    stats = {"candidates": len(universe), "scored": 0, "no_data": 0,
             "oversold": 0, "golden_cross": 0}
    if not universe:
        return [], stats, {}

    klines = load_etf_klines([u["code"] for u in universe], limit=history_days)
    by_code = {u["code"]: u for u in universe}
    hits: list[dict] = []

    for ts_code, rows in klines.items():
        code = ts_code.split(".")[0]
        u = by_code.get(code)
        if not u or len(rows) < MIN_BARS:
            stats["no_data"] += 1
            continue
        stats["scored"] += 1
        ind = _latest_indicators(rows)
        if ind is None:
            stats["no_data"] += 1
            continue
        signal = _classify_signal(ind["rsi6"], ind["macd_dif"], ind["macd_dea"])
        if signal == "—":
            continue
        if signal == "超卖":
            stats["oversold"] += 1
        else:
            stats["golden_cross"] += 1
        hit = dict(u)
        hit.update(ind)
        hit["signal"] = signal
        hits.append(hit)

    # 排序: 超卖优先, 其次 RSI6 低的在前 (越超卖越值得先看)
    hits.sort(key=lambda h: (0 if h["signal"] == "超卖" else 1,
                              h["rsi6"] if h["rsi6"] is not None else 999))
    return hits, stats, klines


def _dedup_hits(hits: list[dict], klines: dict[str, list[dict]],
                basic_by_ts: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """同一跟踪指数的 ETF 合并成一只 → (代表列表, 合并明细)

    分组口径在 tools/batch/etf_universe.py (跟 /t-etf-wyckoff-a2m 共用):
      ① 归一化 benchmark 相同 → 同指数
      ② 60 日收益率相关性 ≥ 0.95 → 同一指数的不同写法 / 不同基金类型

    代表选择: 20 日日均成交额最大 (流动性最好, 最可投); 成交额相同 → K 线根数多者优先。
    """
    from tools.batch.etf_universe import (group_by_tracked_index, liquidity_score,
                                          index_display, normalize_benchmark)

    if not hits:
        return [], []

    by_code = {h["ts_code"]: h for h in hits}
    code_groups, _pair_corr = group_by_tracked_index(
        [h["ts_code"] for h in hits], klines, basic_by_ts)

    reps: list[dict] = []
    details: list[dict] = []
    for members in code_groups.values():
        members = [by_code[c] for c in members]

        def _rank(h: dict) -> tuple:
            return (liquidity_score(klines.get(h["ts_code"], [])),
                    len(klines.get(h["ts_code"], [])))

        rep = max(members, key=_rank)
        row = basic_by_ts.get(rep["ts_code"], {})
        index_name = index_display(row.get("benchmark"))
        rep2 = dict(rep)
        rep2["index_name"] = index_name
        rep2["group_key"] = normalize_benchmark(row.get("benchmark")) or index_name
        rep2["group_size"] = len(members)
        rep2["group_peers"] = [m["code"] for m in members if m["code"] != rep["code"]]
        rep2["liquidity"] = liquidity_score(klines.get(rep["ts_code"], []))
        reps.append(rep2)

        details.append({
            "index_name": index_name,
            "members": len(members),
            "rep_code": rep["code"],
            "rep_name": rep.get("name", ""),
            "peers": [m["code"] for m in members if m["code"] != rep["code"]],
        })

    details.sort(key=lambda d: d["rep_code"])
    return reps, details


# ── ProcessPool worker ────────────────────────────────

def _scan_one_worker(args):
    """Process-pool worker: 读 ETF K线 → analysis_engine → factor history."""
    code, name, history_days = args
    try:
        from tools.storage.store import DataStore
        from tools.analysis.analysis_engine import AnalysisEngine, RawContext
        from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows

        kline = DataStore.get_etf_kline(code)
        if not kline or len(kline) < 30:
            return {'code': code, 'name': name, 'error': f'数据不足: {len(kline) if kline else 0} 根，请先 sync --etf'}

        if history_days and len(kline) > history_days:
            kline = kline[-history_days:]

        ts_code = kline[0].get('ts_code', code)

        basic = DataStore.get_etf_basic(code)
        display_name = basic.get('name', name) or name
        benchmark = basic.get('benchmark', '')

        from tools.storage.sources.eastmoney import _synthesize_weekly
        weekly = _synthesize_weekly(kline)

        ctx = RawContext(
            kline=kline,
            weekly=weekly,
            current_price=kline[-1]['close'],
            market_cap_yi=0,
            industry='ETF',
            code=ts_code,
            name=display_name,
        )

        dates = [k['trade_date'] for k in kline]
        history = AnalysisEngine().analyze_history(ctx, dates)
        rows = compute_factor_history(ctx, step=1, lookback=history_days, history=history)

        for i in range(len(rows)):
            r = rows[i]
            dif_v = r.get('macd_dif')
            dea_v = r.get('macd_dea')
            if dif_v is not None and dea_v is not None:
                if dif_v > dea_v and dif_v > 0:
                    r['macd_signal'] = '金叉多头'
                elif dif_v > dea_v and dif_v < 0:
                    r['macd_signal'] = '弱金叉'
                elif dif_v < dea_v and dif_v < 0:
                    r['macd_signal'] = '死叉空头'
                else:
                    r['macd_signal'] = '强势死叉'
            else:
                r['macd_signal'] = '—'
            bar_v = r.get('macd_bar')
            bar_d_v = r.get('macd_bar_delta')
            if bar_v is None:
                r['macd_state'] = '—'
            elif bar_v > 0 and (bar_d_v is None or bar_d_v >= 0):
                r['macd_state'] = '🟢绿柱'
            elif bar_v > 0 and bar_d_v < 0:
                r['macd_state'] = '🟢柱缩短'
            elif bar_v < 0 and (bar_d_v is None or bar_d_v <= 0):
                r['macd_state'] = '🔴红柱'
            else:
                r['macd_state'] = '🔴柱缩短'
            if i > 0:
                r['change'] = diff_rows(rows[i-1], r)
            else:
                r['change'] = {}

        last = rows[-1] if rows else {}
        return {
            'code': code,
            'ts_code': ts_code,
            'name': display_name,
            'benchmark': benchmark,
            'rsi6': last.get('rsi6'),
            'macd_dif': last.get('macd_dif'),
            'macd_dea': last.get('macd_dea'),
            'macd_bar': last.get('macd_bar'),
            'macd_bar_delta': last.get('macd_bar_delta'),
            'signal': _classify_signal(last.get('rsi6'), last.get('macd_dif'),
                                       last.get('macd_dea')),
            'rows': rows,
        }
    except Exception as e:
        import traceback
        return {'code': code, 'name': name, 'error': str(e)[:200] + ' | TB: ' + repr(traceback.format_exc())[:600]}


# ── md 渲染 ────────────────────────────────

def _md_filename(result: dict) -> str:
    """md 文件名 — 同一份结果在生成和清理时必须算出同一个名字"""
    name = result['name']
    safe_name = (name.replace("/", "-").replace("\\", "-").replace(":", "-")
                 .replace("*", "").replace("?", "").replace('"', "")
                 .replace("<", "").replace(">", "").replace("|", ""))
    return f"{result['ts_code']}-{safe_name}.md"


def _write_md(result: dict, output_dir: Path):
    from tools.render.report_renderer import FACTOR_HISTORY_HEADER, FACTOR_HISTORY_SEP, _format_factor_row

    ts_code = result['ts_code']
    name = result['name']
    benchmark = result.get('benchmark', '')
    rows = result['rows']

    md = []
    md.append(f"# {name} ({ts_code})\n\n")
    md.append(f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    md.append(f"> 数据: ETF 场内日K (ts_code={ts_code}) × {len(rows)} 天\n\n")

    md.append("## ETF 基本信息\n\n")
    md.append(f"- ts_code: `{ts_code}`\n")
    md.append(f"- 名称: {name}\n")
    if benchmark:
        md.append(f"- 跟踪指数: {benchmark}\n")
    md.append(f"- 命中信号: {result.get('signal', '—')}\n")
    if result.get("group_size", 1) > 1:
        md.append(f"- 同指数基金: {result['group_size']} 只 (本只为流动性最好的代表)\n")
    md.append(f"- K线覆盖: {rows[0]['date']} ~ {rows[-1]['date']} ({len(rows)} 天)\n\n")

    last = rows[-1]
    md.append("## 当前状态\n\n")
    def _f(v, fmt=None):
        if v is None: return '—'
        fv = float(v)
        if fmt:
            return f"{fv:{fmt}}"
        # 自动精度: 价格 <10 用 .3f, 否则 .1f; MACD 类小值用 .4f
        if abs(fv) < 10:
            return f"{fv:.3f}"
        return f"{fv:.1f}"
    def _rsi_state(v):
        if v is None: return ''
        if v < 20:   return ' 🔴 深度超卖'
        if v < 30:   return ' 🟠 超卖'
        if v > 80:   return ' 🟡 超买'
        if v > 70:   return ' ⚪ 偏高'
        return ''
    def _macd_state(dif, dea):
        if dif is None or dea is None: return ''
        if dif > 0 and dea <= 0:  return ' 🟢 金叉'
        if dif > 0 and dea > 0:   return ' ⚪ 多头'
        if dif < 0 and dea >= 0:  return ' 🔴 死叉'
        return ' ⚪ 空头'
    rsi6_v = last.get('rsi6')
    dif_v = last.get('macd_dif')
    dea_v = last.get('macd_dea')
    md.append(f"| 指标 | 值 |\n|---|---|\n")
    md.append(f"| 日期 | {last['date']} |\n")
    md.append(f"| 收盘 | {_f(last.get('close'))} |\n")
    md.append(f"| RSI6 | {_f(rsi6_v)}{_rsi_state(rsi6_v)} |\n")
    md.append(f"| RSI12 | {_f(last.get('rsi12'))} |\n")
    md.append(f"| DIF | {_f(dif_v)}{_macd_state(dif_v, dea_v)} |\n")
    md.append(f"| DEA | {_f(dea_v)} |\n")
    md.append(f"| BAR | {_f(last.get('macd_bar'))} |\n")
    md.append(f"| BARΔ | {_f(last.get('macd_bar_delta'))} |\n")
    md.append(f"| MA20 偏离 | {_f(last.get('ma_dev_daily'))}%\n\n")

    md.append(f"## 历史因子走势 (ETF 日K, 共 {len(rows)} 天, 倒序)\n\n")
    md.append(FACTOR_HISTORY_HEADER + "\n")
    md.append(FACTOR_HISTORY_SEP + "\n")
    for i in range(len(rows) - 1, -1, -1):
        row_str = _format_factor_row(rows, i)
        if row_str is not None:
            md.append(row_str + "\n")

    out_path = output_dir / _md_filename(result)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(md), encoding='utf-8')
    return out_path


def _summarize_etf(result: dict) -> str:
    if 'error' in result:
        return f"\n=== {result['name']} ({result['code']}) ===\n  ⚠️ {result['error']}\n"
    ts_code = result['ts_code']
    name = result['name']
    benchmark = result.get('benchmark', '')
    rows = result['rows']
    last = rows[-1]

    rsi6 = last.get('rsi6') or 0
    rsi12 = last.get('rsi12') or 0
    dif = last.get('macd_dif') or 0
    bar = last.get('macd_bar') or 0
    bar_d = last.get('macd_bar_delta') or 0

    lines = [f"\n{'='*60}", f"=== {name} ({ts_code}) ==="]
    if benchmark:
        lines.append(f"    跟踪: {benchmark}")
    lines.append(f"{'='*60}")

    if rsi6 < 20:
        rsi_state = '🔴 深度超卖 (<20)'
    elif rsi6 < 30:
        rsi_state = '🟠 超卖 (<30)'
    elif rsi6 > 80:
        rsi_state = '🟡 超买 (>80)'
    elif rsi6 > 70:
        rsi_state = '⚪ 偏高 (>70)'
    else:
        rsi_state = '⚪ 正常 (30~70)'
    lines.append(f"- RSI6: {rsi6:.1f} ({rsi_state})")
    lines.append(f"- RSI12: {rsi12:.1f}")

    if dif > 0 and last.get('macd_dea', 0) <= 0:
        macd_change = '🟢 金叉'
    elif dif > 0 and last.get('macd_dea', 0) > 0:
        macd_change = '⚪ 多头'
    elif dif < 0 and last.get('macd_dea', 0) >= 0:
        macd_change = '🔴 死叉'
    else:
        macd_change = '⚪ 空头'
    lines.append(f"- DIF: {dif:.3f} ({macd_change})")
    bar_color = '绿柱' if bar > 0 else '红柱'
    bar_momentum = '涨势减弱' if (bar > 0 and bar_d < 0) else ('跌势减弱' if (bar < 0 and bar_d > 0) else '动能持续')
    lines.append(f"- BAR: {bar:.3f} ({bar_color}, {bar_momentum})")
    lines.append(f"- BARΔ: {bar_d:.3f}")

    return '\n'.join(lines)


# ============================================================
# 扫描主入口 (CLI 和 t_analyze_all 共用)
# ============================================================

def _resolve_history_days(code: str, override: int | None) -> int:
    """因子历史回看交易日数 — 走真源 factor_history_lookback (ETF 200 / 股票 120)"""
    if override:
        return override
    from tools.analysis.analysis_result_signals import factor_history_lookback
    return factor_history_lookback(code)


def scan_etf_signals(codes: list[str] | None = None,
                     history_days: int | None = None,
                     workers: int = 0) -> tuple[list[dict], dict]:
    """扫 ETF → (命中结果列表, 统计)

    Args:
        codes:       指定代码 (6位); None = 全市场候选池
        history_days: 回看交易日数; None = 用真源 factor_history_lookback
        workers:     深扫进程数; 0 = 单线程 (t_analyze_all 用, 不能 spawn 子进程)
    Returns:
        (results, stats) — results 是深扫结果 list[dict] (含 rows)
    """
    from tools.batch.etf_universe import load_etf_basic, filter_by_fund_type

    stats: dict = {"mode": "指定代码" if codes else "全市场",
                   "hits": 0, "raw_hits": 0, "scored": 0, "candidates": 0,
                   "oversold": 0, "golden_cross": 0,
                   "merged_groups": 0, "merged_members": 0}
    rep_by_code: dict[str, dict] = {}

    if codes:
        # 指定代码: 不设命中门槛, 全部深扫 (用户点名了)
        basic = {str(r["ts_code"]): r for r in load_etf_basic()}
        universe = []
        for c in codes:
            code6 = c.strip().split(".")[0].zfill(6)
            row = basic.get(f"{code6}.SH") or basic.get(f"{code6}.SZ") or {}
            universe.append({"code": code6,
                             "ts_code": row.get("ts_code", f"{code6}.SH"),
                             "name": row.get("name", code6),
                             "benchmark": row.get("benchmark", ""),
                             "fund_type": row.get("fund_type", "")})
        stats["candidates"] = len(universe)
        stats["raw_hits"] = len(universe)
        stats["hits"] = len(universe)
        work = [(u["code"], u["name"], _resolve_history_days(u["code"], history_days))
                for u in universe]
    else:
        basic = load_etf_basic()
        if not basic:
            stats["error"] = "DataStore 读不到 ETF 基础信息, 先跑 sync --etf"
            return [], stats
        candidate_ts = filter_by_fund_type(basic)
        by_ts = {str(r["ts_code"]): r for r in basic}
        universe = [{"code": ts.split(".")[0], "ts_code": ts,
                     "name": by_ts.get(ts, {}).get("name", ts.split(".")[0]),
                     "benchmark": by_ts.get(ts, {}).get("benchmark", ""),
                     "fund_type": by_ts.get(ts, {}).get("fund_type", "")}
                    for ts in candidate_ts]
        stats["candidates"] = len(universe)
        window = history_days or _resolve_history_days(
            universe[0]["code"] if universe else "", None)
        hits, pre_stats, klines = _prefilter_signals(universe, window)
        stats.update({k: pre_stats[k] for k in
                      ("scored", "candidates", "oversold", "golden_cross")})
        stats["raw_hits"] = len(hits)
        # 同一跟踪指数合并 (口径跟 /t-etf-wyckoff-a2m 同源: 归一化 benchmark + 相关性 ≥0.95)
        reps, merge_details = _dedup_hits(hits, klines, by_ts)
        stats["hits"] = len(reps)
        stats["merged_groups"] = sum(1 for d in merge_details if d["members"] > 1)
        stats["merged_members"] = stats["raw_hits"] - len(reps)
        if not reps:
            return [], stats
        rep_by_code = {r["code"]: r for r in reps}
        work = [(r["code"], r["name"], _resolve_history_days(r["code"], history_days))
                for r in reps]

    if workers and workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_scan_one_worker, item): item[0] for item in work}
            results = []
            for fut in as_completed(futs):
                results.append(fut.result())
    else:
        results = [_scan_one_worker(item) for item in work]

    # 合并组信息挂回结果 (汇总表显示 "同指数 N 只")
    for r in results:
        rep = rep_by_code.get(r.get("code"))
        r["group_size"] = rep.get("group_size", 1) if rep else 1
        r["group_peers"] = rep.get("group_peers", []) if rep else []
        r["index_name"] = rep.get("index_name", "") if rep else ""
    return results, stats


def main():
    parser = argparse.ArgumentParser(description="ETF 场内基金 MACD 因子扫描 (0 网络)")
    parser.add_argument("--etfs",       type=str, nargs='+', default=None,
                        help="ETF 代码列表 (6位, 空格分隔), 全量出 md; 不传 = 扫全市场 (同指数合并后出 md)")
    # CLI 字面值 --lookback 保留 (用户长期沿用), 内部属性名用业务名 history_days
    parser.add_argument("--lookback",   dest="history_days", type=int, default=0,
                        help="回看交易日数 (默认 0 = 用真源 factor_history_lookback, ETF 200)")
    parser.add_argument("--output-dir", type=str, default='docs/etf-macd', help="md 输出目录")
    parser.add_argument("--no-summary", action='store_true', help="不输出 chat summary")
    parser.add_argument("--workers",    type=int, default=4, help="深扫进程数")
    args = parser.parse_args()

    t0 = time.time()
    print(f"=== ETF MACD 扫描 (回看 {args.history_days or '真源(ETF 200)'}, "
          f"workers {args.workers}) ===")
    results, stats = scan_etf_signals(codes=args.etfs,
                                       history_days=args.history_days or None,
                                       workers=args.workers)
    if stats.get("error"):
        print(f"❌ {stats['error']}")
        return 1

    if args.etfs:
        print(f"  候选 {stats['candidates']} 只 (命令行指定), 全部深扫")
    else:
        print(f"  候选池 {stats['candidates']} 只 (fund_type 过滤后) → "
              f"可算 {stats['scored']} 只")
        print(f"  命中 {stats['raw_hits']} 只 (超卖 {stats['oversold']} / 金叉 {stats['golden_cross']}) "
              f"→ 同指数合并后 {stats['hits']} 只 "
              f"({stats['merged_groups']} 组重复, 合并掉 {stats['merged_members']} 只)")

    ok = [r for r in results if 'error' not in r]
    errs = [r for r in results if 'error' in r]
    for r in errs:
        print(f"  ⚠️ {r['name']} ({r['code']}): {r['error']}")

    output_dir = Path(args.output_dir)
    written = 0
    current_names = set()
    for r in ok:
        path = _write_md(r, output_dir)
        current_names.add(path.name)
        written += 1
        print(f"  📄 {path}")
    print(f"\n共写入 {written} 个 md 文件 (耗时 {time.time() - t0:.1f}s)")

    if not args.etfs and output_dir.exists():
        # 清理上一轮留下、这轮不再命中的 md (跟 t_analyze_all 孤儿清理同口径)
        from tools.storage.safe_fs import safe_delete
        removed = 0
        for p in sorted(output_dir.glob("*.md")):
            if p.name not in current_names:
                if safe_delete(p, reason="ETF MACD 扫描: 本轮不再命中, 清理旧 md"):
                    removed += 1
        if removed:
            print(f"  🧹 清理 {removed} 个不再命中的旧 md")

    if not args.no_summary:
        print(f"\n\n{'#'*60}")
        print(f"# ETF MACD 扫描 chat summary")
        print(f"{'#'*60}")
        for r in sorted(ok, key=lambda x: (0 if x.get('signal') == '超卖' else 1,
                                           x.get('rsi6') or 999, x.get('code', ''))):
            print(_summarize_etf(r))
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
