# 财务 / 估值口径

> 本文件是 `handbook/project-lessons.md` 的主题分册 (03-finance.md)。
> 索引 (全 47 条一句话摘要) 见 [`handbook/lessons/README.md`](README.md)。
> 用法: **grep 查**, 不用通读 —— 提到具体话题先在这里搜关键词。

---


### FinanceStrategy.quarterly 是降序, [0] 才是最新季 (2026-10-28 固化)
Type: rule
> **摘要:** `quarterly` 是降序, **[0] 才是最新季** (不是 [-1])

**铁律:** `AnalysisEngine(strategies=[FinanceStrategy])` 返回的 `result.raw['finance']['quarterly']` 是 **降序** (最新季在 index 0), 因为 `analysis_engine.py:999` 写的是 `for r in reversed(financials[-4:])`.

取最新季必须用 `quarterly[0]`, **不是 `[-1]`**.

**反例 (今天导出 22 只 A→M 财务):**
- 汇总表用 `d["quarterly"][-1]` → 取到 20250630 (最旧季) 而不是 20260630
- 后果: 22 只汇总表的 营收yoy / 净利yoy / 毛利率 / ROE / PE / 市值 **整列错位**
- 表现为"PE 列全变" (300519 74.2 → 47.4, 600323 20.5 → 18.6), 但 4 季明细表是对的 (逐行直接取 quarterly 全表)
- 用户问"他们的 PE 呢" 才暴露出来 —— 汇总表和明细表对不上

**排查手法:** 打印 `[r['quarter'] for r in q]` 确认顺序, 不要靠猜。

**apply when:** 任何消费 `finance.quarterly` 的代码 (汇总表 / 排序 / 取最新季)。已加 assert 守护:
```python
qs = [r.get("quarter","") for r in quarterly]
assert qs == sorted(qs, reverse=True), f"{code} quarterly 非降序: {qs}"
```

### 批量导出财务数据必须用生产链路 + 逐行 diff 校验 (2026-10-28 固化)
Type: rule
> **摘要:** 走 AnalysisEngine + 逐行 diff 校验, 别自己重算 ROC/EY/PE

**铁律:** 导出多只票的财务/指标数据时, **不要自己重算** ROC/EY/PE/Magic, 走 `AnalysisEngine(strategies=[FinanceStrategy])` → `result.raw['finance']['quarterly']`, 再用 `report_renderer._section_quarterly_table` 同款 formatter 渲染.

**格式化必须逐个对齐 renderer** (report_renderer.py:84-113):
| 字段 | renderer 写法 | 说明 |
|---|---|---|
| or_yoy / np_yoy | `:+.1f` | 带 +/- 号, **不加千分位** (加了就跟详报不一致) |
| gm / roe | `:.1f` | |
| revenue_yi | `:.1f` | **不加千分位** (1316.8 不是 1,316.8) |
| netprofit_yi / ebit_yi | `:.2f` | |
| mc_yi / netdebt_yi / ev_yi / capital_yi | `_fmt` → `:,.0f` | **千分位 + 整数**, 0/None → `—` |
| roc | `:.1f` | None → `—` |
| ey | `:.2f` | None → `—` |
| static_pe / fwd_pe | `:.1f` | None → `—` |

**校验脚本已落地:** `tools/batch/verify_a2m_financials.py`
- 真源 = `docs/wyckoff-a2m/analyze-*.md` (/t-analyze 详报)
- 逐行 diff 4 季财务表的 88 行 × 16 列
- **踩坑**: 详报里还有 20 列的因子历史表 (行首也是 8 位日期), 必须靠表头 `ROC 资本回报率` 定位窗口, 否则会误抓 100+ 行

**apply when:** 任何"导出 N 只票的完整数据"的脚本。第一版自己重算 ROC 导致 603387 显示 15.7% vs 生产链路 16.4% (口径搞错: 4 季大表是**每季单季快照**语义 `compute_magic_one_day([r], ...)`, 不是最新季 TTM)。改成走生产链路后 diff = 0。

### 静态PE 年化 bug: q_str[5:10] 永远取不到 "06-30" (2026-10-28 修)
Type: bug
> **摘要:** end_date 是 YYYYMMDD, `q_str[5:10]` **永远取不到 "06-30"** → PE 系统性虚高一倍

**症状:** `/t-analyze` 详报「财务数据」section 的 PE 列**系统性偏高 1 倍** (半年报 / 一季报行)。

