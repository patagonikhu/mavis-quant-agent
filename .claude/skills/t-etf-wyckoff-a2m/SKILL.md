---
name: t-etf-wyckoff-a2m
description: ETF 威科夫 A→M 扫描. 全市场 ETF 找"刚从吸筹转主升"的品种. 0 网络, 走 etf/kline parquet. 触发词: "ETF A转M"、"ETF 威科夫拐点"、"ETF 刚启动".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 **命名规范: [`handbook/naming-convention.md`](../../handbook/naming-convention.md)** (7 条硬规则, 5 项 grep 验证)

> **单一事实源:** 过滤逻辑 / 判定参数 / 性能基线,全部在 `tools/batch/etf_wyckoff_a2m_scan.py` 模块顶部 docstring + `--help`。SKILL.md 只保留"啥时候用 + 跑最小例子 + 数据特性"。

## 跟股票版 /t-wyckoff-a2m 的区别 (重要)

**判定逻辑完全相同** (同源 `wyckoff_judge` + V13 严判), 但**过滤链完全不同**:

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

## 跑

```bash
# 默认 (全市场 ETF, fund_type 自动过滤)
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan

# 所有 flag + 默认值
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --help

# 只扫指定 fund_type (默认=除排除项外全扫)
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --types 股票型

# 只扫 whitelist 里的 18 只
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --whitelist-only

# 指定代码
bash tools/with_venv.sh python -m tools.batch.etf_wyckoff_a2m_scan --codes 512480 515790
```

## 数据特性

- **0 网络**, 走 `data/history/etf/kline/*.parquet` (跟 `/t-sync-data --etf` 同源)
- 全市场 2348 只 ETF → fund_type 过滤后 1992 → 根数≥220 后 **1602 只可扫, ~9 秒**
- 命中率 **0.7%** (11 只 / 1602) —— 比股票版 2.6% 更严 (ETF 长期横盘, 难有干净启动)
- 判定: V13 严判 = 当前 M + 最近 40 根 M 占比 < 10% + 单日涨跌 < 20% + K线 ≥ 11 根
- **交叉验证**: 同一只 ETF 走股票版 `_process_one` vs ETF 版, 转变日/M占比/40根stage序列 **8/8 逐项全等**

## 跑后验证

```bash
# 看命中清单
head -20 docs/etf-wyckoff-a2m.md
```

## 相关

- `/t-wyckoff-a2m` (股票版, 同一判定逻辑, 不同过滤链)
- `/t-sync-data --etf` (跑前必跑, ETF K 线增量)
- `/t-analyze` (个股深挖 —— ETF 也能看威科夫阶段)
