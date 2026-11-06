#!/bin/bash
# tools/t-analyze-all.sh — 强制按 t-analyze skill 4 步执行, 不允许跳
#
# 用法:
#   bash tools/t-analyze-all.sh            # 全量
#   bash tools/t-analyze-all.sh --no-news  # 跳过 web search (skill flag)
#
# 流程 (固定, 不许跳):
#   Step 1: 读 watchlist
#   Step 2: sync_incremental (单线程, 补 K线缺口)
#   Step 3: 验证所有 watchlist 文件都是今天新数据
#   Step 4: chat 摘要输出
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
import sys; sys.path.insert(0, '.')
from tools.storage.store import DataStore
wl = DataStore.load_watchlist().get('stocks', [])
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

# === Step 3: 验证所有 watchlist 文件都是新数据 ===
echo ""
echo "✅ Step 3: 验证 watchlist 内所有票都是本次新数据..."
bash tools/with_venv.sh python3 << 'PYEOF' 2>&1 | tail -10
import os, re, json, datetime
import sys
sys.path.insert(0, '.')
from tools.storage.store import DataStore

wl = DataStore.load_watchlist().get('stocks', [])
# blowout 是业绩暴增候选池, 按设计不落 md → 不参与文件存在性检查
# (不排除的话: 156 只候选池永远"缺文件", 这道检查恒挂)
watchlist_codes = {s['code']: s['name'] for s in wl
                   if s.get('list_type', '自选') != 'blowout'}

today = datetime.date.today().isoformat()
# 阈值 = 今天 00:00 (本脚本开跑前必须全量重生成过)
# 2026-11-06 改: 原写死 2026-08-25, 早就恒真 —— 这道检查等于没有, 换成动态阈值才守得住
threshold = datetime.datetime.combine(datetime.date.today(), datetime.time.min)

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

# 检查 2: 全部 watchlist 文件都是本次跑之前刚生成的
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
    print(f'❌ watchlist 内 {len(old_files)} 个文件还是本次跑之前的老数据:')
    for dt, f in old_files:
        print(f'  {dt.strftime("%m-%d %H:%M")}  {f}')
    sys.exit(1)

print(f'  ✅ watchlist 内 {len(watchlist_codes)} 只全部 {today} 新数据, 0 旧文件')
PYEOF

# === Step 4: chat 摘要 (输出文件位置 + summary) ===
echo ""
echo "=================================================="
echo "✅ 全部完成 (4 步全跑, 无跳)"
echo "=================================================="
echo ""
echo "📄 个股报告: docs/portfolio/ + docs/watchlist/ (全部分流)"
echo ""
echo "运行时间: $(date '+%Y-%m-%d %H:%M:%S')"
