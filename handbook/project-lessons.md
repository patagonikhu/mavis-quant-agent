# Mavis Quant Agent — 项目专属教训存档

> 2026-11-04 从 agent memory 迁入。**这些是 mavis-quant-agent 项目专属知识**, 
> 只在本仓库有效, 迁移出 agent memory 层是为了不再每次会话强制注入 (76.9KB → 见 MEMORY.md).
> 用法: 提到具体话题时 grep 本文件; 详细背景见 `docs/eval/*.txt` 存档与 git log.

## 威科夫 / Markup / fallback 判定体系

### WyckoffTradingAgent 参考实现的 MA 判定 (2026-11-03 查证)
Type: fact

**用户曾称参考实现里有 "ma50 >= ma200*0.95" 判断 —— 实测不存在。** 全仓库 grep 确认:
- `0.95` 只出现在 3 个无关地方: `sos_vol_quantile` (量能分位数) / `a_share_entry_research.py` 的
  `CONFIRMED_SIGNAL_PRIOR` (信号置信度权重) / `accum_weight * 0.95` (权重系数)。
  **没有任何一处是 MA 容差。**
- 参考实现有**两套**独立的 stage 判定, 别混:
  1. `core/wyckoff_structure.py::_infer_stage` (L252-264) — **结构判定, 完全不看均线**。
     Markup 唯一判据是 `last_close >= tr.resistance` (突破 TradingRange 阻力)。
     其余: spring/lps → Accum_C; 靠近支撑下 35% → Accum_B; 否则 Accum_A。
  2. `core/wyckoff_engine.py::_detect_markup_entry` (L1682-1732, Layer 2.5) — **趋势判定, 看均线**。
     4 条全满足才算 Markup:
       ① `ma_short(MA50) > ma_long(MA200)` — **严格比较, 无 0.95 容差**
       ② 近 lookback(=10) 日内发生过 MA50 上穿 MA200
       ③ 最近 confirm_days(=5) 日 MA50 连续 > MA200
       ④ MA50 近 5 日角度 ≥ `markup_ma_angle_min` = 2.0%
     配置在 `FunnelConfig` L321-324。
  两套结果合并: `stage_map = accum_stage_map.copy()` 再 `markup_symbols` 覆盖成 "Markup" (L2851-2853)。

**关键差异: 参考实现的 MA 判定是"独立的一层" (Layer 2.5), 不是 fallback。**
它跑在已过 L1/L2/L3 漏斗的池子上, 本身就是强过滤 —— 而我们的 fallback2 是"主路径分数全 0 时
的兜底", 两者地位完全不同, 不能直接照抄阈值。

**apply when:** 讨论威科夫 stage 判定 / fallback2 加严 / 对齐参考实现时。
兜底路径比参考实现的主判定层宽松是结构性的, 不只是阈值问题。

### 威科夫 Markup 判定对齐 WTA + fallback2 加严 (2026-11-03 落地, commit 0a16616)
Type: fact

**做了什么:** 把 Markup 判定 1:1 对齐 WyckoffTradingAgent Layer 2.5 (`_detect_markup_entry`),
4 条判据全满足才算主升浪: ① MA50 > MA200(严格无容差) ② 近10日金叉 ③ 连续5日 MA50 > MA200 ④ MA50 近5日角度 ≥ 2.0%。

**关键: 修掉 3 处静默的 MA200 语义 bug** (注释写 MA200, 代码用别的):
- `above_ma200_sustained` 比较的是 `arrs['ma_long']` = **MA60** 而非 MA200 → 443 候选池 **134 只 (30%)** 判定错误 (含持仓 002415 海康威视)
- `golden_cross_recent` 用 `arrs['ma_window']` = **window(250) 均线** 而非真 200 日均线
- `detectors/markup_entry.py` 同样用 `ma_window`(250) → 688617 只上穿 250 日线、从未上穿真 200 日线却被判 True
**注意**: `arrs['ma_long']` 是 MA60 的 alias, `arrs['ma_window']` 是 250 日均线, `arrs['ma200']` 才是真 200 日均线。

**fallback2 加严**: 改前只要 `len(sub_events)>=1 + slope_60>5 + trend_30>5` 就判 Markup (完全不看 MA200/金叉),
改后额外要求通过 WTA 4 判据。实测 443 只生产候选池 **A→M 命中 10 → 4** (000333/300406/300488/603199)。
被砍的 9 只 MA50/MA200 全在 56.4%~94.7%, dev_ma200 全负, **无一站上 MA200**。

**验证手法 (可复用)**: 新增 `stage_factor.detect_markup_entry()` 与已有的 `detectors/markup_entry.py`
交叉验证 → **432/432 完全一致**, 证明 1:1 搬运无误。

**持仓影响**: 4 只原本靠宽松 fallback2 判 Markup 的被正确拦下 — 东方电缆(603606)/巴比食品(605338)/
我武生物(300357)/广东宏大(002683)。剩余 Markup 持仓 (001219/301101/601033/301109/002690) 均走主路径加分。

**apply when:** 讨论威科夫 stage / A→M 命中数 / MA200 相关判定时。
`markup_detail` 现含 `wta_*` 字段 (4 条判据布尔 + 实际值), 报告渲染「WTA Layer 2.5 判定」表。

### MA 容差口径统一到 helpers._ma50_reached_ma200 (2026-11-03, commit 40edb52)
Type: fact

**做了什么:** 把 `ma50 >= ma200 * 0.95` 提取成共享 helper, 消除全链路口径分歧。

```python
# tools/factors/wyckoff/helpers.py (Section 3 顶部)
MA50_MA200_TOLERANCE = 0.95

def _ma50_reached_ma200(ma50, ma200, tolerance=MA50_MA200_TOLERANCE) -> bool:
    if ma200 is None or ma50 is None or ma200 <= 0:
        return False
    return ma50 >= ma200 * tolerance
```

**统一了 6 处调用点** (原先散落且两套口径并存):
- `stage_factor.py` fallback1 (L349)
- `stage_factor.py::detect_markup_entry` 判据 ①②③ (原严格 `>`)
- `detectors/markup_entry.py` (原严格 `>`) —— **两条 Markup 路径原本容差不同**
- `report_renderer.py` fallback1 复算 + 展示 (2 处)

**为什么必要**: 同一只票不同路径给出矛盾结论。实测 300406: MA50/MA200 = 96.6%
已过 95% 线, 但 WTA 表显示「MA50 未站上 MA200」❌ —— 因为 WTA 用严格 `>`。

**⚠️ 口径语义 (别搞混)**:
- `_ma50_reached_ma200` = **放宽**口径 (MA50 追到年线 95% 即算数)
- WTA 参考实现 = **严格** `MA50 > MA200`
- 参考实现跑在已过 L1/L2/L3 漏斗的池子 (本身强过滤), 本项目跑在财务初筛池,
  两者地位不同, 阈值不可直接照抄。

**实测影响**: A→M 命中 4 → 4 不变 (4 只命中票全走主路径, 不经 WTA 门槛);
但报告里 300406 的 WTA 判据 ① 从假 ❌ 变 ✅。两条 Markup 路径交叉验证 432/432 一致。

**apply when:** 任何涉及「MA50 是否够到 MA200」的判定 —— 别再手写 `* 0.95`, 一律走 helper。

### MA50/MA200 容差调参: 命中数对容差**非单调** (2026-11-03, 最终 0.90, commit aebcb00)
Type: rule

**最终值: `helpers.MA50_MA200_TOLERANCE = 0.90`** (0.80 → 0.90, 提交 aebcb00;
0.95→0.80 见 commit 8c9ae5e)。6 处调用点全部跟随。

**完整调参轨迹 (443 只生产候选池实测)**:
| 容差 | A→M 命中 | M 总根数 |
|---|---|---|
| 0.95 | 4 (美的/九强/恒锋/九华) | 2289 |
| **0.90** | **3 (美的/恒锋/九华)** ← 当前 | 2606 |
| 0.85 | 2 (美的/恒锋) | 2929 |
| 0.80 | 3 (美的/恒锋/中国电研) | 3068 |
| 0.75 | 5 (多出西麦食品 净利yoy+81.9%, 也放回巴比食品 净利yoy+2.28%) | 3107 |

**⚠️ 两列方向相反 (核心洞察, 别混)**:
- **M 总根数随容差放宽单调增** (2289 → 3107) —— 放宽容差确实让更多 K 线被判 Markup
- **A→M 命中数非单调** —— V13 的 m_ratio 上限对 M 增量高度敏感:
  放宽容差 → 更多票在 40 根窗口内被判 M → m_ratio 撑破 10% → 被当"反复 M"踢掉

实测 300406 九强生物: tol=0.95 时 M数 3/40 (ratio 0.075) 过 V13;
tol=0.90 时 15/40 (ratio 0.375) 被踢。
另测 688128 中国电研在 tol=0.80 命中、0.90 掉出。

**另测: 股价/MA200 乖离门槛 (`ma_gap_pct_above`, 判 Markup 主路径用)** —
放宽 0.5% → 0% → -3% 也掉票 (4→3), 同理是 m_ratio 效应, 保持 0.5% 不动。

**两个不同的比值别搞混**:
- `ma50 >= ma200 * TOL` = **MA50/MA200** (均线追上年线的程度) ← 本次调的是这个
- `ma_gap_pct_above > 0.5` = **股价/MA200** (股价站上年线的乖离)

**apply when:** 调容差前必须先扫全档位看**两条曲线** (M 根数 + A→M 命中数) 而非单点;
命中数变少不一定是变严, 也可能是 m_ratio 效应踢掉了本来该留的票。

### fallback2 加严方向搞反: 兜底路径不该带主路径级门槛 (2026-11-03 修, commit c5cf58a)
Type: rule

**用户指出: "fallback2 不对吧, 怎么比 WTA 还要严格呀" —— 完全正确, 上一版改错了。**

**根因:** fallback2 的定位是「主路径分数全 0 时的**兜底**」, 逻辑上必须比主路径宽松。
commit 0a16616 把 WTA Layer 2.5 的 4 条判据当 fallback2 门槛, 造成:
- **fallback2 变死代码**: 实测放行 **0 只** (入口条件过的 41 只全被 WTA 拦下)
- **语义倒挂**: 主路径宽松分支只要 `股价 > MA200`, fallback2 却要求
  `MA50够到MA200 + 金叉 + 连续5日 + 角度2%` —— 兜底比主路径还严

**根本差异**: 参考实现的 Markup 判定是**独立一层** (L2.5, 跑在已过 L1/L2/L3 漏斗的池子上),
我们的是「主路径 → fallback 兜底」串行链。**不能把独立层的门槛直接套到兜底路径上。**

**改法 (只修真 bug, 保持兜底性质)**: fallback2 判定 `slope_60 > -20` → `slope_60 > 5`
- 修掉的真 bug: 改前 fallback2 完全不看 60 日方向, 一只「60日跌15%、只是近30日反弹」的票
  也被判主升浪 (实测原 10 只命中里 4 只 60 日斜率为负)
- WTA 4 判据降级为**独立参考信号** (`markup_detail.wta_*` / 报告表格), 不参与判定

**三方案实测 (443 只候选池)**:
| 方案 | fallback2 条件 | 命中 |
|---|---|---|
| A 还原 | 入口 (无条件) | 10 (含 4 只斜率为负) |
| **C 当前** | **入口 + slope_60 > 5** | **6** |
| B 加 WTA | 入口 + WTA 4 判据 | 3 (fallback2 死代码) |

**apply when:** 任何"给 fallback / 兜底路径加严"的需求 —— 先确认**加严后的条件仍比主路径宽松**,
否则兜底就失去意义 (要么放行 0 只, 要么比主路径还严 = 语义倒挂)。
同理: 参考实现的**独立判定层** (跑在已过滤池子上) 不能直接套到我们的**兜底路径** (跑在全池子上)。

