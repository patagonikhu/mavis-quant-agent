---
name: t-finance
description: 财务多维分析 (ROC+EY 联合排名 + 4 季财务大表 + PEG + DCF L). 0 网络, 走 DataStore. 触发词: "财务分析"、"ROC+EY 排名"、"好公司+便宜股"、"低估优质"、"4 季财务".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.finance_top20               # 默认跑 3 件事: 排名 + 摘要 + 加 watchlist
... --rank-only            # 只排名 (不加 watchlist, 不出摘要)
... --summary-only         # 排名 + 4 项摘要 (不加 watchlist)
... --add-watchlist        # 显式加 Top 20 到 watchlist (list_type=ROC_EY初筛)
... --top 50               # 改 Top N
... --period 2026Q2        # 改报告期
... --min-capital 5        # 过滤 NWC+FA < 5 亿
```

## 4 维分析 (1 张排名 + 1 张 4 季大表)

| 维度 | 公式 | 含义 |
|---|---|---|
| **ROC** (Return on Capital) | TTM EBIT / (净营运资本 + 固定资产) | 资本效率, 高 = 好公司 |
| **EY** (Earnings Yield) | TTM EBIT / EV | EV 回报, 高 = 便宜股 |
| **4 季财务** | 营收/净利 yoy/毛利率/ROE/EBIT × 4 季 | 周期分析 (跟 /t-analyze 22 section 第 1 张表复用) |
| **PEG + DCF L** | Forward PE / 3y EPS CAGR / 隐含市值 (r=8/10/12%) | 估值 + 折现 |

**排名公式 (Greenblatt 2005)**: 综合 = (ROC 排名 + EY 排名) / 2, 数字小的胜出

## 关键约束

- **0 网络**, 走 `DataStore.load_financials_period()` + `load_all_daily_basic()`
- **行业过滤**: 银行/保险/证券/信托/期货/租赁/房地产/电力/水务/燃气/环保/多元金融 (ROC/EY 在这些行业失真)
- **质量退化过滤** (2026-09-10 加): yoy 双暴跌 (-20%/-30%) + EBIT 盈但净利巨亏 (-50%) 跳过
- **缺数据**: 报"请先 /t-sync-data --financials"

## 输出

- `docs/finance-top20.md` — Top N 排名表 (10 列)
- `docs/finance-top20-summary.md` — 排名速览 + 每只 4 季大表 (跟 /t-analyze 22 section 第 1 张表完全一致)
- `data/watchlist.json` (追加 `list_type=ROC_EY初筛`)

## 相关

- `/t-analyze <code>` (Top 20 深挖, 22 section) / `/t-bb-obv` / `/t-near-low` / `/t-sync-data` / `/t-guardrail`
