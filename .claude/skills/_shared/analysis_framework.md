# 统一分析框架 — 威科夫 + 估值

> **本框架是所有 `/t-*` skill 的统一分析基础。** 任何分析个股的 skill 必须使用本框架。
> 决策走 **威科夫 3 大阶段 (Accumulation / Markup / Distribution)** + 估值 (PE + Magic ROC/EY).
>
> **2026-11-02: 估值双指标 (PEG + DCF L) 已删** —— PEG 依赖 EPS 预测, 预测本身不准,
> DCF 用同一套 EPS 预测。估值只看 **PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆)**。
>
> **2026-11-04: EPS 机构一致预期整档已删** —— 它是预测值, 删 PEG/DCF 后已无消费者。
> 财报里的**真实 EPS** (已审计) 仍在用, 别混为一谈。
>
> **2026-11-06: 缠论整层下线** —— 缠论 (中枢 / 背驰 / 止跌 / 买卖点 1买2买3买1卖2卖3卖)
> 全部是 **czsc** 产物, 连 czsc 依赖一起删。**因子矩阵投票逻辑整块删除**
> (不是把阈值从 2 降到 1)。决策主入口改为**威科夫阶段判定**:
> - A→M (Accumulation→Markup) = **买入信号**
> - M→D (Markup→Distribution) = **卖出信号**
>
> ⚠️ 「投资四问 / T 框架」等命名别名随缠论下线一并作废 (原别名本身就是「缠论 + 估值双指标」的别名)。

---

## 1. 框架定义

**2 个核心分析维度**:
1. **威科夫 (wyckoff)** — 3 大阶段 (Accumulation / Markup / Distribution) + 9 种 sub-event
2. **估值 (valuation)** — 静态 PE + Magic ROC/EY (2026-11-02 删 PEG/DCF)

**辅助维度 (仅供参考, 不汇总)**:
- TechnicalStrategy (technical) — 8 个技术指标 (MACD/RSI/KDJ/BOLL/ATR/量比)
- 多市场共振 (resonance) — 已移除 (2026-11-05, `resonance_3period` 网络调用早已删除, 该票恒为 0)
- 缠论 (chan) — 已移除 (2026-11-06, 缠论整层下线)

**× 2 周期** (2 个时间周期, 2026-08-29 删 60m):
- **周线** (1-3 个月) — 决定主升浪方向
- **日线** (1-4 周) — 决定子浪位置

**联合输出**: 威科夫阶段 (日/周) + 9 种 sub-event 触发 + 估值 (PE + Magic ROC/EY) = 决策 2 维

**权重**: 无. 决策走威科夫阶段转变 + sub-event + 风控.

---

## 2. 子模块命名约定 (强制)

| 别名 (旧) | 状态 | 备注 |
|---|---|---|
| 投资四问 | ⚠️ 已作废 (2026-11-06) | 原别名 = 「缠论 + 估值双指标」, 缠论下线后别名失效 |
| T 框架 | ⚠️ 已作废 (2026-11-06) | 同上 |
| 缠论 + 估值双指标 | ⚠️ 已作废 (2026-11-06) | 缠论已下线, 用「威科夫 + 估值双指标」 |
| 威科夫 + 估值双指标 | ✅ 正式名 | 当前框架名 |

> ⚠️ 2026-11-03 起: 「🎯 投资四问」「⏰ T 框架」「🎯 T 框架事件」「🚨 5 类 14 子信号」「📊 基础信息 (Tushare)」五个 section 已从 md 报告删除,
> 数据管线一并删除 (「📊 基础信息」仅删 renderer 一档, `DataStore.get_stock_basic` 数据源保留)。以下两套命名仍用于 **LLM 手动套框架** (不自动进报告);
> T 事件内容由 LLM 在生成报告时从外部查 (年报/新闻/公告) 后直接写入正文。
>
> ⚠️ 2026-11-03 起: 「🎯 止盈 3 层」「🛑 止损 4 档」两档已从 md 报告删除, 数据管线一并删除。
> 理由: 两档都是「当前价 × 固定百分比」的机械外推 (止盈 +5/+10/+20%, 止损 -3/-5/-8/-10%),
> 无标的特异性判断。
>
> ⚠️ 2026-11-03 起: 「🟢 退场信号检查」一档也已从 md 报告删除, 数据管线一并删除。
> 理由: 纯缠论退出判定 (60分底背/60分顶背/止跌信号 → 一句话 verdict) 与
> 「📌 监控触发点」完全重叠。
>
> ⚠️ 2026-11-03 起: 「📌 监控触发点」一档也已从 md 报告删除, 数据管线一并删除
> (factor_risk.compute_monitor_triggers + analysis_engine._derive_monitor_triggers
> + render_data 的 monitor_triggers property; factor_risk.py 删空后整体移除)。
> 理由: 时间触发恒为空 (调用方传 events=[] / code=""), 中枢突破/跌破两档「已触发」硬编码 False,
> 仓位比例与止损价是「当前价 × 固定百分比」的机械外推。
>
> ⚠️ 2026-11-03 起: 「📋 3 层仓位策略」一档也已从 md 报告删除, 数据管线一并删除
> (factor_basic.compute_three_layer_position + analysis_engine._derive_three_layer_position
> + render_data 的 three_layer_position / position_layer 两个 property; factor_basic.py 删空后整体移除)。
> 理由: 中枢位三档仓位 + 止损/目标阶梯, 价位全是「当前价 × 固定百分比」的机械外推,
> 与「📌 监控触发点」重叠; 两档同批删除。

