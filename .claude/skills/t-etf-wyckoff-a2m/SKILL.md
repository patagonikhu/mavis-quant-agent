---
name: t-etf-wyckoff-a2m
description: ETF 威科夫 A→M 扫描. 全市场 ETF 找"刚从吸筹转主升"的品种, 自动按跟踪指数去重, 命中后生成单只 md 详报. 0 网络, 走 DataStore. 触发词: "ETF A转M"、"ETF 威科夫拐点"、"ETF 刚启动".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 **命名规范: [`handbook/naming-convention.md`](../../handbook/naming-convention.md)** (7 条硬规则, 5 项 grep 验证)

> **单一事实源:** 过滤逻辑 / 判定参数 / 去重规则 / 性能基线,全部在 `tools/batch/etf_wyckoff_a2m_scan.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候用 + 跑最小例子 + 数据特性"。

## 跟股票版 /t-wyckoff-a2m 的关系

**判定逻辑完全相同** (同源 `wyckoff_judge` + V13 严判), **输出行为也一致**
(都出汇总 md + 逐只 7 section 详报到 `docs/<子目录>/`), 但**过滤链完全不同**:

| 过滤 | 股票版 | ETF 版 |
|---|---|---|
| exclude-st | ✅ | ❌ ETF 无退市风险标记 |
| exclude-pe | ✅ PE≤60 | ❌ **ETF 无市盈率** (指数基金没盈利) |
| with-quality | ✅ yoy/ROE/毛利 | ❌ **无营收/净利/毛利率/ROE** |
| max-net-debt | ✅ 净负债/市值 | ❌ **无资产负债表** |
| **fund_type** | — | ✅ **唯一有效过滤** |

ETF 唯一需要的过滤 = **fund_type**: 排除 `债券型` / `货币型` / `REITs` / `其他`
(债券是震荡上行不是主升浪; 货币收益单调无形态; REITs 是租金现金流驱动)。

> ⚠️ **威科夫判定是纯技术形态** (MA 关系 + 价格位置 + 子事件), 不依赖任何基本面,
> 所以 ETF / 股票 / 指数通用 —— 这是 ETF 版能直接复用股票版判定的根本原因。

## ETF 重复怎么办 (去重)

**同一跟踪指数常有几十个 ETF 代码** (实测: 中证A500 40 个 / 沪深300 30 个 / 中证500 22 个 / 科创50 20 个),
走势几乎一致 —— 全列出来等于把同一个机会报 N 遍。

本 skill 的做法 —— **先全扫, 后去重** (1992 只候选全部判 V13, 保证不漏), 然后两级合并 (并查集):

1. **归一化 benchmark 相同** → 同指数, 合并
   归一化要拆出复合基准里权重最大的**指数**成分, 因为源数据写法不统一:
   `中证酒指数收益率×95%+商业银行活期存款利率(税后)×5%` → `中证酒指数`
2. **60 日收益率相关性 ≥ 0.95** → 同一指数的不同写法 / 不同基金类型, 合并
   纯字符串匹配抓不到这种: 512690 鹏华中证酒ETF 的 benchmark 写 `中证酒指数`,
   160632 鹏华中证酒LOF 写 `中证酒指数×95%+…`, 161725 招商中证白酒LOF 写 `中证白酒指数…`
   —— 三只是同一个指数 (实测相关度 0.970~0.982)

**阈值 0.95 的实测依据** (11 只命中全 55 对): ≥0.95 命中 4 对 (全是真重复),
第 5 名掉到 0.885 (中证主要消费 ~ 中证酒, 不同指数), 分离干净。

每组只留 **1 个代表**: ① 最近 20 日日均成交额大者优先 (流动性最好, 最可投) ② 成交额相同则 K 线根数多者优先。
其余同组命中代码在汇总 md 的「同指数合并明细」表里列为备选, 且**已有详报会被改标 `merged`** (如实说明, 不留"命中"误导)。

> `--no-dedup` 可关掉去重, 看原始命中 (对比用)。

## 跑

```bash
# 默认 (全市场 ETF, fund_type 自动过滤, 去重, 出汇总 + 逐只详报)
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan

# 所有 flag + 默认值
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --help

# 只扫指定 fund_type (默认=除排除项外全扫)
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --types 股票型

# 指定代码
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --codes 512480 515790

# 不去重 (看原始命中)
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --no-dedup

# 只出扫描汇总, 不跑详报
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --no-full-report

# 详报跑全部命中 (0 = 全部)
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --report-top 0
```

## 输出

| 产物 | 路径 |
|---|---|
| 扫描汇总 | `docs/etf-wyckoff-a2m.md` |
| 单只详报 | `docs/etf-wyckoff-a2m/analyze-{code}-{名称}.md` |

**详报行为跟 `/t-wyckoff-a2m` 一致** (refresh 模式, 不清空目录):

- **A. 刷新** = 目录里已有 且 本次命中 → 重新生成 (数据更新到最新交易日)
- **B. 补充** = 目录里没有 且 本次命中 → 新生成
- **C. 留档** = 目录里已有 但本次未命中 → 也刷新数据, 文件头标 ⚠️

状态标记用 HTML 注释, 幂等 (重复跑不累积):
`<!-- ETF_A2M_STATUS hit -->` (本次命中) / `<!-- ETF_A2M_STATUS merged -->` (同指数被合并) / `<!-- ETF_A2M_STATUS stale -->` (曾命中现未命中)。
跟股票版的 `<!-- A2M_STATUS ... -->` 分开, 免得两边互相覆盖。

## 数据特性

- **0 网络**, 数据走 `DataStore.get_etf_basic()` / `DataStore.get_etf_klines()` (不直读 parquet)
- 全市场 2252 只 ETF → fund_type 过滤后 1992 → 根数≥220 后 **1602 只可扫, ~10 秒**
- 命中率 **0.7%** (11 只 / 1602) —— 比股票版 2.6% 更严 (ETF 长期横盘, 难有干净启动)
- 去重后 **8 只** (11 → 8, 合并 3 组: 中证酒指数 3 只 / 中证主要消费 2 只 / 其余独立)
- 判定: V13 严判 = 当前 M + 最近 40 根 M 占比 < 10% + 单日涨跌 < 20% + K线 ≥ 11 根
- **因子历史走势表: ETF 显示 200 个交易日** (股票 120) —— 指数走势比个股慢, 120 根 (~6 个月)
  看不出一个完整波段。实测 159672: 20260106 → 20261104 (约 10 个月)
  - 真源: `analysis_result_signals.factor_history_lookback(code)` (按 etf_basic 表判定 ETF)
  - ETF 上市较晚的不足 200 根就显示全部 (如 159228 有 336 根 → 显示 200)
- **交叉验证**: 同一只 ETF 走股票版 `_process_one` vs ETF 版, 转变日/M占比/40根stage序列 **8/8 逐项全等**
- **v1→v2 回归**: 换 DataStore 取数后, 原始命中仍是同样 11 只 (逐只转变日/涨幅/M占比/阶段序列全等)

## 跑后验证

```bash
# 看命中清单 + 同指数合并明细
head -30 docs/etf-wyckoff-a2m.md

# 看单只详报 (头部有 A→M 状态标记)
head -8 docs/etf-wyckoff-a2m/analyze-*.md | head -20
```

## 相关

- `/t-wyckoff-a2m` (股票版, 同一判定逻辑, 不同过滤链)
- `/t-sync-data --etf` (跑前必跑, ETF K 线增量)
- `/t-analyze` (个股深挖 —— ETF 也能看威科夫阶段)
