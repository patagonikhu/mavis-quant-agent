---
name: t-near-low
description: 每周监控"跌 70-80% 且距 5y 低 < 3%"的股票清单（含反弹次数、5y最大回撤、2025财报、并发拉tushare）。任何时候用户说"距 5y 低"、"近底"、"距低"、"接近底部"、"超跌清单"时触发。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
/t-near-low                            # 默认: 跌 70-80% + 距 5y 低 < 3% + 5y lookback
/t-near-low --gap 5                    # 距 5y 低 < 5% (宽松)
/t-near-low --gap 2                    # 距 5y 低 < 2% (严格)
/t-near-low --drop 80 --drop-max 90    # 跌 80-90%
/t-near-low --lookback-years 3         # 3y lookback (不含 2021 牛市)
/t-near-low --min-bounces 4            # 反弹次数 ≥ 4
/t-near-low --skip-tushare              # 跳过 tushare (只用 weekly 末根)
```

## 算法 (3 步)

### 第一步: 粗筛 (读 DataStore weekly, 不调 tushare)
遍历 DataStore 全市场 weekly K线 (~5y):
- **max_drop** (默认 5y 窗口, 可选 3y): high → low 最深回撤
- **max_drop 范围**: 默认 `70% ≤ max_drop < 80%` (排除 80%+ 异常)
- **距 5y 低** (用 weekly 末根 8-14): `< 10%` (粗筛)
- **反弹次数** (5y weekly 内 30%+ 反弹事件, window=3 strict local min): `≥ 0` 默认

### 第二步: 并发拉 tushare (8 worker)
对粗筛后候选:
- `get_daily(code, limit=1)` → 拉 8-20 daily 最新价 (今天)
- 用 daily 价重算**距 5y 低**, 精筛 `< 3%`
- `get_income(code, period=20251231)` + `get_income(code, period=20241231)` → 拉 2025A/2024A 净利
- 算同比, 输出"赚/亏/扭亏"

### 第三步: 输出清单 (按距低% 升序)

## Step 1: 检查本地数据

```bash
bash tools/with_venv.sh python3 -c "from tools.data_store import DataStore; codes = DataStore.list_codes(); print(f'本地 parquet: {len(codes)} 只')"
```

如果 < 400, 提示先跑 `bash tools/with_venv.sh python -m tools.history_sync` 同步全市场数据.

## Step 2: 跑筛选脚本 (并发 8 worker, ~10 秒)

```bash
bash tools/with_venv.sh python3 tools/find_near_low.py [参数]
# 加 --write-md 自动写 docs/oversold-watchlist.md (单文件覆盖, 含时间戳)
bash tools/with_venv.sh python3 tools/find_near_low.py --write-md
```

**输出示例**:
```
Loaded: 416, Skipped: 0
粗筛: 跌 ≥70% + weekly 末根距 5y 低 < 10% + 反弹 ≥0 = 50 只
现价: daily 末根 20260820 (5y low/high 来自 weekly)
代码       名称         行业         现价   上周    5y低    5y最大回撤  今gap  反弹  2025净利  今年
002531    天顺风能     电气设备     6.11   6.54   6.08   -72.3%    +0.49%  13次  -1.3亿   亏
601888    中国中免     旅游服务    53.10  54.06  52.73   -80.5%    +0.70%  12次  36.9亿  -24%
...
```

## Step 3: 报告 + 解读

简洁报告:
- 清单表格 (8 列: 代码/名称/现价/上周/5y低/5y最大回撤/今gap/反弹/2025净利/今年)
- 行业分布
- 反弹策略说明: 距 5y 低 < 3% 的超跌股多为业绩下行/亏损, 反弹策略 (涨 10-20% 跑 / 跌破谷底 10% 砍 / 持有 1-3 个月 / 5-10 只分散), 跟价值投资无关
- 实战建议: 反弹策略 (涨 10-20% 跑 / 跌破谷底 10% 砍 / 持有 1-3 个月 / 5-10 只分散)

## Step 4: 深挖 (清单出来后,挑出 3-5 只做完整分析)

清单给出 8-13 只候选后,挑出值得深挖的 3-5 只,用 `/t-analyze` skill 跑完整 22 section 报告。

**✅ 正确做法 (批量入口, 0 重复)**:

```bash
# 多只 (>1 只) 永远走 refresh_all.sh 批量入口
# 1 次 sync_incremental (单线程) + 4 worker 并发 analyze+render
bash tools/refresh_all.sh 002531 300699 600515 688522 000858 --workers 5
```

**❌ 错误做法** (N 只单跑,每次重复 sync):

```bash
# 不要这样 — 每次都跑 sync_incremental, 5 次锁等待
for code in 002531 300699 600515 688522 000858; do
    bash tools/with_venv.sh python -m tools.sync_stock $code