**所有 skill description 必须用 "威科夫 + 估值双指标" 命名**, 禁止用 "6 strategy" / "5方法" / "7 strategy 权重" 等弃用术语.

---

## 3. 完整流程 (3 段式)

### 阶段 1: 拉数据 (Python 工具, 自动)
- `tools/batch/t_analyze_all.py` (v6.2.5 起作为 watchlist 全刷入口)
- 拉 parquet + 跑 3 个 strategy 类 (wyckoff/technical/finance), 写 `docs/{portfolio,watchlist}/analyze-{code}-{name}.md`
- 包含: 威科夫 3 大阶段判定 (周/日) + 9 种 sub-event + 技术指标

### 阶段 2: 套框架 (LLM, 必读本文件)
- ~~投资四问 (卡点/TAM/龙头/估值)~~ ← handbook/analysis-framework.md §2 (2026-11-03 起不在报告内, 仅方法论参考)
- ~~T 框架 (T 位置计算)~~ ← handbook/analysis-framework.md §3 (2026-11-03 起不在报告内, 仅方法论参考)
- 威科夫阶段转变 (A→M 买 / M→D 卖) + 估值 (PE + Magic ROC/EY) ← handbook/analysis-framework.md §2.4

### 阶段 3: 落报告 (LLM 套 7 section 模板)
- 工具: `tools/render/report_renderer.py`
- 输出: `docs/analyze-{code}-{name}.md`
- 强制项: 4 季财务大表 (中段) + 威科夫阶段详情 + 因子历史表 (21 列)

---

## 4. 数据流依赖图

```
data/history/daily/{YYYYQN}.parquet (duckdb 读)
        │
        ▼
3 个 strategy 类 (本文件 §1)
        │
        ├── 威科夫 (wyckoff_stage) — **决策主入口**
        ├── technical (8 个技术指标)
        └── finance (MagicFormula / ROC / EY) — 合并
        │
        ▼
Magic ROC/EY (basic_data/magic_calc) — **决策第二维**
        │
        ▼
威科夫阶段转变信号 (A→M / M→D) + 9 种 sub-event
        │
        ▼
报告 (7 section) ← tools/render/report_renderer.py
```

---

## 5. 方向信号来源

> 2026-11-06 缠论整层下线后, 报告里的 buy/sell 方向信号全部来自威科夫侧:
>
> | 信号 | 方向 | 来源 |
> |---|---|---|
> | A→M 主升启动 (daily/weekly) | buy | `extract_signals` → `wyckoff_stage_change` |
> | M→D 派发开始 (daily/weekly) | sell | 同上 |
> | Spring / LPS / SOS / SC / AR / Compression | buy | `wyckoff_event_bot` |
> | UTAD / EVR / LPSY / BC / UT / DistributionStart | sell | `wyckoff_event_top` |
> | MA 超跌 ≤-10% | buy | `ma_dev_cross` |
> | MA 过热 ≥+10% | sell | `ma_dev_cross` |
>
> 因子矩阵的**综合判定** (🥈标准建仓 / 🟠标准减仓 / 🟡观察) 已随投票逻辑整档删除。

---

## 6. 跨 skill 引用关系

| Skill | 是否做个股分析 | 引用本框架? |
|---|---|---|
| t-analyze | ✅ 是 (单股详报) | ✅ 主入口 |
| t-sector-ma | ✅ 是 (板块) | 调 batch factor_matrix |
| t-earnings-blowout | ✅ 是 (R3 反转) | 走 financials parquet |
| t-backtest | ❌ 否 (5y 回测) | 不引用 |
| t-sync-data | ❌ 否 (sync 入口) | 唯一允许网络 |
| t-guardrail | ❌ 否 (静态扫描) | network + factor + parquet + safe-fs 4 check |

---

## 7. 维护规则 (2026-07-27 起)

1. **新 skill** 描述个股分析, 必须引用本文件
2. **改方法** 改本文件 + analysis-framework.md, 不在 skill 里重复定义
3. **术语** 统一用 "威科夫 + 估值双指标"
4. **代码** 调 `tools/factors/` 库, 不在 skill 里写内联计算

---

## 8. 关联文件

- `handbook/analysis-framework.md` — 投资四问 + T 框架 + 龙头评分 详细定义 (方法论; 2026-11-03 起不再进报告)
- `tools/factors/` — 3 个 factor 库 (威科夫 `wyckoff/` / 估值 `valuation/` / 宏观 `macro/`) + 顶层 `utils.py` + `kline_arrays.py`
- `tools/render/report_renderer.py` — 7 section 报告模板
