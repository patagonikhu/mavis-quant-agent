---
name: t-rsi6-tech
description: RSI 双指标超卖 + 业绩反转 (RSI6<25 + RSI12<30 + 净利 yoy>0 + 营收 yoy>=-10% + 边际放缓<10pp). 0 网络, 全市场扫描. 触发词: "RSI6 超卖"、"全市场 RSI 扫描"、"业绩反转抄底"、"严过滤抄底".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan
    # 默认全市场 + 6 重严过滤 (业绩差不明显 → 留下)

... --no-rsi12                      # 关闭 RSI12 确认
... --tech                          # 仅科技板块 (默认否)
... --threshold-rsi6 20             # 调 RSI6 阈值 (默认 25)
... --threshold-rsi12 25            # 调 RSI12 阈值 (默认 30)
... --write-md                      # 写 docs/rsi6-tech-watchlist.md
... --workers 8                     # 进程数 (默认 4)
... --limit 100                     # 调试: 只扫前 N 只
... --no-junk-filter                # 跳过垃圾股过滤
```

## 6 重反向排除 (业绩差 → return None)

| # | 排除条件 | 阈值 |
|---|---|---|
| 1 | RSI6 短期不超卖 | >= 25 |
| 2 | RSI12 中线不超卖 | >= 30 |
| 3 | 本季净利 yoy 转亏 | <= 0 |
| 4 | 上季净利 yoy 转亏 | <= 0 |
| 5 | 营收 yoy 崩盘 | < -10% |
| 6 | 净利 yoy 边际断崖 | 本季 - 上季 < -10pp |

## 输出样例

```
代码      名称        行业        yoy%    触发日       价格        RSI6   RSI12  质量
603298  杭叉集团      工程机械      +9      20260918  23.52     9.04   24.1   ⭐
300908  仲景食品      食品        +8      20260918  16.80     10.69  17.9   ·
300750  宁德时代      电气设备      +42     20260918  301.95    12.86  19.1   ·
...
```

排序：RSI6 升序，限 50 行。**质量分级**：⭐⭐<5 / ⭐<10 / ·<15。

## 关键约束

- **0 网络**: `DataStore.get_ctx(kline_only=True)` 跳过远程调用
- **架构**: 纯内置 RSI（不走 L2 因子库）
- **性能**: ~13s / 3637 只

## 命中后

- 用 `/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-finance-roc-ey` / `/t-finance-earnings-blowout` / `/t-concept-macd` / `/t-backtest` / `/t-sync-data`

## 命名沿革

- v1: `/t-tech-bb-obv` (已删)
- v2: `/t-macd-r2g` (已删)
- v3: `/t-rsi6-oversold` (实测负期望, 已删)
- **v4: `/t-rsi6-tech` (当前)** — RSI6<25 + RSI12<30 + 业绩反转