done
```

**单只深挖** (用户说"就分析 002531"):

```bash
bash tools/with_venv.sh python -m tools.sync_stock 002531
bash tools/with_venv.sh python3 -c "
import sys; sys.path.insert(0, '.')
from tools.data_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.analysis_data import AnalysisData
from tools.analysis.factor_history import compute_factor_history
from tools.render.report_renderer import render_report
from pathlib import Path
ctx = DataStore.get_ctx('002531')
data = AnalysisData.from_result(ctx, AnalysisEngine().analyze(ctx))
data.factor_history_rows = compute_factor_history(ctx, step=1, lookback=120)
md = render_report(data)
p = Path('docs') / f'analyze-002531-{ctx.name or \"002531\"}.md'
p.write_text(md, encoding='utf-8'); print(p)
"
# 输出: docs/analyze-002531-天顺风能.md (完整 22 section)
```

## 每周自动跑 (cron)

```python
mavis(cron.create, {
  "cron_name": "weekly-near-low",
  "schedule": "0 9 * * 1",       # 每周一 09:00
  "prompt": "跑 t-near-low skill 监控超跌清单, 输出本周 8-10 只, 标记业绩/技术状态",
  "session": {"mode": "new", "agent_name": "mavis"}
})
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--gap` | 3 | 距 5y 低阈值 (daily 价, %) |
| `--weekly-gap` | 10 | 粗筛 weekly 末根距 5y 低 (放宽用) |
| `--drop` | 70 | max_drop 下限 (%) |
| `--drop-max` | 80 | max_drop 上限 (%, 排除异常) |
| `--lookback-years` | 5 | max_drop 窗口 (5 或 3) |
| `--min-bounces` | 0 | 反弹次数阈值 (历史弹性) |
| `--skip-tushare` | False | 跳过 tushare (只用 weekly) |

## 关键限制

- **现价**: tushare daily 末根 (今天), 不是 8-14 weekly
- **5y 最大回撤**: 5y 内最深的 high→low (不一定是最近)
- **业绩**: 2025A 净利 vs 2024A, 0 只同比正增长 (清单里 80% 业绩下行)
- **反弹期望**: 谷底跌 70-80% 反弹期望最好 (中位 +45%), 90%+ 反弹差 (中位 +13%)
- **实战**: 反弹策略不价值投资, 涨 10-20% 跑

## 反弹次数算法

```python
def find_peaks(c, w=3):     # strict local max (window=3)
def find_troughs(c, w=3):  # strict local min (window=3)
def count_bounces(closes, threshold=0.30, window=3):
    """5y weekly 内 30%+ 反弹事件次数"""
    ts = find_troughs(closes, 3)
    ps = find_peaks(closes, 3)
    n = 0
    for t in ts:
        for p in ps:
            if p > t and (closes[p] - closes[t]) / closes[t] >= 0.30:
                n += 1
                break
    return n
```

**5y weekly 数据**: DataStore parquet (由 `tools/history_sync.py` 维护, 全市场)

## 相关资源

- `tools/find_near_low.py` - 筛选脚本 (8 worker 并发, ~10 秒, 走 DataStore)
- `tools/with_venv.sh` - 虚拟环境包装