### fallback1/fallback2 的真实出处 (2026-11-03 从 .git/filter-repo 挖出)
Type: fact

**8-20 的 `init` 提交不是源头** —— 它是 filter-repo 改写后的结果。
原始 469 commits 完整保留在 `.git/filter-repo/fast-export.original` (init 提交信息里写了
"重置整个 commit history, force-push 后 remote 只看 1 init commit")。
**查历史方法**: `grep -n "关键词" .git/filter-repo/fast-export.original` (fast-export 是文本格式,
每条 commit 有 `data <unix_ts>` / `author` / commit message)。

**fallback1 诞生: 2026-07-25 16:04, commit "refactor: 威科夫 v4 完全对齐 WyckoffTradingAgent 3 大阶段"**
- 提交信息明写 "整体对齐度: v3 85% → v4 95%"
- Markup 判定分 3 档 (强信号 / 弱信号 / **弱 fallback**):
  - 强信号: MA50/MA200 金叉 + 持续 5 日 + gap > 0.5% + 上升趋势
  - 弱信号: 在 MA200 上方 + gap > 0.5% + 上升趋势
  - **弱 fallback: slope_60 > 0 + bias_200 > -15% + ma50 ≥ ma200*0.95** ← 就是现在的 fallback1
- **注意: v4 对齐的只是 3 大阶段框架, 没有"兜底路径"这个概念** —— 参考实现本身没有 fallback。

**fallback2 诞生: 2026-07-25 16:20, commit "fix: 周线威科夫 0 个 `?` 阶段 (18/18 全部识别)"**
- 起因是**用户报告 bug**: 特变电工周线 stage=? conf=0% (子事件 ['LPSY'] 周线触发率 < 1/只/年)
- 修法加了两个 fallback:
  - 弱信号 fallback 3: 大跌后低位 + 任何累积/派发类 sub_event → Accumulation
  - **弱信号 fallback 4 (避免 `?`): 有 sub_event + 明确趋势 (slope > 5 → Markup, slope < -5 → Accumulation)** ← 就是现在的 fallback2
- **fallback2 的设计动机是"消灭 ? 状态", 不是"识别主升浪"** —— 这是理解它的关键。

**2026-08-03 v5.10.66 加守卫**: "fallback Markup 也加 trend_30>-10 和 slope_60>-20 守卫
→ 防止大跌后死猫反弹触发 fallback Markup" —— slope_60 > -20 这个松阈值就是这么来的。

**apply when:** 讨论 fallback 语义时 —— fallback2 本质是**兜底消除"数据不足"**，
不是"主升浪的宽松定义"。所以它比 fallback1 松是**符合设计意图**的,
但今天 (commit c5cf58a) 收紧 slope_60 > 5 后它变成了"趋势确认路径", 语义已变。
如果要恢复原语义, 应该是恢复成 slope_60 > -20 或保留但重命名。

### fallback2 语义之争实测: 对 A→M 命中集零影响 (2026-11-03, commit a743e1c)
Type: fact

**实测 462 只生产候选池 → 恢复原语义 (`slope_60 > -20`) vs 当前 (`slope_60 > 5`),
A→M 命中集合完全相同 (6 只: 000333/002683/300488/603199/605338/688128)。**

**原因: fallback1 在串行链里优先于 fallback2。** 分歧区 (-20 < slope_60 <= 5) 的票
(000333 美的 slope=+0.07 / 603199 九华 slope=+4.76) 全部走 **fallback1**,
根本轮不到 fallback2 判定。

→ **fallback2 的 slope 门槛怎么改, 都不会影响 /t-wyckoff-a2m 的命中数。**
  之前 c5cf58a 那次「收紧」对 A→M 扫描其实没有实质效果 (价值在于修正报告叙述 + 挡住 4 只历史误判)。

**4 只被挡住的「60日跌15%死猫反弹」**: 000423 东阿阿胶 -10.18% / 002946 新乳业 -13.38%
/ 300357 我武生物 -8.96% / 688615 合合信息 -15.38%。

### fallback2 的 Accumulation 分支是死代码 (2026-11-03 修, commit a743e1c)
Type: bug

**原始设计** (2026-07-25 commit「fix: 周线威科夫 0 个 `?` 阶段」) 原文:
> "弱信号 fallback 4 (避免 `?`): 有 sub_event + 明确趋势
>  (slope > 5 → Markup, slope < -5 → Accumulation)"

→ Markup/Accumulation 是**互斥双分支, 靠 slope 正负号区分**,
入口条件只有「有 sub_event + |slope| > 5」, **没有 trend_30 门槛**。

**Bug: 入口条件多了 `trend_30 > 5`**, 导致 `elif trend_30 < -5` (判 Accumulation)
**永远进不去** —— 这条分支从 8 月起就是死代码。

**修法:**
```python
fb2_base = (len(sub_events) >= 1 and (slope_60 > 5 or slope_60 < -5))
# Markup       : slope_60 > 5 and trend_30 > 5   (双向上)
# Accumulation : slope_60 < -5 and trend_30 < -5 (双向下 = 真跌势)
```
渲染器 `report_renderer.py` 的 fallback2 表同步 (Accumulation 分支条件加 `slope_60 < -5`)。

**apply when:** 任何"某条 if/elif 分支从没被执行过"的排查 — 先看**入口条件是否已经把
该分支的前提排除了**。分支写成互斥双分支时, 入口不能带其中任一分支的前提。

### fallback1 的完整来源与条件演变 (2026-11-03 考古定案)
Type: fact

**fallback1 诞生: 2026-07-25 16:04, commit「refactor: 威科夫 v4 完全对齐 WyckoffTradingAgent 3 大阶段」**
提交信息明写 "整体对齐度: v3 85% → v4 95%"。
**引入时的原始代码** (blob 9c944a9a, tools/wyckoff.py L488):
```python
elif slope_60 > 0 and dev_ma200 > -15 and ma50 >= ma200 * 0.95:
    # 弱 fallback: 上升趋势 + 在 MA200 附近, 判 Markup 起点
    stage = 'Markup'
```

**⚠️ `ma50 >= ma200 * 0.95` 不是参考实现里的** —— 已复查参考实现:
`grep -rn "0\.95" core/` 只命中 3 处无关地方
(`sos_vol_quantile` 量能分位数 / `a_share_entry_research.CONFIRMED_SIGNAL_PRIOR` 置信度权重 /
`accum_weight * 0.95` 权重系数), **没有一处是 MA 容差**。
参考实现 `_detect_markup_entry` 用的是**严格 `MA50 > MA200`**, 无容差。
→ 这个 0.95 是**我们自己拍的**经验值, 提交标题「完全对齐」名不副实 (只对齐了 3 大阶段框架)。

**条件演变 (4 步)**:
| 时间 | 条件 | 依据 |
|---|---|---|
| 2026-07-25 | `slope_60>0 + dev_ma200>-15 + ma50≥ma200*0.95` | v4 重构, "在 MA200 附近判 Markup 起点" |
| 2026-08-03 v5.10.66 | `+ trend_30 > -10` | commit "威科夫阶段误判 + LPS 假信号": "fallback Markup 也加 trend_30>-10 和 slope_60>-20 守卫 → 防止大跌后死猫反弹触发 fallback Markup" |
| 2026-11-03 (40edb52) | `0.95` → `MA50_MA200_TOLERANCE` (现 0.90) | 口径统一, 数值可调 |
| 2026-11-03 (a743e1c) | 现状 | fallback2 Accumulation 分支修复, fallback1 不变 |

**同 commit 还把 fallback2 入口写成 `len(sub_events)>=1 and (slope_60>5 or slope_60<-5)`** —
即 fallback1 与 fallback2 在 v4/v5.10.66 时代是**紧挨着的两条 elif**, fallback1 在前。

**apply when:** 讨论 fallback1 语义 / MA 容差调参 / 「对齐参考实现」时。
记住: fallback1 的 4 项条件里, **3 项是我们自创的** (slope_60>0, dev_ma200>-15, 0.95容差),
只有 trend_30>-10 是 8-03 补的守卫。参考实现没有「弱 fallback」这个概念。

### Markup 判定准确度实测 (2026-11-03, 443 只生产池, 0 回测)
Type: fact

**标签**: 判定日**过去** N 日涨幅 ≥ +3% (无未来函数; 数据截止 20261102)。
**随机基准线**: 全池正样本占比 **41.4%** (10日) / 42.1% (5日) / 44.2% (20日)。

| 路径 | 判定数 | 准确率(10日) | 召回率 | F1 |
|---|---|---|---|---|
| main | 4 | 25.0% | 1.2% | 2.4% |
| fallback1 | 45 | 57.8% | 32.5% | 41.6% |
| **fallback2** | 8 | **87.5%** | 8.8% | 15.9% |
| fb1+fb2 | 53 | 62.3% | 41.2% | 49.6% |
| 现有全部Markup | 57 | 59.6% | 42.5% | 49.6% |
| struct (WTA结构路径) | 34 | 67.6% | 28.7% | 40.4% |
| **struct∩trend** | 18 | **83.3%** | 18.8% | 30.6% |

**结论**: 现有 Markup 判定整体准确率 59.6% vs 基准 41.4% → **有信息量但不强**。
fallback2 (8 只) 和 struct∩trend (18 只) 准确率最高 (83-88%), 但**召回率极低** (9-19%)。
→ **fallback2 是"少而准"**, fallback1 是"多而杂"。

**关键洞察 (推翻了我之前的错误解释)**:
fallback1 不是"WTA结构路径的替身"。实测两条路径**几乎不重叠**:
- 结构路径判 34 只, 现有 fallback 判 53 只, **交集仅 5 只**
- fallback 认出的票 (青岛食品 slope+76% / 郑中设计 +69% / 中国石油 +27%)
  结构路径大多判「无箱体」—— 因为**它们已经涨了 20-76%, 不在箱体里**
→ fallback1 的真实功能 = **识别"已经启动的主升浪"**, 而结构路径 = **识别"刚突破的启动点"**
→ 两者是**时间轴上的互补**, 不是替代关系

**WTA `_infer_stage` 单独用有 47% 假突破** (34 只里 16 只趋势在跌) —— 因为它的 Markup 判据
只是 `last_close >= tr.resistance`, **不要求放量/大阳线**。对比 WTA 自己的 SOS 触发器:
`structure_breakout`(接近阻力) AND `enough_push`(当日涨幅≥6%) AND `enough_volume`(量比≥2.0)。
→ 移植必须用 SOS 口径, 不能直接搬 `_infer_stage`。

**持仓 11 只验证**: 7 只判 Markup 的**全部近 20 日上涨** (+7.7%~+69.6%);
2 只判 `?` (我武生物 trend30+10.9% / 基蛋生物 -14.5%) 方向存疑;
WTA 4 判据在持仓里**全 ❌** (MA50/MA200 78.7%~103.9% 都没稳定站上年线)。

**apply when:** 讨论 Markup 判定准确度 / fallback1 vs fallback2 取舍 / 结构路径是否值得接。
评估脚本: `tools/batch/_eval_markup_accuracy.py` (全池) + `_eval_holdings_markup.py` (持仓)
+ `_eval_infer_stage.py` (结构路径对照)。结果存档 `docs/eval/markup-accuracy-20261103.txt`。
**注意**: 曾把 `closes[-1+n]` 当成"未来 n 日" (实际是过去), 标签方向写反过 —— 用前 n 日涨幅当标签时
必须用 `closes[-1-n]`。

### Markup 主路径只占 0.93% — 93% 的 Markup 判定靠 fallback 兜 (2026-11-03 实测)
Type: fact

**443 只生产候选池 (432 只有效), 最后一根 K 线:**

