---
name: t-guardrail
description: 守门员框架 (Mavis Guardrails). 4 个内置 check: network / factor / parquet / safe-fs. 0 网络, 纯静态扫描. 触发词: "检查守门员"、"guardrail 跑一下"、"CI 检查".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

> **单一事实源:** 4 个 check / 0 网络 skill 列表 / 白名单入口 全部在 `tools/batch/guardrail.py` 模块顶部 docstring + `ZERO_NET_SKILLS` 字典 + `--help`。SKILL.md 只保留"啥时候跑 + 跑最小例子"。

## 啥时候跑

- 提 PR / 改完代码后,保证不破坏 0 网络 / DataStore / Factor 规则 / 用户数据安全
- CI 检查 (--strict --write-md)
- 想验证"这文件是不是违规使用网络"
- **新增/修改任何批量脚本后,必跑 `--check safe-fs`** —— 防止脚本直接 `unlink`/`rmtree` 用户数据

## 跑

```bash
# 默认 (网络隔离检查)
bash tools/with_venv.sh python -m tools.batch.guardrail

# 4 个 check
bash tools/with_venv.sh python -m tools.batch.guardrail --check parquet
bash tools/with_venv.sh python -m tools.batch.guardrail --check factor
bash tools/with_venv.sh python -m tools.batch.guardrail --check safe-fs

# CI 严格模式 + 写报告
bash tools/with_venv.sh python -m tools.batch.guardrail --strict --write-md

# 单 skill 检查
bash tools/with_venv.sh python -m tools.batch.guardrail --skill t-analyze
```

## safe-fs check (2026-11-02 加)

AST 检测,抓**直接删用户数据**和**覆盖写配置**:

| 判定 | 模式 | 修法 |
|---|---|---|
| ❌ 违规 | `.unlink()` / `os.remove()` / `shutil.rmtree()` 指向 `docs/` `config/` | `tools.storage.safe_fs.safe_delete` (移 `~/.Trash`, 可恢复) |
| ⚠️ 警告 | `open(path,"w")` / `write_text()` 覆盖 `docs/` `config/` | `tools.storage.safe_fs.atomic_write_json` (原子写 + 备份) |

- AST 检测 → 天然跳过 docstring/注释里的示例代码, 不误报
- 支持跨行调用 `os.remove(\n  p\n)` 和变量间接引用 `p = "docs/x.md"` → `os.remove(p)`, 别名 `q = p` 也解析
- 白名单: `tools/storage/` (数据层写入正常) + `safe_fs.py` / `guardrail.py` 自身

## 关键约束

- **0 网络**, 纯静态扫描 (grep / regex / AST)
- 0 网络 skill 列表硬编码在 `guardrail.py::ZERO_NET_SKILLS`(2026-10-20 修:跟当前 8 个真实 0 网络 skill 对齐)
- 网络入口白名单:`tools/storage/sync.py` + `tools/storage/sources/{tushare,eastmoney}.py` 等 11 个允许
- **用户数据不可删** —— `config/watchlist.json` 等 5 个在 `safe_fs.PROTECTED_PATHS`, `safe_delete` 直接拒绝

## 🚫 本 skill 不做什么 (越界 = 违规)

> **用户说 `/t-guardrail` = 只体检并报告。** 跑完就停, 报告违规项, 结束。
> "SKILL.md 没禁止" ≠ "可以做" —— 详见 CLAUDE.md「Skill 越界铁律」。

| ❌ 别做 | 为什么 |
|---|---|
| **发现问题不直接改代码** ⚠️ | 本 skill 是**体检**, 不是修复。扫出违规 → 列出清单 + 建议, **改不改、怎么改由用户定**。这是最容易越界的一个 (看到问题就想动手) |
| **不跑 `/t-analyze` / 别的 skill** | 体检 ≠ 修数据 ≠ 出报告 |
| **不改 guardrail 规则本身** | 觉得某条规则误报 → 报告给用户, 不自己放宽标准 (放宽 = 自己给自己发通行证) |
| **不跑 sync / 不联网** | 本 skill 纯静态扫描, 保持 0 网络 |
| **不删任何文件** | `safe_delete` 只在用户明确要求删某个东西时才用 |

## 跑后验证

```bash
# 看报告 (--write-md 时)
cat docs/guardrail-report.md
```

## 相关

- `/t-sync-data` — 唯一允许网络
- `architecture.md` §3 — 4 条硬约束 + 守门员覆盖
- `CLAUDE.md` 🚨 数据拉取铁律
