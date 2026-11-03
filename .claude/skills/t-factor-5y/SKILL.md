---
name: t-factor-5y
description: 单只 / 批量跑 5 年因子历史走势, 输出 md (30+ 列: 价格 / 缠论 / 威科夫 / 结构 / 技术指标). 0 网络, 复用 AnalysisEngine.analyze_history + compute_factor_history. 触发词: "5年因子"、"长势"、"5年走势"、"factor-5y".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)

> **单一事实源:** 5 年窗口 / `--step` 语义 / 复用链路 / 命名沿革 / 输出格式 全部在 `tools/batch/factor_5y.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候用 + 跑 + 数据特性"。

## 啥时候用

- 单只看 5 年长周期(找中枢切换、5 年最大回撤、5 年最低 RSI)
- 多只并行扫描找"穿越牛熊"的票
- 教学:回看 2021-2024 完整牛熊周期,看缠论 1 买 / 1 卖 / 3 卖 在长周期里的位置
- 想看**长周期结构复盘**,**不替代** `/t-analyze` (17 section 短周期)

## 跑

```bash
# 单只
bash tools/with_venv.sh python -m tools.batch.factor_5y --code 600089

# 批量 (默认 watchlist 持仓)
bash tools/with_venv.sh python -m tools.batch.factor_5y
bash tools/with_venv.sh python -m tools.batch.factor_5y --watchlist-types 持仓,blowout
bash tools/with_venv.sh python -m tools.batch.factor_5y --all-market

# 紧凑视图 (1250/5=250 行)
bash tools/with_venv.sh python -m tools.batch.factor_5y --step 5

# 所有 flag + 默认值 (单一事实源)
bash tools/with_venv.sh python -m tools.batch.factor_5y --help
```

## 数据特性

- **0 网络**, 走 DataStore parquet
- 5 年窗口硬编码: `lookback=1250` (≈ 252 交易日 × 5 年)
- 23 只持仓批量 ~20 秒 (`--workers 4`)
- **不替代 /t-analyze**:本 skill 只输出因子历史表(长周期 1-2-3-5 年节奏);`/t-analyze` 是 17 section 全要素详报

## 跑后验证

```bash
# 看 5 年 + 数据范围 + 摘要
ls docs/factor-5y/ | head
head -50 docs/factor-5y/analyze-600089-特变电工-5y.md
```

## 相关

- `/t-analyze <code>` (单只 17 section 详报, 120 日)
- `/t-sync-data --kline` (跑前必跑,落盘 K 线到 1250 天)
