---
name: t-rsi6-tech
description: RSI 双指标超卖 + 业绩反转 (RSI6<25 + RSI12<30 + 净利 yoy>0 + 营收 yoy>=-10% + 边际放缓<10pp). 0 网络, 全市场扫描. 触发词: "RSI6 超卖"、"业绩反转抄底"、"严过滤抄底".
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

... --no-rsi12   --tech   --write-md   --threshold-rsi6 20   --workers 8   --limit 100
```

## 6 重反向排除 (业绩差 → return None)

| # | 排除条件 | 阈值 |
|---|---|---|
| 1 | RSI6 短期不超卖 | >= 25 |
| 2 | RSI12 中线不超卖 | >= 30 |
| 3 | 本季净利 yoy 转亏 | <= 0 |
| 4 | 上季净利 yoy 转亏 | <= 0 |
| 5 | 营收 yoy 崩盘 | < -10% |
| 6 | 净利 yoy 边际断崖 | 本季-上季 < -10pp |

## 输出

```
代码      名称        行业        yoy%    RSI6   RSI12  质量
603298  杭叉集团      工程机械      +9      9.04   24.1   ⭐
...
```

排序 RSI6 升序，限 50 行。**质量**: ⭐⭐<5 / ⭐<10 / ·<15。

## 关键约束

- 0 网络 / ~13s / 3637 只
- 纯内置 RSI（Wilder smoothing），不走 L2 因子库

## 命中后

`/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-finance-roc-ey` / `/t-finance-earnings-blowout` / `/t-concept-macd` / `/t-backtest`

## 命名沿革

v1 `/t-tech-bb-obv` → v2 `/t-macd-r2g` → v3 `/t-rsi6-oversold` → **v4 `/t-rsi6-tech` (当前)**