---
name: t-sync-data
description: 唯一数据同步入口. 7 个正交 flag 控制 sync 行为, 默认 --auto 智能检测 stale. 触发词: "同步数据"、"拉K线/财务/EPS/fflow"、"sync cache"、"sync 一下".
user-invocable: true
allowed-tools:
  - Bash

## 核心原则

- 所有数据同步走 `tools/storage/sync.py` 一个入口
- 分析 skill (analyze / bb-obv / near-low / magic / backtest) **read-only**, 缺数据时报"请先 /t-sync-data"
- 数据写盘唯一路径: `tools.storage.sync`, 业务层不直连 db/网络

## 用法

```bash
# 智能模式 (推荐, 多数情况 0 网络)
python -m tools.storage.sync                 # --auto 默认

# 试运行 (只显示会跑啥)
python -m tools.storage.sync --auto-dry

# 强刷 stale
python -m tools.storage.sync --auto-force

# 7 个正交 flag
python -m tools.storage.sync --kline          # 增量 K 线 + 6 指数
python -m tools.storage.sync --stk-factor     # 重拉 stk_factor_pro 16 列 (5 季, 8 分钟)
python -m tools.storage.sync --stock-basic    # 行业/名称 (30 天 1 次)
python -m tools.storage.sync --financials     # 5 季财务 (fina_indicator_vip 全市场)
python -m tools.storage.sync --eps            # EPS 机构预期 (datacenter)
python -m tools.storage.sync --fflow          # 主力资金 (Tushare.money_flow)
python -m tools.storage.sync --cache          # signal_cache 缓存

# 一键 alias
python -m tools.storage.sync --all-data       # kline + stock-basic + financials

# 范围 (3 选 1, 默认 --watchlist)
python -m tools.storage.sync --all            # 全市场
python -m tools.storage.sync --codes 002371 300750
```

## 8 个 flag 含义

| Flag | 数据源 | 频率 |
|---|---|---|
| `--kline` | Tushare daily (按日增量) | 每天 |
| `--stk-factor` | Tushare stk_factor_pro (16 列, 含 ps/dv_ratio/float_share) | 5 季一次 (8 分钟) |
| `--stock-basic` | Tushare stock_basic + stk_factor 兜底股本 | 30 天 |
| `--financials` | Tushare fina_indicator_vip (全市场 1 次 API) | 5 季 |
| `--eps` | datacenter.eastmoney.com (机构一致预期) | 30 天 TTL |
| `--fflow` | Tushare.money_flow (主力资金) | 每天 |
| `--cache` | analysis_cache.db (24 列因子) | 跑前 |
| `--meta` | 占位, 暂未实现 | — |

## 实战节奏

```bash
# 周一早上
python -m tools.storage.sync                 # 智能检测, 多数 0 网络

# 盘后
python -m tools.storage.sync                  # 补今天 K 线 + stk_factor

# 季报出后
python -m tools.storage.sync --all-data --all # 全市场 5-10 分钟

# 跑回测前
python -m tools.storage.sync --cache --all    # 全市场缓存
```

## 输出

sync 跑完打印本地数据新鲜度:

```
📊 本地数据新鲜度 (最新一天):
  K线 (OHLCV)        : 20260903
  stk_factor (估值)  : 20260903
  financials (季报)  : 20260630
  EPS (机构预期)     : 2026-09-03 22:28
  fflow (资金流)     : 20260903
  stock_basic (静态) : mtime 2026-09-04 07:27
```

## 数据源 (DAO 层)

`DataStore` (tools/storage/store.py) 是唯一数据读接口. 25+ 公开方法, 6 个 bulk 接口, **业务层不直连 parquet 或 db**.

## 相关

- `/t-analyze` / `/t-bb-obv` / `/t-near-low` / `/t-magic` / `/t-backtest` — 5 个 read-only 分析 skill, 跑前先 sync
- `tools/storage/store.py` — DataStore DAO
- `tools/storage/sources/` — Tushare / eastmoney 接口封装
