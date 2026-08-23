# 统一分析框架 — 5方法×3周期 (2026-07-27 固化)

> **本框架是所有 `/t-*` skill 的统一分析基础。** 任何分析个股的 skill 必须使用本框架。
> 命名约定: **"5方法×3周期"** 是正式名, 之前的"投资四问 + T 框架"和"5类14子信号"都指本框架的子模块。

---

## 1. 框架定义

**5 方法** (5 个独立分析维度):
1. **缠论** — 中枢 + 背驰 + 止跌信号 (3 要素) + 缠论补充 4 方法 (SMC / 量价 / 多市场共振 / 威科夫)
2. **威科夫** — 3 大阶段: Accumulation / Markup / Distribution
3. **SMC** — Order Block (60m 简化版 5 根内突破算法)
4. **量价** — OBV + 5档判定 (强进货/弱进货/中性/偏出货/强出货)
5. **多市场共振** — 个股 vs 创业板 / 科创50 / 沪深300

**× 3 周期** (3 个时间周期):
- **周线** (1-3 个月) — 决定主升浪方向
- **日线** (1-4 周) — 决定子浪位置 + 缠论 4 级别买卖点
- **60 分** (数小时) — 精确进场 / 出场

**联合输出**: 5方法×3周期 = 15 个场景矩阵, 共振数 ≥ 2 重 = 强信号

---

## 2. 子模块命名约定 (强制)

| 别名 (旧) | 正式名 (本框架) | 含义 |
|---|---|---|
| 投资四问 | 5方法×3周期 | 卡点 + TAM + 龙头 + 估值 + T 框架 (5 个子模块) |
| T 框架 | 5方法×3周期 子模块 4 | T 位置 (event_date - today) / 30 |
| 5类14子信号 | 5方法×3周期 退出判定 | v11 + PEG + L/E3 + MA120 + 板块 + fflow + OBV + 缠论综合 |
| 5方法 | 5方法×3周期 简称 | 5 个独立分析维度 |

**所有 skill description 必须用 "5方法×3周期" 命名**, 禁止用 "投资四问 + T 框架" 简写。

---

## 3. 完整流程 (3 段式)

### 阶段 1: 拉数据 (Python 工具, 自动)
- `tools/refresh_all.sh` 或 `tools/sync_stock.py`
- 拉 parquet + 算 8 strategy, 写 `docs/analyze-{code}-{name}.md`
- 包含: 缠论三要素 (周/日/60分中枢+背驰) + 缠论补充 4 方法 + 5方法×3周期 退出判定 + 3 层仓位 + 止盈止损 4 档

### 阶段 2: 套框架 (LLM, 必读本文件)
- 投资四问 (卡点/TAM/龙头/估值) ← docs/analysis-framework.md §2
- T 框架 (T 位置计算) ← docs/analysis-framework.md §3
- 5方法×3周期 综合矩阵 ← 抽到 `.claude/skills/_shared/supplement_analysis.md`
- PEG + DCF L 双指标 ← docs/analysis-framework.md §2.4

### 阶段 3: 落报告 (LLM 套 22 section 模板)
- 工具: `tools/render/report_renderer.py`
- 输出: `docs/analyze-{code}-{name}.md`
- 强制项: 5方法×3周期 (第一段) + PEG/DCF L (中段) + 三层仓位 (末段)

---

## 4. 数据流依赖图

```
data/history/daily/{year}.parquet (duckdb 读)
        │
        ▼
5方法×3周期 综合矩阵 (本文件 §1)
        │
        ├── 缠论 (analysis['chan'].*)
        ├── 威科夫 (wyckoff_stage)
        ├── SMC (smc_ob)
        ├── 量价 (volume_obv)
        └── 多市场共振 (macro_resonance)
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

## 5. 5方法 vs 5方法×3周期

- **5方法** = 5 个独立分析维度 (无周期)
- **5方法×3周期** = 5 方法 + 3 周期 = **15 个场景** (5 × 3)
- 报告里**只看 5方法×3周期**, 不用 5方法 (因为 5 方法不带周期 = 不知道是日线还是 60 分)

**重要**: 任何 "5方法" 的写法都要补全周期 → "5方法×3周期"。

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
| t-ranking | ✅ 是 (排序) | 调 t-analyze + 引用 PEG/MA |
| t-rotation | ❌ 否 (板块轮动) | 不引用 |
| t-trigger | ❌ 否 (信号触发) | 引用缠论字段 |
| t-monitor | ❌ 否 (T 位置监控) | 引用 T 框架 |
| t-signals | ❌ 否 (信号存档) | 不引用 |

---

## 7. 维护规则 (2026-07-27 起)

1. **新 skill** 描述个股分析, 必须引用本文件 + `.claude/skills/_shared/supplement_analysis.md`
2. **改方法** 改本文件 + analysis-framework.md, 不在 skill 里重复定义
3. **术语** 统一用 "5方法×3周期", 不用 "投资四问 + T 框架"
4. **代码** 调 `tools/factors/` 库, 不在 skill 里写内联计算

---

## 8. 关联文件

- `.claude/skills/_shared/supplement_analysis.md` — 5方法×3周期 矩阵 + 场景判定
- `docs/analysis-framework.md` — 投资四问 + T 框架 + 龙头评分 详细定义 (1134 行)
- `tools/factors/` — 22 个 factor 库 (缠论/威科夫/SMC/量价/多市场 + 估值/风控/仓位)
- `tools/render/report_renderer.py` — 22 section 报告模板
