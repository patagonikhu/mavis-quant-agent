---
name: t-finance-earnings-blowout
description: Earnings Blowout 财季炸裂扫描 (放宽版 v6.2.7-A). rev≥15% + np≥20% + 毛利率 (升 OR 跌幅≤5pp) + (jump≥30pp OR 龙头). 3 个独立 rule OR. 0 网络, 走 DataStore. 触发词: "业绩反转"、"Earnings Blowout"、"10x 票".
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
    # 默认 A 方案 + 周期股/ST 过滤 (位置过滤已删, 2026-09-23)

... --include-cycle        # 含周期股 (默认排除)
... --include-st           # 含 ST/*ST (默认排除)
... --jump-mode reverse    # 只看反转 (净利跳升 30pp)
... --jump-mode leader     # 只看龙头 (or/np_yoy>=80%)
... --dry-run              # 只 print 同步计划, 不写 watchlist.json
... --no-sync-watchlist    # 关 watchlist 自动同步
... --limit 10             # Top 10
... --rev-yoy 25           # 临时收紧营收门槛
... --np-yoy 30            # 临时收紧净利门槛
... --gm-tol 2             # 临时收紧毛利率容忍
```

## 关键约束

- **0 网络**, 走 `DataStore.load_all_financials()` (financials parquet)
- **3 rule OR 策略**: 任意一个 rule 过即命中
- **2 道过滤** (周期股 + ST), 位置过滤已删 (2026-09-23)
- **必设 -15% 止损 / +50% 止盈** (R3 抓启动期, 票波动大)
- **缺数据**: 报"请先 /t-sync-data --financials"
- **自动同步 watchlist.json** (blowout 段): 默认开, `--no-sync-watchlist` 关

## 输出

- `docs/earnings-blowout-watchlist.md` (按季分 section)
- 自动同步 `config/watchlist.json` (list_type=blowout): 加新命中 / 删退场

## Hit Rules (3 independent rules, OR'd together)

Strategy pattern (`tools/batch/finance_earnings_blowout.py::_apply_rules`). To add a new rule: write a fn + append to `_RULES`.

| Rule | Conditions | Meaning |
|---|---|---|
| `_rule_main_path` (`rule_main_path`) | `rev_growth & np_growth & gm_qoq_stable & gm_yoy_stable & (reversal OR leader)` | Main path: revenue growth + profit growth + gm stable (both QoQ/YoY) + (reversal OR persistent leader) |
| `_rule_reversal` (`rule_reversal`) | `rev_growth & np_growth & gm_qoq_stable & gm_yoy_stable & reversal` | Reversal: 净利 yoy jump ≥ 30pp + 4 base conditions |
| `_rule_np_surge` (`rule_np_surge`) | `netprofit_yoy > 80 & gm_qoq_stable & gm_yoy_stable & (or_yoy >= rev_floor)` | Profit surge: np 暴增 + gm stable (revenue may underperform, `--np100-rev-floor` to set threshold) |

**`Final mask = rule_main_path | rule_reversal | rule_np_surge`** — 任意一个 rule 过即命中

### Flag derivation (business name + short alias)

| Business name | Alias | Definition |
|---|---|---|
| `rev_growth` | `c1` | `or_yoy >= 15` (2026-09-23 从 25 放宽) |
| `np_growth` | `c2` | `netprofit_yoy >= 20` (2026-09-23 从 50 大放宽) |
| `gm_qoq_stable` | `c3a` | `grossprofit_margin > prev` OR `|gm - prev| <= 5` (gm QoQ up or drop ≤ 5pp, 2026-09-23 从 2 放宽) |
| `gm_yoy_stable` | `c3b` | `grossprofit_margin > prev4` OR `|gm - prev4| <= 5` (gm YoY up or drop ≤ 5pp) |
| `reversal` | `c_jump` | `(netprofit_yoy - netprofit_yoy_prev) >= 30` (np yoy jump ≥ 30pp, 2026-09-23 从 50 放宽) |
| `leader` | `c_leader` | `or_yoy >= 80 AND netprofit_yoy >= 80 AND grossprofit_margin > prev` (persistent leader) |

## 参数速查 (2026-09-23 当前默认)

| CLI | 默认 | 旧值 | 含义 |
|---|---|---|---|
| `--rev-yoy` | 15 | 25 | 营收同比门槛 |
| `--np-yoy` | 20 | 50 | 净利同比门槛 |
| `--np-jump` | 30 | 50 | 净利跳升门槛 (pp) |
| `--gm-tol` | 5 | 2 | 毛利率跌幅容忍 (pp) |
| `--np-surge-floor` | 80 | 100 | OR 旁路净利门槛 |
| `--np100-rev-floor` | 0 | — | OR 旁路营收下限 |
| `--tolerance` | 0 | — | 3 季单调回踩容忍 (pp) |
| `--include-cycle` | False | — | 含周期股 |
| `--include-st` | False | — | 含 ST/*ST |
| `--no-sync-watchlist` | False | — | 关 watchlist 同步 |

> ⚠️ 2026-09-23 已删 `--no-position-filter` (位置过滤代码整段移除, 不可关)

## 相关

- `/t-analyze <code>` (命中后深挖)
- **`/t-finance-roc-ey`** (配套: 财务多维分析, 也是季报驱动)
- `/t-near-low` (5y 回撤超跌)
- **`/t-rsi6-tech`** (下游: 技术面过滤, RSI + 放量)
- `/t-sync-data --financials` (跑前必跑)

## 命名沿革

- **v1: `/t-earnings-blowout`** (2026-08 起, 原名)
- **v2: `/t-finance-earnings-blowout`** (2026-09-18 改) — 加 `finance-` 前缀
- **v3 (2026-09-23)**: 3 rule OR 策略 + 放宽默认 (rev15/np20/jump30/gm5/np80) + 删位置过滤 + watchlist 自动同步