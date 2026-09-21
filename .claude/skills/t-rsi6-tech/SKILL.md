---
name: t-rsi6-tech
description: 6 重反向排除 (RSI 超卖 + 业绩反转 + 边际严) 全市场. 0 网络. 触发词: "RSI6 超卖"、"全市场 RSI 扫描"、"严过滤抄底"、"业绩反弹".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan
    # 默认全市场 + 6 重反向排除 (业绩差不明显 → 留下)

# 可选调整
... --no-rsi12                      # 关闭 RSI12 确认 (仅 RSI6<25)
... --tech                          # 仅科技板块 (默认否, 全市场)
... --threshold-rsi6 20             # 调 RSI6 阈值 (默认 25)
... --threshold-rsi12 25            # 调 RSI12 阈值 (默认 30)
... --write-md                      # 写 docs/rsi6-tech-watchlist.md
... --workers 8                     # 进程数 (默认 4)
... --limit 100                     # 调试: 只扫前 N 只
... --no-junk-filter                # 跳过垃圾股过滤
```

> 2026-09-21 改: 删除 `--no-yoy` 和 `--strict-yoy` flag。**默认行为 = 6 重严过滤**。

## 默认 6 重反向排除 (业绩差 → return None)

| # | 排除条件 | 含义 |
|---|---|---|
| 1 | RSI6 >= 25 | 短期不超卖 |
| 2 | RSI12 >= 30 | 中线不超卖（`--no-rsi12` 可关）|
| 3 | 本季净利 yoy <= 0 | 本季亏（同比负）|
| 4 | 上季净利 yoy <= 0 | 上季亏（单季反转不算）|
| 5 | 营收 yoy < -10% | 营收崩盘 |
| 6 | 净利 yoy 边际放缓 < -10pp | 业绩见顶（断崖下滑）|

**最终留下 = 通过 6 条的票**（业绩差不明显）。

## 默认输出

```
=== RSI6+RSI12 超卖 (6 重严过滤, 全市场, 0 网络) ===
  1. RSI6 < 25
  2. RSI12 < 30
  3. 本季净利 yoy > 0
  4. 上季净利 yoy > 0
  5. 营收 yoy >= -10%
  6. 净利 yoy 边际放缓 < -10pp
  全部 6 条反向排除 (业绩差 → 排除)

代码      名称        行业        yoy%    触发日       价格        RSI6   RSI12  质量
603298  杭叉集团      工程机械      +9      20260918  23.52     9.04   24.1   ⭐
...
```

排序：RSI6 升序（越低越先出），限 50 行。
**质量分级**：⭐⭐ = RSI6 < 5（极限超卖）、⭐ = RSI6 < 10、· = RSI6 < 15。

## 关键约束

- **0 网络**: `DataStore.get_ctx(kline_only=True)` 跳过 EPS/fflow 远程调用
- **架构**: 纯内置 RSI6/RSI12 (Wilder smoothing)，不走 L2 因子库
- **ProcessPoolExecutor**: DuckDB 多线程死锁 → 用进程隔离
- **缺数据**: 报"请先 /t-sync-data"
- **3637 只扫描**（5801 全市场 - 亏损 1849 - 垃圾/小盘 315），13s 跑完

## 回测数据 (科技板块 + yoy>0, 持有 20 天, 30 天去重)

| 策略 | 笔数 | 5%/5% 胜率 | 单笔期望 |
|---|---|---|---|
| 默认 RSI6<25+RSI12<30+科技+yoy>0 | 366 | 51.2% | +0.16% ⭐ |
| 严控 RSI6<20+RSI12<25+科技+yoy>0 | 143 | 57.4% | +0.98% ⭐⭐⭐ |
| 严控 RSI6<15+RSI12<25+科技+yoy>0 | 119 | 54.1% | +0.55% |

**实战选择**:
- 当前默认是全市场 + 6 重严（**非旧版 4 重科技 + yoy>0**）
- 想要更严 RSI6 阈值：`--threshold-rsi6 20`
- 想限定科技板块：`--tech`

## 输出文件

- 默认输出到 chat（stdout）
- `--write-md` 时写 `docs/rsi6-tech-watchlist.md`
- 命中后用 `/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-finance-roc-ey` / `/t-finance-earnings-blowout` / `/t-concept-macd` / `/t-backtest` / `/t-sync-data`

## 命名沿革

- v1: `/t-tech-bb-obv` (BOLL+BBW+OBV 三重确认, 已删)
- v2: `/t-macd-r2g` (MACD 红转绿, 已删)
- v3: `/t-rsi6-oversold` (RSI6 < 10, 实测负期望, 已删)
- v4: `/t-rsi6-tech` (旧: 4 重科技 + yoy>0) — 2026-09-21 改: 6 重全市场严过滤