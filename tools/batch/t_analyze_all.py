"""
t-analyze --all 批量分析: 4-8 worker 并发 analyze+render, 逐只落 md + 汇总走 stdout

2026-09-03 v6.0 改造:
  - 不再偷偷调 sync_incremental (改走 /t-sync skill, 入口 tools/sync_data.py)
  - 缺数据时直接报"请先 /t-sync", 不再调单只兜底
  - 跑前用户先 `python -m tools.storage.sync --all-data`

2026-11-06 改造: blowout 段**完全不参与**批量跑
  - list_type=blowout 是"业绩暴增候选池", 用来筛的, 不是长期跟踪的票
  - --include-blowout flag 已删 (它能打开全跑 → 一次出 157 份详报, 把 docs/blowout/ 撑爆)
  - 要深挖某只 blowout 票: /t-analyze <code> 单只跑 (单只也不落 md, 报告走 stdout)
  - docs/blowout/ 目录已废弃, 历史产物由孤儿清理逻辑整个清掉

2026-11-06 改造: ETF MACD 扫描段整段下线
  - tools/batch/etf_macd_scan.py 已删, --no-etf flag 一并删
  - ETF 扫描由 /t-etf-wyckoff-a2m 单独承担, 本脚本只管持仓 + 自选

2026-11-09 改造: blowout 段**只跑威科夫 M 阶段**的票, 详报落 docs/blowout/
  - 之前 (2026-11-06) blowout 整段排除: 候选池 158 只全不落 md
  - 现在: 先用 tools/batch/blowout_markup_screen.py 筛出**当前日线 stage == Markup** 的票,
    只有这些票跑完整详报 → docs/blowout/analyze-{code}-{name}.md
  - 筛选口径跟 /t-analyze 详报末行同源 (生产侧 analyze_history → 3period.daily.stage),
    **不自己重算 stage**
  - blowout 段的 buy/sell 信号**单独打印**, 不混进持仓+自选的主汇总 (口径不污染)
"""
import sys, datetime, time, os
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
# 2026-11-06 删 --no-etf flag (ETF MACD 扫描整段下线, 见下方注释)
#   保留 parse_known_args 兼容老调用 `python -m ... t_analyze_all --all` (未知参数一律忽略)
# 2026-11-12 加 --prune-orphans: 孤儿 md 清理改成 **opt-in** (默认保留)
#   事故: 2026-11-12 用户让"刷新 docs/watchlist 下的 md", 跑本脚本默认把 65 份
#   不在 watchlist.json 的历史详报当成"孤儿"清掉了 —— 用户要的是重新生成, 不是删除。
#   孤儿清理本身有价值 (2026-09-15 加的), 但必须是**显式意图**, 不能是默认副作用。
_arg_parser = argparse.ArgumentParser(description="t-analyze --all 批量分析")
_arg_parser.add_argument("--prune-orphans", action="store_true",
                          help="清理 docs/{portfolio,watchlist,blowout}/ 里不在 watchlist.json "
                               "的历史孤儿 md (默认不删, 全部保留)")
_arg_parser.add_argument("--sw", action="store_true",
                          help="行业版批量: 给申万二级行业指数出分析报告 → docs/sw/")
_args, _unknown = _arg_parser.parse_known_args()
PRUNE_ORPHANS = _args.prune_orphans

# 2026-11-12: --sw → 行业版批量
# ⚠️ 必须用 **os.execvp 换进程**, 不能在本进程里 import+调用:
#    mp.spawn 的子进程会重新 import 父进程的 __main__ 模块
#    (python -m X → __main__ = X), 而**本模块没有 `if __name__ == '__main__'` 保护**
#    → 子进程会把整个个股批量又跑一遍 (实测: 个股批量在子进程里重跑, 输出串在一起)。
#    execvp 换掉进程后, __main__ 变成 t_analyze_sw (它**有**保护) → 干净。
if _args.sw:
    import os
    _rest = [a for a in sys.argv[1:] if a != "--sw"]
    os.execvp(sys.executable, [sys.executable, "-m", "tools.batch.t_analyze_sw", *_rest])


wl = DataStore.load_watchlist()['stocks']
# 2026-11-06: blowout 段整段排除 (候选池, 用来筛的, 不参与批量详报)
# 2026-11-09 改: blowout 段**只跑威科夫 M 阶段的票** —— 先筛 stage == Markup, 再出详报
#   筛选走 tools/batch/blowout_markup_screen.py (生产侧 analyze_history, 跟详报末行同源)
from tools.batch.blowout_markup_screen import screen_markup_stocks

stocks = [s for s in wl if s.get('list_type', '自选') != 'blowout']
blowout_pool = [s for s in wl if s.get('list_type', '自选') == 'blowout']
excluded = len(wl) - len(stocks)

