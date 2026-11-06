# 报告渲染 / linter / 文档一致性

> 本文件是 `handbook/project-lessons.md` 的主题分册 (05-render-lint.md)。
> 索引 (全 59 条一句话摘要) 见 [`handbook/lessons/README.md`](README.md)。
> 用法: **grep 查**, 不用通读 —— 提到具体话题先在这里搜关键词。

---


### markdown table header / sep 写死脱节 bug (2026-10-14 固化)
Type: rule
> **摘要:** 表格列数变了要**同步改表头 + 分隔行 + 行拼接 + 文档里的列数描述**四处

**铁律:** 任何 "|---|" + "---|" * N 写死 N 的模式 → N 必须从 header 动态算. 加列时 sep 必漏改 → GFM 表格不渲染.

**反例 (今天 KDJ 加 2 列):**
- header: 25 cell (KDJ/K/D/J + KDJ金叉)
- sep:    '|---|' + '---|' * 23 = 24 cell
- 差 1 cell, GFM 严格按 header cell == sep cell 判断, 否则**整张表不渲染**
- 用户报 "table 乱掉了" / "显示不是 table" 几次, 实际是 GFM 不渲染

**修法 (2026-10-14 已修):** `FACTOR_HISTORY_SEP = "|" + "---|" * (FACTOR_HISTORY_HEADER.count("|") - 1)`
- header 加列时 sep 自动跟随, 0 维护
- 静态扫描: `grep -n "'\-\-\-'" tools/render/*.py` 找写死 sep 模式

**apply when:** 任何 md 表格有 '|---|' + '---|' * N 模式. N 必须从 header.count("|") - 1 算. 验证: `awk -F'|' '{print NF-2}'` 跟 header cell 数对比.

**第 4 处: 文档里的列数 (2026-11-06 补)**
因子历史表 21 列 → 20 列时, 除 header + 行拼接外, **还有 12 处文档/注释写着旧列数**:
`SKILL.md` × 3 (t-factor-5y / _shared/analysis_framework / t-backtest)、`handbook/AGENT_MEMORY.md` × 2、
`handbook/lessons/03-finance.md`、`tools/batch/factor_5y.py` × 3、`tools/batch/verify_a2m_financials.py`、
`tools/t-analyze-all.sh` × 3、`README.md`。
**只改代码不改文档 = SKILL.md 跟代码脱节** (违反 `handbook/naming-convention.md` 第 7 条)。
查法: `grep -rn "21 列\|24 列\|因子历史表" --include="*.md" --include="*.py" --include="*.sh" .`

**删展示列 ≠ 删数据链路 (2026-11-06 补)**
删「变化」列时, `diff_rows` / `extract_signals` **一个字都不能动** —— 它们还供
`signals_active` (报告方向信号) 和汇总表「信号」列用。只删渲染层的那一格 + header 里的一格。

**删整个汇总文件 ≠ 删数据链路 (2026-11-06 补)**
删 `docs/signal-watchlist.md` 整个产物时, `diff_rows` / `extract_signals` 同样一个字都不能动 ——
它们还供 stdout 的 buy/sell 明细用。只删「生成 md 文件」那一段代码, 数据链路 + stdout 输出全留。
判据: **用户删的是"文件"还是"这份数据"?** 前者只删产物层, 后者才动链路。

### linter warning 输出被截断 (2026-10-14 固化)
Type: rule
> **摘要:** 不要截断前 5 条 —— 真正的列数错位常在第 6+ 条

**bug:** `tools/batch/t_analyze_one.py:112` `for warn in warnings[:5]` 只显示前 5 条, 后面的 (包括 "表格列数不匹配" "财务数据行数错误" 等关键 🔴 warning) 被截.

**触发链 (今天 300274):**
1. 加 KDJ 2 列 → header 25 cell, sep 写死 24 cell
2. linter 报 9 条 warning (5 placeholder + 1 列数不匹配 + 3 财务)
3. t_analyze_one 截断显示前 5 条
4. 用户看不到列数不匹配, 以为 linter 没报 → bug 漏检
5. md 推 GitHub, GFM 严格按列数渲染 → 整张表不渲染

**修法 (2026-10-14 已改):** `for warn in warnings[:5]` → `for warn in warnings`. 截断是历史遗留 (避免刷屏), 但 linter 报 9 条以上时关键 warning 在后面.

