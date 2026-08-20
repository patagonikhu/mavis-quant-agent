---
name: t-pull
description: 唯一数据入口 (v2.0, 2026-07-29 C 方案) — 项目里所有数据通过 `tools/batch/agent_data.AgentData` 走 dump 路径, 1 个类自动 ensure fresh。禁止所有 skill 直接 curl ifzq/qtimg/push2his/datacenter/10jqka 等已废弃源。其他 skill (t-analyze/t-trigger/t-monitor 等) 必须先调 t-pull (经 AgentData) 拉数据生成 data/dump/{code}.json, 然后读 dump 字段做分析。触发: 用户说"拉数据"/"刷新 dump"/"跑 dump_data"/"拉新数据"/"分析 002028" 时使用。

# t-pull: 唯一数据入口 (v2.0, 2026-07-29 C 方案)

## 核心原则

**1 个类, 1 个参数搞定拉数据**:

```python
from tools.batch.agent_data import AgentData
from tools.analysis.analysis_data import AnalysisData
data = AgentData("002028")  # 默认 max_age_min=60, 1 小时内免拉
analysis = AnalysisData.from_raw(data.raw()).analysis or {}
print(analysis.get("wyckoff", {}).get("stage"))  # 'Accumulation'
data.render()  # 渲染报告
```

**禁止任何其他途径拉数据**:
- ❌ `web.ifzq.gtimg.cn` — WAF 拦截已废弃
- ❌ `qt.gtimg.cn` — GBK 编码 / WAF 拦截已废弃
- ❌ `push2his.eastmoney.com` / `push2.eastmoney.com` — WAF 拦截
- ❌ `datacenter-web.eastmoney.com` — WAF 拦截
- ❌ `basic.10jqka.com.cn` — GBK 编码 / 频控
- ❌ `money.finance.sina.com.cn` (60m) — timeout 5s (v5.10.3 优化)
- ❌ 直 curl `tushare` API
- ✅ **唯一**: `tools/batch/agent_data.AgentData` (走 Tushare + Sina 60m + get_index_daily, dump 字段跟 s5 1:1)

## max_age_min 决定要不要 dump

| 场景 | max_age_min | 行为 | 何时 dump |
|---|---|---|---|
| **日常分析** (默认) | 60 | 1 小时内免拉 | 超 60 分才 dump |
| **盘中盯盘** | 5 | 5 分钟内免拉 | 超 5 分才 dump |
| **强制重拉** | 0 / `force=True` | 永远 dump | 每次都 dump |
| **永不重拉** (看历史) | 999999 | 永不 dump | 0 次 |
| **复盘 / baseline** | 0 | 永远 dump | 每次都 dump |

**C 方案最大价值**:
- 不用管 dump 存不存在、新不新鲜
- 不用记哪个参数对应哪个模式
- 1 个 `max_age_min` 控制所有

## 用法 (C 方案 v2.0)

### 1. 单只股票 (代码)

```python
from tools.batch.agent_data import AgentData

# 默认 1 小时内免拉
data = AgentData("002028")
analysis = AnalysisData.from_raw(data.raw()).analysis or {}
print(analysis.get("wyckoff", {}).get("stage"))  # 'Accumulation'
print(analysis.get("resonance", {}).get("1d", {}).get("direction"))  # '🟢四向↑'

# 强制重拉
data = AgentData("002028", force=True)

# 永不重拉 (只看 cache)
data = AgentData("002028", max_age_min=999999)

# 盘中盯盘 (5 分钟内免拉)
data = AgentData("002028", max_age_min=5)
```

### 2. 渲染报告

```python
data = AgentData("002028")
md = data.render()  # 自动写 docs/analyze-002028-思源电气.md
print(f"报告 {len(md)} chars")
```

### 3. CLI (老用法兼容)