| stage | 只数 | 占比 |
|---|---|---|
| Accumulation | 341 | 78.94% |
| Markup | 57 | 13.19% |
| ? | 34 | 7.87% |

**Markup 内部通道拆分:**
| 通道 | 只数 | 占全池 | 占Markup |
|---|---|---|---|
| **main(主路径/默认判定)** | **4** | **0.93%** | **7.0%** |
| fallback1 | 45 | 10.42% | **78.9%** |
| fallback2 | 8 | 1.85% | 14.0% |

→ **「默认判定」几乎不触发, Markup 判定 93% 是 fallback 兜出来的。**

**主路径 5 档 (render 已按这个渲染, commit c980052):**
① 强信号 `golden_cross_recent AND above_ma200_sustained AND gap>0.5 AND is_uptrend`
② 弱信号 `above_ma200 AND gap>0.5 AND is_uptrend` (stage_factor L308)
③ 加分线 `dev_ma20>5 AND pos_pct>60 AND is_uptrend` (stage_factor L310)
④ fallback1 ④ 条件
⑤ fallback2 sub-event + slope>5 + trend_30>5

**主路径那 4 只**: 300864 南大环境 / 301109 军信股份 / 301603 乔锋智能 / 688628 优利德
→ 3 只走②弱信号, 1 只走③加分线。**全部 WTA 4 判据 ❌, 全部金叉 ❌**。

**apply when:** 讨论 Markup 判定 / 主路径有效性 / fallback 占比时。
评估脚本 `tools/batch/_eval_markup_share.py`。

### fallback1 的 trend_30 门槛: 实测数据 + 已定案为 -5 (2026-11-03 实测 / 2026-11-05 定案, commit d56c772)
Type: fact

**✅ 已落地**: 门槛 -10 → **-5**, 抽成常量 `FB1_TREND30_MIN = -5.0`
(`tools/factors/wyckoff/helpers.py`), 生产判定 + 报告渲染 + 8 个评估脚本三方共用。
**不要再提议调回 -10, 不要再在脚本里写死 -10。** 下面是当初的调参依据。

**⚠️ 之前记的 "12 只里 11 只近月在跌" 是错的** —— 生产候选池实测是 **63 只走 fb1, 其中 19 只 (30%) trend_30 < 0**。

**门槛定义**: `stage_factor.py:385` 的 fallback1 第 4 条件。
`trend_30` = `(closes[i]/closes[i-31]-1)*100` (**31 根**, 不是 30) → 语义 "近 31 根最多跌 10%"。
**来源**: 2026-08-03 v5.10.66 commit, 原文 "fallback Markup 也加 trend_30>-10 ... 防止大跌后死猫反弹",
只验了 3 只票 (300308/300274/002371) → **-10 是拍脑袋的**, 从未量化过放行集合。

**交叉验证**: 复刻 fb1 4 条件 vs 生产 `wyckoff_judge` → **432/432 一致**, 集合完全相同 (63 只)。

**反事实 (标签 = 判定日过去 10 日涨幅 >=+3%, 无未来函数, 基准 19.2%)**:
| 门槛 | 放行 | 准确率(10日) | 召回率 | F1 |
|---|---|---|---|---|
| >-10 (当前) | 63 | 60.3% | 45.8% | 52.1% |
| **>-5** | 54 | **70.4%** | 45.8% | **55.5%** ← F1 峰值 |
| >0 | 44 | 72.7% | 38.6% | 50.4% |
| >5 | 30 | 76.7% | 27.7% | 40.7% |

**分组对照 (当前 63 只)**: `trend_30<0` 19 只 → 准确率(10日) 31.6%, **20日仅 5.3% (≈随机, 纯噪声)**;
`trend_30>=0` 44 只 → 10日 72.7%, 20日 75.0%。**在跌那 19 只贡献 30% 放行量但几乎全是噪声**。

**被挡掉的 14 只**: 13 只挡对 (在跌), 1 只误挡 (002289 宇顺电子 trend_30=-12.03 但过去 10 日 +8.08%)。

**对 A→M 无影响**: 6 命中里 3 只走 fb1 (000333 +1.69 / 300488 +2.97 / 603199 +3.51, trend_30 全为正);
另 3 只走 fb2 (自带 trend_30>5, 不受影响)。

**2026-11-05 定案落地 (commit d56c772)**: 门槛改 -5, 符号 `FB1_TREND30_MIN`。
**落地后实测**: AST 插桩真走 fb1 5 只 → **3 只**; 掉出 002745 木林森 / 002246 北化股份
(slope_60 +3.95 / +0.10 未达 fb2 入口 → 落 '?', 是**换通道/掉队**不是淘汰);
A→M 命中 6 只集合**完全不变**; 持仓 11 只 stage/通道**无一只改变**;
全部 Markup F1 5 日 44.0→44.4% / 10 日 49.1→49.7% / 20 日 62.7→63.6% (判定数 58→56)。
⚠️ **改 fb1 门槛时必须重跑**: AST 通道校验 + A→M + 持仓 11 只 + 全量 lint/pytest。
存档 `docs/eval/fb1-trend30-eval-20261103.txt` (旧门槛研究, 保留)。

### ⚠️ 定案: fallback1/fallback2 实际命中 — AST 插桩 (2026-11-03, **取代下面被否掉的说法**)
Type: bug

**fb1 真触发 5 只, fb2 真触发 6 只, fb2_accum 0 只。** (AST 插桩捕获实际执行分支)

| 通道 | 只数 |
|---|---|
| main (主路径) | 373 |
| **fb1** | **5** |
| **fb2_markup** | **6** |
| fb2_base 进内部分支不满足→'?' | 32 |
| (else '?') | 16 |
| **fb2_accum** | **0** |

**fb1 那 5 只**: 300299 富春股份 / 301589 诺瓦星云 / 688097 博众精工 / 002430 杭氧股份 / 603209 兴通股份
**fb2 那 6 只**: 002683 广东宏大 / 301608 博实结 / 002956 西麦食品 / 603889 新澳股份 / 605338 巴比食品 / 688128 中国电研
**A→M 6 只**: 3 只走 fb2 (002683/605338/688128), 3 只走 main。

**⚠️⚠️ 教训 (最重要): 判定分支必须用 AST 插桩捕获, 不能读输出反推!**
我先前用「读函数返回的最终 scores, 再测 max(scores.values())>0」复刻通道, 得出
"fb1=0, fb2=0" 的**错误结论**。根因: 生产代码的 fallback 分支会**写回 scores**
(`stage_factor.py` L386/L393: `stage='Markup'; scores['Markup']=1`)
→ fallback 命中后返回的 scores 里 Markup=1, 但那是 **fallback 设的**。
→ 复刻因此把 fallback 票误判成 "走主路径"。
**输出里没有"判定当时"的信息, 只有插桩能给。**
脚本 `tools/batch/_verify_fb_channel_ast.py` (含硬断言: 插桩不生效就 SystemExit)。
→ 顺带: 之前两次用"复刻 + 交叉验证 432/432"抓不到这类 bug, 因为复刻的**输入**就错了。

**渲染器标签确实不可信 (这条成立)**:
`report_renderer.py` L451-453 显示侧反推 `_fb2 = trend_30>5 and slope_60>5` (不要求 sub_event/分数为 0)。
→ 标 "fallback1" 的 45 只里只有 5 只真走 fb1, **40 只是主路径误标**;
   标 "fallback2" 的 8 只里 6 只真走 fb2, 2 只 (301101 明月镜片/002690 美亚光电) 误标。

**Markup 57 只的主路径细分**: 42 弱信号 + 4 加分线 + 0 强信号 + 11 只其实是 fb1/fb2。

**修正**: "fb1/fb2 从未触发" **错**; "Markup 全部主路径打的" **错** (11 只是 fallback)。
但 "fb1/fb2 近乎死代码" 仍**接近成立**: 触发率 11/432 = 2.5%。

**apply when**: 讨论 fb1/fb2 命中数/准确率/调参前, 必须用 AST 插桩口径, 别用复刻。
建议 (A) stage_factor 输出加 `channel` 字段, 渲染器直接读 (根治);
(B) 渲染器改判「主路径 3 分支」, 剩余才是 fallback (改动小)。
存档 `docs/eval/fb2-channel-eval-20261103.txt`。

---

### MA200 基准三分裂定案 (2026-11-03, commit e6cbdbe)
Type: fact

**项目里「MA200」这个词历史上指三个不同的东西**, 报告里混用会自相矛盾:

| 口径 | 变量 | 谁在用 |
|---|---|---|
| **window(250) 均线** | `arrs['ma_window'][i]`, factor_values `"ma200"` | fallback1 / Accumulation MA 胶着 / bias_200 (Spring/LPS/EVR/SOS 共用) |
| **真 200 日均线** | `arrs['ma200'][i]`, factor_values `"ma200_true"` | Markup 主路径 (金叉 / above_ma200 / MA gap) / MarkupEntry detector |
| **gap 基准** | `factor_values "ma200_gap"` = 真 MA200, warm-up (i<199) 退回 ma60 | ma_gap_pct_above / above_ma200 |

**报告标签规则**: fallback1 / Accumulation MA 胶着 / bias_200 那几行写 **MA250**;
Markup 段金叉 / MA gap 写 **MA200**。

**已修的 4 处 bug** (e6cbdbe): 金叉行混用真 MA200 prev + 250 日均线 current;
`ma50_angle_5d` 分母用 `sum(closes[i-54:i-49])/5` 而非 `arrs['ma50'][i-5]`;
MA gap 基准用 250 均线; 金叉 lookback 文案写 5 日但代码是 10 日。

**apply when:** 任何碰 MA 基准的改动 — 先问「这行该写 MA200 还是 MA250」。
新增 factor 字段: `ma200_true` / `ma200_gap` / `ma50_5d_ago`。
`ma200_prev` 已修真 MA200 (i>=200, 否则退回 ma200_true)。

### 规则下沉: markup_entry_conditions() 是 4 条判据唯一事实源 (2026-11-03, commit e6cbdbe)
Type: pattern

**反模式**: 渲染器从 `factor_values` / `markup_detail` 里挑几个字段自己手算判据。
→ 渲染器复刻 = 第二份实现 = 必然漂移 (金叉行就曾混用两个 MA200 源)。

**正解**: `tools/factors/wyckoff/detectors/markup_entry.py::markup_entry_conditions()`
返回 4 条判据**逐条结果** dict (ma50_reached / golden_cross / above_5d / angle_ok /
hit / lookback / pairs / above_days / angle / ma50 / ma200 / ma50_5d_ago / ma50_thr)。
`detect_markup_entry()` 只是它的薄封装 (返回 cond["hit"])。

渲染器 (`report_renderer.py::_wy_sub_events_md`) 直接 `from ... import markup_entry_conditions`
调用, **不手算**。

**apply when:** 任何"报告里要展示某判据是否满足"的需求 → 先在 detector 里加字段,
再让渲染器读。不要在渲染器里写 `if a > b and c < d`。
等价的重构验证脚本: `tools/batch/_check_markup_entry_refactor` (逐根 176494 次对照)。

### 周期口径真源: 威科夫日线/周线 (2026-11-05 修 BUG 固化)
Type: pattern

**报告「周线分析」段曾错显日线数据** (2026-11-05 修掉, commit 77016e3)。
根因: analysis dict 里**从来没有** `wyckoff_weekly` 顶层 key (它只是 RawContext 上的字段,
没进 raw), 所以 `s5.get("wyckoff_weekly") or s5.get("wyckoff")` 永远 fallback 到**日线**。
影响: 67 只样本 47% stage 不一致、53% strength 不一致 (600763 日线 Markup/extra_only,
周线真值 Accumulation/weak)。

**唯一真源**:
- 日线判定 = `raw["wyckoff"]`
- 周线判定 = `raw["wyckoff"]["3period"]["weekly"]`
- 周线 sub_events = `raw["wyckoff"]["sub_events_by_period"]["weekly"]`
- 周线 bars = `ctx.weekly` → `RenderData.weekly_kline`

