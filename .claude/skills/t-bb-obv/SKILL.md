---
name: t-bb-obv
description: 科技股扫 BOLL<15% + BBW<10% + OBV 5日/趋势. 0 网络, 走 DataStore. 触发词: "BOLL 触底"、"OBV 吸筹"、"短期形态".
user-invocable: true
allowed-tools:
  - Bash

## 原理

**3 维过滤科技股吸筹形态**:

| 维度 | 公式 | 含义 |
|---|---|---|
| **BOLL 位置** | (close - lower) / (upper - lower) | 价在下轨附近 (≤15%) |
| **BBW 带宽** | (upper - lower) / middle | 振幅压缩 (≤10%) |
| **OBV 5日/趋势** | 5日价跌但 OBV 涨 | 主力暗中吸筹 |

**3 维全过 → 短期可能反弹** (非价值投资, 1-3 个月反弹策略)

## 用法

```bash
/t-bb-obv                        # 默认 watchlist 121 只
/t-bb-obv --all                  # 全市场 5555 只
/t-bb-obv --window 5             # 改 OBV 5日窗口
/t-bb-obv --codes 300274 600741  # 指定
```

## 数据源

`DataStore` → 0 网络, 走 `data/history/{daily,stk_factor}/` parquet

缺数据: 请先 /t-sync-data

## 输出

- `docs/bb_obv_hits.md` — 命中列表 (含 BOLL/BBW/OBV 三维数据)
- stdout: 命中数 + Top 10 速览

## 实战策略 (反弹策略)

- 涨 10-20% 跑
- 跌破 5y 低 10% 砍
- 持有 1-3 个月
- 5-10 只分散
- 单只轻仓

**不适用**: 价值投资 / 长期持有 / 周期股 / 题材小盘

## OBV 信号版块适用性

- ✅ 光学/封测/HBM: 主力控盘度高, OBV 准
- ❌ 周期股: 行业 β 主导, OBV 被淹没
- ❌ 题材/小盘: 主力分散, 噪声大

## 相关

- `/t-analyze <code>` — 命中票深挖 22 section
- `/t-near-low` — 距 5y 低 < 3% 清单
- `/t-magic` — 找"好公司+便宜股"双优
