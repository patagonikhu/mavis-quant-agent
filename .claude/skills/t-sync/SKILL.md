---
name: t-sync
description: 唯一数据同步入口 (2026-09-03 新)。所有 sync 行为 (K线/财务/EPS/fflow/cache/元数据) 都在 `tools/sync.py`,7 个正交 flag 控制,默认全关,显式开才跑。用户说"同步数据"、"补数据"、"拉K线"、"拉财务"、"拉EPS"、"sync cache"、"sync"都走这个 skill,替代原 sync_watchlist_fresh。
user-invocable: true
allowed-tools:
  - Bash

## 核心原则

> **sync 和 analyze 严格分离**。
> 5 个分析 skill (t-analyze/t-bb-obv/t-near-low/t-magic/t-backtest) 全改"只读",
> 缺数据时直接报"请先 /t-sync", 不再偷偷调 sync 函数。

## 用法 (一图流)

```bash
# 7 个正交 flag, 全部默认关, 显式开才跑
python -m tools.sync --kline          # 增量 K 线 (含 daily_basic + 6 指数)
python -m tools.sync --stock-basic    # 股票基础 (行业/名称, 每月 1 次)
python -m tools.sync --financials     # 财务 5 季度 (fina_indicator_vip 全市场)
python -m tools.sync --eps            # EPS 机构预期 (datacenter.consensus)
python -m tools.sync --fflow          # 主力资金 (Tushare.money_flow)
python -m tools.sync --cache          # signal_cache 缓存 (分析结果缓存)
python -m tools.sync --meta           # 板块/事件 元数据
python -m tools.sync --all-data       # [一键] kline + stock-basic + financials

# 范围 (3 选 1, 默认 --watchlist)
python -m tools.sync --kline                 # 默认: watchlist 101 只
python -m tools.sync --kline --all           # 全市场 5549 只
python -m tools.sync --kline --codes 002371 300750  # 指定
python -m tools.sync --status                # 看现状, 不拉数据
```

## 7 个 flag 设计 (正交)

| flag | 默认 | 触发场景 | 拉什么 |
|---|---|---|---|
| `--kline` | 关 | 每天 / 周末 | K线 + daily_basic + 6 指数 |
| `--stock-basic` | 关 | 月初 | 行业分类, 名称 |
| `--financials` | 关 | 季报出后 (4月/8月/10月底) | fina_indicator_vip 5 季度全市场 |
| `--eps` | 关 | 周末 | datacenter.consensus |
| `--fflow` | 关 | 跑回测前 | Tushare money_flow 10-20 日 |
| `--cache` | 关 | 跑回测前 / 跑分析后 | signal_cache.db |
| `--meta` | 关 | 一次性 | sectors/events |

**正交**: `--cache` 只 sync cache, 其他的不动。`--financials` 只 sync 财务, 不会顺手拉 K 线。

**一键 alias**: `--all-data` = `--kline --stock-basic --financials` (最常用组合)

## 7 个分析 skill 的 sync 行为 (2026-09-03 改造后)

| skill | 偷偷 sync? | 缺数据时 |
|---|---|---|
| `/t-analyze` | ❌ 改成只读 | 报"请先 /t-sync" |
| `/t-bb-obv` | ❌ 改成只读 | 同上 |
| `/t-near-low` | ❌ 改成只读 | 同上 |
| `/t-magic` | ❌ 改成只读 | 同上 |
| `/t-backtest` | ❌ 改成只读 | 同上 |

## 实战节奏

```bash
# 周一早上 (跑分析前)
python -m tools.sync --kline --financials        # 周一 1 次, 2-3 分钟
python -m tools.sync --cache --watchlist         # 缓存最近数据

# 季报出后 (4月/8月/10月底)
python -m tools.sync --all-data --all            # 全市场 5-10 分钟

# 跑回测前
python -m tools.sync --cache --all               # 全市场缓存, 慢
```

## 历史

- 之前 sync 散落在 `sync_watchlist_fresh.py` (148 行, dump_one 有递归子进程 bug)
  + `kline_store.py` (sync_incremental/sync_stock_basic/sync_financials)
  + 5 个 batch 脚本偷偷调 sync
- 2026-09-03 v6.0 改造: 全部合并到 `tools/sync.py`,7 个正交 flag
- 旧 `sync_watchlist_fresh.py` 删除 (合并到 sync.py)

## 执行

```bash
# 单次执行 (默认 watchlist K 线, 0 网络因为默认全关)
bash tools/with_venv.sh python -m tools.sync --kline

# 一次性补全
bash tools/with_venv.sh python -m tools.sync --all-data

# 后台跑 (5+ 分钟用 background, 不用 timeout)
bash tools/with_venv.sh python -m tools.sync --all-data --all
```

## 输出示例

```
=== Mavis sync (scope: watchlist 101 只) ===

[1/7] --kline (增量 K 线)
  📥 补 1 个交易日: 20260902 ~ 20260902
  ✅ K 线: 24 条新增

[2/7] --stock-basic (股票基础)
  ✅ stock_basic: 5549 行

[3/7] --financials (财务 5 季度)
  ⏭ financials 20260630: 97 ok / 4 skip, 跳过
  ✅ financials 20260630: 0 行
  ...

=== 完成, 耗时 12.3 秒 ===
```