# === blowout 段: 威科夫 M 阶段筛选 ===
blowout_hits = []
if blowout_pool:
    print(f'  🔎 blowout 候选池 {len(blowout_pool)} 只 → 筛威科夫 M 阶段 (日线 stage == Markup) ...',
          flush=True)
    _b_t0 = time.time()
    blowout_hits = screen_markup_stocks([s['code'] for s in blowout_pool],
                                         workers=int(os.environ.get('T_ANALYZE_WORKERS', '4')))
    print(f'     → 命中 {len(blowout_hits)} 只 M 阶段 ({time.time() - _b_t0:.1f}s)', flush=True)
    _hit_codes = {h['code'] for h in blowout_hits}
    blowout_stocks = [s for s in blowout_pool if s['code'] in _hit_codes]
else:
    blowout_stocks = []
if excluded:
    print(f'  ℹ️  blowout 候选池 {len(blowout_pool)} 只 → 只有 M 阶段的 {len(blowout_stocks)} 只出详报 '
          f'(落 docs/blowout/)', flush=True)

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
    if _lt == 'blowout':
        # 2026-11-09 改: blowout 段**只出 M 阶段的详报** → 期望文件 = 本次 M 命中名单
        #   (非 M 的票不落 md → 它们在 docs/blowout/ 的旧产物按孤儿清理移走)
        continue
    _sub = 'portfolio' if _lt == '持仓' else 'watchlist'
    _expected_by_sub[_sub].add(_name_for_file(_s))
for _s in blowout_stocks:
    _expected_by_sub['blowout'].add(_name_for_file(_s))

for sub in ('portfolio', 'watchlist', 'blowout'):
    if not PRUNE_ORPHANS:
        # 2026-11-12: 孤儿清理改 opt-in —— 默认**一律保留**历史详报
        #   (用户要"刷新 md", 不是"删 md"; 删是不可逆动作, 必须是显式意图)
        continue
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

if not PRUNE_ORPHANS:
    _orphan_n = sum(len(list(Path(f'docs/{s}').glob('analyze-*.md'))) -
                    len(_expected_by_sub[s])
                    for s in ('portfolio', 'watchlist', 'blowout')
                    if Path(f'docs/{s}').exists())
    if _orphan_n > 0:
        print(f'  ℹ️  发现 {_orphan_n} 份历史孤儿 md (不在 watchlist.json) —— 已保留 '
              f'(要清理加 --prune-orphans)', flush=True)

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
    # 2026-11-09 改: blowout 段只跑 M 阶段的票 (外面已筛过), 详报落 docs/blowout/
    lt = s.get('list_type', '自选')
    if lt == '持仓':
        subdir = 'portfolio'
        list_type_label = '持仓'
    elif lt == 'blowout':
        subdir = 'blowout'
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
            ctx, lookback=factor_history_lookback(code), history=history
        )
        # 2026-11-11 加: 行业内地位 (龙头判定) — 行业排名 + 涨幅排名
        #   ETF 无行业 → 返回 {} → 渲染成"无行业数据"
        try:
            from tools.analysis.industry_position import compute_industry_position
            data.industry_position = compute_industry_position(
                code, industry=getattr(ctx, "industry", None),
                trading_days=max(_factor_lookback, 120),
            )
        except Exception:
            data.industry_position = None
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
            has_sig = '⭐' if sig_fmtd else ''
            for _, detail, direction in sigs:
                signals.append((direction, code_tagged, name, detail))
            table_row = (code_tagged, name, (r.get('wyckoff_daily') or '?')[:10],
                         f"{r.get('ma_dev_daily') or 0:+.1f}%",
                         ' '.join(sig_fmtd) if sig_fmtd else '—', has_sig)
        else:
            table_row = (code_tagged, name, '?', '0.0%', '—', '')

        elapsed = time.time() - t0
        return ('ok', code, {'md_path': md_path, 'elapsed': elapsed, 'len': len(md),
                              'signals': signals, 'table_row': table_row,
                              'list_type': list_type_label, 'code': code})
    except Exception as e:
        return ('err', code, str(e)[:200])


# 并发跑 (跟 refresh_all 一致, 默认 4 worker)
WORKERS = int(os.environ.get('T_ANALYZE_WORKERS', '4'))
print(f'\n[并发] {WORKERS} workers', flush=True)

from concurrent.futures import ThreadPoolExecutor, as_completed


