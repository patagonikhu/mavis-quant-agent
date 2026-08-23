# Investment Analysis Framework — 投资分析框架

<a id="top"></a>

> **Last updated:** 2026-06-26
> **Author:** Mavis + user co-built
> **Scope:** A股 / 美股 / 港股 long-term investors
> **Two frameworks in one doc:** (1) Four-Questions qualitative model, (2) T-Framework quantitative timing.

> ⚠️ **Disclaimer:** This framework is built on public information and reasonable inference. It does **not** constitute investment advice. Verify all data independently. All decisions are the investor's own. Markets carry risk; invest prudently.

---

## 1. Framework Overview — 框架总览

<a id="overview"></a>

This framework unifies **two orthogonal dimensions** for every buy/sell decision:

| Dimension | Framework | Question answered |
|---|---|---|
| **Quality (cross-section)** | Four Questions | "Is this a good target?" |
| **Timing (longitudinal)** | T Framework | "Is now the right moment?" |

A decision is complete only when both dimensions are evaluated. **Quality alone ≠ buy; timing alone ≠ buy.**

### 1.1 Two Orthogonal Dimensions — 两个正交维度

<a id="two-dimensions"></a>

```
                    Four Questions (Quality)
                    Bad ←──────────────→ Good
                ┌──────────────────────────────┐
   T Framework  │ ⚠️ Wait / Avoid             │ 🥇 Heavy    │
   Timing       │                              │              │
   Early ←──────│  (early but bad)              │ (early +    │
   Late ───────→│  (late but bad)               │  good) 🥇   │
                │ ❌ Skip                       │ 🥈 / 🥉 Hold│
                └──────────────────────────────┘
```

**Key insight:** A target can be the "best quality" but still be a sell if timing is past (T+12), and a "weak quality" target can still be a buy if it's T-3 and priced-in < 0.5.

### 1.2 Decision Workflow — 决策流程图

<a id="workflow"></a>

```
┌──────────────────────────────────────────────────┐
│ Step 1: Find T-Point                              │
│   → Look up data/events.json for {code}           │
│   → T = (event_date - today) / 30                 │
│   → No event?  →  Priced-in only (one-dim)        │
├──────────────────────────────────────────────────┤
│ Step 2: Estimate Priced-in                        │
│   → Priced-in = (mkt_cap / (TAM × margin × PE))   │
│                  / leader-achievable-share        │
├──────────────────────────────────────────────────┤
│ Step 3: Locate on 2D Matrix (see §5)              │
│   → Reads out single action: 🥇/🥈/🥉/⚠️/❌       │
├──────────────────────────────────────────────────┤
│ Step 4 (optional): Run /t-analyze for deep dive   │
│   → Full Four-Questions report, leader score,     │
│     monitoring, risks (60 lines)                   │
└──────────────────────────────────────────────────┘
```

---

## 2. Four Questions (投资四问) — 买入决策

<a id="four-questions"></a>

Before buying **any** target, answer these 4 questions. All four must be ✅ to be a real opportunity.

1. **Chokepoint** — Is this an irreplaceable link in the supply chain?
2. **TAM** — Is the 5-year market growth big enough?
3. **Leader** — Is this company the *real* leader?
4. **Priced-in** — Has the market already priced in the upside?

### 2.1 Chokepoint Strength — 卡点强度

<a id="chokepoint"></a>

| Dimension | ⭐⭐⭐⭐⭐ Super | ⭐⭐⭐⭐ Strong | ⭐⭐⭐ Medium | ⭐⭐ Weak |
|---|---|---|---|---|
| Substitutes | 0 | 1 weak | 1-2 partial | Many |
| Players | 1-3 | 3-5 | 5-8 | 10+ |
| Capacity ramp | 5+ yrs | 2-3 yrs | 1-2 yrs | < 1 yr |
| Demand elasticity | Zero (cut = stop) | Low | Medium | High |
| Customer binding | Exclusive / long-term | Main supplier | Multi-supplier | Replaceable |

### 2.2 TAM Discipline — 总市场规模约束

<a id="tam"></a>

**Core principle:** A company cannot be worth more than its TAM.

```
Theoretical mkt_cap ceiling = TAM × industry net margin × reasonable PE
```

| Industry | 5-yr TAM growth | Notes |
|---|---|---|
| AI core (HBM/GPU) | 5-10x | Real high-growth |
| AI enablers (optics/cooling) | 4-6x | Real high-growth |
| Physical infra (power/transformer) | 3-5x | Hard bottleneck |
| Critical metals (rare-earth/uranium) | 4-6x | AI+robot dual-driver |
| Traditional (steel/auto) | 1.0-1.3x | **No big growth** |

**Anti-pattern:** Most "AI concept stocks" with TAM < $5B cannot reach Micron-scale market cap.

### 2.3 Leader Score (0-14) — 龙头评估 4 维度

<a id="leader-score"></a>

**Score must be ≥ 11 to be a "real leader."**

| Dimension | 5 pts | 3-4 pts | 2-3 pts | 0-1 pt |
|---|---|---|---|---|
| **Current market share** | > 30% | 10-30% | 3-10% | < 3% |
| **Tech leadership** | 2+ gen ahead | Parity | 1 gen behind | 2+ gen behind |
| **Customer binding** | Exclusive / Top-1 main | Multi Top-5 | 1-2 big customers | None |
| **Capacity expansion** | Ample + ramping | Ample | Tight | Surplus |

**Rating:**
- **≥ 11:** Real leader (use implied-share framework)
- **8-10:** Tier-2 (cautious)
- **< 8:** High-risk (tech lagging / customer churn)

### 2.4 DCF 隐含终局利润 L (替代 Priced-in) — v3.0

<a id="priced-in"></a>

> **v3.0 变更:** Priced-in (TAM-based) 误差 5-10x，已废弃。替换为 DCF 反算隐含终局利润 L，误差 30-60%，方向已知可修正。

**核心问题从"这只股票应该值多少"变为"市场押注这家公司最终能赚多少"。**

#### 2.4.1 DCF 反算公式

```
市值 = Σ(t=1..3) E_t/(1+r)^t                          # 近期 (E1-E3 机构预期)
     + Σ(t=4..8) E3·(1+g)^(t-3)/(1+r)^t               # 过渡期 g=(L/E3)^0.2-1
     + (L/r)/(1+r)^8                                    # 永续期 (g=0)

已知: 市值(实时) + E1/E2/E3(curl 机构预期) + r(折现率)
反算: L (隐含终局利润，二分法求解)
```

