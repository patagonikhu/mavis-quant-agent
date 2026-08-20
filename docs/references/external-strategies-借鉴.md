# 外部策略通用方法论参考 — 通用指标框架 + 通用交叉验证框架 调研

> **调研日期:** 2026-06-29
> **目的:** 看两个相邻项目有哪些策略 / 工作流 / 工具值得 mavis-quant-agent 通用方法论参考
> **作用域:** 只看能落到我们的 4 个 slash 命令 (t-analyze/t-watchlist/t-monitor/t-sector/t-chain/t-etf) 和投资四问 + T 框架 + PEG/DCF L 上的东西

---

## 📦 两个项目快照

### 1. **通用指标框架** (Quant Trader 工具集)

| 维度 | 内容 |
|---|---|
| **定位** | 专业 quant 研究工具包 (Python 包 + MCP server) |
| **规模** | 79 个技能 (agent/src/skills/) + 7 个回测引擎 + 4 个 Alpha Zoo (452 alphas) + MCP 54 个工具 |
| **市场** | A股 / 港股 / 美股 / 加密货币 / 期货 / 期权 |
| **数据源** | 18 种 (baostock, tencent, sina, eastmoney, mootdx, yfinance, stooq, yahoo, OKX, CCXT, Futu, ...) — 国内开源数据库 |
| **核心特性** | Shadow Account (从交易日记提取策略 → 回测 → 对比 PnL)、Alpha Zoo、Multi-Agent Swarm (29 个预置团队) |
| **典型工具** | `backtest()`, `factor_analysis()`, `analyze_options()`, `analyze_trade_journal()`, `extract_shadow_strategy()`, `run_shadow_backtest()`, `list_swarm_presets()`, `run_swarm()` |
| **代码规模** | agent/api_server.py 134K + agent/mcp_server.py 70K + README 110K |

**核心 79 个技能 (按主题)**:
- **技术分析**: candlestick / elliott-wave / ichimoku / harmonic / chanlun / smc / ichimoku
- **量化方法**: factor-research / ml-strategy / multi-factor / pair-trading / quant-statistics
- **风险管理**: risk-analysis / volatility / hedging-strategy
- **期权**: options-strategy / options-payoff / options-advanced
- **基本面**: earnings-revision / earnings-forecast / financial-statement / fundamental-filter
- **风控与套利**: sector-rotation / seasonal / macro-analysis / global-macro / hk-connect-flow
- **另类数据**: sentiment-analysis / social-media-intelligence / onchain-analysis / stablecoin-flow
- **估值**: valuation-model / etf-analysis / corporate-events

---

### 2. **通用交叉验证框架** (价值投资 Skill 合集)

| 维度 | 内容 |
|---|---|
| **定位** | 基于 Claude Code / Codex 的价值投资研究 skill 合集 |
| **规模** | 18 个 skill + 9 个 Python 工具 + 2 个 Codex 适配层 |
| **投资哲学** | 四大师: **巴菲特 + 芒格 + 段永平 + 李录** |
| **报告规模** | 180+ 篇研究报告 (`reports/` 目录) — 数量 + 质量都明显高于 mavis-quant-agent |
| **数据源** | 国内开源数据 (主) + Wind/iFinD 风格付费 API + 雪球 scraping |
| **核心工具** | `tools/financial_rigor.py` (451 行) — 提供精确算术 / 市值校验 / 三场景估值 / Benford 检查 |
| **报告目录结构** | 按公司建文件夹, 同一公司的所有报告集中放 (与 mavis-quant-agent 的 `analyze-{code}.md` 单文件模式不同) |

**核心 18 个 skill (按用法)**:

