#!/bin/bash
# refresh_data.sh — 每小时跑一次, git pull 同步 + 刷新 watchlist 数据
# 替代 mavis cron 自提醒 (跨机器, 不依赖 minimax)
set -e
cd "$(dirname "$0")/.."

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === refresh_data.sh start ==="

# 1. 拉最新代码 (跨机器同步 memory)
git pull --rebase --autostash 2>&1 | head -5 || echo "git pull failed (可能没配 remote)"

# 2. 跑数据源稳定性检查
PYTHONPATH=. python3 tools/fetch/check_data_sources.py 2>&1 | head -10

# 3. 刷新 4 只关注标的 fflow
for code in 300274 002475 000725 002273; do
    PYTHONPATH=. python3 -c "
from tools.fetch.data_source import fetch_fund_flow
ff, st = fetch_fund_flow('$code', days=5)
if ff:
    total = sum(r['main_net'] for r in ff)
    print(f'$code: 5日 {total:+,.0f} 万 (≈ {total/10000:+.2f} 亿) status={st}')
" 2>&1 | tail -1
done

echo "[$(date '+%Y-%m-%d %H:%M:%S')] === refresh_data.sh done ==="
