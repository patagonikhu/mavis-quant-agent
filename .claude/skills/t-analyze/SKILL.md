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
```

> 批量跑**只含持仓 + 自选**。blowout 候选池不参与 (2026-11-06 删 `--include-blowout`:
> 打开它一次会出 157 份详报把目录撑爆)。要深挖某只 blowout 票 → `/t-analyze <code>` 单只跑。
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
- **报告 7 section**: schema 在 `tools/render/report_schema.py`, 改这里不要散到 batch/*.py

## 输出

- `docs/portfolio/analyze-{code}-{name}.md` (持仓票) / `docs/watchlist/...` (自选)
- `docs/signal-watchlist.md` (批量模式汇总)
- **blowout 段不参与批量跑, 也不落 md** (2026-11-06): `list_type=blowout` 是"业绩暴增候选池",
  用来筛的 —— 筛完看汇总表 / stdout 就够, 不给 157 只各存一份详报
  (`--include-blowout` flag 已删, `docs/blowout/` 已废弃并清空)

### 汇总里的信号类型 (2026-11-03, 2026-11-06 缠论下线后调整)

`signal-watchlist.md` 的 buy / sell 两张表来自 `extract_signals()` 的 direction 字段:

| 信号 | 方向 | 含义 |
|---|---|---|
| `A→M主升启动(daily/weekly)` | buy | **威科夫阶段转变** (Accumulation→Markup) |
| 威科夫子事件 (Spring/LPS/SOS/EVR...) | buy/sell | 按事件性质分 |
| MA 超跌 ≤-10% | buy | |
| MA 过热 ≥+10% | sell | |
| `M→D派发开始` | sell | 威科夫 Markup→Distribution |

> ⚠️ 2026-11-06: **缠论整层下线**, `🟢1买/2买/3买` (缠论买卖点) 与「中枢位置变化」两类信号**已删除**。
> 方向信号现在全部来自威科夫阶段转变 + 子事件 + MA 偏离。

> ⚠️ 威科夫 **A→M 是 daily 和 weekly 分开算的** —— 日线 A→M 但周线还是 A, 是两个不同的信号, 报表里会分别列出。
> 「变化」列不会重复显示同一转变 (A→M 只出现一次)。

## 相关

- `/t-sync-data` (跑前必跑) / `/t-finance-earnings-blowout` / `/t-backtest` / `/t-guardrail` / `/t-factor-5y`
