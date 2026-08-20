# 持仓 6 只复盘 (2026-07-25 v4, 威科夫 3 大阶段完全对齐 WyckoffTradingAgent)

## 复盘维度
- 浮盈/浮亏
- 1 买/1 卖 (缠论严格化: 底/顶背驰 + 分型确认, 趋势 1 买需 2 中枢)
- **威科夫 3 大阶段 (v4 完全对齐 WyckoffTradingAgent)**
  - Accumulation 累积 (3 子阶段 Accum_A/B/C, base_low + MA gap + 量能)
  - Markup 主升浪 (MA50/MA200 金叉 + 持续 5 日 + gap > 0.5%)
  - Distribution 派发 (bias_200 > 30% + 3 日连续缩量)
  - **无 D/E 阶段** (Spring 是 Markup 的 sub-event, 不是独立阶段)
- 威科夫 12 种 sub-event (新加 EVR 来自 WyckoffTradingAgent)
- 主力 fflow (东方财富/Tushare 真实数据)

## v4 复盘结果 (3 大阶段, 18 只回归 100% Linter 通过)

| 票 | 浮 | 价格 | 1买 | 1卖 | 日阶段 | 日 sub_event | 解读 |
|---|---|---|---|---|---|---|---|
| **000725 京东方A** | -27.55% | ¥5.79 | ❌ | ❌ | **Markup** | 8/12 (UT/UTAD/EVR) | **弱 Markup 信号** — 上升趋势但 ma50<ma200, 历史顶部痕迹 |
| **600089 特变电工** | +26.57% | ¥20.39 | **⭐ 趋势1买** | ❌ | **Accumulation/Accum_C** | 4/12 | **⭐ 趋势 1 买 + 累积末段** — 跟 4 合 1 趋势 1 买 + 威科夫 Accum_C 双重确认 |
| **601958 金钼股份** | +25.87% | ¥20.70 | 2买 (¥19.75) | ❌ | **Accumulation** | 6/12 | **横盘吸筹** — bias_200=+22% 高位, pos=18% 回撤中 |
| **600362 江西铜业** | -5.01% | ¥42.50 | ❌ | ❌ | **Accumulation** | 9/12 | **横盘吸筹** — slope=-5.1%, pos=28%, 弱信号 |
| **002475 立讯精密** | +2.67% | ¥60.59 | ❌ | ❌ | **Accumulation** | 6/12 | **高位回撤** — bias_200=+15.4%, slope=-15.8%, 弱信号 |
| **300274 阳光电源** | -15.04% | ¥113.42 | **⭐ 趋势1买** | ❌ | **Accumulation** ⭐ | 9/12 (SC/BC/SOS/UT/AR/EVR) | **⭐ 趋势 1 买 + 底部吸筹** — SC 抛售高潮 + 9 sub_event 确认 |

## 关键发现 (v4)

### 1. v4 跟 v3 判定对比 (完全对齐 WyckoffTradingAgent)

| 票 | v3 阶段 | v4 阶段 | 变化 | 含义 |
|---|---|---|---|---|
| 阳光电源 | E 弹簧 80% | **Accumulation** | E → A | 跟 WyckoffTradingAgent 一致 (Spring 不是独立阶段) |
| 特变电工 | A/Accum_C 54% | **A/Accum_C** | 同 | 累积末段确认 |
| 江西铜业 | C 派发 100% | **Accumulation** | C → A | 横盘阶段, 不算派发 |
| 金钼股份 | C 派发 100% | **Accumulation** | C → A | bias_200=+22% 但 pos=18% 回撤中 |
| 立讯精密 | D 80% | **Accumulation** | D → A | 高位回撤, 弱信号 |
| 胜宏科技 | D 66% | **Accumulation** | D → A | 大跌后底部 |
| 京东方A | B 33% | **Markup** | B → Mark | 上升趋势但 ma50<ma200, 弱信号 |

**v4 关键改进**: 完全删除 D/E 阶段, 跟 WyckoffTradingAgent 一致 (D/E 在 WyckoffTradingAgent 里也不存在)。

### 2. ⭐ 趋势 1 买 + 威科夫 Accumulation 双重确认 — 2 只票

- **阳光电源**: ⭐ 趋势 1 买 (缠论 2 中枢 + 中枢外 + 底背 + 分型确认) + Accumulation 弱信号 (SC 抛售高潮 + 9 sub_event) = 双重确认
- **特变电工**: ⭐ 趋势 1 买 + Accumulation/Accum_C (累积末段, b_test=9) = 双重确认
- **行动**: 这 2 只是**最强建仓信号** 🥇

### 3. Distribution 派发信号 — 0 只 (合理)
- 这一波半导体涨太凶, 0 bias_200>30% 派发
- 18 只票全部 Accumulation 或 Markup, 0 Distribution
- **实盘数据** 跟 v4 算法一致

