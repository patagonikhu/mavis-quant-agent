---
name: t-sync
description: 唯一数据同步入口 (2026-09-03 新, v6.1.1)。所有 sync 行为 (K线/财务/EPS/fflow/cache/元数据) 都在 `tools/sync_data.py`, 7 个正交 flag (`--kline/--stock-basic/--financials/--eps/--fflow/--cache/--meta`) 控制, 默认走 `--auto` 智能检测 stale flag (解决"忘记 sync"问题)。用户说"同步数据"、"补数据"、"拉K线"、"拉财务"、"拉EPS"、"sync cache"、"sync"、"sync_data"、"datasync"、"sync-wiki"、"sync watchlist" 都走这个 skill, 替代原 sync_watchlist_fresh。
user-invocable: true
allowed-tools:
  - Bash

## 核心原则

> **sync 和 analyze 严格分离**。
> 5 个分析 skill (t-analyze/t-bb-obv/t-near-low/t-magic/t-backtest) 全改"只读",
> 缺数据时直接报"请先 /t-sync", 不再偷偷调 sync 函数。

## 别名 / 触发词

用户用以下任何说法都触发 t-sync:
- `/t-sync` / `/sync-data` / `/sync_data` / `/datasync`
- "同步数据" / "补数据" / "拉数据" / "拉K线" / "拉财务" / "拉EPS" / "拉fflow" / "sync cache"
- "更新本地数据" / "刷新数据" / "补缓存" / "sync一下"

底层唯一入口: `python -m tools.sync_data` (v6.1.1 改名, 之前叫 sync.py / datasync.py)

## 用法 (一图流)

```bash
# 智能模式 (推荐, 解决"忘记 sync"问题)
python -m tools.sync_data                 # 默认 --auto: 智能检测 stale, 只跑需跑的
python -m tools.sync_data --auto-dry      # 试运行, 只显示会跑什么
python -m tools.sync_data --auto-force    # 强刷所有 stale

# 7 个正交 flag, 全部默认关, 显式开才跑
python -m tools.sync_data --kline          # 增量 K 线 (含 daily_basic + 6 指数)
python -m tools.sync_data --stock-basic    # 股票基础 (行业/名称, 每月 1 次)
python -m tools.sync_data --financials     # 财务 5 季度 (fina_indicator_vip 全市场)
python -m tools.sync_data --eps            # EPS 机构预期 (datacenter.consensus)
python -m tools.sync_data --fflow          # 主力资金 (Tushare.money_flow)
python -m tools.sync_data --cache          # signal_cache 缓存 (分析结果缓存)
python -m tools.sync_data --meta           # 板块/事件 元数据
python -m tools.sync_data --all-data       # [一键] kline + stock-basic + financials

# 范围 (3 选 1, 默认 --watchlist)
python -m tools.sync_data --kline                 # 默认: watchlist 101 只
python -m tools.sync_data --kline --all           # 全市场 5549 只
python -m tools.sync_data --kline --codes 002371 300750  # 指定
python -m tools.sync_data --status                # 看现状, 不拉数据
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
# 周一早上 (跑分析前, 推荐 --auto 智能检测)
python -m tools.sync_data                          # 智能检测, 只跑 stale 的 (多数情况 0 网络)
python -m tools.sync_data --kline --financials     # 强制跑 (不在乎检测)
python -m tools.sync_data --cache --watchlist      # 缓存最近数据

# 季报出后 (4月/8月/10月底)
python -m tools.sync_data --all-data --all         # 全市场 5-10 分钟

# 跑回测前
python -m tools.sync_data --cache --all            # 全市场缓存, 慢

# 试运行 (看会跑啥, 不真跑)
python -m tools.sync_data --auto-dry
```

## 历史

- 之前 sync 散落在 `sync_watchlist_fresh.py` (148 行, dump_one 有递归子进程 bug)
  + `kline_store.py` (sync_incremental/sync_stock_basic/sync_financials)
  + 5 个 batch 脚本偷偷调 sync
- 2026-09-03 v6.0 改造: 全部合并到 `tools/sync.py`,7 个正交 flag
- 2026-09-03 v6.1 改造: 改名 `tools/sync_data.py` + 加 `--auto` 智能检测 stale flag
- 旧 `sync_watchlist_fresh.py` 删除 (合并到 sync.py)

## 执行

```bash
# 推荐: 智能检测 (--auto), 多数情况 0 网络
bash tools/with_venv.sh python -m tools.sync_data

# 试运行 (看会跑什么)
bash tools/with_venv.sh python -m tools.sync_data --auto-dry

# 一次性补全
bash tools/with_venv.sh python -m tools.sync_data --all-data

# 后台跑 (5+ 分钟用 background, 不用 timeout)
bash tools/with_venv.sh python -m tools.sync_data --all-data --all
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
