# CLAUDE.md

> **Mavis 投资分析 Agent** — A 股 / 美股 / 港股产业链研究助手。
> 详细规则按需读: 各 skill 的 SKILL.md / `handbook/analysis-framework.md` / `handbook/AGENT_MEMORY.md`

---

## 🚫 数据拉取铁律

**唯一入口**: `tools/storage/sync.py`。所有数据/网络操作只在 `tools/storage/` 下:
- 读 → `DataStore` (`tools.storage.store.DataStore`)
- 写 → `tools.storage.sync`
- 网络 → `sources/tushare` (仅 sync 调)

**合法源只有 1 个**: `tushare.pro`。
**WAF 拒接域 (严禁直连)**: `push2.eastmoney.com` / `push2his.eastmoney.com` / `push2delay.eastmoney.com` / `web.ifzq.gtimg.cn` / `qtimg`。

> 2026-11-04: `datacenter.eastmoney.com` 退出合法源 — EPS 机构一致预期整档已删。

---

## 🏗️ 三层架构 (v6.2.x)

```
sync_data → DataStore → AnalysisEngine → RenderData → report_renderer
  ↑          ↑              ↑                ↑              ↑
 L1         L1              L2              L2             L3
```

| 层 | 文件 | 禁止 |
|---|---|---|
| L1 Sync | `tools/storage/sync.py` (7 数据段 flag, 默认 `--auto`) | — |
| L1 Store | `tools/storage/store.py` | ❌ 网络 |
| L2 Analysis | `tools/analysis/analysis_engine.py` (3 strategy: wyckoff/technical/finance) | ❌ 网络, ❌ 直读 db |
| L2 容器 | `tools/analysis/render_data.py` | ❌ 网络 |
| L3 Render | `tools/render/report_renderer.py` | ❌ 网络 |

**调用顺序**: `sync` → `DataStore.get_ctx()` → `AnalysisEngine.analyze()` → `render_report()`

**并发铁律**: sync 单线程先跑, 批量 worker 只读 DataStore (0 网络)。
**/t-analyze 0 网络**: 跑 `t_analyze_one` 不需先 sync, 缺数据 DataStore 报错再补。

---

## 🐍 Python 环境固化

```bash
bash tools/with_venv.sh python -m tools.storage.sync --codes 002371
bash tools/with_venv.sh python -m tools.batch.t_analyze_one --code 300274
```

❌ `pip install` / `python3 tools/xxx.py` (绕过 .venv) / `source .venv/bin/activate` (跨 shell 不通)

---

## 🔴🔴🔴 报告输出顺序硬约束 (违反 = 不合格)

| 顺序 | 内容 | 优先级 |
|---|---|---|
| 1️⃣ | 威科夫 3 大阶段判定 (Accumulation / Markup / Distribution) | 🥇 |
| 3️⃣ | 市场状态定量判断 + 方法优先级矩阵 | 🥈 |

> 方向信号由 **威科夫阶段转变 (A→M 买 / M→D 卖) + 9 种 sub-event + MA 偏离** 提供。

section 列表 / 因子矩阵 / fallback: `tools/render/report_schema.py` (7 有效 section, 唯一事实源)。

---

## 📊 估值口径 (2026-11-02 固化)

**只看 PE (静态) + 盈利质量 (ROC / EY / 毛利率 / 杠杆)**, 全部用已审计 TTM 财报算, 不依赖任何预测值。

**已彻底删除, 禁止复活**: **OBV** (10-27) / **主力资金流 fflow** (10-28) / **PEG** (11-02) / **DCF L** (11-02) / **多市场共振 res** (11-05) / **EPS 机构一致预期** (11-04) / **因子矩阵投票** (11-06)。
详见 `docs/fflow-removal-assessment.md` / `docs/obv-removal-record.md`。

> 2026-11-06: **因子矩阵投票逻辑整块删除** (用户指令) —— Phase1 strategy 4→3 个, 因子矩阵从 3 行降为 1 行 (威科夫)。
> **报告变化**: 「因子 × 3 周期 综合矩阵」→「因子 × 2 周期 矩阵 (威科夫)」; 完整性表少 2 列; 因子历史表少 3 列 → **24 列变 21 列**; 「综合判定」(🥈标准建仓/🟠标准减仓/🟡观察) 整档消失。
> **判定口径**: 投票是**整块删除**, 不是把阈值 `bottom_signals >= 2` 降到 1 —— 降阈值会让「任何威科夫 Accumulation 都算建仓建议」, 属另一次决策。方向信号由 **威科夫阶段转变 (A→M 买 / M→D 卖) + 9 种 sub-event + MA 偏离** 提供 (`analysis_result_signals.extract_signals` 仍在)。