**apply when:** 改 t_analyze_one 警告显示逻辑 / 改 linter warning 输出格式. 截断阈值 [:5] 别再留.

**加列/改 report_renderer.py 后必跑:**
```bash
bash tools/with_venv.sh python -m tools.render.report_linter all
```
现有 linter 已经有 3 类检测 (header/sep cell 数, data cell 数, cell 内未转义 `|`), 见 `tools/render/report_linter.py:389-408`. 不要再造重复轮子, 直接跑 linter `all` 看 🔴 warning. 0.x 秒,0 网络. 今天 KDJ 加列 3 次翻车都是这个, 改完不跑等于裸奔.

### SKILL.md 描述 vs 代码实现的兑现铁律 (2026-10-14 固化)
Type: rule
> **摘要:** SKILL.md 写的每个 flag/口径都必须在代码里兑现, 否则文档漂移

**反例 (今天 ETF 段):**
- SKILL.md 写"sync --auto 包含 ETF (4+ETF: kline/stk-factor/stock-basic/financials + ETF 日增量)"
- 实际: sync.py 没 --etf flag, action_auto 不检 ETF stale, --auto 跑完 ETF K线没更新
- 后果: 17 个 ETF md 永远停在 10/09 (用户每天手动调 sync_etf_incremental())

**铁律:** SKILL.md 写的功能必须由代码兑现. 改 SKILL.md 加描述时, 同时改:
- argparse (新 flag)
- action 调度 (实际跑该功能的函数)
- stale 检测 (让 --auto 也能自动跑)

**apply when:** 任何 "我在 SKILL.md 加了 X 功能" / "X 是默认行为" 的描述, 必须有对应代码. 没有就修, 不能留 SKILL.md 一边说"有" 代码一边没接. 静态扫描: `grep -n "add_argument\|auto 检测" tools/storage/sync.py` 跟 SKILL.md 的 9 个 flag 对照.

### EPS 机构一致预期整档删除 (2026-11-04)
Type: decision
> **摘要:** 同名不同物 —— **预测值** (datacenter E0~E3) vs **已审计真实 EPS** (fina_indicator), 别删错

**背景:** 用户问 "eps 是啥, 我要把他干掉"。查清后确认仓库里有**两个同名不同物**:
1. **EPS 机构一致预期** (eastmoney datacenter 拉机构 E0/E1/E2/E3 预测) — 要删的是这个
2. **财报真实 EPS** (`fina_indicator` 已审计, 存 `financials` parquet) — 算 `np_yi` + rsi6 亏损过滤在用, **不能动**

**为什么删 (决策依据):**
- 它是**预测值**, 而项目估值口径早已固化: 只看 PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆), 全部用已审计 TTM 财报算, 不依赖任何预测值
- 11-02 删 PEG/DCF 后, 它**已无任何分析消费者** — 只剩管道残骸 + 报告展示
- 副产物收益: 很多票的 EPS 拉不到 (EMPTY), 却占着数据完整性表一格。删掉后 002371 完整度 87% → **100%**

**删除范围 (11 处代码):**
| 层 | 删了什么 |
|---|---|
| L1 数据 | `tools/storage/caches/eps.py` (整个缓存层) / `eastmoney._build_eps_table` (唯一联网函数) / `sync.py --eps` flag / `DataStore.get_eps()` / `RawContext.eps_table` |
| L2 分析 | `RenderData.EpsRow` + `eps_table` + `eps_status` + 完整性表的 EPS 行 |
| L3 渲染 | 报告「EPS 一致预期」表 / linter 的 "EPS 表格" + "缺 EPS 一致预期" 检查 / section id `eps_finance` → `finance_quarterly` |
| 工具 | guardrail `eps-scope` check (整个) / `lgb_mine.py` PE_TTM+PB 改走 daily_basic / `backfill_roc_ey_cache._load_eps_window` (本就无调用方) |

**实测影响: 判定结果零变化。** 7 只票 (002371/300274/688256/600089/002463/300308/000725) 改动前后报告 diff:
Magic ROC/EY / 因子矩阵 **逐字节一致**, 差异只有 EPS 行 + EPS 表 + section id + 完整度分母。

**连带效应:**
- `eastmoney.py` 由此变成**纯计算模块** (周线合成 + 技术指标), guardrail network 白名单从 2 个入口缩到 1 个 (只剩 tushare)
- `datacenter.eastmoney.com` 退出合法源 → CLAUDE.md「合法源只有 2 个」改成「只有 1 个」
- 顺手修掉 `eastmoney.py` 的死 CLI 块 (引用 11-02 就删掉的 `fetch_all`/`status_emoji`, 一跑就 NameError)

