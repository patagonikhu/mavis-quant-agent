---
name: t-magic
description: Magic Formula 排名 (Greenblatt ROC + EY 联合排名), 实战"好公司+便宜股"双优筛选. 1923 只科技股 H1 2026 数据, Top 20 输出 docs/magic-top20.md + 4 项摘要 (PEG/DCF/Magic/卡点⭐). 用户说"Magic 排名"、"ROC/EY 排名"、"好公司+便宜股"、"加 watchlist"时触发.
user-invocable: true
allowed-tools:
  - Bash

## 原理 (Greenblatt 2005, 《The Little Book That Beats the Market》)

**两个独立指标, 联合排名找双优:**

| 指标 | 公式 | 含义 |
|---|---|---|
| **ROC** (Return on Capital) | TTM EBIT / (净营运资本 + 固定资产) × 100% | 资本效率, **高 = 好公司** |
| **EY** (Earnings Yield) | TTM EBIT / EV × 100% | 盈利对 EV 回报, **高 = 便宜股** |
| **EV** (Enterprise Value) | 市值 + 净债务 (亿) | 买下整家公司要付的总价 |
| **联合排名** | (ROC 排名 + EY 排名) / 2 | 数字小的胜出 (双优) |

**为什么用 TTM (Trailing 12 Months):**
- A 股财务数据是"累计值" (年报=全年, 半年报=半年), 不能简单 2 期相加
- 单季 H1 算的 ROC 会虚高 (矿用车 H1 旺)
- TTM 策略: 优先用最新**全年** (12-31), 半年报 (06-30) 自动降级为 proxy 并标 `seasonal_warning`

**为什么不用 PEG 排名:**
- PEG = Forward PE / 稳态 CAGR, 依赖 EPS 预期
- 1200+ 只科技股**没机构覆盖** (机构 EPS 预期缺失), 多数 PEG 算不出来
- Magic 只用 EBIT/NWC/FA/净债务, **Tushare 一次拉全市场, 0 缺失**

## 行业过滤 (8 类 EXCLUDED_INDUSTRIES, 排名跳过)

银行 / 保险 / 证券 / 信托 / 期货 / 租赁 / 房地产 / 物业管理 / 园区开发
电力 / 水务 / 燃气 / 热力 / 环保 / 多元金融

> 这些行业的 ROC / EY 失真 (银行靠 NIM 不是 EBIT, 房地产靠周转不是资本效率)

## 用法

```bash
/t-magic                            # 默认: 跑全流程 (排名 + 摘要 + 加 watchlist)
/t-magic --rank-only                # 只排名, 不出摘要不加 watchlist
/t-magic --summary-only             # 排名 + 4 项摘要 (PEG/DCF/Magic/卡点⭐)
/t-magic --top 50                   # 改 Top N (默认 20)
/t-magic --period 2026Q2            # 改财务报告期 (默认最新季)
/t-magic --skip-watchlist           # 不加 watchlist
```

## 数据源

| 数据 | 路径 | 同步方式 |
|---|---|---|
| 财务 (EBIT/NWC/FA/净债) | `data/history/financials/{period}.parquet` | `sync_financials()` (Tushare fina_indicator_vip, 1 次 API 拿全市场 9255 行, 1.2s 落盘) |
| 市值 (万元) | `data/history/daily_basic/{period}.parquet` | `sync_incremental()` (Tushare daily_basic) |
| 名称 / 行业 | `data/history/stock_basic/stock_basic.parquet` | `sync_stock_basic()` (Tushare stock_basic) |
| EPS 机构预期 (摘要用) | `data/cache/eps/{code}.json` | `eps_consensus_cache` (datacenter.eastmoney.com) |

**首次跑全流程 ~3 分钟** (含 3 个 API 同步: financials / daily_basic / EPS 缓存)
**续跑 ~30 秒** (0 网络, 走本地 parquet + cache)

## 执行 (4 步, 走 batch 脚本, 0 网络优化)

### Step 1: 同步财务数据 (Tushare fina_indicator_vip, 1 次 API 拿全市场)

```bash
# 默认同步最新季 (H1 2026)
bash tools/with_venv.sh python -c "from tools.storage.store import sync_financials; sync_financials()"
# 续跑: 0 API (status='ok'/'skip' 跳过)
# 首次全量: 1.2 秒
```

**续跑规则:**
- parquet 已有 `status='ok'` 的票 → 跳过
- parquet 已有 `status='skip'` 的票 → 跳过 (VIP 没返的小票, 永久不重试)
- 其余票 → 1 次 VIP API 全市场拉, 客户端筛交集

### Step 2: 跑排名 (直算, 0 网络)

```bash
# 后台跑 (>30s 必须 background, 不用 timeout)
bash tools/with_venv.sh python -m tools.batch.magic_top20 --top 20
```

