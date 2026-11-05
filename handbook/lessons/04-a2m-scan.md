# A→M 扫描 / watchlist

> 本文件是 `handbook/project-lessons.md` 的主题分册 (04-a2m-scan.md)。
> 索引 (全 47 条一句话摘要) 见 [`handbook/lessons/README.md`](README.md)。
> 用法: **grep 查**, 不用通读 —— 提到具体话题先在这里搜关键词。

---


### /t-wyckoff-a2m: compute_factor_history 只算威科夫, 不算其他因子 (2026-10-16 固化)
Type: rule
> **摘要:** 它只算威科夫, 别指望它给别的因子

**铁律:** `/t-wyckoff-a2m` skill 跑全市场扫描时, **compute_factor_history 只算 wyckoff stage 一个因子**, 不要拉满 8 指标 (PE/PB/RSI/KDJ/MACD/BOLL/ATR/量比).

**为什么:**
- A→M 判定只依赖 wyckoff stage 分类 (Accumulation / Markup / Distribution)
- 拉满 8 因子 = 80% 算力浪费 (其他 7 个跟 A→M 无关)
- 跑全市场 5812 只时, 拉满 8 因子 = ~8x 慢 (60s → 480s)

**反例 (2026-10-16 初版差点犯):**
- factor_5y.py 走 `compute_factor_history(ctx, step, lookback=1250, history=history)` 拉满 8 指标
- wyckoff_a2m_scan.py v1 已经直接调精简版 wyckoff_judge (不算 sub_events) 绕开了
- 如果哪天扩展到用 compute_factor_history, 记得传 `factors=['wyckoff']` 或类似白名单参数

**apply when:** 任何批量扫威科夫阶段 / 阶段转变的脚本. 不要复用 factor_5y 的 compute_factor_history 调用方式 (它会拉满 8 指标).

### /t-wyckoff-a2m: 不要自己写估值/PE/财务过滤策略 (2026-10-16 固化, 用户纠错)
Type: rule
> **摘要:** 复合过滤复用现有 `/t-*` skill, 别在扫描器里新加子模块 (用户纠错)

**铁律:** `/t-wyckoff-a2m` 的 A→M 扫描命中后, 想要"PE 低估 + A→M" / "业绩暴增 + A→M" 之类的复合过滤, **复用现有 `/t-*` skill**, 不要在 wyckoff_a2m_scan.py 里新加 PE/估值/fflow 子模块.

**为什么:**
- 现有估值链路: `/t-finance-roc-ey` (PE 静态 + ROC/EY 盈利质量, ~~PEG/DCF L~~ 已于 2026-11-02 整档删除) + `/t-finance-earnings-blowout` (业绩)
- 复合过滤 = A→M 名单 + 上面 skill 的交集 (LLM 端用 watchlist 交集或 md grep 都行)
- 重复造 = 8x 慢 + 数据一致性漂移 + 多维护点

**反例 (2026-10-16):**
- LLM 提: "加 --with-pe flag, 命中 A→M 后只补算 PE 过滤 PE>50"
- 用户反驳: "不要自己写策略, 你没记性么? 这是重复造轮子"
- v1 精简版 `_stage_at()` 自己重算 MA + is_uptrend 阈值, 跟 stage_factor.wyckoff_judge 1:1 但不是同一份代码
- v1 10x 提速的代价 = 跟 /t-analyze 详报数据漂移风险 (详报报 Accumulation, 扫描报 Markup)
- 用户再次纠错: "wyckoff_a2m_scan.py 要复用 compute history"
- v2 改: 走 AnalysisEngine(strategies=[WyckoffStrategy]) + compute_factor_history, 跟 /t-analyze 100% 一致

**正确做法:**
- A→M 命中 → 落 docs/wyckoff-a2m.md 名单
- 跟 `/t-finance-roc-ey` 输出的 4 季财务大表 grep 交集
- 跟 `/t-finance-earnings-blowout` 输出的暴增名单 grep 交集
- ~~跟 `/t-rsi6-tech` 输出的技术超卖名单 grep 交集~~ (2026-11-04 该 skill 已下线, 技术超卖改走 `/t-analyze` 单只详报看 RSI)
- 复合结果在 LLM agent 端拼, 不在 wyckoff_a2m_scan.py 里拼
- /t-wyckoff-a2m 内部: 复用 AnalysisEngine + compute_factor_history, **不自己写 stage 判断器**

