---
name: t-sync-cache
description: ⚠️ DEPRECATED (v6.2.1): 此 skill 已并入 /t-sync-data --cache。Cache 预热走 `python -m tools.storage.sync --cache`, 7 flag 正交统一入口。保留此 skill 仅作 alias, 直接用 /t-sync-data --cache 即可。
user-invocable: true
allowed-tools:
  - Bash

## 注意

**v6.2.1 起, cache 预热统一走 `/t-sync-data --cache`**, 不要再用 `/t-sync-cache`。

```bash
# 推荐 (新)
bash tools/with_venv.sh python -m tools.storage.sync --cache                # 科技股增量补缺 (默认 10 分钟)
bash tools/with_venv.sh python -m tools.storage.sync --cache --all          # 全市场
bash tools/with_venv.sh python -m tools.storage.sync --cache --codes 300274 002371
bash tools/with_venv.sh python -m tools.storage.sync --cache --portfolio      # 仅持仓
bash tools/with_venv.sh python -m tools.storage.sync --cache --timeout 1800  # 跑 30 分钟
bash tools/with_venv.sh python -m tools.storage.sync --cache --workers 4      # 4 并发 (默认 2)
```

底层实现 (`tools/storage/caches/analysis.warmup_cache`) 跟 v6.2.1 之前相同, 但调用入口合并到 sync_data.

## 行为

- **默认范围**: 申万科技行业 (半导体/软件/通信/电子/计算机/光学等) ∩ 本地有 K 线
- **增量**: stale 检查 (哈希对比), 已缓存且 K 线未变的日期直接跳过
- **断点续跑**: 每次到 timeout 自动写已完成结果退出, 下次接着补
- **并发**: 默认 2 worker 并发算 (Phase 1), 主线程串行写 (Phase 2, 避免 SQLite 锁)
- **lookback**: 默认 250 根 (~1 年) / 多次跑逐步覆盖 5 年

## 实现

```
tools/storage/sync.py                          # sync 唯一入口
  └─ action_cache() → warmup_cache()          # v6.2.1 合并
       └─ tools/storage/caches/analysis.py    # analysis_cache.db I/O
            ├─ _calc_signals_for_code()       # 算信号
            └─ write_batch()                   # Phase 2 写
```

## 迁移指南

| 旧 (v6.2.0 之前) | 新 (v6.2.1+) |
|---|---|
| `/t-sync-cache` | `/t-sync-data --cache` |
| `/t-sync-cache --portfolio` | `/t-sync-data --cache --portfolio` |
| `/t-sync-cache --all` | `/t-sync-data --cache --all` |
| `/t-sync-cache --codes 300274` | `/t-sync-data --cache --codes 300274` |
| `python -m tools.batch.signal_cache_warmup` | `python -m tools.storage.sync --cache` |
