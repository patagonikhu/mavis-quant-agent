---
name: t-finance-earnings-blowout
description: Earnings Blowout 财季炸裂扫描 (v6.3.0 prefilter 重构). rev≥15% + np≥20% + 毛利率 (升 OR 跌幅≤5pp) + (jump≥30pp OR 龙头). 3 个独立 rule OR. **5 闸 prefilter 跑在 rule 之前** (ST / 周期股 / 市值 / 上市时间 / EBIT 预警). 0 网络, 走 DataStore. 触发词: "业绩反转"、"Earnings Blowout"、"10x 票".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 🕐 跑节奏 (财务类 — 季报驱动)

**最佳时机**: 季报披露高峰结束后跑

| 季报 | 截止 | 集中披露 | **跑窗口** |
|---|---|---|---|
| Q4 (12-31) | 4-30 次年截止 | 3-15 ~ 4-30 | **1 月** |
| Q1 (3-31) | 4-30 截止 | 4-15 ~ 4-30 | **5 月** |
| Q2 (6-30) | 8-31 截止 | 8-15 ~ 8-31 | **9 月** |
| Q3 (9-30) | 10-31 截止 | 10-15 ~ 10-31 | **11 月** |

**一周/一天跑 1 次无新增价值** — 季报数据每季才更新一次。

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout
    # 默认 A 方案 + 5 闸 prefilter (ST/周期股/市值/上市/EBIT), 位置过滤已删 (2026-09-23)

... --include-cycle        # 含周期股 (默认 prefilter 排除)
... --include-st           # 含 ST/*ST (默认 prefilter 排除)
... --include-junk         # 含垃圾股 (市值/上市, 默认 prefilter 排除)
... --ebit-floor -100      # 关 EBIT 单季崩盘预警 (ebit_crash)
... --ebit-peak-kill -100  # 关 EBIT 业绩见顶预警 (ebit_peak_down)
... --jump-mode reverse    # 只看反转 (净利跳升 30pp)
... --jump-mode leader     # 只看龙头 (or/np_yoy>=80%)
... --dry-run              # 只 print 同步计划, 不写 watchlist.json
... --no-sync-watchlist    # 关 watchlist 自动同步
... --limit 10             # Top 10
... --rev-yoy 25           # 临时收紧营收门槛
... --np-yoy 30            # 临时收紧净利门槛
... --gm-tol 2             # 临时收紧毛利率容忍
... --min-mv 50            # 临时收紧市值下限 (亿, 默认 30)
... --min-listing-q 24     # 临时收紧上市时间 (季, 默认 16)
```

## 关键约束

- **0 网络**, 走 `DataStore.load_all_financials()` (financials parquet)
- **3 rule OR 策略**: 任意一个 rule 过即命中
- **5 闸 prefilter** (v6.3.0 新增, 2026-09-24): 跑在 3 rule 之前, 一次性排除 ST/周期股/小盘/新股/业绩见顶
- **必设 -15% 止损 / +50% 止盈** (R3 抓启动期, 票波动大)
- **缺数据**: 报"请先 /t-sync-data --financials"
- **自动同步 watchlist.json** (blowout 段): 默认开, `--no-sync-watchlist` 关

## 输出

- `docs/earnings-blowout-watchlist.md` (按季分 section)
- 自动同步 `config/watchlist.json` (list_type=blowout): 加新命中 / 删退场

## Hit Pipeline (v6.3.0 2026-09-24 重构)

```
load financials (13 季) ──▶ add_lags (4 季 prev) ──▶ PRE-FILTER 5 闸 ──▶ 3 rule OR ──▶ 输出
                                                                ↑
                                                    ① ST  (默认开, --include-st 关)
                                                    ② 周期股  (默认开, --include-cycle 关)
                                                    ③ 市值 < 30 亿  (默认开, --include-junk 关)
                                                    ④ 上市 < 16 季  (默认开, --include-junk 关)
                                                    ⑤ EBIT 预警 (ebit_crash 单季 < -50% + ebit_peak_down 4 季趋势 < -30%)
                                                       (--ebit-floor/-100 关 ebit_crash, --ebit-peak-kill/-100 关 ebit_peak_down)
