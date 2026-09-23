"""
tools/batch/concept_macd_scan.py — THS 同花顺概念板块反转信号扫描 (2026-09-17 v3 重构)

v3 = 数据源从"top N 龙头股流通市值加权合成 K 线" 改为 "THS 概念板块 K 线直读"

v1 (2026-07): /t-sector-ma (MA5/20/60/120 顶底分数)
v2 (2026-09-17): /t-sector-macd (合成 K线 + 流通市值加权)  ← 已弃
v3 (2026-09-17): /t-concept-macd (THS 概念板块 K线直读)  ← 当前

复用:
  - AnalysisEngine.analyze_history(ctx, dates) 个股/板块调用接口完全一致
  - compute_factor_history(ctx, step, lookback, history) 同接口
  - 渲染: tools.render.report_renderer.{FACTOR_HISTORY_HEADER, FACTOR_HISTORY_SEP, _format_factor_row}

输出:
  docs/concept-macd/<ts_code>.md  每概念 1 个 md

用法:
  bash tools/with_venv.sh python -m tools.batch.concept_macd_scan              # 默认 watchlist.ths_whitelist 15 概念
  ... --concepts "CPO" "PCB" "机器人"                                          # 临时指定
  ... --lookback 500                                                           # 回看天数 (默认 500≈2 年)
  ... --output-dir docs/concept-macd                                            # md 路径
  ... --no-summary                                                              # 不输出 chat summary
  ... --workers 4                                                               # 进程数

0 网络: 走 DataStore.get_ths_kline (读 data/history/ths/kline/<ts_code>.parquet)
跑前必须 sync --ths 落盘
"""
import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import builtins as _b
_orig_print = _b.print
def _flushing_print(*a, **kw):
    kw['flush'] = True
    _orig_print(*a, **kw)
print = _flushing_print


def match_name_for_filename(name: str) -> str:
    """清理文件名: 移除 / \ 空格等不安全字符"""
    if not name:
        return "unknown"
    # Windows / macOS 都禁的字符
    unsafe = '<>:"/\\|?* '
    safe = name
    for c in unsafe:
        safe = safe.replace(c, "_")
    return safe


def _load_ths_whitelist() -> list[str]:
    """从 data/ths_whitelist.json 读 concepts 白名单 (15 个 match 名)

    2026-09-17 拆出独立文件 (跟 watchlist stocks 分离).
    数据结构:
      data/ths_whitelist.json → key "concepts":
        [{ "match": "CPO", "note": "..." }, ...]
    兼容老路径: data/watchlist.json ths_whitelist 字段
    """
    p = Path("config/ths_whitelist.json")
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [item.get("match", "") for item in data.get("concepts", []) if item.get("match")]
        except Exception:
            pass
    # 兼容老路径
    fallback = Path("config/watchlist.json")
    if fallback.exists():
        try:
            data = json.loads(fallback.read_text(encoding="utf-8"))
            return [item.get("match", "") for item in data.get("ths_whitelist", []) if item.get("match")]
        except Exception:
            pass
    return []


# ── 信号提取 (跟 v2 一致, 跟 AnalysisEngine 输出 schema 完全一致) ─────