**apply when:** 任何 `/t-wyckoff-a2m` 的"扩展"想法 (加 PE / 加 KDJ / 加 fflow 子过滤), 都先 grep 现有 `/t-*` skill 是否已有同类输出, 复用, 不新写. /t-wyckoff-a2m 内部也禁止自己写阶段判断器, 必须复用 compute_factor_history.

### /t-wyckoff-a2m: 1 次 SQL 全市场 K 线 + 1 次 sub_events 全段扫 + 缓存 (2026-10-16 v6 固化)
Type: rule
> **摘要:** 性能基线: 1 次 SQL + 全段扫 + 缓存 → 全市场 ~30s

**铁律:** 批量扫威科夫阶段转变 (`/t-wyckoff-a2m` 风格) 必须用 v6 batch 模式, 不要在循环里调 DataStore / scan_sub_events 多次.

**正确链路 (v6, 全市场 ~25s):**
```python
# 1. 1 次 SQL 拿全市场 K 线 (替代 5812 次 DataStore.get_kline 各读 27 parquet)
sql = """
    WITH ranked AS (
        SELECT ts_code, trade_date, open, high, low, close, vol, pct_chg,
               ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
        FROM read_parquet('data/history/daily/*.parquet')
    )
    SELECT ... FROM ranked WHERE rn <= 250 ORDER BY ts_code, trade_date
"""
df_all = duckdb.execute(sql).fetchdf()  # 0.5s 拿 5812 票

# 2. 1 次 scan_sub_events 全段扫 (as_of_idx=None) + 缓存
sub_events_all = scan_sub_events(closes, highs, lows, vols, rng, o=opens, pct_chg=pct_chgs, as_of_idx=None, ...)

# 3. 循环每只, 不同 as_of_idx 时 过滤 sub_events (i <= as_of_idx), 传 precomputed_sub_events_raw
for k in range(lookback + 1):
    as_of_idx = n - lookback - 1 + k
    sub_events_filtered = [e for e in sub_events_all if e['idx'] <= as_of_idx]
    result = WyckoffStageFactor().compute(df, as_of_idx=as_of_idx, precomputed_sub_events_raw=sub_events_filtered, ...)
```

**反例 (v5, 全市场 ~2 分钟):**
```python
# 错: 循环每只票 6 次调 WyckoffStageFactor.compute, 每次都重扫 sub_events
for k in range(6):
    result = WyckoffStageFactor().compute(df, as_of_idx=as_of_idx)  # ❌ 内部又扫一遍 sub_events
```
5x 慢. 原因: scan_sub_events 是 O(n) 扫 250 根 K 线 + 9 种 detector, 循环里调用就重复 6 次.

**反例 (v4, 全市场 ~7 分钟):**
```python
# 错: 5812 次 DataStore.get_ctx → 内部 5812 次 _read_kline 各读 27 parquet 文件
for code in codes:
    ctx = DataStore.get_ctx(code)  # ❌ 5812 次重复 parquet read
```
200x 慢. 原因: duckdb 每次 query parse + 元数据查询 + read 27 文件, 即使内部有缓存, 5K+ 次累积也是分钟级.

**关键 API:**
- `WyckoffStageFactor().compute(df, as_of_idx=N, precomputed_sub_events_raw=events)` — stage_factor.py 已支持
- `scan_sub_events(..., as_of_idx=None)` — 扫整段; as_of_idx=N 时扫到 i <= N
- stage_factor.py line 427 fix: `self.config` → `getattr(self, 'config', {}) or {}` (直调 WyckoffStageFactor 不再 `?`)

**apply when:** 任何批量扫威科夫阶段 / sub_events / 阶段转变的全市场脚本. /t-wyckoff-a2m 的 v6 是参考实现.

### /t-wyckoff-a2m: 走 WyckoffStageFactor.compute() 直调会数据漂移, 必须用 AnalysisEngine (2026-10-16 固化)
Type: rule
> **摘要:** 必须用 AnalysisEngine, 直调 compute() 会数据漂移

**铁律:** `/t-wyckoff-a2m` 判定 stage 必须走 `AnalysisEngine(strategies=[WyckoffStrategy]).analyze_history()` 链路, **不能直接调 WyckoffStageFactor().compute()**.

