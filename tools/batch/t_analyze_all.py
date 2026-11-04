"""
t-analyze --all 批量分析: 4-8 worker 并发 analyze+render, 输出 signal-watchlist.md

2026-09-03 v6.0 改造:
  - 不再偷偷调 sync_incremental (改走 /t-sync skill, 入口 tools/sync_data.py)
  - 缺数据时直接报"请先 /t-sync", 不再调单只兜底
  - 跑前用户先 `python -m tools.storage.sync --all-data`

2026-11-06 改造: blowout 段**完全不参与**批量跑
  - list_type=blowout 是"业绩暴增候选池", 用来筛的, 不是长期跟踪的票
  - --include-blowout flag 已删 (它能打开全跑 → 一次出 157 份详报, 把 docs/blowout/ 撑爆)
  - 要深挖某只 blowout 票: /t-analyze <code> 单只跑 (单只也不落 md, 报告走 stdout)
  - docs/blowout/ 目录已废弃, 历史产物由孤儿清理逻辑整个清掉
"""
import json, sys, datetime, time, os
import argparse
from pathlib import Path
sys.path.insert(0, '.')

from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_result_signals import (
    compute_factor_history,
    diff_rows,
    extract_signals,
    factor_history_lookback,
    format_signals_for_render,
)
from tools.analysis.render_data import RenderData
from tools.render.report_renderer import render_report


# === CLI 参数 ===
_arg_parser = argparse.ArgumentParser(description="t-analyze --all 批量分析")
_arg_parser.add_argument("--no-etf", action="store_true",
                         help="跳过 ETF 扫描 (默认同时跑 etf_macd_scan)")
_args, _unknown = _arg_parser.parse_known_args()  # 兼容老调用 `python -m ... t_analyze_all --all`


wl = DataStore.load_watchlist()['stocks']
# 2026-11-06: blowout 段永久排除 (候选池, 用来筛的, 不参与批量详报)
stocks = [s for s in wl if s.get('list_type', '自选') != 'blowout']
excluded = len(wl) - len(stocks)
if excluded:
    print(f'  ℹ️  跳过 blowout 候选池 {excluded} 只 '
          f'(候选池不参与批量详报; 单只深挖用 /t-analyze <code>)', flush=True)
today = datetime.date.today().isoformat()
output_path = Path('docs/signal-watchlist.md')

# === 2026-09-15 加: 清理 watchlist 之外的孤儿 md (残留旧详报, 让用户以为"没刷新") ===
# 2026-11-02 修 (两个真 bug):
#   ① 旧代码 stocks 已排除 blowout, 但清理循环却遍历 docs/blowout/ → 默认跑就把 blowout 全删了
#      修: 按子目录分别构造期望文件名
#   ③ 2026-11-06: blowout 段改成**不生成 md** (候选池), 于是 docs/blowout/ 里的全是历史产物
#      → _expected_by_sub['blowout'] 恒为空, 清理循环会把它整个清空 (符合预期)
#   ② 旧代码文件名用 watchlist 原始 name, 但 t_analyze_one 写入时会用 stock_basic 回退名
#      (name 为空/未知时) → 自己刚生成的报告下一轮就被当孤儿删掉
#      修: 用跟 t_analyze_one 一样的 name_for_file 解析
#   ③ 删除改用 safe_delete (移到 ~/.Trash, 不可恢复的 unlink 是事故根源)
import re
from tools.storage.safe_fs import safe_delete


def _name_for_file(s: dict) -> str:
    """跟 t_analyze_one.process_one 保持一致的文件名解析 (防自删)"""
    code6 = s['code'].split(".")[0] if "." in s['code'] else s['code']
    nm = s.get("name") or ""
    if not nm or nm in ("未知", "C", code6):
        try:
            sb = DataStore.get_stock_basic(code6)
            cand = sb.get("name", "")
            if cand and cand != "未知" and cand != code6:
                nm = cand
        except Exception:
            pass
        if not nm or nm == "未知":
            nm = code6
    return f'analyze-{code6}-{nm}.md'


# 按子目录分组构造期望文件名 (2026-11-02 修 bug ①)
_all_wl = DataStore.load_watchlist()['stocks']
_expected_by_sub = {'portfolio': set(), 'watchlist': set(), 'blowout': set()}
for _s in _all_wl:
    _lt = _s.get('list_type', '自选')
    # 2026-11-06 改: blowout 段不再生成 md → docs/blowout/ 里全是历史产物, 全清
    if _lt == 'blowout':
        continue
    _sub = 'portfolio' if _lt == '持仓' else 'watchlist'
    _expected_by_sub[_sub].add(_name_for_file(_s))

