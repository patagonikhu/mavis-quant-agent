---
name: t-sector-ma
description: 板块均线顶底扫描 (MA5/20/60/120 + 偏离 + 金叉死叉) + 4 阶段分类. 0 网络, 走 DataStore. 触发词: "板块顶底"、"板块均线"、"板块见顶"、"板块见底".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.sector_ma_scan                        # 默认全市场顶底
... 半导体                   # 指定行业
... 半导体 --side bottom    # 只看底
... 纺织机械 --side phase  # 看 4 阶段分类
... 半导体 --top-dev 10     # 偏离 +10% 更敏感
... --list                  # 列所有行业
```

## 关键约束

- **0 网络**, 走 `DataStore.load_all_kline(years=1.5)` 拿全市场 (含 MA60 算 60 根)
- **4 均线体系**: MA5(短线) / MA20(月度) / MA60(牛熊) / MA120(长期)
- **顶/底分数**: ≥6 强 / 4-5 中 / 2-3 弱 (顶减仓, 底建仓)
- **4 阶段**: 顶部杀跌 / 装死阴跌 / 筑底 / 真底 (放量见底才是真底)
- **板块顶底 ≠ 个股顶底**: 板块见顶时**可能还有补涨龙头**
- **缺数据**: 报"请先 /t-sync-data"

## 输出

- 单行业: 板块顶底信号 + 板块内个股涨跌幅 Top 20
- 全市场: 所有顶/底信号板块 (按 score 排序)

## 相关

- `/t-analyze <code>` (板块内真龙头) / `/t-bb-obv` / `/t-near-low` / `/t-finance` / `/t-earnings-blowout` / `/t-sync-data`
