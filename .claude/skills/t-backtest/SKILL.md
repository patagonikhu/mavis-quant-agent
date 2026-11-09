---
name: t-backtest
description: 信号回测 — 扫描 N 年历史, 统计信号触发后未来 N 天最大涨幅命中率. 0 网络, 走 DataStore. 触发词: "回测"、"信号胜率"、"历史统计".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** `tools/batch/batch_backtest.py` 模块 docstring(用法) + `--help`(参数 / 默认值)。SKILL.md 只保留"啥时候用 + 跑最小例子 + 关键约束"。

## 用法

```bash
# 单信号
... --signal Accumulation / Markup / Distribution / Markdown

# 组合 (AND)
... --signal Markup --signal Distribution

# 关键参数
... --days 30 --threshold 10  # 持仓 30 天, 涨幅阈值 10%
... --lookback 5              # 回看 5 年
... --codes 300274            # 指定股票
... --all                     # 全 watchlist
... --portfolio               # 仅持仓
... --write-md                # 写 docs/backtest-*.md
```

## 关键约束

- **0 网络**, 走 `DataStore` + `AnalysisEngine.analyze_history()` 入口 (不绕过)
- **5 年 backfill**: 跑前先 `python -m tools.storage.sync --cache`
- **缓存命中**: 7 只持仓 × 1250 天 × 7 strategy ≈ 3.5 分钟 (无 cache) / 秒级 (有 cache)
- **信号类别**: 威科夫子事件 / 威科夫阶段 (Accumulation / Markup / Distribution)

## 🚫 本 skill 不做什么 (越界 = 违规)

> **用户说 `/t-backtest <signal>` = 只回测这个信号。** 跑完就停, 汇报结果, 结束。
> "SKILL.md 没禁止" ≠ "可以做" —— 详见 CLAUDE.md「Skill 越界铁律」。

| ❌ 别做 | 为什么 |
|---|---|
| **不换信号 / 不改阈值** | 用户给哪个信号就回测哪个。结果难看 → 报告事实, **别自己调参重跑** (调参 = 改结论) |
| **不加 `--write-md` / `--all` / `--portfolio`** | 用户没要就不加。想加 → 问一句, 等答复 |
| **不跑 `/t-analyze` 或别的 skill** | 回测只出统计, 不出单只详报 |
| **不改回测口径 / 缓存 schema** | 发现 cache 命中率异常 / 逻辑有问题 → **报告给用户**, 不自己改 |
| **不静默跳过缺失日期** | 缺数据要**在结果里显式说明** (缺多少 / 影响哪段), 别让分母被偷换 |

> ✅ **例外**: 上面「关键约束」里声明的 `sync --cache` 预热属本 skill 自身流程, 可以跑。

> 通用原则见 CLAUDE.md「Skill 越界铁律」: 不改代码只报告 / 不追加用户没要的 flag / 不自己跑 sync。

**📤 输出契约** (跑完 stdout 里就有, **别再去 grep md / 重算一遍**):
- stdout 已有「命中数 / 缓存命中 / 达标率」
- **按票明细要 `--write-md` 才落盘** —— 用户没要就别加, 也别自己再算一遍命中率

## 输出

- stdout: 命中数 / 命中率 / 均涨幅
- `--write-md`: `docs/backtest-{信号名}.md`

## 相关

- `/t-analyze` / `/t-sync-data` / `tools/batch/batch_backtest.py` (回测引擎) / `tools/storage/caches/analysis.py` (分析结果缓存)
