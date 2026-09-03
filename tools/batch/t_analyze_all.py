"""
t-analyze --all 批量分析: 4-8 worker 并发 analyze+render, 输出 signal-watchlist.md

2026-09-03 v6.0 改造:
  - 不再偷偷调 sync_incremental (改走 /t-sync skill, 入口 tools/sync.py)
  - 缺数据时直接报"请先 /t-sync", 不再调单只兜底
  - 跑前用户先 `python -m tools.sync --all-data`
"""
import json, sys, datetime, time, os
from pathlib import Path
sys.path.insert(0, '.')

from tools.kline_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_result_signals import compute_factor_history, diff_rows, extract_signals, format_signals_for_render
from tools.analysis.render_data import RenderData
from tools.render.report_renderer import render_report


wl = json.load(open('data/watchlist.json'))['stocks']
stocks = wl
today = datetime.date.today().isoformat()
output_path = Path('docs/signal-watchlist.md')

buy_rows, sell_rows, all_table_rows = [], [], []
md_written = 0
errs = []

print(f'=== t-analyze --all | {len(stocks)} 只 | {datetime.datetime.now().strftime("%H:%M:%S")} ===', flush=True)
print(f'  ℹ️  本脚本 0 网络, 缺数据请先跑: python -m tools.sync --all-data', flush=True)

t_total = time.time()

# 单只处理函数 (供 ThreadPoolExecutor 调用)
def process_one(s):
    code = s['code']
    name = s['name']
    subdir = 'portfolio' if s.get('list_type') == '持仓' else 'watchlist'
    list_type_label = '持仓' if s.get('list_type') == '持仓' else '自选'
    t0 = time.time()
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            # v6.0 改: 不再兜底拉数据, 报"请先 /t-sync"
            return ('err', code, '无K线, 请先跑 /t-sync')

        all_dates = [k['trade_date'].replace('-', '')[:8] for k in ctx.kline]
        dates = all_dates[-120:]
        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2:
            return ('err', code, f'history 不足 ({len(history)} 根)')

        result = history[dates[-1]]
        data = RenderData.from_result(ctx, result)
        data.factor_history_rows = compute_factor_history(ctx, step=1, lookback=120, history=history)
        md = render_report(data)
        md_path = Path('docs') / subdir / f'analyze-{code}-{name}.md'
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(md, encoding='utf-8')

        # signal table
        rows = data.factor_history_rows
        signals = []
        # 2026-09-02 加 list_type 标签 (持仓/自选/Magic初筛)
        tag = {
            '持仓': '🟢',
            'Magic初筛': '💎',
        }.get(list_type_label, '·')
        code_tagged = f"{tag} {code}"
        if len(rows) >= 2:
            r = rows[-1]
            changes = diff_rows(rows[-2], rows[-1])
            sigs = extract_signals(changes)
            sig_fmtd = format_signals_for_render(changes)
            hub_d = r.get('hub_daily') or {}
            hub_str = f"¥{hub_d.get('low',0):.0f}~{hub_d.get('high',0):.0f}{(hub_d.get('pos') or '')[:2]}" if hub_d.get('valid') else '—'
            has_sig = '⭐' if sig_fmtd else ''
            for _, detail, direction in sigs:
                signals.append((direction, code_tagged, name, detail))
            table_row = (code_tagged, name, (r.get('wyckoff_daily') or '?')[:10],
                         f"{r.get('ma_dev_daily') or 0:+.1f}%", hub_str,
                         ' '.join(sig_fmtd) if sig_fmtd else '—', has_sig)
        else:
            table_row = (code_tagged, name, '?', '0.0%', '—', '—', '')

        elapsed = time.time() - t0
        return ('ok', code, {'md_path': md_path, 'elapsed': elapsed, 'len': len(md),
                              'signals': signals, 'table_row': table_row, 'list_type': list_type_label})
    except Exception as e:
        return ('err', code, str(e)[:200])


# 并发跑 (跟 bb_obv_scan / refresh_all 一致, 默认 4 worker)
WORKERS = int(os.environ.get('T_ANALYZE_WORKERS', '4'))
print(f'\n[并发] {WORKERS} workers', flush=True)

from concurrent.futures import ThreadPoolExecutor, as_completed
results = []
with ThreadPoolExecutor(max_workers=WORKERS) as ex:
    futs = {ex.submit(process_one, s): s for s in stocks}
    for i, fut in enumerate(as_completed(futs), 1):
        s = futs[fut]
        status, code, payload = fut.result()
        if status == 'ok':
            results.append(payload)
            print(f'  [{i}/{len(stocks)}] ✅ {code} -> {payload["md_path"].name} ({payload["elapsed"]:.1f}s, {payload["len"]:,}chars)', flush=True)
        else:
            errs.append((code, payload))
            print(f'  [{i}/{len(stocks)}] ❌ {code}: {payload}', flush=True)

# 汇总 signal-watchlist.md
for r in results:
    md_written += 1
    for direction, code, name, detail in r['signals']:
        if direction == 'buy':
            buy_rows.append((code, name, detail))
        else:
            sell_rows.append((code, name, detail))
    all_table_rows.append(r['table_row'])

total_elapsed = time.time() - t_total

# write summary
# 2026-09-02 加: 按 list_type 分类统计 (持仓/自选/Magic初筛)
from collections import Counter
_lt_counter = Counter(s.get("list_type", "自选") for s in stocks)
_lt_summary = " | ".join(f"{k} {v}" for k, v in sorted(_lt_counter.items(), key=lambda x: -x[1]))
lines = [f"# 全量扫描 {today}\n\n> {len(stocks)} 只 ({_lt_summary}) | DataStore | {datetime.datetime.now().strftime('%H:%M:%S')}\n\n"]
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