**反例 (v6 直调, 2026-10-16):**
- 我为了"快"绕开 AnalysisEngine, 直接调 `WyckoffStageFactor().compute(df, as_of_idx=i, ...)` 拿 stage
- 实测 000301: v4 报 09-28~10-12 Accumulation (有 A→M 转变), v6 直调报全 Markup (漏报)
- 漂移 30%+: 654 只当前 M 票, v6 直调全部判 5 天都 M, v4 报 0 命中, 实际 v4 能找到 A→M 转变

**根因 (深度调查后):**
1. **window 参数差异**: v4 (WyckoffStrategy.analyze_history) 内部用 `_judge_full(c, h, l, v, sub_events, window, period_label)`, c = closes[-window:]; v6 直调传 window=min(250, n) 跟 v4 一致, 但传 window=n (1520 根) 会算全段 MA200, 跟 v4 算最近 250 根 MA200 不一样.
2. **precomputed_sub_events_raw 语义**: 传全部历史 sub_events (e['idx'] <= as_of_idx 过滤) 跟 v4 内部传的部分历史不同. v6 拿太多 sub_events, 触发 score_M 跳变, 误判 Markup.
3. **as_of_idx 索引**: v6 直调用 df 的 0-based idx, v4 用 ctx.kline 的日期索引. 两边 K 线长度不同 (v4 1520 根, v6 之前用 _load_all_klines(limit=250) 只 250 根) 导致 MA200 计算基准不同.

**正确做法 (回退 v4 路径):**
```python
ctx = DataStore.get_ctx(code, kline_only=True)
all_dates = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
dates = all_dates[-480:]  # 跟 /t-analyze 同源
engine = AnalysisEngine(strategies=[WyckoffStrategy])
history = engine.analyze_history(ctx, dates)
rows = compute_factor_history(ctx, step=1, lookback=lookback + 10, history=history)
stage = row["wyckoff_daily"]  # 跟 /t-analyze 详报 100% 一致
```

**性能:** 5812 × 0.07s = 7 分钟 (单进程顺序, 不能 ThreadPool - duckdb 锁死).

**v6 "快"路径 (--mode fast, 默认不用):**
- 1 次 SQL 全市场 K 线 (1.04s)
- 每只 1 次 scan_sub_events 全段扫 (as_of_idx=None) + 缓存
- 6 次 WyckoffStageFactor.compute 不传 precomputed, as_of_idx 不同, 每次重扫 sub_events
- 性能: 5812 × 0.1s = 10 分钟 (实际比 v4 慢, 但不漏报)
- 不准: stage 跟 v4 漂移 30%+, 漏报中途回 A 再转 M 的票

**apply when:** 任何批量扫威科夫阶段的脚本, 不要图快直接调 WyckoffStageFactor.compute(). 必须走 AnalysisEngine + compute_factor_history 链路 (memory 里 v1 反例, 这次的 v6 直调是 v2 反例).

### /t-wyckoff-a2m v8: scan_sub_events 全段扫是物理下界瓶颈 (2026-10-16 固化)
Type: fact
> **摘要:** 再优化也快不过它, 优化前先看这条

**实测 (580 票 / 5812 推算):**
- 单只 ~75ms (arrs 13ms + scan_sub_events 50ms + wyckoff_judge × 6 = 0.08ms)
- 5812 票 ~5.3 分钟 单进程
- ThreadPool 1/4/8 worker 实测 32s/32s/33s —— **0 加速**
- **物理下界**: scan_sub_events 内部跑 9 detector, pandas/numpy 计算 GIL 占满

**为什么 v6 报 25s 错了:**
- v6 当时只测了"用 arrs 预算走 WyckoffStageFactor.compute(),但漏报 30% (000301 案例)"
- 误把"compute 用 arrs 不重算"当作 25s 的来源,实际 scan_sub_events 占 67% 时间
- v7 回退 v4 路径重测, 7 分钟 → 5.3 分钟 (v8)

**已确认的实情:**
- _judge_full 跟 wyckoff_judge 末端数值 100% 一致 (实测 5 票 5 周期全等)
- _judge_full 已删 (2026-10-16), 唯一入口 wyckoff_judge
- v8 直调 wyckoff_judge + arrs 预算 (分析引擎代码链路跟以前一样, 只是跳过了 history 重新算)