def _extract_signals(rows: list[dict], max_signals: int = 999) -> dict:
    """从 rows 提取 MACD 反转信号 (红+BARΔ 转正 / 绿+BARΔ 转负)"""
    red_to_green = []
    green_to_red = []

    for i in range(1, len(rows)):
        prev = rows[i-1]
        curr = rows[i]
        bar_prev = prev.get('macd_bar')
        bar_curr = curr.get('macd_bar')
        bar_d = curr.get('macd_bar_delta')

        if bar_prev is None or bar_curr is None:
            continue

        # 红 + BARΔ 转正 (= 跌势减弱, 底信号)
        if bar_prev < 0 and bar_curr > 0 and bar_d is not None and bar_d > 0:
            red_to_green.append({
                'date': curr['date'],
                'bar_prev': round(bar_prev, 3),
                'bar_curr': round(bar_curr, 3),
                'bar_delta': round(bar_d, 3),
                'dif': round(curr.get('macd_dif', 0) or 0, 3),
                'rsi6': round(curr.get('rsi6', 0) or 0, 1),
            })

        # 绿 + BARΔ 转负 (= 涨势减弱, 顶信号)
        if bar_prev > 0 and bar_curr < 0 and bar_d is not None and bar_d < 0:
            green_to_red.append({
                'date': curr['date'],
                'bar_prev': round(bar_prev, 3),
                'bar_curr': round(bar_curr, 3),
                'bar_delta': round(bar_d, 3),
                'dif': round(curr.get('macd_dif', 0) or 0, 3),
                'rsi6': round(curr.get('rsi6', 0) or 0, 1),
            })

    return {
        'red_to_green': red_to_green[-max_signals:] if red_to_green else [],
        'green_to_red': green_to_red[-max_signals:] if green_to_red else [],
    }


def _detect_tops_bottoms(rows: list[dict]) -> dict:
    """DIF 新低/新高 + BARΔ 配合 (近 1 年最低/最高 + 趋势确认)"""
    if not rows:
        return {'dif_new_low': None, 'dif_new_high': None}

    difs = [r.get('macd_dif') for r in rows if r.get('macd_dif') is not None]
    if not difs:
        return {'dif_new_low': None, 'dif_new_high': None}

    min_dif = min(difs)
    max_dif = max(difs)

    min_idx = next(i for i, r in enumerate(rows) if r.get('macd_dif') == min_dif)
    max_idx = next(i for i, r in enumerate(rows) if r.get('macd_dif') == max_dif)

    new_low = rows[min_idx]
    new_high = rows[max_idx]

    # DIF 新低 + 之后 BARΔ 转正 → 真正见底
    new_low_confirmed = False
    for j in range(min_idx + 1, min(min_idx + 6, len(rows))):
        if rows[j].get('macd_bar_delta') is not None and rows[j].get('macd_bar_delta') > 0:
            new_low_confirmed = True
            break

    new_high_confirmed = False
    for j in range(max_idx + 1, min(max_idx + 6, len(rows))):
        if rows[j].get('macd_bar_delta') is not None and rows[j].get('macd_bar_delta') < 0:
            new_high_confirmed = True
            break

    return {
        'dif_new_low': {
            'date': new_low['date'],
            'dif': round(min_dif, 3),
            'confirmed': new_low_confirmed,
        },
        'dif_new_high': {
            'date': new_high['date'],
            'dif': round(max_dif, 3),
            'confirmed': new_high_confirmed,
        },
    }


# ── ProcessPool worker ────────────────────────────────