```bash
# 默认 (1 小时内免拉, 走 CACHED)
bash tools/with_venv.sh python -m tools.dump_data 002028

# 拉 + 渲染
bash tools/with_venv.sh python -m tools.dump_data 002028 --render

# 只读 dump (永不重拉)
bash tools/with_venv.sh python -m tools.dump_data 002028 --analyze-only

# 强制重拉
bash tools/with_venv.sh python -m tools.dump_data 002028 --force

# 自定义 max_age_min
bash tools/with_venv.sh python -m tools.dump_data 002028 --age 5

# 跑 AgentData 自己的 CLI
bash tools/with_venv.sh python -m tools.batch.agent_data 002028
bash tools/with_venv.sh python -m tools.batch.agent_data 002028 --render
bash tools/with_venv.sh python -m tools.batch.agent_data 002028 --force
bash tools/with_venv.sh python -m tools.batch.agent_data 002028 --analyze-only
```

### 4. 批量 (watchlist 全部)

```bash
# 拉 + 渲染全部 watchlist (3-4 分钟, 4 worker)
bash tools/refresh_all.sh

# 只拉数据 (5-8 分钟, 4 worker)
bash tools/pull_all.sh

# 智能刷 (age < 1h 跳过, > 1h 刷) — 推荐日常用
bash tools/with_venv.sh python -m tools.ensure_fresh --watchlist
```

### 5. AgentData 内部能力

```python
data = AgentData("002028")

# 读分析结果 (通过 AnalysisData)
from tools.analysis.analysis_data import AnalysisData
analysis = AnalysisData.from_raw(data.raw()).analysis or {}
analysis.get("wyckoff", {}).get("stage")  # 'Accumulation'
analysis.get("resonance", {}).get("1d", {}).get("direction")  # '🟢四向↑'
data.get("current_price")  # 165.66
data.get("kline")  # 250 根 K 线 list

# 完整 dump dict
raw = data.raw()

# 强制重拉
data.refresh()

# 渲染
md = data.render()  # 默认 docs/analyze-002028-思源电气.md
md = data.render(output_path="custom/path.md")

# 元信息 (是否重拉、age)
print(data.meta)  # {'code': '002028', 'max_age_min': 60, 'fresh': False, 'age_min': 0.3}
print(data)  # <AgentData 002028 CACHED, age=0.3min>
```

## 数据流 (C 方案 v2.0)

```
AgentData(code, max_age_min=60)
   │
   ├─ dump 存在 + age ≤ max_age_min + 关键字段齐 → 直接读 (0 API, 0.003s)
   │     └─ dump 关键字段缺失 (老 dump) → 强制重拉一次
   │
   └─ 否则调 dump_data.dump_code() 拉 + 算 5 因子 + 写 dump (11s/只)
         └─ 自动 _meta.pulled_at 字段 (供下次 age 判断)
                ↓
       data/dump/{code}.json
                ↓
       读分析结果 (AnalysisData.from_raw(dump).analysis)
                ↓
       渲染 docs/analyze-{code}-{name}.md
                ↓
   数据源: Tushare (daily/weekly/financial/forecast) + Sina 60m + get_index_daily
```

## 性能 (v5.10.3 + C 方案实测)

| 模式 | 耗时 | API 调用 |
|---|---|---|
| CACHED (dump 新鲜) | **0.003s/只** | 0 |
| FRESH (强制重拉) | 11s/只 | 7-9 次 Tushare + 1 次 Sina 60m |
| 17 baseline 串行 | **197s = 3.3 分钟** | 17 × 9 = 153 次 |
| 17 docs re-render (--analyze-only) | 1.5 分钟 | 0 |

## 其他 skill 必须先调 t-pull

```python
# t-analyze / t-trigger / t-monitor / t-sector 等其他 skill 使用前:
# 1. from tools.batch.agent_data import AgentData
# 2. data = AgentData(code, max_age_min=60)  # 默认 1h 免拉
# 3. from tools.analysis.analysis_data import AnalysisData
#    analysis = AnalysisData.from_raw(data.raw()).analysis or {}
#    analysis.get("wyckoff", {}).get("stage")   # 读威科夫阶段
#    analysis.get("resonance", {})              # 读共振
#    analysis.get("chan", {})                   # 读缠论
#    analysis.get("scene")                      # 读场景
# 4. data.render()  # 渲染报告
```

