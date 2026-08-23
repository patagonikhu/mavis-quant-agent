# AGENT_MEMORY.md — mavis-quant-agent 项目 memory (8-24)

> 📍 **位置**: 项目根 `docs/AGENT_MEMORY.md` (git 跟踪)
> 🔄 **Mirror**: `~/.minimax/agents/mavis/memory/MEMORY.md` (agent 启动用)

---

## ⚠️ memory 使用纪律 (8-24 固化, 再犯剁手)

- **memory 是历史参考, 不是真相** — 任何结论必须**当下 grep/read 验证**
- **说"line X 做了 Y"** — 必须这轮里有 `grep -n "..." tools/X.py` 输出
- **说"文件坏了"** — 必须有真 grep 证据 (如 `ModuleNotFoundError`/`ImportError`/`No such file or directory`)
- **找不到** → 老老实实说"没找到/未验证", **不补 plausible 解释**
- **不**以"我记得"/"应该是"/"应该的" 开头说任何事
- 错就老实说"我之前是瞎编的", 不狡辩

---

## 🏗️ 实际架构 (2026-08-22 现状, 8-24 验证)

**唯一数据源**: `data/history/daily/{year}.parquet` (duckdb 读, 按年分片)
**唯一入口**: `tools/data_store.py` `DataStore` (classmethod, 静态访问)
**同步**: `tools/history_sync.py` `sync_incremental()` (幂等, 增量补缺失)
**单只工具**: `tools/sync_stock.py` (替代老 `老 data 工具.py`, 7-22 删)
**全市场扫**: `tools/batch/am_divergence.py` (t-am-divergence skill 配套)
**分析引擎**: `tools/analysis/analysis_engine.py` (8 strategies, PHASE1_STRATEGIES)
**因子历史**: `tools/analysis/factor_history.py` (含 `macd_div_bot` 字段)

### DataStore 入口 (`tools/data_store.py`)
```python
from tools.data_store import DataStore

DataStore.get_kline(code, limit=0)        # 日线 K线 (limit=0 = config.kline_days)
DataStore.get_weekly(code, limit=0)      # 周线 (从日线聚合)
DataStore.get_ctx(code, kline_only=False) # RawContext (kline_only=True 跳过网络, 全市场扫用)
DataStore.get_stock_basic(code)           # 名称/行业 (本地 cache)
DataStore.get_daily_basic(code)           # PE/PB/市值
DataStore.get_eps(code)                   # EPS 一致预期
DataStore.list_codes()                    # 全市场代码 (duckdb 查 parquet)
DataStore.watchlist_codes()               # watchlist.json
```

### 8 PHASE1_STRATEGIES (tools/analysis/analysis_engine.py)
| Strategy | weight | 输出 |
|---|---|---|
| ChanStrategy | 0.20 | `ctx.chan_result` |
| WyckoffStrategy | 0.20 | `ctx.wyckoff_result` / wyckoff_weekly / wyckoff_60m |
| SmcStrategy | 0.10 | `ctx.smc_result` |
| ObvStrategy | 0.10 | `ctx.obv_result` |
| FflowStrategy | 0.10 | `ctx.fflow_result` |
| ResonanceStrategy | 0.15 | `ctx.resonance_result` |
| PegStrategy | 0.15 | — |
| **MacdDivergenceStrategy** | 0.05 | `ctx.macd_div_result` (commit 0045a86 加) |

### Tushare 限流 (2000 积分档, 8-24 验证)
- 全接口: **80 req/min**
- 单接口: **100 req/min** (daily/daily_basic/money_flow 走单接口)
- 2000 积分档: 解锁 weekly/monthly + 财务三表 + 龙虎榜 + 北向 + 融资融券 + 指数成分股
- 限量档 (白名单 _NO_RETRY_2000): forecast/top_list/north_flow/margin

### Tushare bulk API
- `daily(trade_date='20240822')` **一次拿一天全市场** (~5000 行) — 拉 5000 只 1y = 250 req = **2.5 min**
- 拉单只 `daily(ts_code, start, end)` 慢 20x: 5000 只 1y = 5000 req = 50 min (限流)