def _scan_one_worker(args):
    """Process-pool worker: 读 THS K线 → analysis_engine → factor history."""
    match_name, lookback, max_signals = args
    try:
        from tools.storage.store import DataStore
        from tools.analysis.analysis_engine import AnalysisEngine, RawContext
        from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows

        # 1. 读 THS K 线 (走 DataStore.get_ths_kline, 反查名 + 落盘 parquet)
        ths_kline = DataStore.get_ths_kline(match_name)
        if not ths_kline or len(ths_kline) < 30:
            return {'match_name': match_name, 'error': f'数据不足: {len(ths_kline) if ths_kline else 0} 根'}

        # 截取最近 lookback 天
        if lookback and len(ths_kline) > lookback:
            ths_kline = ths_kline[-lookback:]

        # 反查 ts_code 用于 md 文件名 (thematic name)
        code = ths_kline[0].get('ts_code', '?')
        name = match_name
        # 找 ths_index 拿到 name (用于 md 标题)
        index_rows = DataStore.get_ths_index()
        for r in index_rows:
            if r.get('ts_code') == code:
                name = f"{match_name}({r.get('name', '')})"
                break

        # 2. 构造周线 (THS 指数 K 线自然周聚合)
        from tools.storage.sources.eastmoney import _synthesize_weekly
        weekly = _synthesize_weekly(ths_kline)

        # 3. 包装成 RawContext (AnalysisEngine 复用接口)
        ctx = RawContext(
            kline=ths_kline,
            weekly=weekly,
            eps_table=[],
            fflow={},
            moneyflow=[],
            current_price=ths_kline[-1]['close'],
            market_cap_yi=0,
            industry='THS',
            code=code,
            name=match_name,
        )

        # 4. AnalysisEngine.analyze_history + compute_factor_history (复用, 跟个股同)
        dates = [k['trade_date'] for k in ths_kline]
        history = AnalysisEngine().analyze_history(ctx, dates)
        rows = compute_factor_history(ctx, step=1, lookback=lookback, history=history)

        # 5. 后处理: MACD 信号 + 状态 + 变化 (跟个股详报一致 schema)
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

        # 6. 提取反转 + 顶底
        signals = _extract_signals(rows, max_signals=max_signals)
        tops_bottoms = _detect_tops_bottoms(rows)

        return {
            'match_name': match_name,
            'code': code,
            'name': name,
            'rows': rows,
            'signals': signals,
            'tops_bottoms': tops_bottoms,
        }
    except Exception as e:
        import traceback
        return {'match_name': match_name, 'error': str(e)[:200] + ' | TB: ' + repr(traceback.format_exc())[:600]}


# ── md 渲染 ────────────────────────────────

