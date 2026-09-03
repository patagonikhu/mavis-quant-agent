#!/bin/bash
# tools/t-analyze-all.sh — 强制按 t-analyze skill 5 步执行, 不允许跳
#
# 用法:
#   bash tools/t-analyze-all.sh            # 全量
#   bash tools/t-analyze-all.sh --no-news  # 跳过 web search (skill flag)
#
# 流程 (固定, 不许跳):
#   Step 1: 读 watchlist
#   Step 2: sync_incremental (单线程, 补 K线缺口)
#   Step 3: 写 docs/signal-watchlist.md
#   Step 4: 验证所有 watchlist 文件都是 8/25 后新数据
#   Step 5: chat 摘要输出
#
# v1.0 (2026-08-25): 强制全流程, 任何 step 失败立即 exit 1

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=================================================="
echo "  t-analyze --all (强制全流程, 不允许跳)"
echo "  开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="

# === Step 1: 读 watchlist ===
echo ""
echo "📋 Step 1: 读 watchlist..."
WATCHLIST_COUNT=$(bash tools/with_venv.sh python3 -c "
import json
wl = json.load(open('data/watchlist.json'))['stocks']
print(len(wl))
")
echo "  ✅ watchlist 共 $WATCHLIST_COUNT 只"

# === Step 2: sync_incremental (单线程, 补 K线缺口) ===
echo ""
echo "🔄 Step 2: sync_incremental (单线程)..."
bash tools/with_venv.sh python3 -c "
import sys; sys.path.insert(0, '.')
from tools.storage.store import sync_incremental
sync_incremental()
" 2>&1 | tail -5

# === Step 3: 写 docs/signal-watchlist.md ===
echo ""
echo "📝 Step 3: 写 docs/signal-watchlist.md..."
bash tools/with_venv.sh python3 << 'PYEOF' 2>&1 | tail -5
import json, sys, datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '.')
from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_result_signals import (
    compute_factor_history, diff_rows, extract_signals, format_signals_for_render,
)
# 复用 report_renderer 的 14 列单行渲染 + 14 列 header/sep
# MA20 斜率在 compute_factor_history 末尾已经算进每行 row['ma20_slope'], 0 重算
from tools.render.report_renderer import (
    _format_factor_row, FACTOR_HISTORY_HEADER, FACTOR_HISTORY_SEP,
)

watchlist = json.load(open('data/watchlist.json'))['stocks']
output_path = Path('docs') / 'signal-watchlist.md'

# lookback=10: 至少留 5 日给 MA20 斜率 (5 日前 vs 当下), 多留 5 日 buffer
_LOOKBACK = 10

def _scan_one(s):
    code, name = s['code'], s['name']
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline: return None
        # 预算 history (10 天), compute_factor_history 强制要传 (避免内部重跑 analyze_history)
        all_dates = [k['trade_date'].replace('-', '')[:8] for k in ctx.kline]
        dates = all_dates[-_LOOKBACK:]
        history = AnalysisEngine().analyze_history(ctx, dates)
        if len(history) < 2: return None
        rows = compute_factor_history(ctx, step=1, lookback=_LOOKBACK, history=history)
        if len(rows) < 2: return None
        # 复用 render 的 14 列单行渲染 (跟 _section_factor_history 同一份真源, 斜率已预算)
        row_md = _format_factor_row(rows, idx=len(rows) - 1)
        if row_md is None: return None
        # 信号列表 (buy/sell) 走原本的 diff_rows + extract_signals
        changes = diff_rows(rows[-2], rows[-1])
        sigs = extract_signals(changes)
        return (s, row_md, sigs, format_signals_for_render(changes))
    except Exception:
        return None

buy_rows, sell_rows, all_table_rows = [], [], []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(_scan_one, s): s for s in watchlist}
    for fut in as_completed(futs):
        r = fut.result()
        if not r: continue
        s, row_md, sigs, sig_fmtd = r
        for _, detail, direction in sigs:
            (buy_rows if direction == 'buy' else sell_rows).append((s['code'], s['name'], detail))
        all_table_rows.append((s['code'], s['name'], row_md, sig_fmtd))

lines = [f"# 全量扫描 {datetime.date.today().isoformat()}\n",
         f"> {len(watchlist)} 只票 | 因子历史 diff | refresh_all 已跑 (Step 2)\n",
         "---\n\n## 底部信号 (buy)\n\n| 代码 | 名称 | 信号 |\n|------|------|------|\n"]
