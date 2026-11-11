---
name: t-factor-5y
description: 单只 / 批量跑 5 年因子历史走势, 输出 md (23 列: 价格 / 威科夫 / 行业威科夫 / 技术指标). 0 网络, 复用 AnalysisEngine.analyze_history + compute_factor_history. 触发词: "5年因子"、"长势"、"5年走势"、"factor-5y".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)

> **单一事实源:** 5 年窗口 / 采样口径 / 复用链路 / 命名沿革 / 输出格式 全部在 `tools/batch/factor_5y.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候用 + 跑 + 数据特性"。

## 啥时候用

- 单只看 5 年长周期(找阶段切换、5 年最大回撤、5 年最低 RSI)
- 多只并行扫描找"穿越牛熊"的票
- 教学:回看 2021-2024 完整牛熊周期,看威科夫阶段在长周期里的位置
- 想看**长周期结构复盘**,**不替代** `/t-analyze` (7 section 短周期)

## 跑

```bash
# 单只 (点名 → 落 1 份详细 md)
bash tools/with_venv.sh python -m tools.batch.factor_5y --code 600089
bash tools/with_venv.sh python -m tools.batch.factor_5y --codes 600089 000792

# 批量 (按范围 → 只落 1 份汇总 md, 每只 1 行)
bash tools/with_venv.sh python -m tools.batch.factor_5y                      # 默认 watchlist 持仓
bash tools/with_venv.sh python -m tools.batch.factor_5y --watchlist-types 持仓,blowout

# 批量 + 逐只落 md (会清掉不在本次范围内的旧 md)
bash tools/with_venv.sh python -m tools.batch.factor_5y --write-md-all

# 所有 flag + 默认值 (单一事实源)
bash tools/with_venv.sh python -m tools.batch.factor_5y --help
```

### 输出规则 (2026-11-06 改)

| 场景 | 落盘 | 说明 |
|---|---|---|
| `--code` / `--codes` (点名) | 每只 1 份详细 md | 要细看某只票, 就点名跑它 |
| 按范围批量 (默认) | **只出 1 份汇总 md** `_summary-{scope}.md` | 每只 1 行: 行业 / 5年累计 / 最大回撤 / RSI6 / 阶段 |
| 按范围批量 + `--write-md-all` | 每只 1 份详细 md + 汇总 md | 会同步清掉不在本次范围的旧 md |

> ⚠️ 批量**不要**逐只落 md: 21 只 × 约 290KB, 164 只 ≈ 49MB, 目录直接被撑爆,
> 而且批量场景的用途是"多只横向对比找穿越牛熊的票" —— 对比看**汇总表**才有用。
> 2026-11-06 事故: 验证列序时顺手 `--watchlist-types 持仓,自选` 跑了 164 只 → 目录堆了 168 份 md.
> (例外: 用户明确要求"每只都要 md"时才落 —— **采样永远日度全量, 没有降采样开关**)

## 数据特性

- **0 网络**, 走 DataStore parquet
- 5 年窗口硬编码: `lookback=1250` (≈ 252 交易日 × 5 年)
- 21 只持仓批量 ~60 秒 (`--workers 4`)
- **不替代 /t-analyze**:本 skill 只输出因子历史表(长周期 1-2-3-5 年节奏);`/t-analyze` 是 7 section 全要素详报

## 🚫 本 skill 不做什么 (越界 = 违规)

> **用户说 `/t-factor-5y` = 只出因子历史表。** 跑完就停, 汇报结果, 结束。
> "SKILL.md 没禁止" ≠ "可以做" —— 详见 CLAUDE.md「Skill 越界铁律」。

| ❌ 别做 | 为什么 |
|---|---|
| **不落逐只 md** | 默认只出 1 份汇总表 (`_summary-{scope}.md`)。逐只 md 要显式 `--write-md-all` —— 用户没要就不加 |
| **不擅自降采样** | 采样永远日度全量 (2026-11-11 已删 `--step` 开关, 没有第二个选项)。2026-11-11 事故: 205 只批量时 driver 里硬写 `STEP=5`, 用户要日度全量, 205 份 md 重跑 |
| **不跑 `/t-analyze`** | 因子历史表 ≠ 7 section 详报。用户想看详报 → 让用户自己调 `/t-analyze` |
| **不改 lookback / 窗口** | 5 年窗口是硬编码 `lookback=1250`, 别为了让结果好看去动 |
| **不因为"结果有异常"就调参重跑** | 异常 → 报告给用户, 等判断 |

> 通用原则见 CLAUDE.md「Skill 越界铁律」: 不改代码只报告 / 不追加用户没要的 flag / 不自己跑 sync。

**📤 输出契约** (跑完 stdout 里就有, **别再去 grep md / 重算一遍**):
- stdout 只有「完成 N/N」+ 汇总 md 路径
- **逐只清单在汇总 md 里** (`docs/factor-5y/_summary-*.md`), 要清单读那个文件, **别重跑**

## 跑后验证

```bash
# 批量汇总表 (每只 1 行, 横向对比用)
cat "docs/factor-5y/_summary-持仓.md"

# 单只详细表
head -50 docs/factor-5y/analyze-600089-特变电工-5y.md
```

## 相关

- `/t-analyze <code>` (单只 7 section 详报, 120 日)
- `/t-sync-data --kline` (跑前必跑,落盘 K 线到 1250 天)