## 实际拉数据源 (v5.10.3 dump_data 内部)

| 数据 | 源 | 备注 |
|---|---|---|
| 日 K 线 (250 根) | Tushare.daily | 2000 积分档可用 |
| 周 K 线 (60 根) | Tushare.weekly | 单接口频控, 复用 fetch_all |
| 60m K 线 (400 根) | Sina.money.finance | v5.10.3 timeout 5s |
| 财务指标 (EPS/ROE) | Tushare.fina_indicator | 2000 积分档 |
| 历史财务 (5 年) | Tushare.income | 2000 积分档 |
| 业绩预告 | Tushare.forecast | 2000 积分档 |
| 资金流 (fflow 60 天) | Tushare.moneyflow | 单接口 4s/次 |
| 指数 K 线 (3 指数 60 根) | Tushare.index_daily | v5.10.2 修复 (之前 get_daily 拉空) |
| 实时价 | Tushare.daily_basic | 通过 data_source 统一入口 |

**唯一例外**: `tools/fetch/data_source.py.fetch_realtime` 拉当前价 (Tushare.daily_basic, OK)
**禁止**: ifzq / qtimg / push2his / datacenter / 10jqka 直接 curl

## 检验

```bash
# 1. CACHED 路径快 (0.003s)
bash tools/with_venv.sh python -c "
from tools.batch.agent_data import AgentData
from tools.analysis.analysis_data import AnalysisData
import time
start = time.time()
data = AgentData('002028', max_age_min=999999)
analysis = AnalysisData.from_raw(data.raw()).analysis or {}
print(f'CACHED 耗时: {time.time()-start:.3f}s')
print(f'  威科夫: {analysis.get(\"wyckoff\", {}).get(\"stage\")}')
"

# 2. FRESH 路径重拉 (11s)
bash tools/with_venv.sh python -c "
from tools.batch.agent_data import AgentData
from tools.analysis.analysis_data import AnalysisData
import time
start = time.time()
data = AgentData('002028', force=True)
analysis = AnalysisData.from_raw(data.raw()).analysis or {}
print(f'FRESH 耗时: {time.time()-start:.1f}s')
print(f'  威科夫: {analysis.get(\"wyckoff\", {}).get(\"stage\")}')
"

# 3. 17 baseline 跑通
bash tools/with_venv.sh python -m tools.regression_test test
# 应该 17/17 PASS, 性能 ±20%
```

## 7-29 修过的拉数据 bug

1. **t-trigger v3.4** — 删 18 处 curl, 改读 dump 字段
2. **tushare_fetcher.py** — 加 `get_index_daily` 拉指数 K 线
3. **fetch_financial.py** — 7-30 物理删除 (没有任何 .py import, dump_data 已有 eps_table / current_price / market_cap_yi 完整字段, 重复造轮子)
4. **dump_data.py** — `resonance_3period` 1 次拉 5 段指数 K 线 + 算 3 周期
5. **data_source.py** — 已统一入口, 所有 skill 走 `data_source.fetch_*` 函数
6. **agent_data.py v1.0** — C 方案落地, 1 个类替代 dump_data + data_source 拉数据逻辑

## 历史信号计算 (factor_history)

如需计算某只票的**历史信号序列** (逐日扫描每天的威科夫/缠论/场景状态), 使用 `factor_history`:

```python
from tools.batch.agent_data import AgentData
from tools.analysis.analysis_data import AnalysisData
from tools.analysis.factor_history import compute_factor_history

data = AgentData("002028")
ctx = data.raw()  # 完整 dump dict
history = compute_factor_history(ctx)
# history: list of {date, wyckoff_stage, scene, chan_daily_beichi, ...}
# 用途: 回测信号胜率 / 扫历史顶底
```

**与单点分析的区别**:
- `AnalysisData.from_raw(dump).analysis` → **当前时刻**一次性分析结果
- `factor_history.compute_factor_history(ctx)` → **逐日历史**信号序列 (供回测)
