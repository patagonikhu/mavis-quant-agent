---
name: t-roc-ey
description: ROC + EY 联合排名 (Greenblatt 公式), 找"好公司+便宜股"双优. 0 网络, 走 DataStore. 触发词: "ROC/EY 排名"、"好公司+便宜股"、"资本效率排名"、"低估优质".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.roc_ey_top20               # 默认 Top 20 (不加 watchlist)
... --rank-only            # 只排名
... --summary-only         # 排名 + 4 项摘要
... --add-watchlist        # 显式加 Top 20 到 watchlist (list_type=ROC_EY初筛)
... --top 50               # 改 Top N
... --period 2026Q2        # 改报告期
... --min-capital 5        # 过滤 NWC+FA < 5 亿
```

## 关键约束

- **0 网络**, 走 `DataStore.load_financials_period()` + `load_all_daily_basic()`
- **行业过滤**: 银行/保险/证券/信托/期货/租赁/房地产/电力/水务/燃气/环保/多元金融 (ROC/EY 在这些行业失真)
- **缺数据**: 报"请先 /t-sync-data --financials"

## 输出

- `docs/roc-ey-top20.md` / `docs/roc-ey-top20-summary.md`
- `data/watchlist.json` (追加 `list_type=ROC_EY初筛`)

## 相关

- `/t-analyze <code>` (Top 20 深挖) / `/t-bb-obv` / `/t-near-low` / `/t-sync-data`
