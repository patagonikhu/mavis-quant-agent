---
name: t-guardrail
description: 守门员框架 (Mavis Guardrails). 4 个内置 check: network / factor / eps-scope / parquet. 0 网络, 纯静态扫描. 触发词: "检查守门员"、"guardrail 跑一下"、"CI 检查".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.guardrail                       # 默认 network
... --check parquet    # 业务层直读 parquet / duckdb / sqlite3 (除 signal_cache)
... --check factor     # 通用 factor 必须纯函数 + 估值单文件
... --check eps-scope  # --eps 范围白名单
... --strict --write-md # 严格模式 + 写 docs/{parquet,eps-scope,network}-guardrail.md
```

## 4 个 check

| Check | 规则 |
|---|---|
| `network` | `/t-sync-data` 之外 8 个 skill 必须 0 网络 (无 requests/urllib/tushare 等) |
| `parquet` | 业务层走 DataStore, 禁止 `pd.read_parquet` / `duckdb.execute('read_parquet...')` / `sqlite3.connect('data/...')` |
| `factor` | 通用 factor 纯函数 (`def compute_xxx`), 估值单文件 (`tools/factors/valuation/factor_lib.py`) |
| `eps-scope` | `--eps` 不允许全市场 (datacenter 限频 + WAF 风险) |

## 相关

- `/t-sync-data` — 唯一允许网络
- `architecture.md` §3 — 4 条硬约束 + 守门员覆盖
- `CLAUDE.md` 🚨 数据拉取铁律
