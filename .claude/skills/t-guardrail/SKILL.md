---
name: t-guardrail
description: 守门员框架 (Mavis Guardrails). 4 个内置 check: network / factor / eps-scope / parquet. 0 网络, 纯静态扫描. 触发词: "检查守门员"、"guardrail 跑一下"、"CI 检查".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** 4 个 check / 0 网络 skill 列表 / 白名单入口 全部在 `tools/batch/guardrail.py` 模块顶部 docstring + `ZERO_NET_SKILLS` 字典 + `--help`。SKILL.md 只保留"啥时候跑 + 跑最小例子"。

## 啥时候跑

- 提 PR / 改完代码后,保证不破坏 0 网络 / DataStore / EPS 范围 / Factor 规则
- CI 检查 (--strict --write-md)
- 想验证"这文件是不是违规使用网络"

## 跑

```bash
# 默认 (网络隔离检查)
bash tools/with_venv.sh python -m tools.batch.guardrail

# 4 个 check
bash tools/with_venv.sh python -m tools.batch.guardrail --check parquet
bash tools/with_venv.sh python -m tools.batch.guardrail --check factor
bash tools/with_venv.sh python -m tools.batch.guardrail --check eps-scope

# CI 严格模式 + 写报告
bash tools/with_venv.sh python -m tools.batch.guardrail --strict --write-md

# 单 skill 检查
bash tools/with_venv.sh python -m tools.batch.guardrail --skill t-analyze
```

## 关键约束

- **0 网络**, 纯静态扫描 (grep / regex)
- 0 网络 skill 列表硬编码在 `guardrail.py::ZERO_NET_SKILLS`(2026-10-20 修:跟当前 8 个真实 0 网络 skill 对齐)
- 网络入口白名单:`tools/storage/sync.py` + `tools/storage/sources/{tushare,eastmoney}.py` 等 11 个允许

## 跑后验证

```bash
# 看报告 (--write-md 时)
cat docs/guardrail-report.md
```

## 相关

- `/t-sync-data` — 唯一允许网络
- `architecture.md` §3 — 4 条硬约束 + 守门员覆盖
- `CLAUDE.md` 🚨 数据拉取铁律