**想真到 30s 必须动:**
- A. 加缓存层 (每天首次 5 分钟落 parquet, 后续秒级读) + staleness 跟 K 线同步
- B. 删 detector 几个冷门 (tr_pullback / compression), loss 风险用户不接受
- C. 默认 --watchlist-only, 全市场加 --force-all-market 警告

**apply when:** 任何 /t-wyckoff-a2m 性能优化/加快提议. 不要再说"用 ThreadPool 加速 30s",已实测 0 加速. 不要再试"把 scan_sub_events 用 numpy 向量化", 工程量大且 GIL 仍占.

### /t-wyckoff-a2m 过滤链收紧 (2026-10-28 落地)
Type: rule
> **摘要:** ST/PE/质量/杠杆各档阈值 + 实测命中数 (调参时对着看)

**最终默认过滤链 (5 步, 全部 1 次 SQL/步, 0 网络):**
| 步 | 过滤 | 默认 | 剩余 (实测) |
|---|---|---|---|
| 0 | 原始候选 | — | 5816 |
| 1 | `exclude-st` | 开 | 5619 |
| 2 | `exclude-pe` | **40** (原 80) | 3659 |
| 3 | `with-quality basic` (营收yoy>5 + 净利yoy>5) + `min-roe 5` + `min-gross-margin 20` | basic/5/20 | 311 |
| 4 | `max-net-debt-ratio` 净负债/市值 | **0.5** | 299 |
| 5 | V12 严判 | lookback=5 | **5 命中** (9s) |

**flag 清单** (`tools/batch/wyckoff_a2m_scan.py`):
- `--exclude-pe` 默认 **40** (原 80)
- `--min-growth` 默认 None → 用档位门槛; basic 档门槛 **5** (原 0, 试过 10 太严)
- `--min-roe` 默认 **5.0** (0=不限)
- `--min-gross-margin` 默认 **20.0** (0=不限)
- `--max-net-debt-ratio` 默认 **0.5** (0=不限)
- 回退旧默认: `--exclude-pe 80 --min-growth 0 --min-roe 0 --min-gross-margin 0 --max-net-debt-ratio 0`

**阈值依据 (全市场分布, 2026-10-28 实测):**
- PE: 中位 38.3 / P25 20.7 / P75 85.4 / P90 189
- ROE: 中位 **2.29** / P25 0.06 / P75 4.89 / P90 8.22
- 毛利率: 中位 24.28 / P25 14.95 / P75 36.96 / P90 54.02
- 净负债/市值: 中位 -0.00 / P75 0.14 / P90 0.48

**门槛调参轨迹 (重要):**
- basic 档 yoy 0 → 10 → **5**: 0 会放进低基数假增长 (创维数字 yoy +202% 但 ROE 2.5%);
  10 又误伤基蛋生物 (营收 yoy 8.3% 但净利 yoy +69.4% / 毛利 69.1% / PE 10.5 — 差 1.7pct 就被砍);
  折中到 5 后基蛋生物 + 军信股份都回来
- 最终 5 只: 300832 新产业 / 301101 明月镜片 / 603387 基蛋生物 / 688410 山外山 / 301109 军信股份
  (全部 PE 15-38 / ROE 5.3-9.6% / 毛利 55-81% / 杠杆<0.2)

**⚠️ 顺带修的语义 bug:** `financials.grossprofit_margin` 是**毛利率水平值 (绝对 %)**, 不是毛利率同比增速.
旧代码把 quality 档位门槛 (0/10/20) 直接传给它 → "strong=10" 实际是 "毛利率 > 10%" 而非旧文档写的 "毛利率增速 > 10%".
financials 表**没有**毛利率 yoy 字段. 现已在 `_filter_by_quality` docstring 澄清, `gm_min` 与 `gm_abs_min` 都作用在同一列取更严者.

**⚠️ argparse help 里的 `%` 必须转义成 `%%`**: 否则 `python -m tools.batch.wyckoff_a2m_scan --help` 直接崩
(ValueError: unsupported format character ')' (0x29) at index 219). 我踩过一次 — help 文本里写 "绝对%)" 就炸.

**apply when:** 任何调 `/t-wyckoff-a2m` 过滤条件时; 分析脚本 `tools/batch/analyze_a2m_filters.py` 可跑全市场漏斗模拟 (不跑 A→M 判定).

