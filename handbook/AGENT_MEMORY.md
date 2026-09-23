# Mavis Project Cheat Sheet (8-24, 极简)

> ⚠️ 严格纪律: 每条事实必须这轮 grep 验证。memory 不当真理用。

---

## 入口 (grep 验证过)

- **项目根**: `/Users/I514959/workspace/mavis-quant-agent/`
- **数据**: DataStore (tools/storage/store.py, DAO 层) → `data/history/{daily,stk_factor,financials,stock_basic,eps,fflow_history}/`
- **同步**: `tools/storage/sync.py` (8 flag: kline/stk-factor/stock-basic/financials/eps/fflow/cache/meta, 默认 --auto)
- **分析引擎**: `tools/analysis/analysis_engine.py` 6 个 strategy 类 (chan/wyckoff/smc/obv/fflow/finance)
- **10 个活 skill**: `.claude/skills/{t-analyze, t-sync-data, t-rsi6-tech, t-near-low, t-backtest, t-roc-ey, t-sector-macd, t-earnings-blowout, t-guardrail}/` (2026-09-17 重命名链: t-sector-ma → t-sector-macd, 旧名不再支持)
- **批量分析**: `tools/batch/t_analyze_all.py` (4 worker 并发)
- **单只分析**: `tools/batch/t_analyze_one.py --code <code>` (新加, 829 行详报)

## 纪律 (8-24 固化)

- 引用 memory 前**必须 grep 验证**
- 说"line X 做了 Y"必须当场有 grep 输出
- 找不到老实说"没找到", 不补 plausible
- 不以"我记得/应该是"开头
- 错就老实说"我之前是瞎编的"

## v6.2.4 重构教训: stk_factor 接 auto 链路 (2026-09-22 修)

**症状:** 用户每天跑 `--auto`(默认行为),stk_factor 静默跳过 14 天不断流,直到用户手动查才发现。

**根因:** v6.2.4 把 `daily_basic` 重构成 `stk_factor_pro`,只加了 `--stk-factor` 手动 flag,但:
- `detect_stale_flags()` 的 `flags` 字典里没加 `stk_factor` key
- 检测函数没读 `STK_FACTOR_DIR`
- `action_auto()` 真跑分支没接 `action_stk_factor`

**修法:** `tools/storage/sync.py` 三处改动:
1. `flags` 字典加 `stk_factor: False`
2. `# 4. stk_factor` 检测块(读 parquet metadata `done_dates` 合并集,距今天 ≥ 1 天 → True)
3. `action_auto()` 加 `if flags["stk_factor"]: action_stk_factor(force=False)`

**铁律:** 任何新增的 sync action flag,**必须同步在 `detect_stale_flags()` + `action_auto()` 两处加**。加完跑 dry-run 模拟 stale 场景验证。

**验收:** `python -c "from tools.storage.sync import detect_stale_flags; print(detect_stale_flags())"` 应该带 `stk_factor` key。
