#!/bin/bash
# tools/refresh_all.sh — 一键全刷 watchlist + 渲染报告 (v2, 2026-07-22 加速)
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
# 2026-08-15: 引入 — 修 factor_history.py 字典字面量塞赋值的 bug 时,57 份 md 报告残留"历史计算失败"残行
# 因为 render 阶段没依赖 factor_history (只有 batch_summary 才 import), pipeline 走完才在尾部炸
# 防护: 启动时 import 所有 render/batch 必用的关键模块, 任何一个失败立即 exit 1
# 任何 .py 改动 (factor_history / analysis_engine / report_renderer / batch_summary) 必须:
#   1. 本地 `python -c "import <module>"` 通过
#   2. 或者重跑此 smoke test 通过
SMOKE_MODULES=(
    "tools.analysis.factor_history"   # batch_summary 强依赖
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

# 拿 codes
if [ -z "$CODES" ]; then
    CODES=$(python3 -c "
import json
print(' '.join(s['code'] for s in json.load(open('data/watchlist.json'))['stocks']))
")
fi

TOTAL=$(echo $CODES | wc -w | xargs)
START=$(date +%s)

echo "🔄 全刷 $TOTAL 只票, $WORKERS 并发"
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="

# 阶段 1: dump (4 并发)
DUMP_DIR=$(mktemp -d)
DUMP_OK=()
DUMP_FAIL=()

dump_one() {
    local code=$1
    # 8-22 重写: sync_stock.py 走 DataStore + parquet, 不写 data/_old_d/{code}.json
    # 判定成功 = 命令退出码 0 + 输出含 "K线: N 根"
    if bash tools/with_venv.sh python3 -m tools.sync_stock "$code" >"$DUMP_DIR/${code}.log" 2>&1; then
        if grep -q "K线: " "$DUMP_DIR/${code}.log" 2>/dev/null; then
            echo "DUMP_OK:$code"
        else
            echo "DUMP_FAIL:$code (无 K线 输出)"
        fi
    else
        echo "DUMP_FAIL:$code $(tail -1 "$DUMP_DIR/${code}.log" 2>/dev/null)"
    fi
}
export -f dump_one
export DUMP_DIR
export ROOT

# xargs -P N 并发
printf '%s\n' $CODES | xargs -I{} -P "$WORKERS" -n 1 bash -c 'dump_one "$@"' _ {} > "$DUMP_DIR/results.txt" 2>&1 || true

# 统计
OK_COUNT=$(grep -c "^DUMP_OK:" "$DUMP_DIR/results.txt" 2>/dev/null | head -1 || echo 0)
OK_COUNT=${OK_COUNT:-0}
FAIL_COUNT=$(grep -c "^DUMP_FAIL:" "$DUMP_DIR/results.txt" 2>/dev/null | head -1 || echo 0)
FAIL_COUNT=${FAIL_COUNT:-0}
DUMP_END=$(date +%s)
echo ""
echo "📦 dump 阶段: ✅ $OK_COUNT / ❌ $FAIL_COUNT, 耗时 $((DUMP_END-START)) 秒"
# 失败详情
if [ "$FAIL_COUNT" -gt 0 ]; then
    grep "^DUMP_FAIL:" "$DUMP_DIR/results.txt" | head -3 | sed 's/^/    /'
fi

# 阶段 2: render (4 并发, 只 render 成功的)
if [ -z "$NO_RENDER" ] && [ "$OK_COUNT" -gt 0 ]; then
    OK_CODES=$(grep "^DUMP_OK:" "$DUMP_DIR/results.txt" | cut -d: -f2)
    
    RENDER_DIR=$(mktemp -d)
    RENDER_OK=0
    RENDER_FAIL=0
    
    render_one() {
        local code=$1
        # 2026-07-25 收敛: 用 sync_stock.py --render (内部调新 renderer report_renderer.render_report)
        if bash tools/with_venv.sh python3 -m tools.sync_stock "$code" --render >"$RENDER_DIR/${code}.log" 2>&1; then
            echo "RENDER_OK:$code"
        else
            echo "RENDER_FAIL:$code"
        fi
    }
    export -f render_one
    export RENDER_DIR
    export ROOT
    
    echo ""
    echo "📖 渲染报告 (4 并发)..."
    printf '%s\n' $OK_CODES | xargs -I{} -P "$WORKERS" -n 1 bash -c 'render_one "$@"' _ {} > "$RENDER_DIR/results.txt" 2>&1 || true

    RENDER_OK=$(grep -c "^RENDER_OK:" "$RENDER_DIR/results.txt" 2>/dev/null | head -1 || echo 0)
    RENDER_OK=${RENDER_OK:-0}
    RENDER_FAIL=$(grep -c "^RENDER_FAIL:" "$RENDER_DIR/results.txt" 2>/dev/null | head -1 || echo 0)
    RENDER_FAIL=${RENDER_FAIL:-0}
    # render 失败详情
    if [ "$RENDER_FAIL" -gt 0 ]; then
        echo "    render 失败列表:"
        grep "^RENDER_FAIL:" "$RENDER_DIR/results.txt" | sed 's/^/      /'
    fi
    rm -rf "$RENDER_DIR"
fi

END=$(date +%s)
echo "=================================================="
echo "完成: dump ✅ $OK_COUNT / ❌ $FAIL_COUNT"
[ -z "$NO_RENDER" ] && echo "      render ✅ $RENDER_OK / ❌ $RENDER_FAIL"
echo "      总耗时: $((END-START)) 秒 (vs v1 串行约 $((TOTAL*10)) 秒)"
SPEEDUP=$((TOTAL*10*100/(END-START+1)))
echo "      加速: ~${SPEEDUP}x"
echo "结束时间: $(date '+%Y-%m-%d %H:%M:%S')"

# 2026-07-31: 阶段 3 - batch md (直接读 docs/analyze-*.md 最后一行, 0 重算)
if [ -z "$NO_RENDER" ] && [ "$RENDER_OK" -gt 0 ]; then
    echo ""
    echo "📋 生成 batch md (直接读 57 份 analyze md 文件, 0 重算)..."
    bash tools/with_venv.sh python3 -m tools.batch.batch_summary 2>&1 | tail -5
fi

rm -rf "$DUMP_DIR"