**三档折现率:**

| r | 含义 | L 偏差 |
|---|---|---|
| 8% | 低风险资产基准，**最接近真实 L** | +14% (偏高) |
| 10% | 中性基准 | +43% (偏高) |
| 12% | 高风险压力测试 | +71% (偏高) |

**校正规则:** r=10% 的 L 乘 0.7 ≈ r=8% 的 L ≈ 真实市场隐含 L。

#### 2.4.2 Bash 计算 (每次分析必须执行)

```bash
python3 << 'EOF'
GROWTH_YEARS = 5
def fair_value(L, e1, e2, e3, r_pct):
    r = r_pct/100.0
    pv = e1/(1+r)**1 + e2/(1+r)**2 + e3/(1+r)**3
    if e3>0 and L>0 and abs(L-e3)>1e-9:
        g = (L/e3)**(1.0/GROWTH_YEARS)-1.0
        for t in range(4,9): pv += e3*(1+g)**(t-3)/(1+r)**t
    elif e3>0 and L>0:
        for t in range(4,9): pv += e3/(1+r)**t
    pv += (L/r)/(1+r)**(3+GROWTH_YEARS)
    return pv

def implied_L(cap, e1, e2, e3, r_pct):
    r=r_pct/100.0; hi=max(cap*r*(1+r)**8*10,e3*100,1000.); lo=0.
    for _ in range(300):
        mid=(lo+hi)/2.
        if fair_value(mid,e1,e2,e3,r_pct)<cap: lo=mid
        else: hi=mid
    return (lo+hi)/2.

# 替换以下参数
cap=321; e1=14.54; e2=17.07; e3=19.83
for r in [8,10,12]:
    L=implied_L(cap,e1,e2,e3,r)
    g=((L/e3)**0.2-1)*100
    print(f"r={r}%  L={L:.1f}亿  L/E3={L/e3:.2f}x  g={g:.1f}%/yr")
EOF
```

**输入数据来源 (全部真实 curl，无 LLM 估算):**

| 参数 | 来源 | 准确性 |
|---|---|---|
| `cap` (市值) | 股价×股本，push2 API | ✅ 实时 |
| `E1/E2/E3` (净利润) | datacenter API `PARENT_NETPROFIT` | ✅ 机构平均 ±20% |
| `net_margin` (净利率) | `PARENT_NETPROFIT/TOTAL_OPERATE_INCOME` 历史均值 | ✅ 真实历史 |

#### 2.4.3 解读 L — 两步判断

**第一步: L/E3 快速筛 (无需外部数据)**

| L/E3 | 市场预期 | 信号 |
|---|---|---|
| < 1 | 预期业绩下滑 | 可能低估，需验证 |
| 1-2 | 温和增长 | 正常区间 |
| 2-5 | 较高增长 | 需验证天花板 |
| 5-10 | 高增长叙事 | 叙事已较满 |
| > 10 | 极度透支 | 除非宏大叙事否则不买 |

**第二步: L vs 可达利润 (核心判断)**

```
可达利润 = 公司营收天花板 × 真实净利率

营收天花板: 粗估即可，问"这家公司最好情况下能做多大生意"
净利率:     用 curl 历史均值，真实数据

L / 可达利润:
  < 0.5  → 明显低估，高信念买入
  0.5-1  → 合理偏低
  1-2    → 合理偏高，谨慎
  > 2    → 明显高估，不买
```

**示例 — 双环传动:**
```
L(r=10%) = 41亿，净利率 12%
→ 隐含营收天花板 = 41/12% = 342亿
→ 粗判: RV减速器中性市场 ~1500亿(全球机器人放量)，龙头15% = 225亿营收 × 12% = 27亿
→ L=41亿 / 可达利润27亿 = 1.5x → 合理偏高，叙事部分定价

修正: 乐观场景(百万台机器人) 可达利润=60亿，L=41亿/60亿=0.68x → 低估
结论: 叙事展望期，中性合理，乐观低估 → 博弈机会
```

---

### 2.5 PEG Ratio — PEG 比率 (EPS-based sanity check)

---

### 2.5 PEG Ratio — PEG 比率 (EPS-based sanity check)

<a id="peg-ratio"></a>

**Why a second valuation method?** Priced-in (TAM-based, top-down) and PEG (EPS-based, bottom-up) often disagree. When they do, **PEG wins** because EPS is hard data while TAM is estimate.

**Formula:**
```
PEG = Forward PE / Expected EPS Growth Rate (%)
```

**Parameters (硬数据, 优先信这个):**
| 参数 | 含义 | 取值范围参考 |
|---|---|---|
| `Forward PE` | 当前价 / 未来 12 个月 EPS 预期 | 5-100 (因行业) |
| `EPS Growth Rate` | 3-5 年 EPS 年复合增速 (%) | 0-100 |

**Bucket table:**

| PEG | Meaning | 行动 |
|---|---|---|
| **< 1.0** | Cheap (Lynch classic buy zone) | 加仓候选 |
| **1.0 - 1.5** | Fair (in-line with growth) | 标准 |
| **1.5 - 2.0** | Expensive | 谨慎 |
| **> 2.0** | Very expensive (pay $2 for $1 growth) | 减仓 / 跳过 |

**PEG vs Priced-in conflict resolution:**

| Priced-in | PEG | 解读 | 行动 |
|---|---|---|---|
| < 0.8 ✓ | < 1.5 ✓ | **Two-sided cheap** — high conviction | 🥇 重仓候选 |
| < 0.8 ✓ | > 2.0 ✗ | TAM 故事便宜, 短期盈利撑不起 | ⚠️ 降级 (PEG wins) |
| > 1.2 ✗ | < 1.0 ✓ | 短期便宜, 故事贵 | 🥉 轻仓观察 |
| > 1.2 ✗ | > 2.0 ✗ | Two-sided expensive | ❌ 不买 / 减仓 |

**Rule of thumb:** When Priced-in and PEG conflict, **trust PEG** (EPS is audited data; TAM is LLM estimate).

**PEG's 7 known pitfalls:**

| # | 陷阱 | 应对 |
|---|---|---|
| 1 | Negative EPS (unprofitable) | Use PS or EV/Sales instead |
| 2 | Cyclical EPS at peak | Look at mid-cycle EPS instead |
| 3 | Decelerating growth | PEG = 1 today, but growth dropping → future PEG > 2 |
| 4 | One-time items in EPS | Use normalized EPS |
| 5 | Reinvestment dilution | Adjust for share issuance |
| 6 | PEG < 1 value trap | Could be growth declining, not cheap |
| 7 | Industry PEG ≠ market PEG | Tech PEG = 1.5 fair; utilities PEG = 1 expensive |

