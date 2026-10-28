---
name: t-wyckoff-a2m
description: 全市场扫描"最近 N 根 K 线内威科夫阶段从 Accumulation 转到 Markup"的股票. 0 网络, 走 duckdb daily parquet. 触发词: "A转M"、"威科夫拐点"、"近期突破".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 **命名规范: [`handbook/naming-convention.md`](../../handbook/naming-convention.md)** (7 条硬规则, 5 项 grep 验证)

## 啥时候用

- 全市场找"刚开始走主升浪"的票(威科夫 A→M 转变)
- K 线每日变化 → **不天天跑**,每周 / 每次大跌后跑一次
- 命中后用 `/t-analyze <code>` 看单只 22 section 详报

## 跑

```bash
# 全市场 + 默认过滤 + 默认详报 (最小例子)
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan

# 所有 flag + 默认值
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan --help

# 只看汇总, 不跑详报
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan --no-full-report

# 只扫 watchlist
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan --watchlist-only
```

> 默认过滤链(全开):`exclude-st` → `exclude-pe 60` → `with-quality basic` (营收/净利 yoy>0) + `ROE>5%` + `毛利率>20%` → `净负债/市值<0.5`.
> 任何 flag 关/换门槛都用 `--help` 看当前默认 + 含义,不要凭记忆。

## 过滤链 (2026-10-28 收紧后再放宽)

5 步前置过滤, 全部 1 次 SQL/步, 0 网络:

| 步 | 过滤 | 默认 | 剩余 (实测) |
|---|---|---|---|
| 0 | 原始候选 (全市场) | — | 5816 |
| 1 | `exclude-st` ST/*ST | 开 | 5619 |
| 2 | `exclude-pe` PE 上限 | **60** | 4213 |
| 3 | `with-quality basic` (营收yoy>0 + 净利yoy>0) + `min-roe 5` + `min-gross-margin 20` | basic/5/20 | 458 |
| 4 | `max-net-debt-ratio` 净负债/市值 | **0.5** | 441 |
| 5 | V12 严判 A→M | lookback=5 | **10 命中** (11s) |

**为什么这样定** (2026-10-28 用全市场分布 + 22 只命中票反推, 逐档实测):

- `exclude-pe 80 → 40 → 60`: 全市场 PE 中位 38.3 / P75 85.4. 80 放进来的芯联集成(176.8)/晶华新材(91.7)/创维数字(46.7) 全是"贵 + ROE<3% + 毛利<20%"的差票; 40 又误伤 PE 46.7 的科远智慧等 → 最终 60
- `min-roe 5`: 全市场 ROE 中位仅 **2.29%** / P75 4.89%, 所以 5% 已经砍掉一半. 专治"低基数假增长" (创维数字 yoy +202% 但 ROE 2.5%)
- `min-gross-margin 20`: 全市场毛利率中位 24.28%. 专治薄利行业 (中创物流 6.7% / 立中集团 10.5%)
- `max-net-debt-ratio 0.5`: 专治重债 (瀚蓝环境净负债 320 亿 > 市值 224 亿). PE 低但重债不算便宜
- 营收/净利 yoy 门槛 **0**: 调参轨迹 0 → 10 → 5 → 0. 10 误伤基蛋生物 (营收 yoy 8.3% 但净利 +69.4%); 5 砍掉零增长但 PE 便宜的稳健票 (云南白药 PE 18.5 / 净利 yoy 1.9%); 回 0 后新增 5 只全是 PE 便宜、无差票

> ⚠️ **语义澄清**: `with-quality` 档位门槛作用在 `grossprofit_margin` 上, 那是**毛利率水平值 (绝对 %)**,
> 不是毛利率同比增速 — financials 表没有毛利率 yoy 字段. yoy 维度只由 `or_yoy` / `netprofit_yoy` 承担.
> 所以 `strong=10` 的真实含义是 "毛利率 > 10%", 不是文档旧版写的 "毛利率增速 > 10%".
> 营收/净利 yoy 门槛可用 `--min-growth` 单独覆盖 (0=不限).

**效果**: 22 只 → 10 只, 10 只**全部**是"便宜 + 干净"那批 (PE 15-51 / ROE 5.3-9.6% / 毛利 30-81% / 杠杆<0.2),
被砍的全是贵 / 重债 / 亏损. 扫描耗时 44s → 11s.

**放宽**: `--exclude-pe 0 --min-growth 0 --min-roe 0 --min-gross-margin 0 --max-net-debt-ratio 0` 回旧默认.

## 跑流程

1. **step 1 — 扫描**: 全市场过 `_filter_by_st` → `_filter_by_pe` → `_filter_by_quality` (+ROE/毛利率) → `_filter_by_leverage` → V12 严判 (`tools/batch/wyckoff_a2m_scan.py::wyckoff_judge_v12`),复用 AnalysisEngine + compute_factor_history,跟 `/t-analyze` 100% 一致
2. **step 2 — 详报**: 命中后逐只跑 `/t-analyze <code>` (默认前 100, 0=全部),落到 `docs/wyckoff-a2m/`

## V12 严判 (代码 = 单一事实源, 见 wyckoff_a2m_scan.py L263-282)

- `当前 K 线 stage == Markup` (必须)
- 最近 30 根 K 线 `M 占比 < 20%` (排除"反复 M"假突破)
- 单日涨跌 > 20% 跳过 (除权 / 复牌)

## 数据特性 (这是必须知道的)

- **0 网络**, 走 duckdb daily parquet
- 全市场 ~11s (2026-10-28 收紧过滤后; 旧默认 ~44s), ~5816 → ST 排 → PE>40 排 → quality+ROE+毛利 排 → 杠杆排 → ~384 票
- 单只扫描瓶颈 = `scan_sub_events` 内部 9 detector Python loop 序列 (GIL 锁死, ThreadPool 0 加速)
- mp.spawn 详报 1.5-2.6x 加速(起步小批量 ≤8 收益最高, worker 太多被启动开销拖累)
- ⚠️ **不能用 mp.fork** (duckdb fork 后死锁)
- 默认清空 `docs/wyckoff-a2m/` 旧 md; `--no-clean` 跳过
- 详报目录跟 watchlist 完全隔离,不污染自选
- 数据日期 = parquet 最新 trade_date,不是 wall clock

## 命名沿革 (关键节点)

- **v8** (2026-10-16) — 删 `_judge_full` 慢路径 + 直调 `wyckoff_judge` + 全市场 1 次 SQL + 5.3 分钟单进程
- **v9/v10** (2026-10-16/17) — 判定从"任何当前 M 命中"演进到"T-1..T-5 全 A"
- **v11** (2026-10-17) — T-0=M + T-1..T-5 ∈ {A, ?};已弃用
- **v12 字面版** (2026-10-20) — 当前 M + 最近 30 根 M 占比 < 20%;**当前默认**

## 跑后验证

```bash
# 详报全列
ls docs/wyckoff-a2m/analyze-*.md | wc -l

# lint 单个详报 (数据完整性)
bash tools/with_venv.sh python -m tools.render.report_linter docs/wyckoff-a2m/analyze-605189-富春染织.md all
```

## 相关

- `/t-analyze` (单只深挖, 9 子事件 + stage_detail)
- `/t-rsi6-tech` (技术面超卖)
- `/t-near-low` (5y 回撤超跌)
- `/t-factor-5y` (5 年因子历史)
- `/t-sync-data --kline` (跑前必跑, K 线每日增量)
