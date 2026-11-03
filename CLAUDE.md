# CLAUDE.md

> **Mavis 投资分析 Agent** — A 股 / 美股 / 港股产业链研究助手。
> 详细规则按需读: 各 skill 的 SKILL.md / `handbook/analysis-framework.md` / `handbook/AGENT_MEMORY.md`

---

## 🚫 数据拉取铁律

**唯一入口**: `tools/storage/sync.py`。所有数据/网络操作只在 `tools/storage/` 下:
- 读 → `DataStore` (`tools.storage.store.DataStore`)
- 写 → `tools.storage.sync`
- 网络 → `sources/tushare` / `sources/eastmoney` (仅 sync 调)

**合法源只有 2 个**: `tushare.pro` / `datacenter.eastmoney.com` (EPS 预期)。
**WAF 拒接域 (严禁直连)**: `push2.eastmoney.com` / `push2his.eastmoney.com` / `push2delay.eastmoney.com` / `web.ifzq.gtimg.cn` / `qtimg`。

---

## 🏗️ 三层架构 (v6.2.x)

```
sync_data → DataStore → AnalysisEngine → RenderData → report_renderer
  ↑          ↑              ↑                ↑              ↑
 L1         L1              L2              L2             L3
```

| 层 | 文件 | 禁止 |
|---|---|---|
| L1 Sync | `tools/storage/sync.py` (8 数据段 flag, 默认 `--auto`) | — |
| L1 Store | `tools/storage/store.py` | ❌ 网络 |
| L2 Analysis | `tools/analysis/analysis_engine.py` (4 strategy: chan/wyckoff/structure/finance) | ❌ 网络, ❌ 直读 db |
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
| 1️⃣ | 缠论三要素 (中枢位置 + 背驰 + 止跌) | 🥇 |
| 2️⃣ | 2 个缠论补充策略 (威科夫 + 多市场共振) | 🥇 |
| 3️⃣ | 市场状态定量判断 + 方法优先级矩阵 | 🥈 |
| 6️⃣ | 买卖点 (1买/2买/3买/1卖/2卖/3卖) | 必须 |

section 列表 / 因子矩阵 / fallback: `tools/render/report_schema.py` (10 有效 section, 唯一事实源)。

---

## 📊 估值口径 (2026-11-02 固化)

**只看 PE (静态) + 盈利质量 (ROC / EY / 毛利率 / 杠杆)**, 全部用已审计 TTM 财报算, 不依赖任何预测值。

**已彻底删除, 禁止复活**: **OBV** (10-27) / **主力资金流 fflow** (10-28) / **PEG** (11-02) / **DCF L** (11-02)。
详见 `docs/fflow-removal-assessment.md` / `docs/obv-removal-record.md`。

删除后综合判定 = **3 类等权投票** (chan / wyckoff / 多市场共振), 建仓阈值 `bottom_signals >= 2`, 减仓阈值 `top_signals >= 2`。

> 2026-11-04: 结构分析 (OB/FVG/Sweep) 那一票已从因子矩阵投票与报告展示中移除 (实测对现有报告 action 零影响, 阈值不变); 同日生产层完成改名, 全仓统一用 `structure` / `结构` 命名。

---

## 你的数据文件

| 文件 | 用途 | 维护者 |
|---|---|---|
| `data/events.json` | 关键事件库 (T 点) | 你 + LLM 协作 |
| `data/watchlist.json` | 关注清单 | **你手维护** |
| `data/sectors.json` | 板块/ETF → 成分股 | LLM 首填, 你改 |

**git policy**: 默认直连, 失败才用 `proxy.sin.sap.corp:8080`。数据 API (`requests`/`curl`) 永不加 proxy, 拉代码 (`git pull`) 用 `git config http.proxy` 配。

---

## 8 个 Slash 命令 (详见 `.claude/skills/`)

| 命令 | 用途 |
|---|---|
| `/t-analyze <code> [--all]` | 单只/批量, 10 section 详报 |
| `/t-backtest <signal>` | 信号回测 (5 年) |
| `/t-sync-data [flags]` | 8 数据段 flag 正交 sync, 默认 `--auto` |
| `/t-rsi6-tech` | RSI6<25 + RSI12<30 双指标超卖 + 科技板块 + 季报 yoy>0 (4 重, 0 网络) |
| `/t-earnings-blowout` | R3 启动期反转信号, 找 10x 票 |
| `/t-factor-5y <code>` | 5 年因子历史轨迹 (120 日窗口) |
| `/t-wyckoff-a2m` | 全市场 A→M (Accumulation→Markup) 三重确认扫描 |
| `/t-guardrail [--check X]` | 静态守门员 (network/factor/eps-scope/parquet/safe-fs) |

已删: `/t-watchlist` `/t-monitor` `/t-sector` `/t-etf` `/t-chain` `/t-checklist` `/t-bottleneck` `/t-trigger` `/t-rotation` `/t-ranking` `/t-near-low` `/t-roc-ey` (2026-11-02)。
