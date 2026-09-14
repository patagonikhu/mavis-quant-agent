# 统一分析框架 — 缠论 + 估值双指标

> **本框架是所有 `/t-*` skill 的统一分析基础。** 任何分析个股的 skill 必须使用本框架。
> 决策走 **缠论 1买/2买/3买/1卖/2卖/3卖** + 估值双指标 (PEG + DCF L).

---

## 1. 框架定义

**2 个核心分析维度**:
1. **缠论 (chan)** — 1买/2买/3买/1卖/2卖/3卖 + 中枢 + 背驰 + 止跌信号 (3 要素)
2. **估值 (valuation)** — PEG (双指标) + DCF L (3 档 r=8/10/12%) + Magic ROC/EY 三合一 (v6.2.5 合并)

**辅助维度 (仅供参考, 不汇总)**:
- 威科夫 (wyckoff) — 3 阶段: Accumulation / Markup / Distribution (阶段判定)
- SMC (smc) — Order Block + FVG + Liquidity Sweep (周/日 双周期)
- OBV (obv) — 经典 Granville + obv5 + obv_trend (2026-08-29 简化)
- fflow (fflow) — Tushare.money_flow 大单+特大单净流入 5 档 (走 DataStore 落盘)
- TechnicalStrategy (technical) — 8 个技术指标 (MACD/RSI/KDJ/BOLL/ATR/量比)
- 多市场共振 (resonance) — 不参与 (v3.1 起 WAF 频发)

**× 2 周期** (2 个时间周期, 2026-08-29 删 60m):
- **周线** (1-3 个月) — 决定主升浪方向
- **日线** (1-4 周) — 决定子浪位置 + 缠论 4 级别买卖点

**联合输出**: 缠论 6 个买卖点 (1买/2买/3买/1卖/2卖/3卖) + 估值双指标 (PEG + DCF L) = 决策 2 维

**权重**: 无. 决策走缠论 1买/2买/3买/1卖/2卖/3卖 + 风控.

---

## 2. 子模块命名约定 (强制)

| 别名 (旧) | 正式名 (本框架) | 含义 |
|---|---|---|
| 投资四问 | 缠论 + 估值双指标 | 卡点 + TAM + 龙头 + 估值 (4 问) + T 框架 |
| T 框架 | 缠论 + 估值双指标 子模块 | T 位置 (event_date - today) / 30 |
| 旧称 | 正式名 (本框架) | 备注 |
|---|---|---|
| 投资四问 + T 框架 | 缠论 + 估值双指标 | 卡点/TAM/龙头/估值 + T 位置 |

**所有 skill description 必须用 "缠论 + 估值双指标" 命名**, 禁止用 "6 strategy" / "5方法" / "7 strategy 权重" 等弃用术语.

---

## 3. 完整流程 (3 段式)

### 阶段 1: 拉数据 (Python 工具, 自动)
- `tools/batch/t_analyze_all.py` (v6.2.5 起作为 watchlist 全刷入口)
- 拉 parquet + 跑 6 个 strategy 类 (chan/wyckoff/smc/obv/fflow/finance), 写 `docs/{portfolio,watchlist}/analyze-{code}-{name}.md`
- 包含: 缠论三要素 (周/日 中枢+背驰) + 缠论补充 4 方法 + 退出判定 + 3 层仓位 + 止盈止损 4 档 (走缠论, 不走 strategy 权重)

### 阶段 2: 套框架 (LLM, 必读本文件)
- 投资四问 (卡点/TAM/龙头/估值) ← docs/analysis-framework.md §2
- T 框架 (T 位置计算) ← docs/analysis-framework.md §3
- 缠论 1买/2买/3买 + 估值双指标 (PEG + DCF L) ← docs/analysis-framework.md §2.4
- 因子 × 2 周期 综合矩阵 (5 类等权投票, 仅作参考, 不进仓位/退出决策)

### 阶段 3: 落报告 (LLM 套 22 section 模板)
- 工具: `tools/render/report_renderer.py`
- 输出: `docs/analyze-{code}-{name}.md`
- 强制项: 缠论三要素 (第一段) + PEG/DCF L (中段) + 三层仓位 (末段)

---

## 4. 数据流依赖图

```
data/history/daily/{YYYYQN}.parquet (duckdb 读)
        │
        ▼
6 个 strategy 类 (本文件 §1, 仅供 factor_matrix 投票)
        │
        ├── 缠论 (analysis['chan'].*) — **决策主入口**
        ├── 威科夫 (wyckoff_stage)
        ├── SMC (smc_ob)
        ├── OBV (obv_factor) — obv5 + obv_trend
        ├── fflow (fflow_factor) — 走 ctx.moneyflow
        └── finance (PegFactor + DcfFactor + MagicFormula) — 合并
        │
        ▼
PEG + DCF L (basic_data/peg_calc/dcf_calc) — **决策第二维**
        │
        ▼
退出信号 (exit_signals) + 止盈止损 (stop_profit_loss) + 三层仓位 (three_layer_position) — 走纯缠论
        │
        ▼
报告 (22 section) ← tools/render/report_renderer.py
```

---

## 5. (空)

> 决策全走缠论 1买/2买/3买/1卖/2卖/3卖 + 风控
> 因子矩阵 (5 类等权投票) 仅作参考, 不进仓位/退出决策

---

## 6. 跨 skill 引用关系

| Skill | 是否做个股分析 | 引用本框架? |
|---|---|---|
| t-analyze | ✅ 是 (单股详报) | ✅ 主入口 |
| t-sector-ma | ✅ 是 (板块) | 调 batch factor_matrix |
| t-finance | ❌ 否 (全市场排名) | 不引用 |
| t-earnings-blowout | ✅ 是 (R3 反转) | 走 financials parquet |
| t-bb-obv | ❌ 否 (BOLL+OBV 扫描) | 不引用 |
| t-near-low | ❌ 否 (5y 低清单) | 不引用 |
| t-backtest | ❌ 否 (5y 回测) | 不引用 |
| t-sync-data | ❌ 否 (sync 入口) | 唯一允许网络 |
| t-guardrail | ❌ 否 (静态扫描) | network + factor + eps-scope 3 check |

---

## 7. 维护规则 (2026-07-27 起)

1. **新 skill** 描述个股分析, 必须引用本文件
2. **改方法** 改本文件 + analysis-framework.md, 不在 skill 里重复定义
3. **术语** 统一用 "缠论 + 估值双指标"
4. **代码** 调 `tools/factors/` 库, 不在 skill 里写内联计算

---

## 8. 关联文件

- `docs/analysis-framework.md` — 投资四问 + T 框架 + 龙头评分 详细定义 (1134 行)
- `tools/factors/` — 22 个 factor 库 (缠论/威科夫/SMC/量价/多市场 + 估值/风控/仓位)
- `tools/render/report_renderer.py` — 22 section 报告模板
