---
name: t-wyckoff-a2m
description: 全市场扫描"最近 N 根 K 线内威科夫阶段从 Accumulation 转到 Markup"的股票. 0 网络, 走 duckdb daily parquet. 触发词: "A转M"、"威科夫拐点"、"近期突破".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 **命名规范: [`handbook/naming-convention.md`](../../handbook/naming-convention.md)** (7 条硬规则, 5 项 grep 验证)

## 🕐 跑节奏

- 日级别运行,K 线每日变化
- **不天天跑** — A→M 转变在市场平静期很少触发(全市场 baseline ~0.1%), 每周 / 每次大跌后跑一次
- 命中后用 `/t-analyze <code>` 看 22 section 详报

## 用法

```bash
# 最小例子 (全市场 + 默认 ST 排除 + 默认 basic quality + 详报前 10)
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan

# 自定义
... --watchlist-only                    # 只扫 watchlist (持仓+blowout+自选)
... --watchlist-types 持仓               # --watchlist-only 时限定类型
... --lookback 8                        # 看最近 8 根 K 线 (实际扫描只用 lookback, 判定时窗口独立)

# 过滤 (v11 默认 quality=off, ST 默认排除)
... --with-quality basic|strong|elite   # 财务前置过滤: 三指标 or_yoy / 毛利率 / 净利同比
                                         #   basic=均>0 (~1949 票, 默认推荐)
                                         #   strong=均>10% (~1100 票)
                                         #   elite=均>20% (~300 票)
                                         #   off=不限 (~5608 票, v11 默认)
... --with-finance                       # 加 PE 过滤 (剔除 PE≤0 / PE>500 亏损/异常)
... --exclude-pe 100                     # 前置过滤 PE>100 的高估值票 (推荐默认 100, 0=不限)
                                            # 50=适中 / 80=较严 / 100=推荐 (排除 23% 估值过高票)
                                            # 跟 --with-finance 互补 (finance 是命中后剔, exclude-pe 是扫描前剔)
... --include-st                         # (反旗) 包含 ST/*ST (默认已排除, 退市风险)

# 详报输出
... --full-report                        # 命中后逐只跑 /t-analyze 22 section 详报 (默认 True)
... --no-full-report                     # 只输出扫描汇总, 不跑详报
... --report-top 0                       # 详报全部 (默认 10, 0=全部)
... --report-subdir wyckoff-a2m-test     # 详报子目录 (用于测试, 跳 watchlist 分流)
... --no-clean                           # 跳过 docs/<subdir>/ 自动清空 (默认清空)

# 性能
... --workers 4                          # 详报 mp.spawn worker 数 (默认 0 = 单进程, 设 1 也单进程)

# 输出
... --no-write-md                        # 不落盘扫描汇总 (默认 True = 落 docs/wyckoff-a2m.md)
... --output docs/wyckoff-a2m.md         # 自定义汇总 md 输出路径
... --bench-report 12                    # [测试用] 跳过扫描, 只跑 12 只详报对比 mp 加速 (docs/_bench/)
```

> **deprecated flag:**
> - `--with-rsi` (2026-10-17 弃用,v11 判定已经够严,不再需要 RSI 过滤,代码保留兼容)

## 典型工作流

**场景 1: 全市场扫描 + 前 10 详报 (默认)**

```bash
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan
# 全市场 ~110s 扫 + 命中后前 10 只详报 (单进程 ~30s, mp.spawn 4 worker ~10s)
# 落盘:
#   docs/wyckoff-a2m.md (扫描汇总表)
#   docs/wyckoff-a2m/analyze-<code>-<name>.md (详报, 跟 watchlist 完全隔离)
```

**场景 2: 全部详报 (~111 命中)**

```bash
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan --report-top 0 --workers 8
# 全市场 ~110s 扫 + 全部 111 只详报
# mp.spawn 8 worker 估 ~50s (含启动开销, 8 worker 启动 5-10s)
```

**场景 3: 只看持仓**

```bash
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan --watchlist-only --watchlist-types 持仓
# 23 只持仓, 1-2 分钟
```