---

## 📁 当前文件路径速查 (8-24 验证)

| 用途 | 路径 |
|---|---|
| 项目 memory (真源) | `docs/AGENT_MEMORY.md` |
| Agent memory (mirror) | `~/.minimax/agents/mavis/memory/MEMORY.md` |
| CLAUDE.md | `CLAUDE.md` (项目根) |
| Skills (4 个) | `.claude/skills/{t-am-divergence, t-analyze, t-near-low, t-ranking}/` |
| **唯一数据源** | `data/history/daily/{year}.parquet` (duckdb 读) |
| DataStore 入口 | `tools/data_store.py` |
| Tushare fetch | `tools/fetch/tushare_fetcher.py` (含 `get_daily_by_date` 批量) |
| 同步脚本 | `tools/history_sync.py` |
| 单只工具 (替代老 data 工具) | `tools/sync_stock.py` |
| 全市场扫脚本 | `tools/batch/am_divergence.py` |
| 三层分析入口 | `tools/analysis/analysis_engine.py` (8 strategies) |
| 因子历史计算 | `tools/analysis/factor_history.py` (含 `macd_div_bot`) |
| ~~老的 watchlist dump~~ | ⚠️ `tools/老 data 工具.py` **已删** (7-22 660 行僵尸) |

---

## 🎯 当前 4 个 skill (8-24)

| Skill | 状态 | 干啥 |
|---|---|---|
| `t-am-divergence` | ✅ | 全市场扫 A→M + 缠论底背驰 + MACD 底背驰 三重确认 |
| `t-analyze` | ✅ | 单只 + 全 watchlist + 板块分析 (22 section 报告) |
| `t-near-low` | ✅ | 距 5y 低 < N% 跌 70-80% 清单 |
| `t-ranking` | ✅ | 读 `docs/analyze-*.md` 按评级+PEG 排序 |

**8-22 之前曾有, 8-24 已删的死 skill (9 个)**: t-pull, t-chain, t-bottleneck, t-rotation, t-etf, t-watchlist, t-signals, t-history, t-monitor

---

## 🧪 sync_incremental 幂等保证 (8-24 验证)

- `max_local = duckdb 查最近 2 个 parquet MAX(trade_date)`
- `max_local >= today` → 秒返回, 0 网络
- 否则 `missing = [d for d in trading_calendar if not has_data_for_date(d)]` 过滤已有
- `_append_records`: 旧+新 `drop_duplicates(ts_code, trade_date, keep="last")` 去重
- Tushare 限流: 写已拉 → `sys.exit(0)` 优雅退出, 下次从 `max_local+1` 续

**多次跑 = 0 漏数据 / 0 重复数据 / 0 重复网络请求**

---

## 🛠️ 8-24 清理记录 (commit 6036d4f + 后续)

**删除**:
- `tools/老 data 工具.py` (661 行僵尸, 7-22)
- 9 个死 skill (见上表)
- `data/_old_d/` 目录

**修复**:
- `tools/data_store.py` 删 dead `from tools.老 data 工具 import _PROJECT_CFG` (module-level 已有)
- `tools/batch/regression_test.py:104` 改走 `DataStore + AnalysisEngine`
- `tools/refresh_all.sh` dump 成功判定: 文件存在 → "K线: " grep

**批量清理** (commit 6036d4f, 34 文件):
- 47 个文件 `老 data` → `老 data 工具` (历史) / `sync_stock` (新)
- 16 个旧 `docs/analyze-*.md` 错误信息改 `sync_stock`
- `.claude/skills/_shared/analysis_framework.md` 改 `sync_stock.py`
- `docs/watchlist-overview.md` 改 `DataStore.get_ctx()`
- `tools/render/report_renderer.py` 6 处错误信息改 `sync_stock`
- 20+ 因子文件 docstring 改 "老 data 工具"

---
