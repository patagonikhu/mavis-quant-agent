---
name: t-near-low
description: 每周扫"跌 70-80% + 距 5y 低 < 3%"清单. 0 网络, 走 DataStore. 触发词: "距 5y 低"、"近底"、"超跌清单".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.find_near_low               # 默认 70-80% 跌 + 距 5y 低 < 3%
... --gap 5                # 宽松 < 5%
... --gap 2                # 严格 < 2%
... --drop 80 --drop-max 90 # 跌 80-90%
... --lookback-years 3     # 3y lookback
... --min-bounces 4        # 反弹次数 ≥ 4
... --write-md             # 写 docs/oversold-watchlist.md
```

## 关键约束

- **0 网络**, 走 `DataStore.load_all_kline(years=5.5)` 拿 5y weekly
- **3 维**: 5y 回撤 70-80% + 距 5y 低 < 3% + 反弹次数 ≥ 0
- **缺数据**: 报"请先 /t-sync-data"
- **非价值投资**, 反弹策略 (1-3 个月)

## 输出

- `docs/oversold-watchlist.md` (8-13 只, 按距低 % 升序)

## 相关

- `/t-analyze <code>` (命中后深挖) / `/t-bb-obv` / `/t-finance` / `/t-sync-data`
