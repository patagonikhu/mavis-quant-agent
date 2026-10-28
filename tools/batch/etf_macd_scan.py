"""
tools/batch/etf_macd_scan.py — ETF 场内基金 MACD 因子扫描 (2026-10-09)

输出:
  docs/etf-macd/<ts_code>-<name>.md  每只 ETF 1 个 md

用法:
  bash tools/with_venv.sh python -m tools.batch.etf_macd_scan
  ... --etfs 515790 159995 512480       # 指定代码 (6位)
  ... --lookback 500                    # 回看天数 (默认 500≈2 年)
  ... --output-dir docs/etf-macd
  ... --no-summary
  ... --workers 4

0 网络: 走 DataStore.get_etf_kline (读 data/history/etf/kline/<YYYYQN>.parquet)
跑前必须 sync --etf 落盘
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


def _load_etf_whitelist() -> list[dict]:
    p = Path("config/etf_whitelist.json")
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return [item for item in data.get("etfs", []) if item.get("code")]
    except Exception:
        return []


# ── ProcessPool worker ────────────────────────────────

def _scan_one_worker(args):
    """Process-pool worker: 读 ETF K线 → analysis_engine → factor history."""
    code, name, lookback = args
    try:
        from tools.storage.store import DataStore
        from tools.analysis.analysis_engine import AnalysisEngine, RawContext
        from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows

        kline = DataStore.get_etf_kline(code)
        if not kline or len(kline) < 30:
            return {'code': code, 'name': name, 'error': f'数据不足: {len(kline) if kline else 0} 根，请先 sync --etf'}

        if lookback and len(kline) > lookback:
            kline = kline[-lookback:]

        ts_code = kline[0].get('ts_code', code)

        basic = DataStore.get_etf_basic(code)
        display_name = basic.get('name', name) or name
        benchmark = basic.get('benchmark', '')

        from tools.storage.sources.eastmoney import _synthesize_weekly
        weekly = _synthesize_weekly(kline)

        ctx = RawContext(
            kline=kline,
            weekly=weekly,
            eps_table=[],
            current_price=kline[-1]['close'],
            market_cap_yi=0,
            industry='ETF',
            code=ts_code,
            name=display_name,
        )

        dates = [k['trade_date'] for k in kline]
        history = AnalysisEngine().analyze_history(ctx, dates)
        rows = compute_factor_history(ctx, step=1, lookback=lookback, history=history)

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

        return {
            'code': code,
            'ts_code': ts_code,
            'name': display_name,
            'benchmark': benchmark,
            'rows': rows,
        }
    except Exception as e:
        import traceback
        return {'code': code, 'name': name, 'error': str(e)[:200] + ' | TB: ' + repr(traceback.format_exc())[:600]}


# ── md 渲染 ────────────────────────────────

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
    md.append(f"| MA20 偏离 | {_f(last.get('ma_dev_daily'))}% |\n\n")

    md.append(f"## 历史因子走势 (ETF 日K, 共 {len(rows)} 天, 倒序)\n\n")
    md.append(FACTOR_HISTORY_HEADER + "\n")
    md.append(FACTOR_HISTORY_SEP + "\n")
    for i in range(len(rows) - 1, -1, -1):
        row_str = _format_factor_row(rows, i)
        if row_str is not None:
            md.append(row_str + "\n")

    safe_name = name.replace("/", "-").replace("\\", "-").replace(":", "-").replace("*", "").replace("?", "").replace('"', "").replace("<", "").replace(">", "").replace("|", "")
    out_path = output_dir / f"{ts_code}-{safe_name}.md"
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


# ── 主流程 ────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ETF 场内基金 MACD 因子扫描 (0 网络)")
    parser.add_argument("--etfs",       type=str, nargs='+', default=None,
                        help="ETF 代码列表 (6位, 空格分隔), 默认读 config/etf_whitelist.json")
    parser.add_argument("--lookback",   type=int, default=500, help="回看天数 (默认 500≈2 年)")
    parser.add_argument("--output-dir", type=str, default='docs/etf-macd', help="md 输出目录")
    parser.add_argument("--no-summary", action='store_true', help="不输出 chat summary")
    parser.add_argument("--workers",    type=int, default=4, help="进程数")
    args = parser.parse_args()

    if args.etfs:
        etf_list = [{'code': c.strip().zfill(6), 'name': c.strip()} for c in args.etfs if c.strip()]
        source = "命令行指定"
    else:
        etf_list = _load_etf_whitelist()
        source = "config/etf_whitelist.json"

    if not etf_list:
        print("⚠️ 没有 ETF 可扫. 检查 config/etf_whitelist.json 或 --etfs 参数")
        return 1

    output_dir = Path(args.output_dir)
    print(f"=== ETF MACD 扫描 ({len(etf_list)} 只, lookback {args.lookback}, 源={source}) ===")
    print(f"ETFs: {', '.join(e['code'] for e in etf_list)}")
    print(f"输出: {output_dir}/")
    print()

    t0 = time.time()
    work = [(e['code'], e.get('name', e['code']), args.lookback) for e in etf_list]
    results = []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(_scan_one_worker, item): item[0] for item in work}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            if 'error' in r:
                print(f"  ⚠️ {r['name']} ({r['code']}): {r['error']}")
            else:
                print(f"  ✅ {r['name']} ({r['ts_code']}): {len(r['rows'])} 天")

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
        print(f"# ETF MACD 扫描 chat summary")
        print(f"{'#'*60}")
        for r in sorted(results, key=lambda x: x.get('code', '')):
            print(_summarize_etf(r))
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
