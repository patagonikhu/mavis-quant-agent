# CLAUDE.md

> **Mavis 投资分析 Agent** — A 股 / 美股 / 港股产业链研究助手。
> 详细规则按需读: 各 skill 的 `SKILL.md` / `handbook/` (git 内, 按需 grep) / `architecture.md`

---

## 🚫 行为契约 (所有 skill 通用, 2026-11-06 固化)

**"SKILL.md 没禁止" ≠ "可以做"。** 用户说哪个 skill 就只做那件事 —— 范围外的动作**先问用户, 等答复**。

| 铁律 | 内容 |
|---|---|
| **不越界** | 不因"上游数据变新"就重跑别的 skill; **不改代码 / schema / 阈值** (发现问题**只报告**); 不追加用户没要的 flag / 范围; 不自己跑 sync (本 skill 声明的预热除外) |
| **回复** | 默认 **≤400 字**; 结论先行 → 关键数据给表格 → 不写散文; 不汇报工具过程; 产物给路径不贴全文; 数字带对比基线; >800 字**先问用户** |
| **验证** | **默认跑 `bash tools/with_venv.sh python -m tools.verify` (9s, 含 py_compile + 批量 import + pytest + guardrail + 命名规范)**; 改单只输出 → 跑 **1 只** + diff; 改渲染器/共享码 → 跑 **3 只** (1 走该分支 + 1 不走 + 1 边界) + 逐字节 diff; 改数据管线 → 才全量 (`--full`) |
| **⛔ 验证别做** | ① **全量一个改动会话只跑 1 次, 放最后** (中间步骤跑 fast 就够) ② 19 个脚本 import 用 `tools.verify` 的批量版, **别用 19 次独立 python 进程** (0.4s vs 57s) ③ 别把「改数据管线→才全量」执行成「每个中间步骤都全量」 |
| **⛔ 三个别做** | ① 别自己写脚本重算刚跑完的任务 (读 stdout 摘要) ② 别加 `2>&1` (tqdm 进度条会冲出上下文触发 externalize) ③ 一次问不到就停 |

> 事故实录: ① `/t-sync-data` 后顺手重跑 `t_analyze_one` 刷新旧报告, 被当场纠正;
> ② `/t-analyze --all` 脚本本体 **38s** 却花了 **6.5 分钟** —— 多出的 5 分半里 **50s 是自写的单线程
> 重算脚本** (比被它验证的批量任务还慢) + 15s 重复查 `~/.Trash` + 2000 字长回复 + 读 70 行源码。
> 根因都是「没写禁令」→ 现在每个 skill 都有「🚫 本 skill 不做什么」+「📤 输出契约」两段。

---

## 🚫 数据拉取铁律

**唯一入口**: `tools/storage/sync.py` — 读走 `DataStore`, 写走 `sync`, 网络只在 `sources/tushare`。
**合法源只有 `tushare.pro`**。
**WAF 拒接域 (严禁直连)**: `push2*.eastmoney.com` / `web.ifzq.gtimg.cn` / `qtimg`。

---

## 🏗️ 三层架构 (v6.2.x)

```
sync_data → DataStore → AnalysisEngine → RenderData → report_renderer
  L1          L1            L2              L2            L3
```

**并发铁律**: sync 单线程先跑, 批量 worker 只读 DataStore (0 网络)。业务层禁止直连 db / 网络。
→ 详见 [`architecture.md`](architecture.md)

---

## 🐍 环境 + 报告契约

**venv**: `bash tools/with_venv.sh python ...` — ❌ 裸 `python3` / ❌ `pip install` / ❌ `source activate`
**网络**: **proxy 只给 GitHub 用** —— git push/pull 直接走 `http://proxy.sin.sap.corp:8080` (带 scheme, 直连慢 ~8s / 代理 ~2.2s); **其余一切 (数据 API / tushare / 网页 / 第三方接口) 一律直连, 永不加 proxy**
**`config/watchlist.json`**: 手维护 (持仓 / 自选 / blowout 候选池三段)

**报告**: 7 有效 section (2026-11-11 加「🏆 行业地位 (龙头判定)」, **2026-11-12 删「MA 均线」**), 顺序与内容真源 = [`tools/render/report_schema.py`](tools/render/report_schema.py) (改 schema 别散到 `batch/*.py`)。
**方向信号** = 威科夫阶段转变 (A→M 买 / M→D 卖) + 9 种 sub-event + MA 偏离。

---

## 📊 估值口径

**只看 PE (静态) + 盈利质量 (ROC / EY / 毛利率 / 杠杆)**, 全部用已审计 TTM 财报算, **不依赖任何预测值**。

**已彻底删除, 禁止复活**: OBV / fflow 主力资金流 / PEG / DCF L / 多市场共振 res / EPS 机构一致预期 / 因子矩阵投票。
删除原因与事故实录 → `handbook/lessons/` (03-finance + 05-render-lint)。

---

## 📚 项目知识库 (`handbook/`, git 内, 0 注入成本)

**提到具体话题先 grep 这里, 别凭记忆。**

| 文件 | 内容 |
|---|---|
| `handbook/lessons/` (5 分册 + `README.md` 索引) | **项目专属教训 70 条** —— 提到具体话题先 grep |
| `handbook/AGENT_MEMORY.md` | 极简 cheat sheet: 入口路径 / 纪律 / 周期口径真源 |
| `handbook/analysis-framework.md` | 决策框架 (PE + ROC/EY 双轴) |
| `handbook/naming-convention.md` | 命名硬规则 (7 条 + 5 项 grep 验证) |
| `handbook/finance-glossary.md` | 术语表 / 估值口径速查 |

**铁律**: 往 agent memory 写东西前先问「换个项目还成立吗」—— 不成立就写进本目录。

---

## 8 个 Slash 命令

`/t-analyze` · `/t-sync-data` · `/t-backtest` · `/t-factor-5y` · `/t-wyckoff-a2m` ·
`/t-etf-wyckoff-a2m` · `/t-finance-earnings-blowout` · `/t-guardrail`

各自参数、性能基线与边界 → `.claude/skills/<name>/SKILL.md`。