> 2026-11-06: **因子历史表「变化」列删除** (用户指令) —— 因子历史表 **21 列 → 20 列**。
> **只删展示列, 数据链路不动**: `diff_rows` / `extract_signals` 仍在 —— 它们供 `signals_active` (报告方向信号) 和汇总表「信号」列使用, 只是不再渲染成因子历史表的一列。
> 改列只动 2 处: `FACTOR_HISTORY_HEADER` 常量 + `_format_factor_row` 的 `line` 拼接, **必须严格同步**, 否则列错位。

> 2026-11-04: **EPS 机构一致预期整档已删** —— 它是**预测值** (datacenter 拉机构 E0/E1/E2/E3), 11-02 删 PEG/DCF 后已无任何分析消费者, 只剩管道残骸 + 报告展示。删除范围: `tools/storage/caches/eps.py` (整个缓存层)、`eastmoney._build_eps_table` (唯一联网函数, 删完该文件变纯计算模块)、`sync.py --eps` flag、`DataStore.get_eps()`、`RawContext.eps_table`、`RenderData.EpsRow/eps_table/eps_status`、报告「EPS 一致预期」表、linter 的 EPS 检查、guardrail 的 `eps-scope` check。section id `eps_finance` → `finance_quarterly`。
> **实测: 判定结果零变化** (7 只票 Magic/ROC/EY/因子矩阵逐字节一致)。副产物: 原本 EPS 常为 EMPTY 的票 (如 002371) 数据完整度 87% → **100%**。
> ⚠️ 注意区分: **财报里的真实 EPS 仍在** (`fina_indicator` 已审计, `financials` parquet), 算 `np_yi` 在用, **不要动** (rsi6 亏损过滤已于同日随 rsi6 家族下线)。

> 2026-11-04: **RSI 双超卖扫描整族下线** —— `/t-rsi6-tech` + `rsi6_tech_scan.py` + `rsi6_backtest.py` + `rsi6_backtest_history.py` 全删 (3 个脚本互相 import, 拆开留孤儿代码), 回测产物 `docs/rsi6-*.md` 经 safe_fs 移回收站。
> **下线原因 (实测, 非拍脑袋):** ① 判定用「最近 N 根任一跌破过 RSI 阈值」但表里显示**最新** RSI6 值 → 98 只命中里 **63 只显示 RSI6>35** (看着完全不超卖), 输出自相矛盾; ② 业绩过滤 09-23 就删了, 剩下「纯 RSI 双超卖」对 321 只 watchlist 命中 98 只 = **31% 命中率**, 无区分度; ③ `--check-news` / `--news-days` 是**占位 flag**, 代码层不实现。
> **对生产链路零影响**: rsi6 只是独立扫描器, 不参与威科夫/Magic 判定, 不被 `/t-analyze` 调用。财报 EPS 仅少一个消费端 (亏损过滤), `np_yi` 那处保留。
> 技术超卖的替代路径: `/t-analyze` 单只详报 (含 RSI6/RSI12/KDJ 全套技术指标) 或 `/t-factor-5y` 长周期轨迹。

> 2026-11-06: **ETF MACD 扫描整段下线** (用户指令) —— `tools/batch/etf_macd_scan.py` + `docs/etf-macd/` (72 份产物) 全删 (safe_fs → 回收站), `t_analyze_all` 的 ETF 段 + `--no-etf` flag 一并删。
> **它从来不是 slash skill** (无 SKILL.md), 只是被 `/t-analyze --all` 顺带调的一个独立扫描器 —— 命中逻辑 = RSI6<30 超卖 **或** MACD 金叉多头, 出逐只 md + 汇总表。
> **不参与威科夫/Magic/因子矩阵任何判定**, 删掉对 `/t-analyze` 单只详报和批量股票段**零影响**。
> ETF 扫描能力由 `/t-etf-wyckoff-a2m` 承担 (A→M 三重确认); ETF 候选池唯一真源仍是 `tools/batch/etf_universe.py` (fund_type 过滤 + 同指数两级并查集去重, 现只剩一个消费者)。

