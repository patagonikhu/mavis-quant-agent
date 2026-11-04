# 威科夫 / Markup / fallback 判定体系

> 本文件是 `handbook/project-lessons.md` 的主题分册 (01-wyckoff.md)。
> 索引 (全 47 条一句话摘要) 见 [`handbook/lessons/README.md`](README.md)。
> 用法: **grep 查**, 不用通读 —— 提到具体话题先在这里搜关键词。

---


### WyckoffTradingAgent 参考实现的 MA 判定 (2026-11-03 查证)
Type: fact
> **摘要:** 参考实现里**没有** MA50≥MA200×0.95 这种容差; MA 判定是独立一层 (L2.5 趋势判定), 只在主路径漏斗后跑

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
> **摘要:** 生产 Markup 判定 = WTA L2.5 趋势判定 + fallback 加严, 已对齐 (0a16616), 别再自行推导

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
> **摘要:** MA50/MA200 容差只认 `helpers._ma50_reached_ma200` 一个函数, 到处自己算必然漂

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
> **摘要:** 命中数对容差**非单调**, 调参必须实测扫描取值, 不能靠直觉 (最终 0.90, aebcb00)

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
> **摘要:** 兜底路径不该带主路径级门槛 —— 又漏又慢, 方向反了 (c5cf58a)

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
> **摘要:** 出处从 `.git/filter-repo` 挖出来的, 别猜也别凭印象

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
> **摘要:** 两种语义对 A→M 命中集**零影响**, 不用在这上面耗时间 (a743e1c)

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
> **摘要:** 已删; 留着只会让人以为它在起作用

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
> **摘要:** 已考古定案, 改动前先读这条

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
> **摘要:** 443 只生产池实测 (0 回测), 结论以此为准

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
> **摘要:** 93% 的 Markup 判定靠 fallback 兜 —— 优化主路径收益有限, 别在这上面使劲

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
> **摘要:** 门槛已定案为 -5 (d56c772), 别再改

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
> **摘要:** 用 AST 插桩实测 fallback 命中, **取代下面被否掉的说法** (以本条为准)

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
> **摘要:** MA200 基准有 3 种口径, 已定案 (e6cbdbe), 别混用

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
> **摘要:** `markup_entry_conditions()` 是 4 条判据**唯一事实源**, 改判据只改这一处

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
> **摘要:** 威科夫日线/周线的真实周期口径有固定真源, 别自己重算 (2026-11-05 修 BUG 固化)

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
> **摘要:** 它的口径跟生产不同 —— **别用**, 生产走 AnalysisEngine

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
> **摘要:** 先 grep 再回答, 别凭印象说"有/没有"

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
