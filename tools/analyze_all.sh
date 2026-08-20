#!/bin/bash
# tools/analyze_all.sh — Phase 2: 跑全 watchlist 分析 (v5.6, 2026-07-29 拆分)
#
# 职责: 读 data/dump/{code}.json (raw 段), 跑 5方法×3周期 分析, 写回 analysis 段
#   - 不拉数据 (依赖 Phase 1 已经写好的 raw JSON)
#   - 跑缠论 / 威科夫 / SMC / 量价 / 多市场共振
#   - 跑 buy_sell_points / three_layer_position / exit_signals / stop_profit_loss
#   - 跑 peg_calc / dcf_calc / sector_overheat / five_categories
#   - 写回 JSON: 新增 analysis 字段, _section="merged"
#
# 性能:
#   - 4 进程并发 (纯 CPU, 不消耗 API, 4 并发安全)
#   - 单票 13s, 56 只 ≈ 3 分钟 (4 并发)
#   - 多次跑不消耗 Tushare 配额
#
# 用途:
#   bash tools/analyze_all.sh                   # 分析全 watchlist
#   bash tools/analyze_all.sh 300274 000725     # 分析指定 code
#   bash tools/analyze_all.sh --workers 8       # 8 并发
#
# 对应 (走 dump_data.py sentinel 参数):
#   python3 -c "from tools.dump_data import dump_code; dump_code('300274', analyze_only=True)"  # 串行分析 1 只
#
# 前置: 必须先跑 bash tools/pull_all.sh (Phase 1) 拉数据, 否则 JSON 不存在会 FAIL

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

# 参数解析
WORKERS=4
CODES=""
while [ $# -gt 0 ]; do
    case "$1" in
        --workers)
            WORKERS="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: analyze_all.sh [--workers N] [code1 code2 ...]"
            echo "  --workers N: 并发数 (默认 4)"
            echo "  无 code 参数: 分析 watchlist 全部"
            echo ""
            echo "Phase 2: 只跑分析, 不拉数据 (纯 CPU)"
            echo "前置: bash tools/pull_all.sh  (Phase 1)"
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

# 过滤: 只跑有 raw JSON 的票
EXISTING_CODES=""
MISSING_CODES=""
for code in $CODES; do
    if [ -f "data/dump/${code}.json" ]; then
        EXISTING_CODES="$EXISTING_CODES $code"
    else
        MISSING_CODES="$MISSING_CODES $code"
    fi
done
EXISTING_CODES=$(echo $EXISTING_CODES | xargs)
MISSING_CODES=$(echo $MISSING_CODES | xargs)

if [ -n "$MISSING_CODES" ]; then
    echo "⚠️  警告: 以下 ${#MISSING_CODES[@]} 只票没 raw JSON, 跳过:"
    echo "    $MISSING_CODES"
    echo "    (先跑 bash tools/pull_all.sh 拉数据)"
    echo ""
fi

if [ -z "$EXISTING_CODES" ]; then
    echo "❌ 没有可分析的 raw JSON, 先跑 Phase 1: bash tools/pull_all.sh"
    exit 1
fi

TOTAL=$(echo $EXISTING_CODES | wc -w | xargs)
START=$(date +%s)

echo "📊 Phase 2: 分析 $TOTAL 只票, $WORKERS 并发 (纯 CPU, 不消耗 API)"
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="

# 跑分析 (4 并发, 纯 CPU)
ANALYZE_DIR=$(mktemp -d)
ANALYZE_OK=0
ANALYZE_FAIL=0

analyze_one() {
    local code=$1
    # Phase 2: dump_code(analyze_only=True) 走读 JSON 路径, 不拉数据
    if bash tools/with_venv.sh python3 -c "
import sys
sys.path.insert(0, '$ROOT')
from tools.dump_data import dump_code
import json
data = dump_code('$code', analyze_only=True)
out = 'data/dump/${code}.json'
with open(out, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
wy = data.get('chan_signals', {}).get('威科夫阶段', '—')
print(f'  ✅ {\"$code\"}: 威科夫={wy}')
" >"$ANALYZE_DIR/${code}.log" 2>&1; then
        echo "ANALYZE_OK:$code"
    else
        echo "ANALYZE_FAIL:$code $(tail -1 "$ANALYZE_DIR/${code}.log" 2>/dev/null)"
    fi
}
export -f analyze_one
export ANALYZE_DIR
export ROOT

# xargs -P N 并发
printf '%s\n' $EXISTING_CODES | xargs -I{} -P "$WORKERS" -n 1 bash -c 'analyze_one "$@"' _ {} > "$ANALYZE_DIR/results.txt" 2>&1

# 统计
ANALYZE_OK=$(grep -c "^ANALYZE_OK:" "$ANALYZE_DIR/results.txt" 2>/dev/null)
ANALYZE_OK=${ANALYZE_OK:-0}
ANALYZE_FAIL=$(grep -c "^ANALYZE_FAIL:" "$ANALYZE_DIR/results.txt" 2>/dev/null)
ANALYZE_FAIL=${ANALYZE_FAIL:-0}
ANALYZE_END=$(date +%s)
echo ""
echo "📊 Phase 2: ✅ $ANALYZE_OK / ❌ $ANALYZE_FAIL, 耗时 $((ANALYZE_END-START)) 秒"
if [ "$ANALYZE_FAIL" -gt 0 ]; then
    echo "    失败详情:"
    grep "^ANALYZE_FAIL:" "$ANALYZE_DIR/results.txt" | head -5 | sed 's/^/      /'
fi

END=$(date +%s)
echo "=================================================="
echo "Phase 2 完成: ✅ $ANALYZE_OK / ❌ $ANALYZE_FAIL, 耗时 $((END-START)) 秒"
echo ""
echo "下一步: bash tools/refresh_all.sh  (重跑一遍, 会自动跳未过期的 dump, 约 2 分钟刷完 md)"

rm -rf "$ANALYZE_DIR"
