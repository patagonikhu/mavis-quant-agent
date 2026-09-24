---
name: t-sync-data
description: 唯一数据同步入口. 9 个正交 flag (kline/stk-factor/stock-basic/financials/eps/fflow/cache/ths/all-data), 默认 --auto 智能检测 stale. **末尾输出 tushare 网络请求统计**. 触发词: "同步数据"、"拉K线/财务/EPS/fflow"、"sync cache"、"sync 一下".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
# 默认智能 (推荐, 多数 0 网络, 末尾输出网络统计)
bash tools/with_venv.sh python -m tools.storage.sync

# 9 个正交 flag (按需刷单个)
... --kline              # 增量 K 线 (每天)
... --stk-factor         # 17 列估值因子 (5 季 1 次, 8 分钟)
... --stock-basic        # 名称/行业 (30 天 1 次)
... --financials         # 5 季财务
... --eps                # 机构一致预期 (datacenter)
... --fflow              # 主力资金 (按天全市场, ~13 分钟)
... --cache              # signal_cache (跑回测前)
... --ths                 # THS 同花顺概念板块 K 线 (按 ths_whitelist, 默认 3 年回填)
... --all-data           # [一键] --kline --stock-basic --financials 一起跑 (最常用)

# 范围 (3 选 1, 默认 --all)
... --all                 # 全市场
... --codes 002371 300750 # 指定
```

## 关键约束

- **唯一允许网络的 skill** (其他 8 个全部 0 网络, 走 DataStore)
- **`--eps` 范围互斥** (守门员): 默认 watchlist, `--eps --all` 强制缩回 watchlist + warning
- **`--ths` 默认不拉** (显式才跑), ths_index 30 天缓存 1 刷, ths_daily 增量续存
- 写盘唯一路径: `tools.storage.sync`, 业务层不直连 db/网络

## 网络请求统计 (2026-09-18 加)

每次 sync 末尾自动输出本次实际调用的 tushare API 次数:

```
🌐 网络请求统计 (总计 8 次):
   index_daily                  6 次
   daily                        1 次
   fina_indicator_vip           1 次
```

按 API 分组, 调用次数降序, 0 次的不显示. 用途:
- 0 网络排查 (看是否意外产生 API 调用)
- 性能监控 (哪个 API 调用最多)
- sync 模式验证 (sync --auto 全 fresh 时应该是 0)

## 相关

- 8 个分析 skill (read-only): `/t-analyze` / `/t-near-low` / `/t-rsi6-tech` / `/t-finance-roc-ey` / `/t-finance-earnings-blowout` / `/t-concept-macd` / `/t-backtest` / `/t-guardrail`