| Skill | 用途 | 对应 mavis |
|---|---|---|
| `/investment-research` | 4 大师综合框架分析单股 | ≈ `/t-analyze` |
| `/investment-team` | 4 大师视角分别出报告再综合 (multi-agent) | **没有** — 我们是 LLM 单一 agent |
| `/investment-checklist` | 巴菲特买入前 6 关 Checklist | 部分覆盖于 `投资四问` |
| `/industry-research` | 行业深度研究 | ≈ `/t-chain` |
| `/industry-funnel` | 行业漏斗筛选 | ≈ `/t-sector` |
| `/thesis-tracker` | 投资论文追踪 + 季度检查 | **没有** — 这是持仓后纪律 |
| `/bottleneck-hunter` | 供应链瓶颈扫描 + 套利机会 | **没有** |
| `/quality-screen` | 7 条硬指标排除非一流公司 | 部分覆盖 |
| `/earnings-review` | 单季财报分析 | **没有** |
| `/earnings-team` | 4 大师 + 研究底稿 + 读者评审 (完整流程) | **没有** |
| `/management-deep-dive` | 管理层评估 (李录视角) | **没有** |
| `/news-pulse` | 新闻脉冲扫描 | 部分覆盖于 `WebSearch` |
| `/portfolio-review` | 组合复盘 (portfolio-latest.md 持续更新) | **没有** |
| `/private-company-research` | 未上市公司 (字节跳动/宇树等) | **没有** |
| `/deep-company-series` | 公司深度专题系列 | **没有** |
| `/dyp-ask` | 段永平视角问答 | 部分覆盖于 `CLAUDE.md` 段落 |
| `/financial-data` | 财务数据 ETL | 我们用 Bash + curl, 没有专用 skill |
| `/wechat-article` | 公众号文章输出 | **没有** |

---

## 🔍 能通用方法论参考的策略与具体做法

### ✅ 优先级 A — 直接通用方法论参考 (核心方法论)

#### A1. **四大师投资哲学框架** (从 通用交叉验证框架)
**通用交叉验证框架 怎么做的:** 把巴菲特/芒格/段永平/李录的判断方法系统化为 4 个独立 skill, 然后让 `investment-team` skill 拉 4 个视角并行出报告。

**mavis-quant-agent 现状:** CLAUDE.md 只列了"投资四问", 没有引用大师原文 / 没有 famous quotes。

**通用方法论参考做法:**
- 在 `docs/analysis-framework.md` 每个框架后, 加 1-2 段"巴菲特/芒格/段永平怎么说"
- 例: 投资四问的"卡点" 旁加一句 `—— "要找傻瓜都能经营的生意, 因为早晚会有个傻瓜来经营" (巴菲特)`
- 例: PEG 旁加 `—— "Pay $1 for $1 of growth" (Lynch)`
- 可选: 后续 v4.0 加 `/investment-team` skill, 跑 4 视角并行

**通用方法论参考收益:** 决策框架更深、更系统; 报告更有可信度 (引用大师); 用户复用经验

---

#### A2. **精确算术工具 (`tools/financial_rigor.py`)** (从 通用交叉验证框架)
**通用交叉验证框架 怎么做的:** 451 行 Python 工具, 提供:
- `exact()` — Decimal 精确运算, 避免浮点误差
- `verify_market_cap()` — 市值自动校验 (股价 × 总股本 vs 报告市值)
- `verify_valuation()` — PE / PB / FCF/share 一键校验
- `cross_validate()` — 多数据源交叉验证, 字段一致性
- `three_scenario_valuation()` — 三场景估值 (乐观/中性/保守)
- `benford_check()` — Benford 法则检测异常数据
- `exact_calc()` — 表达式精确计算

**mavis-quant-agent 现状:** v3.0 框架有 `dcf_implied.py` (穿透叙事 skill 用), 但 `t-analyze` 的 PEG / Priced-in 三场景算术都是 LLM 心算, 浮点误差 + 漏校验常见。

**通用方法论参考做法:**
- 把 `tools/financial_rigor.py` 直接 copy 一份到我们项目 (`mavis-quant-agent/tools/`)
- 在 `/t-analyze` 工作流 step 4 (输出) 强制调用:
  - `verify_market_cap(price, shares, reported)` — 报错就停
  - `verify_valuation(price, eps, fcf_per_share)` — 算 PE/PEG/FCF yield
  - `cross_validate("EPS", {"wind": 1.5, "choice": 1.48, "tonghuashun": 1.49})` — 多源校验
