---
name: t-rsi6-tech
description: RSI 双指标超卖信号 (RSI6<25 且 RSI12<30, 看最近 lookback 根任一跌破). 0 网络, 从 watchlist.json 选股. 触发词: "RSI 超卖"、"技术面抄底".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)
> 📐 **命名规范: [`handbook/naming-convention.md`](../../handbook/naming-convention.md)** (7 条硬规则, 5 项 grep 验证)

## 🕐 跑节奏 (技术面 — 日级)

- 业绩过滤由上游 `/t-finance-earnings-blowout` 完成 (blowout 段已是业绩过关票)
- 此 skill 只判断技术信号 (RSI 双指标超卖), 不再做业绩过滤
- 日级别运行, K 线每日变化, 想看长势可配合 `/t-analyze <code>` 深挖

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan
    # 默认 watchlist + RSI 双指标超卖 + lookback 2

... --lookback 5                 # RSI 看最近 N 根 (默认 2; --lookback 5 更宽松)
... --threshold-rsi6 20          # RSI6 阈值 (默认 25)
... --threshold-rsi12 30         # RSI12 阈值 (默认 30)
... --no-rsi12                   # 关 RSI12 (只用 RSI6)
... --all-market                 # 全市场扫 (默认 watchlist)
... --watchlist-types 持仓       # 只跑某类
... --tech                       # 仅科技板块
... --write-md                   # 写 docs/rsi6-tech-watchlist.md
... --check-news                 # 命中后 MCP 查近 30 天负面新闻 (有则标 ⚠️)
... --news-days 30               # 新闻回溯天数 (默认 30)
```

## 跑流程 (2 步)

1. **step 1** — 跑 RSI 双指标超卖扫描 (DataStore 0 网络)
2. **step 2 (可选, --check-news 时启用)** — 命中后逐只查 MCP 负面新闻
   - 走 `connector__hengsheng__call_api` (StockNewslist, emotionDirectionCode=负面)
   - 近 N 天 (默认 30) 任一负面新闻 → 命中行加 ⚠️
   - MCP 不可用时静默跳过,不影响主流程

## 触发规则 (v4.5 单一信号)

最近 N 根 K 线任一:

1. **RSI 超卖 (唯一条件)**: `RSI6 < 25 AND RSI12 < 30`

**触发标签**: 单一 `"RSI"`

设计取舍:
- v4.5 删放量 spike: 妖股真实时序是 **RSI 超卖 → 21-47 天后才放量启动** (不在同一根 K 线), `RSI + 放量 AND` 时间维度不存在, 24 只妖股 0 命中
- 纯 RSI 超卖: 24 只妖股 **15 只命中 (63%)** (24 只业绩暴增妖股样本)

## 关键约束

- **0 网络**, 走 `DataStore` (K线 parquet)
- **默认 watchlist** 选股 (持仓+blowout+自选), `--all-market` 全市场
- **不做业绩过滤** — 由 `/t-finance-earnings-blowout` 把关 (blowout 段已是业绩过关票)
- `--lookback N` 默认 2 (RSI 看最近 2 根 K 线任一跌破)

## 输出

```
代码      名称     行业     RSI6   RSI12  触发   价格     质量
300972   万辰集团  食品    10.52  21.7   RSI    157.00   ·
002558   巨人网络  互联网   12.85  26.0   RSI    23.60    ·
600292   电投水电  水电     18.60  29.0   RSI    11.38
```

排序 RSI6 升序 (越低越强), 限 50 行。**质量**:
- ⭐⭐ RSI6 < 5
- ⭐  RSI6 < 10
- ·   RSI6 < 15

## 参数速查

| CLI | 默认 | 含义 |
|---|---|---|
| `--all-market` | False | 全市场 (默认 watchlist) |
| `--from-watchlist` | (True) | watchlist 选股 (兼容旧 CLI) |
| `--watchlist-types` | 持仓,blowout,自选 | list_type 过滤 |
| `--threshold-rsi6` | 25 | RSI6 阈值 |
| `--threshold-rsi12` | 30 | RSI12 阈值 |
| `--no-rsi12` | False | 关 RSI12 |
| `--lookback` | 2 | RSI 看最近 N 根任一跌破 |
| `--tech` | False | 科技板块限定 |
| `--write-md` | False | 写 docs/rsi6-tech-watchlist.md |
| `--check-news` | False | 命中后 MCP 查近 30 天负面新闻 |
| `--news-days` | 30 | 新闻回溯天数 (需 --check-news) |

## 命中后

`/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` (单只深挖)
- `/t-finance-earnings-blowout` (上游业绩过滤)
- `/t-near-low` (5y 回撤超跌)
- `/t-finance-roc-ey` (财务多维)
- `/t-concept-macd` (概念 MACD)
- `/t-backtest` (信号回测)
- `/t-sync-data --kline` (跑前必跑)

## 命名沿革

- **v1 `/t-tech-bb-obv`** (技术 + BB + OBV, 2026-07 起步)
- **v2 `/t-macd-r2g`** (MACD 红转绿)
- **v3 `/t-rsi6-oversold`** (RSI 单超卖 + 科技 + yoy 四重)
- **v4 `/t-rsi6-tech`** (当前, watchlist 选股; v4.5 删放量 spike, 纯 RSI 双超卖)
