---
name: t-sync-data
description: 唯一数据同步入口。所有 sync 行为 (K线/财务/EPS/fflow/cache/元数据) 都在 `tools/storage/sync.py`, 7 个正交 flag (`--kline/--stock-basic/--financials/--eps/--fflow/--cache/--meta`) 控制, 默认走 `--auto` 智能检测 stale flag (解决"忘记 sync"问题)。用户说"同步数据"、"补数据"、"拉K线"、"拉财务"、"拉EPS"、"sync cache"、"sync" 都走这个 skill, 替代原 sync_watchlist_fresh。
user-invocable: true
allowed-tools:
  - Bash

## 核心原则 (v6.2.3)

> **sync 和 analyze 严格分离**。
> 5 个分析 skill (t-analyze/t-bb-obv/t-near-low/t-magic/t-backtest) 全改"只读",
> 缺数据时直接报"请先 /t-sync-data", 不再偷偷调 sync 函数。

> **所有数据/网络操作只在 `tools/storage/` 下** (v6.2.2 架构守门)。
> 5 个分析 skill + N 个 batch 脚本, 读走 `DataStore` / `caches/analysis.*`,
> 写走 `sync.py`, 都不直连 db/网络。

## 别名 / 触发词

用户用以下任何说法都触发 t-sync-data:
- `/t-sync-data` / `/t-sync` / `/sync-data` / `/sync_data` / `/datasync`
- "同步数据" / "补数据" / "拉数据" / "拉K线" / "拉财务" / "拉EPS" / "拉fflow" / "sync cache"
- "更新本地数据" / "刷新数据" / "补缓存" / "sync一下"

底层唯一入口: `python -m tools.storage.sync`

## 用法 (一图流)

```bash
# 智能模式 (推荐, 解决"忘记 sync"问题)
python -m tools.storage.sync                 # 默认 --auto: 智能检测 stale, 只跑需跑的
python -m tools.storage.sync --auto-dry      # 试运行, 只显示会跑什么
python -m tools.storage.sync --auto-force    # 强刷所有 stale

# 7 个正交 flag, 显式开才跑
python -m tools.storage.sync --kline          # 增量 K 线 (含 daily_basic + 6 指数)
python -m tools.storage.sync --stock-basic    # 股票基础 (行业/名称, 每月 1 次)
python -m tools.storage.sync --financials     # 财务 5 季度 (fina_indicator_vip 全市场)
python -m tools.storage.sync --eps            # EPS 机构预期 (datacenter.consensus)
python -m tools.storage.sync --fflow          # 主力资金 (Tushare.money_flow)
python -m tools.storage.sync --cache          # signal_cache 缓存 (analysis_cache.db)
python -m tools.storage.sync --meta           # 板块/事件 元数据 (占位, 暂未实现)

# 一键 alias
python -m tools.storage.sync --all-data       # kline + stock-basic + financials

# 范围 (3 选 1, 默认 --watchlist)
python -m tools.storage.sync --kline                 # 默认: watchlist 101 只
python -m tools.storage.sync --kline --all           # 全市场 5549 只
python -m tools.storage.sync --kline --codes 002371 300750  # 指定
python -m tools.storage.sync --status                # 看现状, 不拉数据
```

## 实战节奏

```bash
# 周一早上 (跑分析前, 推荐 --auto 智能检测)
python -m tools.storage.sync                          # 智能检测, 只跑 stale 的 (多数情况 0 网络)
python -m tools.storage.sync --kline --financials     # 强制跑 (不在乎检测)
python -m tools.storage.sync --cache --watchlist      # 缓存最近数据

# 季报出后 (4月/8月/10月底)
python -m tools.storage.sync --all-data --all         # 全市场 5-10 分钟

# 跑回测前
python -m tools.storage.sync --cache --all            # 全市场缓存, 慢

# 试运行 (看会跑啥, 不真跑)
python -m tools.storage.sync --auto-dry
```

## tools/storage/ 目录结构 (v6.2.3 数据层唯一)