- 三场景估值 (乐观/中性/保守) 用 `three_scenario_valuation()`, 不要 LLM 编

**通用方法论参考收益:** 算术 100% 准, 多源校验避免"单源假数据", Benford 检测异常, 报告可信度飞跃

---

#### A3. **AI 研究偏见自查清单** (从 通用交叉验证框架 `investment-research` SKILL)
**通用交叉验证框架 怎么做的:** 在做研究前, 必须先评级"信息丰富度" (A/B/C):
- A 级: 共识过强, AI 输出趋同于市场, alpha 有限 → 重点做**反面检验**
- B 级: 资料中等, 数据需推算 → 每个推算标注**置信度**
- C 级: 资料稀缺 → AI 会过度保守, 用**第一性原理** 4 问 (客户/复购/可复制性/管理层)

并在报告开头标注 AI 研究置信度 vs 实际投资确定性。

**mavis-quant-agent 现状:** 没有。LLM 容易:
- 在数据稀缺时编 Priced-in 三场景 (我们已多次被抓)
- 在信息丰富时和卖方报告高度雷同 (无 alpha)
- 没有正面 / 反面观点检查

**通用方法论参考做法:** 加进 `t-analyze` 输出格式:
```markdown
## AI 研究置信度 (v3.1 新)
| 等级 | 评估 | 影响 |
|---|---|---|
| A (信息充裕) | 数据充裕, 卖方覆盖>5 | 警惕"共识陷阱", 重点找聪明人不买的理由 |
| B (数据适中)   | 数据部分缺失需推算  | 推算数据标 ⚠️ 置信度 |
| C (数据稀缺)   | 资料极少             | 用第一性原理 4 问, 不强行填估值 |

[本研究等级: B]
[AI 研究置信度 vs 投资确定性: 前者高, 后者需读者判断]
```

**通用方法论参考收益:** 让用户清楚认知 LLM 输出的局限性, 不会被"看起来完整的报告"误导

---

#### A4. **Priced-in 反面检验 (反方观点)** (从 通用交叉验证框架 多处)
**通用交叉验证框架 怎么做的:**
- `investment-research` SKILL: "每个核心判断都必须附带反面论据 (但另一方面...)"
- CLAUDE.md: "禁止使用'我认为'/'我觉得'/'显然'等主观表述"
- CLAUDE.md: "呈现正反两面"

**mavis-quant-agent 现状:** 没有反面检验环节。LLM 倾向"确认偏差" — 用户问"能买吗", LLM 就列出买的理由。

**通用方法论参考做法:** `t-analyze` 输出**强制**新增 "## 反方观点" 章节:
```markdown
## 反方观点 (强制, v3.1)
**核心看空论据:**
1. PEG 0.5 看似便宜, 但稳健 CAGR 14% (而非 30%) → PEG 实际 1.0
2. AI 变压器订单下半年可能放缓 (美国数据中心 capex 周期见顶)
3. 估值锚点 22x PE 已透支 2027E 增长

**为什么我们仍然看好:** (这一栏只在前一栏没击溃时写)
```

**通用方法论参考收益:** 减少确认偏差, 让用户看全图, 避免"过度信任 LLM"

---

### ✅ 优先级 B — 工具/工作流升级

#### B1. **Shadow Account (交易日记 → 策略 → 回测)** (从 通用指标框架)
**通用指标框架 怎么做的:**
1. 用户导入同花顺/东财交易 CSV
2. `analyze_trade_journal` 分析行为 (持仓周期、win rate、处置效应、追涨杀跌、过度交易、锚定)
3. `extract_shadow_strategy` 提炼 3-5 条 if-then 规则
4. `run_shadow_backtest` 多市场回测规则, 对比 PnL
5. `render_shadow_report` 输出 HTML/PDF 报告

**mavis-quant-agent 适用性:** mavis focus 在**买入决策** (research) 而非**交易执行** (execution)。但"自我策略审计"思路可通用方法论参考。