### 详报里"当前买卖点"只能从 buy_sell_points 取, 因子历史表的买卖点列是"当天新出现" (2026-11-02 踩坑)
Type: rule
> **摘要:** 因子历史表的买卖点列是"当天新出现", 不是"当前持有"

**铁律:** 汇总"当前有效买卖点"用 `result.raw['buy_sell_points'][tf]` (tf = daily/weekly),
经 `tools/render/report_renderer.py::_bsp_str()` 转字符串。**不要**从因子历史表的「买卖点」列取。

**两个坑:**
1. 因子历史表的「买卖点」列 = **当天首次出现**的信号 (`new_bsp_daily` / `new_bsp_weekly` diff), 不是当前仍生效的。
   最新那行经常是 `—`, 因为信号是前几天出现的。
2. 报告里「🟢 退场信号检查」section 是**模板桩** —— 11 只票的值完全一样 (`60分_底背: ❌` / `vs_MA120: -31` / `板块_MA20_偏离: -22`),
   **不代表该股真实状态**, 别引用。
3. 「缠论详情 → 买卖点」行 (日线/周线各一份) 显示的是 `buy_sell_points` 的**当前**状态, 权威来源。

**实测 (11 只持仓, 20261102):** 有买卖点的只有 4 只 ——
海康威视 🟢1买@34.91(含MACD底背) / 我武生物 🟢1买@21.54(含双中枢) / 东方电缆 🟢1买@37.90 / 基蛋生物 🔴1卖@8.47。
其余 7 只当前**无 123 买卖点**(只有 DIF走平 / 山川 / MACD底背 这类连续状态信号)。

**apply when:** 任何"这只票现在什么信号"的汇总。参照之前把"历史某日的 1卖/1买 快照"当成当前状态写进汇总, 结论完全错。

### A→M 判定必须用生产函数跑, 不能信历史 md 里的 A2M_STATUS 标记 (2026-11-02)
Type: rule
> **摘要:** 不能信历史 md 里的 A2M_STATUS 标记 —— 那是上次扫描时打的

**权威来源:** `tools.batch.wyckoff_a2m_scan._process_one(code, df)` — 0 网络, 走生产算法。

**踩坑:** `docs/wyckoff-a2m/analyze-*.md` 里的 `<!-- A2M_STATUS hit/stale -->` 标记是**上一次扫描时**打的,
不是最新状态。11-02 重跑 `wyckoff_a2m_scan --no-full-report --write-md` 后, 军信股份 301109 从 `hit` 翻成 `stale`
(M 占比 12.5% >= 10%), 安井食品 603345 从 `hit` 翻成 `stale`。

**判定门槛 (V13 严判, `_process_one`):**
1. 最后 1 根 K 线 stage == 'Markup' (必须)
2. 最近 40 根里 Markup 占比 < 10% (允许 ≤ 3 根) —— 排除"之前 M → 转 A → 再转 M"假反弹
3. 单日涨跌幅 > 20% 跳过 (除权/复牌)

**被拒时怎么拿到原因:** `_process_one` 返 `None` 时要自己复算 stages + m_ratio 才能知道是卡在 2 还是 3。
实测 11 只持仓的 M 占比: 永兴 30.0% / 美亚 27.5% / 海康 20.0%(且 stage=Accumulation) / 明月 17.5% /
青岛 15.0% / 军信 12.5% / 东方电缆 12.5%(stage=Accumulation) / 基蛋 10.0%(stage=?)。

**apply when:** 任何"A→M 命中了哪些票"的结论。跑完扫描必须重新读 md 里的标记, 或直接调 `_process_one`。

### ETF A→M: 去重必须用收益率相关性, 纯 benchmark 字符串匹配会漏 (2026-11-05)
Type: rule
> **摘要:** 元数据 (benchmark/名称) 不可信 —— 用**价格序列相关性**判同指数, 阈值要有实测断层依据

**背景:** `/t-etf-wyckoff-a2m` 扫全市场 ETF, 同一跟踪指数有几十个代码 (中证A500 40 个 / 沪深300 30 个),
全列出来等于把同一个机会报 N 遍。