**禁止写** `s5.get("wyckoff_weekly")` —— 该 key 不存在, 写了等于静默退回日线。
`analysis_result_signals.py` 是对的样板 (用 `p3 = wy_raw.get("3period")`)。

**apply when:** 碰报告里任何 "周线" 段的威科夫/阶段/强度取值时。
**回归防线**: `bash tools/with_venv.sh python -m tools.batch._verify_weekly_period`
(报告 md ↔ 生产值双向对账, 日/周各查 stage + 判定强度)。

### ⚠️ 陷阱: WyckoffStrategy.analyze() 是死代码, 口径跟生产不同
Type: bug

`WyckoffStrategy.analyze(ctx)` **没有任何生产调用** —— 全链路 (report_renderer /
t_analyze_one / t_analyze_all / factor_5y / batch_matrix / etf_macd_scan /
caches/analysis) 都走 `AnalysisEngine.analyze_history()`。

两者子事件扫描口径不同:
- `analyze_history` 传 `precomputed_arrs` → detector 里 ma200 取 `arrs['ma_window']` (250 均线)
- `analyze()` 不传 → detector 内部 `sum(c[i-200:i])/200` (真 MA200)
→ 实测 67 只里 **1 只日线 + 8 只周线** 判定不同 (死路径更宽松, 多认出 EVR)。
例如 301109 周线: 死路径 Accumulation/weak, 生产 `?`/none。

**apply when:** 量化/验证威科夫判定时**必须走 analyze_history**。
我用 `WyckoffStrategy().analyze()` 写探针, 拿到的周线数是错的口径, 差点把错误数字
写进结论 (已修正)。这类"顺手调个类直接跑"的脚本, 动手前先 grep 确认它是否在生产链路上。

### 持仓威科夫事件是否"存在"先 grep 后回答 (2026-10-08 固化)
Type: rule

**反例 (今天 600089 之后又翻车):**
- 用户问"持仓怎么最近一个威科夫子事件都没有"
- LLM 凭印象回答"持仓内威科夫事件没触发" — 这是错的
- 实际数据: 23 只持仓, 20+ 只持仓里 Spring×3, UTAD×4~14, LPS×4~18 满报告
- 用户问的"最近一个都没有"是 "今天新增触发", 不是 "历史结构"

**正确答案:**
- "持仓里**今天**有没有出现 Spring/UTAD/JAC 的**当日新触发**" — 今天缺,因为大盘阴跌没反转
- "持仓的**历史威科夫事件累计**" — 大量都在,详报里全有

**铁律:** 任何关于"持仓里 X 事件有没有" / "信号触发没触发" 类问题, 先 grep 数据再回答. 不要凭"今天底部信号列表里没有 X 就说没有". 

```
bash tools/with_venv.sh python -c "
import re, os
files = ...
for f in sorted(files):
    ...
    found = []
    for kw in [Spring, UTAD, LPS, JAC, ...]:
        n = re.findall(kw, content)
        if n: found.append(f'{kw}×{n}')
    print(code, name, found)
"
```

apply when: 用户问任何 "持仓/板块里事件/信号/触发是否存在" 类问题. 不凭印象, 先 grep.

## 数据同步 / tushare / parquet 落盘

### tushare 限频实测: 账号 ≥200/分, doc_id=328 的 "30/分" 对本账号不成立 (2026-11-03)
Type: fact

**实测 680 次调用, 峰值 196/分, 零限频。** 探针 `tools/batch/_probe_tushare_limit{2,3,4}.py` (只读接口, 撞限频只报错不损数据)。

| 轮次 | 目标速率 | 调用数 | 成功 | 空 | 限频 |
|---|---|---|---|---|---|
| 1 | 120/分 | 120 | 96 | 24 | ❌ |
| 2 | 196/分 | 300 | 270 | 30 | ❌ |
| 3 | 142/分 | 260 | 260 | 0 | ❌ |

**官方文档自相矛盾 (必须实测, 不能查文档下结论)**:
- `doc_id=328` (stk_factor_pro 接口页): "5000积分每分钟可以请求**30**次, 8000积分以上每分钟500次"
- `doc_id=290` (积分频次对应表): 2000以上 → **200**次/分; 5000以上 → 500次/分
- → 本账号按 doc_id=290 的 **200/分** 档, **不是** doc_id=328 说的 30/分

**返空 ≠ 限流 (重要)**:
- 0 行响应 **100% 由日历决定** (非交易日)。用 trade_cal (`is_open` 是 **int64**, 不是字符串, 用 `== 1` 过滤) 交叉核对 + 本地 parquet 对照, 逐条一致。
- 同一日期重复调 10 次稳定返 0; fields 数量 (4 字段 vs 生产 17 字段) 无关。
- **限流从不表现为返空, 而是抛异常**, msg 含 "每分钟最多"/"访问频率"/"2002"。

**旁证**:
- `tushare.py:198` 已有旧结论「5000 积分档不限流」(2026-09-04, 用户原话「现在还需要个毛的retry」) —— 但 `sync.py:375` 的 sleep 2.0s 一直没跟着改。
- 全项目其它 tushare 调用点早就用 0.15~0.3s (store.py:525/546/613, eps.py:233, sync.py:235) = 200~400/分, **只有 stk_factor_pro 一处用 2.0s, 是孤例**。

**时间账** (单次调用 ≈0.7s, 5570 行): sleep 2.0→0.3s, 日常增量 15 天只省 **0.4 分钟**; 真正价值在 **force 全量重拉 330 天: 14.85 → 5.50 分钟**。

**apply when:** 任何 tushare 限频/调参讨论。结论: sleep 2.0s → **0.3s** (留 1.5x 余量, 与其它调用点一致), 不建议再低。注释里**不要引用 doc_id=328 的 30/分**。
结果存档 `docs/eval/tushare-rate-limit-20261103.txt`。

### tushare 返空的第三种原因: 交易日盘前 (2026-11-04 实测补充)
Type: fact

**补充/修正** memory 里「返空 100% 由日历决定 (非交易日)」的说法 —— 那条只在**收盘后**成立。

2026-11-04 08:24 (周三, 交易日, 盘前 2 小时) 跑 `/t-sync-data`:
- `--auto` 检测 stale: --kline / --stk_factor / --etf 需跑, --stock_basic / --financials 跳过
- 补 20261104: `daily` / `index_daily` / `stk_factor_pro` / `fund_daily` **全部返空**
- `trade_cal` 交叉核对: `20261104 is_open=1` (交易日, 非节假日)

**→ 交易日 + 盘前也会返空**, 因为 tushare 日线要**收盘后**才有当日数据。
A 股 9:30 开盘 / 15:00 收盘 → **15:00 前拉当日日线必返空**, 与限流无关。

**返空的三种原因 (排查顺序)**:
1. **盘前** (交易日 15:00 前) → 正常, 等收盘后再跑
2. **非交易日** (用 `trade_cal` `is_open==1` 核对, 注意是 int64 不是字符串) → 正常, skip
3. **限流** → **从不是返空, 是抛异常** (msg 含 "每分钟最多"/"访问频率"/"2002")

**apply when:** 看到 sync 报 "返空 (待确认原因, 不 retry)" 时, 先判断是不是盘前。
`sync.py` 已有 `状态: EMPTY, 可能是节假日` 的提示语, 但**盘前场景也会印这句**, 容易误判成节假日。

### stk_factor_pro 比日线更晚: 盘后 1.5 小时仍返空 (2026-11-04 16:39 实测)
Type: fact

**盘后 ≠ 全部接口都有当日数据。** 各接口当日数据发布时刻不同, 别用「已收盘」推断一定能拉到。

2026-11-04 16:39 (周三, 交易日, 收盘后 1h39m) 跑 `/t-sync-data`:
| 接口 | 当日 (20261104) | 结论 |
|---|---|---|
| `daily` (K线) | ✅ 5572 只 | 收盘后即可用 |
| `index_daily` | ✅ 3 个指数 | 收盘后即可用 |
| `fund_daily` (ETF) | ✅ 2201 只 | 收盘后即可用 |
| `stk_factor_pro` | ❌ **返空 (调 2 次都空)** | **比日线晚, T+1 才有** |

本地 stk_factor parquet 最近交易日 = 20261030 / 20261102 / 20261103, **20261104 缺失**,
即每个交易日都只有 T-1 —— 稳定的 1 天滞后, 不是异常。

**与代码里的既有认知一致**: `tools/storage/store.py:836` 注释
「不能用 `_latest_trade_date`, 今天 (盘后) stk_factor_pro 还没出」。

**apply when:** sync 报 `stk_factor_pro 返空` 而 daily 正常 → **正常, 不要 retry、不要 force 重拉**。
等次日 `--auto` 会自动补上 (续跑机制: done_dates 存在 parquet metadata, 断点续拉)。
只有「连续多天都缺」才值得查。

**token 读取**: `tools/storage/sources/tushare.py` 从 `os.environ["TUSHARE_TOKEN"]` 读,
在 `.env` (不入 git)。手动调 tushare 接口时必须先 `load_dotenv('.env')`, 否则报「您的token不对」。

### stk_factor resume 按 trade_date 记进度 → 深市代码永久缺失 (2026-11-02 实测, 已修 51e48d5 + b6ade5b)
Type: bug

**症状:** 11-02 深市 7 只 (001219/002415/002683/002690/300357/301101/301109) 的 `stk_factor` 停在 **20261030**, 沪市 4 只是 11-02。
→ `ctx.market_cap_yi` 用的是 10-30 收盘价算的市值, PE 也是 10-30 口径。

**根因 (`tools/storage/sync.py::action_stk_factor`):**
1. `get_stk_factor_by_date(d)` 一次返**全市场**某一天所有代码
2. `done.add(d)` 只按 **trade_date** 记进度, 不按 code 记
3. 所以某天 Tushare 只先发沪市时, 该日期被标成"已完成", **深市代码永远补不上** (除非 `--force` 全量重拉)
4. 续跑还会显示荒谬数字: `已完成 1168 个 trade_date (从 parquet metadata)` vs `总交易日 325` —— 1168 是 5 个季度的日期并集, 但 `len(done)` 没去重跨季重复

**实测 11-02 Tushare 侧:** 全市场只返 **2504 行** (沪1904/深246/北354), 深市持仓全部缺。
按 `ts_code + trade_date` 单查也返 `EMPTY` (status=EMPTY, 不 retry) —— 上游确实没发, 不是我们漏拉。
**这是上游发布节奏问题, 不是本地 bug; 但本地 resume 逻辑让它变成永久性缺失。**

**绕过办法 (已用于持仓汇总):** 总股本取 `stk_factor` 最新值的 `total_share` (单位 **万股**, 不是亿股),
乘 K 线 11-02 收盘价 → 市值(亿) = `total_share * 10000 * close / 1e8`。
实测 001219 = 42.8亿 (之前用 10-30 口径算成 38.9亿, 差 10%)。

**apply when:** 任何用 `ctx.market_cap_yi` / 详报 PE 的场景 —— 先查该股 `stk_factor` 的 `trade_date` 是不是最新交易日。
修法方向: `done` 应该记 `(trade_date, ts_code)` 或者按 code 维度校验行数, 而不是按日期。

### stk_factor / fflow 写盘覆盖丢数据 bug (2026-10-14 固化)
Type: rule

**bug:** `caches/stk_factor_history.py::write_stk_factor_quarter()` 写盘时只写 rows 参数内容, 没合并旧 parquet. 每次 sync 覆盖历史 done 数据.