**apply when:** 想删某个数据源前, 先分清「预测值」vs「已审计真实值」—— 同名不同物, 删错一个直接断生产链。
删完必须做**改动前后报告 diff**, 而不是只看 "跑通了" —— 跑通 ≠ 判定没变。

### RSI 双超卖扫描整族下线 (2026-11-04)
Type: decision
> **摘要:** 判定口径必须 == 展示口径, 否则表里显示的值会跟结论反向

**下线范围:** `/t-rsi6-tech` skill + `rsi6_tech_scan.py` + `rsi6_backtest.py` + `rsi6_backtest_history.py` + 回测产物 `docs/rsi6-*.md` (safe_fs 移回收站)。

**为什么下线 (实测, 不是拍脑袋):**
1. **输出自相矛盾**: 判定用「最近 lookback 根任一跌破过 RSI 阈值」, 但表里显示的是**最新** RSI6 值 (`cur6`)。
   实测 98 只命中里 **63 只显示 RSI6>35** —— 一张标题写着「RSI 超卖」的表, 2/3 的行看着完全不超卖。
   → **判定口径和展示口径必须是同一个值**, 否则用户按数字看会得出反向结论。
2. **无区分度**: 业绩过滤 09-23 就删了, 剩「纯 RSI 双超卖」对 321 只 watchlist 命中 98 只 = **31% 命中率**。
3. **占位 flag**: `--check-news` / `--news-days` 代码层**不实现** (MCP connector 是 LLM 内置, bash subprocess 不可用)。

**对生产链路零影响:** rsi6 是独立扫描器, 不参与威科夫/Magic 判定, 不被 `/t-analyze` 调用。删掉后 7 section 详报逐字节一致。

**技术超卖的替代路径:** `/t-analyze <code>` (单只详报含 RSI6/RSI12/KDJ 全套技术指标) 或 `/t-factor-5y` (长周期轨迹)。

**顺带:** 财报 EPS (已审计) 的亏损过滤随 rsi6 下线, 只剩 `analysis_engine` 算 `np_yi` 一个消费端。
评估过「换 n_income 直取」: **不划算** —— ① `financials` 表根本没有 `n_income` 列, 要新拉 income 表;
② 等价口径是「营收 × profit_to_gr」(`netprofit_margin` 字段是脏值, 600061 上是 6743 不是 26.46), 与 eps×股本 中位差 3.4%;
③ 但 **17% 的票差 >20%** (全是微利股, eps 精度不足), 4 季财务表的净利/PE/EY 三列都会动 —— 收益远小于风险, 不做。

**apply when:** 任何「扫描器命中率 >20%」的信号, 先查是不是判定口径和展示口径脱节了。
删 skill 时连带删互相 import 的同族脚本, 别留孤儿代码。

### 完整性表的「详情」行是设计词表, 别当残留占位符扫 (2026-11-05)
Type: rule

**症状:** 每次 `/t-analyze` 都刷同一条假 warning:
`⚠️ 占位符 [占位符未计算]: | 详情 | OK (¥0.874) | ... | 未计算 | OK |`

**根因:** linter 的占位符检测是**纯子串匹配** (`"未计算" in line`),
但「数据完整性」表的词表里 `"未计算"` 是**合法值** —— `render_data.completeness_report()` 里
`if not status_obj: report[key] = ("❓", "未计算")` 就是设计好的: 这张表本来就要如实报
"哪个维度算不出来" (指数/ETF 没财报 → Magic 维度未计算)。

**修法:** 精确豁免 `| 详情 |` 行 (该行头全仓库只有完整性表在用, 已 grep 确认)。
同类豁免已有先例: `"未完成"` → 豁免 `"金叉未完成"`。

**实测影响:** 全仓 550 份报告, 占位符 warning **15 条 → 0 条**,
**其它 857 条 warning 一条没动** (872 → 857, 差值恰好 15)。真占位符 (`Magic 未计算` /
`历史计算失败`) 仍全部抓到。

**apply when:** 加任何新的占位符检测时 —— 先问「这个词在**合法业务文案**里会不会出现」,
会就得加豁免, 否则每次跑都刷噪音, 久了就没人看 warning 了。