### 4. 江西铜业/立讯精密 v4 重新分类
- v3 判派发 100% 是因为 v3 算法包含 BC/UT sub-event 弱信号
- v4 严格化后, 这 2 只不算派发, 而是 Accumulation 弱信号 (横盘吸筹)
- **跟 WyckoffTradingAgent 严格定义一致**

### 5. 京东方 A 触发 UT/UTAD 但不应急于加仓
- -27% 浮亏但判 Markup (弱信号)
- 原因: 上升趋势但 ma50<ma200, 历史顶部痕迹
- **结论**: 不应急于加仓, 等 ⭐ 趋势 1 买 + 威科夫 Accumulation 双重确认

## 12 种 sub-event 触发率 (跨 58 只 × 250 根)

| sub-event | 每只/年 | 含义 |
|---|---|---|
| SOS | 0.7 | 强势信号 (Markup 起点) |
| UT | 0.6 | 上探 (顶部假突破) |
| LPSY | 0.6 | 最后供给 (反弹结束) |
| ST | 0.5 | 二次测试 (Accumulation 末段) |
| EVR | 0.5 | 巨量+滞涨 (主力意图) |
| BC | 0.4 | 买入高潮 (Distribution 起点) |
| SOW | 0.3 | 弱势信号 (跌势) |
| UTAD | 0.3 | 派发后上探 (Distribution 末段) |
| SC | 0.3 | 抛售高潮 (Accumulation 起点) |
| AR | 0.3 | 自动反弹 (Accumulation 中段) |
| PSY | 0.0 | 初步支撑 (稀有) |
| Spring | 0.0 | 弹簧 (58 只成长股 1 年内无, 历史上稀有) |

## 持仓建议 (基于 v4 完整算法)

| 票 | 建议 | 理由 |
|---|---|---|
| 阳光电源 | **🥇 加仓** | ⭐ 趋势 1 买 + Accumulation + 9 sub_event, 双重确认 |
| 特变电工 | **🥈 持有** | ⭐ 趋势 1 买 + Accumulation/Accum_C, 趋势延续 |
| 金钼股份 | **🟡 持有观察** | Accumulation 弱信号, bias_200=+22% 高位, 浮盈 26% 减仓 1/3 |
| 江西铜业 | **🟡 持有观察** | Accumulation 弱信号, 浮亏 -5% 等确认 |
| 立讯精密 | **🟠 减仓 1/3** | 高位回撤 (bias_200=+15.4%), 警惕 |
| 京东方 A | **🔴 警惕** | -27% 浮亏 + 弱 Markup, **不应急于加仓** |

## 持仓总结 (v4 后)

- **🥇 强建仓 (1/6)**: 阳光电源 (⭐ 趋势 1 买 + Accumulation 双重确认)
- **🥈 持有 (1/6)**: 特变电工 (⭐ 趋势 1 买 + Accumulation/Accum_C)
- **🟡 持有观察 (2/6)**: 金钼股份 / 江西铜业
- **🟠 减仓 1/3 (1/6)**: 立讯精密
- **🔴 警惕 (1/6)**: 京东方 A

## v4 算法改进总结

1. **删除 D/E 阶段, 完全对齐 WyckoffTradingAgent 3 大阶段**:
   - Accumulation 累积 (3 子阶段 Accum_A/B/C)
   - Markup 主升浪
   - Distribution 派发
   - Spring 是 Markup 的 sub-event, 不是独立阶段
   - D 下跌/派发后下跌, WyckoffTradingAgent 不再分类

2. **3 大阶段判定严格化**:
   - Accumulation: AND 门 (base_low + MA gap + 量能)
   - Markup: 必须 slope_60 > 0 + bias_200 > -10% (排除跌势反弹)
   - Distribution: bias_200 > 30% + 3 日连续缩量 (强信号) + bias_200 > 15% + 缩量 + 派发 sub-event (弱信号)

3. **弱信号 fallback 避免 `?` 未分类**:
   - Accumulation: 横盘 (slope ±8%) + pos 25-75% + 累积 sub-event, 或 大跌后低位 + SC/AR/ST sub-event
   - Markup: slope_60 > 0 + bias_200 > -15% + ma50 ≥ ma200*0.95 (刚突破 MA200)

4. **Linter 验证**: 18/18 ✅ 100% 完整度

## 待优化

1. **Distribution 弱信号 pos 阈值** — 当前 18% 处于边缘, 中际旭创 bias_200=-6.4% 实际是被回撤拉低, 用全段均值算 ma200 会失真
2. **Markup 起点 fallback 阈值** — `ma50 >= ma200*0.95` 可改成 0.97 减少误判
3. **utad 5 条件对齐** — 当前只判断 UTAD sub-event 触发, 跟 WyckoffTradingAgent dist_upthrust_* 5 条件 (breakout 1% + close_back 0.3% + upper_shadow 35% + vol_ratio 1.5 + bias_200 ≥ 15) 还有差距
