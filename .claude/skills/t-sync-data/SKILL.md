---
name: t-sync-data
description: 唯一数据同步入口. 7 个正交 flag (kline/stk-factor/stock-basic/financials/eps/fflow/cache), 默认 --auto 智能检测 stale. 触发词: "同步数据"、"拉K线/财务/EPS/fflow"、"sync cache"、"sync 一下".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
# 默认智能 (推荐, 多数 0 网络)
bash tools/with_venv.sh python -m tools.storage.sync
bash tools/with_venv.sh python -m tools.storage.sync --auto-dry       # 试运行

# 7 个正交 flag (按需刷单个)
... --kline              # 增量 K 线 (每天)
... --stk-factor         # 17 列估值因子 (5 季 1 次, 8 分钟)
... --stock-basic        # 名称/行业 (30 天 1 次)
... --financials         # 5 季财务
... --eps                # 机构一致预期 (datacenter)
... --fflow              # 主力资金 (按天全市场, ~13 分钟)
... --cache              # signal_cache (跑回测前)

# 范围 (3 选 1, 默认 --all)
... --all                 # 全市场
... --codes 002371 300750 # 指定
```

## 关键约束

- **唯一允许网络的 skill** (其他 8 个全部 0 网络, 走 DataStore)
- **`--eps` 范围互斥** (守门员): 默认 watchlist, `--eps --all` 强制缩回 watchlist + warning
- 写盘唯一路径: `tools.storage.sync`, 业务层不直连 db/网络

## 相关

- 7 个分析 skill (read-only): `/t-analyze` / `/t-bb-obv` / `/t-near-low` / `/t-roc-ey` / `/t-earnings-blowout` / `/t-sector-ma` / `/t-backtest`
- `/t-guardrail` (含 eps-scope-guard)
