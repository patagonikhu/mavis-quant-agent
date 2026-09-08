# 统一分析框架 — 6 strategy × 2 周期 (2026-08-29 简化: 删 60m; 2026-09-02 加 ValuationStrategy)

> **本框架是所有 `/t-*` skill 的统一分析基础。** 任何分析个股的 skill 必须使用本框架。
> 命名约定: **"6 strategy × 2 周期"** 是正式名, 之前的"5方法×3周期"和"投资四问 + T 框架"指本框架的子模块。

---

## 1. 框架定义

**6 strategy** (6 个独立分析维度):
1. **缠论 (chan)** — 中枢 + 背驰 + 止跌信号 (3 要素) + 缠论补充 4 方法 (SMC / 量价 / 多市场共振 / 威科夫)
2. **威科夫 (wyckoff)** — 3 大阶段: Accumulation / Markup / Distribution
3. **SMC (smc)** — Order Block + FVG + Liquidity Sweep (周/日 双周期)
4. **OBV (obv)** — 经典 Granville + obv5 (5日价跌+OBV涨) + obv_trend (OBV>MA20) (2026-08-29 简化: 删 60d 段背离)
5. **fflow (fflow)** — Tushare.money_flow 大单+特大单净流入 5 档 verdict (走 DataStore 落盘)
6. **估值 (valuation)** — PEG + DCF L + Magic ROC/EY 三合一 (v6.2.5 合并 peg+dcf)

**× 2 周期** (2 个时间周期, 2026-08-29 删 60m):
- **周线** (1-3 个月) — 决定主升浪方向
- **日线** (1-4 周) — 决定子浪位置 + 缠论 4 级别买卖点

**联合输出**: 6 strategy × 2 周期 = 12 个场景矩阵, 共振数 ≥ 2 重 = 强信号

**权重** (analysis_engine._STRATEGY_WEIGHTS):
chan 0.20 / wyckoff 0.20 / smc 0.10 / obv 0.10 / fflow 0.10 / valuation 0.15 (合计 0.85, 留给 LLM 主观 0.15)

---

## 2. 子模块命名约定 (强制)

| 别名 (旧) | 正式名 (本框架) | 含义 |
|---|---|---|
| 投资四问 | 6 strategy × 2 周期 | 卡点 + TAM + 龙头 + 估值 + T 框架 (5 个子模块) |
| T 框架 | 6 strategy × 2 周期 子模块 4 | T 位置 (event_date - today) / 30 |
| 5类14子信号 | 6 strategy × 2 周期 退出判定 | PEG + L/E3 + MA120 + 板块 + fflow + OBV + 缠论综合 |
| 5方法 | 6 strategy × 2 周期 简称 (旧称) | 6 个独立分析维度 |

**所有 skill description 必须用 "6 strategy × 2 周期" 命名**, 禁止用 "投资四问 + T 框架" 简写。

---

## 3. 完整流程 (3 段式)

### 阶段 1: 拉数据 (Python 工具, 自动)
- `tools/batch/t_analyze_all.py` (v6.2.5 起作为 watchlist 全刷入口)
- 拉 parquet + 算 6 strategy, 写 `docs/{portfolio,watchlist}/analyze-{code}-{name}.md`
- 包含: 缠论三要素 (周/日 中枢+背驰) + 缠论补充 4 方法 + 6 strategy 退出判定 + 3 层仓位 + 止盈止损 4 档

### 阶段 2: 套框架 (LLM, 必读本文件)
- 投资四问 (卡点/TAM/龙头/估值) ← docs/analysis-framework.md §2
- T 框架 (T 位置计算) ← docs/analysis-framework.md §3
- 6 strategy × 2 周期 综合矩阵 (见本文件)
- PEG + DCF L 双指标 ← docs/analysis-framework.md §2.4

### 阶段 3: 落报告 (LLM 套 22 section 模板)
- 工具: `tools/render/report_renderer.py`
- 输出: `docs/analyze-{code}-{name}.md`
- 强制项: 6 strategy × 2 周期 (第一段) + PEG/DCF L (中段) + 三层仓位 (末段)

---

## 4. 数据流依赖图

```
data/history/daily/{YYYYQN}.parquet (duckdb 读)
        │
        ▼
6 strategy × 2 周期 综合矩阵 (本文件 §1)
        │
        ├── 缠论 (analysis['chan'].*)
        ├── 威科夫 (wyckoff_stage)
        ├── SMC (smc_ob)
        ├── OBV (obv_factor) — obv5 + obv_trend
        ├── fflow (fflow_factor) — 走 ctx.moneyflow
        └── valuation (PegFactor + DcfFactor + MagicFormula) — 合并
        │
        ▼
PEG + DCF L (basic_data/peg_calc/dcf_calc)
        │
        ▼
退出信号 (exit_signals) + 止盈止损 (stop_profit_loss) + 三层仓位 (three_layer_position)
        │
        ▼
报告 (22 section) ← tools/render/report_renderer.py
```

---

## 5. 6 strategy vs 6 strategy × 2 周期

- **6 strategy** = 6 个独立分析维度 (无周期)
- **6 strategy × 2 周期** = 6 strategy + 2 周期 = **12 个场景** (6 × 2)
- 报告里**只看 6 strategy × 2 周期**, 不用 6 strategy (因为 6 strategy 不带周期 = 不知道是日线还是周线)

**重要**: 任何 "6 strategy" 的写法都要补全周期 → "6 strategy × 2 周期"。

---

## 6. 跨 skill 引用关系

| Skill | 是否做个股分析 | 引用本框架? |
|---|---|---|
| t-analyze | ✅ 是 (单股详报) | ✅ 主入口 |
| t-sector | ✅ 是 (板块批量) | ✅ 调 t-analyze |
| t-etf | ✅ 是 (ETF 持仓) | ✅ 调 t-analyze |
| t-watchlist | ✅ 是 (57 只批量) | ✅ 调 t-analyze |
| t-bottleneck | ❌ 否 (产业链) | 仅引用 PEG/DCFL |
| t-chain | ❌ 否 (产业链) | 仅引用 PEG |
| t-checklist | ✅ 是 (六关评分) | 调 t-analyze + 引用 MA |
| t-rotation | ❌ 否 (板块轮动) | 不引用 |
| t-trigger | ❌ 否 (信号触发) | 引用缠论字段 |
| t-monitor | ❌ 否 (T 位置监控) | 引用 T 框架 |
| t-signals | ❌ 否 (信号存档) | 不引用 |

---

## 7. 维护规则 (2026-07-27 起)

1. **新 skill** 描述个股分析, 必须引用本文件
2. **改方法** 改本文件 + analysis-framework.md, 不在 skill 里重复定义
3. **术语** 统一用 "5方法×3周期", 不用 "投资四问 + T 框架"
4. **代码** 调 `tools/factors/` 库, 不在 skill 里写内联计算

---

## 8. 关联文件

- `docs/analysis-framework.md` — 投资四问 + T 框架 + 龙头评分 详细定义 (1134 行)
- `tools/factors/` — 22 个 factor 库 (缠论/威科夫/SMC/量价/多市场 + 估值/风控/仓位)
- `tools/render/report_renderer.py` — 22 section 报告模板