**触发场景:**
- 某次 sync 只拉了 1 天 (e.g. 10/13) → 写 2026Q4.parquet 时覆盖之前的 10/8 10/9 10/12 数据
- 注释说"修 bug: 先读旧 parquet metadata, 避免覆盖历史 done_dates" — **只修了 done_dates 状态, 没修 rows 数据**
- md 渲染时拿不到 stk_factor, 历史 PE 列显示 "—"

**修法 (2026-10-14 已修):** write_stk_factor_quarter 内部读旧 rows → 合并新 rows → drop_duplicates → 写回. append 语义, 跟 write_fflow_quarter 一致.

**修复验证:** 手动补 4 天 stk_factor (10/8/9/12/13) → 22237 行, sync 再跑一次不覆盖, md PE 列 4 个值齐.

**apply when:** 任何按 trade_date 增量同步、按季落盘的写入函数. 必须先读旧 rows 合并再写, 不能直接覆盖. 静态扫描: `grep -n "write_table\|pq.write" tools/storage/caches/*.py` 看是不是覆盖写.

### financials 季报拉不到数据的 3 个叠加 bug (2026-11-02 修)
Type: bug

**症状:** `sync --status` 显示 `financials: 最新 2026Q3 | 5580 只 (0 ok / 5580 skip)`,
但 Tushare `fina_indicator_vip period=20260930` 实测返 **6699 行**。该季永远拉不到数据。

**根因 (3 个叠加, 前一个掩盖后一个):**

1. **stale 检测 KeyError 被静默吞掉 (最致命, 一直存在)**
   `sync.py::detect_stale_flags` 里 `q = q_label[5:]` 取到 `"3"`, 但字典 key 写的是 `"Q1"/"Q3"`
   → `KeyError` → 被外层 `except Exception: pass` 吞掉
   → **financials 的 stale 检测从来没生效过** (不止 Q3, 一直是死的)
   修: key 改 `"1"/"2"/"3"/"4"` + 解析失败显式 raise; except 改保守标 stale + 打警告

2. **文件存在但 0 行 ok 不算 stale**
   原来只看"最新季 parquet 文件存不存在"。全 skip 时文件仍存在 (只有 5 列骨架), 所以永不标 stale。
   修: 加 `_financials_ok_count(file)` — 最新季 ok 行数 == 0 → 标 stale

3. **skip 被当"永久无数据"**
   `store.py::sync_financials` 里 `fetch_status=='skip'` 一律 continue。
   但季报是**陆续披露**的 —— 10/23 跑 Q3 时报告还没出齐 → VIP 返空 → 全标 skip;
   之后报告陆续发布 (截止 10/31), 但 skip 被当永久 → 永远不重拉。
   修: 新增 `store.py::_past_disclosure_deadline(period, buffer_days=15)`
   (Q1=4/30, Q2=8/31, Q3=10/31, Q4=次年4/30)。
   **只有过了截止日 + 15 天缓冲, skip 才算"定稿, 永久不拉"; 窗口内 → 重试。**

**修复后:** 2026Q3 → 5571 ok / 9 skip / 0 失败 (112 列)。剩余 9 个 = Q3 未披露新股 (Q2 同为 10 个, 正常)。

**apply when:** 任何"某季财报 0 数据 / 全 skip"的排查。**排查手法:**
```sql
SELECT fetch_status, count(*) FROM read_parquet('data/history/financials/YYYYQN.parquet') GROUP BY 1
```
全 skip + 列数只有 5 (ts_code/code/end_date/industry/fetch_status) = 骨架没填上, 说明 API 当时返空。
**通用教训:** `except Exception: pass` 吞掉 KeyError → 整个检测函数静默失效。stale 检测里的异常必须"保守标 stale + 打警告"。

### EPS parquet 并发写损坏 bug (2026-10-28 修)
Type: bug

**症状:** `/t-wyckoff-a2m --report-top 20 --workers 8` 跑完 10 只, 详报只生成 7 份, 3 只报:
`OSError: Could not open Parquet input source '<Buffer>': Couldn't deserialize thrift: don't know what type:`

**根因 (两个叠加):**
1. `tools/storage/caches/eps.py::write_eps` 是 **read-modify-write** (读全表 → 删旧 code → concat → 写全表).
   多进程 (mp.spawn, workers=8) 并发时: A 读完 → B 读完 → A 写 → B 写 → **B 的版本丢了 A 拉的数据**.
2. `_write_all` 用 `df.to_parquet(EPS_FILE)` **直接写目标文件** (非原子).
   一个进程写到一半, 另一个进程在 `get_eps` 里读 → 拿到半个文件 → parquet footer/thrift 解析失败.

**修法 (已落地):**
- `write_eps` 加锁: 进程内 `threading.Lock` + 跨进程 `fcntl.flock` (锁文件 `data/history/eps/.eps_consensus.lock`)
- `_write_all` 改原子写: `tempfile.mkstemp(dir=EPS_DIR)` → `df.to_parquet(tmp)` → `os.replace(tmp, EPS_FILE)`
- `_read_all` 加容错: 读失败 (损坏) → 备份坏文件到 `.parquet.corrupt-<ts>` + 当空表返回, 让 write_eps 能重建

**apply when:** 任何"单文件全表 + upsert"模式的 cache (eps / stk_factor / fflow)。多进程 batch 跑详报时这些 cache 会被并发写。
**排查手法:** 详报批量跑完数量对不上 → 先查 `data/history/*/ *.parquet` 能否被 duckdb 读 (`read_parquet` 报 thrift 错 = 文件损坏)。

### 季度切换的字典硬编码陷阱 (2026-10-14 固化, 防再翻车)
Type: rule

**铁律:** 业务运行时 (sync / rsi6 / backtest / batch) 永远不要硬编码 "2025Q3" / "2026Q2" / "20250825" / "20260901" 这类季度/日期字符串, 用 `caches/fflow_history.py` 里 3 个工具函数动态算:

1. **`_quarter_of(date_str) -> "YYYYQN"`** — 任意 YYYYMMDD → 季度
   - 例: 财报落盘日 "20260630" → "2026Q2"
2. **`quarters_since(start, end) -> list[str]`** — 区间所有季度
   - 例: backtest 5 年窗口, sync 字典初始化
3. **`latest_disclosed_quarter(dir="data/history/financials") -> "YYYYQN" | None`** — 已落盘最新季
   - **区别 _quarter_of(today)**: 10/14 时 today=2026Q4 但 Q3 财报 10/15 才披露, 所以 latest_disclosed=2026Q3
   - 用在: 业绩过滤 / 回测 end 边界 (不要用 _quarter_of(today) 拿当季)

**反例 (今天 2026-10-14):**
- `quarter_data = {q: [] for q in ["2025Q3", ..., "2026Q3"]}` → Q4 首日 KeyError
- rsi6_tech_scan 硬编码 `"2026Q2"` → Q3 财报落盘后用过时数据
- argparse default "2021Q2"/"2026Q2" → 跨年失效
- 手拼 `f"blowout-{latest_q[:4]}Q{...}"` → 跨年错位
- 月份→季度字典 `{"03":"Q1"..."12":"Q4"}` → 重复实现 _quarter_of

**已修 (11 处, 2026-10-14):**
- sync.py (4 处 quarter_data/quarter_done)
- rsi6_tech_scan (2 处 2026Q2 / glob pattern)
- finance_earnings_blowout / finance_roc_ey (2 处手拼)
- btf/runner + __init__ + run_1year (3 处 start/end default)
- backfill_roc_ey_cache / backtest_blowout / blowout_backtest_v630 (3 处 default)
- rsi6_backtest_history (2 处 default)

**P2 例外 (历史回测语料, 故意写死):**
- `backtest_roc_ey.py:48` REBALANCE_DATES (9 个季调仓日)
- `backtest_roc_ey_filters.py:31` 同
- `rsi6_backtest.py:158` YOY_HISTORY_START = '20240101'
- 这 3 处已加注释"故意写死, 别动", 新一轮回测需人工追加

**apply when:** 任何"业务运行时"写死季度/日期, 立即改动态算. 历史回测语料不动, 加注释.

## 财务 / 估值口径

### FinanceStrategy.quarterly 是降序, [0] 才是最新季 (2026-10-28 固化)
Type: rule

**铁律:** `AnalysisEngine(strategies=[FinanceStrategy])` 返回的 `result.raw['finance']['quarterly']` 是 **降序** (最新季在 index 0), 因为 `analysis_engine.py:999` 写的是 `for r in reversed(financials[-4:])`.

取最新季必须用 `quarterly[0]`, **不是 `[-1]`**.

**反例 (今天导出 22 只 A→M 财务):**
- 汇总表用 `d["quarterly"][-1]` → 取到 20250630 (最旧季) 而不是 20260630
- 后果: 22 只汇总表的 营收yoy / 净利yoy / 毛利率 / ROE / PE / 市值 **整列错位**
- 表现为"PE 列全变" (300519 74.2 → 47.4, 600323 20.5 → 18.6), 但 4 季明细表是对的 (逐行直接取 quarterly 全表)
- 用户问"他们的 PE 呢" 才暴露出来 —— 汇总表和明细表对不上

**排查手法:** 打印 `[r['quarter'] for r in q]` 确认顺序, 不要靠猜。

**apply when:** 任何消费 `finance.quarterly` 的代码 (汇总表 / 排序 / 取最新季)。已加 assert 守护:
```python
qs = [r.get("quarter","") for r in quarterly]
assert qs == sorted(qs, reverse=True), f"{code} quarterly 非降序: {qs}"
```

### 批量导出财务数据必须用生产链路 + 逐行 diff 校验 (2026-10-28 固化)
Type: rule

**铁律:** 导出多只票的财务/指标数据时, **不要自己重算** ROC/EY/PE/Magic, 走 `AnalysisEngine(strategies=[FinanceStrategy])` → `result.raw['finance']['quarterly']`, 再用 `report_renderer._section_quarterly_table` 同款 formatter 渲染.

**格式化必须逐个对齐 renderer** (report_renderer.py:84-113):
| 字段 | renderer 写法 | 说明 |
|---|---|---|
| or_yoy / np_yoy | `:+.1f` | 带 +/- 号, **不加千分位** (加了就跟详报不一致) |
| gm / roe | `:.1f` | |
| revenue_yi | `:.1f` | **不加千分位** (1316.8 不是 1,316.8) |
| netprofit_yi / ebit_yi | `:.2f` | |
| mc_yi / netdebt_yi / ev_yi / capital_yi | `_fmt` → `:,.0f` | **千分位 + 整数**, 0/None → `—` |
| roc | `:.1f` | None → `—` |
| ey | `:.2f` | None → `—` |
| static_pe / fwd_pe | `:.1f` | None → `—` |

**校验脚本已落地:** `tools/batch/verify_a2m_financials.py`
- 真源 = `docs/wyckoff-a2m/analyze-*.md` (/t-analyze 详报)
- 逐行 diff 4 季财务表的 88 行 × 16 列
- **踩坑**: 详报里还有 24 列的因子历史表 (行首也是 8 位日期), 必须靠表头 `ROC 资本回报率` 定位窗口, 否则会误抓 100+ 行

**apply when:** 任何"导出 N 只票的完整数据"的脚本。第一版自己重算 ROC 导致 603387 显示 15.7% vs 生产链路 16.4% (口径搞错: 4 季大表是**每季单季快照**语义 `compute_magic_one_day([r], ...)`, 不是最新季 TTM)。改成走生产链路后 diff = 0。

### 静态PE 年化 bug: q_str[5:10] 永远取不到 "06-30" (2026-10-28 修)
Type: bug

**症状:** `/t-analyze` 详报「财务数据」section 的 PE 列**系统性偏高 1 倍** (半年报 / 一季报行)。

