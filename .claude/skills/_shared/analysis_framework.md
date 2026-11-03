# 统一分析框架 — 缠论 + 估值

> **本框架是所有 `/t-*` skill 的统一分析基础。** 任何分析个股的 skill 必须使用本框架。
> 决策走 **缠论 1买/2买/3买/1卖/2卖/3卖** + 估值 (PE + Magic ROC/EY).
>
> **2026-11-02: 估值双指标 (PEG + DCF L) 已删** —— PEG 依赖 EPS 预测, 预测本身不准,
> DCF 用同一套 EPS 预测。估值只看 **PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆)**。

---

## 1. 框架定义

**2 个核心分析维度**:
1. **缠论 (chan)** — 1买/2买/3买/1卖/2卖/3卖 + 中枢 + 背驰 + 止跌信号 (3 要素)
2. **估值 (valuation)** — 静态 PE + Magic ROC/EY (2026-11-02 删 PEG/DCF)

**辅助维度 (仅供参考, 不汇总)**:
- 威科夫 (wyckoff) — 3 阶段: Accumulation / Markup / Distribution (阶段判定)
- 结构分析 (structure) — Order Block + FVG + Liquidity Sweep (周/日 双周期)
- TechnicalStrategy (technical) — 8 个技术指标 (MACD/RSI/KDJ/BOLL/ATR/量比)
- 多市场共振 (resonance) — 不参与 (v3.1 起 WAF 频发)

**× 2 周期** (2 个时间周期, 2026-08-29 删 60m):
- **周线** (1-3 个月) — 决定主升浪方向
- **日线** (1-4 周) — 决定子浪位置 + 缠论 4 级别买卖点

**联合输出**: 缠论 6 个买卖点 (1买/2买/3买/1卖/2卖/3卖) + 估值 (PE + Magic ROC/EY) = 决策 2 维

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

> ⚠️ 2026-11-03 起: 「🎯 投资四问」「⏰ T 框架」「🎯 T 框架事件」「🚨 5 类 14 子信号」四个 section 已从 md 报告删除,
> 数据管线一并删除。以下两套命名仍用于 **LLM 手动套框架** (不自动进报告);
> T 事件内容由 LLM 在生成报告时从外部查 (年报/新闻/公告) 后直接写入正文。
>
> ⚠️ 2026-11-03 起: 「🎯 止盈 3 层」「🛑 止损 4 档」两档已从 md 报告删除, 数据管线一并删除。
> 理由: 两档都是「当前价 × 固定百分比」的机械外推 (止盈 +5/+10/+20%, 止损 -3/-5/-8/-10%),
> 无标的特异性判断。
>
> ⚠️ 2026-11-03 起: 「🟢 退场信号检查」一档也已从 md 报告删除, 数据管线一并删除。
> 理由: 纯缠论退出判定 (60分底背/60分顶背/止跌信号 → 一句话 verdict) 与
> 「📌 监控触发点」完全重叠; 卖点 (1卖/2卖/3卖) 仍由
> 缠论买卖点 section 独立提供, 删除不丢卖点信息。
>
> ⚠️ 2026-11-03 起: 「📌 监控触发点」一档也已从 md 报告删除, 数据管线一并删除
> (factor_risk.compute_monitor_triggers + analysis_engine._derive_monitor_triggers
> + render_data 的 monitor_triggers property; factor_risk.py 删空后整体移除)。
> 理由: 时间触发恒为空 (调用方传 events=[] / code=""), 中枢突破/跌破两档「已触发」硬编码 False,
> 仓位比例与止损价是「当前价 × 固定百分比」的机械外推;
> 删除后报告已无独立风控 section, 买卖点 (1买~3卖) 仍由缠论买卖点 section 独立提供。
>
> ⚠️ 2026-11-03 起: 「📋 3 层仓位策略」一档也已从 md 报告删除, 数据管线一并删除
> (factor_basic.compute_three_layer_position + analysis_engine._derive_three_layer_position
> + render_data 的 three_layer_position / position_layer 两个 property; factor_basic.py 删空后整体移除)。
> 理由: 中枢位三档仓位 + 止损/目标阶梯, 价位全是「当前价 × 固定百分比」的机械外推,
> 与「📌 监控触发点」重叠; 两档同批删除,
> 卖点 (1卖/2卖/3卖) 仍由缠论买卖点 section 独立提供。

