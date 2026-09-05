---
name: t-near-low
description: 每周扫"跌 70-80% + 距 5y 低 < 3%"清单. 0 网络, 走 DataStore. 触发词: "距 5y 低"、"近底"、"超跌清单".
user-invocable: true
allowed-tools:
  - Bash

## 原理

**3 维过滤超跌反弹候选**:

| 维度 | 公式 | 默认 |
|---|---|---|
| **5y 最大回撤** | (high - low) / high | 70% ≤ 回撤 < 80% (排除 90%+ 异常) |
| **距 5y 低** | (current - low_5y) / low_5y | < 3% (严格) / < 5% (宽松) |
| **反弹次数** | 5y weekly 30%+ 反弹事件 | ≥ 0 (历史弹性) |

**全过 → 距 5y 底 < 3% 的超跌股, 反弹期望 +45%** (谷底 70-80% 反弹中位)

## 用法

```bash
/t-near-low                          # 默认 70-80% 跌 + 距 5y 低 < 3%
/t-near-low --gap 5                  # 距 5y 低 < 5% (宽松)
/t-near-low --gap 2                  # 距 5y 低 < 2% (严格)
/t-near-low --drop 80 --drop-max 90  # 跌 80-90%
/t-near-low --lookback-years 3        # 3y lookback (不含 2021 牛市)
/t-near-low --min-bounces 4           # 反弹次数 ≥ 4
/t-near-low --write-md                # 写 docs/oversold-watchlist.md
```

## 数据源

`DataStore` → 0 网络, 走 `data/history/daily/*.parquet` (5y weekly K 线)

缺数据: 请先 /t-sync-data

## 输出

- `docs/oversold-watchlist.md` — 清单 (8-13 只, 按距低 % 升序)
- stdout: 命中数 + 速览

## 实战策略 (反弹策略, 非价值投资)

- 涨 10-20% 跑
- 跌破谷底 10% 砍
- 持有 1-3 个月
- 5-10 只分散
- 单只轻仓

**原因**: 距 5y 低 < 3% 的票多为业绩下行/亏损, 80% 业绩下行 — **纯技术反弹策略, 不是价值投资**

## 深挖 (命中后)

```bash
bash tools/with_venv.sh python3 tools/batch/t_analyze_one.py --code <hit_code>
```

## 相关

- `/t-analyze <code>` — 命中票深挖 22 section
- `/t-bb-obv` — 短期吸筹形态
- `/t-magic` — 找"好公司+便宜股"双优
