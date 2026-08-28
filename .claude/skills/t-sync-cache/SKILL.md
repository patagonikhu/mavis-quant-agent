---
name: t-sync-cache
description: 增量补全科技股信号缓存（signal_cache.db）。每次跑 10 分钟，断点续跑，已有数据自动跳过。用户说"补缓存"、"sync cache"、"更新信号缓存"、"跑 warmup"时触发。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
/t-sync-cache                  # 科技股增量补缺，10 分钟后退出
/t-sync-cache --timeout 1800   # 跑 30 分钟
/t-sync-cache --all            # 全市场（慢，建议拆多次）
/t-sync-cache --codes 300274 002371
/t-sync-cache --portfolio      # 仅持仓
/t-sync-cache --full           # 强制重算（忽略已缓存）
/t-sync-cache --workers 4      # 4 并发（默认 2）
/t-sync-cache --lookback 3000  # 覆盖 8 年（默认 1500）
```

## 执行

用户参数直接透传给 `tools.batch.signal_cache_warmup`：

```bash
# 后台跑（>30s 必须 background, 不用 timeout）
bash tools/with_venv.sh python -m tools.batch.signal_cache_warmup
```

## 行为

- **默认范围**: 申万科技行业（半导体/软件/通信/电子/计算机/光学等）∩ 本地有 K 线
- **增量**: stale 检查（哈希对比），已缓存且 K 线未变的日期直接跳过
- **断点续跑**: 每次到 timeout 自动写已完成结果退出，下次接着补
- **并发**: 默认 2 worker 并发算（Phase 1），主线程串行写（Phase 2，避免 SQLite 锁）
- **lookback**: 默认 1500 根（约 5 年），step=1（每日精度）

## 输出示例

```
科技股: 1921 只 (申万行业筛选 ∩ 本地K线)
预热 1921 只 | batch_size=250根/只 | step=1 | 4并发 | 增量 | timeout=600s
初始缓存: {'rows': 671295, 'codes': 1829, 'size_mb': 140.25}
  [1/51] ✅ 000970: 写251行(跳0) 6s
  [10/51]   ⏭️ 600089 0s
  ...
── Phase2: 写缓存 ──
完成: 写2,500行 / 跳17,822行 / 20只 / 15s
缓存: {'rows': 674147, 'codes': 1829, 'size_mb': 140.89}
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--codes` | (科技股) | 指定股票代码 |
| `--all` | False | 全市场 |
| `--portfolio` | False | 仅持仓 |
| `--workers` | 2 | Phase 1 并发数 |
| `--batch-size` | 250 | 每次每只补的根数（断点续跑步长） |
| `--lookback` | 1500 | 回看根数（~5 年） |
| `--step` | 1 | 计算间隔（1=每日精度） |
| `--full` | False | 强制重算最老段（不检查 stale） |
| `--timeout` | 600 | 超时秒数，到时间写已完成结果退出 |

## 策略：多次跑逐步覆盖

每次从**最老的缺口**补起（`stale_dates = all_stale[:batch_size]`），多跑几次 5 年全覆盖：
```
第 1 次: 补 2021 (oldest year)
第 2 次: 补 2022
...
第 5 次: 补 2025 (newest year)
```

## 实现

```
tools/batch/signal_cache_warmup.py  # 入口
tools/analysis/signal_cache.py       # SQLite 读写 (data/analysis_cache.db, 24 列)
tools/analysis/analysis_engine.py    # AnalysisEngine.analyze_history() 计算
```