**踩坑 1 — benchmark 写法不统一, 字符串匹配抓不全:**
源数据里同一个指数有多种写法:
- 512690 鹏华中证酒ETF → `中证酒指数收益率×100%`
- 160632 鹏华中证酒指数(LOF) → `中证酒指数收益率×95%+商业银行活期存款利率(税后)×5%`
- 161725 招商中证白酒指数-A → `中证白酒指数收益率×95%+金融机构人民币活期存款基准利率(税后)×5%`

**三只是同一个指数** (实测 60 日日收益率相关度 0.970~0.982), 但字符串完全不同 → 纯 benchmark 分组会漏合并。
→ 归一化要**拆出复合基准里权重最大的指数成分**, 且**再加一层相关性判定**。

**踩坑 2 — 相关度阈值怎么定 (别拍脑袋):**
11 只命中全 55 对实测: ≥0.95 命中 4 对 (全是真重复), 第 5 名掉到 0.885 (中证主要消费 ~ 中证酒, 不同指数),
第 12 名之后最高只有 0.67 → **0.95 分离干净**, 有明确断层可以站住。

**最终方案 (两级并查集):**
1. 归一化 benchmark 相同 → 合并
2. 60 日收益率相关性 ≥ 0.95 → 合并 (抓写法不一致)
3. 每组留**流动性最好**的当代表 (最近 20 日日均成交额最大, 同额比 K 线根数)
4. 被合并的代码**已有详报改标 `merged`**, 别留着"命中"标记误导

**apply when:** 任何"同一标的多个代码"的去重场景。benchmark / 标的名称这类**元数据不可信**,
**用价格序列相关性做最终判据** —— 元数据只是候选, 相关性才是事实。

### ETF 详报: report 的 sector 参数要显式传 (2026-11-05)
Type: fact
> **摘要:** `render_report` 的 sector 默认恒 "—", ETF 要显式传跟踪指数

`render_report(data, sector="—")` 的 sector 默认恒为 "—" —— **全仓库没人传过**, 所以股票版详报的「板块」行一直是 "—"。
ETF 没有"行业"概念, 但有**跟踪指数**, 拿它当板块最有信息量。
→ `t_analyze_one.process_one()` 加了可选 `sector` 参数 (默认 None = 保持 "—", 存量报告零影响),
ETF 扫描器传跟踪指数进去。

**连带修的:** `DataStore.get_ctx()` 之前对 ETF 返回空 name → ETF 详报标题是 `# 512800 — | 2026-11-04`。
加了 ETF 兜底 (name 取 etf_basic, industry 取 benchmark), 标题变成 `# 512800 华宝中证银行ETF | 2026-11-04`。

**apply when:** 报告里任何"从 ctx 拿的字段"为空时, 先查 DataStore 有没有对应的兜底源, 而不是改渲染层。

### watchlist note 决策铁律 (2026-09-24 固化)
Type: rule
> **摘要:** 持仓/加仓/清仓结论的写法铁律, 见对应章节

**铁律:** watchlist 里**不维护主观 note 字段**, 决策只用**当下的 K 线 + 业绩 + 资金面**, 不引用历史备忘.

**为什么:**
- note 是历史包袱 (上次复盘时点 + 当时的判断), 跟当下市场状态可能完全脱节
- 用户提问"买还是卖"时, LLM 翻 note 会制造**重复 + 矛盾** (note 说减仓, 但当下信号可能反转)
- 真实决策需要的是**实时数据 + 纪律位**, 不是考古

**操作:**
- watchlist.json 只保留 code/name/sector/list_type/tech_params 五种字段
- 不主动往 watchlist 加 notes 字段
- 引用 watchlist 时只看硬数据, 不看主观判断

**已发生:** 2026-09-24 清掉 mavis-quant-agent/config/watchlist.json 里 226 条 notes.

apply when: 任何引用 watchlist / 分析持仓股的场景

### ETF 候选池: 删手工白名单后要收敛到「数据源 + fund_type 过滤」 (2026-11-06)
Type: rule
> **摘要:** 手工维护的名单必然过期; 候选池口径要有单一真源, 两个扫描器不能各写各的

**背景:** `config/etf_whitelist.json` 是手工维护的 18 只 ETF 名单 (光伏/芯片/半导体/军工…),
两个 ETF 扫描器口径还不一致:
- `etf_wyckoff_a2m_scan.py` 自己定义 `EXCLUDE_FUND_TYPES` + 自己过滤
- `etf_macd_scan.py` 读那个白名单

