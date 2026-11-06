---
name: t-sync-data
description: 唯一数据同步入口. 7 个正交 flag. **--auto 默认行为 = 智能检测 stale, 只跑需跑的 (含 ETF)**, 显式传 flag = 强跑那一项. **末尾输出 tushare 网络请求统计**. 触发词: "同步数据"、"拉K线/财务"、"sync cache"、"sync 一下".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** 10 个正交 flag / 3 范围 flag / 3 行为 flag / ETF 数据说明 全部在 `tools/storage/sync.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候用 + 跑最小例子 + 关键约束"。

## 啥时候用

- 跑任何 read-only skill 前的**前置依赖**(K 线每日新)
- 数据 stale 时(看 docs/skills-output/.../*.md 报错"请先 /t-sync-data")
- 想批量加 ETF 段时

## 跑

```bash
# 默认 (推荐, = --auto 智能检测, 含 ETF, 末尾输出网络统计)
bash tools/with_venv.sh python -m tools.storage.sync

# 看所有 flag + 帮助
bash tools/with_venv.sh python -m tools.storage.sync --help

# 一键 (--kline --stock-basic --financials 一起跑, 最常用)
bash tools/with_venv.sh python -m tools.storage.sync --all-data

# 显式拉单段 + 指定范围
bash tools/with_venv.sh python -m tools.storage.sync --all --financials
bash tools/with_venv.sh python -m tools.storage.sync --codes 002371
bash tools/with_venv.sh python -m tools.storage.sync --period 20251231 --financials

# 看现状 (不拉)
bash tools/with_venv.sh python -m tools.storage.sync --status

# 强刷 stale
bash tools/with_venv.sh python -m tools.storage.sync --auto-force
```

## 关键约束

- **唯一允许网络的 skill**(其他全部 0 网络,走 DataStore)
- **写盘唯一路径**: `tools.storage.sync`,业务层不直连 db/网络
- **`--ths` 默认不拉**(显式才跑),ths_index 30 天缓存 1 刷,ths_daily 增量续存
- **`--etf` 随 --auto 默认跑**(日增量幂等,已最新秒返回);fund_basic 月级缓存 30 天
- `--auto` 是默认行为:不传任何 sync flag 时隐式走

## 🚫 本 skill 不做什么 (越界 = 违规)

> **用户说 `/t-sync-data` = 只拉数据。拉完就停, 输出同步结果, 结束。**
> "SKILL.md 没禁止" ≠ "可以做" —— 没写禁令的地方一律不做, 别拿"顺手"当理由。

| ❌ 别做 | 为什么 |
|---|---|
| **不跑任何分析 / 报告** | `t_analyze_one` / `t_analyze_all` / `factor_5y` / `a2m` 扫描 / `blowout` / `backtest` 全都不属于同步 |
| **不因为"上游数据变新"就重跑别的 skill** | 同步更新了 K 线, **不等于**授权你重跑依赖它的分析。发现某份报告数据旧了 → **报告给用户**("X 的数据已更新到 Y, 旧报告用的是 Z, 要重跑吗?"), **等用户答**, 不自己动手 |
| **不 retry 已知的返空** | `stk_factor_pro` 当日返空 = 正常 T-1 滞后 (lessons 02-data-sync 第 3 条), 次日 `--auto` 会自动补。**不要**单独 `--stk-factor` 重试, **不要** `--auto-force` |
| **不改 watchlist.json 的内容** | 同步只管数据落盘; watchlist 增删是 `/t-finance-earnings-blowout` 的活 |

> 通用原则见 CLAUDE.md「Skill 越界铁律」: 不改代码只报告 / 不追加用户没要的 flag / 不自己跑 sync。

**📤 输出契约** (跑完 stdout 里就有, **别再去 grep md / 重算一遍**):
- stdout 末尾已有「🌐 网络请求统计」+「📊 本地数据新鲜度」
- **不用再跑 `--status` 复查** —— 那是重复劳动
- `stk_factor_pro` 当日返空是正常的 T-1 滞后, 不要再单独 retry

## 跑后验证

```bash
# 看现状 + 末尾自动输出网络统计
bash tools/with_venv.sh python -m tools.storage.sync --status | head -50
```

## 相关

- 分析 skill (read-only): `/t-analyze` / `/t-finance-earnings-blowout` / `/t-backtest` / `/t-guardrail`
