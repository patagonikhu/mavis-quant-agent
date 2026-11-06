# Investment Analysis Framework — 投资分析框架

> **Last updated:** 2026-11-02 (v3.1)
> **Scope:** A股 / 美股 / 港股 long-term investors
> **用途:** LLM 手动套框架的方法论参考, **不自动进 md 报告**。
>
> ⚠️ **已删除/禁止复活的模块:** `data/events.json` (v6.2 删) / T框架时间维度 / DCF L / PEG / OBV / fflow。
> 估值**只看 PE (静态) + ROC/EY/毛利率/杠杆**, 全用已审计财报, 不依赖预测值。

---

## 1. Four Questions (投资四问) — 买入决策

Before buying **any** target, answer these 4 questions. All four must be ✅.

1. **Chokepoint** — 是否是供应链不可替代的卡点?
2. **TAM** — 5 年市场成长空间够大吗?
3. **Leader** — 这家公司是真龙头吗?
4. **Priced-in** — 市场是否已经 price in 了上涨空间?

### 1.1 Chokepoint Strength — 卡点强度

| Dimension | ⭐⭐⭐⭐⭐ Super | ⭐⭐⭐⭐ Strong | ⭐⭐⭐ Medium | ⭐⭐ Weak |
|---|---|---|---|---|
| Substitutes | 0 | 1 weak | 1-2 partial | Many |
| Players | 1-3 | 3-5 | 5-8 | 10+ |
| Capacity ramp | 5+ yrs | 2-3 yrs | 1-2 yrs | < 1 yr |
| Demand elasticity | Zero (cut = stop) | Low | Medium | High |
| Customer binding | Exclusive / long-term | Main supplier | Multi-supplier | Replaceable |

### 1.2 TAM Discipline

```
Theoretical mkt_cap ceiling = TAM × industry net margin × reasonable PE
```

| Industry | 5-yr TAM growth | Notes |
|---|---|---|
| AI core (HBM/GPU) | 5-10x | Real high-growth |
| AI enablers (optics/cooling) | 4-6x | Real high-growth |
| Physical infra (power/transformer) | 3-5x | Hard bottleneck |
| Critical metals (rare-earth) | 4-6x | AI+robot dual-driver |
| Traditional (steel/auto) | 1.0-1.3x | **No big growth** |

### 1.3 Leader Score (0-14)

**Score must be ≥ 11 to be a "real leader."**

| Dimension | 5 pts | 3-4 pts | 2-3 pts | 0-1 pt |
|---|---|---|---|---|
| **Current market share** | > 30% | 10-30% | 3-10% | < 3% |
| **Tech leadership** | 2+ gen ahead | Parity | 1 gen behind | 2+ gen behind |
| **Customer binding** | Exclusive / Top-1 main | Multi Top-5 | 1-2 big customers | None |
| **Capacity expansion** | Ample + ramping | Ample | Tight | Surplus |

- **≥ 11:** Real leader
- **8-10:** Tier-2 (cautious)
- **< 8:** High-risk

---

## 2. Decision Matrix — 决策矩阵 (当前口径)

**硬约束 (与生产过滤链一致, 真源 `.claude/skills/t-wyckoff-a2m/SKILL.md`):**

| 闸 | 阈值 | 作用 |
|---|---|---|
| PE (静态) | ≤ 60 | 太贵直接出局 |
| ROE | > 5% | 专治低基数假增长 (全市场中位仅 2.29%) |
| 毛利率 | > 20% | 专治薄利行业 (全市场中位 24.28%) |
| 净负债/市值 | < 0.5 | PE 低但重债不算便宜 |
| 营收 yoy / 净利 yoy | > 0 | 质量闸 basic |

**盈利质量双轴 (生产口径, `tools/factors/valuation/factor_lib.py`):**
```
ROC = TTM EBIT / (NWC + FA)        EY = TTM EBIT / EV,  EV = 市值 + 净债务
```

| 组合 | 判断 |
|---|---|
| ROC 高 + EY 高 | 便宜的好公司 ✅ |
| ROC 高 + EY 低 | 好公司但不便宜 → 等价格 |
| ROC 低 + EY 高 | 便宜但盈利质量差 → 警惕 (可能价值陷阱) |
| ROC 低 + EY 低 | 出局 ❌ |

> ⚠️ 旧的 **"L / 可达利润" 轴 (DCF 隐含终局利润) 已随 DCF 一起删除, 禁止复活**。

---

## 3. Buffett Six-Gate Checklist — 巴菲特六关

**快速否决 (任意一条触发 → 直接否决):**
- 说不清楚这家公司怎么赚钱
- 连续3年自由现金流为负且看不到改善
- 管理层有诚信污点
- 竞争优势正在被不可逆侵蚀
- 买入理由主要是"别人都在买"或"最近涨得好"

**六关评分表:**

| 关卡 | 核心问题 | 否决线 |
|--|--|--|
| **一关：能力圈** | 能用一句话说清楚怎么赚钱？10年后大概率还在做什么？ | ★1 = 直接否决 |
| **二关：好生意** | ROE>5%？毛利率>20%？FCF持续为正？净负债/市值<0.5？ | 3条不达标 = ★1-2 |
| **三关：护城河** | 品牌/转换成本/网络效应/规模/技术——在变宽还是变窄？ | 正在被侵蚀 = ★1 |
| **四关：管理层** | 诚实度？资本配置记录？持股？所有者心态？ | 诚信污点 = 直接否决 |
| **五关：安全边际** | PE ≤ 60 且 ROC/EY 过关？ | ★1 = 不买 |
| **六关：决策纪律** | 停牌5年能接受吗？5句话能说清楚吗？ | ❌ = 不买 |

**镜子测试:**
```
"我以 ___ 元买入 ___ 公司，因为：
1. 这门生意的本质是___；
2. 护城河是___，在变宽/变窄；
3. 管理层___，值得/不值得信赖；
4. 当前价格相当于内在价值___折；
5. 即使我错了，下行风险可控/不可控，因为___。"
```