---

## 3. Combined Decision Matrix — 综合决策矩阵 (v3.0)

<a id="decision-matrix"></a>

**两个指标，覆盖两个维度:**
- **PEG** — 近期盈利够不够便宜（硬约束）
- **DCF L / 可达利润** — 长期叙事有没有打满（叙事空间）

```
                        L / 可达利润
                    <0.5   0.5-1   1-2    >2
  Leader  ≥11       🥇      🥈      🥉     ⚠️
  score   8-10      🥈      🥉      ⚠️     ❌
          <8        🥉      ⚠️      ❌     ❌❌

🥇 Heavy (>15%)  🥈 Standard (8-15%)  🥉 Light (3-8%)
⚠️ Wait          ❌ Don't buy
```

**PEG 降级规则 (硬约束，优先级最高):**

| PEG | 行动 |
|---|---|
| < 1.5 | 矩阵结果有效 |
| 1.5-2.0 | 矩阵结果降一档 (🥇→🥈, 🥈→🥉) |
| > 2.0 | 矩阵结果降两档 (🥇→🥉, 🥈→⚠️) |

**双侧确认规则:**

| PEG | L/可达利润 | 结论 |
|---|---|---|
| < 1.5 ✅ | < 0.8 ✅ | 🥇 高信念，双侧便宜 |
| < 1.5 ✅ | > 2.0 ❌ | ⚠️ 近期便宜但叙事透支 |
| > 2.0 ❌ | < 0.5 ✅ | 🥉 叙事低估但近期贵，等 PEG 修复 |
| > 2.0 ❌ | > 2.0 ❌ | ❌ 不买 |

---

## 4. T Framework (T 框架) — 时机决策

<a id="t-framework"></a>

> **Core idea:** Each target has **one key event** (the "T-point"). Compute how far T-point is from today. Position determines action.
>
> **Mantra:** *T-3 埋伏 (position early), T+0 加仓 (add at event), T+6 跑路 (exit by T+6)*

### 4.1 T-Position Formula — T 位置公式

<a id="t-formula"></a>

```
T = (event_date - today).days / 30   // unit: months
```

- Negative T → event is in the future (position-building phase)
- T = 0 → event is happening (main-uptrend phase)
- Positive T → event has passed (harvest / decay phase)

### 4.2 T-Phases (10 stages) — T 阶段 10 分

<a id="t-phases"></a>

| Phase | Stage name | Emoji | Action |
|---|---|---|---|
| T-12 | Story-start | 🔴 High risk | Observe, micro position (<1%) |
| T-9 | Story-fermenting | 🟡 Medium | Add to 2% |
| T-6 | Concept-confirmed | 🟢 Best ambush #1 | Add to 3% |
| T-3 | About-to-deliver | 🟢 Best ambush #2 | Add to 4% |
| T-1 | Sweet-spot window | 🟢 Sweetest | Add another 1-2% |
| T+0 | Main-uptrend | 🚀 Main wave | Full position, hold |
| T+3 | Delivery-peak | 🟡 Top signal | Trim 1/3 |
| T+6 | Danger zone | 🔴 Should trim | Trim another 1/3, keep floor |
| T+9 | Divergence | 🔴 Ebbing | Observation only |
| T+12 | Decay | ⛔ Liquidate | Full exit |

**Phase boundaries (auto-computed):**

```
T ≤ -12      → T-12
-12 < T ≤ -9 → T-9
-9  < T ≤ -6 → T-6
-6  < T ≤ -3 → T-3
-3  < T ≤ -1 → T-1
-1  < T ≤ 0.5→ T+0
0.5 < T ≤ 3  → T+3
3   < T ≤ 6  → T+6
6   < T ≤ 9  → T+9
T > 9        → T+12
```

### 4.3 Action Recommendations — 操作建议表

<a id="t-actions"></a>

| T-range | Phase | Action | Position size |
|---|---|---|---|
| T-12 ~ T-9 | Story-start | Observe | < 1% |
| T-6 ~ T-3 | Best ambush | **Add aggressively** | 3-4% |
| T-1 | Sweet-spot | **Add** | +1-2% |
| T+0 | Main-uptrend | **Full hold** | target size |
| T+3 | Delivery-peak | **Trim 1/3** | -1/3 |
| T+6 | Danger zone | **Trim another 1/3** | -1/3 (keep floor) |
| T+9 | Divergence | Observation only | floor only |
| T+12+ | Decay | **Full exit** | 0% |

---

## 5. 2D Matrix: T-Position × Priced-in — 二维矩阵

<a id="two-value-matrix"></a>

**The two orthogonal dimensions compose into a 9-cell matrix.** Each cell maps to one action.

```
                              Priced-in ratio
                       <0.5    0.5-0.8    0.8-1.2    1.2-2.0    >2.0
T range
T-6 ~ T-1 (ambush)    🥇 Heavy 🥇 Heavy   🥈 Std     ⚠️ Wait    ❌ Skip
                                                    for dip
T+0 ~ T+3 (delivery)  🥈 Std   🥉 Light    ⚠️ Trim    ❌ Trim    ❌ Liq
T+6+ (post-event)     ⚠️ Trim  ❌ Liquidate ❌ Liquidate ❌ Liq   ❌❌ Liq
```

**Nine-cell interpretation:**

| Position | Meaning | Implication |
|---|---|---|
| Bottom-left (low priced-in, early T) | **Gold opportunity** | Add aggressively |
| Top-right (high priced-in, late T) | **Trap** | Run |
| Diagonal (priced-in matches T phase) | **Fair** | Hold at current size |
| Top-left (low priced-in, late T) | **Failed delivery** | Sell — story didn't deliver |
| Bottom-right (high priced-in, early T) | **Run-ahead** | Don't chase, wait for pullback |

**Quick examples (as of 2026-06-26):**

| Target | T | Priced-in | Cell | Action |
|---|---|---|---|---|
| 特变电工 (600089) | T-2 | 0.56 | bottom-left | 🥇 Heavy |
| 绿的谐波 (688017) | T-0.6 | 0.8 | middle | 🥈 Std (leader 6/14 weak → consider downgrading to 🥉) |
| Micron (MU) | T+9 | 10 | top-right | ❌❌ Liquidate |
| 金力永磁 (300748) | T-1 | 0.56 | bottom-left | 🥈 Standard |