def _run_pool(pool_stocks, pool_label):
    """跑一批票, 返回 (ok_results, errs)

    2026-11-09: 抽成函数 —— blowout 段 (M 阶段票) 要单独跑一遍,
    信号单独汇总, 不混进持仓+自选的主汇总。
    """
    ok_results, pool_errs = [], []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(process_one, s): s for s in pool_stocks}
        for i, fut in enumerate(as_completed(futs), 1):
            status, code, payload = fut.result()
            if status == 'ok':
                ok_results.append(payload)
                _md_name = payload["md_path"].name if payload.get("md_path") else "(未落盘)"
                print(f'  [{pool_label} {i}/{len(pool_stocks)}] ✅ {code} -> {_md_name} '
                      f'({payload["elapsed"]:.1f}s, {payload["len"]:,}chars)', flush=True)
            else:
                pool_errs.append((code, payload))
                print(f'  [{pool_label} {i}/{len(pool_stocks)}] ❌ {code}: {payload}', flush=True)
    return ok_results, pool_errs


results = []
results, errs = _run_pool(stocks, '持仓+自选')

# === blowout 段: 只跑 M 阶段的票, 详报落 docs/blowout/ ===
blowout_results = []
if blowout_stocks:
    print(f'\n=== blowout M 阶段详报 ({len(blowout_stocks)} 只 → docs/blowout/) ===', flush=True)
    blowout_results, blowout_errs = _run_pool(blowout_stocks, 'blowout')
    errs.extend(blowout_errs)

# 汇总 → stdout (不落汇总 md 文件)
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

# 2026-09-02 加: 按 list_type 分类统计 (持仓/自选/ROC_EY初筛), 2026-09-09 Magic→ROC_EY
# 2026-11-06: 汇总 md 删了, 这个分布从 md 头挪到 stdout —— 信息不能跟着文件一起丢
from collections import Counter
_lt_counter = Counter(s.get("list_type", "自选") for s in stocks)
_lt_summary = " | ".join(f"{k} {v}" for k, v in sorted(_lt_counter.items(), key=lambda x: -x[1]))

print(f'\n=== 完成 ===')
print(f'扫描: {len(stocks)} 只 ({_lt_summary})')
print(f'MD生成: {md_written}/{len(stocks)}')
print(f'有今日信号: {sum(1 for r in all_table_rows if r[5]=="⭐")}只')
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

# === blowout 段汇总 (2026-11-09) ===
# 信号**单独打印** —— 不混进上面持仓+自选的主汇总 (两个口径不能混)
if blowout_stocks:
    _b_buy, _b_sell = [], []
    for r in blowout_results:
        for direction, code, name, detail in r['signals']:
            (_b_buy if direction == 'buy' else _b_sell).append((code, name, detail))
    _b_md = sum(1 for r in blowout_results if r.get('md_path'))
    print(f'\n=== blowout M 阶段 ({len(blowout_stocks)} 只候选 → {len(blowout_hits)} 只 M 阶段) ===')
    print(f'MD生成: {_b_md}/{len(blowout_stocks)} (→ docs/blowout/)')
    print(f'有今日信号: {sum(1 for r in blowout_results if r["table_row"][5] == "⭐")}只')
    for _h in blowout_hits:
        _row = next((r for r in blowout_results if r.get('code') == _h['code']), None)
        _wk = _h.get('stage_weekly', '?')
        print(f'  🔵 {_h["code"]} {_row["table_row"][1] if _row else "?"} | '
              f'日线 M / 周线 {_wk} | MA偏离 {_row["table_row"][3] if _row else "—"} | '
              f'{" ".join(_row["table_row"][4].split()) if _row else "—"}')
    if _b_buy:
        print(f'\n  --- blowout buy ({len(_b_buy)}) ---')
        for code, name, detail in _b_buy:
            print(f'  🟢 {code} {name} | {detail}')
    if _b_sell:
        print(f'\n  --- blowout sell ({len(_b_sell)}) ---')
        for code, name, detail in _b_sell:
            print(f'  🔴 {code} {name} | {detail}')


# === 行业威科夫 Markup 汇总 (2026-11-12 加, 用户要求"跑完总结哪些行业现在是 Markup") ===
# 数据源: 行业威科夫 cache (只读) —— 跟个股报告「因子历史」里的行业威科夫列同源
try:
    from tools.analysis.industry_stage import (
        format_markup_summary,
        summarize_markup_industries,
    )
    print()
    print(format_markup_summary(summarize_markup_industries()))
except Exception as _e:
    print(f'\n=== 行业威科夫 Markup 汇总 ===\n  ⚠️ 读取失败: {type(_e).__name__}: {_e}')


# 2026-11-06 删 ETF MACD 扫描段 (整段下线)
#   - tools/batch/etf_macd_scan.py 已删 (safe_delete → 回收站)
#   - --no-etf flag 一并删 (没东西可跳了)
#   - docs/etf-macd/ 产物目录已清空
#   ETF 扫描能力由 /t-etf-wyckoff-a2m 承担 (候选池口径仍在 tools/batch/etf_universe.py)
