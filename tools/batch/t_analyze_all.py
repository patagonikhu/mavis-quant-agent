"""
t-analyze --all verbose 版本：每只股票分阶段打印进度
"""
import json, sys, datetime, time, inspect
from pathlib import Path
sys.path.insert(0, '.')

from tools.kline_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows, extract_signals, format_signals_for_render
from tools.analysis.render_data import RenderData
from tools.render.report_renderer import render_report
from tools.render import report_renderer as rr_mod


# Auto-discover all _section_* functions
_section_funcs = {}
for name, fn in inspect.getmembers(rr_mod, inspect.isfunction):
    if name.startswith('_section_') and name != '_section_data_sources':
        # 去掉 _section_ 前缀，得到中文标签
        raw = name[len('_section_'):]
        # 自动猜测中文标签
        _section_funcs[name] = raw.replace('_', '·')

_originals = {}


def make_verbose_section(name, original):
    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        code = '?'
        if args and hasattr(args[0], 'code'):
            code = args[0].code
        elif args and isinstance(args[0], str):
            code = args[0]
        print(f'    [section] {code}: {name}', flush=True)
        return result
    return wrapper


def install_verbose():
    for name in _section_funcs:
        orig = getattr(rr_mod, name)
        if name not in _originals:
            _originals[name] = orig
        setattr(rr_mod, name, make_verbose_section(_section_funcs[name], orig))


# Monkey-patch AnalysisEngine.analyze_history
_orig_analyze_history = AnalysisEngine.analyze_history


def verbose_analyze_history(self, ctx, dates):
    code = ctx.code if hasattr(ctx, 'code') else '?'
    n = len(self._phase1) if hasattr(self, '_phase1') else '?'
    print(f'    [analysis] {code}: {n} strategies', flush=True)
    for inst in self._phase1:
        print(f'      [strategy] {code}: {inst.name}', flush=True)
    return _orig_analyze_history(self, ctx, dates)


wl = json.load(open('data/watchlist.json'))['stocks']
stocks = wl
today = datetime.date.today().isoformat()
output_path = Path('docs/signal-watchlist.md')

buy_rows, sell_rows, all_table_rows = [], [], []
md_written = 0
errs = []

# Install verbose hooks
install_verbose()
AnalysisEngine.analyze_history = verbose_analyze_history

print(f'=== t-analyze --all (verbose) | {len(stocks)} 只 | {datetime.datetime.now().strftime("%H:%M:%S")} ===', flush=True)

# 增量同步最新 K 线 (跟 find_near_low / bb_obv_scan 对齐)
# 手动传 target_date, 否则增量逻辑看不到未来日期
try:
    from tools.kline_history_backfill import sync_incremental
    today_str = datetime.datetime.now().strftime('%Y%m%d')
    print(f'  增量同步 K 线 (sync_incremental, target_date={today_str})...', flush=True)
    sync_incremental(target_date=today_str)
except Exception as e:
    print(f'  [WARN] sync_incremental 失败: {e}', flush=True)