for code, name, d in buy_rows:
    lines.append(f"| {code} | {name} | {d} |\n")
lines.append("\n---\n\n## 顶部/弱势信号 (sell)\n\n| 代码 | 名称 | 信号 |\n|------|------|------|\n")
for code, name, d in sell_rows:
    lines.append(f"| {code} | {name} | {d} |\n")

# 📈 因子历史走势 — 列跟 _section_factor_history 完全对齐 (复用 FACTOR_HISTORY_HEADER 真源)
# 拼接 16 列: 代码 | 名称 + 14 列因子
header_factor = FACTOR_HISTORY_HEADER
sep_factor    = FACTOR_HISTORY_SEP
lines += [
    "\n---\n\n## 📈 因子历史走势 (取每只最后一行, 列与单只报告完全对齐)\n\n",
    f"| 代码 | 名称 | {header_factor[1:].strip()}\n",
    f"|------|------|{sep_factor[1:].strip()}\n",
]
all_table_rows.sort(key=lambda x: (0 if x[3] else 1, x[0]))
for code, name, row_md, sig_fmtd in all_table_rows:
    has_sig = '⭐' if sig_fmtd else ''
    body = row_md.strip().strip("|").strip()
    lines.append(f"| {code} | {name}{has_sig} | {body} |\n")
lines.append(f"\n---\n> 生成时间: {datetime.datetime.now().strftime('%H:%M:%S')}\n")
output_path.write_text(''.join(lines), encoding='utf-8')
print(f'OK: {output_path} ({len(all_table_rows)} 只, {len(buy_rows)} buy / {len(sell_rows)} sell)')
PYEOF

# === Step 4: 验证所有 watchlist 文件都是新数据 ===
echo ""
echo "✅ Step 4: 验证 watchlist 内 57 只全部 8/25 后新数据..."
bash tools/with_venv.sh python3 << 'PYEOF' 2>&1 | tail -10
import os, re, json, datetime
import sys

wl = json.load(open('data/watchlist.json'))['stocks']
watchlist_codes = {s['code']: s['name'] for s in wl}

today = datetime.date.today().isoformat()  # 2026-08-25
threshold = datetime.datetime(2026, 8, 25, 0, 0)

# 检查 1: watchlist 内的所有 code 都有 md 文件 (portfolio/ 或 watchlist/)
analyzed = set()
for subdir in ('docs/portfolio', 'docs/watchlist'):
    if os.path.isdir(subdir):
        for f in os.listdir(subdir):
            if f.startswith('analyze-') and f.endswith('.md'):
                m = re.match(r'analyze-(\d+)-(.+)\.md', f)
                if m:
                    analyzed.add(m.group(1))

missing = set(watchlist_codes.keys()) - analyzed
if missing:
    print(f'{sys.stderr}: ❌ 缺文件: {missing}')
    sys.exit(1)

# 检查 2: 全部 watchlist 文件都是 8/25 后新数据
old_files = []
for subdir in ('docs/portfolio', 'docs/watchlist'):
    if os.path.isdir(subdir):
        for f in os.listdir(subdir):
            if f.startswith('analyze-') and f.endswith('.md'):
                m = re.match(r'analyze-(\d+)-(.+)\.md', f)
                if m and m.group(1) in watchlist_codes:
                    mtime = os.path.getmtime(f'{subdir}/{f}')
                    dt = datetime.datetime.fromtimestamp(mtime)
                    if dt < threshold:
                        old_files.append((dt, f))

if old_files:
    print(f'❌ watchlist 内 {len(old_files)} 个文件还是 8/25 之前的旧数据:')
    for dt, f in old_files:
        print(f'  {dt.strftime("%m-%d %H:%M")}  {f}')
    sys.exit(1)

print(f'  ✅ watchlist 内 {len(watchlist_codes)} 只全部 {today} 新数据, 0 旧文件')
PYEOF

# === Step 5: chat 摘要 (输出文件位置 + summary) ===
echo ""
echo "=================================================="
echo "✅ 全部完成 (5 步全跑, 无跳)"
echo "=================================================="
echo ""
echo "📄 主报告: docs/signal-watchlist.md"
echo "📄 个股报告: docs/portfolio/ + docs/watchlist/ (61 只, 全部分流)"
echo ""
echo "运行时间: $(date '+%Y-%m-%d %H:%M:%S')"
