---
name: t-macd-r2g
description: MACD 红转绿信号 (红柱≥N天 + 最近2根翻绿). 0 网络. 触发词: "红转绿"、"MACD 底部"、"红柱翻绿".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.macd_r2g_scan              # 默认: MACD 红转绿 (红柱≥20天)
... --min-red 15          # 红柱天数 ≥ 15 天 (实战常用, 默认 20 偏严)
... --write-md            # 写 docs/macd-r2g-watchlist.md
... --limit 100           # 调试: 只扫前 N 只
... --workers 8           # 线程数 (默认 4)
... --no-junk-filter      # 跳过垃圾股过滤
```

## 关键约束

- **0 网络**: `DataStore.get_ctx(kline_only=True)` 跳过 EPS/fflow 网络拉取
- **架构**: 纯内置 MACD 指标 (`_macd_arr`), 不走 L2 因子库, 不算任何"用不到"的特征数组
- **算法**: 红柱持续 ≥ N 天 + 最近 2 根 K 线翻绿 (刚转绿)
- **质量**: ⭐ = bar_diff 已转正 (动能反转初期)
- **性能**: 3638 只 ~1.5min
- **缺数据**: 报"请先 /t-sync-data"

## 输出

- `docs/macd-r2g-watchlist.md`
- 命中后用 `/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-roc-ey` / `/t-earnings-blowout` / `/t-sector-ma` / `/t-backtest` / `/t-sync-data`