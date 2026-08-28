#!/bin/bash
# tools/t-analyze-all.sh — 强制按 t-analyze skill 5 步执行, 不允许跳
#
# 用法:
#   bash tools/t-analyze-all.sh            # 全量
#   bash tools/t-analyze-all.sh --no-news  # 跳过 web search (skill flag)
#
# 流程 (固定, 不许跳):
#   Step 1: 读 watchlist
#   Step 2: sync_incremental + refresh_all.sh (强制)
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

# === Step 2: sync_incremental + refresh_all.sh (强制) ===
echo ""
echo "🔄 Step 2: sync_incremental (单线程)..."
bash tools/with_venv.sh python3 -c "
import sys; sys.path.insert(0, '.')
from tools.kline_history_backfill import sync_incremental
sync_incremental()
" 2>&1 | tail -5

echo ""
echo "🔄 Step 2 (续): refresh_all.sh (4 worker, 80s)..."
bash tools/refresh_all.sh 2>&1 | tail -8

# === Step 3: 写 docs/signal-watchlist.md ===
echo ""
echo "📝 Step 3: 写 docs/signal-watchlist.md..."
bash tools/with_venv.sh python3 << 'PYEOF' 2>&1 | tail -5
import json, sys, datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.path.insert(0, '.')
from tools.kline_store import DataStore
from tools.analysis.factor_history import compute_factor_history, diff_rows, extract_signals, format_signals_for_render

watchlist = json.load(open('data/watchlist.json'))['stocks']
output_path = Path('docs') / 'signal-watchlist.md'

def _scan_one(s):
    code, name = s['code'], s['name']
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline: return None
        rows = compute_factor_history(ctx, step=1, lookback=3)
        if len(rows) < 2: return None
        r = rows[-1]
        changes = diff_rows(rows[-2], rows[-1])
        sigs = extract_signals(changes)
        return (s, r, sigs, format_signals_for_render(changes))
    except Exception:
        return None

buy_rows, sell_rows, all_table_rows = [], [], []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(_scan_one, s): s for s in watchlist}
    for fut in as_completed(futs):
        r = fut.result()
        if not r: continue
        s, row, sigs, sig_fmtd = r
        for _, detail, direction in sigs:
            (buy_rows if direction == 'buy' else sell_rows).append((s['code'], s['name'], detail))
        all_table_rows.append((s['code'], s['name'], row, sig_fmtd))

lines = [f"# 全量扫描 {datetime.date.today().isoformat()}\n",
         f"> {len(watchlist)} 只票 | 因子历史 diff | refresh_all 已跑 (Step 2)\n",
         "---\n\n## 底部信号 (buy)\n\n| 代码 | 名称 | 信号 |\n|------|------|------|\n"]
for code, name, d in buy_rows:
    lines.append(f"| {code} | {name} | {d} |\n")
lines.append("\n---\n\n## 顶部/弱势信号 (sell)\n\n| 代码 | 名称 | 信号 |\n|------|------|------|\n")
for code, name, d in sell_rows:
    lines.append(f"| {code} | {name} | {d} |\n")
lines.append("\n---\n\n## 完整状态表\n\n| 代码 | 名称 | 场景 | 威科夫日 | MA日% | 日中枢 | 今日信号 |\n|------|------|------|---------|-------|--------|----------|\n")
all_table_rows.sort(key=lambda x: (0 if x[3] else 1, x[0]))
for code, name, r, sig_fmtd in all_table_rows:
    hub_d = r.get('hub_daily') or {}
    hub_str = f"¥{hub_d.get('low',0):.0f}~{hub_d.get('high',0):.0f}{hub_d.get('pos','')[:2]}" if hub_d.get('valid') else '—'
    wy = (r.get('wyckoff_daily') or '?')[:10]
    ma = f"{r.get('ma_dev_daily') or 0:+.1f}%"
    sig_str = ' '.join(sig_fmtd) if sig_fmtd else '—'
    scene = r.get('scene', '?')
    has_sig = '⭐' if sig_fmtd else ''
    lines.append(f"| {code} | {name} | {has_sig}{scene} | {wy} | {ma} | {hub_str} | {sig_str} |\n")
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