**根因:** `tools/analysis/analysis_engine.py` 里 `end_date` 是 `YYYYMMDD` (无分隔符, 如 "20260630"), 但 `ytd_factor` 查表用的 key 是 `"06-30"`:
```python
ytd_factor = {"03-31":4.0, "06-30":2.0, "09-30":4.0/3.0, "12-31":1.0}.get(q_str[5:10], 1.0)
#                                                          ^^^^^^^ = "630" 不是 "06-30"
```
→ 永远 miss → 恒等于 1.0 → 半年报净利少乘 2 → **PE 虚高一倍**。

**实测影响 (22 只 A→M 命中票, 21 只有效 PE 全部虚高):**
| 代码 | 旧(错) | 新(对) | 代码 | 旧(错) | 新(对) |
|---|---|---|---|---|---|
| 600323 瀚蓝环境 | 18.6 | **9.3** | 300519 新光药业 | 47.4 | **23.7** |
| 601766 中国中车 | 18.6 | **9.3** | 002380 科远智慧 | 50.6 | **25.3** |
| 603387 基蛋生物 | 21.0 | **10.5** | 000810 创维数字 | 93.3 | **46.7** |
| 000538 云南白药 | 22.9 | **11.4** | 603683 晶华新材 | 183.4 | **91.7** |
| 688469 芯联集成 | 353.7 | **176.8** | | | |

**修法 (已落地):**
```python
_md = q_str[4:8]  # YYYYMMDD → MM-DD
_mmdd = f"{_md[:2]}-{_md[2:]}" if (len(_md)==4 and _md.isdigit()) else q_str[5:10]
ytd_factor = {"03-31":4.0,"06-30":2.0,"09-30":4.0/3.0,"12-31":1.0}.get(_mmdd, 1.0)
```
12-31 行 ytd_factor=1.0 本来就对, 所以只有 03-31 / 06-30 / 09-30 行受影响。

**排查手法:** 手算 PE = 季末市值 / (净利亿 × ytd_factor), 跟代码输出对不上就是这里。

**apply when:** 任何用 `end_date` 切片取 "MM-DD" 的代码。已 grep 全仓, 只有这一处踩坑 (`_quarter_of` 那些用 `date[4:6]` 取月份是对的)。

### OBV / fflow / PEG / DCF 四个维度已彻底删除 (2026-11-02 固化)
Type: fact
> **摘要:** 已彻底删除禁止复活; 估值只看 PE(静态) + ROC/EY/毛利率/杠杆

**铁律:** 这四个维度**已经从代码 + 文档 + memory 全部清除**, 不要复活, 不要"顺手加回来"。

| 维度 | 删除时间 | 原因 |
|---|---|---|
| **OBV** (量能) | 2026-10-27 | 实测负超额 (多头排列 -2.38%, 底背离 -1.60%), 剥掉"离 MA120 距离"成分后胜率 48.5% < 基准 50.1% |
| **fflow** (主力资金流) | 2026-10-28 | parquet 落后 K 线 29 天时, 38 只核心票里 14 只判"出货", 其中 13 只被同时点的量价指标判"偏进货" — 方向完全相反, 是陈旧数据造成的系统性看空偏差 |
| **PEG** | 2026-11-02 | 假指标 — 依赖 EPS 预测, 而 EPS 预测本身不准 (机构覆盖偏差 + 财报季前后剧烈变动) |
| **DCF L / L_E3** | 2026-11-02 | 用的是同一套 EPS 预测, 跟 PEG 同源, 一起删 |

**删除后的估值口径:** 只看 **PE (静态) + 盈利质量 (ROC / EY / 毛利率 / 杠杆)**, 全部用已审计 TTM 财报算, 不依赖任何预测值。
报告 20 section → 18 section (§5️⃣ PEG/DCF L 整档删除)。

**2026-11-02 补充清理 (用户指出"还在提这些删除的逻辑"):**
- `tools/btf/`: 删 `PEG15Strategy` (4 策略 → 3 + 基准), SQL 查 `peg`/`dcf_l` 两列删掉
- `tools/storage/caches/analysis.py`: `analysis_cache` 的 `peg`/`dcf_l` 列从自动迁移列表删除, `get_valuation_coverage()` 只返 roc/ey
- `tools/batch/backfill_roc_ey_cache.py`: 4 元组 → 2 元组 (roc/ey), 删 EPS 预读 (只喂 PEG/DCF), `_calc_4cols_for_code` → `_calc_2cols_for_code`
- `tools/analysis/signal_tracker.py`: 删 `MODEL_INFERENCE` 里的 'PEG' 模型推断条目
- `tools/research/lgb_mine.py`: 删 PEG 特征 + 修 3 个 bug (`ctx.valuation_data` 不存在 → 改 `ctx.eps_table`/`ctx.market_cap_yi`; `raw` 未定义 → 改 `ctx.name`/K 线自算; config 路径少一层 `.parent`)
- `handbook/analysis-framework.md`: 删 §2.5 PEG 整档 (53 行), §3 决策矩阵双轴改 PE+ROCEY, §11 三个排序表 PEG 列 → PE 列, §14.1 公式表补 ROC/EY, §14.5 术语表 PEG → ROC/EY, §15 五关改 PE+ROCEY+L
- `handbook/finance-glossary.md`: 删 §8 PEG 详解 (83 行) + 12 处引用改写
- `handbook/backtest-roc-ey-formula.md`: 用当前 4 策略代码重跑重建 (原 PEG +147% 结论作废)
  → 2026-11-06 删除: 内容恒 0 (roc/ey 两列从未回填), 跑 `tools/btf/run_1year.py` 可重新生成
