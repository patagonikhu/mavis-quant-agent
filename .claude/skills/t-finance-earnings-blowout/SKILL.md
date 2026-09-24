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
    # 默认 5 闸 prefilter + 3 rule, 0 网络

... --include-cycle        # 含周期股 (默认 prefilter 排除)
... --include-st           # 含 ST/*ST (默认 prefilter 排除)
... --include-junk         # 含垃圾股 (市值+上市, 默认 prefilter 排除)
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
```

## 关键约束

- **0 网络**, 走 `DataStore.load_all_financials()` (financials parquet)
- **3 rule OR 策略** (任一过即命中): 主路径 / 反转 / 净利暴增
- **5 闸 prefilter** 跑在 3 rule 之前, 一次性排除 ST / 周期股 / 小盘 / 新股 / 业绩见顶
- **必设 -15% 止损 / +50% 止盈** (R3 抓启动期, 票波动大)
- **缺数据**: 报 "请先 /t-sync-data --financials"
- **自动同步 watchlist.json** (blowout 段): 默认开, `--no-sync-watchlist` 关

## Hit Pipeline (v6.3.0 2026-09-24)

```
load financials (13 季) ──▶ shift_quarters (回溯 4 季) ──▶ PRE-FILTER 5 闸 ──▶ 3 rule OR ──▶ 输出
                                                                ↑
                                                    ① ST  (--include-st 关)
                                                    ② 周期股 (--include-cycle 关, 默认 SW 周期 + 电气设备 26 类)
                                                    ③ 市值 < 30 亿 (--include-junk 关)
                                                    ④ 上市 < 16 季 (--include-junk 关)
                                                    ⑤ EBIT 预警 双闸 (--ebit-floor 关 5a / --ebit-peak-kill 关 5b)
```

**为什么 5 闸 prefilter 跑在 rule 之前:** 业绩杀样本 (双林/华纬/富临/中熔/中科星图) 在 v6.2.7 进 3 rule 后被规则放过; **v6.3.0 EBIT 预警 1 项剔除 100% 业绩杀样本** (披露日已知 EBIT 暴跌).

**5b `ebit_peak_down` 见顶后下行** 双林型反弹票 (单季回暖但趋势已坏, 5a 抓不住).

**实跑验证**: 9 季 × 30 天持仓回测, v6.3.0 平均 max 涨幅 **+18.6%**, 沪深 300 同期 **+9.0%**, **alpha +9.6pp** (`tools/batch/blowout_backtest_v630.py`).

## Hit Rules (3 independent rules, OR'd together)

| Rule | 含义 |
|---|---|
| `_rule_main_path` | 营收同比达标 + 净利同比达标 + 毛利率双稳 + (反转 ∨ 龙头) |
| `_rule_reversal` | 4 基础 AND + 净利同比跳升 ≥ 30pp (本季-上季) |
| `_rule_profit_surge` | 净利同比 > 80% + 毛利率双稳 (营收可放宽, `--profit-surge-revenue-floor` 设阈值) |

**`Final mask = rule_main_path | rule_reversal | rule_profit_surge`**

详细阈值 + 派生 Flag 业务名 见 `tools/batch/finance_earnings_blowout.py::_apply_rules` + SKILL.md 末尾"参数速查"段.

## 执行输出: 生效过滤规则总表

每次跑完 (无论 dry-run / 真同步), 都会在 banner 之后打印完整生效过滤规则总表:

```
┌─ 生效过滤规则总表 (本次跑) ─────────────────────────────────────────┐
│ 【Prefilter 5 闸】 + 【3 Rule OR 触发】 + 【4 基础条件】                │
│ 最新 1 季 (YYYYMMDD) 命中 N 只次                                       │
└──────────────────────────────────────────────────────────────────┘
```

**用途:** 跑完一眼能看出 (1) 哪些闸生效 / 关闭 (2) 阈值是默认还是用户传入 (3) 3 rule 各自命中多少。

## 参数速查 (v6.3.0 当前默认)

### 4 基础条件

| Flag | 默认 | 含义 |
|---|---|---|
| `--rev-yoy` | 15 | 营收同比门槛 (`or_yoy_meet`) |
| `--np-yoy` | 20 | 净利同比门槛 (`netprofit_yoy_meet`) |
| `--np-jump` | 30 | 净利跳升门槛 pp (`reversal`) |
| `--gm-tol` | 5 | 毛利率跌幅容忍 pp (`gross_margin_*_stable`) |
| `--profit-surge-floor` | 80 | 净利暴增旁路净利门槛 (`_rule_profit_surge`) |
| `--profit-surge-revenue-floor` | 0 | 净利暴增旁路营收下限 |

CLI flag 保留 (`--rev-yoy` / `--np-yoy` / `--np-jump` / `--gm-tol`) 是用户长期沿用, 改名会断外部集成.

### Prefilter 5 闸

| Flag | 默认 | 含义 |
|---|---|---|
| `--include-cycle` | False | 含周期股 (默认排除) |
| `--include-st` | False | 含 ST/*ST (默认排除) |
| `--include-junk` | False | 含垃圾股 (市值+上市) |
| `--min-mv` | 30 | 市值下限 (亿) |
| `--min-listing-q` | 16 | 上市时间下限 (季) |
| `--ebit-floor` | -50 | EBIT 单季崩盘预警 (`ebit_crash`), -100 关闭 |
| `--ebit-peak-kill` | -30 | EBIT 业绩见顶预警 (`ebit_peak_down`), -100 关闭 |

### Watchlist

| Flag | 含义 |
|---|---|
| `--no-sync-watchlist` | 关 watchlist 同步 |
| `--dry-run` | watchlist 同步 dry-run |

## 输出

- `docs/earnings-blowout-watchlist.md` (按季分 section, 18 列中文)
- 自动同步 `config/watchlist.json` (list_type=blowout): 加新命中 / 删退场

## 相关

- `/t-analyze <code>` (命中后深挖)
- **`/t-finance-roc-ey`** (配套: 财务多维分析)
- `/t-near-low` (5y 回撤超跌)
- **`/t-rsi6-tech`** (下游: 技术面过滤, RSI 双超卖)
- `/t-sync-data --financials` (跑前必跑)

## 命名沿革

- **v1: `/t-earnings-blowout`** (2026-08 起)
- **v2: `/t-finance-earnings-blowout`** (2026-09-18 加 `finance-` 前缀)
- **v3 (2026-09-23)**: 3 rule OR + 放宽默认 (rev15/np20/jump30/gm5/profit_surge 80) + 删位置过滤
- **v6.3.0 (2026-09-24)**: **5 闸 prefilter 跑在 3 rule 之前** + EBIT 双闸预警剔除业绩杀样本 (5/5 全活) + 业务名去工程缩写 + 执行输出生效过滤规则总表
