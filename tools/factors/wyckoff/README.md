# 威科夫 Factor 算法详解

**所有代码都按 WyckoffTradingAgent 1:1 搬运**，3 层架构：9 detector → scanner → stage_factor。

> **最后更新**: 2026-08-03 (v5.10.42 + v3 周期配置)  
> **3 年回测 (57 只票，每天采样)**: 见文档末尾

---

## 目录

1. [架构概览](#1-架构概览)
2. [9 个 Sub-Event Detector](#2-9-个-sub-event-detector)
3. [3 大阶段判定 (Stage Factor)](#3-3-大阶段判定-stage-factor)
4. [周期自适应参数 (Period Config)](#4-周期自适应参数-period-config)
5. [3 年回测胜率汇总](#5-3-年回测胜率汇总)
6. [实战用法](#6-实战用法)

---

## 1. 架构概览

```
┌─────────────────────────────────────────────────────────┐
│                StageFactor (主入口)                      │
│  - 调 3 周期 (日/60m/周) StageFactor                   │
│  - 用 scoring_map 算 score:                              │
│    Accumulation=0.6 / Markup=1.0 / Distribution=-0.6    │
│  - 输出 stage + confidence + 9 sub_events                │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│              Scanner (scanner.py)                        │
│  - 扫整段 K 线 (n=250 默认)                            │
│  - 9 detector 各自扫一遍                                │
│  - 输出 list[{name, date, idx, price, vol}]            │
│  - 周期自适应: 60m/周 各自用不同阈值                     │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│              9 Detector (detectors/)                    │
│  - Spring / LPS / EVR / SOS / Compression               │
│  - TrendPullback / MarkupEntry                          │
│  - DistributionStart / UTAD                             │
│  - 每个 detector 用 helpers.py 的 13 个工具函数         │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│              Helpers (helpers.py)                       │
│  - 13 个内部工具: _find_range / _swing_values / etc.   │
│  - 跟 WyckoffTradingAgent 1:1 命名                       │
└─────────────────────────────────────────────────────────┘
```

**关键路径**:
- `stage_factor.py` → `scanner.py` → 9 `detectors/*.py` → `helpers.py`
- **所有公式都从 WyckoffTradingAgent/core/wyckoff_engine.py 1:1 搬运**

---

## 2. 9 个 Sub-Event Detector

### 2.1 Spring (终极震仓)

**触发**: 跌破前 60 日支撑位 + 收盘收回 + 放量 + 位置低

```python
# helpers.py / detectors/spring.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | 60 日内存在 trading range (区间宽度 ≤ 4 × ATR) | range_pct < 30-60% |
| 2 | 前 1 日 或 当日盘中跌破支撑位 | `low < support_level` |
| 3 | 收盘价**必须**在支撑位之上 | `close > support_level` |
| 4 | 当日量 ≥ 5 日均量 × 1.3 | vol_ratio = 1.3 |
| 5 | 当日量 ≥ 前 1 日量 × 1.15 | vol_ratio_prev = 1.15 |
| 6 | bias_200 ≤ 15% (不超高位) | max_bias = 15% |

**含义**: 主力在底部震仓洗盘——**抄底信号**

---

### 2.2 LPS (最后支撑点)

**触发**: 回踩 MA20 + 缩量 + MA20 上升 + 价格在区间下部

```python
# helpers.py / detectors/lps.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | 当前 bar 收盘 ≥ MA20 | `close >= MA20` |
| 2 | bias_200 ≤ 25% (不超) 且 ≥ -20% (不在主跌浪) | max_bias=25, min_bias=-20 |
| 3 | 价格在 60 日区间的**下 35%** (LPS 应在底部) | pos_pct ≤ 0.65 |
| 4 | MA20 上升 (5 日前 < 现在) | ma_rising |
| 5 | 近期 low 接近 MA20 (2% 内) | ma_tolerance = 2% |
| 6 | 缩量 (近 N 日最大量 < 60 日最大量 × 0.5) | vol_dry_ratio = 0.5 |

**含义**: 主力在累积末段缩量回踩——**加仓信号**

---

### 2.3 EVR (努力无结果)

**触发**: 高位放量 + 涨跌幅小 + 结构稳定

```python
# helpers.py / detectors/evr.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | bias_200 在 10-25% 之间 (高位) | min_bias=10, max_bias=25 |
| 2 | 当日量 ≥ 20 日均量 × 1.8 | vol_ratio = 1.8 |
| 3 | 当日涨跌幅在 ±2% 内 (努力但失败) | max_drop=2, max_rise=2 |
| 4 | 当前 close ≥ 3 日前 close × 0.98 (结构稳) | _evr_structure_ok |
| 5 | 确认日不破底 (简化后永远 True) | (2026-07-31 简化) |

**含义**: 主力在高位放量但不涨——**派发信号**

---

### 2.4 SOS (强势信号)

**触发**: 单日涨幅 + 放量突破

```python
# helpers.py / detectors/sos.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | bias_200 ≤ 25% (不超) | max_bias = 25% |
| 2 | **单日涨幅 ≥ 6%** (60m 用 2%, 周线用 8%) | pct_min |
| 3 | 当日量比 = vol / 前 60 日均量 (需 ≥ 3.0) | vol_ratio = 3.0 |
| 4 | 当日量 ≥ 60 日量的 95% 分位 | vol_quantile = 0.95 |
| 5 | 突破前 60 日新高 (或 MA50 上穿 MA200) | breakout_window = 60 |

**含义**: 主力放量突破——**主升浪信号**

---

### 2.5 Compression (压缩蓄势)

**触发**: ATR 收窄 + 缩量 + 方向向上

```python
# helpers.py / detectors/compression.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | 短期 MA ≥ 长期 MA (方向向上) | _compression_direction_ok |
| 2 | bias_200 ≤ 25% | max_bias = 25% |
| 3 | 缩量: 近 5 日均量 / 前 25 日均量 ≤ 0.6 | vol_decline_ratio = 0.6 |
| 4 | ATR 收窄: 近 5 日 ATR ≤ 20 日 ATR 的 20% 分位 | atr_quantile = 0.20 |

**含义**: 主力蓄势待发——**爆发前夜信号**

---

### 2.6 TrendPullback (趋势回踩)

**触发**: 上升趋势 + 缩量回调 + 当前反弹

```python
# helpers.py / detectors/trend_pullback.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | bias_200 ≤ 35% (顶部) | max_bias = 35% |
| 2 | 找 peak (近 10+1 根内最高) | lookback=10 |
| 3 | 回调幅度 5-20% (不能太少太多) | min=5, max=20 |
| 4 | 当前 close ≥ 前 1 根 close (反弹) | last_close > prev_close |
| 5 | 缩量: 回调段量 / 上涨段量 ≤ 阈值 (按市值/MA streak 调整) | vol_shrink_ratio = 0.6 |

**含义**: 上升趋势的回调企稳——**加仓信号**

---

### 2.7 MarkupEntry (主升浪起点)

**触发**: MA50/MA200 金叉 + 持续上方 + MA50 角度足够

```python
# detectors/markup_entry.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | MA50 > MA200 | `ma_short > ma_long` |
| 2 | 近 10 根内**曾**出现金叉 (前 1 根 ≤ MA200 ≤ 当前) | crossover_found |
| 3 | 最近 5 日 MA50 持续在 MA200 上方 | confirm_days = 5 |
| 4 | MA50 5 日变化率 ≥ 2% (角度够) | ma_angle_min = 2% |

**含义**: MA 金叉主升起点——**主升信号**

---

### 2.8 DistributionStart (派发起点)

**触发**: bias_200 > 30% + 缩量

```python
# detectors/distribution_start.py
```

| 步骤 | 条件 | 默认阈值 |
|---|---|---|
| 1 | bias_200 > 30% (高位) | high_thr = 30% |
| 2 | 近 3 日均量 < 60 日均量 × 50% (缩量) | vol_dry_ratio = 0.5 |

**含义**: 高位缩量——**主力在派发**

---

### 2.9 UTAD (派发后上探)

**触发**: 高位假突破 + 收回 + 放量

```python
# detectors/upthrust.py
```

| 条件 | 公式 | 默认阈值 |
|---|---|---|
| 1. 突破前高 | `(high / 前 60 日最高 - 1) × 100` | > **1.0%** |
| 2. 收盘收回 | `(前高 / close - 1) × 100` | > **0.3%** |
| 3. 上影线长 | `shadow / (high - low)` | > **0.35** |
| 4. 放量 | `当日量 / 前 21 日均量` | > **1.5** |
| 5. bias_200 | `(close / MA200 - 1) × 100` | > **15%** |

**含义**: 主力在高位假突破拉高出货——**强减仓信号**

---

## 3. 3 大阶段判定 (Stage Factor)

### 3.1 评分系统

```python
# tools/factors/wyckoff/stage_factor.py:209-216
score_map = {
    "Accumulation": 0.6,
    "Markup":       1.0,
    "Distribution": -0.6,
    "Markdown":     1.0,
    "?":            0.0,
}
```

### 3.2 Accumulation 评分

**触发条件** (3 条件全满足 → 基础分 +3):

```python
# stage_factor.py:218-256
```

| 子条件 | 条件 | 加分 |
|---|---|---|
| 基础 | `period_low > 0 + price ≤ period_low × 1.45` (底部) | +1 |
| 基础 | `MA50/MA200 gap ≤ 8%` (横盘) | +1 |
| 基础 | `近 20 日均量 / 前 100 日均量 < 0.75` (缩量) | +1 |
| 加分 | Spring / LPS / EVR 触发 | +2 |
| 加分 | `dev_ma20 < 5% AND dev_ma60 < 0` | +1 |

**阶段细分**:
- `b_test_count >= 3` → Accum_B
- `b_test_count >= 3 AND c_ok` → Accum_C
- 其他 → Accum_A

### 3.3 Markup 评分

| 子条件 | 条件 | 加分 |
|---|---|---|
| 金叉 | `MA50 上穿 MA200` + 持续上方 + MA gap > 0.5% | +3 |
| 加分 | SOS 触发 | +1 |
| 加分 | MA50 5 日角度 ≥ 2% | +1 |
| 弱 | `above_ma200 AND ma_gap > 0.5% AND is_uptrend` | +1 |
| 弱 | `dev_ma20 > 5% AND pos > 60% AND is_uptrend` | +1 |

### 3.4 Distribution 评分

| 子条件 | 条件 | 加分 |
|---|---|---|
| 强 | `dev_ma200 > 30% AND 近 3 日均量 < 60 日均量 × 50%` | +3 |
| 加分 | DistributionStart / UTAD 触发 | +1 |
| 加分 | EVR 触发 | +1 |
| 弱 | `dev_ma200 > 15% AND 缩量 AND 派发 sub_event` | +2 |

### 3.5 阶段判定 (主循环)

```python
# stage_factor.py:159-197
def _judge_full(self, ...):
    # 1. 取最后 250 根 K 线
    c = closes[-window:]
    
    # 2. 基础指标
    ma20 = mean(c[-20:])
    ma50 = mean(c[-50:])  # 等
    ma200 = mean(c[-200:]) if len >= 200 else ma60
    
    # 3. bias 偏离
    dev_ma20 = (c[-1] / ma20 - 1) * 100
    dev_ma200 = (c[-1] / ma200 - 1) * 100
    
    # 4. 扫 9 sub_event
    sub_events = scan_sub_events(c, h, l, v, rng, ...)
    
    # 5. 评分 3 阶段
    score_Accumulation = score_Markup = score_Distribution = 0
    # (按上述规则加分)
    
    # 6. 取 max
    scores = {'Accumulation': ..., 'Markup': ..., 'Distribution': ...}
    stage = max(scores, key=scores.get)
    confidence = int(max_score / total_score * 100)
```

---

## 4. 周期自适应参数 (Period Config)

**所有 detector 都按周期** (`daily` / `60m` / `weekly`) **用不同阈值**，从 `config/project.yaml` 读：

```yaml
# config/project.yaml (示例)
wyckoff_detectors:
  sos:
    pct_min:        { daily: 6.0, 60m: 2.0, weekly: 8.0 }  # 周期分层
    max_bias:       { daily: { default: 25, star: 40 }, ... }
  spring:
    vol_ratio:      { daily: 1.3, 60m: 1.2, weekly: 1.5 }
  evr:
    vol_ratio:      { daily: 1.8, 60m: 1.5, weekly: 2.0 }
  trend_pullback:
    min_pullback:   { daily: 5.0, 60m: 0.5, weekly: 8.0 }
    max_pullback:   { daily: 20.0, 60m: 8.0, weekly: 25.0 }
  utad:
    breakout_pct:   { daily: 1.0, 60m: 0.5, weekly: 1.5 }
    vol_ratio_thr:  { daily: 1.5, 60m: 1.3, weekly: 2.0 }
```

**周期适配表** (`scanner.py:140-145`):

| 周期 | MA 长窗 | bias_min | lookback |
|---|---|---|---|
| daily | 200 | 15% | 60 |
| 60m | 60 | 20% | 120 |
| weekly | 50 | 30% | 60 |

---

## 5. 3 年回测胜率汇总

**数据**: 57 只 watchlist 票 × 3 年（2023-08 ~ 2026-08）  
**采样**: 每天 1 个样本（不隔 5 天）  
**去重策略**: 同月 1 次（独立事件）  
**未来收益**: 5d / 10d / 20d 涨跌幅

### 5.1 全阶段胜率（每天采样，同月去重）

| 组合 | 样本 | 5d 胜率 | 5d 均 | 10d 胜率 | 10d 均 | 20d 胜率 | 20d 均 |
|---|---|---|---|---|---|---|---|
| Markup/Markup | 10912 | 52% | +1.81% | 53% | +3.16% | 53% | +5.52% |
| Accumulation/Markup | 4501 | 54% | +1.31% | 57% | +2.46% | 60% | +5.56% |
| Accumulation/Accumulation | 3614 | 54% | +1.31% | 57% | +2.42% | 61% | +4.66% |
| Markup/Accumulation | 1529 | 52% | +0.78% | 53% | +1.75% | 54% | +3.18% |
| **Distribution/Markup** | **529** | **45%** | **-0.44%** | 47% | +0.45% | 56% | +3.26% |

### 5.2 加 UTAD 后胜率

| 组合 | 样本 | 5d 胜率 | 10d 胜率 | 20d 胜率 | 20d 均 |
|---|---|---|---|---|---|
| **Accumulation/Accumulation+UTAD** | **17** | 59% | **71%** | **76%** | +5.42% |
| **Accumulation/Markup+UTAD** | **111** | **62%** | 60% | 60% | +6.96% |
| Markup/Markup+UTAD | 485 | 45% | 47% | 50% | +5.64% |
| Markup/Markup+UTAD (密集) | 13 | 54% | 38% | 69% | +20.66% |
| **Distribution/Markup+UTAD** | **19** | **37%** | **37%** | 58% | +1.61% |
| Markup/Markup+独立 UTAD (7d) | 167 | **38%** | 40% | 44% | +2.70% |

### 5.3 强信号 Top 5 (加仓)

| 排名 | 组合 | n | 5d 胜率 | 10d 胜率 | 20d 胜率 | 20d 均 |
|---|---|---|---|---|---|---|
| 🥇 | **Accumulation/Accumulation+UTAD** | 17 | 59% | **71%** | **76%** | +5.42% |
| 🥈 | **Accumulation/Markup+UTAD** | 111 | **62%** | 60% | 60% | **+6.96%** |
| 🥉 | Markup/Markup+密集 UTAD | 13 | 54% | 38% | 69% | +20.66% |
| 4 | Markup/Accumulation+独立 UTAD | 19 | 47% | 53% | 63% | +11.01% |
| 5 | Markup/Markup (无 UTAD) | 694 | 50% | 50% | 55% | +5.19% |

### 5.4 强信号 Top 3 (减仓)

| 排名 | 组合 | n | 5d 跌率 | 5d 跌均 | 10d 跌率 | 10d 跌均 |
|---|---|---|---|---|---|---|
| 🥇 | **Distribution/Markup+UTAD** | 19 | **63%** | **-4.18%** | 63% | -1.47% |
| 🥈 | **派发/任意 + UTAD** | 463 | **54%** | -0.53% | 54% | +0.51% |
| 🥉 | Markup/Markup + 独立 UTAD | 167 | 5d 跌率 60% (胜率 38%) | -1.20% | 40% | +0.20% |

### 5.5 关键发现

1. **3 周期 stage 单看区分涨/跌弱**（胜率 50-55%）
2. **派发（Distribution）真信号**：5d 跌率 59%（日线）/ 64%（周线）
3. **双周期 Markup/Accumulation+UTAD 是最强加仓**（20d 60-76% 胜率）
4. **Distribution+UTAD 是最强减仓**（5d 跌率 63% / -4.18%）
5. **同月去重 + 7 天独立 UTAD = 真正独立信号**（密集 UTAD 接近随机）
6. **UTAD 单独用不是强看跌信号**（5d 跌率 48-50%）—— 必须结合派发阶段

---

## 6. 实战用法

### 6.1 加仓信号

| 当前组合 | 操作 | 依据 |
|---|---|---|
| **Accumulation/Accumulation+UTAD** | 🟢 **加仓** | 20d 76% 胜率 |
| **Accumulation/Markup+UTAD** | 🟢 加仓 | 5d 62% / 20d 60% |
| Accumulation/Accumulation (无) | ⚪ 关注 | 20d 61% |
| Accumulation/Markup (无) | ⚪ 关注 | 20d 60% |
| Markup/Markup | ⚪ 持平 | 5d 52% |

### 6.2 减仓信号

| 当前组合 | 操作 | 依据 |
|---|---|---|
| **Distribution/Markup+UTAD** | 🔴 **强减仓** | 5d 跌率 63% / -4.18% |
| **派发 + UTAD** | 🔴 减仓 | 5d 跌率 54% |
| Markup/Markup+独立 UTAD | 🔴 减仓 | 5d 跌率 60% |
| **Distribution (任意)** | 🔴 减仓 | 5d 跌率 59% |

### 6.3 单 UTAD 用法

**5d 跌率 48-50%** —— **不是强信号**

**UTAD 必须结合**:
- ✅ 派发阶段（Distribution）—— 强减仓
- ✅ Accumulation + UTAD —— 强加仓
- ❌ 单独 UTAD —— 接近随机

### 6.4 同月去重 + 7 天独立

**实战做法**:
1. 同一只票同月内多次 UTAD → **只算 1 次**
2. 7 天内连续触发 → **只算 1 次**
3. **独立 UTAD = 真信号**（胜率高于密集 UTAD）

---

## 7. 文件清单

```
tools/factors/wyckoff/
├── __init__.py
├── README.md          (本文件)
├── helpers.py         (13 个工具函数, WyckoffTradingAgent 1:1)
├── stage_factor.py    (主入口: WyckoffStageFactor, 3 阶段判定)
└── detectors/
    ├── __init__.py
    ├── scanner.py             (扫整段 K 线, 调 9 detector)
    ├── spring.py              (Spring 终极震仓)
    ├── lps.py                 (LPS 最后支撑点)
    ├── evr.py                 (EVR 努力无结果)
    ├── sos.py                 (SOS 强势信号)
    ├── compression.py         (Compression 压缩蓄势)
    ├── trend_pullback.py      (TrendPullback 趋势回踩)
    ├── markup_entry.py        (MarkupEntry 主升浪起点)
    ├── distribution_start.py  (DistributionStart 派发起点)
    └── upthrust.py            (UTAD 派发后上探)
```

---

## 8. 相关代码

- **阶段判定** (AnalysisEngine): `tools/analysis/analysis_engine.py:200-264`
- **3 周期 SMC 因子** (类似威科夫): `tools/factors/smc/`
- **3 周期缠论因子** (背驰/中枢/买卖点): `tools/factors/chan/`
- **Stage 调用方**: `tools/analysis/factor_matrix.py` (因子 × 3 周期矩阵, 2026-08-17 改名前 `five_method_matrix.py`)

---

**版本**: v5.10.42 + 3 年回测 (2026-08-03)
