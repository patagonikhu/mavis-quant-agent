---
name: t-analyze
description: 股票分析 + 批量扫描. 单只 /t-analyze <code>; 批量 /t-analyze --all. 0 网络, 走 DataStore. 触发词: "分析XX股票"、"XX能买吗"、"批量分析"、"全部扫一遍".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** `tools/batch/t_analyze_one.py` + `tools/batch/t_analyze_all.py` argparse `--help`(参数 / 默认值 / 性能对比)。SKILL.md 只保留"啥时候用 + 跑最小例子 + 关键约束"。

## 用法

```bash
# 单只
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --code 300274

# 多只 (逗号或空格分隔, 默认单进程)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --codes "600177,300274,300750"

# 多只 + 多进程并发 (mp.spawn 跨 GIL, 4 worker 实测 2.6x 加速)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --codes "600177,300274" --workers 4

# 强制输出子目录 (跳过 watchlist 分流, 如 wyckoff-a2m 命中专用)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --code 600177 --output-subdir wyckoff-a2m

# 全 watchlist 批量 (复用 t_analyze_all, 4 worker 并发)
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python -m tools.batch.t_analyze_all

# 跳过 ETF 段
bash tools/with_venv.sh python -m tools.batch.t_analyze_all --no-etf

# 包含 blowout 票
bash tools/with_venv.sh python -m tools.batch.t_analyze_all --include-blowout
```

## `--codes` 性能对比 (2026-10-17 实测, 4 只详报)

| workers | 总耗时 | 加速 |
|---|---|---|
| 1 (单进程, 默认) | 13s | 1.00x |
| 4 (mp.spawn) | 5s | **2.6x** |

> mp.spawn 不能 fork (duckdb fork 死锁), 默认 0 worker = 单进程; 设 `os.cpu_count()` 用全部核心

## 关键约束

- **0 网络**, 走 `DataStore.get_ctx()` + `AnalysisEngine.analyze()`, 详见 architecture.md §4
- **缺数据**: 报"请先 /t-sync-data", 不兜底拉
- **报告 18 section**: schema 在 `tools/render/report_schema.py`, 改这里不要散到 batch/*.py

## 输出

- `docs/portfolio/analyze-{code}-{name}.md` (持仓票) / `docs/watchlist/...` (自选)
- `docs/signal-watchlist.md` (批量模式汇总)

## 相关

- `/t-sync-data` (跑前必跑) / `/t-finance-roc-ey` / `/t-finance-earnings-blowout` / `/t-near-low` / `/t-rsi6-tech` / `/t-backtest` / `/t-guardrail` / `/t-factor-5y`
