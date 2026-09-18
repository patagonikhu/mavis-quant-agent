---
name: t-finance-earnings-blowout
description: Earnings Blowout 财季炸裂扫描. R3 v6.2.7 启动期反转信号: 营收 yoy>=25% + 净利 yoy>=50% + 毛利率 (升 OR 跌幅≤2pp) + (净利 yoy 跳升>=50pp OR 持续高增龙头). **财务类, 季报披露后跑一次** (1/5/9/11月). 0 网络, 走 DataStore. 触发词: "业绩反转"、"Earnings Blowout"、"R3 启动期"、"10x 票".
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
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout       # 默认 R3 + 位置过滤
... --include-cycle        # 含周期股
... --no-position-filter   # 关闭位置过滤
... --jump-mode reverse    # 只看反转 (净利跳升 50pp)
... --jump-mode leader     # 只看龙头 (or_yoy>=80% AND np_yoy>=80%)
... --np-jump 100          # 净利跳升 100pp 更严
... --limit 10             # Top 10
... --no-md                # 只 stdout
```

## 关键约束

- **0 网络**, 走 `DataStore.load_all_financials()` (financials parquet)
- **R3 触发模式 `--jump-mode`**: `reverse` / `leader` / `either` (默认)
- **位置过滤默认开**: 距 1y 低 <= 200% + 距 1y 高 >= -30%
- **周期股默认排除** (SW 周期类 + 电气设备)
- **必设 -15% 止损 / +50% 止盈** (R3 抓启动期, 票波动大)
- **缺数据**: 报"请先 /t-sync-data --financials"

## 输出

- `docs/earnings-blowout-watchlist.md` (按季分 section)

## 相关

- `/t-analyze <code>` (命中后深挖)
- **`/t-finance-roc-ey`** (配套: 财务多维分析, 也是季报驱动)
- `/t-near-low` (5y 回撤超跌)
- `/t-rsi6-tech` (RSI 超卖, 日级)
- `/t-sync-data --financials` (跑前必跑)

## 命名沿革

- **v1: `/t-earnings-blowout`** (2026-08 起, 原名)
- **v2: `/t-finance-earnings-blowout`** (2026-09-18 改) — 加 `finance-` 前缀与 `/t-rsi6-tech` 等技术类区分
