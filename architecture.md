# Mavis 项目架构 & 代码组织规范

> 📌 **本文件是所有 skill / batch / 业务代码的"代码放哪"规范。**
> 决策规则: 本文件 + `.claude/skills/_shared/analysis_framework.md` + CLAUDE.md 三者对齐。

---

## 1. 三层架构 (铁律)

```
L1 Sync    →  tools/storage/sync.py             # 唯一网络入口
L1 Store   →  tools/storage/store.py (DataStore) # 唯一数据读接口
L2 Analysis → tools/analysis/ + tools/factors/  # 0 网络, 0 直读 parquet
L3 Render  →  tools/render/                     # 报告渲染
+ batch    →  tools/batch/<skill>.py            # 7 个 skill 入口 + 守门员
```

**任何业务代码** 都走这条链。**0 绕开**。

---

## 2. 模块归属表 (新代码必须按此放)

| 代码类型 | 唯一路径 | 入口 | 禁止 |
|---|---|---|---|
| **网络拉数据** | `tools/storage/sources/{tushare,eastmoney}.py` | `tools.storage.sync` 调 | 任何业务层 import 网络库 |
| **数据读** (DAO) | `tools/storage/store.py` (DataStore) | 25+ 公开方法, 7+ bulk | 直读 parquet / 直连 sqlite / 调 duckdb |
| **数据落盘** | `tools/storage/caches/{eps,fflow_history}.py` | sync 调 | 业务层自己写 parquet |
| **7 strategy 类** | `tools/analysis/analysis_engine.py` | `AnalysisEngine.analyze(ctx)` | 自己拉数据, 直读 dump |
| **Phase2 派生** | `tools/analysis/analysis_result_signals.py` | `compute_factor_history` 等 | 复制 strategy 逻辑 |
| **RenderData 容器** | `tools/analysis/render_data.py` | `RenderData.from_result(ctx, result)` | 拼 markdown 字符串 |
| **通用 factor** | `tools/factors/{basic,risk,volume}.py` (纯函数) | `compute_xxx(...)` | `class XXX(Factor)` 包装 |
| **估值 factor** | `tools/factors/valuation/factor_lib.py` (单文件) | `compute_peg/dcf/roc/ey` | 散到多文件 |
| **缠论/威科夫/SMC** | `tools/factors/{chan,wyckoff,smc}/` | 复杂框架, 允许 class | 写业务逻辑 |
| **报告 schema** | `tools/render/report_schema.py` (单一真源) | `report_linter` 校验 | 在 8 处各定义 22 section |
| **报告渲染** | `tools/render/report_renderer.py` | `render_report(data)` | 业务层调 report 前自己拼 markdown |
| **批量入口** | `tools/batch/<skill>.py` (one-file-per-skill) | 调 DataStore + AnalysisEngine | 直读 parquet / 调 duckdb / 直连 sqlite |
| **守门员** | `tools/batch/guardrail.py` | `check_xxx()` + `--check xxx` | 在 review 阶段手动验 |

---

## 3. 4 条硬约束 (违反则 `/t-guardrail` FAIL)

