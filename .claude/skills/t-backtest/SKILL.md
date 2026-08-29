---
name: t-backtest
description: 信号回测 — 扫描 N 年历史，统计指定信号触发后未来 N 天的最大涨幅命中率。用户说"回测"、"信号胜率"、"历史统计"时触发。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
# 单信号
/t-backtest --signal Spring
/t-backtest --signal Accumulation
/t-backtest --signal fflow:强进货

# 组合信号 (AND)
/t-backtest --signal Spring --signal fflow:强进货
/t-backtest --signal Accumulation --signal 1买

# 参数
/t-backtest --signal Spring --days 30 --threshold 10
/t-backtest --lookback 3y   # 默认 5y
/t-backtest --codes 300274
/t-backtest --all            # 全 watchlist
/t-backtest --portfolio      # 仅持仓（默认）
/t-backtest --write-md       # 写 docs/backtest-*.md
```

## 信号列表

| 类别 | 信号 |
|---|---|
| 威科夫子事件 | Spring / LPS / EVR / SOS / Compression / TrendPullback / MarkupEntry / DistributionStart / UTAD |
| 缠论买卖点 | 1买 / 1买⭐ / 2买 / 3买 / 双中枢 / 笔结束 / 吞没 |
| 威科夫阶段 | Accumulation / Markup / Distribution / Markdown |
| 主力 fflow | fflow:强进货 / 偏进货 / 中性 / 偏出货 / 强出货 |
| 背驰 | 底背驰 / 顶背驰 |
| OBV | obv5 / obv_trend |

> 2026-08-29 删: 场景 scene:A/B/C/D/E (硬编码 if-else, 已被 signals_active 列表替代)

## 算法 (3 步)

1. **加载数据**: 从 parquet 读全量，构造 RawContext
2. **信号扫描**: `engine.analyze_history(ctx, dates)`，逐日检查 `--signal` 命中
3. **算未来涨幅**: 命中日后 N 日 `max(high)/close - 1`，`hit = max_ret >= threshold%`

## 缓存优化 (v2)

`tools/batch/batch_backtest.py` 已集成 `signal_cache` (SQLite)：
- 第一次：扫全量 + 写缓存（慢）
- 后续：读缓存秒级返回（命中行就 0.5s 跑完）
- 缓存路径：`data/analysis_cache.db`

> ⚠️ **跑前先看 cache 覆盖范围**: `sqlite3 data/analysis_cache.db "SELECT COUNT(DISTINCT code), MIN(date_str), MAX(date_str) FROM analysis_cache"`
> 没覆盖的股票需先跑 `t-sync-cache` 补齐

## 执行

```bash
# 后台跑（>30s 必须 background, 不用 timeout）
bash tools/with_venv.sh python3 tools/batch/batch_backtest.py --signal Spring --days 30 --threshold 10 --portfolio 2>&1
```

**关键参数**:
| 参数 | 默认 | 说明 |
|---|---|---|
| `--days` | 30 | 持仓期 (天) |
| `--threshold` | 10.0 | 涨幅阈值 % |
| `--lookback` | 5 | 回看年数 |
| `--workers` | 4 | 并发数 |
| `--no-cache` | False | 强制重算（忽略缓存） |

## 输出格式

```
📊 回测报告: [Spring] | 5年 (2021-08 ~ 2026-08) | 持仓30天 | 阈值10%

命中 12 次 | 命中率 67% (8/12) | 均涨幅 +18.3% | 中位 +14.2% | 最大 +42%
失败 4 次 | 均跌幅 -5.1%

明细 (12 次):
  2024-01 Spring @¥42.1 → 30日最大 +23% ✅
  2024-06 Spring @¥38.7 → 30日最大 +8%  ❌
  ...
```

`--write-md` 输出到 `docs/backtest-{信号名}.md` (单文件覆盖)

## 性能

- **无缓存**: 7 只持仓 × 1250天 × 7 strategy ≈ 3.5 分钟
- **有缓存**: 7 只 × 命中行缓存命中 ≈ 0.5 秒
- **全 watchlist (61 只)**: 第一次 ~30 分钟 (后台)，后续 1 秒

## 相关

- `tools/batch/batch_backtest.py` — 缓存版回测引擎（正式入口）
- `tools/analysis/signal_cache.py` — SQLite 缓存（24 列：wyckoff/chan 9 bool + hub + MA + Boll）
- `tools/analysis_cache.py` — 旧版稀疏缓存（已弃用，保留兼容）
