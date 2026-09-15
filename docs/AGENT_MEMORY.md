# Mavis Project Cheat Sheet (8-24, 极简)

> ⚠️ 严格纪律: 每条事实必须这轮 grep 验证。memory 不当真理用。

---

## 入口 (grep 验证过)

- **项目根**: `/Users/I514959/workspace/mavis-quant-agent/`
- **数据**: DataStore (tools/storage/store.py, DAO 层) → `data/history/{daily,stk_factor,financials,stock_basic,eps,fflow_history}/`
- **同步**: `tools/storage/sync.py` (8 flag: kline/stk-factor/stock-basic/financials/eps/fflow/cache/meta, 默认 --auto)
- **分析引擎**: `tools/analysis/analysis_engine.py` 6 个 strategy 类 (chan/wyckoff/smc/obv/fflow/finance)
- **10 个活 skill**: `.claude/skills/{t-analyze, t-sync-data, t-macd-r2g, t-near-low, t-backtest, t-roc-ey, t-sector-ma, t-earnings-blowout, t-guardrail}/` (2026-09-15 重命名: t-tech-bb-obv → t-macd-r2g, 旧名不再支持)
- **批量分析**: `tools/batch/t_analyze_all.py` (4 worker 并发)
- **单只分析**: `tools/batch/t_analyze_one.py --code <code>` (新加, 829 行详报)

## 纪律 (8-24 固化)

- 引用 memory 前**必须 grep 验证**
- 说"line X 做了 Y"必须当场有 grep 输出
- 找不到老实说"没找到", 不补 plausible
- 不以"我记得/应该是"开头
- 错就老实说"我之前是瞎编的"