def _write_md(result: dict, output_dir: Path):
    """每概念 1 个 md (复用个股 _format_factor_row, 23 列 schema 一致)"""
    from tools.render.report_renderer import FACTOR_HISTORY_HEADER, FACTOR_HISTORY_SEP, _format_factor_row

    code = result['code']
    name = result['name']
    rows = result['rows']
    signals = result['signals']
    tops_bottoms = result['tops_bottoms']

    md = []
    md.append(f"# {name} - 概念反转信号扫描 (THS K线)\n\n")
    md.append(f"> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    md.append(f"> 数据: 同花顺概念板块 K线 (ts_code={code}) × {len(rows)} 天\n\n")

    # 板块基本信息
    md.append("## 板块基本信息\n\n")
    md.append(f"- ts_code: `{code}` (Tushare 同花顺体系)\n")
    md.append(f"- 名称: {name}\n")
    md.append(f"- K线覆盖: {rows[0]['date']} ~ {rows[-1]['date']} ({len(rows)} 天)\n\n")

    # 当前状态
    last = rows[-1]
    md.append("## 当前状态 (THS K线)\n\n")
    def _f(v, fmt='.1f'):
        if v is None:
            return '—'
        return f"{float(v):{fmt}}"
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
    md.append(f"| MA20 偏离 | {_f(last.get('ma_dev_daily'))}% |\n\n")

    # 反转信号 (按日期倒序, 最新在上)
    md.append("## 反转信号 (重点关注)\n\n")
    rtg = list(reversed(signals['red_to_green']))   # 倒序
    gtr = list(reversed(signals['green_to_red']))   # 倒序
    md.append(f"### 🟢 红柱 + BARΔ 转正 (底信号, {len(rtg)} 次)\n\n")
    if rtg:
        md.append("| 日期 | BAR(prev) | BAR(curr) | BARΔ | DIF | RSI6 |\n")
        md.append("|---|---|---|---|---|---|\n")
        for s in rtg:
            md.append(f"| {s['date']} | {s['bar_prev']} | {s['bar_curr']} | {s['bar_delta']} | {s['dif']} | {s['rsi6']} |\n")
    else:
        md.append("无\n")
    md.append("\n")

    md.append(f"### 🔴 绿柱 + BARΔ 转负 (顶信号, {len(gtr)} 次)\n\n")
    if gtr:
        md.append("| 日期 | BAR(prev) | BAR(curr) | BARΔ | DIF | RSI6 |\n")
        md.append("|---|---|---|---|---|---|\n")
        for s in gtr:
            md.append(f"| {s['date']} | {s['bar_prev']} | {s['bar_curr']} | {s['bar_delta']} | {s['dif']} | {s['rsi6']} |\n")
    else:
        md.append("无\n")
    md.append("\n")

    # 顶底信号
    md.append("## 顶底信号\n\n")
    low = tops_bottoms.get('dif_new_low') or {}
    high = tops_bottoms.get('dif_new_high') or {}
    if low and low.get('date'):
        conf = "✅ 已确认" if low.get('confirmed') else "⚠️ 未确认"
        md.append(f"### 📉 DIF 新低 (近 1 年最低): {low.get('date')}\n")
        md.append(f"- DIF = {low.get('dif', 0):.3f}\n")
        md.append(f"- 后续 BARΔ 转正: {conf}\n\n")
    if high and high.get('date'):
        conf = "✅ 已确认" if high.get('confirmed') else "⚠️ 未确认"
        md.append(f"### 📈 DIF 新高 (近 1 年最高): {high.get('date')}\n")
        md.append(f"- DIF = {high.get('dif', 0):.3f}\n")
        md.append(f"- 后续 BARΔ 转负: {conf}\n\n")

    # 历史因子走势
    md.append(f"## 历史因子走势 (THS K线, 共 {len(rows)} 天, 倒序)\n\n")
    md.append(FACTOR_HISTORY_HEADER + "\n")
    md.append(FACTOR_HISTORY_SEP + "\n")
    for i in range(len(rows) - 1, -1, -1):
        row_str = _format_factor_row(rows, i)
        if row_str is not None:
            md.append(row_str + "\n")

    # 文件名: {concept中文名}_{ts_code}.md (一眼能看出是啥概念)
    safe_name = match_name_for_filename(result.get('match_name', ''))
    out_path = output_dir / f"{safe_name}_{code}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(md), encoding='utf-8')
    return out_path


def _summarize_concept(result: dict) -> str:
    if 'error' in result:
        return f"\n=== {result['match_name']} ===\n  ⚠️ {result['error']}\n"
    code = result['code']
    name = result['name']
    rows = result['rows']
    last = rows[-1]
    signals = result['signals']
    tops_bottoms = result['tops_bottoms']

    rsi6 = last.get('rsi6') or 0
    rsi12 = last.get('rsi12') or 0
    dif = last.get('macd_dif') or 0
    bar = last.get('macd_bar') or 0
    bar_d = last.get('macd_bar_delta') or 0

    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"=== 概念: {name} (ts_code={code}) ===")
    lines.append(f"{'='*60}")

    # RSI6 4档状态 (阈值提示)
    if rsi6 < 20:
        rsi_state = '🔴 深度超卖 (<20, 关键买点)'
    elif rsi6 < 30:
        rsi_state = '🟠 超卖 (<30, 注意反弹)'
    elif rsi6 > 80:
        rsi_state = '🟡 超买 (>80, 减仓信号)'
    elif rsi6 > 70:
        rsi_state = '⚪ 偏高 (>70)'
    else:
        rsi_state = '⚪ 正常 (30~70)'
    lines.append(f"- RSI6: {rsi6:.1f} ({rsi_state})")
    lines.append(f"- RSI12: {rsi12:.1f}")

    # MACD 状态 (变色)
    if dif > 0 and last.get('macd_dea', 0) <= 0:
        macd_change = '🟢 金叉 (DIF 上穿 DEA)'
    elif dif > 0 and last.get('macd_dea', 0) > 0:
        macd_change = '⚪ 多头 (DIF/DEA 都 >0)'
    elif dif < 0 and last.get('macd_dea', 0) >= 0:
        macd_change = '🔴 死叉 (DIF 下穿 DEA)'
    else:
        macd_change = '⚪ 空头 (DIF/DEA 都 <0)'
    lines.append(f"- DIF: {dif:.3f} ({macd_change})")

    bar_color = '绿柱' if bar > 0 else '红柱'
    if bar > 0 and bar_d < 0:
        bar_momentum = '涨势减弱'
    elif bar < 0 and bar_d > 0:
        bar_momentum = '跌势减弱'
    else:
        bar_momentum = '动能持续'
    lines.append(f"- BAR: {bar:.3f} ({bar_color})")
    lines.append(f"- BARΔ: {bar_d:.3f} ({bar_momentum})")

    rtg = signals['red_to_green']
    gtr = signals['green_to_red']
    lines.append(f"- 反转信号:")
    lines.append(f"  🟢 红+BARΔ 转正 (底): {len(rtg)} 次{(' 最近: ' + rtg[-1]['date']) if rtg else ''}")
    lines.append(f"  🔴 绿+BARΔ 转负 (顶): {len(gtr)} 次{(' 最近: ' + gtr[-1]['date']) if gtr else ''}")

    low = tops_bottoms.get('dif_new_low') or {}
    high = tops_bottoms.get('dif_new_high') or {}
    if low.get('date'):
        conf = '✅ 已确认' if low.get('confirmed') else '⚠️ 未确认'
        lines.append(f"- 顶底信号:")
        lines.append(f"  📉 DIF 新低: {low.get('date')} ({low.get('dif'):.2f}) {conf}")
    if high.get('date'):
        conf = '✅ 已确认' if high.get('confirmed') else '⚠️ 未确认'
        lines.append(f"  📈 DIF 新高: {high.get('date')} ({high.get('dif'):.2f}) {conf}")
    return '\n'.join(lines)