t_total = time.time()
for i, s in enumerate(stocks, 1):
    code, name = s['code'], s['name']
    subdir = 'portfolio' if s.get('list_type') == '持仓' else 'watchlist'
    list_type_label = '持仓' if s.get('list_type') == '持仓' else '自选'
    print(f'\n[{i}/{len(stocks)}] {code} {name} ({list_type_label}) START', flush=True)
    t0 = time.time()
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            print(f'  ❌ {code}: 无K线', flush=True)
            errs.append((code, '无K线 (本地 parquet 空, sync 失败?)'))
            continue
        all_dates = [k['trade_date'].replace('-', '')[:8] for k in ctx.kline]
        dates = all_dates[-120:]
        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2:
            print(f'  ❌ {code}: history 不足', flush=True)
            errs.append((code, f'history 不足 ({len(history)} 根)'))
            continue
        result = history[dates[-1]]
        print(f'    [render] {code}: building MD...', flush=True)
        data = RenderData.from_result(ctx, result)
        data.factor_history_rows = list(history.values())
        md = render_report(data)
        md_path = Path('docs') / subdir / f'analyze-{code}-{name}.md'
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding='utf-8')
        md_written += 1
        # signal table
        rows = compute_factor_history(ctx, step=1, lookback=120)
        if len(rows) >= 2:
            r = rows[-1]
            changes = diff_rows(rows[-2], rows[-1])
            sigs = extract_signals(changes)
            sig_fmtd = format_signals_for_render(changes)
            hub_d = r.get('hub_daily') or {}
            hub_str = f"¥{hub_d.get('low',0):.0f}~{hub_d.get('high',0):.0f}{(hub_d.get('pos') or '')[:2]}" if hub_d.get('valid') else '—'
            has_sig = '⭐' if sig_fmtd else ''
            for _, detail, direction in sigs:
                if direction == 'buy':
                    buy_rows.append((code, name, detail))
                else:
                    sell_rows.append((code, name, detail))
            all_table_rows.append((code, name, (r.get('wyckoff_daily') or '?')[:10],
                                  f"{r.get('ma_dev_daily') or 0:+.1f}%", hub_str,
                                  ' '.join(sig_fmtd) if sig_fmtd else '—', has_sig))
        elapsed = time.time() - t0
        print(f'    [done] {code} -> {md_path.name} ({elapsed:.1f}s, {len(md):,}chars)', flush=True)
    except Exception as e:
        elapsed = time.time() - t0
        print(f'  ❌ {code}: {type(e).__name__}: {e} ({elapsed:.1f}s)', flush=True)
        import traceback
        traceback.print_exc()
        errs.append((code, str(e)[:200]))

total_elapsed = time.time() - t_total

# write summary
lines = [f"# 全量扫描 {today}\n\n> {len(stocks)} 只 | DataStore | {datetime.datetime.now().strftime('%H:%M:%S')}\n\n"]
lines.append("## 底部信号（buy）\n\n| 代码 | 名称 | 信号 |\n|------|------|------|\n")
for code, name, detail in buy_rows:
    lines.append(f"| {code} | {name} | {detail} |\n")
if not buy_rows:
    lines.append("| — | 无 | — |\n")
lines.append("\n## 顶部/弱势信号（sell）\n\n| 代码 | 名称 | 信号 |\n|------|------|------|\n")
for code, name, detail in sell_rows:
    lines.append(f"| {code} | {name} | {detail} |\n")
if not sell_rows:
    lines.append("| — | 无 | — |\n")
lines.append("\n## 完整状态表\n\n| 代码 | 名称 | 场景 | 威科夫日 | MA日% | 日中枢 | 今日信号 |\n|------|------|------|---------|-------|--------|----------|\n")
all_table_rows.sort(key=lambda x: (0 if x[6] == '⭐' else 1, x[0]))
for code, name, wy, ma, hub_str, sig_str, has_sig in all_table_rows:
    lines.append(f"| {code} | {name} | {has_sig} | {wy} | {ma} | {hub_str} | {sig_str} |\n")
output_path.parent.mkdir(exist_ok=True)
output_path.write_text(''.join(lines), encoding='utf-8')

print(f'\n=== 完成 ===')
print(f'FILE: {output_path}')
print(f'MD生成: {md_written}/{len(stocks)}')
print(f'有今日信号: {sum(1 for r in all_table_rows if r[6]=="⭐")}只')
print(f'总耗时: {total_elapsed:.0f}s')
if errs:
    print(f'错误: {len(errs)}只')
    for c, e in errs:
        print(f'  {c}: {e}')
    # 把 errs 写进 md 报告 (顶部提示, 不影响看表)
    with output_path.open('a', encoding='utf-8') as f:
        f.write(f"\n\n## ⚠️ 失败清单 ({len(errs)} 只)\n\n")
        f.write("| 代码 | 错误 |\n|---|---|\n")
        for c, e in errs:
            f.write(f"| {c} | {e} |\n")
