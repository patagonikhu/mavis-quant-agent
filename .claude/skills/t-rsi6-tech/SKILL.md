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

> **单一事实源:** v4.5 干啥的 / 命名沿革 / 默认阈值 / 触发规则 / 占位说明,全部在 `tools/batch/rsi6_tech_scan.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候跑 + 跑最小例子 + 数据特性"。

## 啥时候跑

- **业绩过滤由上游 `/t-finance-earnings-blowout` 完成** (blowout 段已是业绩过关票),本 skill 只判断技术信号
- 日级别运行, K 线每日变化, 想看长势配合 `/t-analyze <code>` 深挖

## 跑

```bash
# 默认 (watchlist + RSI 双指标超卖 + lookback 2)
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan

# 看所有 flag + 默认值 (单一事实源)
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan --help

# 全市场扫 (默认 watchlist)
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan --all-market

# 写 docs/rsi6-tech-watchlist.md
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan --write-md
```

## 数据特性

- **0 网络**, 走 `DataStore` (K线 parquet)
- **默认 watchlist 选股** (持仓+blowout+自选), `--all-market` 全市场
- **不做业绩过滤** — 由 `/t-finance-earnings-blowout` 把关
- **占位 flag**: `--check-news` / `--news-days` 当前在代码层**不真正实现** (MCP connector 是 LLM 内置,bash subprocess 不可用)。LLM agent 在线时,应手工调 MCP `StockNewslist` + `emotionDirectionCode=负面` 替代
- v4.5 (2026-09-23) 删放量 spike:妖股真实时序 = RSI 超卖 → 21-47 天后才放量启动,同一根 K 线 AND 永远命中不了,24 只妖股 0 命中

## 跑后验证

```bash
# 列命中
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan --write-md
head -20 docs/rsi6-tech-watchlist.md

# 看 RSI6 升序 hits (LLM agent 在线时手工跑)
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan --limit 20
```

## 相关

- `/t-finance-earnings-blowout` (上游业绩过滤)
- `/t-analyze <code>` (单只深挖)
- `/t-near-low` (5y 回撤超跌)
- `/t-finance-roc-ey` (财务多维)
- `/t-sync-data --kline` (跑前必跑)
