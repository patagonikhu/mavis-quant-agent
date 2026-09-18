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
| L1 Sync | `tools/storage/sync.py` | 8 flag: --kline/--stk-factor/--stock-basic/--financials/--eps/--fflow/--cache/--meta, 默认 --auto | — |
| L1 Store | `tools/storage/store.py` | DataStore I/O, 25+ 公开方法 | ❌ 网络 |
| L2 Analysis | `tools/analysis/analysis_engine.py` | 6 strategy (chan/wyckoff/smc/obv/fflow/finance) | ❌ 网络, ❌ 直读 db |
| L2 容器 | `tools/analysis/render_data.py` | RenderData dataclass | ❌ 网络 |
| L3 Render | `tools/render/report_renderer.py` | RenderData → Markdown | ❌ 网络 |

**调用顺序:** `sync` → `DataStore.get_ctx()` → `AnalysisEngine.analyze()` → `render_report()`。

**/t-analyze 0 网络:** 跑 `t_analyze_one` 不需要先 sync, 缺数据 DataStore 报错, 报错了再 sync 补。

**并发铁律:** sync 单线程先跑, 批量 worker 只读 DataStore (0 网络)。

---

## 🔴 OBV 三块 (易混点)

| 块 | 来源 | 用途 |
|---|---|---|
| **经典 OBV** | `tools/factors/volume/price_fflow.py::obv_factor` | K线累计, 5 档 verdict (obv5/obv_trend) |
| **fflow_factor** | 同文件 `fflow_factor` | Tushare money_flow 主力净流入, 5 档 verdict |

**OBV 信号适用性:** ✅ 光学/封测/HBM (主力控盘度高) / ❌ 题材股/小盘股 (噪声大) / ❌ 周期股 (β 主导)。触发后必须结合 "板块 MA20 偏离" + "fflow 5日净流出" + "T 框架阶段" 综合判定, 单 OBV 趋势不直接清仓。

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
| 2️⃣ | 4 个缠论补充策略 (SMC-OB + 量价 OBV + 威科夫 + 多市场共振) | 🥇 一等 |
| 3️⃣ | 市场状态定量判断 + 方法优先级矩阵 | 🥈 二等 |
| 4️⃣ | 大盘 + 美股背景 | 🥈 可选 |
| 5️⃣ | PEG / DCF L (基本面对冲, 必须在 1️⃣2️⃣ 之后) | 🥉 二等 |
| 6️⃣ | 主力 fflow (Tushare.money_flow 真值) | 🥉 验证 |
| 7️⃣ | 三层仓位 + 买卖点 (1买/2买/3买/1卖/2卖/3卖) | 综合 必须 |

详细 22 section / 因子矩阵 / 数据源 fallback: 见 `tools/render/report_schema.py`。

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
