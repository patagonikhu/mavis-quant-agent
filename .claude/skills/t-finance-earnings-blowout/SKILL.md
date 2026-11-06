---
name: t-finance-earnings-blowout
description: Earnings Blowout 财季炸裂扫描 (v6.3.0). 5 闸 prefilter (ST/周期股/市值/上市/EBIT 预警) 跑在 3 rule (主路径/反转/净利暴增) 之前. 0 网络, 走 DataStore. 触发词: "业绩反转"、"Earnings Blowout"、"10x 票".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)
> 📐 **命名规范: [`handbook/naming-convention.md`](../../handbook/naming-convention.md)** (7 条硬规则, 5 项 grep 验证)

> **单一事实源:** 本 skill 全部"v6.3.0 干啥的 / prefilter 5 闸 / 4 基础条件 / 3 rule OR / 默认阈值 / 输出"在 `tools/batch/finance_earnings_blowout.py` 的模块顶部 docstring + `--help` 完整说明。SKILL.md 只保留"啥时候跑 + 跑最小例子"。

## 啥时候跑 (季报驱动)

| 季报 | 截止 | 跑窗口 |
|---|---|---|
| Q4 (12-31) | 4-30 次年截止 | **1 月** |
| Q1 (3-31) | 4-30 截止 | **5 月** |
| Q2 (6-30) | 8-31 截止 | **9 月** |
| Q3 (9-30) | 10-31 截止 | **11 月** |

**一周/一天跑 1 次无新增价值** — 季报数据每季才更新一次。

## 跑

```bash
# 默认 (5 闸 prefilter + 3 rule OR, 0 网络, ~30 秒全市场 13 季)
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout

# 看所有 flag + 默认值 + 帮助 (单一事实源)
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --help

# 收紧 4 基础条件 + 看净利跳升 Top N
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout \
    --rev-yoy 30 --np-yoy 80 --top-np-jump 200

# 只看龙头 (or/np_yoy >= 80%, 含持续高增方向)
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --jump-mode leader

# 仅 dry-run (看同步计划, 不写 watchlist.json)
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --dry-run
```

> 默认 prefilter 全开 (ST / 周期股 / 市值 < 30亿 / 上市 < 16 季 / EBIT 双闸预警) → 自动写 `config/watchlist.json` (`list_type=blowout`)。

> ⚠️ **2026-11-06 起: blowout 段退场直接删** (本季不再命中 = 候选池淘汰, 整条从 watchlist.json 移除)。
> 11-02 曾改成"降级为自选 + `<季度>-retired` tag 保留", 结果 123 只历史票混进自选池被 `/t-analyze --all` 白写一遍详报 (143 份里 123 份是这种票) —— 已回退。
> `持仓`/`自选` 段是用户配置, 本流程**不碰**; 只有 `list_type=blowout` 段会被自动增删。

## 数据特性

- **0 网络**, 走 `DataStore.load_all_financials()` (financials parquet)
- 全市场 13 季 5555 只 ~30 秒 (内存 pandas, 0 网络)
- **不替代 `/t-analyze`**:仅命中 + 18 列全中文 md;详查看 `/t-analyze <code>`
- **必设 -15% 止损 / +50% 止盈** (R3 抓启动期, 票波动大)
- 缺数据报错 "请先 `/t-sync-data --financials`"

## 🚫 本 skill 不做什么 (越界 = 违规)

> **用户说 `/t-finance-earnings-blowout` = 只做财季炸裂扫描。** 跑完就停, 汇报命中, 结束。
> "SKILL.md 没禁止" ≠ "可以做" —— 详见 CLAUDE.md「Skill 越界铁律」。

| ❌ 别做 | 为什么 |
|---|---|
| **不跑分析 / 逐只详报** | 命中名单看 stdout 或 `docs/earnings-blowout-watchlist.md` 就行, 别顺手给命中票各跑一份 `/t-analyze` |
| **不自己跑 sync** | 缺财报 → 报"请先 `/t-sync-data --financials`", 不自己拉 |
| **不改阈值 / prefilter / 代码** | 5 闸 + 3 rule 是 v6.3.0 固化的, 想调 → 报告给用户 |
| **不额外改 watchlist 的其他段** | 本 skill 只动 `list_type=blowout` 段 (增新命中 / 删退场)。`持仓`/`自选` 段是用户配置, **不碰** |
| **不重扫刚退场又符合条件的票** | 退场 = 当季不再满足条件。重新命中是**下季**的事, 不是本轮该做的 |

## 跑后验证

```bash
# 看最新季度命中
head -100 docs/earnings-blowout-watchlist.md

# 看生效过滤规则 (脚本自动 print 完整过滤总表)
bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --dry-run
```

## 相关

- `/t-analyze <code>` (命中后深挖)
- `/t-sync-data --financials` (跑前必跑)