> 2026-11-06: `etf_macd_scan.py` 整族下线, 本条现在只剩 `etf_wyckoff_a2m_scan.py` 一个消费者,
> 但"候选池口径要有单一真源"这条铁律不变 —— 以后再加 ETF 扫描器, 从 `etf_universe.py` 取。

**铁律:** ETF 扫描器的候选池 = **DataStore.get_etf_basic() 全表 + fund_type 过滤**, 口径写在
`tools/batch/etf_universe.py` 一个地方, 所有扫描器从那儿取。

**为什么:**
- 手工名单会过期, 且**没人记得更新** —— 白名单里的票跟实际想看的完全脱节
- 两个扫描器都自称"扫全市场 ETF", 口径却对不上号 → 命中数没法横向比较
- ETF 唯一有效的过滤就是 **fund_type** (没有 PE/ROE/毛利率/退市标记):
  排除 `债券型 / 货币型 / REITs / 其他`, 实测 2252 → 1992 只

**删白名单的连带动作 (漏一个就静默跳过):**
1. `safe_fs.PROTECTED_PATHS` 里的保护项要先摘掉, 否则 `safe_delete` 直接拒绝
2. 扫描器默认值要换成数据源, 不能留"读不到就跳过"的口径 (否则扫描**静默**变成 0 只)
3. `sync.py` docstring 里描述白名单的那行也要改 (SKILL.md/代码/文档三处一致)

**apply when:** 任何"扫全市场"型扫描器。候选池一律从 DataStore 取, 不维护手工名单。

### 全市场扫描器: 两段式 (批量预筛 + 只对命中深扫) (2026-11-06)
Type: rule
> **摘要:** 别对 2000 只逐只跑完整分析 —— 先用批量指标预筛, 只对命中跑深扫

**实测 (ETF MACD 扫描, 1992 只候选; 该扫描器 2026-11-06 已下线, 数据留作基线):**

| 做法 | 耗时 |
|---|---|
| 逐只跑完整分析 (AnalysisEngine.analyze_history + 因子历史) | **~16 分钟** |
| 段 1 批量预筛 (1 次 SQL 读 K 线 0.8s + `compute_indicators` 0.0008s/只) | 1.5s |
| 段 2 只对命中跑深扫 (命中 200 → 同指数合并后 72 只, 4 worker) | ~7s |
| **全流程** | **~10s** |

**要点:**
- 预筛指标要用**跟正式分析同一个函数** (`compute_indicators`), 别自己写 EMA/RSI (口径会漂)
- 预筛的 K 线窗口要跟深扫**完全一致** (`lookback` 一样的根数), 否则指标值对不上
- 深扫只对命中跑 —— 命中集通常只占候选池 5~10%

**apply when:** 任何候选池 > 500 的扫描器。先量一下"逐只深扫"的耗时, 超过 2 分钟就该拆两段。

### ETF 扫描: 命中后必须按跟踪指数去重 (2026-11-06)
Type: rule
> **摘要:** 同一指数的 ETF 命中会重复 4~6 只, 不去重出的一堆 md 是同一份技术面

**实测:** 200 只命中 → 同指数合并后 **72 个不同标的** (34 组重复, 合并掉 128 只)。
典型: 中证银行 15 只、中证红利低波 6 只、800 自由现金流 6 只、中药 6 只、主要消费 5 只。

> 2026-11-06: ETF MACD 扫描器已下线; 这条规则现在由 `etf_wyckoff_a2m_scan.py` 落地执行。

**铁律:** 复用 A→M 那套两级并查集口径 (`tools/batch/etf_universe.group_by_tracked_index`):
① 归一化 benchmark 相同 ② 60 日收益率相关性 ≥ 0.95; 每组留流动性最好的代表。

**为什么:** 不去重的话, 报告目录里 33 份 md 有 15 份是同一个银行指数, 汇总表也全是重复行 ——
用户看到的"命中数"是假的 (200 ≠ 200 个机会)。

**apply when:** 任何"同一标的多个代码"的 ETF 场景。去重口径只有一处, 别在第二个扫描器里重写一遍。

