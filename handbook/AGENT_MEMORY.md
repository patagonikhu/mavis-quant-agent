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

## v6.3.0 命名规范 (2026-09-24 固化, 所有 skill / batch 脚本统一遵守)

**铁律:** 任何 skill / batch 脚本的代码命名必须走业务语义, 禁止工程缩写 / 数字索引 / 工程术语词.

**行业基础:**
- PEP 8 (Python 官方): 函数 / 变量 `snake_case`, 类 `PascalCase`, 常量 `UPPER_SNAKE_CASE`
- Google Python Style Guide: 业务命名优先, 避免无意义缩写
- Tushare / AkShare / Eastmoney 数据 API 字段名: 业务全称 (`netprofit_yoy` 不用 `np_yoy`)

### 7 条硬规则

1. **业务名优先, 不用工程缩写**
   - ✅ `revenue_growth` `gross_margin` `net_profit_yoy` `operating_revenue`
   - ❌ `rev_growth` `gm` `np_yoy` `or_yoy` (例外: 仅 Tushare 原生字段名保留 `or_yoy`)
   - 例外: tushare / akshare / eastmoney 原生字段名是上游 API 约束, 不动

2. **不用工程术语词** (用户可能看不懂)
   - ✅ `_shift_quarters` (按季回溯)
   - ❌ `_add_lags` `n_lags` `lags`
   - ✅ `cumulative_decline` (累计下滑)
   - ❌ `lag` `lookback` (用业务意图命名, 不暴露时间序列术语)

3. **不用数字索引 / 数量后缀做功能代号**
   - ✅ `_rule_main_path` `_rule_reversal` `_rule_profit_surge`
   - ❌ `c1` `c2` `c3a` `c3b` `5a` `5b` `4c` `4_conditions`
   - ✅ `ebit_one_year_ago` (4 季 = 1 年, 业务名)
   - ❌ `ebit_prev4` `ebit_4q_ago` `decline_4q`

4. **pandas lag 后缀走业务名 (不写 prev2 / prev4)**
   - ✅ `last_quarter` / `two_quarters_ago` / `three_quarters_ago` / `one_year_ago`
   - ❌ `prev` / `prev2` / `prev3` / `prev4` (业内 lag 标识, 用户看不懂)
   - 注意: `_add_lags` 风格函数内部用动态生成列名, 改函数为 `if/elif` 显式分支

5. **死代码立即删**
   - 不留无人调用的函数 / 列 / 变量 (即使注释说 "保留派生")
   - 验证死代码: `for fn in <all_def_funcs>; do grep -c "$fn" file.py; done` 看引用计数

6. **CLI flag 保留 (用户长期沿用)**
   - 不动 `--rev-yoy` `--np-yoy` `--gm-tol` `--np-jump` 等 CLI 字面值
   - 改动只动内部 `args.xxx` 属性名, 不动 argparse `--foo` 字面参数

7. **SKILL.md 跟代码同步**
   - 任何变量 / 函数 / 列 / CLI flag 改名, SKILL.md 的派生 Flag 表 / 参数速查表 / Hit Pipeline 段必须同步
   - 历史叙述段 (v1 / v2 / v3 / v6.3.0 改名记录) 保留作为变更追踪

### 5 项 grep 验证 (Linter 自检)

新增代码 / 改完代码后, 跑这 5 行 grep, 命中即改:

```bash
# 1. 工程缩写 (c_jump / c_leader / 5a / 5b / 4c / 4_conditions 等)
grep -nE "\bc[1-9]_|\b[1-9]a\b|\b[1-9]b\b|\bc_jump|\bc_leader|\bc_lead|\brule_[1-9]" file.py

# 2. 工程术语 (用户可能看不懂)
grep -nE "\blag[s]?\b|\badd_lag|\bn_lag\b|\blookback" file.py

# 3. 数字后缀命名
grep -nE "[a-z]_[1-9]\b|\b[a-z]+_qoq_rising_prev[1-9]" file.py

# 4. 死代码 (def 了但 0 引用)
for fn in <all_def_funcs>; do grep -c "$fn" file.py; done

# 5. CLI flag vs 内部属性名 (CLI 不动, 内部动)
grep -nE "\-\-(np|rev|gm)" file.py    # CLI flag 保留
grep -nE "args\.(np|rev|gm)_" file.py  # 内部属性改业务名
```

### 参考实现

- `tools/batch/finance_earnings_blowout.py` v6.3.0 (2026-09-24, 已全清) 是参考样板
- 7 个 commit (`de239ee` → `4d0d923`) 演示了清理流程
- 命中验证: 总命中 891 只次, 业绩杀 5/5 + 好票 3/3 不破