### skip_reason 没透传 → 报告把日期当成了"没算出来的原因" (2026-11-05)
Type: bug

**症状:** 300274 阳光电源报告的完整性表 Magic 单元显示 `ROC/EY=20260930` —— 用户完全看不懂。

**根因 (两层):**
1. `compute_magic_one_day()` **有** `skip_reason` (`revenue_profit_collapse` /
   `non_operating_loss` / `industry_excluded` / `no_data` / `capital_too_small`),
   但 `analysis_engine.FinanceStrategy` 组装 valuation_data 时**没带这个字段**。
2. `render_data` 于是退化成 `reason = skip_reason or period_label or "数据缺"`
   → 拿到 period_label (一个**日期**) 当原因。
   → 同理 `report_renderer` 的 `has_magic = bool(_val and not _val.get("skip_reason"))`
   这个分支**从来没被走到过** (skip_reason 恒为空) —— 写好的逻辑是死的。

**修法:**
1. `analysis_engine` 补 `"skip_reason": latest_magic.get("skip_reason")`
2. `render_data` 把 reason 映射成人话 (`_magic_skip_text`): 营收+净利双暴跌 / 营业外巨亏 /
   行业已排除 / 该季无财报数据
3. `report_renderer` 把门控拆成两个 —— `has_val` (显示跟踪期 + 行业) 与
   `has_magic` (只控制"判定"说明), 否则 Magic 被跳过时连行业和期号都一起没了

**实测影响:** 321 只 watchlist 里 **307 只 (96%) 逐字节不变**, 只有 11 只 Magic 被跳过的票
换了文案 + 隐藏"判定"说明。300274 的真实原因是 `revenue_profit_collapse`
(20260930 营收 -26.4% + 净利 -44.5%, 触发质量闸门 or_yoy<-20 且 np_yoy<-30)。

**⚠️ 注意:** Magic 没算出来**不是 bug**, 是质量闸门在起作用 (主业崩塌时 ROC/EY 本就无意义)。

**apply when:** 报告里任何"字段在但值是 None"的情况 —— 先查**上游有没有把原因字段传下来**。
`or period_label` 这种"拿别的字段兜底"的写法, 会把"原因"变成噪声。

### 因子历史表改行数: dates 必须覆盖 lookback, 否则整行被静默丢弃 (2026-11-05)
Type: rule

**症状:** 因子历史走势表要 200 行 (ETF), 改完 `compute_factor_history(lookback=200)` 后**表里还是 120 行**,
不报错、不告警。

**根因:** `compute_factor_history` 的行来自 `history[date]`; 那个日期不在 `history` 里 → `result is None`
→ **`continue` 整行跳过** (不报错!)。而 `history` 是 `analyze_history(ctx, dates)` 算的,
`dates` 传 120 天 → 就没有第 121~200 天的行。

**修法:** `dates` 必须 ⊇ 回看天数。批量入口原本写死 `all_dates[-120:]`, 改成
`all_dates[-max(factor_history_lookback(code), 120):]` (ETF 200 / 股票 120 不变)。

**验证:** 同一只 ETF 走两个批量入口 (`t_analyze_one` dates=480 / `t_analyze_all` dates=200)
→ 报告 **0 行差异** (两个入口必须一致, CLAUDE.md 硬规则)。

**⚠️ 顺带发现:** `report_renderer._section_factor_history(data, lookback=120)` 的 `lookback` 参数
**根本没被用到** (rows 全从 `data.factor_history_rows` 来) —— 是死参数, 别照着它去改行数。

**apply when:** 任何"表格行数由 lookback 控制"的地方 —— 先确认**数据源覆盖范围 ≥ 展示范围**,
否则多出来的部分会被静默吞掉, 看起来像是"参数没生效"。

### 批量脚本默认只出汇总, 别逐只落 md (2026-11-06)
Type: rule
> **摘要:** 批量跑默认落 1 份汇总表; 逐只 md 只在"点名"时出 —— 否则目录被撑爆, 而且没人会翻

**事故实录 (2026-11-06):** 用户要求把因子历史表的 3 列(威科夫(日/周) / 威科夫强度 / 子事件(日/周))
移到收盘价右边。我为了"验证列序"跑了
`python -m tools.batch.factor_5y --watchlist-types 持仓,自选 --workers 4`
→ 8 分钟生成 **168 份 md / 49MB** (每只 1250 行 × 当时 24 列 ≈ 290KB)。
我自己写下的意图明明是"45 份 (持仓 21 + 自选 20 + 4 只历史票)", 命令却写成了"持仓,自选"全量。