> 2026-11-04: 结构分析 (OB/FVG/Sweep) 那一票已从因子矩阵投票与报告展示中移除 (实测对现有报告 action 零影响, 阈值不变); 同日生产层完成改名, 全仓统一用 `structure` / `结构` 命名。
>
> 2026-11-05: 结构分析 (structure / 原 SMC) **整个从 analysis 层删除** —— `StructureStrategy` 类、`RawContext.structure_result`、`raw["structure"]`、10 个 `structure_*` 行字段、`tools/factors/structure/` 因子库目录全部移除。报告层早已不消费, 只算不用。删除后 Phase1 strategy = **wyckoff / technical / finance**。
>
> 2026-11-05: 多市场共振 (res) 那一票一并移除 —— `resonance_3period` 网络调用早已删除, 该票恒为 0, 报告里的「多市场共振: 已移除」占位块 + 因子矩阵表格行同步删掉。判定结果零变化。
>
> 2026-11-05: 结构分析的 **md 层同步清理** —— README 第 9 章「结构分析 (OB/FVG/Sweep)」整节 + 已于 10-28 下线的「量价综合」整节删除, 报告模板「OB 列表」文案删除。报告 md 里 OB / FVG / Sweep / 扫流 / 结构分析 / 订单块 / 量价 全部 0 命中。

---

## 你的数据文件

| 文件 | 用途 | 维护者 |
|---|---|---|
| `data/events.json` | ~~关键事件库 (T 点)~~ v6.2 已删, T 事件由 LLM 写正文 | — |
| `config/watchlist.json` | 关注清单 | **你手维护** |
| ~~`data/sectors.json`~~ | ~~板块/ETF → 成分股~~ 仓库无此文件, 板块走 THS 概念接口 | — |

**git policy**: 默认直连, 失败才用 `proxy.sin.sap.corp:8080`。数据 API (`requests`/`curl`) 永不加 proxy, 拉代码 (`git pull`) 用 `git config http.proxy` 配。

---

## 8 个 Slash 命令 (详见 `.claude/skills/`)

| 命令 | 用途 |
|---|---|
| `/t-analyze <code> [--all]` | 单只/批量, 7 section 详报 |
| `/t-backtest <signal>` | 信号回测 (5 年) |
| `/t-sync-data [flags]` | 7 数据段 flag 正交 sync, 默认 `--auto` |
| `/t-earnings-blowout` | R3 启动期反转信号, 找 10x 票 |
| `/t-factor-5y <code>` | 5 年因子历史轨迹 (120 日窗口) |
| `/t-wyckoff-a2m` | 全市场 A→M (Accumulation→Markup) 三重确认扫描 |
| `/t-etf-wyckoff-a2m` | 全市场 ETF A→M (fund_type 过滤 + 同指数去重 + 单只详报) |
| `/t-guardrail [--check X]` | 静态守门员 (network/factor/parquet/safe-fs) |

已删: `/t-watchlist` `/t-monitor` `/t-sector` `/t-etf` `/t-chain` `/t-checklist` `/t-bottleneck` `/t-trigger` `/t-rotation` `/t-ranking` `/t-near-low` `/t-roc-ey` (2026-11-02)。
已删: `/t-rsi6-tech` (2026-11-04, RSI 双超卖扫描整族下线 —— 见下方说明)。

---

## 📚 项目知识库 (`handbook/`, git 内, 0 注入成本)

> 这些是**本项目专属**的踩坑存档 —— 威科夫 fallback 调参、tushare 限频、A→M 扫描口径、
> PE 年化 bug、报告渲染 linter 铁律等。**提到具体话题先 grep 这里**, 别凭记忆。
> 2026-11-04 从 agent memory 迁入 (原 76.9KB 每次会话强制注入 → 现按需读取)。

| 文件 | 内容 |
|---|---|
| `handbook/AGENT_MEMORY.md` | 极简 cheat sheet: 入口路径 / 纪律 / 周期口径真源 / 威科夫强度记法 |
| `handbook/project-lessons.md` | **项目专属教训索引页** (47 条 → 5 个主题分册, `handbook/lessons/`; 一句话摘要在 `lessons/README.md`) |
| `handbook/analysis-framework.md` | 决策框架 (PE + ROC/EY 双轴, §2.5 PEG 已删) |
| `handbook/finance-glossary.md` | 财务术语表 |
| `handbook/naming-convention.md` | 命名硬规则 (7 条 + 5 项 grep 验证) |
| `handbook/backtest-roc-ey-formula.md` | ROC/EY 回测公式 |

**铁律:** 往 agent memory 写东西前先问「换个项目还成立吗」—— 不成立就写进本目录。
项目专属知识进 agent memory 会撑爆注入预算, 让所有任务变慢。
