---
name: t-concept-macd
description: THS 同花顺概念板块反转信号扫描. 板块作为真实指数, 直接喂 THS K线 (无合成 K线). 跑 RSI/MACD/BARΔ, 输出每概念 1 个 md. 0 网络. 触发词: "概念反转"、"概念 MACD"、"板块反转"、"概念扫描".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.concept_macd_scan              # 默认 watchlist.ths_whitelist 15 个概念
... --concepts CPO PCB 机器人                                  # 指定概念名 (空格分隔)
... --lookback 500                                             # 回看天数 (默认 500=~2 年)
... --output-dir docs/concept-macd                             # md 输出目录 (默认 docs/concept-macd)
... --no-summary                                               # 不输出 chat summary
... --workers 4                                                # 进程数 (默认 4)
```

## 核心算法 (THS 概念板块 K线 + 复用 analysis_engine)

```
1. 从 watchlist.json ths_whitelist 读 15 个概念 match 名
   (单个 --concepts 指定时, 用这 N 个名代替白名单)
2. 对每个名: DataStore.get_ths_kline(match) → 反查名码 + 读 parquet
3. 包装 RawContext → 喂给 AnalysisEngine.analyze_history (复用, 跟个股同接口)
4. compute_factor_history(ctx, step, lookback, history)
5. 输出 md (docs/concept-macd/<code>.md) + chat summary
```

## 关键约束

- **0 网络**: 走 DataStore.get_ths_kline (读 ths_kline/*.parquet)
- **复用**: `compute_factor_history(ctx, step, lookback, history)` 接口与个股完全一致
- **数据要求**: 跑前必须 `/t-sync-data --ths` 把概念落盘 (THS K线在 data/history/ths/kline/)
- **缺数据**: 报"请先 /t-sync-data --ths, 没找到 data/history/ths/ths_index.parquet"

## 输出

### md 文件 (每个概念 1 个)

`docs/concept-macd/<ts_code>.md` 包含:
- 板块基本信息 (龙头股列表)  ← v3: 改名"成分股 (THS 概念总览)"
- 当前状态 (合成 K线 RSI6/12, DIF/DEA/BAR/BARΔ, MA20偏离)
- **反转信号**:
  - 🟢 红柱 + BARΔ 转正 (底信号, 近 1 年 N 次)
  - 🔴 绿柱 + BARΔ 转负 (顶信号, 近 1 年 N 次)
- **顶底信号**:
  - 📉 DIF 新低 (近 1 年最低 + 后续 BARΔ 转正确认)
  - 📈 DIF 新高 (近 1 年最高 + 后续 BARΔ 转负确认)
- **历史因子走势** (THS K线, 默认 500 天)
  - 23 列: 跟个股详报完全一致 schema

### chat summary (默认)

每个概念 1 段:
- ts_code + name (ts_code 在前, name 在后)
- RSI6/12 + DIF/DEA/BAR/BARΔ + 状态描述
- 反转信号 (近 1 年 N 次 最近触发日)
- 顶底信号 (DIF 新低/新高 + 确认状态)

## 相关

- `/t-sync-data --ths` (跑前必跑, 落盘 THS 概念 K线)
- `/t-analyze <code>` (单只详报)
- `/t-rsi6-tech` / `/t-roc-ey`

## 命名沿革

- **v1 `/t-sector-ma`** (MA5/20/60/120 顶底分数, 2026-07)
- **v2 `/t-sector-macd`** (合成 K线 + 流通市值加权, 2026-09-17, 已弃)
- **v3 `/t-concept-macd`** (当前, 2026-09-17) — THS 概念板块直接读 (替代 v2 加权合成, 消除跳价误差, 复用 AnalysisEngine 同 schema)
