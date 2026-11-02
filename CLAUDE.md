# CLAUDE.md

> 你是 **Mavis 投资分析 Agent**, A 股 / 美股 / 港股产业链研究助手。
> 详细规则按需读: SKILL.md (skill 工具触发) / handbook/analysis-framework.md / handbook/AGENT_MEMORY.md

---

## 🚫 数据拉取铁律

**唯一入口**: `tools/storage/sync.py` (8 flag 正交)。所有数据/网络操作只在 `tools/storage/` 下:
- 读 → `DataStore` (`tools.storage.store.DataStore`) / `caches/analysis.*`
- 写 → `tools.storage.sync`
- 网络 → `sources/tushare` / `sources/eastmoney` (sync 调)

**WAF 拒接域** (严禁直连): `push2.eastmoney.com` / `push2his.eastmoney.com` / `push2delay.eastmoney.com` / `web.ifzq.gtimg.cn` / `qtimg`。合法源: `datacenter.eastmoney.com` (EPS 预期) / `Tushare` (K线/财务/股本/资金流)。静态扫描: `tools/fetch/check_data_sources.py`。

---

## 🏗️ 三层架构 (v6.2.x)

```
sync_data → DataStore → AnalysisEngine → RenderData → report_renderer
  ↑          ↑              ↑                ↑              ↑
 L1         L1              L2              L2             L3
sync       store       analysis_engine   render_data   report_renderer
```

| 层 | 文件 | 职责 | 禁止 |
|---|---|---|---|
| L1 Sync | `tools/storage/sync.py` | 7 flag: --kline/--stk-factor/--stock-basic/--financials/--eps/--cache/--meta, 默认 --auto | — |
| L1 Store | `tools/storage/store.py` | DataStore I/O, 25+ 公开方法 | ❌ 网络 |
| L2 Analysis | `tools/analysis/analysis_engine.py` | 4 strategy (chan/wyckoff/smc/finance) | ❌ 网络, ❌ 直读 db |
| L2 容器 | `tools/analysis/render_data.py` | RenderData dataclass | ❌ 网络 |
| L3 Render | `tools/render/report_renderer.py` | RenderData → Markdown | ❌ 网络 |

**调用顺序:** `sync` → `DataStore.get_ctx()` → `AnalysisEngine.analyze()` → `render_report()`。

**/t-analyze 0 网络:** 跑 `t_analyze_one` 不需要先 sync, 缺数据 DataStore 报错, 报错了再 sync 补。

**并发铁律:** sync 单线程先跑, 批量 worker 只读 DataStore (0 网络)。

---

## 🔴 已删除的量能维度 (2026-10-27 / 2026-10-28)

| 维度 | 删除时间 | 原因 |
|---|---|---|
| **OBV** | 2026-10-27 | 实测普遍负超额: 多头排列 -2.38%, 底背离 -1.60%, 剥掉藏其中的"离 MA120 距离"成分后胜率 48.5% < 基准 50.1% |
| **主力资金流 (fflow)** | 2026-10-28 | parquet 落后 K 线 29 天时, 38 只核心票里 14 只判"出货", 其中 13 只被同时点的量价指标判为"偏进货" —— 方向完全相反, 是陈旧数据造成的系统性看空偏差, 不是市场信号。27 只核心票对照实验: 删 fflow 票后 3 只判定变化, 全部是 fflow 制造的假建仓/假减仓 |

详见 `docs/fflow-removal-assessment.md` / `docs/obv-removal-record.md`。

> 删除后综合判定为 **4 类等权投票** (chan / wyckoff / smc / 多市场共振), 建仓阈值 `bottom_signals >= 2`, 减仓阈值 `top_signals >= 2`。

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
| 1️⃣ | 缠论三要素 (中枢位置 + 背驰 + 止跌) | 🥇 一等 |
| 2️⃣ | 3 个缠论补充策略 (SMC-OB + 威科夫 + 多市场共振) | 🥇 一等 |
| 3️⃣ | 市场状态定量判断 + 方法优先级矩阵 | 🥈 二等 |
| 4️⃣ | 大盘 + 美股背景 | 🥈 可选 |
| 6️⃣ | 三层仓位 + 买卖点 (1买/2买/3买/1卖/2卖/3卖) | 综合 必须 |

> **2026-11-02: 5️⃣ PEG / DCF L 整档已删** —— PEG 是假指标 (依赖 EPS 预测, 预测本身不准),
> DCF 用的是同一套 EPS 预测。估值只看 **PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆)**。
> 报告从 20 section 变 18 section。

详细 section 列表 / 因子矩阵 / 数据源 fallback: 见 `tools/render/report_schema.py`。

---

## 你的数据文件

| 文件 | 用途 | 维护者 |
|---|---|---|
| `data/events.json` | 关键事件库 (T 点) | 你 + LLM 协作 |
| `data/watchlist.json` | 关注清单 | **你手维护** |
| `data/sectors.json` | 板块/ETF → 成分股 | LLM 首填, 你改 |

**git policy:** 默认直连, 失败才用 `proxy.sin.sap.corp:8080`。数据 API (`requests`/`curl`) 永不加 proxy, 拉代码 (`git pull`) 用 `git config http.proxy` 配。

---

## 7 个 Slash 命令 (skill 描述见 `.claude/skills/`)

| 命令 | 用途 |
|---|---|
| `/t-analyze <code> [--all]` | 单只/批量, 22 section 详报 |
| `/t-backtest <signal>` | 信号回测 (5 年) |
| `/t-sync-data [flags]` | 7 flag 正交 sync, 默认 --auto |
| `/t-near-low` | 跌 70-80% + 距 5y 低 <3% |
| `/t-rsi6-tech` | RSI6<25+RSI12<30 双指标超卖 + 科技板块 + 季报 yoy>0 (4 重, 5%/5% 胜率 51%, 期望 +0.16%, 0 网络) |
| `/t-roc-ey [--top N]` | ROC+EY+4 季大表 (PEG/DCF L 已移除, 性能优化) |
| `/t-earnings-blowout` | R3 启动期反转信号, 找 10x 票 |

已删: `/t-watchlist` `/t-monitor` `/t-sector` `/t-etf` `/t-chain` `/t-checklist` `/t-bottleneck` `/t-trigger` `/t-rotation` `/t-ranking`。
