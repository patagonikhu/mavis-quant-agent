# Mavis Project Cheat Sheet (8-24, 极简)

> ⚠️ 严格纪律: 每条事实必须这轮 grep 验证。memory 不当真理用。

---

## 入口 (grep 验证过)

- **项目根**: `/Users/I514959/workspace/mavis-quant-agent/`
- **数据**: DataStore (tools/storage/store.py, DAO 层) → `data/history/{daily,stk_factor,financials,stock_basic}/`
- **同步**: `tools/storage/sync.py` (7 flag: kline/stk-factor/stock-basic/financials/cache/meta/all-data, 默认 --auto)
- **分析引擎**: `tools/analysis/analysis_engine.py` 4 个 strategy 类 (chan/wyckoff/technical/finance)
- **活 skill**: `.claude/skills/{t-analyze, t-sync-data, t-backtest, t-finance-earnings-blowout, t-concept-macd, t-guardrail, t-etf-wyckoff-a2m}/` (2026-11-02 删 t-near-low / t-finance-roc-ey; 2026-11-04 删 t-rsi6-tech)
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

## v6.3.0 命名规范 (2026-09-24)

详见: [`handbook/naming-convention.md`](naming-convention.md) (158 行, 7 条硬规则 + 5 项 grep 验证)

## 周期口径真源: 威科夫日线/周线 (2026-11-05 修 BUG 后固化)

**症状**: 报告「周线分析」段的威科夫判定 (阶段/强度/基础指标/公式代入值) 显示的是**日线**数据。
「因子 × N 周期 综合矩阵」的周线列同样错显日线。67 只样本: 47% stage 不一致、53% strength 不一致。

**根因**: analysis dict 里**从来没有** `wyckoff_weekly` 这个顶层 key (它只是 `RawContext`
上的字段, 没进 raw), 所以 `s5.get("wyckoff_weekly") or s5.get("wyckoff")` 永远 fallback 到日线。

**铁律**:

| 要什么 | 从哪取 |
|---|---|
| 日线威科夫判定 | `raw["wyckoff"]` |
| 周线威科夫判定 | `raw["wyckoff"]["3period"]["weekly"]` |
| 周线 sub_events | `raw["wyckoff"]["sub_events_by_period"]["weekly"]` |
| 周线 bars (公式代入) | `ctx.weekly` → `RenderData.weekly_kline` |

**禁止**: `s5.get("wyckoff_weekly")` —— 这个 key 不存在, 写了就等于静默退回日线。
`analysis_result_signals.py` 是**对的**样板 (用 `p3 = wy_raw.get("3period")`)。

**死代码坑**: `WyckoffStrategy.analyze(ctx)` **没有任何生产调用** —— 全链路
(report_renderer / t_analyze_one / t_analyze_all / factor_5y / batch_matrix / etf_macd_scan /
caches/analysis) 都走 `AnalysisEngine.analyze_history()`。两者子事件扫描口径不同:
`analyze_history` 传 `precomputed_arrs` → detector 里 ma200 取 `arrs['ma_window']` (250 均线);
`analyze()` 不传 → detector 内部 `sum(c[i-200:i])/200` (真 MA200)。
→ 实测 67 只里 **1 只日线 + 8 只周线** 判定不同 (死路径更宽松, 多认出 EVR)。
**验证口径时必须用 `analyze_history`, 用 `WyckoffStrategy().analyze()` 会得到错数。**

**回归防线**: `bash tools/with_venv.sh python -m tools.batch._verify_weekly_period`
(报告 md ↔ 生产值双向对账, 日线/周线各查 stage + 判定强度)。

**周线段文案单位**: 生产侧 `slope_60` / `trend_30` 是**按周期 bars** 算的 (周线段 = 60 周/30 周),
报告里写「60 日斜率」是错的 → renderer 用 `period_unit` 参数 (日/周) 分开。

## 威科夫强度简写记法 (2026-11-05)

报告「因子历史走势」表 (22 列 → **24 列**) 的「威科夫强度」列, 原「A天(日/周)」列改造而来
(原内容 = 连续累积天数, 已连同 `accum_days_daily/weekly` 一并删除)。

**记法**:

| token | 含义 |
|---|---|
| `m` | Markup 严格门 (strict) |
| `m1` | Markup 弱信号门 (weak) |
| `m2` | Markup 加分线 (extra_only) |
| `mf1` | Markup **fallback1** 兜底 (4 项软条件) |
| `mf2` | Markup **fallback2** 兜底 (sub-event + 60周期斜率向上 + 30周期趋势向上) |
| `A` | Accumulation (不分强弱档, 一律 A) |
| `D` | Distribution (不分强弱档, 一律 D) |
| `—` | 未判定 (stage=`?` 或 strength=`none`/缺失) |

**单一事实源**: `tools/factors/wyckoff/helpers.py::strength_token(stage, strength, channel)`。
renderer **只读不算**, 不自己查表 —— 映射下沉生产侧是硬规则。

**必须带 stage**: 同一 strength 档在不同 stage 语义不同 (Accumulation 的 weak 也显示 `A`)。
**必须带 channel**: `stage_strength` 把 `fallback1` / `fallback2_markup` 都塌成 `strength="fallback"`,
光看 strength 分不出两档兜底 → 必须读生产侧 `channel` 字段 (`stage_factor.py` 直出) 才能分成 mf1 / mf2。
**判定逻辑不动**: `stage_factor.py` 未改, strength / channel 复用 commit `0f76fe8` 已加的字段。

**⚠️ main_weak ≠ fallback1** (实测易混): 两者是**串行链上互斥**的两端 ——
`main_weak` 是主路径 (三阶段至少一个得分), `fallback1` 只在主路径**全 0 分**时才被评估 (`elif`)。
AST 插桩实测 38 只样本: `main_weak` 26 只 / `fallback1` **0 只**, 交集 0 —— 结构上不可能重叠。

**列序**: 日期 → 收盘 → **威科夫(日/周) → 威科夫强度 → 子事件(日/周)** → RSI6 → MACD → ...
`FACTOR_HISTORY_HEADER` 是 24 列的唯一真源, sep 按其自动算列数。

> 2026-11-04 改: 威科夫 3 列从「MA偏离」右边移到「收盘」右边 (用户要求威科夫信息靠前, 紧跟价格看)。
> 改列序只需动 2 处 —— header 常量 + `_format_factor_row` 的 `line` 拼接, **必须严格同步**, 否则列错位。
> 验证: 改动前后按列名对齐逐格比对 (2880 cell 应 0 差异)。