# ── 主流程 ────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="THS 同花顺概念板块反转信号扫描 (v3, 0 网络)")
    parser.add_argument("--concepts",    type=str, nargs='+', default=None,
                        help="概念名列表 (空格分隔), 默认读 watchlist.json ths_whitelist")
    parser.add_argument("--lookback",    type=int, default=500, help="回看天数 (默认 500=~2 年)")
    parser.add_argument("--output-dir",  type=str, default='docs/concept-macd', help="md 输出目录")
    parser.add_argument("--no-summary",  action='store_true', help="不输出 chat summary")
    parser.add_argument("--workers",     type=int, default=4, help="进程数")
    parser.add_argument("--max-signals", type=int, default=999, help="md 每个反转段最多保留几条 (默认 999=全保留)")
    args = parser.parse_args()

    if args.concepts:
        concepts = [c.strip() for c in args.concepts if c.strip()]
        source = "命令行指定"
    else:
        concepts = _load_ths_whitelist()
        source = "watchlist.json ths_whitelist"
    if not concepts:
        print("⚠️ 没有概念可扫. 检查 watchlist.json ths_whitelist 字段 或 --concepts 参数")
        return 1

    output_dir = Path(args.output_dir)

    print(f"=== 概念反转信号扫描 ({len(concepts)} 个概念, lookback {args.lookback}, 源={source}) ===")
    print(f"概念: {', '.join(concepts)}")
    print(f"输出: {output_dir}/")
    print()

    t0 = time.time()
    work = [(c, args.lookback, args.max_signals) for c in concepts]
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_scan_one_worker, item): item[0] for item in work}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            if 'error' in r:
                print(f"  ⚠️ {r['match_name']}: {r['error']}")
            else:
                print(f"  ✅ {r['match_name']} ({r['code']}): {len(r['rows'])} 天")

    print(f"\n=== 全部完成 ({time.time()-t0:.1f}s) ===")

    written = 0
    for r in results:
        if 'error' not in r:
            path = _write_md(r, output_dir)
            written += 1
            print(f"  📄 {path}")
    print(f"\n共写入 {written} 个 md 文件")

    if not args.no_summary:
        print(f"\n\n{'#'*60}")
        print(f"# 概念反转信号 chat summary")
        print(f"{'#'*60}")
        for r in sorted(results, key=lambda x: x.get('match_name', '')):
            print(_summarize_concept(r))
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