- `handbook/finance-glossary.md`: 术语表 / 估值口径速查

**⚠️ 已知遗留 (跟 PEG/OBV 无关, 但会让估值链路失效):**
`analysis_cache` 的 `roc` / `ey` 两列**全表 0 行非空** — `backfill_roc_ey_cache.py` 从没成功跑过。
后果: `tools/btf/run_1year.py` 的 EY8 / ROC25 / ROC25+EY8 三个策略**选不出任何票** (收益恒 0),
`/t-finance-roc-ey` 的 ROC/EY 列也拿不到历史值。
修法: `python -m tools.batch.backfill_roc_ey_cache` 回填后重跑。

**apply when:** 任何"加个量能指标"/"加个估值指标"/"把 PEG 拿回来"的提议 — 先看这条。
新增量能/估值维度前, 必须先跑回测证明超额 > 0, 否则不进决策链 (4 类等权投票)。

### stock_basic 股本兜底读已废弃的 daily_basic → 详报股本恒 0.00亿股 (2026-11-03 修, 32d7d2b)
Type: bug
> **摘要:** 兜底不能读已废弃的 daily_basic, 否则详报股本恒 0.00 亿股 (已修 32d7d2b)

**症状:** 所有详报「📊 基础信息」section 总股本/流通股本都显示 `0.00亿股`。
美的应该 76.30亿股、海康 91.65亿股 —— 全是 0。

**根因:** `tools/storage/store.py::sync_stock_basic()` 的股本兜底读
`data/history/daily_basic/*.parquet`, 但 **v6.2.4 已用 `stk_factor_pro` 取代
daily_basic, 该目录根本不存在** → `except Exception` 静默吞掉 →
stock_basic parquet 落盘时丢了 `total_share`/`float_share` 两列 (schema 里压根没这两列)。

渲染器 `tools/render/report_renderer.py::_section_ts_basic` 读
`sb.get("total_share")` → 拿不到 → 显示 0.00。

**修法:** 兜底改读 `data/history/stk_factor/*.parquet`。
该源本来就有 `total_share` + `float_share` 两列 (单位 **万股**), 
**不需要再靠 `circ_mv / close` 反算** —— 旧代码那个反算逻辑也一并删了。

**重建方法:** stock_basic 有 30 天 mtime 缓存, 改了代码不会自动重建:
```bash
cp data/history/stock_basic/stock_basic.parquet ~/.Trash/stock_basic-backup-$(date +%s).parquet
touch -d "2026-09-01 00:00:00" data/history/stock_basic/stock_basic.parquet
bash tools/with_venv.sh python -m tools.storage.sync --stock-basic
```

**⚠️ 通用教训 (跟 financials stale 检测同一个病根):**
`except Exception as e: print(...)` 把异常降级成一行 warning 就继续走 ——
**数据源路径写错时, 下游 schema 会静默缺列, 半年都没人发现**。
凡是"从本地 parquet 补字段"的兜底逻辑, 必须 (a) 断言补上的行数 > 0,
(b) 补完校验 parquet 的列名真的存在。**代码里读 parquet 路径时, 必须先确认该目录存在。**

**验证:** 重建后补 5571/5577 只 (缺的 6 只 = 新股)。
活跃目录 (portfolio 24 / watchlist 28 / wyckoff-a2m 24) 全部刷新, 0.00 归零。
`docs/runA` / `docs/runB` 是 10-30 的一次性归档, 没有任何脚本会再写, 保留原样。
`docs/blowout` 2026-11-06 起也不再生成 (blowout 段只进汇总表, 不落 md), 目录已清空。

**apply when:** 任何"详报里某个字段恒为 0 / 恒为空"的排查。
先查对应 parquet 的 **schema 列名**, 再查渲染器读的是不是同一个 key。