**脚本行为:**
- 1923 只科技股 × ~1.5s 跑完
- 1205 只有效 (686 EBIT≤0 跳过, 0 行业 EXCLUDED)
- 联合排名: ROC 降序 + EY 降序, 取均值小者
- 写: `docs/magic-top20.md` (Top 20 表 + 统计 + 数据流图 + 用法)

### Step 3: 跑 4 项摘要 (PEG / DCF / Magic / 卡点⭐)

```bash
bash tools/with_venv.sh python -m tools.batch.magic_top20_summary
```

**脚本行为:**
- 读 Top 20 表, 逐只补 PEG / DCF L/可达 / Magic 排名
- 14/20 有数据, 6 只小盘无机构 EPS 预期 (Magic 排名已有, PEG/DCF 标 N/A)
- 写: `docs/magic-top20-summary.md`

**卡点⭐** = N/A (LLM 判断, 代码跑不出); 用户可单独点单只补

### Step 4: 加 watchlist (可选)

```bash
bash tools/with_venv.sh python -m tools.batch.add_magic_top20_to_watchlist
```

**脚本行为:**
- 读 Top 20, 加进 `data/watchlist.json`, `list_type="Magic初筛"`
- 跳过已存在的票 (江波龙 #11 已在 watchlist)
- changelog 加 1 条

## 输出示例

`docs/magic-top20.md`:
```
# Magic Formula 排名 Top 20 — 2026-09-01

| # | 代码 | 名称 | 行业 | ROC (%) | EY (%) | ROC 排名 | EY 排名 | 综合 | 市值 (亿) | EV (亿) |
|---|------|------|------|---------|--------|----------|---------|------|-----------|---------|
| 1 | 600262 | 北方股份 | 专用机械 | 1182.3 | 14.80 | 2 | 5 | 4.0 | 37 | 18 |
| 2 | 002546 | 新联电子 | 电气设备 | 194.3 | 10.60 | 7 | 8 | 11.5 | 67 | 65 |
| 3 | 300724 | 捷佳伟创 | 专用机械 | 136.9 | 17.40 | 12 | 3 | 12.0 | 193 | 178 |
...
```

`docs/magic-top20-summary.md`:
```
# Magic Top 20 摘要 — 2026-09-01

| # | 代码 | 名称 | 行业 | 卡点⭐ | PEG | DCF (r=10%) | Magic 排名 | 当前价 | 总市值 (亿) |
|---|------|------|------|--------|-----|-------------|------------|--------|-------------|
| 1 | 600262 | 北方股份 | 专用机械 | N/A | ❌ 需要 actual + estimate | ❌ 需要至少 2 年 E 数据 | #1 (综合 4.0) | 15.24 | 36 |
...
```

## 实战策略

| 信号 | 操作 |
|---|---|
| Magic #1-#5 + PEG<1.5 | **🥇 高信念, 双侧便宜** (重点关注) |
| Magic #1-#10 + PEG>2 | 🥉 好公司但贵, 等 PEG 修复或减仓 |
| Magic #10-#20 + PEG<1.5 | 🥈 标准, 便宜但资本效率一般 |
| 任意 + L/E3>8 或 L/可达>2 | ❌ 叙事透支, 不买 |

**OBV 信号版块适用性:**
- ✅ 光学/封测/HBM: 主力控盘度高, OBV 准
- ❌ 周期股 (矿用车/锂电/钢铁): 行业 β 主导, OBV 个股信号被淹没
- ❌ 题材/小盘: 主力分散, 噪声大

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--top` | 20 | 输出前 N 名 |
| `--period` | 最新季 | 财务报告期 (e.g. 2026Q2) |
| `--rank-only` | False | 只跑排名, 不出摘要不加 watchlist |
| `--summary-only` | False | 排名 + 摘要, 不加 watchlist |
| `--skip-watchlist` | False | 跳过加 watchlist 那步 |

## 相关

- `tools/batch/magic_top20.py` — 排名 (1205 有效 / 1923 科技股, ~15s)
- `tools/batch/magic_top20_summary.py` — 4 项摘要 (14/20 有 PEG/DCF)
- `tools/batch/add_magic_top20_to_watchlist.py` — 加进 watchlist
- `tools/factors/valuation/magic_formula.py` — calc_roc / calc_ey / calc_magic_score
- `data/history/financials/{period}.parquet` — Tushare fina_indicator_vip 落盘
- `docs/magic-top20.md` / `docs/magic-top20-summary.md` — 输出
- `/t-analyze <code>` — Magic Top 20 中某只深挖 22 section
- `/t-bb-obv` / `/t-near-low` — 配套扫描 (Magic 给"好公司+便宜", bb-obv 给"短期吸筹形态", near-low 给"超跌反弹")
