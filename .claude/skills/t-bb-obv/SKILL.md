---
name: t-bb-obv
description: 每日扫科技股，找 BOLL<15% + BBW<10% + OBV 5日价跌或OBV>MA20 三重确认。信号极少（每天 0-2 只），实战"宁可错过不可做错"。
user-invocable: true
allowed-tools:
  - Bash

## ⚠️ 重要：不走 cache

`t-bb-obv` **直接用 `analyze_history` 实时算**（不走 cache, 只跑 2 个 strategy 算 4 字段, 不组装 16 字段）。

**为什么**：
- cache 经常不完整（`t-sync-cache` 太慢，没人会等）
- 直算 30 天 K 线 = 0.2s/只，比读 cache 还快
- 数据一致性：直算永远跟 `t-analyze` 一致

**Cache 的用途**：**只给回测用**（`/t-backtest`）
- `/t-backtest` 跑历史 5 年全市场，cache 让 0.6s 出结果
- `/t-bb-obv` 跑最近 5-10 天，每次 cache 都会被新数据 invalidate，直算更可靠

## 用法

```bash
/t-bb-obv                              # 默认: 科技股, 最近 2 日
/t-bb-obv --window 5                  # 5 日窗口
/t-bb-obv --all                       # 全市场 (不只科技股)
/t-bb-obv --no-obv                    # 只要 BOLL+BBW 双确认
/t-bb-obv --workers 8                 # 8 并发
/t-bb-obv --write-md                  # 写 docs/bb-obv-watchlist.md
/t-bb-obv --limit 100                 # 调试: 只扫前 100 只
```

## 策略 (3 重确认, 严格)

每只股票, 最近 `window` 日内必须同时满足:

1. **BOLL% < 15%** — 接近下轨, 短期超卖
2. **BBW < 10%** — 布林带收窄, 低波/蓄势
3. **OBV 实战信号** (满足任一):
   - `obv5`: 5 日价跌 + OBV 涨 (短期吸筹)
   - `obv_trend`: OBV > MA20 (资金净流入)

**时效性** (2026-08-29 改): `--window N` = "距今天 ≤ N 个日历日" (以 today 为基准, 不是最后交易日).
例: 周六 8/29 跑 window=3, 8/25 (距今 4 天) 被排除, 8/27 (距今 2 天) 入选.
实战过期信号无意义, 不要看 4-5 天前的"老信号".

**实战理念**: 宁可错过不可做错. 每天 0-2 只命中, 不构成高频信号.

## 执行 (走 analyze_history)

```bash
# 后台跑（>30s 必须 background, 不用 timeout）
bash tools/with_venv.sh python -m tools.batch.bb_obv_scan

# 自定义窗口
bash tools/with_venv.sh python -m tools.batch.bb_obv_scan --window 5
```

## 数据流

```
DataStore.get_ctx(code)  →  K线 (30 天)
                              ↓
AnalysisEngine(strategies=[Wyckoff, Obv]).analyze_history(ctx, dates)
                              ↓
              (跳过 chan/smc/fflow/peg, 省 70% 时间)
                              ↓
history[date] = AnalysisResult (raw['wyckoff'] + raw['obv'])
                              ↓
                  拼 5 字段 {boll_pct, boll_width, obv5, obv_trend}
                              ↓
                  检查 3 重条件, 命中即输出
```

## 输出示例

```
=== 科技股 (1203 只) | 最近 5 日 | BOLL<15% AND BBW<10% AND OBV 5日/趋势 ===
读 cache (boll_bpct/boll_bwidth/obv5/obv_trend) ❌ ← 错误! 实际是直算
扫描 1832 只 (4 workers)...

代码    名称        行业        触发日    价格    BOLL%   BBW%   状态
002111  威海广泰    航空        20260827  9.41    14.4    4.12   ✅✅✅
600885  宝胜股份    电气设备    20260827  34.02   5.2     8.20   ✅✅✅
300073  当升科技    电气设备    20260825  40.42   -6.1    5.61   ✅✅✅
```

## 频率参考

| 条件 | 每天命中 | 备注 |
|---|---|---|
| 科技股 BOLL<15 AND BBW<10 | 1-30 只 | 每天波动大 |
| **+ OBV 实战信号 (3 重)** | **0-2 只** | 实战 0.1%/天 |
| 放宽到 BBW<15 | 3 重 ≈ 0-5 只 | 信号更多但质量略降 |

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--window` | 2 | 触底窗口 (距今 ≤ N 个**日历日**, 默认 2 = 2 天内) |
| `--all` | False | 全市场 (默认科技股 ≈ 1132 只) |
| `--no-obv` | False | 只要 BOLL+BBW 双确认 |
| `--boll-threshold` | 15 | BOLL% 上限 (越小越严) |
| `--bbw-threshold` | 10 | BBW 上限 (越小越严) |
| `--workers` | 4 | 并发数 |
| `--write-md` | False | 写 docs/bb-obv-watchlist.md |
| `--limit` | 0 | 调试: 只扫前 N 只 (0=全部) |

## 数据源

| 数据 | 来源 |
|---|---|
| K线 | `DataStore.get_ctx()` (parquet, 0 网络) |
| BOLL%/BBW/OBV | **实时算** (`AnalysisEngine(strategies=[Wyckoff, Obv]).analyze_history`) |
| 科技股过滤 | `data/history/stock_basic/stock_basic.parquet` (申万行业) |
| 股票基础信息 | `DataStore.get_stock_basic()` |

## 相关

- `tools/batch/bb_obv_scan.py` — 扫描脚本 (analyze_history 直算)
- `/t-sync-cache` — **回测用** (cache 5 年数据)
- `/t-backtest --signal "boll:15" --signal "bbw:10"` — 历史胜率回测 (用 cache)
