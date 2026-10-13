---
name: t-sync-data
description: 唯一数据同步入口. 9 个正交 flag (kline/stk-factor/stock-basic/financials/eps/fflow/cache/ths/etf/all-data). **--auto 检测 4+ETF (kline/stk-factor/stock-basic/financials + ETF 日增量)**, 其他 (eps/fflow/cache/ths) 按需手动. **末尾输出 tushare 网络请求统计**. 触发词: "同步数据"、"拉K线/财务/EPS/fflow"、"sync cache"、"sync 一下".
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
... --ths                # THS 同花顺概念板块 K 线 (按 ths_whitelist, 默认 3 年回填)
... --etf                # ETF 场内基金 (fund_basic 月级 + fund_daily 日增量, 默认随 --auto 跑)
... --all-data           # [一键] --kline --stock-basic --financials 一起跑 (最常用)

# 范围 (3 选 1, 默认 --all)
... --all                 # 全市场
... --codes 002371 300750 # 指定
```

## 关键约束

- **唯一允许网络的 skill** (其他全部 0 网络, 走 DataStore)
- **`--eps` 范围互斥** (守门员): 默认 watchlist, `--eps --all` 强制缩回 watchlist + warning
- **`--ths` 默认不拉** (显式才跑), ths_index 30 天缓存 1 刷, ths_daily 增量续存
- **`--etf` 随 --auto 默认跑** (日增量幂等, 已是最新秒返回); fund_basic 月级缓存 30 天
- 写盘唯一路径: `tools.storage.sync`, 业务层不直连 db/网络

## ETF 数据说明 (2026-10-09 加)

| 操作 | 接口 | 频率 | 存储 |
|---|---|---|---|
| ETF 基本信息 | `fund_basic(market='E')` | 月级 (30 天缓存) | `data/history/etf/etf_basic.parquet` |
| ETF K线增量 | `fund_daily(trade_date=date)` | 日级 (1次/天) | `data/history/etf/kline/<ts_code>.parquet` |

首次 bootstrap (~2年历史): ~517 次请求，之后每天 1 次。  
白名单: `config/etf_whitelist.json` (15 只默认, 可自定义)。

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

- 分析 skill (read-only): `/t-analyze` / `/t-near-low` / `/t-rsi6-tech` / `/t-finance-roc-ey` / `/t-finance-earnings-blowout` / `/t-etf` / `/t-backtest` / `/t-guardrail`