**通用方法论参考做法 (轻量):**
- 加 `/trade-audit <CSV>` skill: 输入交易 CSV, 输出:
  - 持仓周期分布
  - 赢率 + 平均赢/亏
  - 处置效应 (盈利卖太快 vs 亏损扛太久)
  - 板块集中度 / 个股集中度
  - 是否过度交易 (换手率异常)
- 输出 7 个指标的简版, 让用户看到自己交易的"性格"

**通用方法论参考收益:** 知道自己的交易 bias, 才能让 t-analyze 避免相同坑 (e.g. 用户爱追高 → t-analyze 强制加"已涨 % vs 同板块 %" 对比)

---

#### B2. **Multi-Agent Swarm (投资委员会 / 牛熊辩论)** (从 通用指标框架)
**通用指标框架 怎么做的:** 29 个预置团队, 例如:
- **Investment Committee**: bull 视角 → bear 视角 → risk review → PM 决策
- **Earnings Research Desk**: fundamentals + revisions + options → earnings strategist
- **Risk Committee**: drawdown + tail risk + regime analysis

**mavis-quant-agent 现状:** 单 LLM agent, 一个调用出一个视角。

**通用方法论参考做法:** 短期太重, 长期有价值:
- v3.1 加 `/t-investment-committee <code>`: 内部 spawn 3 个 LLM (bull/bear/risk), 然后 PM 综合
- 类似 通用交叉验证框架 的 `/investment-team` (4 大师视角)
- 优点: 反方观点天然生成 (B4 的反面检验自动做)
- 缺点: 3-5x Token 成本

**通用方法论参考收益:** 显著提升报告质量; 反方观点自动有; 适合长期持仓决策

---

#### B3. **财务 ETL 管道 (`ashare_data.py` 等)** (从 通用交叉验证框架)
**通用交叉验证框架 怎么做的:** tools/ 下 9 个 Python 模块:
- `ashare_data.py` — 拉 + 缓存 (防重复拉)
- `financial_rigor.py` — 精确算术
- `morningstar_fair_value.py` — Morningstar 公允价值数据
- `xueqiu_scraper.py` — 雪球数据爬虫
- `stock_screener.py` — 筛选器
- `momentum_backtest_v2.py` — 动量回测
- `report_audit.py` — 报告审计 (检查报告完整性)

**mavis-quant-agent 现状:** 没有数据缓存层, 每次跑 `/t-analyze` 都重新 curl, 慢。

**通用方法论参考做法:**
- 加 `tools/data_cache.py` — JSON 缓存 (key=code+date, TTL 1 天)
- 所有 skill 调用前先查缓存, miss 才 curl
- 减少 80% 网络调用, 加快 5x

**通用方法论参考收益:** 速度 + 离线能力 + 减少 WebSearch 频率

---

#### B4. **报告目录按公司分类 + 报告命名规范** (从 通用交叉验证框架)
**通用交叉验证框架 怎么做的:**
```
reports/
├── 腾讯/
│   ├── 腾讯-research-20260408.md
│   ├── 腾讯-earnings-2025Q4.md
│   ├── 腾讯-management-20260409.md
│   └── 腾讯-thesis.md
├── 拼多多/
└── ...
```

**mavis-quant-agent 现状:** `docs/analyze-{code}.md` 单文件累积 (v1, v2, v3...版本堆叠)

**通用方法论参考做法:** 评估两种方案:
- **方案 A (轻):** 保持单文件, 改进版本管理 (v2.7 简化到 2 版, 已经做了)
- **方案 B (重):** 改 通用交叉验证框架 模式, 每公司文件夹
  ```
  docs/companies/000725-京东方A/
  ├── latest.md            ← 当前最新报告
  ├── v1-research.md       ← 历史版本
  ├── thesis.md            ← 长期投资论文
  └── earnings-2025Q4.md   ← 季报分析
  ```

**通用方法论参考收益:** 方案 B 更专业, 支持更细的分析 (thesis 长期跟踪, earnings 季度更新), 但工程量大

**短期:** 保持现状; 长期: 评估方案 B

---

