---
name: t-am-divergence
description: 全市场扫描最近 5 天出现 A→M 阶段切换 + 缠论底背驰 + MACD 底背驰三重确认的股票
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
/t-am-divergence                       # 默认: 5d 内三重确认
/t-am-divergence --window 10           # 10d 内
/t-am-divergence --no-macd             # 只要 A→M + 缠论
/t-am-divergence --workers 16          # 16 并发（默认8）
/t-am-divergence --write-md            # 写 docs/am-divergence-watchlist.md
/t-am-divergence --limit 100           # 调试：只扫前100只
```

## 算法 (4 步)

Step 1: `sync_incremental()` 补缺失交易日
Step 2: `DataStore.list_codes()` 获取本地历史库所有股票（全市场）
Step 3: 每只只跑 3 个 strategy（WyckoffStrategy / ChanStrategy / MacdDivergenceStrategy），`compute_factor_history(lookback=window+30, strategies=[...])`
Step 4: 找最近 window 天内的 A→M 切换（Accumulation→Markup），检查切换日前 30d 内：
  - 缠论底背驰（daily_beichi direction=bot）
  - MACD 底背驰（MacdDivergenceStrategy triggered）
Step 5: 三重确认 → 输出清单（按距今天数升序）

## 运行

```bash
bash tools/with_venv.sh python -m tools.batch.am_divergence
bash tools/with_venv.sh python -m tools.batch.am_divergence --window 10 --write-md
```

## 输出示例

```
代码    名称        行业        A→M日        价格  缠论日        MACD日      距今  三重
002371  北方华创    半导体设备  2026-08-18   312.5  2026-08-15  2026-08-16   4d  ✅✅✅
300274  阳光电源    电气设备    2026-08-19    48.2  2026-08-17  —            3d  ✅✅⬜
```

## 性能

- 全 A 股 ~5000 只 × 3 strategy × 35 天 ÷ 8 并发 ≈ 2-3 分钟
- 只跑 3 个 strategy（不是全部7个），比全量快 2-3x

## 数据源

| 数据 | 来源 |
|---|---|
| K线历史 | `data/history/daily/*.parquet`（DataStore.get_kline） |
| 周线 | 日线聚合（_synthesize_weekly） |
| 股票基础信息 | `data/cache/stock_basic.json` |
| 威科夫/缠论/MACD | AnalysisEngine 实时计算（0网络） |

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--window` | 5 | A→M 切换窗口天数 |
| `--no-macd` | False | 只要 A→M + 缠论，不强求 MACD |
| `--workers` | 8 | 并发数 |
| `--write-md` | False | 写 docs/am-divergence-watchlist.md |
| `--limit` | 0 | 调试：只扫前 N 只（0=全部） |

## 相关文件

- `tools/batch/am_divergence.py` — 扫描脚本
- `tools/analysis/factor_history.py` — `compute_factor_history(strategies=...)` 按需策略
- `tools/data_store.py` — `DataStore.list_codes()` 全市场代码
