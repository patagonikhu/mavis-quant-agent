---
name: t-analyze
description: 股票分析 + 批量扫描. 单只 /t-analyze <code>; 批量 /t-analyze --all. 0 网络, 走 DataStore. 触发词: "分析XX股票"、"XX能买吗"、"批量分析"、"全部扫一遍".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** `tools/batch/t_analyze_one.py` + `tools/batch/t_analyze_all.py` argparse `--help`(参数 / 默认值 / 性能对比)。SKILL.md 只保留"啥时候用 + 跑最小例子 + 关键约束"。

## 用法

```bash
# 单只
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --code 300274

# 多只 (逗号或空格分隔, 默认单进程)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --codes "600177,300274,300750"

# 多只 + 多进程并发 (mp.spawn 跨 GIL, 4 worker 实测 2.6x 加速)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --codes "600177,300274" --workers 4

# 强制输出子目录 (跳过 watchlist 分流, 如 wyckoff-a2m 命中专用)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --code 600177 --output-subdir wyckoff-a2m

# 全 watchlist 批量 (复用 t_analyze_all, 4 worker 并发)
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python -m tools.batch.t_analyze_all

# 显式清理「不在 watchlist.json 里的历史孤儿 md」 (2026-11-12 起默认**不删**)
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python -m tools.batch.t_analyze_all --prune-orphans

# 行业版: 给申万二级行业指数出分析报告 → docs/sw/ (125 个, 10s)
bash tools/with_venv.sh python -m tools.batch.t_analyze_all --sw

# 行业版 (单个行业)
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --sw --code 801012.SI
```

> 🏭 **行业版 (`--sw`, 2026-11-12 加)** —— 跟个股同款报告, 但换掉 2 个对行业不适用的段:
>
> | 个股段 | 行业版换成 | 为什么 |
> |---|---|---|
> | 「财务数据」 | **「📈 行业估值 (PE/PB/市值)」** | 行业指数没财报, 但申万指数自带 PE/PB/总市值/流通市值 |
> | 「🏆 行业地位」 | **「🏆 行业间地位」** + **「👑 行业内龙头榜」** | 个股看"我在行业里排第几"; 行业看"我在所有行业里排第几" + "这个行业里谁是大票谁在涨" |
>
> 其余段 (MA / 因子历史 / 因子矩阵 / 因子详情 / XGBoost / 数据源) **零改动复用** →
> 行业指数 K 线拼 RawContext 后走**同一套** AnalysisEngine/WyckoffStrategy。
> 产物: `docs/sw/analyze-{801012}-{行业名}.md` · 125 个二级行业 (有 K 线数据的) · 8 worker **10s**。
> 龙头榜口径跟个股版同源 (市值排名用总市值, 涨幅用**总市值比值** = 股东实际收益, 送转不失真)。
>
> ⚠️ 行业版**没有**「财务数据」段 → linter 按 `<!-- report_type: industry -->` 标记切到
> `INDUSTRY_SECTIONS` 清单 (report_schema.py), 别拿个股的 8 段清单去卡它。

> ⚠️ **批量入口默认不删任何 md** (2026-11-12 用户纠错后改的):
> 用户让「刷新 docs/watchlist 下的 md」, 老默认把 65 份不在 `watchlist.json` 里的
> 历史孤儿 md 当成垃圾清掉了 —— 用户要的是**重新生成**, 不是删除。
> 现在孤儿清理改成 **opt-in** (`--prune-orphans`), 默认全部保留, 只打印提示。
> **删是不可逆动作, 不能藏在刷新路径里。**

> 批量跑**只含持仓 + 自选**。blowout 候选池**只跑威科夫 M 阶段的票** (2026-11-09 改):
> 先用 `tools/batch/blowout_markup_screen.py` 筛出当前**日线 stage == Markup** 的票,
> 只有这些票出详报 → `docs/blowout/`; 其余 148 只不落 md。
> 筛选口径跟详报末行同源 (生产侧 `analyze_history` → `3period.daily.stage`), 不自己重算 stage。
> 要深挖某只**非 M 阶段**的 blowout 票 → `/t-analyze <code>` 单只跑。
>
> 批量跑**不含 ETF**。ETF MACD 扫描段 2026-11-06 整段下线 (`--no-etf` flag 一并删),
> ETF 扫描走独立的 `/t-etf-wyckoff-a2m`。

