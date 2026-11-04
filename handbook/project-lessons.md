# Mavis Quant Agent — 项目专属教训存档（索引页）

> 2026-11-04 从 agent memory 迁入。**这些是 mavis-quant-agent 项目专属知识**,
> 只在本仓库有效, 迁移出 agent memory 层是为了不再每次会话强制注入 (76.9KB → 见 MEMORY.md)。
>
> **2026-11-05 重构:** 原单文件 1300+ 行 / 47 条教训, 拆成 **5 个主题分册**
> (本轮又新增 2 条, 现共 **49 条**),
> 内容**一字未改** (已逐行 diff 验证 0 差异), 只加了「一句话摘要」+ 索引页。

## 怎么用

**grep 查, 不用通读。** 提到具体话题时:

1. 先开 [`handbook/lessons/README.md`](lessons/README.md) —— 索引页, 49 条一句话摘要, 一屏扫完
2. 找到相关条目 → 进对应主题分册看细节 (带完整背景 / 实测数据 / commit 号)
3. 每个分册内也可以直接 grep 关键词

## 主题分册

| 分册 | 主题 | 条数 | 覆盖内容 |
|---|---|---|---|
| [`lessons/01-wyckoff.md`](lessons/01-wyckoff.md) | 威科夫 / Markup / fallback 判定体系 | 18 | MA 容差 / fallback 语义 / 周期口径 / 死代码陷阱 |
| [`lessons/02-data-sync.md`](lessons/02-data-sync.md) | 数据同步 / tushare / parquet 落盘 | 8 | 限频实测 / 写盘覆盖丢数据 / 季度切换陷阱 |
| [`lessons/03-finance.md`](lessons/03-finance.md) | 财务 / 估值口径 | 5 | 降序陷阱 / PE 年化 bug / 已删维度清单 |
| [`lessons/04-a2m-scan.md`](lessons/04-a2m-scan.md) | A→M 扫描 / watchlist | 11 | 性能基线 / 过滤链 / ETF 去重 / 买卖点口径 |
| [`lessons/05-render-lint.md`](lessons/05-render-lint.md) | 报告渲染 / linter / 文档一致性 | 7 | 表格列数 / linter 截断 / SKILL 兑现铁律 / 占位符豁免 / skip_reason 透传 |

**索引页** (47 条一句话摘要): [`handbook/lessons/README.md`](lessons/README.md)

## 新增教训的规矩

- 落 `handbook/lessons/` 对应主题分册, **不新建文件** (5 个主题够用了)
- 标题格式: `### <结论/坑名> (YYYY-MM-DD)` + 下一行 `Type: fact|rule|bug|decision|pattern`
- `Type:` 行下面加一行 `> **摘要:** <一句话可执行 take-away>`
- 写完同步更新 `lessons/README.md` 的索引表 (摘要 + 分册)
- 判断该不该进这里: **"换个项目还成立吗?"** —— 成立 → 进 agent memory; 不成立 → 进本目录

## 相关

- `handbook/AGENT_MEMORY.md` — 极简 cheat sheet (入口路径 / 纪律 / 周期口径真源)
- `handbook/analysis-framework.md` — 决策框架 (PE + ROC/EY 双轴)
- `handbook/naming-convention.md` — 命名硬规则 (7 条 + 5 项 grep 验证)
- `handbook/finance-glossary.md` — 财务术语表
- 详细背景存档: `docs/eval/*.txt` + git log