#### B5. **News Pulse (新闻脉冲扫描)** (从 通用交叉验证框架)
**通用交叉验证框架 怎么做的:** `news-pulse` skill — 大规模扫描最近新闻, 过滤有效信号, 输出"市场情绪方向"

**mavis-quant-agent 现状:** WebSearch 失败率高 (上轮实测 400 错误), 不能持续用

**通用方法论参考做法:**
- 不依赖 WebSearch, 改用**多数据源扫描**:
  - 雪球热帖 (网页爬取)
  - 东财研报 (curl API)
  - 公司公告 (`cninfo.com.cn` 巨潮资讯网 公开 API)
  - 龙虎榜数据 (curl)
- News Pulse 用这些稳定源替代 web 搜索

**通用方法论参考收益:** 跳出 WebSearch 400 困境; 数据源更稳; 自动检测宏观信号

---

### ✅ 优先级 C — 可选通用方法论参考

#### C1. **Multi-source cross-validation framework** (通用交叉验证框架 `cross_validate` 启发)
为每个关键数字找 ≥ 2 个数据源:
- EPS: Wind + Choice + 同花顺
- 市值: 实时价 (腾讯) + 东财
- 评级: 至少 1 个卖方研报 + 1 个独立分析
- 偏差 > 2% 自动报警

#### C2. **Benjamin 检查 (异常数据检测)** (通用交叉验证框架 `benford_check`)
财报数字首位数应该符合 Benford 法则 (1 出现 ~30%, 9 ~5%)。偏离说明数据造假可能。

#### C3. **Behavioral Finance 模块** (通用指标框架 有)
锚定效应 / 处置效应 / 过度自信 — 个人投资者偏误自检。可作为可选章节加入 `t-analyze` 输出末尾: "你的潜在认知偏误对这只股的影响"

#### C4. **技术分析模块** (通用指标框架 有 79 个)
candlestick / elliott wave / ichimoku / chanlun — 我们 v3.0 框架是纯基本面 + T 框架, 完全不要技术分析; 但**用户可能问"现在能不能买"** (短期), 加一个轻量"技术面确认"模块可能有用。

#### C5. **FAQ 案例库** (通用交叉验证框架 隐含做)
通用交叉验证框架 有 180+ 报告, 时间久了自然形成 "成功/失败案例库"。mavis 也有 ~16 个 analyze 文件, 但没系统性复用。

通用方法论参考做法: 加 `docs/lessons-learned.md` — 每个投资决策后回顾, 记录"对/错/为什么"。

---

## 📊 通用方法论参考优先级总表

| # | 策略 | 来源 | 优先级 | 工作量 | 收益 | 建议时机 |
|---|---|---|---|---|---|---|
| A1 | 四大师引用 | 通用交叉验证框架 | **A** | 1 天 | 中 | v3.1 |
| A2 | financial_rigor.py | 通用交叉验证框架 | **A** | 2 天 | 高 | v3.1 |
| A3 | AI 研究偏见自查 | 通用交叉验证框架 | **A** | 1 天 | 高 | v3.1 |
| A4 | 反方观点强制 | 通用交叉验证框架 | **A** | 0.5 天 | 高 | v3.1 |
| B1 | trade-audit | 通用指标框架 | B | 2 天 | 中 | v3.2 |
| B2 | investment-team 多 agent | 通用指标框架/通用交叉验证框架 | B | 5 天 | 高 | v3.3 或 v4.0 |
| B3 | data_cache | 通用交叉验证框架 | B | 1 天 | 中 | v3.1 |
| B4 | 报告目录重构 | 通用交叉验证框架 | B | 3 天 | 中 | v3.2 |
| B5 | News Pulse 多源 | 组合 | B | 3 天 | 高 | v3.2 |
| C1 | multi-source cross-validate | 通用交叉验证框架 | C | 2 天 | 中 | v3.3 |
| C2 | Benford 检查 | 通用交叉验证框架 | C | 1 天 | 低 | v3.3 |
| C3 | behavioral-finance | 通用指标框架 | C | 1 天 | 低 | 评估需求 |
| C4 | 轻量技术面 | 通用指标框架 | C | 3 天 | 中 | 评估需求 |
| C5 | lessons-learned.md | 通用交叉验证框架 | C | 长期 | 中 | 持续 |