**根因:** `tools/analysis/analysis_engine.py` 里 `end_date` 是 `YYYYMMDD` (无分隔符, 如 "20260630"), 但 `ytd_factor` 查表用的 key 是 `"06-30"`:
```python
ytd_factor = {"03-31":4.0, "06-30":2.0, "09-30":4.0/3.0, "12-31":1.0}.get(q_str[5:10], 1.0)
#                                                          ^^^^^^^ = "630" 不是 "06-30"
```
→ 永远 miss → 恒等于 1.0 → 半年报净利少乘 2 → **PE 虚高一倍**。

**实测影响 (22 只 A→M 命中票, 21 只有效 PE 全部虚高):**
| 代码 | 旧(错) | 新(对) | 代码 | 旧(错) | 新(对) |
|---|---|---|---|---|---|
| 600323 瀚蓝环境 | 18.6 | **9.3** | 300519 新光药业 | 47.4 | **23.7** |
| 601766 中国中车 | 18.6 | **9.3** | 002380 科远智慧 | 50.6 | **25.3** |
| 603387 基蛋生物 | 21.0 | **10.5** | 000810 创维数字 | 93.3 | **46.7** |
| 000538 云南白药 | 22.9 | **11.4** | 603683 晶华新材 | 183.4 | **91.7** |
| 688469 芯联集成 | 353.7 | **176.8** | | | |

**修法 (已落地):**
```python
_md = q_str[4:8]  # YYYYMMDD → MM-DD
_mmdd = f"{_md[:2]}-{_md[2:]}" if (len(_md)==4 and _md.isdigit()) else q_str[5:10]
ytd_factor = {"03-31":4.0,"06-30":2.0,"09-30":4.0/3.0,"12-31":1.0}.get(_mmdd, 1.0)
```
12-31 行 ytd_factor=1.0 本来就对, 所以只有 03-31 / 06-30 / 09-30 行受影响。

**排查手法:** 手算 PE = 季末市值 / (净利亿 × ytd_factor), 跟代码输出对不上就是这里。

**apply when:** 任何用 `end_date` 切片取 "MM-DD" 的代码。已 grep 全仓, 只有这一处踩坑 (`_quarter_of` 那些用 `date[4:6]` 取月份是对的)。

### OBV / fflow / PEG / DCF 四个维度已彻底删除 (2026-11-02 固化)
Type: fact

**铁律:** 这四个维度**已经从代码 + 文档 + memory 全部清除**, 不要复活, 不要"顺手加回来"。

| 维度 | 删除时间 | 原因 |
|---|---|---|
| **OBV** (量能) | 2026-10-27 | 实测负超额 (多头排列 -2.38%, 底背离 -1.60%), 剥掉"离 MA120 距离"成分后胜率 48.5% < 基准 50.1% |
| **fflow** (主力资金流) | 2026-10-28 | parquet 落后 K 线 29 天时, 38 只核心票里 14 只判"出货", 其中 13 只被同时点的量价指标判"偏进货" — 方向完全相反, 是陈旧数据造成的系统性看空偏差 |
| **PEG** | 2026-11-02 | 假指标 — 依赖 EPS 预测, 而 EPS 预测本身不准 (机构覆盖偏差 + 财报季前后剧烈变动) |
| **DCF L / L_E3** | 2026-11-02 | 用的是同一套 EPS 预测, 跟 PEG 同源, 一起删 |

**删除后的估值口径:** 只看 **PE (静态) + 盈利质量 (ROC / EY / 毛利率 / 杠杆)**, 全部用已审计 TTM 财报算, 不依赖任何预测值。
报告 20 section → 18 section (§5️⃣ PEG/DCF L 整档删除)。

**2026-11-02 补充清理 (用户指出"还在提这些删除的逻辑"):**
- `tools/btf/`: 删 `PEG15Strategy` (4 策略 → 3 + 基准), SQL 查 `peg`/`dcf_l` 两列删掉
- `tools/storage/caches/analysis.py`: `analysis_cache` 的 `peg`/`dcf_l` 列从自动迁移列表删除, `get_valuation_coverage()` 只返 roc/ey
- `tools/batch/backfill_roc_ey_cache.py`: 4 元组 → 2 元组 (roc/ey), 删 EPS 预读 (只喂 PEG/DCF), `_calc_4cols_for_code` → `_calc_2cols_for_code`
- `tools/analysis/signal_tracker.py`: 删 `MODEL_INFERENCE` 里的 'PEG' 模型推断条目
- `tools/research/lgb_mine.py`: 删 PEG 特征 + 修 3 个 bug (`ctx.valuation_data` 不存在 → 改 `ctx.eps_table`/`ctx.market_cap_yi`; `raw` 未定义 → 改 `ctx.name`/K 线自算; config 路径少一层 `.parent`)
- `handbook/analysis-framework.md`: 删 §2.5 PEG 整档 (53 行), §3 决策矩阵双轴改 PE+ROCEY, §11 三个排序表 PEG 列 → PE 列, §14.1 公式表补 ROC/EY, §14.5 术语表 PEG → ROC/EY, §15 五关改 PE+ROCEY+L
- `handbook/finance-glossary.md`: 删 §8 PEG 详解 (83 行) + 12 处引用改写
- `handbook/backtest-roc-ey-formula.md`: 用当前 4 策略代码重跑重建 (原 PEG +147% 结论作废)

**⚠️ 已知遗留 (跟 PEG/OBV 无关, 但会让估值链路失效):**
`analysis_cache` 的 `roc` / `ey` 两列**全表 0 行非空** — `backfill_roc_ey_cache.py` 从没成功跑过。
后果: `tools/btf/run_1year.py` 的 EY8 / ROC25 / ROC25+EY8 三个策略**选不出任何票** (收益恒 0),
`/t-finance-roc-ey` 的 ROC/EY 列也拿不到历史值。
修法: `python -m tools.batch.backfill_roc_ey_cache` 回填后重跑。

**apply when:** 任何"加个量能指标"/"加个估值指标"/"把 PEG 拿回来"的提议 — 先看这条。
新增量能/估值维度前, 必须先跑回测证明超额 > 0, 否则不进决策链 (4 类等权投票)。

### stock_basic 股本兜底读已废弃的 daily_basic → 详报股本恒 0.00亿股 (2026-11-03 修, 32d7d2b)
Type: bug

**症状:** 所有详报「📊 基础信息」section 总股本/流通股本都显示 `0.00亿股`。
美的应该 76.30亿股、海康 91.65亿股 —— 全是 0。

**根因:** `tools/storage/store.py::sync_stock_basic()` 的股本兜底读
`data/history/daily_basic/*.parquet`, 但 **v6.2.4 已用 `stk_factor_pro` 取代
daily_basic, 该目录根本不存在** → `except Exception` 静默吞掉 →
stock_basic parquet 落盘时丢了 `total_share`/`float_share` 两列 (schema 里压根没这两列)。

渲染器 `tools/render/report_renderer.py::_section_ts_basic` 读
`sb.get("total_share")` → 拿不到 → 显示 0.00。

**修法:** 兜底改读 `data/history/stk_factor/*.parquet`。
该源本来就有 `total_share` + `float_share` 两列 (单位 **万股**), 
**不需要再靠 `circ_mv / close` 反算** —— 旧代码那个反算逻辑也一并删了。

**重建方法:** stock_basic 有 30 天 mtime 缓存, 改了代码不会自动重建:
```bash
cp data/history/stock_basic/stock_basic.parquet ~/.Trash/stock_basic-backup-$(date +%s).parquet
touch -d "2026-09-01 00:00:00" data/history/stock_basic/stock_basic.parquet
bash tools/with_venv.sh python -m tools.storage.sync --stock-basic
```

**⚠️ 通用教训 (跟 financials stale 检测同一个病根):**
`except Exception as e: print(...)` 把异常降级成一行 warning 就继续走 ——
**数据源路径写错时, 下游 schema 会静默缺列, 半年都没人发现**。
凡是"从本地 parquet 补字段"的兜底逻辑, 必须 (a) 断言补上的行数 > 0,
(b) 补完校验 parquet 的列名真的存在。**代码里读 parquet 路径时, 必须先确认该目录存在。**

**验证:** 重建后补 5571/5577 只 (缺的 6 只 = 新股)。
活跃目录 (portfolio 24 / watchlist 28 / wyckoff-a2m 24) 全部刷新, 0.00 归零。
`docs/runA` / `docs/runB` / `docs/blowout` 是 10-30 的一次性归档, 没有任何脚本会再写, 保留原样。

**apply when:** 任何"详报里某个字段恒为 0 / 恒为空"的排查。
先查对应 parquet 的 **schema 列名**, 再查渲染器读的是不是同一个 key。

## A→M 扫描 / watchlist

### /t-wyckoff-a2m: compute_factor_history 只算威科夫, 不算其他因子 (2026-10-16 固化)
Type: rule

**铁律:** `/t-wyckoff-a2m` skill 跑全市场扫描时, **compute_factor_history 只算 wyckoff stage 一个因子**, 不要拉满 8 指标 (PE/PB/RSI/KDJ/MACD/BOLL/ATR/量比).

**为什么:**
- A→M 判定只依赖 wyckoff stage 分类 (Accumulation / Markup / Distribution)
- 拉满 8 因子 = 80% 算力浪费 (其他 7 个跟 A→M 无关)
- 跑全市场 5812 只时, 拉满 8 因子 = ~8x 慢 (60s → 480s)

**反例 (2026-10-16 初版差点犯):**
- factor_5y.py 走 `compute_factor_history(ctx, step, lookback=1250, history=history)` 拉满 8 指标
- wyckoff_a2m_scan.py v1 已经直接调精简版 wyckoff_judge (不算 sub_events) 绕开了
- 如果哪天扩展到用 compute_factor_history, 记得传 `factors=['wyckoff']` 或类似白名单参数

**apply when:** 任何批量扫威科夫阶段 / 阶段转变的脚本. 不要复用 factor_5y 的 compute_factor_history 调用方式 (它会拉满 8 指标).

### /t-wyckoff-a2m: 不要自己写估值/PE/财务过滤策略 (2026-10-16 固化, 用户纠错)
Type: rule

**铁律:** `/t-wyckoff-a2m` 的 A→M 扫描命中后, 想要"PE 低估 + A→M" / "业绩暴增 + A→M" 之类的复合过滤, **复用现有 `/t-*` skill**, 不要在 wyckoff_a2m_scan.py 里新加 PE/估值/fflow 子模块.

**为什么:**
- 现有估值链路: `/t-finance-roc-ey` (PE 静态 + ROC/EY 盈利质量, ~~PEG/DCF L~~ 已于 2026-11-02 整档删除) + `/t-finance-earnings-blowout` (业绩) + `/t-rsi6-tech` (技术)
- 复合过滤 = A→M 名单 + 上面 skill 的交集 (LLM 端用 watchlist 交集或 md grep 都行)
- 重复造 = 8x 慢 + 数据一致性漂移 + 多维护点

**反例 (2026-10-16):**
- LLM 提: "加 --with-pe flag, 命中 A→M 后只补算 PE 过滤 PE>50"
- 用户反驳: "不要自己写策略, 你没记性么? 这是重复造轮子"
- v1 精简版 `_stage_at()` 自己重算 MA + is_uptrend 阈值, 跟 stage_factor.wyckoff_judge 1:1 但不是同一份代码
- v1 10x 提速的代价 = 跟 /t-analyze 详报数据漂移风险 (详报报 Accumulation, 扫描报 Markup)
- 用户再次纠错: "wyckoff_a2m_scan.py 要复用 compute history"
- v2 改: 走 AnalysisEngine(strategies=[WyckoffStrategy]) + compute_factor_history, 跟 /t-analyze 100% 一致