**场景 4: 保留旧详报 + 重跑**

```bash
# 默认清空 docs/wyckoff-a2m/ 旧 md; --no-clean 跳过清空
bash tools/with_venv.sh python -m tools.batch.wyckoff_a2m_scan --no-clean
```

## 跑流程

1. **step 1** — 跑威科夫当前 stage 扫描 (复用 AnalysisEngine + compute_factor_history, 0 网络)
2. **step 2** — 命中后逐只跑 `/t-analyze <code>` 22 section 详报 (默认, 可关)

## 判定逻辑

**链路 (跟 `/t-analyze` 详报 100% 一致):**
```
DataStore.get_ctx(code)
  → AnalysisEngine(strategies=[WyckoffStrategy])
  → engine.analyze_history(ctx, dates)            ← 预算 480 日 history
  → compute_factor_history(ctx, step=1, lookback=120, history=history)
  → row['wyckoff_daily'] 字段 (Accumulation / Markup / Distribution)
```

**v11 字面版 命中条件 (2026-10-17):**
1. **T-0 (最后根) K 线 stage = 'Markup'** — 必须
2. **T-1 ~ T-5 (前 5 根) ALL ∈ {Accumulation, ?}** — 必须 (A + ? 都接受, 排除 Markup 和 Distribution)
   - `?` = 停牌 / 数据不足 / 反弹区, 跟 A 等价
3. T-6 ~ T-9 不卡 (过渡期)
4. **非除权 / 复牌日**: 单日涨跌 > 20% 视为异常, 跳过
5. **stage_detail 不输出** — 简化版只输出 stage 主分类; 9 sub_event / 子阶段 (A/B/C, UTAD / LPS) 在 `/t-analyze` 详报里看

## 性能基线 (2026-10-17 实测, 12 核 mac, v11 字面版)

| scope | 任务数 | 命中 | 扫描耗时 | 详报 (单进程) | 详报 (mp.spawn 4) |
|---|---|---|---|---|---|
| 持仓 watchlist | 23 | (随市场) | ~2-3s | — | — |
| 5812 全市场 (quality=off) | 5812 | (随市场) | **~115s** | — | — |
| 5608 (exclude-st 默认) | 5608 | (随市场) | ~113s | — | — |
| **1949 (quality=basic + ST 排)** | **1949** | **5** (v11 字面版) | **~107s** | **~15s** (5 只) | — |
| 111 命中 + 详报 (quality=basic) | — | 111 | ~109s | ~290s | **~110s** (mp 1.5-2.6x 实测) |

**单只扫描瓶颈:** `scan_sub_events` 内部 9 detector Python loop 序列, GIL 锁死, ThreadPool 0 加速.
(cProfile 2026-10-17 实测: spring detector 占 40%, 其它 8 detector 平分 60%, 不是 c[:i] 复制).

## 性能 trick: mp.spawn 跨 GIL

| workers | 12 只详报 (bench) | 4 只详报 (实测) |
|---|---|---|
| 1 (单进程) | 32.7s | 13s |
| 4 (mp.spawn) | — | 5s (~2.6x) |
| 12 (mp.spawn) | 21.9s (~1.5x) | — |

**结论:** mp.spawn 1.5-2.6x 加速, 起步小批量 (≤8) 收益最高, worker 太多反而被启动开销拖累.

⚠️ **不能用 mp.fork** (duckdb fork 后死锁); mp.spawn 启动开销 ~3-5s per worker.

## 输出

```
| 转变天数 | 代码 | 名称 | 行业 | 转变日 | 转变价 | 当前价 | 涨幅% | PE | PE_TTM | 阶段序列 |
|---|---|---|---|---|---|---|---|---|---|---|
| **T-10** | `605189` | 富春染织 | 纺织 | 20261015 | ¥17.85 | ¥17.85 | +0.0% | — | — | A/A/A/A/A/A/A/A/A/A/M |
```

