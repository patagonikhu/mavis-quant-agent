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
- 命中后用 `/t-analyze <code>` 看单只 11 section 详报

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

# 默认就是刷新模式 (不清空目录, 刷新已有 + 补充新增 + 留档标注)
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan
```

> 默认过滤链(全开):`exclude-st` → `exclude-pe 60` → `with-quality basic` (营收/净利 yoy>0) + `ROE>5%` + `毛利率>20%` → `净负债/市值<0.5`.
> 任何 flag 关/换门槛都用 `--help` 看当前默认 + 含义,不要凭记忆。

> 📋 **每次跑开头会自动打印「本次运行生效条件」** (2026-10-29 加):
> scope / lookback / 5 步过滤链各自 ON-OFF + 实际门槛 / V13 严判 4 条 / 输出设置,
> 全部直接读 `args` 打印 —— 这是**唯一可靠的条件来源**, 因为下面的表格是手写的, 会漂移。
> 用户问"现在的条件是啥"→ 看这段打印, 不要背文档。
> 同样一份条件也会写进 `docs/wyckoff-a2m.md` 头部的「本次生效条件」表, 便于事后追溯。

## 过滤链 (2026-10-28 收紧后再放宽)

5 步前置过滤, 全部 1 次 SQL/步, 0 网络:

| 步 | 过滤 | 默认 | 剩余 (实测) |
|---|---|---|---|
| 0 | 原始候选 (全市场) | — | 5816 |
| 1 | `exclude-st` ST/*ST | 开 | 5619 |
| 2 | `exclude-pe` PE 上限 | **60** | 4213 |
| 3 | `with-quality basic` (营收yoy>0 + 净利yoy>0) + `min-roe 5` + `min-gross-margin 20` | basic/5/20 | 458 |
| 4 | `max-net-debt-ratio` 净负债/市值 | **0.5** | 441 |
| 5 | V13 严判 A→M | lookback=5 | **7 命中** (13s) |

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

1. **step 1 — 扫描**: 全市场过 `_filter_by_st` → `_filter_by_pe` → `_filter_by_quality` (+ROE/毛利率) → `_filter_by_leverage` → V13 严判 (`tools/batch/wyckoff_a2m_scan.py::_process_one`),复用 AnalysisEngine + compute_factor_history,跟 `/t-analyze` 100% 一致
2. **step 2 — 详报**: 命中后逐只跑 `/t-analyze <code>` (默认前 100, 0=全部),落到 `docs/wyckoff-a2m/`

### step 2 两种模式 (2026-10-29 加 `--refresh`, **2026-11-02 起 `--refresh` 是默认**)

| 模式 | 行为 | 用 flag |
|---|---|---|
| **刷新 (默认)** | **不清空**, 分 3 类处理 | (无, 默认开) |
| **清空** | 先清空 `docs/wyckoff-a2m/`,再生成命中的详报 | `--clean` |

> ⚠️ **2026-11-02 变更**: 清空从默认改成 opt-in (`--clean`)。
> 原因: 旧逻辑用 `p.unlink()` **直接删除**, 不进回收站, 而 `docs/` 被 `.gitignore` 排除 → **删了就没了, 恢复不了**。
> 历史命中票是复盘素材, 默认必须保留。**除非你明确要清空, 否则永远别加 `--clean`。**

`--refresh` 的 3 类处理 (默认行为):

| 类 | 定义 | 处理 |
|---|---|---|
| **A. 刷新** | 目录里已有 **且** 本次仍命中 | 重新生成 (数据更新到最新交易日) + 文件头标 🟢「本票为本次 A→M 扫描命中」 |
| **B. 补充** | 目录里没有 **且** 本次命中 | 新生成 + 同上标记 |
| **C. 留档** | 目录里已有 但本次**未命中** | 也刷新数据 + 文件头标 ⚠️「本票本次 A→M 扫描未命中 (曾命中过, 现留档)」 |

最终目录 = 旧文件 ∪ 本次命中, **不删任何历史票**。

配套 flag:
- `--keep-stale` — C 类不刷新数据, 只在文件头标注 (默认会刷新)
- `--stale-only` — 只跑 C 类, 不跑本次命中
- `--no-clean` — 完全不碰旧文件 (数据永远停在生成那天)
- `--clean` — ⚠️ 显式清空目录 (不可恢复, 慎用)

> ⚠️ **为什么不用 `--no-clean` 代替 `--refresh`**:
> `--no-clean` 是完全不碰旧文件, 那些票的行情数据会永远停在生成那天, 跟同目录其他文件不同步。
> `--refresh` 会把 C 类的数据也刷到最新, 只是标个状态区分。

> 标记块用 HTML 注释包裹 (`<!-- A2M_STATUS hit -->`), 幂等 —— 重复跑不会累积, 旧标记先删再插。

## V13 严判 (代码 = 单一事实源, 见 wyckoff_a2m_scan.py::_process_one)

- `当前 K 线 stage == Markup` (必须)
- 最近 **40** 根 K 线 `M 占比 < 10%` (允许 ≤ 3 根, 排除"反复 M"假突破)
- 单日涨跌 > 20% 跳过 (除权 / 复牌)
- K 线长度 ≥ 11 根

## 数据特性 (这是必须知道的)

- **0 网络**, 走 duckdb daily parquet
- 全市场 ~11s (2026-10-28 收紧过滤后; 旧默认 ~44s), ~5816 → ST 排 → PE>40 排 → quality+ROE+毛利 排 → 杠杆排 → ~384 票
- 单只扫描瓶颈 = `scan_sub_events` 内部 9 detector Python loop 序列 (GIL 锁死, ThreadPool 0 加速)
- mp.spawn 详报 1.5-2.6x 加速(起步小批量 ≤8 收益最高, worker 太多被启动开销拖累)
- ⚠️ **不能用 mp.fork** (duckdb fork 后死锁)
- 2026-11-02 起默认**不清空** `docs/wyckoff-a2m/` (--refresh 默认开); `--clean` 才清空 (不可恢复)
- 详报目录跟 watchlist 完全隔离,不污染自选
- 数据日期 = parquet 最新 trade_date,不是 wall clock

## 命名沿革 (关键节点)

- **v8** (2026-10-16) — 删 `_judge_full` 慢路径 + 直调 `wyckoff_judge` + 全市场 1 次 SQL + 5.3 分钟单进程
- **v9/v10** (2026-10-16/17) — 判定从"任何当前 M 命中"演进到"T-1..T-5 全 A"
- **v11** (2026-10-17) — T-0=M + T-1..T-5 ∈ {A, ?};已弃用
- **v12** (2026-10-20) — 当前 M + 最近 30 根 M 占比 < 20% (允许 ≤5 根)
- **v13** (2026-10-29) — 当前 M + 最近 **40** 根 M 占比 < **10%** (允许 ≤3 根);**当前默认**

**v12 → v13 变更影响** (2026-10-29 实测, 同一天同一份数据对照):

| | V12 (30根/20%) | V13 (40根/10%) |
|---|---|---|
| 命中数 | 13 | **7** |
| 保留 | — | 001219 / 002380 / 603337 / 300406 / 301109 / 002683 / 688097 |
| 被砍 | — | 300832 (M/40=35%) / 301101 (10.0%) / 603387 (10.0%) / 002690 (22.5%) / 002472 (12.5%) / 601101 (32.5%) |
| 新增 | — | 无 (单调收紧, 只砍不加) |

被砍的都符合预期: 300832 新产业 40 根里 14 根是 M (35%), 601101 昊华能源 13 根 (32.5%),
002690 美亚光电 9 根 (22.5%) —— 确实是"反复 M", 不是干净启动.
`301101 明月镜片` / `603387 基蛋生物` 40 根占比 = 10.0% 恰好卡在阈值 (判定 `>= 0.10` 排除), 属边界被砍.
`688097 博众精工` 阶段序列看着像反复 (A/A/M/M/A/M), 但 40 根里只 3 根 M (7.5%), 实际干净 → 保留.

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
- `/t-factor-5y` (5 年因子历史)
- `/t-sync-data --kline` (跑前必跑, K 线每日增量)