**正确做法:**
- A→M 命中 → 落 docs/wyckoff-a2m.md 名单
- 跟 `/t-finance-roc-ey` 输出的 4 季财务大表 grep 交集
- 跟 `/t-finance-earnings-blowout` 输出的暴增名单 grep 交集
- 跟 `/t-rsi6-tech` 输出的技术超卖名单 grep 交集
- 复合结果在 LLM agent 端拼, 不在 wyckoff_a2m_scan.py 里拼
- /t-wyckoff-a2m 内部: 复用 AnalysisEngine + compute_factor_history, **不自己写 stage 判断器**

**apply when:** 任何 `/t-wyckoff-a2m` 的"扩展"想法 (加 PE / 加 KDJ / 加 fflow 子过滤), 都先 grep 现有 `/t-*` skill 是否已有同类输出, 复用, 不新写. /t-wyckoff-a2m 内部也禁止自己写阶段判断器, 必须复用 compute_factor_history.

### /t-wyckoff-a2m: 1 次 SQL 全市场 K 线 + 1 次 sub_events 全段扫 + 缓存 (2026-10-16 v6 固化)
Type: rule

**铁律:** 批量扫威科夫阶段转变 (`/t-wyckoff-a2m` 风格) 必须用 v6 batch 模式, 不要在循环里调 DataStore / scan_sub_events 多次.

**正确链路 (v6, 全市场 ~25s):**
```python
# 1. 1 次 SQL 拿全市场 K 线 (替代 5812 次 DataStore.get_kline 各读 27 parquet)
sql = """
    WITH ranked AS (
        SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg,
               ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
        FROM read_parquet('data/history/daily/*.parquet')
    )
    SELECT ... FROM ranked WHERE rn <= 250 ORDER BY ts_code, trade_date
"""
df_all = duckdb.execute(sql).fetchdf()  # 0.5s 拿 5812 票

# 2. 1 次 scan_sub_events 全段扫 (as_of_idx=None) + 缓存
sub_events_all = scan_sub_events(closes, highs, lows, vols, rng, o=opens, pct_chg=pct_chgs, as_of_idx=None, ...)

# 3. 循环每只, 不同 as_of_idx 时 过滤 sub_events (i <= as_of_idx), 传 precomputed_sub_events_raw
for k in range(lookback + 1):
    as_of_idx = n - lookback - 1 + k
    sub_events_filtered = [e for e in sub_events_all if e['idx'] <= as_of_idx]
    result = WyckoffStageFactor().compute(df, as_of_idx=as_of_idx, precomputed_sub_events_raw=sub_events_filtered, ...)
```

**反例 (v5, 全市场 ~2 分钟):**
```python
# 错: 循环每只票 6 次调 WyckoffStageFactor.compute, 每次都重扫 sub_events
for k in range(6):
    result = WyckoffStageFactor().compute(df, as_of_idx=as_of_idx)  # ❌ 内部又扫一遍 sub_events
```
5x 慢. 原因: scan_sub_events 是 O(n) 扫 250 根 K 线 + 9 种 detector, 循环里调用就重复 6 次.

**反例 (v4, 全市场 ~7 分钟):**
```python
# 错: 5812 次 DataStore.get_ctx → 内部 5812 次 _read_kline 各读 27 parquet 文件
for code in codes:
    ctx = DataStore.get_ctx(code)  # ❌ 5812 次重复 parquet read
```
200x 慢. 原因: duckdb 每次 query parse + 元数据查询 + read 27 文件, 即使内部有缓存, 5K+ 次累积也是分钟级.

**关键 API:**
- `WyckoffStageFactor().compute(df, as_of_idx=N, precomputed_sub_events_raw=events)` — stage_factor.py 已支持
- `scan_sub_events(..., as_of_idx=None)` — 扫整段; as_of_idx=N 时扫到 i <= N
- stage_factor.py line 427 fix: `self.config` → `getattr(self, 'config', {}) or {}` (直调 WyckoffStageFactor 不再 `?`)

**apply when:** 任何批量扫威科夫阶段 / sub_events / 阶段转变的全市场脚本. /t-wyckoff-a2m 的 v6 是参考实现.

### /t-wyckoff-a2m: 走 WyckoffStageFactor.compute() 直调会数据漂移, 必须用 AnalysisEngine (2026-10-16 固化)
Type: rule

**铁律:** `/t-wyckoff-a2m` 判定 stage 必须走 `AnalysisEngine(strategies=[WyckoffStrategy]).analyze_history()` 链路, **不能直接调 WyckoffStageFactor().compute()**.

**反例 (v6 直调, 2026-10-16):**
- 我为了"快"绕开 AnalysisEngine, 直接调 `WyckoffStageFactor().compute(df, as_of_idx=i, ...)` 拿 stage
- 实测 000301: v4 报 09-28~10-12 Accumulation (有 A→M 转变), v6 直调报全 Markup (漏报)
- 漂移 30%+: 654 只当前 M 票, v6 直调全部判 5 天都 M, v4 报 0 命中, 实际 v4 能找到 A→M 转变

**根因 (深度调查后):**
1. **window 参数差异**: v4 (WyckoffStrategy.analyze_history) 内部用 `_judge_full(c, h, l, v, sub_events, window, period_label)`, c = closes[-window:]; v6 直调传 window=min(250, n) 跟 v4 一致, 但传 window=n (1520 根) 会算全段 MA200, 跟 v4 算最近 250 根 MA200 不一样.
2. **precomputed_sub_events_raw 语义**: 传全部历史 sub_events (e['idx'] <= as_of_idx 过滤) 跟 v4 内部传的部分历史不同. v6 拿太多 sub_events, 触发 score_M 跳变, 误判 Markup.
3. **as_of_idx 索引**: v6 直调用 df 的 0-based idx, v4 用 ctx.kline 的日期索引. 两边 K 线长度不同 (v4 1520 根, v6 之前用 _load_all_klines(limit=250) 只 250 根) 导致 MA200 计算基准不同.

**正确做法 (回退 v4 路径):**
```python
ctx = DataStore.get_ctx(code, kline_only=True)
all_dates = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
dates = all_dates[-480:]  # 跟 /t-analyze 同源
engine = AnalysisEngine(strategies=[WyckoffStrategy])
history = engine.analyze_history(ctx, dates)
rows = compute_factor_history(ctx, step=1, lookback=lookback + 10, history=history)
stage = row["wyckoff_daily"]  # 跟 /t-analyze 详报 100% 一致
```

**性能:** 5812 × 0.07s = 7 分钟 (单进程顺序, 不能 ThreadPool - duckdb 锁死).

**v6 "快"路径 (--mode fast, 默认不用):**
- 1 次 SQL 全市场 K 线 (1.04s)
- 每只 1 次 scan_sub_events 全段扫 (as_of_idx=None) + 缓存
- 6 次 WyckoffStageFactor.compute 不传 precomputed, as_of_idx 不同, 每次重扫 sub_events
- 性能: 5812 × 0.1s = 10 分钟 (实际比 v4 慢, 但不漏报)
- 不准: stage 跟 v4 漂移 30%+, 漏报中途回 A 再转 M 的票

**apply when:** 任何批量扫威科夫阶段的脚本, 不要图快直接调 WyckoffStageFactor.compute(). 必须走 AnalysisEngine + compute_factor_history 链路 (memory 里 v1 反例, 这次的 v6 直调是 v2 反例).

### /t-wyckoff-a2m v8: scan_sub_events 全段扫是物理下界瓶颈 (2026-10-16 固化)
Type: fact

**实测 (580 票 / 5812 推算):**
- 单只 ~75ms (arrs 13ms + scan_sub_events 50ms + wyckoff_judge × 6 = 0.08ms)
- 5812 票 ~5.3 分钟 单进程
- ThreadPool 1/4/8 worker 实测 32s/32s/33s —— **0 加速**
- **物理下界**: scan_sub_events 内部跑 9 detector, pandas/numpy 计算 GIL 占满

**为什么 v6 报 25s 错了:**
- v6 当时只测了"用 arrs 预算走 WyckoffStageFactor.compute(),但漏报 30% (000301 案例)"
- 误把"compute 用 arrs 不重算"当作 25s 的来源,实际 scan_sub_events 占 67% 时间
- v7 回退 v4 路径重测, 7 分钟 → 5.3 分钟 (v8)

**已确认的实情:**
- _judge_full 跟 wyckoff_judge 末端数值 100% 一致 (实测 5 票 5 周期全等)
- _judge_full 已删 (2026-10-16), 唯一入口 wyckoff_judge
- v8 直调 wyckoff_judge + arrs 预算 (分析引擎代码链路跟以前一样, 只是跳过了 history 重新算)

**想真到 30s 必须动:**
- A. 加缓存层 (每天首次 5 分钟落 parquet, 后续秒级读) + staleness 跟 K 线同步
- B. 删 detector 几个冷门 (tr_pullback / compression), loss 风险用户不接受
- C. 默认 --watchlist-only, 全市场加 --force-all-market 警告

**apply when:** 任何 /t-wyckoff-a2m 性能优化/加快提议. 不要再说"用 ThreadPool 加速 30s",已实测 0 加速. 不要再试"把 scan_sub_events 用 numpy 向量化", 工程量大且 GIL 仍占.

### /t-wyckoff-a2m 过滤链收紧 (2026-10-28 落地)
Type: rule

**最终默认过滤链 (5 步, 全部 1 次 SQL/步, 0 网络):**
| 步 | 过滤 | 默认 | 剩余 (实测) |
|---|---|---|---|
| 0 | 原始候选 | — | 5816 |
| 1 | `exclude-st` | 开 | 5619 |
| 2 | `exclude-pe` | **40** (原 80) | 3659 |
| 3 | `with-quality basic` (营收yoy>5 + 净利yoy>5) + `min-roe 5` + `min-gross-margin 20` | basic/5/20 | 311 |
| 4 | `max-net-debt-ratio` 净负债/市值 | **0.5** | 299 |
| 5 | V12 严判 | lookback=5 | **5 命中** (9s) |

**flag 清单** (`tools/batch/wyckoff_a2m_scan.py`):
- `--exclude-pe` 默认 **40** (原 80)
- `--min-growth` 默认 None → 用档位门槛; basic 档门槛 **5** (原 0, 试过 10 太严)
- `--min-roe` 默认 **5.0** (0=不限)
- `--min-gross-margin` 默认 **20.0** (0=不限)
- `--max-net-debt-ratio` 默认 **0.5** (0=不限)
- 回退旧默认: `--exclude-pe 80 --min-growth 0 --min-roe 0 --min-gross-margin 0 --max-net-debt-ratio 0`

**阈值依据 (全市场分布, 2026-10-28 实测):**
- PE: 中位 38.3 / P25 20.7 / P75 85.4 / P90 189
- ROE: 中位 **2.29** / P25 0.06 / P75 4.89 / P90 8.22
- 毛利率: 中位 24.28 / P25 14.95 / P75 36.96 / P90 54.02
- 净负债/市值: 中位 -0.00 / P75 0.14 / P90 0.48

**门槛调参轨迹 (重要):**
- basic 档 yoy 0 → 10 → **5**: 0 会放进低基数假增长 (创维数字 yoy +202% 但 ROE 2.5%);
  10 又误伤基蛋生物 (营收 yoy 8.3% 但净利 yoy +69.4% / 毛利 69.1% / PE 10.5 — 差 1.7pct 就被砍);
  折中到 5 后基蛋生物 + 军信股份都回来
- 最终 5 只: 300832 新产业 / 301101 明月镜片 / 603387 基蛋生物 / 688410 山外山 / 301109 军信股份
  (全部 PE 15-38 / ROE 5.3-9.6% / 毛利 55-81% / 杠杆<0.2)

**⚠️ 顺带修的语义 bug:** `financials.grossprofit_margin` 是**毛利率水平值 (绝对 %)**, 不是毛利率同比增速.
旧代码把 quality 档位门槛 (0/10/20) 直接传给它 → "strong=10" 实际是 "毛利率 > 10%" 而非旧文档写的 "毛利率增速 > 10%".
financials 表**没有**毛利率 yoy 字段. 现已在 `_filter_by_quality` docstring 澄清, `gm_min` 与 `gm_abs_min` 都作用在同一列取更严者.

