---
name: t-factor-5y
description: 单只 / 批量跑 5 年因子历史走势, 输出 md (30+ 列: 价格 / 缠论 / 威科夫 / SMC / fflow / OBV / 技术指标). 0 网络, 复用 AnalysisEngine.analyze_history + compute_factor_history. 触发词: "5年因子"、"长势"、"5年走势"、"factor-5y".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)

## 用法

```bash
# 单只
bash tools/with_venv.sh python -m tools.batch.factor_5y --code 600089

# 批量 (默认 watchlist 持仓)
bash tools/with_venv.sh python -m tools.batch.factor_5y
bash tools/with_venv.sh python -m tools.batch.factor_5y --watchlist-types 持仓
bash tools/with_venv.sh python -m tools.batch.factor_5y --all-market

# 范围选项
... --codes 600089 000792 688498        # 指定多只
... --step 5                             # 采样间隔 (默认 5, 1250/5=250 行, 5 年紧凑)
... --step 1                             # 全采样 (1250 行, 适合详查但文件大)
... --output-dir docs/factor-5y          # md 输出目录 (默认 docs/factor-5y)
... --workers 4                          # 并发 (默认 4)
... --no-summary                         # 不输出 chat summary
```

## 算法 (复用现有代码, 不重算)

```
1. DataStore.get_ctx(code)              ← 默认 kline_days=1250 (5 年), 0 网络
2. AnalysisEngine.analyze_history(ctx, all_dates)  ← 一次算出全期 AnalysisResult dict
3. compute_factor_history(ctx, step, lookback=1250, history=...) ← 30+ 列拼装
4. RenderData.from_result(ctx, result) + data.factor_history_rows = ...
5. _section_factor_history(data, lookback=1250) ← markdown 渲染
6. 写 docs/factor-5y/analyze-{code}-{name}-5y.md
```

**复用基础 (跟 /t-analyze 同链路):**
- `tools/analysis/analysis_result_signals.py::compute_factor_history` (跟 t_analyze_one 完全一致)
- `tools/render/report_renderer.py::_section_factor_history` (跟 t-analyze --all 输出的 "📈 因子历史走势" 表同一函数, 仅 lookback 不同)
- `tools/render/factor_history_only.py` (已存在, 单只 + 默认 1260 天, 本 skill 是其批量 + SKILL.md 包装)

## 关键约束

- **0 网络**, 走 DataStore parquet
- **`--step` 必须 ≥ 1**:step=1 输出全 1250 行, 文件大 (~1.5MB);step=5 默认 250 行 (~300KB), 推荐
- **5 年窗口硬编码**:`lookback=1250` (≈ 252 交易日 × 5 年 = 1260, 1250 是项目默认)
- **同步阻塞**:批量模式按 `--workers` 并发, 23 只持仓默认 ~20 秒
- **不替代 /t-analyze**:`/t-analyze` 是 22 section 全要素详报;`/t-factor-5y` 只输出因子历史表 (长周期视角)

## 输出 md 结构

每只 1 个 md, 文件名 `docs/factor-5y/analyze-{code}-{name}-5y.md`:

```markdown
# 因子历史走势 (5 年) — 600089 特变电工

> 数据范围: 2021-10-08 ~ 2026-09-30 (1250 日 ≈ 5.0 年)
> 采样: step=5 (每 5 个交易日 1 行, 共 250 行)
> 数据源: DataStore (本地 K 线, 0 网络)

## 📈 因子历史走势

| 日期 | 收盘 | 涨跌幅 | ... 30+ 列 |
|------|------|--------|------------|
| 2021-10-08 | 12.30 | +0.5% | ... |
| 2021-10-15 | 12.45 | +1.2% | ... |
| ... |
| 2026-09-30 | 17.32 | -1.5% | ... |
```

**30+ 列包括:** 收盘/涨跌幅/MA20偏离/MA60偏离/BOLL/缠论中枢/缠论买卖点/Wyckoff阶段/Wyckoff子事件/SMC OB/FVG/Sweep/fflow/OBV/MACD/RSI6/RSI12/KDJ/ATR/量比 等。

## 跟 /t-analyze 的区别

| 维度 | /t-analyze | /t-factor-5y |
|---|---|---|
| 时间窗 | 近 120 日 (默认 lookback=120) | **5 年 (1250 日)** |
| 输出 | 22 section 完整报告 | **仅因子历史走势表** |
| 文件大小 | ~40KB | ~300KB (step=5) / ~1.5MB (step=1) |
| 用途 | 当下决策 + 短周期 | **长周期结构复盘 (1-2-3-5 年节奏)** |
| 信号 | 含买/卖/警告 | **纯数据, 无主观判断** |

## 命中后

- 长周期看趋势:**Step=1 看 5 年 K 线 + 缠论中枢演变** — 找 5 年级别的中枢切换 / 5 年最大回撤 / 5 年最低 RSI
- 对比分析:把多只 md 并排对比,找 "5 年里哪几只穿越牛熊"
- 教学用途:回看 2021-2024 完整牛熊周期,看缠论 1 买 / 1 卖 / 3 买在长周期里的位置

## 相关

- `/t-analyze <code>` — 单只 22 section 详报 (120 日)
- `/t-near-low` — 5 年回撤 + 距 5y 低 (基于 factor_5y 输出)
- `/t-concept-macd` — 概念板块 MACD (90 日)
- `/t-sync-data --kline` — 跑前必跑 (落盘 K 线到 1250 天)
- `tools/render/factor_history_only.py` — 单只 stdout 版 (本 skill 的更轻量 CLI,无 md 文件)