**三个错:**
1. **验证列序不需要批量跑** —— 抽 1~2 只样本就够, 164 只纯属浪费 8 分钟
2. **批量场景的产物形态错了** —— 批量是"多只横向对比找穿越牛熊的票", 该出**汇总表**,
   不是 164 份逐只长表 (每只 290KB, 要横向比还得一只只开)
3. **手工维护的"批量范围"参数** —— `--watchlist-types 持仓,自选` 一次多拉 143 只, 没人复核

**修法 (已落地):**
- `factor_5y.py` 输出分两种: **点名** (`--code`/`--codes`) → 逐只详细 md;
  **按范围批量** (默认) → **只出 1 份汇总 md** `_summary-{scope}.md` (每只 1 行:
  5年累计% / 最大回撤% / 当前RSI6 / 5年最低RSI6 / 阶段(日/周) / 阶段转变数 / 行数)
- 想逐只落 md 要显式 `--write-md-all` (且会清掉不在本次范围的旧 md)
- SKILL.md 加「输出规则」表, 写死哪种场景落哪种产物

**口径提醒:** 汇总表的"阶段转变数"要跟 `diff_rows` 的 `wyckoff_daily` 同口径 (**相邻两行阶段不同**),
不能用"非空行数" —— 那是把每天的阶段都数一遍, 会数出 200+ 这种假数字。

**apply when:** 任何候选池 > 20 的批量脚本。默认落汇总表, 逐只 md 留给"点名"场景。

### 候选池段 (list_type=blowout) 不落 md, 只进汇总表 (2026-11-06)
Type: rule
> **摘要:** blowout 是"业绩暴增候选池", 用来筛的 —— 筛完看汇总表就行, 别给 157 只各存一份详报

**事故实录 (2026-11-06):** 我跑了 `t_analyze_all --include-blowout` → 157 份 blowout 详报
(9.1MB) 落进 `docs/blowout/`。用户当场纠正: **"list_type=blowout 不生成 md 的呀"**。

**为什么 blowout 段不该落 md:**
- blowout 段 = 业绩暴增候选池, 是**筛选用**的输入, 不是持仓/自选那种"长期跟踪的票"
- 筛选结论要的是**对比** (谁值得买), 对比看 stdout 的 buy/sell 汇总就够
- 157 份 × 58KB 详报, 没人会一只只翻, 但目录里就是 157 个"看起来很重要"的噪声文件

**落地 (t_analyze_one + t_analyze_all 两个入口):**
- `list_type == 'blowout'` → `subdir = None` → **不写文件**, 报告走 stdout / 汇总表
- `t_analyze_all` 的孤儿清理: `_expected_by_sub['blowout']` 恒为空 → `docs/blowout/` 全清
- **`--include-blowout` flag 直接删掉** (2026-11-06) —— 批量跑永久只含持仓+自选,
  没有开关能把候选池拉回批量详报; 要深挖就单只 `/t-analyze <code>`
  (教训: "默认排除 + 一个开关能打开" 迟早被打开, 与其加开关不如把开关删了)
- `_verify_weekly_period.LINT_SUBS` 去掉 blowout (目录已废弃)
- 显式 `--output-subdir` (如 wyckoff-a2m) 仍然落 md —— 那是用户显式要求的目录

**apply when:** 任何 watchlist 分段。**先问这段是"要长期跟踪的"还是"一次性筛的"** ——
后者只进汇总表, 不占目录。

### 下线一个功能: 连带清理清单 (2026-11-06)
Type: rule
> **摘要:** 删一个功能至少要清 6 处, 漏一处就留下"看起来还在用"的死路径

**触发场景 (2026-11-06 一天连踩 3 次):** blowout 落 md → 删 `--include-blowout` flag → 删 ETF MACD 扫描段。

