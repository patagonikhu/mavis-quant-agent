#!/bin/bash
# tools/refresh_all.sh — 一键全刷 watchlist + 渲染报告 (v3, 2026-08-27 list_type 分流)
#
# v3 更新:
#   - 按 list_type 分流输出: 持仓→docs/portfolio/, 自选→docs/watchlist/
#   - 一次 compute_factor_history, 结果复用 (render + out_results 不重复)
#
# 性能优化 (v2):
#   - dump 阶段: 4 进程并发 (Tushare 全接口 80/分 内, 实际跑 6-7 段/秒)
#   - render 阶段: 4 进程并发
#   - 流水线: dump 完一只立即 render, 不等全 dump 完
#   - 总耗时: 56 只 ≈ 2 分钟 (vs v1 串行 6-8 分钟, 3-4x 加速)
#
# 用途:
#   bash tools/refresh_all.sh                 # 刷全部 watchlist (默认 4 并发)
#   bash tools/refresh_all.sh 300274 000725   # 刷指定 code
#   bash tools/refresh_all.sh --workers 8     # 8 并发 (小心 Tushare 单接口 100/分)
#   bash tools/refresh_all.sh --no-render     # 跳过渲染
#
# 性能:
#   - dump: 56 * 7.5s / 4 = 105s ≈ 1.75 min
#   - render: 56 * 2.1s / 4 = 30s ≈ 0.5 min
#   - 总: ~2 min (4 并发)

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

# === Smoke test (2026-08-15 v3) ===
# 2026-08-15: 引入 — 修 analysis_result_signals.py 字典字面量塞赋值的 bug 时,57 份 md 报告残留"历史计算失败"残行
# 因为 render 阶段没依赖 factor_history (只有 batch_summary 才 import), pipeline 走完才在尾部炸
# 防护: 启动时 import 所有 render/batch 必用的关键模块, 任何一个失败立即 exit 1
# 任何 .py 改动 (factor_history / analysis_engine / report_renderer / batch_summary) 必须:
#   1. 本地 `python -c "import <module>"` 通过
#   2. 或者重跑此 smoke test 通过
SMOKE_MODULES=(
    "tools.analysis.analysis_result_signals"   # batch_summary 强依赖
    "tools.analysis.analysis_engine"  # dump + render 强依赖
    "tools.render.report_renderer"    # render 强依赖
    "tools.batch.batch_summary"       # batch 阶段
)
for mod in "${SMOKE_MODULES[@]}"; do
    if ! bash tools/with_venv.sh python3 -c "import ${mod}" 2>/dev/null; then
        echo "❌ Smoke test FAILED: ${mod} 不可 import"
        echo "   修代码后再跑: bash tools/with_venv.sh python3 -c 'import ${mod}'"
        exit 1
    fi
done
echo "✅ Smoke test: ${#SMOKE_MODULES[@]} 个核心模块 import 通过"

# 参数解析
WORKERS=4
NO_RENDER=""
CODES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --no-render)
            NO_RENDER=1
            shift
            ;;
        --help|-h)
            echo "用法: refresh_all.sh [--workers N] [--no-render] [code1 code2 ...]"
            echo "  --workers N: 并发数 (默认 4)"
            echo "  --no-render: 跳过报告渲染"
            echo "  无 code 参数: 刷 watchlist 全部"
            exit 0
            ;;
        *)
            CODES="$CODES $1"
            shift
            ;;
    esac
done
CODES=$(echo $CODES | xargs)  # trim

# 拿 codes + list_type map
LIST_TYPE_MAP=$(mktemp)
if [ -z "$CODES" ]; then
    python3 -c "
