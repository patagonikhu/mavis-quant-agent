---
name: t-analyze
description: 股票分析 + 批量扫描. 单只 /t-analyze <code>; 批量 /t-analyze --all. 0 网络, 走 DataStore. 触发词: "分析XX股票"、"XX能买吗"、"批量分析"、"全部扫一遍".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
# 单只
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --code 300274

# 批量 (watchlist 全部, 4 worker 并发)
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python -m tools.batch.t_analyze_all
```

## 关键约束

- **0 网络**, 走 `DataStore.get_ctx()` + `AnalysisEngine.analyze()`, 详见 architecture.md §4
- **缺数据**: 报"请先 /t-sync-data", 不兜底拉
- **报告 22 section**: schema 在 `tools/render/report_schema.py`, 改这里不要散到 batch/*.py

## 输出

- `docs/portfolio/analyze-{code}-{name}.md` (持仓票) / `docs/watchlist/...` (自选)
- `docs/signal-watchlist.md` (批量模式汇总)

## 相关

- `/t-sync-data` (跑前必跑) / `/t-roc-ey` / `/t-bb-obv` / `/t-near-low` / `/t-earnings-blowout` / `/t-sector-ma` / `/t-backtest` / `/t-guardrail`