for sub in ('portfolio', 'watchlist', 'blowout'):
    d = Path(f'docs/{sub}')
    if not d.exists():
        continue
    expected_files = _expected_by_sub[sub]
    removed = 0
    for f in d.glob('analyze-*.md'):
        if f.name not in expected_files:
            if safe_delete(f, reason=f"t_analyze_all 孤儿 md 清理 ({sub})"):
                removed += 1
    if removed:
        print(f'  🧹 docs/{sub}: 清理 {removed} 个孤儿 md (不在 watchlist.json)', flush=True)

buy_rows, sell_rows, all_table_rows = [], [], []
md_written = 0
errs = []

print(f'=== t-analyze --all | {len(stocks)} 只 | {datetime.datetime.now().strftime("%H:%M:%S")} ===', flush=True)
print(f'  ℹ️  本脚本 0 网络, 缺数据请先跑: python -m tools.storage.sync --all-data', flush=True)

t_total = time.time()

# 单只处理函数 (供 ThreadPoolExecutor 调用)
def process_one(s):
    code = s['code']
    name = s['name']
    # 2026-09-18 加: 3 路分流 — 持仓→portfolio, 自选→watchlist, blowout→blowout
    # 2026-11-06 改: blowout 段**不落 md** (候选池, 用来筛的) —— 只进汇总表
    lt = s.get('list_type', '自选')
    if lt == '持仓':
        subdir = 'portfolio'
        list_type_label = '持仓'
    elif lt == 'blowout':
        # backstop: blowout 段已不参与批量跑 (上面的 stocks 已排除),
        # 这里再兜一层 —— 万一以后有人把它加回 stocks, 也不该落 md
        subdir = None
        list_type_label = 'blowout'
    else:
        subdir = 'watchlist'
        list_type_label = '自选'
    t0 = time.time()
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            # v6.0 改: 不再兜底拉数据, 报"请先 /t-sync"
            return ('err', code, '无K线, 请先跑 /t-sync')

        all_dates = [k['trade_date'].replace('-', '')[:8] for k in ctx.kline]
        # 2026-11-05: dates 必须**覆盖**因子历史表的回看天数, 否则 compute_factor_history
        #   会因 history 里缺该日期而整行丢弃 (result is None → continue)。
        #   ETF 回看 200 天 → dates 至少 200; 股票维持 120 (不变)。
        _factor_lookback = factor_history_lookback(code)
        dates = all_dates[-max(_factor_lookback, 120):]
        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2:
            return ('err', code, f'history 不足 ({len(history)} 根)')

        result = history[dates[-1]]
        data = RenderData.from_result(ctx, result)
        # 2026-11-05: lookback 按标的类型取 (ETF 200 / 股票 120)
        data.factor_history_rows = compute_factor_history(
            ctx, step=1, lookback=factor_history_lookback(code), history=history
        )
        md = render_report(data)
        md_path = None
        if subdir:
            md_path = Path('docs') / subdir / f'analyze-{code}-{name}.md'
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(md, encoding='utf-8')

        # signal table
        rows = data.factor_history_rows
        signals = []
        # 2026-09-02 加 list_type 标签 (持仓/自选/ROC_EY初筛), 2026-09-09 Magic→ROC_EY
        tag = {
            '持仓': '🟢',
            'ROC_EY初筛': '💎',
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


# 并发跑 (跟 refresh_all 一致, 默认 4 worker)
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
            _md_name = payload["md_path"].name if payload.get("md_path") else "(blowout 候选池, 不落盘)"
            print(f'  [{i}/{len(stocks)}] ✅ {code} -> {_md_name} ({payload["elapsed"]:.1f}s, {payload["len"]:,}chars)', flush=True)
        else:
            errs.append((code, payload))
            print(f'  [{i}/{len(stocks)}] ❌ {code}: {payload}', flush=True)

# 汇总 signal-watchlist.md
for r in results:
    if r.get('md_path'):
        md_written += 1
    for direction, code, name, detail in r['signals']:
        if direction == 'buy':
            buy_rows.append((code, name, detail))
        else:
            sell_rows.append((code, name, detail))
    all_table_rows.append(r['table_row'])

total_elapsed = time.time() - t_total

# write summary
# 2026-09-02 加: 按 list_type 分类统计 (持仓/自选/ROC_EY初筛), 2026-09-09 Magic→ROC_EY
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

# 2026-09-09 加: stdout 直接输出信号明细 (buy_rows + sell_rows), 不需要打开 md 文件看
if buy_rows:
    print(f'\n=== 今日底部信号 (buy, {len(buy_rows)} 只) ===')
    for code, name, detail in buy_rows:
        print(f'  🟢 {code} {name} | {detail}')
if sell_rows:
    print(f'\n=== 今日顶部信号 (sell, {len(sell_rows)} 只) ===')
    for code, name, detail in sell_rows:
        print(f'  🔴 {code} {name} | {detail}')
if not buy_rows and not sell_rows:
    print(f'\n  无今日 buy/sell  信号')
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

# ── ETF 段 (默认跟 --all 一起跑) ────────────────────────────
if not _args.no_etf:
    print(f'\n=== ETF MACD 扫描 ===', flush=True)
    from tools.batch.etf_macd_scan import scan_etf_signals, _write_md
    t_etf = time.time()
    # 2026-11-06: 候选池从 config/etf_whitelist.json (18 只手工名单, 已删)
    #   换成 DataStore 全市场 ETF + fund_type 过滤 (跟 /t-etf-wyckoff-a2m 同口径)
    # workers=0: 单线程 (t_analyze_all 顶层不在 __main__ 保护内, 不能 spawn 子进程)
    etf_results, etf_stats = scan_etf_signals(workers=0)
    if etf_stats.get('error'):
        print(f"  ⚠️ {etf_stats['error']}, 跳过 ETF 扫描", flush=True)
    else:
        etf_ok = [r for r in etf_results if 'error' not in r]
        print(f"  候选池 {etf_stats['candidates']} 只 (fund_type 过滤) → 可算 {etf_stats['scored']} 只 → "
              f"命中 {etf_stats['raw_hits']} 只 (超卖 {etf_stats['oversold']} / 金叉 {etf_stats['golden_cross']}) "
              f"→ 同指数合并后 {etf_stats['hits']} 只", flush=True)
        for r in etf_results:
            if 'error' in r:
                print(f"  ⚠️ {r['name']} ({r['code']}): {r['error']}", flush=True)
            else:
                print(f"  ✅ {r['name']} ({r['ts_code']}): {len(r['rows'])} 天 | {r.get('signal', '—')}", flush=True)

        # 每个代表出 1 份 md (同指数已合并, 不重复出)
        etf_output_dir = Path('docs/etf-macd')
        etf_written = 0
        for r in etf_ok:
            if _write_md(r, etf_output_dir) is not None:
                etf_written += 1
        print(f'\n  ETF 完成: {etf_written} 份 md / 命中 {len(etf_ok)} 只 ({time.time()-t_etf:.1f}s)', flush=True)

        # 追加 ETF 状态到 signal-watchlist.md (合并后的代表, 不是全量命中)
        with output_path.open('a', encoding='utf-8') as f:
            f.write(f"\n\n## ETF 状态 ({len(etf_ok)} 只命中, 同指数已合并)\n\n")
            f.write("| ETF | RSI6 | DIF | BAR | BARΔ | 状态 | 命中 | 同指数 |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for r in sorted(etf_ok, key=lambda x: (0 if x.get('signal') == '超卖' else 1,
                                                   x.get('rsi6') or 999, x.get('code', ''))):
                last = r['rows'][-1]
                rsi6 = last.get('rsi6') or 0
                dif  = last.get('macd_dif') or 0
                bar  = last.get('macd_bar') or 0
                bar_d = last.get('macd_bar_delta') or 0
                dea  = last.get('macd_dea') or 0
                if rsi6 < 20:   rsi_tag = '🔴'
                elif rsi6 < 30: rsi_tag = '🟠'
                elif rsi6 > 70: rsi_tag = '⚪'
                else:           rsi_tag = ''
                if dif > 0 and dea <= 0:   macd_tag = '🟢金叉'
                elif dif > 0 and dea > 0:  macd_tag = '多头'
                elif dif < 0 and dea >= 0: macd_tag = '🔴死叉'
                else:                       macd_tag = '空头'
                same = f"{r.get('group_size', 1)} 只" if r.get('group_size', 1) > 1 else "—"
                f.write(f"| {r['name']} ({r['ts_code']}) | {rsi_tag}{rsi6:.1f} | {dif:.3f} | {bar:.3f} | {bar_d:.3f} | {macd_tag} | {r.get('signal', '—')} | {same} |\n")

        print(f'  ETF 汇总已追加到 {output_path}', flush=True)