**连带清单 (逐条 grep 确认, 别凭印象):**
1. **主脚本/模块本身** —— `safe_fs.safe_delete` (移 `~/.Trash`, 永不 unlink)
2. **CLI flag** —— 功能没了 flag 就是死开关, 一起删 (`--no-etf` / `--include-blowout`)
3. **产物目录** —— 跟着删, 别留空壳 (`docs/etf-macd/` 72 份 / `docs/blowout/`)
4. **调用方的 import + 那一整段** —— 删 import 不删调用段 → ImportError; 删调用段不删 flag → 死 flag
5. **汇总文件里的那段** —— 例: `docs/factor-5y/_summary-{scope}.md` 的表头行
6. **跨文件引用** —— 注释 / docstring / SKILL.md / CLAUDE.md / handbook, 留着就误导后人
7. **并行的第二套实现** —— `app/` 是独立的 FastAPI + LangGraph 服务层, `tools/` 下线某个维度时
   它可能还留着**另一份完整实现**。2026-11-06 实测: fflow 10-28 下线时漏了 `app/`,
   整个 `app/signals/capital.py` (主力资金连续净流入 / 北向异常 / 机构席位 / ETF 申购)
   一直活到今天; `app/` 在 README / CLAUDE.md / architecture.md 里**一个字都没提**,
   光扫 `tools/` 会以为"全仓都清了"
   → 下线前必须 `grep -rn <符号> app/ btf/ tests/`, **不只看 `tools/`**
   (附: `app/` 还长期缺 pydantic/langchain_core 跑不起来, 且 `tushare_provider.py`
   有 4 个 `cache_key_*` 根本不存在 —— 它本身就是死代码, 别拿它当"还有人在用"的证据)

**共享模块留不留, 看消费者数量:** `etf_universe.py` 被两个扫描器共用 → 删掉其中一个扫描器时
**它保留** (另一个还在用), 只把 docstring 里"两个扫描器共用"的描述改掉;
变成 **0 消费者** 的模块才删。删之前先数一遍消费者。

**验证纪律:**
- 改动前后**逐行 diff** (归一化掉耗时 / 时间戳 / 并发完成顺序), 别只看"跑通了"
- 冒烟测**幸存的那个**功能 (ETF A→M: 2252→1992→1602 只可扫, 10.2s, 命中 11→去重 8, 与 SKILL.md 一致)
- `guardrail --check {network|factor|parquet|safe-fs}` 4 项全绿

**apply when:** 任何"下线 / 删除一个功能"的动作。

### 配置/文档声称的数据源 ≠ 代码实际调用 (2026-11-06)
Type: rule
> **摘要:** 报告里「数据源矩阵」这类声明型配置, 每一行都要 grep 回代码核实 —— 删功能后没人回来改它

**触发场景:** 用户发现报告「📡 数据源矩阵」内容是错的。逐条 grep 回
`tools/storage/sources/tushare.py` + `tools/storage/store.py` 核实, 发现
`config/sources.yaml` (2026-07-24 固化, 一年没动) 有 **4 处与代码不符**:

| 声称 | 实际 |
|---|---|
| kline_60min → Sina | 代码 0 处 Sina 调用 (60min 已无消费者) |
| kline_weekly → Tushare.weekly | 日线**合成** (`_synthesize_weekly`), weekly 只在死代码里 |
| real_time → daily_basic 实时价 | 实时价 = 日 K 线最新收盘, daily_basic 只给 PE/市值/换手率 |
| industry_sector → 行业 + 概念板块 | stock_basic 只有 industry, 概念板块走 THS |

**反模式: "配置即真相"。** `config/sources.yaml` 顶部写着
「所有数据 fetch 必须按本表声明的源拉取, 不在表里的数据 = 未支持」——
听着像权威, 实际是**一年前的手工快照**, 没有任何机制保证它跟代码同步。

**核实方法 (每行都要做):**
```bash
# 1. 这个源在代码里还有调用吗?
grep -rn "<源关键字>" tools/ --include="*.py" | grep -v __pycache__
# 2. 文件还在吗? (白名单/配置里的路径可能早就没了)
[ -f <path> ] || echo "死引用"
# 3. 真的被读吗? (加载了 ≠ 用了 —— eastmoney.py 加载 project.yaml 但一次没用)
grep -rn "<变量名>" tools/ --include="*.py" | grep -v __pycache__
```

**顺带会查出来的同类漂移 (本次一起修的):**
- section 标题承诺「状态」列, 表里根本没有 → 标题改成实际列名
- `config/project.yaml` 里的死配置 (`kline_60m_count` / `daily_basic_days`, 0 处引用)
- `guardrail.py` 的 `NETWORK_ALLOWED` 里 3 条**文件已不存在**的路径 + 1 条已纯计算的模块

**apply when:** 任何"声明型配置" (数据源表 / 白名单 / 标题文案 / 口径说明) 呈现给用户之前,
或者下线一个功能之后 —— 声明不会自己跟着代码走, 只能靠 grep 回去核实。