**所有 skill description 必须用 "缠论 + 估值双指标" 命名**, 禁止用 "6 strategy" / "5方法" / "7 strategy 权重" 等弃用术语.

---

## 3. 完整流程 (3 段式)

### 阶段 1: 拉数据 (Python 工具, 自动)
- `tools/batch/t_analyze_all.py` (v6.2.5 起作为 watchlist 全刷入口)
- 拉 parquet + 跑 4 个 strategy 类 (chan/wyckoff/structure/finance), 写 `docs/{portfolio,watchlist}/analyze-{code}-{name}.md`
- 包含: 缠论三要素 (周/日 中枢+背驰) + 缠论补充 4 方法

### 阶段 2: 套框架 (LLM, 必读本文件)
- ~~投资四问 (卡点/TAM/龙头/估值)~~ ← handbook/analysis-framework.md §2 (2026-11-03 起不在报告内, 仅方法论参考)
- ~~T 框架 (T 位置计算)~~ ← handbook/analysis-framework.md §3 (2026-11-03 起不在报告内, 仅方法论参考)
- 缠论 1买/2买/3买 + 估值 (PE + Magic ROC/EY) ← handbook/analysis-framework.md §2.4
- 因子 × 2 周期 综合矩阵 (3 类等权投票: chan/wyckoff/resonance, 仅作参考, 不进仓位/退出决策)

### 阶段 3: 落报告 (LLM 套 8 section 模板)
- 工具: `tools/render/report_renderer.py`
- 输出: `docs/analyze-{code}-{name}.md`
- 强制项: 缠论三要素 (第一段) + 4 季财务大表 (中段) + 缠论买卖点 (末段)

---

## 4. 数据流依赖图

```
data/history/daily/{YYYYQN}.parquet (duckdb 读)
        │
        ▼
4 个 strategy 类 (本文件 §1, 仅供 factor_matrix 投票)
        │
        ├── 缠论 (analysis['chan'].*) — **决策主入口**
        ├── 威科夫 (wyckoff_stage)
        ├── 结构分析 (structure_ob)
        └── finance (MagicFormula / ROC / EY) — 合并
        │
        ▼
Magic ROC/EY (basic_data/magic_calc) — **决策第二维**
        │
        ▼
缠论买卖点 (buy_sell_points) — 走纯缠论
        │
        ▼
报告 (8 section) ← tools/render/report_renderer.py
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
| t-earnings-blowout | ✅ 是 (R3 反转) | 走 financials parquet |
| t-backtest | ❌ 否 (5y 回测) | 不引用 |
| t-sync-data | ❌ 否 (sync 入口) | 唯一允许网络 |
| t-guardrail | ❌ 否 (静态扫描) | network + factor + eps-scope + parquet + safe-fs 5 check |

---

## 7. 维护规则 (2026-07-27 起)

1. **新 skill** 描述个股分析, 必须引用本文件
2. **改方法** 改本文件 + analysis-framework.md, 不在 skill 里重复定义
3. **术语** 统一用 "缠论 + 估值双指标"
4. **代码** 调 `tools/factors/` 库, 不在 skill 里写内联计算

---

## 8. 关联文件

- `handbook/analysis-framework.md` — 投资四问 + T 框架 + 龙头评分 详细定义 (1134 行, 方法论; 2026-11-03 起不再进报告)
- `tools/factors/` — 22 个 factor 库 (缠论/威科夫/结构/量价/多市场 + 估值/风控/仓位)
- `tools/render/report_renderer.py` — 8 section 报告模板
