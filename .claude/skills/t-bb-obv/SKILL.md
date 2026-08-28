---
name: t-bb-obv
description: 每日扫科技股，找 BOLL<15% + BBW<10% + OBV 底背离 三重确认。信号极少（每天 0-2 只），实战"宁可错过不可做错"。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
/t-bb-obv                              # 默认: 科技股, 最近 2 日
/t-bb-obv --window 5                  # 5 日窗口
/t-bb-obv --all                       # 全市场 (不只科技股)
/t-bb-obv --no-obv                    # 不要 OBV 底背离 (只要 BOLL+BBW)
/t-bb-obv --workers 8                 # 8 并发
/t-bb-obv --write-md                  # 写 docs/bb-obv-watchlist.md
/t-bb-obv --limit 100                 # 调试: 只扫前 100 只
```

## 策略 (3 重确认, 严格)

每只股票, 最近 `window` 日内必须同时满足:

1. **BOLL% < 15%** — 接近下轨, 短期超卖
2. **BBW < 10%** — 布林带收窄, 低波/蓄势
3. **OBV 底背离** — 价跌但 OBV 上行, 机构吸筹

**实战理念**: 宁可错过不可做错. 每天 0-2 只命中, 不构成高频信号.

## 执行

```bash
# 后台跑（>30s 必须 background, 不用 timeout）
bash tools/with_venv.sh python -m tools.batch.bb_obv_scan

# 自定义窗口
bash tools/with_venv.sh python -m tools.batch.bb_obv_scan --window 5
```

## 输出示例

```
=== 科技股 | 最近 2 日 | BOLL<15% AND BBW<10% AND OBV 底背离 ===
代码    名称        行业        触发日    价格   OBV底日    距今  状态
688223  海光信息    AI 芯片    20260827  145.2  20260824   0d   ✅✅✅
600362  江西铜业    有色金属    20260826   55.3  20260822   1d   ✅✅✅
```

## 频率参考

| 条件 | 每天命中 | 备注 |
|---|---|---|
| 科技股 BOLL<15 AND BBW<10 | 1-30 只 | 每天波动大 |
| **+ OBV 底背离 (3 重)** | **0-2 只** | 实战 0.1%/天 |
| 放宽到 BBW<15 | 3 重 ≈ 0-5 只 | 信号更多但质量略降 |

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--window` | 2 | 触底窗口天数 |
| `--all` | False | 全市场 (默认科技股 ≈ 1132 只) |
| `--no-obv` | False | 不要 OBV 底背离 (只要 BOLL+BBW) |
| `--boll-threshold` | 15 | BOLL% 上限 (越小越严) |
| `--bbw-threshold` | 10 | BBW 上限 (越小越严) |
| `--workers` | 4 | 并发数 |
| `--write-md` | False | 写 docs/bb-obv-watchlist.md |
| `--limit` | 0 | 调试: 只扫前 N 只 (0=全部) |

## 数据源

| 数据 | 来源 |
|---|---|
| K线 + BOLL/BBW | `data/analysis_cache.db` (cache 命中) |
| OBV 实时算 | K线 close + volume (无网络) |
| 科技股过滤 | `data/history/stock_basic/stock_basic.parquet` (申万行业) |
| 股票基础信息 | DataStore.get_stock_basic() |

## 相关

- `tools/batch/bb_obv_scan.py` — 扫描脚本
- `tools/analysis/signal_cache.py` — BOLL/BBW 缓存
- `/t-sync-cache` — 补齐 cache (信号基础)
- `/t-backtest --signal "boll:15" --signal "bbw:10"` — 历史胜率回测