### 删死代码会制造新的死代码 + 多轮替换会毁掉编号 (2026-11-06)
Type: rule
> **摘要:** AST 扫出来的死代码要**删完再扫一遍** —— 删掉消费者会让它依赖的缓存/辅助函数当场变孤儿

**触发场景:** 清理 12 个死函数 + 1 个死模块 (`tools/batch_runner.py`)。
两个自己踩的坑, 都不是 AST 能查出来的:

**坑 1: 删消费者 → 依赖方变孤儿 (同一轮内产生)**
删 `factor_lib.batch_magic_scores` (全市场 Magic 批量排名) 之后, 它的唯一依赖
`_load_bulk_caches` + 模块级缓存 `_FIN_DICT` / `_SB_DICT` / `_DB_DICT`
**当场变成死代码** —— AST 扫描是**改前**跑的, 看不见改后新产生的孤儿。
另外还带出一个 2026-09-09 就存在的**孤儿小节头**「板块过热预警」,
它下面的功能早在 2026-11-05 搬到 `analysis_engine._derive_sector_overheat` 了,
只剩个空标题。

**解法:** 删完**必须再跑一遍 AST 扫描** (改前扫 ≠ 改后干净)。
不要相信「第一次扫出 12 个, 我删了 12 个, 就完事了」。

**坑 2: 多轮字符串替换把编号改废**
删掉 `helpers.py` 的 Section 1 后要把 Section 2..8 重编号成 1..7。
用 `sed` 倒序替换 (8→2) 会**级联** (8→2→1→…→0),
第二轮「全部 -1」又把所有 Section 变成 **Section 0**。
→ **必须单次遍历、按出现顺序重编号**, 不能多轮替换:
```python
n = 0
for m in re.finditer(r"# Section \d+:", s):   # 按出现顺序
    n += 1
    s = s[:m.start()] + f"# Section {n}:" + s[m.end():]
```

**死代码判定的已知误报 (别当真的删):**
| 误报 | 原因 |
|---|---|
| `__init__` | 构造函数, 扫描器把 dunder 也当死函数 |
| `tools/factors/utils.py` 4 个函数 | 被 `tests/unit/test_factors_utils.py` 用 (pytest 收集的 tests/ 要跳过) |
| `report_schema.get_section_ids` | 在 `report_linter.py` 里**函数内 import**, 扫描器只扫模块级 |
| btf 4 个空方法 | ABC 抽象方法, 子类都有覆盖 —— **保留** |

**验证纪律 (比删本身更重要):**
改动前后**逐字节 diff 生成的报告**, 别只看「跑通了」:
```bash
cp docs/watchlist/analyze-<code>-*.md /tmp/after.md
git stash push && python -m tools.batch.t_analyze_one --code <code> && cp ... /tmp/before.md
git stash pop && python -m tools.batch.t_analyze_one --code <code>
diff /tmp/before.md /tmp/after.md     # 必须完全一致
```
本次 300588 / 002371 / 600089 三份报告改动前后**逐字节一致**才算过。

**apply when:** 任何"删死代码 / 下线功能"的动作。

### 删参数前:「AST 说没用」≠「调用方没在传」 (2026-11-06)
Type: rule
> **摘要:** AST 只能证明参数**函数体内**没用, 证明不了外部没在传 —— 删参数前必须 grep 全仓调用点 + 跑一遍受影响的 skill 入口

**触发场景:** 清理冗余参数/变量, 删了 ~30 个参数。
**3 个参数在函数体内确实没用, 但外部调用点正在传值**, 删完签名就炸:

| 被删参数 | 炸的调用点 | 报错 |
|---|---|---|
| `_section_factor_history(lookback=)` | `tools/batch/factor_5y.py:116` | `unexpected keyword argument 'lookback'` |
| `_section_factor_history(lookback=)` | `tools/render/factor_history_only.py:35` | 同上 |
| `_process_one(lookback=)` | `tools/batch/_eval_a2m_hits.py:51` | `takes 2 positional arguments but 3 were given` |

**为什么 AST 看不出来:** AST 扫描只统计**参数在函数体内部**有没有被 Load。
外部调用方传了 `lookback=1250` 而函数体忽略它 —— 函数体视角看是「冗余」, 调用方视角看是「在传值」。
**「被传但被忽略」是比「没传」更危险的一种**: 改签名直接炸, 不改则永远是静默的假参数。