**Note:** §5 2D matrix was originally backed by `/t-twovalue` skill (removed in v2.1). For per-target output, `/t-analyze` includes this matrix in its analysis.

---

## 6. Data Contract — 数据约定

<a id="data-contract"></a>

### 6.1 `data/events.json` Schema

<a id="events-schema"></a>

| Field | Type | Required | Description |
|---|---|---|---|
| `code` | string (6-digit) | yes | Stock code, e.g. "688017" |
| `name` | string | yes | Stock name, e.g. "绿的谐波" |
| `sector` | string | yes | Sector / sub-sector, e.g. "机器人-减速器" |
| `event_type` | enum | yes | One of: `量产` / `产品发布` / `业绩兑现` / `大订单` / `政策催化` / `技术突破` / `产能扩张` / `股东减持` |
| `event_date` | ISO date (YYYY-MM-DD) | yes | T-point date |
| `description` | string | yes | Short description of the event |
| `impact` | enum | no (default `正`) | `正` / `负` / `中` |
| `confidence` | float 0-1 | no (default 1.0) | Confidence in event happening on time |
| `source` | string | no | Source of the event info |
| `notes` | string | no | Free-form notes |

### 6.2 `CatalystEvent` Contract (text spec, no code)

<a id="catalyst-event"></a>

```
Concept: CatalystEvent (a single key event tied to one target)

Fields (mirrors events.json):
  code        : str        # stock code
  name        : str        # display name
  sector      : str        # sector tag
  event_type  : enum       # see §6.1
  event_date  : date       # the T-point
  description : str        # 1-line description
  impact      : enum       # positive / negative / neutral
  confidence  : float      # 0..1, default 1.0
  source      : str        # optional source
  notes       : str        # optional notes

Default event library: see data/events.json (12 default events).
Update cadence: rolling, LLM + user co-maintain.
```

**Important:** This is a **spec, not code**. The Python skeleton in earlier versions of `t-framework-implementation.md` (`event_db.py` / `t_calculator.py` / etc.) referenced a `t_framework/` package that does **not exist in the repo**. Treat that code as design notes only. Implementation, if ever needed, lives outside this framework doc.

---

## 7. Six "Under-Priced + High-Growth" Sectors — 6 板块清单

<a id="sectors"></a>

| Sector | TAM 2030 | 5-yr TAM growth | Chokepoint | Concentration | Rec |
|---|---|---|---|---|---|
| **Robotic precision parts** | $50B | **10x** | ⭐⭐⭐⭐⭐ | Medium | 🥇 |
| **NdFeB rare-earth magnets** | $30B | **4-5x** | ⭐⭐⭐⭐⭐ | Medium | 🥇 |
| **Power transformers / UHV** | $55B | **3-4x** | ⭐⭐⭐⭐⭐ | High | 🥇 |
| **SiC power devices** | $30B | **6x** | ⭐⭐⭐⭐ | Medium | 🥈 |
| **Advanced packaging equipment** | $10B | **8x** | ⭐⭐⭐⭐ | Medium | 🥈 |
| **Specialty industrial gases** | $12B | **2.5x** | ⭐⭐⭐⭐ | Medium | 🥉 |

### 7.1 Sector 1: Robotic Precision Parts — 机器人精密零部件

<a id="sector-robotic"></a>

**TAM:** Global ~$3B (2025) → ~$15B (2030), 5x
**Drivers:**
- Each humanoid robot needs 2-5 kg NdFeB magnets + 20-40 precision reducers
- 100M robots = 2-5M tons NdFeB + 2-4B reducers
- Global capacity is **nowhere near** enough

**Why under-priced:**
- Retail focuses on整机 (whole units like Tesla Optimus / Figure), ignores parts
- Harmonic Drive (Japan) monopolizes 70% of harmonic reducers
- Domestic replacement just starting, most players < $3B mkt_cap

**A-share targets:**

| Target | Code | Mkt cap | Implied share | Leader score | 5x prob |
|---|---|---|---|---|---|
| 绿的谐波 | 688017.SH | ~$1.5B | 5% | 6 (high-risk high-reward) | 30% |
| 双环传动 | 002472.SZ | ~$3.5B | 7% | 8 | 30% |
| 鸣志电器 | 603728.SH | ~$1.5B | < 5% | 7 | 25% |
| 汉宇集团 | 300403.SZ | ~$0.5B | < 3% | 5 (high-risk) | 20% |

**Watch:** Tesla Optimus production timing, Figure / 1X funding, Harmonic Drive pricing.

### 7.2 Sector 2: NdFeB Rare-Earth Magnets — 稀土永磁

<a id="sector-ndfeb"></a>

**TAM:** Global ~$8B (2025) → ~$30B (2030), 3.7x
**Drivers:**
- Robot motors (2-5 kg/unit) + EV (2-3 kg/unit) + wind (high-volume)
- China supplies 90% of global; West must rebuild supply chain (geopolitical premium)

**Why under-priced:**
- "Rare-earth = Chinese resource" miss; real value is **magnet processing**
- Global CR5 only 30-35%; fragmented
- Most players $1-3B mkt_cap, far from ceiling

**Targets:**

| Target | Code | Mkt cap | Implied share | Leader score | 5x prob |
|---|---|---|---|---|---|
| 金力永磁 | 300748.SZ | ~$2.5B | 10% | 10 | 30% |
| 中科三环 | 000970.SZ | ~$2B | 8% | 9 | 25% |
| 宁波韵升 | 600366.SH | ~$1.5B | 6% | 8 | 25% |
| 正海磁材 | 300224.SZ | ~$1.5B | 6% | 8 | 25% |
| 北方稀土 | 600111.SH | ~$15B | 50% | 11 | 25% (upstream, not magnet) |
| MP Materials | MP.US | ~$30B | ~30% | 12 (US-only) | 25% |

**Watch:** NdFeB magnet prices per ton, robot production timing, China rare-earth export controls.

### 7.3 Sector 3: Power Transformers / UHV — 电力变压器/特高压

<a id="sector-transformer"></a>

**TAM:** Global ~$30B (2025) → ~$55B (2030), 2x
**Drivers:**
- AI data-center power surge (2025: 200 TWh → 2030: 900 TWh)
- Grid upgrade cycle
- EV charging infrastructure boom
- **Physical 2-3 year ramp; lead times extended from 30 weeks → 120+ weeks**

**Why under-priced:**
- Retail doesn't grok "transformer lead time = 120+ weeks"
- Pure physical bottleneck, cannot be solved by quick capacity add
- Yet market awareness is still low

**Targets:**

