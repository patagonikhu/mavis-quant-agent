---
name: t-backtest
description: 信号回测 — 扫描 N 年历史, 统计信号触发后未来 N 天最大涨幅命中率. 0 网络, 走 DataStore. 触发词: "回测"、"信号胜率"、"历史统计".
user-invocable: true
allowed-tools:
  - Bash

## 原理

扫描历史 K 线, 找到信号触发日, 算"未来 N 天最大涨幅" (vs 沪深 300 基准).

**关键: 必须复用 `AnalysisEngine.analyze_history()` 入口**, 不绕过

## 用法

```bash
# 单信号
/t-backtest --signal Spring
/t-backtest --signal Accumulation
/t-backtest --signal fflow:强进货

# 组合 (AND)
/t-backtest --signal Spring --signal fflow:强进货
/t-backtest --signal Accumulation --signal 1买

# 参数
/t-backtest --signal Spring --days 30 --threshold 10
/t-backtest --lookback 3y
/t-backtest --codes 300274
/t-backtest --all            # 全 watchlist
/t-backtest --portfolio      # 仅持仓
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

## 数据源

`DataStore` + `AnalysisEngine.analyze_history()` → 0 网络

- 单信号 → `signal_cache` (SQLite) 命中行 O(1) 读
- 5 年 backfill 需要先 `python -m tools.storage.sync --cache`

## 输出

```
📊 回测报告: [Spring] | 5年 (2021-08 ~ 2026-08) | 持仓30天 | 阈值10%
命中 12 次 | 命中率 67% (8/12) | 均涨幅 +18.3%
```

或 `docs/backtest-{信号名}.md` (--write-md 模式)

## 性能

- **无缓存**: 7 只持仓 × 1250 天 × 7 strategy ≈ 3.5 分钟
- **有缓存**: O(1) 读, 秒级
- **全 watchlist (119 只)**: 首次 30 分钟, 后续 1 秒

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--days` | 30 | 持仓期 (天) |
| `--threshold` | 10.0 | 涨幅阈值 % |
| `--lookback` | 5 | 回看年数 |
| `--workers` | 4 | 并发数 |
| `--no-cache` | False | 强制重算 |

## 相关

- `/t-analyze` — 单只深挖
- `/t-magic` — Magic 排名
- `tools/batch/batch_backtest.py` — 回测引擎
- `tools/analysis/signal_cache.py` — SQLite 缓存 (24 列因子)
