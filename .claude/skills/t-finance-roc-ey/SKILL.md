---
name: t-finance-roc-ey
description: 财务多维分析 (ROC+EY 联合排名 + 4 季财务大表). 财务类, 0 网络, 走 DataStore. **季报披露后跑一次** (1/5/9/11月). 触发词: "财务分析"、"ROC+EY 排名"、"好公司+便宜股"、"低估优质"、"4 季财务".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** 公式 / 排名算法 / 行业过滤 / 输出文件 / 命名沿革 全部在 `tools/batch/finance_roc_ey.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候跑 + 跑 + 数据特性 + 跑后验证"。

## 啥时候跑 (季报驱动)

| 季报 | 截止 | 跑窗口 |
|---|---|---|
| Q4 (12-31) | 4-30 次年截止 | **1 月** |
| Q1 (3-31) | 4-30 截止 | **5 月** |
| Q2 (6-30) | 8-31 截止 | **9 月** |
| Q3 (9-30) | 10-31 截止 | **11 月** |

## 跑

```bash
# 默认 (3 步: 排名 + 摘要 + 加 watchlist)
bash tools/with_venv.sh python -m tools.batch.finance_roc_ey

# 所有 flag + 默认值
bash tools/with_venv.sh python -m tools.batch.finance_roc_ey --help

# 改 Top N / 改报告期
bash tools/with_venv.sh python -m tools.batch.finance_roc_ey --top 50 --period 2026Q2

# 跳过 watchlist / 只摘要
bash tools/with_venv.sh python -m tools.batch.finance_roc_ey --skip-watchlist
bash tools/with_venv.sh python -m tools.batch.finance_roc_ey --summary-only
```

## 数据特性

- **0 网络**, 走 `DataStore.load_financials_period()` + `load_all_daily_basic()` (绕开 watchlist gate)
- **行业过滤**: 银行/保险/证券/信托/期货/租赁/房地产/电力/水务/燃气/环保/多元金融 (ROC/EY 在这些行业失真)
- **质量退化过滤** (2026-09-10 加): yoy 双暴跌 + EBIT 盈但净利巨亏 跳过
- 缺数据报错 "请先 `/t-sync-data --financials`"

## 跑后验证

```bash
# Top 20 排名表
head -50 docs/roc-ey-top20.md

# 排名速览 + 4 季大表
ls docs/roc-ey-top20-summary.md && head -100 docs/roc-ey-top20-summary.md
```

## 相关

- **`/t-finance-earnings-blowout`** (配套: R3 启动期反转, 也是季报驱动)
- `/t-analyze <code>` (Top 20 深挖)
- `/t-rsi6-tech` (RSI 超卖, 日级)
- `/t-sync-data --financials` (跑前必跑)