1. **业务层 0 网络** — `tools/analysis/` + `tools/render/` + `tools/factors/` + `tools/batch/`(除 `sync.py` / `guardrail.py` / `_test_speed.py`) 禁止 `import requests/tushare/urllib/duckdb-socket` 实际调用
2. **业务层走 DataStore** — 禁止 `pd.read_parquet` / `duckdb.execute('read_parquet...')` / `sqlite3.connect('data/...')` (signal_cache 业务 sqlite 除外)
3. **新 factor 走纯函数** — 通用 factor 必须 `def compute_xxx(...)`, 禁止 `class XxxFactor(Strategy)`
4. **报告 22 section 单一真源** — 在 `tools/render/report_schema.py` 改, 不在 batch/*.py 拼 markdown

### 守门员覆盖

| 约束 | 守门员 check | 跑法 |
|---|---|---|
| 1. 业务层 0 网络 | `network` | `python -m tools.batch.guardrail --check network` |
| 2. 业务层走 DataStore | `parquet` | `python -m tools.batch.guardrail --check parquet` |
| 3. factor 纯函数 | `factor` | `python -m tools.batch.guardrail --check factor` |
| 4. 22 section schema | `report_linter` | 走 `report_renderer` 自动跑 |

---

## 4. 复用清单 (调谁, 别自己造轮子)

### 4.1 数据访问

```python
# ✅ 唯一入口
from tools.storage.store import DataStore
ctx = DataStore.get_ctx(code)                       # 单只全 ctx
df = DataStore.load_all_kline(years=5.5)            # 全市场 K 线 dict
df = DataStore.load_all_daily_full(years=0.5)        # 全市场 K 线 df (含 pct_chg)
df = DataStore.load_all_daily_basic()                # 全市场 daily_basic
df = DataStore.load_all_financials()                 # 全市场 financials (5 季)
df = DataStore.load_financials_period(period)        # 单季 financials
df = DataStore.get_stk_factor_latest()               # 每只票最新一日 stk_factor
mc = DataStore.get_market_cap_at_date('20260903')    # 某日全市场市值 dict
df = DataStore.load_stock_basic()                    # 静态股票基础信息
df = DataStore.load_all_fflow_history()              # 全市场主力资金流 (5 季)
```

### 4.2 AnalysisEngine

```python
# ✅ 唯一入口
from tools.analysis.analysis_engine import AnalysisEngine
result = AnalysisEngine().analyze(ctx)                # 7 strategy + 派生
# result.raw 拿所有数据: result.raw["chan"] / ["wyckoff"] / ["peg"] / ...
```

### 4.3 通用 factor (纯函数)

```python
from tools.factors.factor_basic import (
    compute_three_layer_position,
    compute_alpha_001,
)
from tools.factors.factor_risk import (
    compute_exit_signals,
    compute_stop_profit_loss,
)
from tools.factors.factor_volume import (
    compute_obv,  # 经典 OBV (Granville 1963)
)
from tools.factors.valuation.factor_lib import (
    compute_peg, compute_dcf_l, compute_roc, compute_ey,
    compute_magic_one_day, find_full_year_financials,
)
```

### 4.4 报告

```python
from tools.analysis.render_data import RenderData
from tools.render.report_renderer import render_report

data = RenderData.from_result(ctx, result)
md = render_report(data)  # 22 section markdown
```

---

## 5. 典型反模式 (被 guardrail 抓)

```python
# ❌ 反模式 1: 直读 parquet
import pandas as pd
df = pd.read_parquet("data/history/daily/2026Q3.parquet")
# → 改用: DataStore.load_all_kline(years=0.5)

# ❌ 反模式 2: SQL 直读
import duckdb
df = duckdb.execute("FROM read_parquet('data/history/financials/*.parquet')").df()
# → 改用: DataStore.load_all_financials()

# ❌ 反模式 3: 直连业务 db
import sqlite3
conn = sqlite3.connect("data/analysis_cache.db")  # signal_cache 业务 db 除外
# → 改用: DataStore 的相关方法, 或确认是 signal_cache 类业务缓存

# ❌ 反模式 4: 业务层调网络
import requests
r = requests.get("https://api.tushare.com/...")
# → 改用: tools/storage/sources/tushare.py (sync 调)
```

---

## 6. 添加新功能 checklist

| 场景 | 必须做 |
|---|---|
| **加新 strategy 类** | `analysis_engine.py` 加 class + `RawContext` 加字段 + `_run_phase1_strategies` 注册 + `render_data.py` 加 @property + 跑 `guardrail --check parquet/network` + 跑 `t-analyze <code>` verify |
| **加新 factor (通用)** | `factors/{basic,risk,volume}.py` 加 `def compute_xxx(...)` 纯函数 + `analysis_result_signals.py` 调 + 跑 `guardrail --check factor` |
| **加新 factor (估值)** | `factors/valuation/factor_lib.py` 加 (单文件铁律) + 跑 `guardrail --check factor` |
| **加新 batch 脚本** | `batch/<skill>.py` 走 DataStore + AnalysisEngine + 跑 `guardrail --check parquet/network` |
| **加新数据源** | `storage/sources/<name>.py` + `storage/sync.py` 加 1 flag + 跑 `t-sync-data --<flag>` verify |
| **加新守门员 check** | `batch/guardrail.py` 加 `check_xxx()` + `main()` 加 `--check xxx` 分发 |

---

## 7. 关联文件

- **CLAUDE.md** — 项目铁律 (3 层架构 / 数据拉取 / 报告顺序)
- **`.claude/skills/_shared/analysis_framework.md`** — 统一分析框架 (缠论 + 估值双指标)
- **`.claude/skills/strategy_recipe.md`** — 新 Strategy 模板 (5 步)
- **`tools/render/report_schema.py`** — 22 section 单一真源
- **`docs/architecture.md` / `docs/guardrail-report.md`** — 守门员报告