**自造验证工具本身也会假绿 —— 3 个坑:**
1. **绑定检查中断**: 用 `except (ValueError, TypeError)` 包 `inspect.signature`, 一个函数抛别的异常
   → 整个 defs 构建循环**提前中断** → 只扫到 108/1223 个调用点 → 报「0 失败」是**假绿**。
   → 教训: 校验器的单点异常**必须兜住**, 否则「没报错」= 「没检查到」。
2. **import 副作用**: `tools/batch/t_analyze_all.py` **无 `if __name__ == '__main__'` 保护**,
   `importlib.import_module` 它会**直接跑全市场扫描** → 校验器被拖慢 + 污染产物。
   → 校验器要先排掉这类模块。
3. **缺依赖**: `app/` 依赖 pydantic / langchain / fastapi, 本地没装 → **导入式校验根本覆盖不到 app/**。
   → 正解是**纯 AST 建签名**(不 import 任何模块), 覆盖率才是 100%。

**正解 (三件套, 缺一不可):**
```bash
# 1. grep 全仓: 参数名 + 函数名 两个维度都搜
grep -rn "<func>(\|<param>=" --include=*.py . | grep -v "^./.venv"

# 2. 纯 AST 绑定检查 (不 import → 不受缺依赖/sys.exit 影响)
#    对每个调用点, 试所有同名定义, 全绑不上才报错
#    → 本轮: 1223 个调用点, 抓出上面 3 个真 bug

# 3. 改动前后逐字节 diff + **逐个跑受影响的 skill 入口**
#    (签名改动只会在某条具体链路上炸, 必须真跑)
bash tools/with_venv.sh python -m tools.batch.factor_5y --codes 300588 --write-md-all
bash tools/with_venv.sh python tools/render/factor_history_only.py 300588.SZ --days 60
```

**本轮实际抓到 3 个真 bug 靠的就是第 3 步** —— 光看报告 diff 全绿 (164 份逐字节一致),
但 `/t-factor-5y` 和 `factor_history_only` 这两条**不进 /t-analyze 报告的链路**已经炸了。
→ **改签名后, 把所有引用该函数的 skill 入口都跑一遍**, 别只跑出报告的那条。

**apply when:** 任何"删函数参数 / 改函数签名"的动作。

### 删术语前先确认它在该上下文的真实语义 (2026-11-06)
Type: rule
> **摘要:** grep 命中的术语**不能按字面批量删** —— 同一个词在不同上下文里可能完全是另一回事

**触发场景:** 清理 md 层残留的 缠论 / SMC / 结构分析 / OB / FVG / Sweep / 扫流。
`grep -ril "OB"` 之类会捞出两类**同名不同物**的假目标:

| 词 | 看起来像 | 实际是 | 处置 |
|---|---|---|---|
| `chan_` / `chan` | 已下线的缠论 | **威科夫通道** (channel) 缩写 —— 生产侧字段名就叫 `channel`, 插桩 tag `('chan', ...)` 与读取端 `k == "chan"` 必须成对 | **别改** (豁免表见 `handbook/naming-convention.md`) |
| `OB` | 订单块 (Order Block) | alpha101 的 `OB` = (open+close)/2, 在 `tools/research/ga_mine.py` | **别改** |

反过来, 真要删的 `signal_tracker.py` 里 `MODEL_INFERENCE` 的 `('结构', ['OB','FVG','扫流','Demand','Supply','BOS','CHoCH'])`
—— 那是 SMC 的模型推断条目, 2026-10-28 删 PEG 时同样删过 `MODEL_INFERENCE` 里的 'PEG' 条目, 同一套路。

**做法:**
```bash
# 1. 先全量列出命中, 按「文件 + 行」逐条看上下文, 再决定删/改/留
grep -rn "<术语>" --include=*.md --include=*.py --include=*.yaml . | grep -v "^./.venv"
# 2. 删除只动「纯墓碑注释」(纯说明, 无行为)
# 3. 行为代码里的条目 → 参考同类的历史删除先例 (PEG 删法)
# 4. 误报 → 在 naming-convention.md 的豁免表里留一条, 避免下一个人再踩
```

**验证:** `config/sources.yaml` 会被报告渲染器读 (数据源矩阵), 改注释也要重跑全量对报告做逐字节 diff。

**apply when:** 任何"按术语批量清理残留"的动作。
