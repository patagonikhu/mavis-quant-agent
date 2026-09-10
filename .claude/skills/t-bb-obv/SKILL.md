---
name: t-bb-obv
description: 科技股扫 BOLL<15% + BBW<10% + OBV 5日/趋势. 0 网络, 走 DataStore. 触发词: "BOLL 触底"、"OBV 吸筹"、"短期形态".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.bb_obv_scan                       # 默认 watchlist
... --all                  # 全市场
... --codes 300274 600741  # 指定
... --window 5             # 改 OBV 5 日窗口
```

## 关键约束

- **0 网络**, 走 `DataStore` 读 K 线 + stk_factor
- **3 维过滤**: BOLL 位置 + BBW 带宽 + OBV 5日/趋势 (全过 → 1-3 个月反弹)
- **OBV 适用性**: ✅ 光学/封测/HBM, ❌ 周期股/题材小盘 (主力分散 OBV 噪声大)
- **缺数据**: 报"请先 /t-sync-data"

## 输出

- `docs/bb_obv_hits.md` — 命中列表
- 命中后用 `/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-roc-ey` / `/t-earnings-blowout` / `/t-sector-ma` / `/t-backtest` / `/t-sync-data`