| Target | Code | Mkt cap | Implied share | Leader score | 5x prob |
|---|---|---|---|---|---|
| **特变电工** | 600089.SH | ~$9.5B | **14%** | **11** | **35%** |
| 思源电气 | 002028.SZ | ~$5B | 8% | 10 | 30% |
| 平高电气 | 600312.SH | ~$2.5B | 5% | 9 | 25% |

**Watch:** Global transformer lead times, US IRA / CHIPS Act power-equipment policy, AI hyperscaler data-center power.

### 7.4 Sector 4: SiC Power Devices — SiC 功率器件

<a id="sector-sic"></a>

**TAM:** Global ~$5B (2025) → ~$30B (2030), 6x
**Drivers:**
- AI server power (Si replacing SiC, +5-10% efficiency)
- EV 800V high-voltage platforms (require SiC)
- Solar / storage inverters fully SiC

**Why under-priced:**
- Fragmented players, most still small-cap
- Chinese vendors just breaking overseas tech blockade
- "AI + SiC" narrative not yet fully priced

**Targets:**

| Target | Code | Mkt cap | Implied share | Leader score | 5x prob |
|---|---|---|---|---|---|
| 斯达半导 | 603290.SH | ~$3.5B | 12% | 10 | 25% |
| 时代电气 | 688187.SH | ~$20B | 50% | 11 | 25% |
| 士兰微 | 600460.SH | ~$5B | 15% | 9 | 25% |
| 三安光电 | 600703.SH | ~$10B | 25% | 10 | 25% |
| Wolfspeed | WOLF.US | ~$5B | 10% | 7 (high-reward) | 30% |

**Watch:** Tesla / BYD 800V platform penetration, SiC substrate pricing, Wolfspeed financial health.

### 7.5 Sector 5: Advanced Packaging Equipment — 先进封装设备

<a id="sector-packaging"></a>

