---
name: t-near-low
description: 每周扫"跌 70-80% + 距 5y 低 < 3%"清单. 0 网络, 走 DataStore. 触发词: "距 5y 低"、"近底"、"超跌清单".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** 链路 / 默认 3 维过滤 / 输出文件 全部在 `tools/batch/find_near_low.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候用 + 跑"。

## 啥时候用

- 周级扫描超跌清单 (5y 回撤 70-80% + 距 5y 低 < 3%)
- **非价值投资**, 反弹策略 (1-3 个月)

## 跑

```bash
# 默认
bash tools/with_venv.sh python -m tools.batch.find_near_low

# 所有 flag + 默认值
bash tools/with_venv.sh python -m tools.batch.find_near_low --help

# 改门槛 / 写 watchlist
bash tools/with_venv.sh python -m tools.batch.find_near_low --gap 5 --min-bounces 4 --write-md
```

## 数据特性

- **0 网络**, 走 `DataStore.load_all_kline(years=5.5)` 拿 5y daily
- 5.5y lookback hard-coded
- 缺数据报错 "请先 `/t-sync-data`"

## 跑后验证

```bash
bash tools/with_venv.sh python -m tools.batch.find_near_low --write-md
cat docs/oversold-watchlist.md
```

## 相关

- `/t-analyze <code>` (命中后深挖)
- `/t-factor-5y` (5 年因子历史)
- `/t-sync-data` (跑前必跑)