import json, sys
wl = json.load(open('data/watchlist.json'))['stocks']
print(' '.join(s['code'] for s in wl))
map_d = {s['code']: s.get('list_type', '自选') for s in wl}
json.dump(map_d, open('$LIST_TYPE_MAP', 'w'), ensure_ascii=False)
"
    CODES=$(python3 -c "
import json
wl = json.load(open('data/watchlist.json'))['stocks']
print(' '.join(s['code'] for s in wl))
")
else
    # 指定 code 时, list_type 默认为自选
    for c in $CODES; do
        python3 -c "import json; wl=json.load(open('data/watchlist.json'))['stocks']; m={s['code']:s.get('list_type','自选') for s in wl}; print(m.get('$c','自选'))" >> "$LIST_TYPE_MAP"
    done
fi

TOTAL=$(echo $CODES | wc -w | xargs)
START=$(date +%s)

echo "🔄 全刷 $TOTAL 只票, $WORKERS 并发"
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="

# 预建输出目录
mkdir -p docs/portfolio docs/watchlist

# 阶段 0: 预同步 (CLAUDE.md 铁律: sync_incremental 单线程先跑, 4 worker 再各取所需)
echo ""
echo "🔄 预同步: sync_incremental (单线程, 全市场增量补齐)..."
bash tools/with_venv.sh python3 -c "from tools.kline_history_backfill import sync_incremental; sync_incremental()" 2>&1 | tail -3
echo ""

# 阶段 1: dump + render (4 并发)
DUMP_DIR=$(mktemp -d)

process_one() {
    local code=$1
    # 从 LIST_TYPE_MAP 读 list_type
    local list_type
    list_type=$(python3 -c "import json; m=json.load(open('$LIST_TYPE_MAP')); print(m.get('$code','自选'))")
    local subdir
    [ "$list_type" = "持仓" ] && subdir="portfolio" || subdir="watchlist"

    # analyze + render 合一，阶段 0 已做 sync_incremental，这里 0 网络
    if bash tools/with_venv.sh python3 -c "
from tools.kline_store import DataStore
from tools.analysis.analysis_data import AnalysisData
from tools.analysis.analysis_result_signals import compute_factor_history
from tools.render.report_renderer import render_report
from pathlib import Path
code = '$code'
subdir = '$subdir'
ctx = DataStore.get_ctx(code)
if not ctx.kline:
    print(f'⚠️ {code} 无K线')
    exit(1)
print(f'  - K线: {len(ctx.kline)} 根')
# 一次 compute_factor_history，结果存 out_results（render + 信号提取共享）
out = {}
rows = compute_factor_history(ctx, step=1, lookback=120, out_results=out)
last_date = ctx.kline[-1]['trade_date'].replace('-', '')[:8]
result = out.get(last_date) or (out[max(out)] if out else None)
if result is None:
    print(f'⚠️ {code} 分析结果为空'); exit(1)
print(f'  - 场景: {result.scene}')
if '$NO_RENDER' != '1':
    data = AnalysisData.from_result(ctx, result)
    data.factor_history_rows = rows
    md = render_report(data)
    name = ctx.name or code
    p = Path('docs') / subdir / f'analyze-{code}-{name}.md'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding='utf-8')
    print(f'  - 报告: {len(md)} chars → {p}')
" >"$DUMP_DIR/${code}.log" 2>&1; then
        if grep -q "K线: " "$DUMP_DIR/${code}.log" 2>/dev/null; then
            echo "DUMP_OK:$code"
        else
            echo "DUMP_FAIL:$code (无 K线 输出)"
        fi
    else
        echo "DUMP_FAIL:$code $(tail -1 "$DUMP_DIR/${code}.log" 2>/dev/null)"
    fi
}
export -f process_one
export DUMP_DIR NO_RENDER LIST_TYPE_MAP ROOT

# xargs -P N 并发
printf '%s\n' $CODES | xargs -I{} -P "$WORKERS" -n 1 bash -c 'process_one "$@"' _ {} > "$DUMP_DIR/results.txt" 2>&1 || true

# 统计
OK_COUNT=$(grep -c "^DUMP_OK:" "$DUMP_DIR/results.txt" 2>/dev/null | head -1 || echo 0)
OK_COUNT=${OK_COUNT:-0}
FAIL_COUNT=$(grep -c "^DUMP_FAIL:" "$DUMP_DIR/results.txt" 2>/dev/null | head -1 || echo 0)
FAIL_COUNT=${FAIL_COUNT:-0}
DUMP_END=$(date +%s)
echo ""
echo "📦 analyze+render: ✅ $OK_COUNT / ❌ $FAIL_COUNT, 耗时 $((DUMP_END-START)) 秒"
if [ "$FAIL_COUNT" -gt 0 ]; then
    grep "^DUMP_FAIL:" "$DUMP_DIR/results.txt" | head -3 | sed 's/^/    /'
fi

RENDER_OK=$OK_COUNT
RENDER_FAIL=$FAIL_COUNT

END=$(date +%s)
echo "=================================================="
echo "完成: dump ✅ $OK_COUNT / ❌ $FAIL_COUNT"
[ -z "$NO_RENDER" ] && echo "      render ✅ $RENDER_OK / ❌ $RENDER_FAIL"
echo "      总耗时: $((END-START)) 秒 (vs v1 串行约 $((TOTAL*10)) 秒)"
echo ""
echo "📁 报告目录:"
echo "  持仓: docs/portfolio/  ($(find docs/portfolio -name 'analyze-*.md' 2>/dev/null | wc -l | xargs) 只)"
echo "  自选: docs/watchlist/  ($(find docs/watchlist -name 'analyze-*.md' 2>/dev/null | wc -l | xargs) 只)"
SPEEDUP=$((TOTAL*10*100/(END-START+1)))
echo "      加速: ~${SPEEDUP}x"
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')"

# batch md (读 docs/portfolio/ 和 docs/watchlist/ 的 md 文件, 0 重算)
if [ -z "$NO_RENDER" ] && [ "$RENDER_OK" -gt 0 ]; then
    echo ""
    echo "📋 生成 batch md..."
    bash tools/with_venv.sh python3 -m tools.batch.batch_summary 2>&1 | tail -5
fi

rm -f "$LIST_TYPE_MAP"
rm -rf "$DUMP_DIR"
