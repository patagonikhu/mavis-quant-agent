#!/bin/bash
# tools/pull_all.sh — Phase 1: 拉全 watchlist 数据 (v5.6, 2026-07-29 拆分)
#
# 职责: 只拉数据, 写 data/dump/{code}.json (raw 段, _section="raw")
#   - 不跑分析 (5方法×3周期 / 缠论 / 威科夫 / PEG / DCF / 仓位 / 止盈)
#   - 不调 signals_5method
#   - 不写 report
#   - 输出 JSON 带 _section="raw" 标记
#
# 后续用 tools/analyze_all.sh 跑分析 (Phase 2), 或 tools/refresh_all.sh (拉+跑+渲染一站式)
#
# 性能:
#   - 4 进程并发 (Tushare 全接口 80/分 内, 实际跑 6-7 段/秒)
#   - 单票拉数据 ~13s, 56 只 ≈ 3 分钟 (4 并发)
#
# 用途:
#   bash tools/pull_all.sh                  # 拉全 watchlist (默认 4 并发)
#   bash tools/pull_all.sh 300274 000725    # 拉指定 code
#   bash tools/pull_all.sh --workers 8      # 8 并发 (小心 Tushare 单接口 100/分)
#
# 对应 (走 dump_data.py sentinel 参数):
#   python3 -c "from tools.dump_data import dump_code; dump_code('300274', pull_only=True)"  # 串行拉 1 只

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
            echo "用法: pull_all.sh [--workers N] [code1 code2 ...]"
            echo "  --workers N: 并发数 (默认 4)"
            echo "  无 code 参数: 拉 watchlist 全部"
            echo ""
            echo "Phase 1: 只拉数据, 写 data/dump/{code}.json (raw 段)"
            echo "下一步: bash tools/analyze_all.sh (Phase 2: 跑分析)"
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

echo "📥 Phase 1: 拉 $TOTAL 只票数据, $WORKERS 并发"
echo "开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "=================================================="

# 拉数据 (4 并发)
DUMP_DIR=$(mktemp -d)
PULL_OK=0
PULL_FAIL=0

pull_one() {
    local code=$1
    # Phase 1: dump_code(pull_only=True) 走拉数据分支, 不跑分析
    if bash tools/with_venv.sh python3 -c "
import sys
sys.path.insert(0, '$ROOT')
from tools.dump_data import dump_code
import json, pathlib
data = dump_code('$code', pull_only=True)
out = pathlib.Path('data/dump') / '${code}.json'
out.parent.mkdir(parents=True, exist_ok=True)
with open(out, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f'  {chr(9989)} {\"$code\"}: kline={len(data.get(\"kline\") or [])} weekly={len(data.get(\"weekly\") or [])} 60m={len(data.get(\"kline_60m\") or [])} daily_basic={len(data.get(\"daily_basic_long\") or [])}')
" >"$DUMP_DIR/${code}.log" 2>&1; then
        if [ -f "data/dump/${code}.json" ]; then
            echo "PULL_OK:$code"
        else
            echo "PULL_FAIL:$code (JSON 未生成)"
        fi
    else
        echo "PULL_FAIL:$code $(tail -1 "$DUMP_DIR/${code}.log" 2>/dev/null)"
    fi
}
export -f pull_one
export DUMP_DIR
export ROOT

# xargs -P N 并发
printf '%s\n' $CODES | xargs -I{} -P "$WORKERS" -n 1 bash -c 'pull_one "$@"' _ {} > "$DUMP_DIR/results.txt" 2>&1

# 统计
PULL_OK=$(grep -c "^PULL_OK:" "$DUMP_DIR/results.txt" 2>/dev/null)
PULL_OK=${PULL_OK:-0}
PULL_FAIL=$(grep -c "^PULL_FAIL:" "$DUMP_DIR/results.txt" 2>/dev/null)
PULL_FAIL=${PULL_FAIL:-0}
PULL_END=$(date +%s)
echo ""
echo "📦 Phase 1: ✅ $PULL_OK / ❌ $PULL_FAIL, 耗时 $((PULL_END-START)) 秒"
if [ "$PULL_FAIL" -gt 0 ]; then
    echo "    失败详情:"
    grep "^PULL_FAIL:" "$DUMP_DIR/results.txt" | head -5 | sed 's/^/      /'
fi

END=$(date +%s)
echo "=================================================="
echo "Phase 1 完成: ✅ $PULL_OK / ❌ $PULL_FAIL, 耗时 $((END-START)) 秒"
echo ""
echo "下一步: bash tools/analyze_all.sh  (Phase 2: 跑分析, 纯 CPU 13s/票, 不消耗 API)"

rm -rf "$DUMP_DIR"
