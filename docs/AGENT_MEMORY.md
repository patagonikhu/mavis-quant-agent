# Mavis Project Cheat Sheet (8-24, 极简)

> ⚠️ 严格纪律: 每条事实必须这轮 grep 验证。memory 不当真理用。

---

## 入口 (grep 验证过)

- **项目根**: `/Users/I514959/workspace/mavis-quant-agent/`
- **数据**: `data/history/daily/{year}.parquet` (duckdb 读)
- **统一入口**: `tools/kline_store.py` `DataStore` (classmethod)
- **同步**: `tools/kline_history_backfill.py` `sync_incremental()` (幂等)
- **单只**: `tools/sync_stock.py`
- **分析引擎**: `tools/analysis/analysis_engine.py` 8 strategies
- **5 个活 skill**: `.claude/skills/{t-bb-obv, t-analyze, t-backtest, t-near-low, t-sync-cache}/`

## 纪律 (8-24 固化)

- 引用 memory 前**必须 grep 验证**
- 说"line X 做了 Y"必须当场有 grep 输出
- 找不到老实说"没找到", 不补 plausible
- 不以"我记得/应该是"开头
- 错就老实说"我之前是瞎编的"
