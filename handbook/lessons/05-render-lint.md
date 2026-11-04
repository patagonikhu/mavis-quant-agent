# 报告渲染 / linter / 文档一致性

> 本文件是 `handbook/project-lessons.md` 的主题分册 (05-render-lint.md)。
> 索引 (全 47 条一句话摘要) 见 [`handbook/lessons/README.md`](README.md)。
> 用法: **grep 查**, 不用通读 —— 提到具体话题先在这里搜关键词。

---


### markdown table header / sep 写死脱节 bug (2026-10-14 固化)
Type: rule
> **摘要:** 表格列数变了要**同步改表头 + 分隔行 + 行拼接**三处

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
缠论 / Magic ROC/EY / 买卖点 / 因子矩阵 **逐字节一致**, 差异只有 EPS 行 + EPS 表 + section id + 完整度分母。

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

**对生产链路零影响:** rsi6 是独立扫描器, 不参与缠论/Magic/买卖点判定, 不被 `/t-analyze` 调用。删掉后 7 section 详报逐字节一致。

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
`缠论未计算` / `历史计算失败`) 仍全部抓到。

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
→ 8 分钟生成 **168 份 md / 49MB** (每只 1250 行 × 24 列 ≈ 290KB)。
我自己写下的意图明明是"45 份 (持仓 21 + 自选 20 + 4 只历史票)", 命令却写成了"持仓,自选"全量。

**三个错:**
1. **验证列序不需要批量跑** —— 抽 1~2 只样本就够, 164 只纯属浪费 8 分钟
2. **批量场景的产物形态错了** —— 批量是"多只横向对比找穿越牛熊的票", 该出**汇总表**,
   不是 164 份逐只长表 (每只 290KB, 要横向比还得一只只开)
3. **手工维护的"批量范围"参数** —— `--watchlist-types 持仓,自选` 一次多拉 143 只, 没人复核

**修法 (已落地):**
- `factor_5y.py` 输出分两种: **点名** (`--code`/`--codes`) → 逐只详细 md;
  **按范围批量** (默认) → **只出 1 份汇总 md** `_summary-{scope}.md` (每只 1 行:
  5年累计% / 最大回撤% / 当前RSI6 / 5年最低RSI6 / 阶段(日/周) / 5年买卖点 / 行数)
- 想逐只落 md 要显式 `--write-md-all` (且会清掉不在本次范围的旧 md)
- SKILL.md 加「输出规则」表, 写死哪种场景落哪种产物

**口径提醒:** 汇总表的"买卖点数"要跟 `diff_rows` 的 `new_bsp_daily` 同口径 (**key 新出现**),
不能用"bsp_daily 非空的行数" —— 那是"活跃买卖点"每天在, 会数出 200+ 这种假数字。

**apply when:** 任何候选池 > 20 的批量脚本。默认落汇总表, 逐只 md 留给"点名"场景。

### 候选池段 (list_type=blowout) 不落 md, 只进汇总表 (2026-11-06)
Type: rule
> **摘要:** blowout 是"业绩暴增候选池", 用来筛的 —— 筛完看汇总表就行, 别给 157 只各存一份详报

**事故实录 (2026-11-06):** 我跑了 `t_analyze_all --include-blowout` → 157 份 blowout 详报
(9.1MB) 落进 `docs/blowout/`。用户当场纠正: **"list_type=blowout 不生成 md 的呀"**。

**为什么 blowout 段不该落 md:**
- blowout 段 = 业绩暴增候选池, 是**筛选用**的输入, 不是持仓/自选那种"长期跟踪的票"
- 筛选结论要的是**对比** (谁值得买), 对比看 `signal-watchlist.md` 汇总表就够
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
