---
name: t-sync-cache
description: 增量补全科技股信号缓存（signal_cache.db）。每次跑 10 分钟，断点续跑，已有数据自动跳过。用户说"补缓存"、"sync cache"、"更新信号缓存"、"跑 warmup"时触发。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
/t-sync-cache                  # 科技股增量补缺，10 分钟后自动退出
/t-sync-cache --timeout 1800   # 跑 30 分钟
/t-sync-cache --all            # 全市场（慢，建议拆多次跑）
/t-sync-cache --codes 300274 002371  # 指定股票
/t-sync-cache --full           # 强制重算（忽略已缓存）
```

## 执行

用户输入的参数直接透传给脚本：

```bash
# /t-sync-cache
tools/with_venv.sh python -m tools.batch.signal_cache_warmup

# /t-sync-cache --timeout 1800
tools/with_venv.sh python -m tools.batch.signal_cache_warmup --timeout 1800

# /t-sync-cache --all
tools/with_venv.sh python -m tools.batch.signal_cache_warmup --all

# /t-sync-cache --codes 300274 002371
tools/with_venv.sh python -m tools.batch.signal_cache_warmup --codes 300274 002371

# /t-sync-cache --full
tools/with_venv.sh python -m tools.batch.signal_cache_warmup --full
```

## 行为

- **默认范围**: 申万科技行业（半导体/软件/通信/电子/计算机/光学等）∩ 本地有 K 线
- **增量**: stale 检查，已缓存且 K 线未变的日期直接跳过，不重算
- **断点续跑**: 每次 10 分钟到时间自动写已完成结果退出，下次接着补
- **并发**: 默认 2 worker 并发算，主线程串行写（避免 SQLite 锁竞争）
- **lookback**: 默认 250 根（约 1 年），避免内存 OOM；完整 5 年用 `--lookback 1250`（慢，建议分多次）

## 输出示例

```
科技股: 1823 只 (申万行业筛选 ∩ 本地K线)
预热 1823 只 | lookback=1250天 | step=1 | 4并发 | 增量(stale跳过) | timeout=600s
预计写入上限: ~2,278,750 行 | 初始: 总行数=45,230 / 股票数=312

  [   1/1823] +127  300274  3s
  [   2/1823]  ⏭️   002371  0s   ← 已缓存跳过
  ...

⏰ timeout 600s 到，取消剩余 1421 个任务，写已完成结果...

── Phase2: 写缓存 ──
完成: 写5,842行 / 跳12,301行 / 402只 / 601s
缓存: 总行数=51,072 / 股票数=402
```

## 实现细节

```
tools/batch/signal_cache_warmup.py   # 入口，--timeout 默认 600
tools/analysis/signal_cache.py       # SQLite 读写，data/analysis_cache.db
tools/analysis/analysis_result_signals.py     # compute_factor_history() 核心计算
```
