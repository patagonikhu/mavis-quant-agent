---
name: t-backtest
description: 信号回测 — 扫描 N 年历史, 统计信号触发后未来 N 天最大涨幅命中率. 0 网络, 走 DataStore. 触发词: "回测"、"信号胜率"、"历史统计".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
# 单信号
... --signal Spring / Accumulation / fflow:强进货 / 1买 / 顶背驰

# 组合 (AND)
... --signal Spring --signal fflow:强进货

# 关键参数
... --days 30 --threshold 10  # 持仓 30 天, 涨幅阈值 10%
... --lookback 5              # 回看 5 年
... --codes 300274            # 指定股票
... --all                     # 全 watchlist
... --portfolio               # 仅持仓
... --write-md                # 写 docs/backtest-*.md
```

## 关键约束

- **0 网络**, 走 `DataStore` + `AnalysisEngine.analyze_history()` 入口 (不绕过)
- **5 年 backfill**: 跑前先 `python -m tools.storage.sync --cache`
- **缓存命中**: 7 只持仓 × 1250 天 × 7 strategy ≈ 3.5 分钟 (无 cache) / 秒级 (有 cache)
- **信号类别**: 威科夫子事件 / 缠论买卖点 / 威科夫阶段 / fflow 5 档 / OBV / 背驰

## 输出

- stdout: 命中数 / 命中率 / 均涨幅
- `--write-md`: `docs/backtest-{信号名}.md`

## 相关

- `/t-analyze` / `/t-roc-ey` / `/t-sync-data` / `tools/batch/batch_backtest.py` (回测引擎) / `tools/analysis/signal_cache.py` (24 列因子缓存)