```
tools/storage/
├── sync.py         ( 468)  sync 唯一入口 (7 flag + --auto)
├── store.py        (1548)  DataStore I/O 入口 (25+ 公开方法, 6 bulk 接口)
├── sources/        (2055)  拉数据底层
│   ├── tushare.py  ( 949)  Tushare 9 段接口 (daily/daily_basic/...)
│   └── eastmoney.py(1106)  东财 datacenter (EPS 机构预期)
├── caches/         ( 780)  本地 cache
│   ├── analysis.py ( 644)  analysis_cache.db (24 列因子, 461MB / 166 万行)
│   └── eps.py      ( 136)  EPS JSON cache (30 天 TTL, per-code)
└── schemas/        (  12)  手工配置 (sectors/events 已删, 占位)
```

读 → `DataStore.load_*` / `caches/analysis.get_*` (只读)
写 → `tools.storage.sync` (唯一写入口)
网络 → `sources/tushare / eastmoney` (sync 调, 业务层不直连)
配置 → `DataStore.watchlist_*` (watchlist 是唯一存活的 schema)

## 关键行为

### --auto 智能检测

```bash
python -m tools.storage.sync
# 默认走 --auto, 7 个 flag 全默认关, 自动判断 stale:
#   --kline: 距今天 > 1 天 (考虑周末) → 拉
#   --stock-basic: 距上次 > 30 天 → 拉
#   --financials: 缺最新季 (距季末 > 100 天) → 拉
#   --eps/--fflow/--cache/--meta: 暂不自动, 显式开才跑
```

### --auto-dry 试运行

只打印会跑什么 flag, 不真拉数据. 安全用于 CI / 部署前检查.

### 7 个 flag 正交

- `--cache` 只 sync cache, 其他的不动 (`sync_data` 行为)
- `--financials` 只 sync 财务, 不会顺手拉 K 线
- 用户原话: "sync cache 只 sync cache, 其他的不管"

## 历史

- 之前 sync 散落在 `sync_watchlist_fresh.py` (148 行, dump_one 有递归子进程 bug)
  + `kline_store.py` (sync_incremental/sync_stock_basic/sync_financials)
  + 5 个 batch 脚本偷偷调 sync
- 2026-09-03 v6.0 改造: 全部合并到 `tools/sync_data.py`, 7 个正交 flag
- 2026-09-03 v6.1 改名 `tools/sync_data.py` → `tools/datasync.py` → `tools/sync_data.py`
- 2026-09-03 v6.1.1 改名 `tools/sync_data.py` → `tools/storage/sync.py` (大迁到 storage/)
- 2026-09-03 v6.2.1 合并 `signal_cache_warmup.py` → `caches/analysis.warmup_cache`
- 2026-09-03 v6.2.2 全 db/网络操作走 storage/ (架构守门, 0 处散落)
- 2026-09-03 v6.2.3 caches/analysis 合并冗余 read 接口 (_query 通用)
- 旧 `sync_watchlist_fresh.py` 删除

## 执行

```bash
# 试运行 (推荐先跑)
bash tools/with_venv.sh python -m tools.storage.sync --auto-dry

# 智能同步 (多数情况 0 网络)
bash tools/with_venv.sh python -m tools.storage.sync

# 一次性补全
bash tools/with_venv.sh python -m tools.storage.sync --all-data

# 后台跑 (5+ 分钟用 background, 不用 timeout)
bash tools/with_venv.sh python -m tools.storage.sync --all-data --all
```

## 输出示例

```
=== Mavis sync_data (scope: watchlist 101 只) [默认 --auto 智能检测] ===

🔍 自动检测结果:
  ✅ 跳过  --kline
  ✅ 跳过  --stock-basic
  ✅ 跳过  --financials
  ✅ 跳过  --eps
  ✅ 跳过  --fflow
  ✅ 跳过  --cache
  ✅ 跳过  --meta

✨ 全 fresh, 不用 sync
```