**字段:**
- **T-N**: 转变距今 N 个交易日 (T-1=昨天, T-5=5 天前)
- **阶段序列**: 最近 10 根 K 线 stage (A / M / D / ?)
- **涨幅%**: 转变日至今
- **PE** 可选 (只有 `--with-finance` 时出)

## 关键约束

- **0 网络**, 走 duckdb 直读 daily parquet
- **默认全市场** (v11 极严, 1949 票里命中 ~5)
- **复用 compute_factor_history** (跟 `/t-analyze` 100% 一致, 不自己写策略)
- **`docs/wyckoff-a2m/`** 详报目录, 跟 watchlist 完全隔离
- **默认清空** `docs/wyckoff-a2m/` 旧 md (按 F5 跑的语义); `--no-clean` 跳过
- **ST 默认排除** (`--include-st` 反旗开启)
- **数据日期**: 用 parquet 最新 trade_date, 不是当前 wall clock

## 命名沿革 (2026-10-16 → 10-17)

- **v1-v4** — 初版到稳定版 (复用 `AnalysisEngine.analyze_history` + `compute_factor_history`, 7 分钟全市场)
- **v5/v6** — 直调 `WyckoffStageFactor.compute()` 25s, 但漏报 30% 数据漂移
- **v7** — 回退 v4 路径确保数据一致
- **v8** (2026-10-16) — 删 `_judge_full` 慢路径 + 直调 `wyckoff_judge` + 1 次 SQL 全市场 K 线 + 每只预算 arrs + scan_sub_events 全段扫 1 次缓存. **~5 分钟全市场** (历史数据), ThreadPool 0 加速.
- **v9** (2026-10-16) — 判定放宽, 任何当前 M 都命中 (宽松模式)
- **v10** (2026-10-17) — 收紧: T-0=M + 前 9 根有非 M
- **v11 字面版** (2026-10-17) — 再收紧: T-0=M + T-1..T-5 ∈ {A, ?}. **当前默认**.

## 相关

- `/t-analyze` (单只深挖, 9 子事件 + stage_detail)
- `/t-rsi6-tech` (技术面超卖)
- `/t-near-low` (5y 回撤超跌)
- `/t-factor-5y` (5 年因子历史)
- `/t-sync-data --kline` (跑前必跑, K 线每日增量)

## 参数速查

| CLI | 默认 | 含义 |
|---|---|---|
| `--watchlist-only` | False | 只扫 watchlist |
| `--watchlist-types` | 持仓,blowout,自选 | `--watchlist-only` 时类型过滤 |
| `--lookback` | 5 | 阶段序列长度 (md 输出) |
| `--workers` | 0 | 详报 mp worker 数 (0=os.cpu_count, 1=单进程) |
| `--write-md` | True | 落盘扫描汇总 md |
| `--output` | docs/wyckoff-a2m.md | md 输出路径 |
| `--full-report` | True | 命中后逐只跑详报 |
| `--report-top` | 10 | 详报数 (0=全部) |
| `--report-subdir` | wyckoff-a2m | 详报子目录 (跳 watchlist 分流) |
| `--no-clean` | False | 跳过自动清空 docs/<subdir>/ |
| `--with-quality` | off | 财务前置过滤 (basic / strong / elite / off) |
| `--with-finance` | False | 详报 PE 过滤 (命中后, PE≤0 / >500) |
| `--exclude-pe` | 0 | 前置 PE 过滤上限 (PE > N 剔, 0=不限, 推荐 100) |
| `--exclude-st` | True | 排除 ST/*ST |
| `--include-st` | False | (反旗) 包含 ST |
| `--with-rsi` | False | **deprecated** (v11 不需要 RSI) |
| `--bench-report` | (unset) | [测试用] 跳过扫描跑 12 只详报对比 mp |

## 跑后验证

```bash
# 详报全列
ls docs/wyckoff-a2m/analyze-*.md | wc -l
# 应 = docs/wyckoff-a2m.md 表行数 - 1

# lint 单个详报 (数据完整性)
bash tools/with_venv.sh python -m tools.render.report_linter docs/wyckoff-a2m/analyze-605189-富春染织.md all
```
