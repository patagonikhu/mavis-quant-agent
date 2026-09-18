---
name: t-finance-roc-ey
description: 财务多维分析 (ROC+EY 联合排名 + 4 季财务大表 + PEG + DCF L). 财务类, 0 网络, 走 DataStore. **季报披露后跑一次** (1/5/9/11月). 触发词: "财务分析"、"ROC+EY 排名"、"好公司+便宜股"、"低估优质"、"4 季财务".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 🕐 跑节奏 (财务类 — 季报驱动)

**最佳时机**: 季报披露高峰结束后跑

| 季报 | 截止 | 集中披露 | **跑窗口** |
|---|---|---|---|
| Q4 (12-31) | 4-30 次年截止 | 3-15 ~ 4-30 | **1 月** |
| Q1 (3-31) | 4-30 截止 | 4-15 ~ 4-30 | **5 月** |
| Q2 (6-30) | 8-31 截止 | 8-15 ~ 8-31 | **9 月** |
| Q3 (9-30) | 10-31 截止 | 10-15 ~ 10-31 | **11 月** |

**一周/一天跑 1 次无新增价值** — 季报数据每季才更新一次。

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.finance_roc_ey               # 默认跑 排名 + 摘要
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

- `/t-analyze <code>` (Top 20 深挖, 22 section)
- **`/t-finance-earnings-blowout`** (配套: R3 启动期反转, 也是季报驱动)
- `/t-near-low` (5y 回撤超跌, 周/月级)
- `/t-rsi6-tech` (RSI 超卖, 日级)
- `/t-sync-data --financials` (跑前必跑)

## 命名沿革

- **v1: `/t-roc-ey`** (2026-07 起, 原名)
- **v2: `/t-finance-roc-ey`** (2026-09-18 改) — 加 `finance-` 前缀与 `/t-rsi6-tech` 等技术类区分