---

## 🎯 推荐下一步 (本周)

**v3.1 重点做 A1-A4 + B3** (1 周内):

| 工作 | 工时 | 文件 |
|---|---|---|
| 1. A4 反方观点章节 | 2h | `.claude/skills/t-analyze/SKILL.md` + `analysis-framework.md` |
| 2. A3 AI 研究置信度评级 | 3h | 同上, 加在 t-analyze 输出 |
| 3. A1 大师引用 | 4h | 在 `docs/analysis-framework.md` 每个框架后加 |
| 4. A2 financial_rigor.py | 6h | copy 到 `tools/`, 写调用示例 |
| 5. B3 data_cache | 3h | `tools/data_cache.py` + 接入 t-analyze |

合计: ~18 小时 (2-3 个工作日), 报告质量可显著提升。

---

## 📝 几点反思

1. **我们比 通用交叉验证框架 缺少的不是 skill 数量, 是工具和工程化**
   - 通用交叉验证框架 有 9 个 Python 工具, 我们基本 0 个
   - 通用交叉验证框架 报告目录规范, 我们文件管理有点乱
   - 通用交叉验证框架 强调"数据交叉验证", 我们靠 LLM 估算

2. **我们比 通用指标框架 缺少的不是技术宽度, 是纪律**
   - 通用指标框架 有 Shadow Account (从交易日记学), 我们没有持仓后的纪律系统
   - 通用指标框架 有 Swarm 多 agent 辩论, 我们是单 agent
   - 通用指标框架 有 79 个 quant 技能, 我们专注**基本面** (这是差异化, 不是缺)

3. **真正要学的是 "通用交叉验证框架 的工程化 + 我们聚焦基本面" = 最佳**
   - AI 偏见自查 (A3) + 反方观点 (A4) — 立刻可加
   - financial_rigor.py (A2) — Python 工具, 几天可上
   - 报告目录重构 (B4) — 工程化, 跳过 polyfill

4. **multi-agent swarm (B2) 是真正的杠杆点**, 但需要 LLM token 成本 3-5x, 等核心方法论稳定后再做

---

## 引用文件路径 (便于深入)

### 通用交叉验证框架 关键文件
- `~/通用交叉验证框架/CLAUDE.md` — 项目指令 + 报告命名规范
- `~/通用交叉验证框架/AGENTS.md` — Codex 兼容性规则
- `~/通用交叉验证框架/codex-skills/investment-research/SKILL.md` — 4 大师综合框架
- `~/通用交叉验证框架/codex-skills/investment-checklist/SKILL.md` — 巴菲特 6 关
- `~/通用交叉验证框架/codex-skills/thesis-tracker/SKILL.md` — 持仓后纪律
- `~/通用交叉验证框架/codex-skills/bottleneck-hunter/SKILL.md` — 供应链瓶颈
- `~/通用交叉验证框架/codex-skills/quality-screen/SKILL.md` — 7 条去劣
- `~/通用交叉验证框架/tools/financial_rigor.py` — 精确算术
- `~/通用交叉验证框架/tools/ashare_data.py` — 数据 ETL
- `~/通用交叉验证框架/data/watchlist.json` — 关注清单结构
- `~/通用交叉验证框架/data/fundamentals.json` — 财务数据库

### 通用指标框架 关键文件
- `~/通用指标框架/README.md` — 总览
- `~/通用指标框架/agent/SKILL.md` — 主 SKILL (含 79 个 skill 索引)
- `~/通用指标框架/agent/src/skills/` — 79 个技能源码
- `~/通用指标框架/agent/api_server.py` — FastAPI 服务
- `~/通用指标框架/agent/mcp_server.py` — MCP server 实现

---

**下一步:** 用户确认优先级 → 写 v3.1 实现计划 → 一周内交付 A1+A2+A3+A4+B3 五个核心升级

(报告完)