## `--codes` 性能对比 (2026-10-17 实测, 4 只详报)

| workers | 总耗时 | 加速 |
|---|---|---|
| 1 (单进程, 默认) | 13s | 1.00x |
| 4 (mp.spawn) | 5s | **2.6x** |

> mp.spawn 不能 fork (duckdb fork 死锁), 默认 0 worker = 单进程; 设 `os.cpu_count()` 用全部核心

## 关键约束

- **0 网络**, 走 `DataStore.get_ctx()` + `AnalysisEngine.analyze()`, 详见 architecture.md §4
- **缺数据**: 报"请先 /t-sync-data", 不兜底拉
- **报告 8 section** (2026-11-11 加「🏆 行业地位 (龙头判定)」): schema 在 `tools/render/report_schema.py`, 改这里不要散到 batch/*.py

## 🚫 本 skill 不做什么 (越界 = 违规)

> **用户说 `/t-analyze <code>` = 只出这一只的详报。** 跑完就停, 汇报结论, 结束。
> "SKILL.md 没禁止" ≠ "可以做" —— 详见 CLAUDE.md「Skill 越界铁律」。

| ❌ 别做 | 为什么 |
|---|---|
| **不扩到 `--all` / 批量** | 用户给了一只就只跑一只。想批量 → 报告里问一句, 等用户答 |
| **不跑别的扫描器** | `a2m` / `factor_5y` / `backtest` 都不属于"分析一只票" (blowout 段的 M 阶段筛选是 `--all` 自身的内建步骤, 不是另起扫描器) |
| **不改 watchlist.json** | 自选/持仓增删是用户配置, 本 skill 只读不写 |
| **不改阈值 / 判定口径** | 报告里看出问题 (数据缺失 / 指标反常 / 段落报错) → **报告给用户**, 不自己动代码 |
| **不因为"数据旧了"就自己重跑别的票** | 发现上游数据更新导致某份旧报告过期 → 报告给用户并等答复 |

> 通用原则见 CLAUDE.md「Skill 越界铁律」: 不改代码只报告 / 不追加用户没要的 flag / 不自己跑 sync。

**📤 输出契约** (跑完 stdout 里就有, **别再去 grep md / 重算一遍**):
- stdout 末尾已有「扫描 N 只 / MD生成 / 有今日信号」+ buy/sell 明细
- **不用再去 grep md 找信号清单** —— 明细就在 stdout
- 逐只详报已落 `docs/{portfolio,watchlist,blowout}/`, 结论看 stdout 即可

## 输出

- `docs/portfolio/analyze-{code}-{name}.md` (持仓票) / `docs/watchlist/...` (自选)
- `--sw` → `docs/sw/analyze-{行业代码}-{行业名}.md` (行业版, 125 个二级行业)
- **批量模式汇总走 stdout** —— buy / sell 信号明细直接打在终端, 不落汇总 md
- **blowout 段只落 M 阶段的票** (2026-11-09): `list_type=blowout` 是"业绩暴增候选池",
  批量跑时先筛出当前**日线 stage == Markup** 的票, 只有这些票出详报 → `docs/blowout/`
  (非 M 的票不落 md, 也不进主汇总; blowout 段的 buy/sell 信号单独打印, 不污染主口径)
  2026-11-06 曾整段排除 (`--include-blowout` flag 已删, `docs/blowout/` 清空过), 现已恢复但加了 M 阶段门槛

### 汇总里的信号类型

stdout 的 buy / sell 明细来自 `extract_signals()` 的 direction 字段:

| 信号 | 方向 | 含义 |
|---|---|---|
| `A→M主升启动(daily/weekly)` | buy | **威科夫阶段转变** (Accumulation→Markup) |
| 威科夫子事件 (Spring/LPS/SOS/EVR...) | buy/sell | 按事件性质分 |
| MA 超跌 ≤-10% | buy | |
| MA 过热 ≥+10% | sell | |
| `M→D派发开始` | sell | 威科夫 Markup→Distribution |


> ⚠️ 威科夫 **A→M 是 daily 和 weekly 分开算的** —— 日线 A→M 但周线还是 A, 是两个不同的信号, 报表里会分别列出。

## 相关

- `/t-sync-data` (跑前必跑) / `/t-finance-earnings-blowout` / `/t-backtest` / `/t-guardrail` / `/t-factor-5y`