**TAM:** Global hybrid bonding ~$0.5B (2025) → ~$4B (2030), 8x
**Drivers:**
- HBM4 requires hybrid bonding (traditional bumps can't do it)
- Chiplet architecture rising
- 3D DRAM stacking

**Why under-priced:**
- Retail watches "HBM makers" (Micron), ignores "HBM equipment makers"
- Besi / ASMPT overseas already rallied
- Domestic equipment (北方华创 etc.) still at low levels

**Targets:**

| Target | Code | Mkt cap | Implied share | Leader score | 5x prob |
|---|---|---|---|---|---|
| 北方华创 | 002371.SZ | ~$30B | 30% | 11 | 25% |
| 中微公司 | 688012.SH | ~$20B | 25% | 11 | 25% |
| 拓荆科技 | 688072.SH | ~$5B | 15% | 9 | 25% |
| 华海清科 | 688120.SH | ~$5B | 15% | 9 | 25% |
| 长川科技 | 300604.SZ | ~$5B | 15% | 9 | 25% |

**Watch:** HBM4 production timing, hybrid bonding equipment localization, TSMC CoWoS capacity.

### 7.6 Sector 6: Specialty Industrial Gases — 特种工业气体

<a id="sector-gases"></a>

**TAM:** Global electronic specialty gas ~$5B (2025) → ~$12B (2030), 2.4x
**Drivers:**
- Wafer fab expansion needs lots of specialty gas (Ne, He, fluorides)
- Domestic fab localization accelerating
- AI chip manufacturing needs ultra-high-purity gas

**Why under-priced:**
- "Invisible but indispensable" sector
- Most retail don't know how much gas each wafer consumes

**Targets:**

| Target | Code | Mkt cap | Implied share | Leader score | 5x prob |
|---|---|---|---|---|---|
| 金宏气体 | 688106.SH | ~$1.5B | 10% | 8 | 20% |
| 华特气体 | 688268.SH | ~$1.5B | 10% | 9 | 25% |
| 雅克科技 | 002409.SZ | ~$4B | 25% | 10 | 25% |
| 南大光电 | 300346.SZ | ~$3B | 20% | 9 | 25% |
| 凯美特气 | 002549.SZ | ~$1B | 5% | 7 | 20% |

**Watch:** Domestic fab construction progress, Kr / Xe / Ne prices, US export controls on specialty gas to China.

---

## 8. Monthly Monitoring Checklist — 月度监控清单

<a id="monthly-checklist"></a>

(15 minutes, run on the 1st of each month)

### 8.1 AI Demand-Source Indicators

```
□ 1.  US 10-yr Treasury yield (< 4% bullish, > 4.5% alert)
□ 2.  US hyperscaler quarterly capex guide (growth < 30% = warning)
□ 3.  NVIDIA data-center revenue YoY (growth < 40% = warning)
□ 4.  US EIA data-center power consumption (slowing = warning)
□ 5.  Global AI token consumption (MoM)
□ 6.  OpenAI / Anthropic ARR growth
□ 7.  Anthropic valuation (strategic-partner watchlist)
```

### 8.2 Storage / Semiconductor Indicators

```
□ 8.  DRAMeXchange HBM spot price (turning down = cycle peak)
□ 9.  Three memory makers (Micron / SK Hynix / Samsung) relative moves
□ 10. LightCounting optical-module shipment data
□ 11. TSMC CoWoS utilization
□ 12. Global wafer fab construction progress
□ 13. HBM long-term agreement progress (Micron / SK Hynix)
```

### 8.3 Physical-Bottleneck Indicators

```
□ 14. Global transformer lead times (track through 2027)
□ 15. US gas-turbine order backlog (GE Vernova / Siemens)
□ 16. US EIA new data-center installed capacity
□ 17. NdFeB magnet price per ton
□ 18. Kr / Xe / Ne specialty-gas prices
□ 19. Rare-earth export control policy changes
```

### 8.4 Risk Indicators

```
□ 20. DeepSeek moment (model efficiency breakthrough)
□ 21. China hyperscaler capex growth (China demand)
□ 22. Apple / Tesla / BYD end-demand signals
□ 23. Fed rate policy direction
□ 24. US-China chip control escalation / easing
□ 25. Taiwan Strait / Middle East geopolitical risk
```

---

## 9. Threshold Triggers — 关键观察指标阈值

<a id="thresholds"></a>

### 9.1 Demand-Side "Peak" Signals (any → trim 50%)

- ❌ Any hyperscaler capex growth drops from +50% to < +25%
- ❌ NVIDIA data-center revenue YoY drops from +80% to < +40%
- ❌ DeepSeek-class event repeats (model efficiency 5x)
- ❌ Anthropic / OpenAI publicly acknowledge "investment cycle too long"

### 9.2 Supply-Side "Loosening" Signals (any → trim 30%)

- ❌ Samsung HBM4 yield breaks 70% + wins NVIDIA Vera Rubin main supply
- ❌ CXMT / YMTC HBM breakthrough
- ❌ Micron guidance "slightly below consensus" (not blowout)
- ❌ Any memory maker announces major capex expansion (>20% increment)

### 9.3 Valuation "Danger" Signals (any → full exit)

- ❌ Forward PE breaks 95% historical percentile
- ❌ Sell-side target dispersion > 70%
- ❌ Implied share breaks 80% (approaching industry ceiling)
- ❌ Priced-in ratio > 2.0

---

## 10. Operating Discipline — 操作纪律

<a id="discipline"></a>

### 10.1 Position-Building Discipline

1. **Stagger entries:** any target built over 3-6 months; single buy ≤ 25% of target position
2. **Wait for pullback:** don't chase after > 50% rally; wait for MA50 or implied share < 50%
3. **Reserve 20% cash:** never all-in; black-swan needs dry powder

### 10.2 Holding Discipline

1. **Quarterly review:** re-evaluate leader-score + priced-in every quarter
2. **Hard stop:** single target drawdown 30% → cut half; 50% → full exit
3. **Rebalance:** semi-annually; trim winners, add to losers

### 10.3 Sell Discipline

1. **Fundamentals deteriorate:** leader score drops 3+ → trim
2. **Valuation extreme:** priced-in > 1.5 → start trimming
3. **Target achieved:** 5x / 10x target hit → lock in partial profit
4. **Opportunity cost:** better target found (higher priced-in reverse opportunity)

### 10.4 T-Framework-Specific

- **T-3 埋伏:** Best ambush window, scale up aggressively
- **T+0 加仓:** Main wave, full hold
- **T+6 跑路:** Mandatory trim, regardless of "story continues" narrative
- **T+12 清仓:** Hard exit, even if still bullish

---

## 11. Stock Ranking — 最终投资清单排序

<a id="ranking"></a>

### 11.1 Heavy (🥇)

| Target | Code | Sector | Leader | Priced-in | PEG (估) | Verdict |
|---|---|---|---|---|---|---|
| **特变电工** | 600089.SH | Transformer | 11 | 0.56 | ~0.7 ✓ | 🥇 **Heavy** |
| **MP Materials** | MP.US | Rare-earth | 12 | 0.7 | ~1.2 ✓ | 🥇 **Heavy** |

### 11.2 Standard (🥈)

| Target | Code | Sector | Leader | Priced-in | PEG (估) | Verdict |
|---|---|---|---|---|---|---|
| 金力永磁 | 300748.SZ | Rare-earth | 10 | 0.56 | ~0.6 ✓ | 🥈 Standard |
| 思源电气 | 002028.SZ | Transformer | 10 | 0.6 | ~1.3 ✓ | 🥈 Standard |
| 北方华创 | 002371.SZ | Packaging equip | 11 | 0.85 | ~1.3 ✓ | 🥈 Standard |
| 中微公司 | 688012.SH | Packaging equip | 11 | 0.85 | ~1.5 ✓ | 🥈 Standard |
| 双环传动 | 002472.SZ | Robot | 8 | 0.8 | ~2.0 ⚠️ | 🥈 Standard (PEG 警示) |
| 时代电气 | 688187.SH | SiC | 11 | 1.0 | ~1.5 ✓ | 🥈 Standard |

### 11.3 Light / Wait (🥉)

| Target | Code | Sector | Leader | Priced-in | PEG (估) | Verdict |
|---|---|---|---|---|---|---|
| 绿的谐波 | 688017.SH | Robot | 6 | 0.8 (乐观) | ~2.7 ✗ | 🥉 Light (**PEG 严重警示, 实战再降一档 ⚠️**) |
| 中科三环 | 000970.SZ | Rare-earth | 9 | 0.7 | ~1.0 ✓ | 🥉 Light |
| 斯达半导 | 603290.SH | SiC | 10 | 0.8 | ~1.3 ✓ | 🥉 Light |
| 雅克科技 | 002409.SZ | Specialty gas | 10 | 0.9 | ~1.2 ✓ | 🥉 Light |
| Wolfspeed | WOLF.US | SiC | 7 | 0.8 | N/A (亏损) | 🥉 Light (PEG 不可计算) |
| 华海清科 | 688120.SH | Packaging equip | 9 | 0.9 | ~1.5 ✓ | 🥉 Light |

**PEG-divergence flag (key insight):** 688017 绿的谐波 shows the classic case where Priced-in 0.8 (looks fair) BUT PEG 2.7 (looks very expensive). Per §2.5 conflict rule, **trust PEG** → 实战降级到 ⚠️ 观望, 不是 🥉 轻仓。

### 11.4 Skip / Trim (❌)

| Target | Code | Reason |
|---|---|---|
| Micron (MU) | MU.US | Priced-in 10.7x, extreme over-valuation |
| 中际旭创 | 300308.SZ | Over priced-in (implied share 67%) |
| GE Vernova | GEV.US | Priced-in 1.1x, expensive |
| Arm Holdings | ARM.US | Priced-in 0.36x fair but already rallied |

---

## 12. Excluded List — 排除清单

<a id="excluded"></a>

### 12.1 Already Over-Priced-In Sectors

| Sector | Target | Priced-in | Verdict |
|---|---|---|---|
| HBM | Micron | 10.7x | ❌ Skip |
| Optical modules | 中际旭创 | 1.49 | ⚠️ Light or wait |
| Liquid cooling | Vertiv | 1.5+ | ⚠️ Wait |
| AI Cloud | CoreWeave | 1.8+ | ❌ Skip |

### 12.2 TAM Too Small (Cannot Reach $1T Mkt Cap)

| Sector | TAM 2030 | Max possible mkt cap | Verdict |
|---|---|---|---|
| HBM EMC materials | $0.5B | $5B | ❌ TAM too small |
| Low-α spherical silica | $0.2B | $2B | ❌ |
| GaN power devices | $2B | $20B | ⚠️ Small-cap |
| Photoresist | $1B | $10B | ❌ |
| HBM test equipment | $1.2B | $12B | ❌ |
| TSV hybrid-bonding equipment | $1.5B | $15B | ⚠️ Small-cap |

### 12.3 No Real Orders ("Concept Stocks")

Any "AI concept stock" without specific customers / orders / capacity is excluded regardless of narrative.

---

## 13. 5x / 10x Portfolio Configurations — 配置方案

<a id="portfolio"></a>

**Assumption:** Principal 1M RMB, 5-year 5x target.

### 13.1 Plan A: Steady (higher hit rate)

```
40% — Large-cap AI leaders (NVIDIA, MSFT, TSMC)
       Target 2-3x → contribute 0.8-1.2x
30% — Mid-cap AI beneficiaries (GE Vernova, 特变电工, Arm)
       Target 3-5x → contribute 0.9-1.5x
20% — High-elasticity themes (MP Materials, Cameco, 北方稀土)
       Target 5-10x → contribute 1.0-2.0x
10% — Cash / Defensive (UST, staples)
       Target 1.1x → contribute 0.11x
─────────────────────────────────────
Portfolio target: 2.8-4.8x
```

### 13.2 Plan B: Aggressive (pursuing 5x+)

```
30% — Mid-cap high-conviction (特变电工, Arm)
       Target 5x → contribute 1.5x
30% — High-elasticity themes (MP Materials, Cameco, Bloom Energy)
       Target 10x → contribute 3.0x
20% — Small-cap breakthrough (NuScale, Energy Fuels, 华友钴业)
       Target 5-10x → contribute 1.0-2.0x
20% — Large-cap anchor (NVDA, TSMC)
       Target 2-3x → contribute 0.4-0.6x
─────────────────────────────────────
Portfolio target: 5.9-7.1x
```

### 13.3 Plan C: Single Best Target (lottery)

**If only one: 特变电工 (600089.SH)**

Reasons:
- Lowest valuation (priced-in 0.56x)
- Hardest chokepoint (physical bottleneck)
- Smallest rally (not started yet)
- Highest demand certainty (AI data-center power)
- 5x space → $47.5B mkt cap, reasonable

---

## 14. Appendix — 附录

<a id="appendix"></a>

### 14.1 Valuation Formulas — 估值公式

```
Forward PE = current price / next-12-month EPS
PEG        = Forward PE / EPS growth (%)
Implied share = current mkt_cap / (TAM × net margin × reasonable PE)
Priced-in ratio = implied share / leader-achievable share
Implied growth rate = (current Forward PE / reasonable PE)^(1/n) - 1
```

### 14.2 Leader-Score Formula — 龙头评分公式

```
Leader score = current share + tech lead + customer bind + capacity expand
             (0-5)        (0-3)       (0-3)          (0-3)
             Total 14; ≥ 11 = real leader
```

### 14.3 Decision Formula — 决策公式

```
Decision = f(chokepoint, TAM growth, leader score, priced-in)
         = f(⭐⭐⭐⭐+, 5x+, ≥ 11, < 0.8)
```

### 14.4 T Algorithm Pseudocode — T 算法伪代码

```
function T_position(event_date, today):
    days_diff = (event_date - today).days
    return -days_diff / 30.0   # negative = future

function determine_phase(T):
    if T <= -12:  return T-12
    elif T <= -9: return T-9
    elif T <= -6: return T-6
    elif T <= -3: return T-3
    elif T <= -1: return T-1
    elif T <= 0.5: return T+0
    elif T <= 3:  return T+3
    elif T <= 6:  return T+6
    elif T <= 9:  return T+9
    else:         return T+12

function action_for_phase(phase):
    # see §4.3 table — return string
    ...

function locate_2d_cell(T_phase, priced_in_bucket):
    # see §5 9-cell matrix — return action emoji + verb
    ...

function two_value_decision(event, priced_in):
    T = T_position(event.event_date, today)
    phase = determine_phase(T)
    bucket = bucketize(priced_in)  # <0.5 / 0.5-0.8 / 0.8-1.2 / 1.2-2.0 / >2.0
    return locate_2d_cell(phase, bucket)
```

### 14.5 Glossary — 术语表

| Term | English | Definition |
|---|---|---|
| TAM | Total Addressable Market | Total addressable market size |
| 卡点 | Chokepoint | Irreplaceable link in supply chain |
| Priced-in | Priced in | Already reflected in price |
| Forward PE | Forward Price-to-Earnings | P/E using next-12-month EPS |
| PEG | PE to Growth | PE / growth ratio |
| Implied Growth Rate | Implied Growth Rate | Growth rate implied by current valuation |
| Equity Duration | Equity Duration | Sensitivity to long-term expectations |
| Terminal Value | Terminal Value | DCF year-10+ value |
| RPO | Remaining Performance Obligations | Backlog / long-term commitments |
| SCAs | Strategic Customer Agreements | Long-term strategic customer deals |
| MR-MUF | Mass Reflow-Molded UnderFill | HBM packaging (SK Hynix-led) |
| CoWoS | Chip-on-Wafer-on-Substrate | TSMC advanced packaging |
| HBM | High Bandwidth Memory | High-bandwidth memory |
| ASIC | Application-Specific IC | Application-specific integrated circuit |
| SMR | Small Modular Reactor | Small modular reactor |
| NdFeB | Neodymium Iron Boron | Sintered NdFeB magnet |
| SiC | Silicon Carbide | Silicon carbide power device |
| GaN | Gallium Nitride | Gallium nitride power device |
| TSV | Through-Silicon Via | Through-silicon via (advanced packaging) |
| EUV | Extreme Ultraviolet | EUV lithography |

### 14.6 Version History — 版本历史

- **2026-06-26 v2.1:** Added §2.5 PEG Ratio (EPS-based valuation sanity check). Added PEG downgrade rule to §3 Decision Matrix (PEG > 2.0 → tier down). PEG column added to §11 ranking tables. Formula + parameters now displayed ABOVE bucket tables (per UX request).
- **2026-06-26 v2.0:** Merged INVESTMENT-FRAMEWORK.md + t-framework-implementation.md into this single `docs/analysis-framework.md`. Added §5 2D matrix (T × Priced-in). Removed Python code skeleton (the `t_framework/` package never existed in the repo; treated as design notes). English-only anchor IDs for cross-tool compatibility. Briefly added `/t-twovalue` skill, then removed in v2.1 (user feedback: not useful).
- **2026-06-25 v1.0:** Initial framework. Four-Questions + 4-dimension Leader Score + Priced-in framework. 6 under-priced sectors. 25-item monthly monitoring checklist.
- **2026-06-30 v2.2:** Added §15 Buffett Six-Gate Checklist (from 通用交叉验证框架 investment-checklist skill). Added §16 Bottleneck Hunter Framework (from 通用交叉验证框架 bottleneck-hunter skill). Added `/t-checklist` and `/t-bottleneck` slash commands.

---

> This document is for framework reference only. **Verify all data independently.**
> **Markets carry risk; invest prudently. This is not investment advice.**

---

## 15. Buffett Six-Gate Checklist — 巴菲特六关买入 Checklist

<a id="checklist"></a>

> 来源：通用交叉验证框架 `investment-checklist` skill。目标是**排除坏选择**，不是找最好的。
> 5句话说不完整 = 不买，没有例外。

### 15.1 快速否决清单（任意一条触发 → 直接否决）

- [ ] 说不清楚这家公司怎么赚钱
- [ ] 连续3年自由现金流为负且看不到改善
- [ ] 管理层有诚信污点
- [ ] 竞争优势正在被不可逆侵蚀
- [ ] 需要靠"下一个接盘者出更高价"来赚钱（博傻）
- [ ] 买入理由主要是"别人都在买"或"最近涨得好"
- [ ] 无法用200字以内写清楚买入理由

### 15.2 六关评分表

| 关卡 | 核心问题 | ★1-5 | 否决线 |
|--|--|--|--|
| **一关：能力圈** | 能用一句话说清楚怎么赚钱？10年后大概率还在做什么？ | ★? | ★1 = 直接否决 |
| **二关：好生意** | ROE>15%？毛利率>40%？FCF持续为正？轻资产？负债<3年净利？ | ★? | 3条不达标 = ★1-2 |
| **三关：护城河** | 品牌/转换成本/网络效应/规模/技术——有没有？在变宽还是变窄？ | ★? | 正在被侵蚀 = ★1 |
| **四关：管理层** | 诚实度？资本配置记录？持股？所有者心态？治理？ | ★? | 诚信污点 = 直接否决 |
| **五关：安全边际** | PEG < 1.5？DCF L/可达利润 < 1.0？ | ★? | ★1 = 不买 |
| **六关：决策纪律** | 停牌5年能接受吗？镜子测试5句话能说清楚吗？ | ✅/❌ | ❌ = 不买 |

### 15.3 镜子测试（必填）

```
"我以 ___ 元买入 ___ 公司，因为：
1. 这门生意的本质是___，我理解它；
2. 它的护城河是___，而且在变宽/变窄；
3. 管理层___，值得/不值得信赖；
4. 当前价格相当于内在价值的___折，有/无足够安全边际；
5. 即使我错了，下行风险可控/不可控，因为___。"
```

### 15.4 与 Mavis 现有框架的整合

| Mavis 框架 | Checklist 对应 |
|--|--|
| 卡点 ⭐ | 三关护城河 |
| 龙头评分 N/14 | 二关好生意 + 三关护城河 |
| PEG + DCF L | 五关安全边际 |
| T框架 | 六关决策纪律（T位置决定加仓节奏）|

**使用方法**：`/t-analyze` 输出后，对 🥇🥈 标的额外执行 `/t-checklist` 六关复核，确保没有遗漏的红线。

---

## 16. Bottleneck Hunter — 瓶颈猎手框架

<a id="bottleneck"></a>

> 来源：通用交叉验证框架 `bottleneck-hunter` skill。
> 核心理念：不问"AI推荐什么股票"，问**"如果这个趋势继续扩张，哪一环会先不够用？"**

### 16.1 供应链四层拆解

```
Layer 1（核心）：已充分定价，alpha有限
   ↓ 关注度低，alpha集中区
Layer 2（子组件/材料）：支撑核心组件的零部件和材料  ← 重点
Layer 3（上游设备/原料）：制造子组件所需的设备和原材料  ← 重点
Layer 4（基础设施）：电力、冷却、土地、认证
```

**与 Mavis 产业链文档的对应：**
- (8-24 删 chain-*.md, 文档已 archive)

### 16.2 瓶颈判定6条标准

| # | 标准 | 🔴严重 | 🟡中等 | 🟢轻微 |
|--|--|--|--|--|
| 1 | 供给集中度 | ≤2家供应商 | 3-5家 | >5家 |
| 2 | 扩产周期 | >2年 | 1-2年 | <1年 |
| 3 | 替代难度 | 不可替代 | 部分可替代 | 易替代 |
| 4 | 产能利用率 | >90% | 70-90% | <70% |
| 5 | 需求增速 | >50%/年 | 20-50% | <20% |
| 6 | 客户验证周期 | >1年 | 6-12月 | <6月 |

**瓶颈评级**：
- 🔴×4+ → **S级**（单点故障，最高优先级）
- 🔴×3 → **A级**（严重受限）
- 🔴×1-2 → **B级**（有压力但可控）

### 16.3 估值门槛（瓶颈真实 ≠ 投资机会）

| 信号 | 含义 | 行动 |
|--|--|--|
| PS > 30x 且收入增速 < 100% | 估值透支 | 信号强度上限 ★★ |
| 市值 > TAM 20% | 增长预期过度内化 | 标注 ⚠️ 估值透支 |
| 亏损 + PS > 15x | 需要解释盈利路径 | 降级到 ★★★ |
| PS < 10x 且收入增长 | 估值具有安全边际 | 信号强度可 +1级 |

**10年退出检验**：以当前市值买入，10年后25x PE退出，年化回报 < 10% → 无安全边际。

### 16.4 AI基础设施瓶颈地图（当前版本）

```
S级瓶颈：
  光模块激光器（EML/CW）— 全球供应商<3家，扩产周期2年+
  InP/GaAs衬底 — 铟镓资源稀缺，供给极度集中
  CoWoS先进封装 — 台积电垄断，扩产滞后

A级瓶颈：
  HBM内存 — SK海力士62%份额，三星急剧下滑
  高速光模块（800G/1.6T）— 中际旭创A股最强，但已充分定价
  大型变压器 — 交期2-4年，特变电工受益（已在 §11 重仓）

B级瓶颈：
  半导体设备（国产替代）— 北方华创/中微，景气周期中
  液冷系统 — Vertiv全球第一，但已偏贵
```

### 16.5 与 Mavis 现有框架整合

`/t-bottleneck <趋势>` 命令执行流程：
1. 拆解趋势的 Layer 1-4 供应链
2. 对每个环节用16.2的6条标准评级
3. S/A级瓶颈 → 找上市公司 → 过16.3估值门槛
4. 输出瓶颈机会排名表（格式与 `/t-analyze` 兼容）
5. 自动更新 `data/sectors.json` 中对应板块的成分股

**关键原则**：第二层、第三层瓶颈（不是Layer 1龙头）才是 alpha 集中区。Layer 1 大多已被充分定价。


