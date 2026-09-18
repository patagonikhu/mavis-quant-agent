---
name: t-rsi6-tech
description: RSI6<25 + RSI12<30 双指标超卖 + 科技板块 + 季报盈利 (四重过滤). 0 网络. 触发词: "RSI6 超卖"、"科技超卖"、"RSI 双指标"、"抄底科技股".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan              # 默认 RSI6<25+RSI12<30+科技+yoy>0
... --no-tech                       # 不限科技板块 (全市场扫描)
... --no-yoy                        # 不限季报 yoy>0
... --no-rsi12                      # 关闭 RSI12 确认 (仅 RSI6<25)
... --threshold-rsi6 20             # 调 RSI6 阈值 (默认 25)
... --threshold-rsi12 25            # 调 RSI12 阈值 (默认 30)
... --write-md                      # 写 docs/rsi6-tech-watchlist.md
... --workers 8                     # 进程数 (默认 4)
... --limit 100                     # 调试: 只扫前 N 只
... --no-junk-filter                # 跳过垃圾股过滤
```

## 默认触发条件 (四重过滤, 全部满足)

1. **RSI6 < 25** — 短期超卖 (Wilder's smoothing) — 默认 25
2. **RSI12 < 30** — 中期确认 — 默认 30
3. **科技板块** — 行业 ∈ {元器件, 半导体, 软件服务, 通信设备, IT设备, 互联网} (关掉用 --no-tech)
4. **季报 netprofit_yoy > 0** — 最新季报盈利同比正 (关掉用 --no-yoy)

## 关键约束

- **0 网络**: `DataStore.get_ctx(kline_only=True)` 跳过 EPS/fflow 网络拉取
- **架构**: 纯内置 RSI6/RSI12 (Wilder smoothing), 不走 L2 因子库
- **质量**: ⭐⭐ = RSI6<5 (极限超卖), ⭐ = RSI6<10, · = RSI6<15
- **性能**: 552 只科技股 ~3s (ProcessPoolExecutor, 因 duckdb 多线程死锁改进程池)
- **缺数据**: 报"请先 /t-sync-data"

## 回测数据 (科技板块 + yoy>0, 持有 20 天, 30 天去重)

| 策略 | 笔数 | 5%/5% 胜率 | 单笔期望 | 平均终 |
|---|---|---|---|---|
| **默认 RSI6<25+RSI12<30+科技+yoy>0** | **366** | **51.2%** | **+0.16%** ⭐ | **+8.62%** ⭐ |
| 严控 RSI6<20+RSI12<25+科技+yoy>0 | 143 | 57.4% | **+0.98%** ⭐⭐⭐ | +8.34% |
| 严控 RSI6<15+RSI12<25+科技+yoy>0 | 119 | 54.1% | +0.55% | +7.78% |
| 三重 (无 yoy) | 253 | 54.6% | +0.59% | +5.66% |
| 仅 RSI6<10 (旧) | 88 | 39.0% | -1.02% ⛔ | -1.12% |

**实战选择**:
- 想要 **更多信号 (每月 90 个)**: 用默认 `RSI6<25+RSI12<30+科技+yoy>0`
- 想要 **更高期望 (笔数减半)**: 加严到 `RSI6<20+RSI12<25+科技+yoy>0` (`--threshold-rsi6 20 --threshold-rsi12 25`)

## 输出

- `docs/rsi6-tech-watchlist.md` (默认四重条件命中)
- 命中后用 `/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-roc-ey` / `/t-earnings-blowout` / `/t-sector-ma` / `/t-backtest` / `/t-sync-data`

## 命名沿革

- v1: `/t-tech-bb-obv` (BOLL+BBW+OBV 三重确认, 已删)
- v2: `/t-macd-r2g` (MACD 红转绿, 已删)
- v3: `/t-rsi6-oversold` (RSI6 < 10, 实测负期望, 已删)
- **v4: `/t-rsi6-tech` (当前)** — RSI6<25 + RSI12<30 + 科技板块 + 季报 yoy>0