---
name: t-magic
description: Magic Formula 排名 (Greenblatt ROC + EY 联合排名), 找"好公司+便宜股"双优. 0 网络, 走 DataStore. 触发词: "Magic 排名"、"ROC/EY 排名"、"好公司+便宜股"、"加 watchlist".
user-invocable: true
allowed-tools:
  - Bash

## 原理 (Greenblatt 2005, 《The Little Book That Beats the Market》)

| 指标 | 公式 | 含义 |
|---|---|---|
| **ROC** (Return on Capital) | TTM EBIT / (净营运资本 + 固定资产) × 100% | 资本效率, **高 = 好公司** |
| **EY** (Earnings Yield) | TTM EBIT / EV × 100% | 盈利对 EV 回报, **高 = 便宜股** |
| **EV** | 市值 + 净债务 | 买下整家公司要付的总价 |
| **联合排名** | (ROC 排名 + EY 排名) / 2 | 数字小的胜出 (双优) |

**行业过滤**: 银行/保险/证券/信托/期货/租赁/房地产/电力/水务/燃气/环保/多元金融 (8+5 类, ROC/EY 在这些行业失真, 跳过)

## 用法

```bash
/t-magic                          # 默认 Top 20
/t-magic --rank-only              # 只排名, 不出摘要不加 watchlist
/t-magic --summary-only           # 排名 + 4 项摘要 (PEG/DCF/Magic/卡点)
/t-magic --top 50                 # 改 Top N
/t-magic --period 2026Q2          # 改报告期
/t-magic --skip-watchlist         # 不加 watchlist
/t-magic --min-capital 5          # 过滤 NWC+FA < 5 亿 (剔除分母过小假阳性)
```

## 数据来源

`DataStore` (DAO 层) → `data/history/{financials,stk_factor,stock_basic,eps}/` parquet

**0 网络**, 缺数据时: `请先 /t-sync-data`

## 输出

| 文件 | 内容 |
|---|---|
| `docs/magic-top20.md` | Top 20 排名表 + 统计 + 用法 |
| `docs/magic-top20-summary.md` | 4 项摘要 (PEG / DCF / Magic 排名 / 卡点⭐) |
| `data/watchlist.json` | 追加 `list_type="Magic初筛"` (跳过已存在) |

## 实战策略

| 信号 | 行动 |
|---|---|
| Magic #1-#5 + PEG<1.5 | 🥇 高信念, 双侧便宜 (重点关注) |
| Magic #1-#10 + PEG>2 | 🥉 好公司但贵, 等 PEG 修复 |
| Magic #10-#20 + PEG<1.5 | 🥈 标准, 便宜但资本效率一般 |
| 任意 + L/E3>8 或 L/可达>2 | ❌ 叙事透支, 不买 |

**OBV 信号版块适用性:**
- ✅ 光学/封测/HBM: 主力控盘度高, OBV 准
- ❌ 周期股 (矿用车/锂电/钢铁): 行业 β 主导, OBV 被淹没
- ❌ 题材/小盘: 主力分散, 噪声大

## 相关

- `/t-analyze <code>` — Magic Top 20 中某只深挖 22 section
- `/t-bb-obv` — 短期吸筹形态扫描
- `/t-near-low` — 距 5y 低 < 3% 超跌清单
