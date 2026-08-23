#!/bin/bash
# tools/with_venv.sh — 自动激活 .venv 跑项目命令 (固化环境, 避免每次 source)
#
# 用法:
#   bash tools/with_venv.sh python3 tools/sync_stock.py 002371
#   bash tools/with_venv.sh python3 tools/analyze_data.py
#   bash tools/with_venv.sh bash tools/pull_all.sh
#
# 等价于:
#   source .venv/bin/activate && python3 tools/sync_stock.py 002371
#
# 自动 fallback: 如果 .venv 不存在, 跑 tools/setup_venv.sh 一键建 (uv sync + uv pip install)
#   整个流程 < 60s, 任何机器一气呵成
#
# 不传任何参数时: 进交互式 Python REPL (在 .venv 里)

set -e

cd "$(dirname "$0")/.."
ROOT=$(pwd)

# 1. 检查 .venv
if [ ! -d ".venv" ]; then
    echo "⚠️  .venv 不存在, 一键建环境 (uv sync + uv pip install -r requirements.txt + tushare)"
    if ! command -v uv &> /dev/null; then
        echo "📥 装 uv (curl https://astral.sh/uv/install.sh | sh)..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$PATH"
    fi
    uv sync
    uv pip install -r requirements.txt
    uv pip install tushare
    echo "✅ .venv 建好 (Python 3.13 + 全套依赖)"
    echo ""
fi

# 2. 自动激活 + 跑命令
# 注意: 必须 source .venv/bin/activate (不能用 uv run, 因为它会重置 cwd)
source .venv/bin/activate

# 3. 没参数 → 进 REPL
if [ $# -eq 0 ]; then
    echo "✅ .venv 已激活 ($(python3 --version))"
    echo "💡 提示: 不传参数进 REPL, 或传命令 (如 python3 tools/sync_stock.py 002371)"
    exec python3
fi

# 4. 跑命令
exec "$@"