**⚠️ argparse help 里的 `%` 必须转义成 `%%`**: 否则 `python -m tools.batch.wyckoff_a2m_scan --help` 直接崩
(ValueError: unsupported format character ')' (0x29) at index 219). 我踩过一次 — help 文本里写 "绝对%)" 就炸.

**apply when:** 任何调 `/t-wyckoff-a2m` 过滤条件时; 分析脚本 `tools/batch/analyze_a2m_filters.py` 可跑全市场漏斗模拟 (不跑 A→M 判定).

### 详报里"当前买卖点"只能从 buy_sell_points 取, 因子历史表的买卖点列是"当天新出现" (2026-11-02 踩坑)
Type: rule

**铁律:** 汇总"当前有效买卖点"用 `result.raw['buy_sell_points'][tf]` (tf = daily/weekly),
经 `tools/render/report_renderer.py::_bsp_str()` 转字符串。**不要**从因子历史表的「买卖点」列取。

**两个坑:**
1. 因子历史表的「买卖点」列 = **当天首次出现**的信号 (`new_bsp_daily` / `new_bsp_weekly` diff), 不是当前仍生效的。
   最新那行经常是 `—`, 因为信号是前几天出现的。
2. 报告里「🟢 退场信号检查」section 是**模板桩** —— 11 只票的值完全一样 (`60分_底背: ❌` / `vs_MA120: -31` / `板块_MA20_偏离: -22`),
   **不代表该股真实状态**, 别引用。
3. 「缠论详情 → 买卖点」行 (日线/周线各一份) 显示的是 `buy_sell_points` 的**当前**状态, 权威来源。

**实测 (11 只持仓, 20261102):** 有买卖点的只有 4 只 ——
海康威视 🟢1买@34.91(含MACD底背) / 我武生物 🟢1买@21.54(含双中枢) / 东方电缆 🟢1买@37.90 / 基蛋生物 🔴1卖@8.47。
其余 7 只当前**无 123 买卖点**(只有 DIF走平 / 山川 / MACD底背 这类连续状态信号)。

**apply when:** 任何"这只票现在什么信号"的汇总。参照之前把"历史某日的 1卖/1买 快照"当成当前状态写进汇总, 结论完全错。

### A→M 判定必须用生产函数跑, 不能信历史 md 里的 A2M_STATUS 标记 (2026-11-02)
Type: rule

**权威来源:** `tools.batch.wyckoff_a2m_scan._process_one(code, df)` — 0 网络, 走生产算法。

**踩坑:** `docs/wyckoff-a2m/analyze-*.md` 里的 `<!-- A2M_STATUS hit/stale -->` 标记是**上一次扫描时**打的,
不是最新状态。11-02 重跑 `wyckoff_a2m_scan --no-full-report --write-md` 后, 军信股份 301109 从 `hit` 翻成 `stale`
(M 占比 12.5% >= 10%), 安井食品 603345 从 `hit` 翻成 `stale`。

**判定门槛 (V13 严判, `_process_one`):**
1. 最后 1 根 K 线 stage == 'Markup' (必须)
2. 最近 40 根里 Markup 占比 < 10% (允许 ≤ 3 根) —— 排除"之前 M → 转 A → 再转 M"假反弹
3. 单日涨跌幅 > 20% 跳过 (除权/复牌)

**被拒时怎么拿到原因:** `_process_one` 返 `None` 时要自己复算 stages + m_ratio 才能知道是卡在 2 还是 3。
实测 11 只持仓的 M 占比: 永兴 30.0% / 美亚 27.5% / 海康 20.0%(且 stage=Accumulation) / 明月 17.5% /
青岛 15.0% / 军信 12.5% / 东方电缆 12.5%(stage=Accumulation) / 基蛋 10.0%(stage=?)。

**apply when:** 任何"A→M 命中了哪些票"的结论。跑完扫描必须重新读 md 里的标记, 或直接调 `_process_one`。

### watchlist note 决策铁律 (2026-09-24 固化)
Type: rule

**铁律:** watchlist 里**不维护主观 note 字段**, 决策只用**当下的 K 线 + 业绩 + 资金面**, 不引用历史备忘.

**为什么:**
- note 是历史包袱 (上次复盘时点 + 当时的判断), 跟当下市场状态可能完全脱节
- 用户提问"买还是卖"时, LLM 翻 note 会制造**重复 + 矛盾** (note 说减仓, 但当下信号可能反转)
- 真实决策需要的是**实时数据 + 纪律位**, 不是考古

**操作:**
- watchlist.json 只保留 code/name/sector/list_type/tech_params 五种字段
- 不主动往 watchlist 加 notes 字段
- 引用 watchlist 时只看硬数据, 不看主观判断

**已发生:** 2026-09-24 清掉 mavis-quant-agent/config/watchlist.json 里 226 条 notes.

apply when: 任何引用 watchlist / 分析持仓股的场景

## 报告渲染 / linter / 文档一致性

### markdown table header / sep 写死脱节 bug (2026-10-14 固化)
Type: rule

**铁律:** 任何 "|---|" + "---|" * N 写死 N 的模式 → N 必须从 header 动态算. 加列时 sep 必漏改 → GFM 表格不渲染.

**反例 (今天 KDJ 加 2 列):**
- header: 25 cell (KDJ/K/D/J + KDJ金叉)
- sep:    '|---|' + '---|' * 23 = 24 cell
- 差 1 cell, GFM 严格按 header cell == sep cell 判断, 否则**整张表不渲染**
- 用户报 "table 乱掉了" / "显示不是 table" 几次, 实际是 GFM 不渲染

**修法 (2026-10-14 已修):** `FACTOR_HISTORY_SEP = "|" + "---|" * (FACTOR_HISTORY_HEADER.count("|") - 1)`
- header 加列时 sep 自动跟随, 0 维护
- 静态扫描: `grep -n "'\-\-\-'" tools/render/*.py` 找写死 sep 模式

**apply when:** 任何 md 表格有 '|---|' + '---|' * N 模式. N 必须从 header.count("|") - 1 算. 验证: `awk -F'|' '{print NF-2}'` 跟 header cell 数对比.

### linter warning 输出被截断 (2026-10-14 固化)
Type: rule

**bug:** `tools/batch/t_analyze_one.py:112` `for warn in warnings[:5]` 只显示前 5 条, 后面的 (包括 "表格列数不匹配" "财务数据行数错误" 等关键 🔴 warning) 被截.

**触发链 (今天 300274):**
1. 加 KDJ 2 列 → header 25 cell, sep 写死 24 cell
2. linter 报 9 条 warning (5 placeholder + 1 列数不匹配 + 3 财务)
3. t_analyze_one 截断显示前 5 条
4. 用户看不到列数不匹配, 以为 linter 没报 → bug 漏检
5. md 推 GitHub, GFM 严格按列数渲染 → 整张表不渲染

**修法 (2026-10-14 已改):** `for warn in warnings[:5]` → `for warn in warnings`. 截断是历史遗留 (避免刷屏), 但 linter 报 9 条以上时关键 warning 在后面.

**apply when:** 改 t_analyze_one 警告显示逻辑 / 改 linter warning 输出格式. 截断阈值 [:5] 别再留.

**加列/改 report_renderer.py 后必跑:**
```bash
bash tools/with_venv.sh python -m tools.render.report_linter all
```
现有 linter 已经有 3 类检测 (header/sep cell 数, data cell 数, cell 内未转义 `|`), 见 `tools/render/report_linter.py:389-408`. 不要再造重复轮子, 直接跑 linter `all` 看 🔴 warning. 0.x 秒,0 网络. 今天 KDJ 加列 3 次翻车都是这个, 改完不跑等于裸奔.

### SKILL.md 描述 vs 代码实现的兑现铁律 (2026-10-14 固化)
Type: rule

**反例 (今天 ETF 段):**
- SKILL.md 写"sync --auto 包含 ETF (4+ETF: kline/stk-factor/stock-basic/financials + ETF 日增量)"
- 实际: sync.py 没 --etf flag, action_auto 不检 ETF stale, --auto 跑完 ETF K线没更新
- 后果: 17 个 ETF md 永远停在 10/09 (用户每天手动调 sync_etf_incremental())

**铁律:** SKILL.md 写的功能必须由代码兑现. 改 SKILL.md 加描述时, 同时改:
- argparse (新 flag)
- action 调度 (实际跑该功能的函数)
- stale 检测 (让 --auto 也能自动跑)

**apply when:** 任何 "我在 SKILL.md 加了 X 功能" / "X 是默认行为" 的描述, 必须有对应代码. 没有就修, 不能留 SKILL.md 一边说"有" 代码一边没接. 静态扫描: `grep -n "add_argument\|auto 检测" tools/storage/sync.py` 跟 SKILL.md 的 9 个 flag 对照.

### EPS 机构一致预期整档删除 (2026-11-04)
Type: decision

**背景:** 用户问 "eps 是啥, 我要把他干掉"。查清后确认仓库里有**两个同名不同物**:
1. **EPS 机构一致预期** (eastmoney datacenter 拉机构 E0/E1/E2/E3 预测) — 要删的是这个
2. **财报真实 EPS** (`fina_indicator` 已审计, 存 `financials` parquet) — 算 `np_yi` + rsi6 亏损过滤在用, **不能动**

**为什么删 (决策依据):**
- 它是**预测值**, 而项目估值口径早已固化: 只看 PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆), 全部用已审计 TTM 财报算, 不依赖任何预测值
- 11-02 删 PEG/DCF 后, 它**已无任何分析消费者** — 只剩管道残骸 + 报告展示
- 副产物收益: 很多票的 EPS 拉不到 (EMPTY), 却占着数据完整性表一格。删掉后 002371 完整度 87% → **100%**

**删除范围 (11 处代码):**
| 层 | 删了什么 |
|---|---|
| L1 数据 | `tools/storage/caches/eps.py` (整个缓存层) / `eastmoney._build_eps_table` (唯一联网函数) / `sync.py --eps` flag / `DataStore.get_eps()` / `RawContext.eps_table` |
| L2 分析 | `RenderData.EpsRow` + `eps_table` + `eps_status` + 完整性表的 EPS 行 |
| L3 渲染 | 报告「EPS 一致预期」表 / linter 的 "EPS 表格" + "缺 EPS 一致预期" 检查 / section id `eps_finance` → `finance_quarterly` |
| 工具 | guardrail `eps-scope` check (整个) / `lgb_mine.py` PE_TTM+PB 改走 daily_basic / `backfill_roc_ey_cache._load_eps_window` (本就无调用方) |

**实测影响: 判定结果零变化。** 7 只票 (002371/300274/688256/600089/002463/300308/000725) 改动前后报告 diff:
缠论 / Magic ROC/EY / 买卖点 / 因子矩阵 **逐字节一致**, 差异只有 EPS 行 + EPS 表 + section id + 完整度分母。

**连带效应:**
- `eastmoney.py` 由此变成**纯计算模块** (周线合成 + 技术指标), guardrail network 白名单从 2 个入口缩到 1 个 (只剩 tushare)
- `datacenter.eastmoney.com` 退出合法源 → CLAUDE.md「合法源只有 2 个」改成「只有 1 个」
- 顺手修掉 `eastmoney.py` 的死 CLI 块 (引用 11-02 就删掉的 `fetch_all`/`status_emoji`, 一跑就 NameError)

**apply when:** 想删某个数据源前, 先分清「预测值」vs「已审计真实值」—— 同名不同物, 删错一个直接断生产链。
删完必须做**改动前后报告 diff**, 而不是只看 "跑通了" —— 跑通 ≠ 判定没变。