### 口径一致性验证: 预筛值必须跟深扫值逐只比对 (2026-11-06)
Type: rule
> **摘要:** 改动扫描器口径后, 拿预筛结果跟正式分析结果逐只 diff, 不能只看"跑通了"

**做法:** 预筛算出的 `rsi6 / macd_dif / macd_dea / 信号分类` 跟深扫最后一根因子历史行
的值逐只比对 (容差: RSI 0.05 / DIF 0.002)。

**本次实测:** 72 只代表**零漂移** (信号分类全一致, 指标全在容差内); 唯一差异是 4 只次新 ETF
历史不足 200 根 (117/37/134/139 行) —— 属正常, 不是口径问题。

**顺带固化:** 因子历史回看根数走真源 `factor_history_lookback(code)` (ETF 200 / 股票 120),
别在扫描器里再写死一个数。

**apply when:** 任何"两段式扫描"或"预筛 + 深算"的改动。上线前跑一次逐只 diff。

### 扫描器取数: 别用「全局最新那一天」, 要用「每股最新」 (2026-11-05)
Type: rule
> **摘要:** Tushare 的估值数据当天陆续发布 —— 全局 MAX(trade_date) 在半拉日会漏检一大半, 静默出错

**背景:** 2026-11-05 收盘后 sync, `stk_factor` 当天只发布 **2387 / 5572 只**
(sync 自己识别成"半拉, 不标 done, 下次补" —— 这一层是对的)。
问题出在**下游扫描器**: `wyckoff_a2m_scan.py` 的杠杆闸 SQL 写的是
`WHERE trade_date = (SELECT MAX(trade_date) FROM ...)` → 只查那 2387 只 →
另外 3185 只 JOIN 不上 → **杠杆闸根本没检查它们**。

**实测后果:**
- 全市场高杠杆票 (净负债/市值 > 0.5): 旧写法判出 **219** 只, 应判 **525** 只 (**漏 306, 零反向**)
- 抽查漏掉的: 600340 杠杆 **30.4 倍** (净负债 1372 亿 / 市值 45 亿)、600325 19.9 倍、600376 13.1 倍
- 命中集从 452 → 548 票 (池子变大), A→M 命中 12 → 19 只

**铁律:** 要"每只票的最新估值" → `ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) = 1`,
**不要** `WHERE trade_date = (SELECT MAX(trade_date) ...)`。
半拉日下两者差 3191 只; 数据完整时只差 6 只 (当天停牌但有旧值的票, 方向是"更多票被正确检查")。

**同文件已有先例**: `_filter_by_pe` 早在 2026-11-03 就因为同一个 bug 改成了 per-code latest
(见该函数 docstring)。**修一处要 grep 全部同类写法** —— 这次又剩 2 处没跟上。

**配套: 财务季也别硬编码.** `quarter_utils.py` 的铁律写死"业务运行时永远不要硬编码 2026Q2"。
本文件原来把 financials 写死 2026Q2+2025Q4 → 等 2026Q3 落盘后过滤链落后一季。
改成动态取 (最新季 + 上一季兜底) 后:
- 8 只票恢复进池 (健帆生物/西藏药业/威高血净/国茂股份/翱捷科技/天德钰/西高院/联赢激光)
  —— 累计口径下 Q3 比 H1 宽 + ROE 累计上升
- **605338 巴比食品被剔出** (前三季营收 -0.44%, H1 还是 +2.03%) —— 跟"营收已转负"的判断一致

**apply when:** 任何读 stk_factor / daily_basic / financials 的扫描器。
先问: **我取的是"全局最新日"还是"每股最新"?** 前者在上游当天陆续发布时必然漏检。

**同场修掉的 flag 兑现 bug: `--no-full-report` 完全失效.**
`wyckoff_a2m_scan.py` 打印的「本次运行生效条件」写着"详报 ❌ 不跑 (--no-full-report)",
但 step 2 的 refresh 分支只判 `if args.refresh:` , **不看 full_report**
→ 默认 (--refresh) 模式下这个 flag 一点用没有, 照跑 A/B/C 三类详报 (20 只 × ~2s)。
修法: `if args.refresh and args.full_report:`。
**apply when:** 打印的"生效条件"跟实际行为对不上 → 优先怀疑 flag 分支没判那个参数
(本项目铁律: SKILL.md 写的每个 flag 都必须在代码里兑现)。
