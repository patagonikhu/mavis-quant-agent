# 新 Strategy / Factor 编写模板

> 📌 **本文件是"写新 strategy / factor 时的 5 步模板"。**
> 配合 [`architecture.md`](architecture.md) §6 "添加新功能 checklist" 一起看。

---

## 0. 决策: 你要加什么?

| 场景 | 走哪条路径 | 看哪节 |
|---|---|---|
| 加 **新 strategy 类** (走 7 strategy 主路径) | §1 — 5 步 |  |
| 加 **新 factor** (通用: 价/量/风险) | §2 — 3 步 |  |
| 加 **新 factor** (估值: PEG/DCF/Magic) | §3 — 2 步 (单文件铁律) |  |
| 加 **新 batch 入口** | §4 — 3 步 |  |

---

## 1. 新 Strategy 类 (5 步)

**目标:** 加 1 个新 strategy 类 (例如 `FundFlowStrategy`),复用到 `/t-analyze` + `/t-backtest` + `/t-bb-obv`。

### Step 1: 在 `tools/analysis/analysis_engine.py` 加 1 个类

参考现有 6 个 strategy 类的写法 (例如 `ObvStrategy`),**只读 `ctx`,不拉数据**:

```python
class NewStrategy:
    """新 strategy 模板 (返回 dict, 不继承 ABC)"""
    name = "new"

    def analyze_history(self, ctx: RawContext) -> dict:
        # 1. 读 ctx 字段 (不要 import tushare / 调网络)
        kline = ctx.kline
        # 2. 算 factor
        score = ...
        # 3. 返回 dict (跟其他 strategy 一致: 含 score + 因子数据)
        return {"score": score, "factor_x": ..., "verdict": "🟢/🟡/🔴"}
```

### Step 2: 在 `RawContext` 加字段 + `DataStore.get_ctx` 加 1 行

如果你需要新数据 (例如 `ctx.fund_flow_extended`):

```python
# 1. RawContext 加字段
@dataclass
class RawContext:
    ...
    fund_flow_extended: list = field(default_factory=list)

# 2. DataStore.get_ctx 加 1 行 (调 sources/caches/ 落盘)
def get_ctx(cls, code, ...):
    ...
    return RawContext(
        ...,
        fund_flow_extended=read_fund_flow_extended(code),  # 走落盘, 不直连网络
    )
```

### Step 3: 在 `_run_phase1_strategies()` 注册 (1 行)

```python
# tools/analysis/analysis_engine.py
def _run_phase1_strategies(ctx, ...):
    ...
    result["new"] = NewStrategy().analyze_history(ctx)
```

### Step 4: 在 `tools/analysis/render_data.py` 加 @property (暴露到 `data.xxx`)

```python
@property
def new_factor(self) -> dict:
    return self.analysis.get("new", {})
```

### Step 5: 验证

```bash
# 守门员
python -m tools.batch.guardrail --check parquet
python -m tools.batch.guardrail --check network

# 跑 1 单只 verify 22 section 报出来
python -m tools.batch.t_analyze_one --code 300274

# 跑回测 verify 信号可回放
python -m tools.batch.batch_backtest --signal new:xxx
```

---

## 2. 新通用 Factor (3 步)

**目标:** 加 1 个新 factor (例如 `compute_atr_breakout`),供 analysis_result_signals 或 render_data 调。

### Step 1: 在 `tools/factors/{basic,risk,volume}.py` 加 `def compute_xxx(...)`

```python
# tools/factors/factor_basic.py (或 factor_risk.py / factor_volume.py)
def compute_atr_breakout(df: pd.DataFrame, window: int = 20) -> dict:
    """纯函数: 输入 df, 输出 dict (无副作用)"""
    atr = df["high"] - df["low"]
    breakout = (df["close"] - df["close"].shift(1)) / atr
    return {"score": float(breakout.iloc[-1]), "atr_mean": float(atr.mean())}
```

### Step 2: 在 `analysis_result_signals.py` 调

```python
from tools.factors.factor_basic import compute_atr_breakout

def compute_factor_history(ctx, ...):
    ...
    new_result = compute_atr_breakout(pd.DataFrame(ctx.kline))
    raw["new_factor"] = new_result
```

### Step 3: 验证

```bash
python -m tools.batch.guardrail --check factor    # 验证 class Factor 没复活
python -m tools.batch.t_analyze_one --code 300274  # 跑 1 单只 verify
```

---

## 3. 新估值 Factor (2 步, 单文件铁律)

**目标:** 加 1 个估值类计算 (例如 `compute_ev_ebit`)

### Step 1: 在 `tools/factors/valuation/factor_lib.py` 加 `def compute_xxx(...)`

**铁律:** 估值 factor 全部在 `factor_lib.py` 单文件,不允许新文件。

```python
# tools/factors/valuation/factor_lib.py
def compute_ev_ebit(financials: list[dict], market_cap_wan: float) -> dict:
    """纯函数"""
    ebit = _ttm_ebit(financials)[0] or 0
    ev = market_cap_wan + netdebt(financials)
    return {"ev_ebit": ev / ebit if ebit else 0}
```

### Step 2: 验证

```bash
python -m tools.batch.guardrail --check factor    # 验证单文件铁律
```

---

## 4. 新 Batch 入口 (3 步)

**目标:** 加 1 个新 batch 脚本 (例如 `tools/batch/my_scan.py`),配套 1 个 skill。

### Step 1: 写 `tools/batch/my_scan.py`

```python
"""my_scan.py — 简述用途"""
import sys
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 唯一数据访问入口
from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine

def main():
    # 1. 取数据 (走 DataStore, 0 网络 0 直读 parquet)
    df = DataStore.load_all_kline(years=1.0)
    # 2. 算 factor
    ...
    # 3. 输出 markdown / stdout
    print(...)

if __name__ == "__main__":
    main()
```

### Step 2: 加 `.claude/skills/t-my-scan/SKILL.md`

(参考现有 9 个 skill 的 SKILL.md 格式)

### Step 3: 验证

```bash
python -m tools.batch.my_scan                                # 跑通
python -m tools.batch.guardrail --check parquet              # 业务层走 DataStore
python -m tools.batch.guardrail --check network              # 0 网络
```

---

## 5. 反例 (禁止)

```python
# ❌ 反例 1: 在 strategy 里调网络
class NewStrategy:
    def analyze_history(self, ctx):
        import tushare as ts
        df = ts.pro_api().fund_flow(...)   # ❌ 业务层调网络, 应走 DataStore

# ❌ 反例 2: 直读 parquet
def load_data():
    import pandas as pd
    return pd.read_parquet("data/history/daily/2026Q3.parquet")  # ❌ 应走 DataStore

# ❌ 反例 3: class XXX(Factor) 包装
class MyFactor:                             # ❌ 应 def compute_my_factor(...)
    def __init__(self):
        self.name = "my"
    def compute(self, df):
        return df["close"].mean()

# ❌ 反例 4: 在 tools/factors/valuation/ 新建文件
# tools/factors/valuation/my_new_file.py  # ❌ 应加到 factor_lib.py 单文件
```

---

## 6. 关联

- [`architecture.md`](architecture.md) — 模块归属表 + 4 条硬约束
- `tools/analysis/analysis_engine.py` — 7 strategy 类范本
- `tools/factors/valuation/factor_lib.py` — 估值 factor 范本
- `tools/batch/guardrail.py` — 守门员 (验证约束)