```

**为什么 prefilter 跑在 rule 之前 (v6.3.0 关键改动):**

| 旧版 (v6.2.7-A) | 新版 (v6.3.0) |
|---|---|
| 周期股 + ST 过滤在 3 rule **之后** 跑 | **5 闸 prefilter 在 3 rule 之前** 跑, 13 季 5555 只全表扫, 0 网络 |
| 业绩杀票 (双林/华纬/富临/中熔/中科星图) 进 3 rule, 然后被规则放过 | **EBIT 预警 1 项剔除 100% 业绩杀样本** (披露日已知 EBIT 暴跌) |

**EBIT 预警设计 (核心):**
- **`ebit_crash` 单季崩盘**: 本季 EBIT / 上季 EBIT < 0.5 → 踢 (环比腰斩即踢). 抓披露日业绩腰斩票, **业绩杀 5/5 都能被这一项过滤**.
- **`ebit_peak_down` 见顶后下行**: 当前 EBIT < 4 季前 **AND** 4 季内任一季环比 < 0.7 → 踢. 抓"业绩高峰过后被杀"的双林型反弹票 (ebit_crash 抓不住).
- 默认 `ebit_crash`=-50%, `ebit_peak_down`=-30%. 关闭 `--ebit-floor -100` 或 `--ebit-peak-kill -100`.

**为什么不用总股本闸 (v6.3.0 简化):**
- DataStore.load_stock_basic 返回的列里**没有 total_share** (sync 注释说"用 daily_basic 兜底", 但实际没生效).
- ③ 市值已经能 95% 覆盖小盘股, 没必要再加股本闸.

## Hit Rules (3 independent rules, OR'd together)

Strategy pattern (`tools/batch/finance_earnings_blowout.py::_apply_rules`). To add a new rule: write a fn + append to `_RULES`.

| Rule | Conditions | Meaning |
|---|---|---|
| `_rule_main_path` (`rule_main_path`) | `or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & (reversal OR leader)` | Main path: 营收同比达标 + 净利同比达标 + 毛利率双稳 (环比 + 同比) + (反转 OR 持续龙头) |
| `_rule_reversal` (`rule_reversal`) | `or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & reversal` | Reversal: 净利同比跳升 ≥ 30pp + 4 基础条件 |
| `_rule_profit_surge` (`rule_profit_surge`) | `netprofit_yoy > 80 & gross_margin_qoq_stable & gross_margin_yoy_stable & (or_yoy >= revenue_floor)` | Profit surge: 净利暴增 + 毛利率双稳 (营收可放宽, `--profit-surge-revenue-floor` 设阈值) |

**`Final mask = rule_main_path | rule_reversal | rule_profit_surge`** — 任意一个 rule 过即命中

### 派生 Flag (业务名)

| Business name | Definition |
|---|---|
| `or_yoy_meet` | `or_yoy >= 15` (营收同比达标, 2026-09-23 从 25 放宽) |
| `netprofit_yoy_meet` | `netprofit_yoy >= 20` (净利同比达标, 2026-09-23 从 50 大放宽) |
| `gross_margin_qoq_stable` | `grossprofit_margin > prev` OR `|gm - prev| <= 5` (毛利率环比升或跌幅 ≤ 5pp, 2026-09-23 从 2 放宽) |
| `gross_margin_yoy_stable` | `grossprofit_margin > prev4` OR `|gm - prev4| <= 5` (毛利率同比升或跌幅 ≤ 5pp) |
| `reversal` | `(netprofit_yoy - netprofit_yoy_prev) >= 50` (净利同比跳升 ≥ 50pp) |
| `leader` | `or_yoy >= 80 AND netprofit_yoy >= 80 AND grossprofit_margin > prev` (持续高增龙头) |
| `lead_revenue` | `or_yoy >= 80` (龙头分支 - 营收条件) |
| `lead_profit` | `netprofit_yoy >= 80` (龙头分支 - 净利条件) |
| `lead_margin` | `grossprofit_margin > prev` (龙头分支 - 毛利率条件) |
| `ebit_crash` | EBIT 单季环比 < -50% (披露日业绩腰斩即踢, prefilter 5a) |
| `ebit_peak_down` | EBIT 4 季趋势见顶 (当前 < 4 季前 AND 4 季内任一季 < -30%, prefilter 5b) |

## 参数速查 (2026-09-24 v6.3.0 当前默认)

| CLI | 默认 | 旧值 | 含义 |
|---|---|---|---|
| `--rev-yoy` | 15 | 25 | 营收同比门槛 |
| `--np-yoy` | 20 | 50 | 净利同比门槛 |
| `--np-jump` | 30 | 50 | 净利跳升门槛 (pp) |
| `--gm-tol` | 5 | 2 | 毛利率跌幅容忍 (pp) |
| `--profit-surge-floor` | 80 | 100 | OR 旁路净利门槛 (2026-09-24 改名: 旧 `--np-surge-floor`) |
| `--profit-surge-revenue-floor` | 0 | — | OR 旁路营收下限 (2026-09-24 改名: 旧 `--np100-rev-floor`) |
| `--tolerance` | 0 | — | 3 季单调回踩容忍 (pp) |

### Prefilter 5 闸 (v6.3.0 2026-09-24 新增)

| CLI | 默认 | 含义 |
|---|---|---|
| `--include-cycle` | False | 含周期股 (默认 prefilter 排除) |
| `--include-st` | False | 含 ST/*ST (默认 prefilter 排除) |
| `--include-junk` | False | 含垃圾股 (市值+上市, 默认 prefilter 排除) |
| `--min-mv` | 30 | 市值下限 (亿) |
| `--min-listing-q` | 16 | 上市时间下限 (季, 4 年) |
| `--ebit-floor` | -50 | EBIT 单季环比跌幅 > 50% 踢 (`ebit_crash`, 设 -100 关闭) |
| `--ebit-peak-kill` | -30 | EBIT 4 季趋势见顶踢 (`ebit_peak_down`, 设 -100 关闭) |

### Watchlist

| CLI | 默认 | 含义 |
|---|---|---|
| `--no-sync-watchlist` | False | 关 watchlist 同步 |
| `--dry-run` | False | watchlist 同步 dry-run |

> ⚠️ 2026-09-23 已删 `--no-position-filter` (位置过滤代码整段移除, 不可关)

## 相关

- `/t-analyze <code>` (命中后深挖)
- **`/t-finance-roc-ey`** (配套: 财务多维分析, 也是季报驱动)
- `/t-near-low` (5y 回撤超跌)
- **`/t-rsi6-tech`** (下游: 技术面过滤, RSI 双超卖)
- `/t-sync-data --financials` (跑前必跑)

## 命名沿革

- **v1: `/t-earnings-blowout`** (2026-08 起, 原名)
- **v2: `/t-finance-earnings-blowout`** (2026-09-18 改) — 加 `finance-` 前缀
- **v3 (2026-09-23)**: 3 rule OR 策略 + 放宽默认 (rev15/np20/jump30/gm5/np80) + 删位置过滤 + watchlist 自动同步
- **v6.3.0 (2026-09-24)**: **5 闸 prefilter 跑在 3 rule 之前** + EBIT 预警剔除业绩杀样本 (双林/华纬/富临/中熔/中科星图 5/5 全活) + 字段业务名去 c1/c2/c3a/c3b 缩写