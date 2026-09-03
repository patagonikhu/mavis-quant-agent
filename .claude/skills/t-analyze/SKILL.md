---
name: t-analyze
description: 股票分析 + 批量扫描。单只：/t-analyze <code>；全量 watchlist：/t-analyze --all；板块：/t-analyze --sector AI；指定多只：/t-analyze 300308 600089。任何时候用户说"分析XX股票"、"XX怎么看"、"XX能买吗"、"批量分析"、"全部扫一遍"、"AI板块怎么样"都走这个 skill。替代原 t-watchlist / t-batch / t-sector。
user-invocable: true
allowed-tools:

> 🚨 **禁用 timeout 铁律 (2026-08-28 固化)**
> 任何长跑命令（>30s）必须 `run_in_background=true` + 任务完成自动通知 chat，**禁止 timeout=120/300 截断**。
> 测试只需 1 只股票验证脚本正确。

> 🚨 **复用 Analysis 入口铁律 (2026-08-21 v3.5)**
> 任何 backtest / 信号回测 / 胜率统计 必须复用 `AnalysisEngine.analyze_history(ctx, dates)` 入口。
> ❌ 禁止直接调 `detect_upthrust` / `find_beichi_signals` 等单层函数（已删）
> ❌ 禁止绕过 `Engine.analyze_history()` 入口自己写算法
> ❌ 禁止用 `c_d` (close) 当 `hist` 参数调背驰函数（必须用 `_calc_macd_hist(c_d)`）

> 🚨 **拉数据铁律 (2026-09-03 v6.2.3)**
> 跑这个 skill 前，先调 `/t-sync-data` 走 `tools/storage/sync.py` 拉数据（也可不调，前提是 parquet 已有数据）。
> 默认 `python -m tools.storage.sync` 走 `--auto` 智能检测 stale, 只跑需跑的; 强制全跑用 `python -m tools.storage.sync --all-data`。

> 📂 **数据层依赖 (v6.2.2 架构守门)**
> 读数据走 `DataStore` (`tools.storage.store.DataStore`) 或 `caches/analysis` 公开接口, **不直连 db/网络**。
> 完整 storage/ 目录结构见 `t-sync-data/SKILL.md`。

## 入口判断

```
/t-analyze 688017                → 单只
/t-analyze 688017 绿的谐波       → 单只（含名称）
/t-analyze --all                 → 全量（watchlist 全部 71 只，含 4 指数）
/t-analyze --sector AI           → 板块
/t-analyze 300308 600089         → 多只
```

---

## 批量模式（--all / --sector / 多 code）

### Step 1: 确定股票列表

```bash
bash tools/with_venv.sh python3 -c "
import json
print([s['code'] for s in json.load(open('data/watchlist.json'))['stocks']])
"
```

### Step 2: 数据预热（可选）

数据已存在则跳过。新增股票需先跑：
```bash
# 2026-08-31 起 t_analyze_all.py 自动 sync + 并发
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python3 tools/batch/t_analyze_all.py
```

### Step 3: 信号扫描 + MD 渲染（一次性）

> 单次 `AnalysisEngine.analyze_history(120天)` 同时输出信号表和个股 MD。
> **正式脚本**: `tools/batch/t_analyze_all.py`（含 verbose 进度 + 异常立即抛）

```bash
# 后台跑（>30s 必须 background，不用 timeout）
bash tools/with_venv.sh python3 tools/batch/t_analyze_all.py
```

**脚本行为**：
- 71 只全处理（含 000001/000300/399001/399006 指数）
- 串行逐只，verbose 输出：
  - `[1/67] 000725 京东方A (持仓) START`
  - `  [analysis] 6 strategies: chan/wyckoff/smc/obv/fflow/peg`
  - `  [render] building MD...`
  - `  [section] 000725: ma / technical / factor·history / ...` (34 sections)
  - `  [done] 000725 -> analyze-000725-京东方A.md (2.6s, 31,209chars)`
- 异常立即抛（不吞）：`traceback.print_exc()` 输出
- 产物：`docs/{portfolio,watchlist}/analyze-{code}-{name}.md` + `docs/signal-watchlist.md`

### Step 4: chat 输出

```
📄 FILE: docs/signal-watchlist.md
SUMMARY: 共 N 只, M 只有今日信号
SIG_CODES: 000001, 002475, ...
```

### Step 5: 深挖（可选）

`/t-analyze {code}` 单只模式 → 见下

---

## 单只模式（/t-analyze {code}）

```
60xxxx / 688xxx → 上交所
00xxxx / 30xxxx / 002xxx / 003xxx → 深交所
```

```bash
# Step A: 拉数据（可选，parquet 已有则跳过）
bash tools/with_venv.sh python -m tools.storage.sync --codes {code}

# Step B: 分析 + 渲染 MD
bash tools/with_venv.sh python3 << 'PYEOF'
import sys, json
from pathlib import Path
sys.path.insert(0, '.')
from tools.storage.store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.render_data import RenderData
from tools.analysis.analysis_result_signals import diff_rows, extract_signals
from tools.render.report_renderer import render_report

code = '{code}'
ctx = DataStore.get_ctx(code)
if not ctx.kline: print('ERROR: 无K线数据'); exit(1)

# L2+L3 合并: AnalysisEngine.analyze_history 一次遍历
all_dates = [k['trade_date'].replace('-','')[:8] for k in ctx.kline]
dates = all_dates[-120:]
history = AnalysisEngine().analyze_history(ctx, dates)
result = history[dates[-1]]
rows = list(history.values())

# 按 list_type 分流
list_type = next((s.get('list_type','自选') for s in json.load(open('data/watchlist.json'))['stocks'] if s['code']==code), '自选')
subdir = 'portfolio' if list_type == '持仓' else 'watchlist'

# L4: 渲染
data = RenderData.from_result(ctx, result)
data.factor_history_rows = rows
md = render_report(data)
out_path = Path(f'docs/{subdir}/analyze-{code}-{ctx.name}.md')
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(md, encoding='utf-8')
print(f'REPORT: {out_path}')

# 今日信号
if len(rows) >= 2:
    r = rows[-1]
    changes = diff_rows(rows[-2], rows[-1])
    print(f"威科夫: {r.get('wyckoff_daily','?')}  MA偏: 日{r.get('ma_dev_daily') or 0:+.1f}%")
    for sig_type, detail, direction in extract_signals(changes):
        print(f"{'⬆️买' if direction=='buy' else '⬇️卖'} | {sig_type} | {detail}")
    if not extract_signals(changes): print('无新信号')
PYEOF
```

**AnalysisResult 字段**: `code, name, current_price, raw, signals_active, action`
(2026-08-29 删: scene, scene_name, resonance_count — 硬编码 if-else 不准)
- `action`: ⬜/🥇/🥈/⚠️/❌

> **🚨 读 MD 而非手算**：MD 报告 `## 📊 技术指标 (8 种)` 已含 MA/EMA/ADX/RSI/BB/OBV/ATR/量比。**禁止重复手算**。

---

## 投资四问

| # | 问 | 答案 |
|---|---|---|
| 1 | 卡点 ⭐⭐⭐⭐⭐ (1-5) | 不可替代环节？ |
| 2 | TAM | 5年总市场增长够大？ |
| 3 | 龙头评分 0-14（≥11 才是） | 市占/技术/客户/产能 4 维 |
| 4 | 估值 | DCF L + PEG 双指标 |

**综合**: ①②③④ 4 个全 ✅ = 🥇 重仓；任一 ❌ = ❌ 不买

---

## T 框架

```
T = (event_date - today) / 30
T-3 埋伏, T+0 加仓, T+6 跑路
```
events.json 找 `code` 匹配的事件；找不到就说"未识别到 T 点"

---

## PEG 四件套（v4 强制）

| 口径 | 公式 | 用法 |
|---|---|---|
| **PEG_A** (本财年, 后视镜) | FwdPE / g_NTM | ⚠️ 易失真，不参与决策 |
| **PEG_C** (下一财年, 前视镜) | FwdPE / g_next | 🥈 辅助 |
| **PEG_真实** (稳态 CAGR) ✅ | FwdPE / CAGR_3yr | **决策依据** |
| **PEG_表观** (NTM YoY) | FwdPE / g_NTM | ⚠️ 与 PEG_A 相同 |

**复苏扭曲识别** (前年亏损/ROE<0 时 NTM 增速虚高，必须用 CAGR)：
- 任意历史年 EPS<0 或 ROE<0 → 用 PEG_真实
- NTM 净利率较历史均值跳跃 >3x → NTM 不可信

**PEG 决策**：
| PEG | 行动 |
|---|---|
| <1.0 | Lynch 买入区 |
| 1.0-1.5 | 合理 |
| 1.5-2.0 | 偏贵，降一档 |
| >2.0 | 高估，降两档 |

---

## DCF L

**三档折现率 r=8/10/12%**，校正值 = r=10% × 0.7

```
L/E3 < 2    → 叙事未满
L/E3 2-5   → 较高预期
L/E3 > 5   → 叙事已满，警惕
L/可达利润 < 0.8  → 叙事低估 ✅
L/可达利润 1-2    → 合理
L/可达利润 > 2    → 叙事透支 ❌
```

**DCF 假设 (板块 hardcode)** — 来自 `tools/factors/valuation/dcf_engine.py`：
| 板块 | WACC | FCF | g |
|---|---|---|---|
| 半导体设备 | 0.110 | 0.80 | 0.030 |
| AI 芯片 | 0.130 | 1.30 | 0.050 |
| 消费电子代工 | 0.100 | 0.88 | 0.030 |
| AI 服务器 | 0.110 | 0.90 | 0.040 |
| 光学 | 0.110 | 0.90 | 0.035 |
| 机器人 | 0.130 | 1.00 | 0.040 |
| 默认 | 0.100 | 0.85 | 0.030 |

---

## MA 均线（v5 框架）

| 状态 | 判定 | 行动 |
|---|---|---|
| ✅ 多头 | MA5>MA20>MA60>MA120 | 趋势健康 |
| ❌ 空头 | 反过来 | 不接飞刀 |
| ⚠️ 拉高出货 | MA60>MA120 但 P<MA5 | 降级 (立讯案例) |
| ✅ 健康调整 | P>MA120 但 P<MA60, MA120 上行 | 加仓窗口 |

**偏离阈值**：
```
P/MA5 > 5%    短期超买
P/MA20 > 10%  短期严重超买
P/MA60 > 20%  中期顶部
P/MA120 > 50% 长期透支
```

---

## 主力资金（OBV 段背离）

**段背离阈值** (60日 4 个 15日 窗口):
- 底背离: 价 pct<-2% + OBV 净增 >+3%
- 顶背离: 价 pct>+2% + OBV 净增 <-3%
- ≥2 窗口 = 强背离 (±2 分), =1 = 单次 (±1)

**OBV 退出信号板块适用性**:
- ✅ 光学/封测/HBM: 主力控盘度高，OBV 准
- ❌ 题材/小盘: 主力分散，噪声大
- ❌ 周期股: 行业 β 主导，OBV 被淹没

---

## 缠论输出（已集成在 report_renderer）

> **render_report 已自动渲染缠论 section**：`## 🚨 60 分钟级背驰信号`、`## 📐 缠论完整数据 (4 个级别)`。
> 不要再手写 `format_chan_output` / `format_three_hubs`（不存在，2026-08-28 删）。

**数据来源**: `result.raw['chan']['levels']` 由 `AnalysisEngine.analyze_history()` 计算
**渲染函数**: `tools/render/report_renderer.py::_section_chan_full`
**4 个级别**: 周 / 日 / 60分 / 30分
**读取 MD** (推荐): `Read docs/portfolio/analyze-{code}-{name}.md` → 找 `## 📐 缠论` section

**板块适用性**:
- CPO / 光学 / 半导体封测: 背驰有效
- 半导体设备 / AI芯片 / AI服务器: 背驰失效 → 改用板块 MA20 偏离>30%

---

## 市场状态（v3.5 scene 替代旧 v7）

**5 维三指标打分 (0-9)**：
- MACD 面积 (0-3): 最近 3 段 up-seg 红柱面积
- MA20 斜率 (0-3): 近 20/40 斜率
- 60日涨幅 (0-3): 强度

| 总分 | 状态 | 主用方法 |
|---|---|---|
| ≥7 | 主升浪 🚀 | 威科夫Markup + MA偏离 + 量价 |
| 4-6 | 过渡回调 🔄 | 缠论背驰 + 中枢 + 量价 |
| 0-3 | 震荡下跌 ⬇️ | 缠论中枢 + SMC-OB |

**v3.6 AnalysisResult 字段** (2026-08-29 简化):
- `result.code`, `result.name`, `result.current_price`
- `result.raw` (各 strategy 结果 dict: wyckoff/chan/smc/obv/fflow/peg)
- `result.signals_active` (所有命中的信号列表)
- `result.action` (⬜/🥇/🥈/⚠️/❌)

---

## 5 方法 × 3 周期 综合矩阵

5 重保险：模板/补全/linter/skill/CLAUDE.md 全部要求。
**必须输出**:
```
**场景**: C (震荡观望)            ← A-E 之一
**共振数**: 5 重                    ← 数字 + "重"
**行动**: ⬜ 震荡观望               ← 🥇/🥈/🥉/🟢/🟡/⬜/❌ 之一
```

**9 退场信号**:
- fflow 5日 >30亿 净流出 (🔴 清)
- OBV 强顶背离 (限光学/封测/HBM)
- MACD 高位死叉 (限半导体设备/HBM)
- 5方法总分 ≤-3
- PEG >3.0 / L/E3>8 / L/可达>2.5

---

## 数据来源标识 (报告内强制)

| 图例 | 含义 |
|---|---|
| 🟢 | 实数据 (DataStore parquet) |
| 🟡 | 硬编码 (STOCK_REGISTRY) |
| ⚪ | 派生 (从实数据公式算出) |

EPS、ROE、6 关评估必标 🟢/🟡/⚪。

---

## 报告输出 (单只)

**60 行内**，必含：
1. 头部（代码/名称/日期/板块/卡点/TAM/龙头/PEG/DCF L）
2. T 框架（事件/T位置/阶段/操作）
3. 投资四问（一行一个 ✅/❌/⚠️）
4. 估值双检查（DCF L 三档 + PEG）
5. 5方法×3周期 矩阵（场景/共振/行动）
6. 主力资金 + 三层仓位（底/中/波动）+ 止损阶梯
7. 监控指标 + 风险
8. 🔍 Web 新事件（y/N 询问加 events.json）
9. 💡 我注意到

文件命名：`docs/{portfolio,watchlist}/analyze-{code}-{name}.md`，按 list_type 分流，每次 v{N+1} 只保留最近 2 版。

---

## 工作纪律

1. **估值必须用真实数据** — DCF L 用代码算，禁止 LLM 估算
2. **框架优先** — 先套四问 + T 再给建议
3. **T 位置必须算** — events.json 找不到就说"未识别"
4. **报告 60 行内** — 信息密度优先
5. **复苏扭曲必须识别** — 用 CAGR
6. **永远输出退出信号** — 强制 9 退场信号检查
7. **强制规则优先于情绪** — 触发即机械执行
8. **任何分析必带主力 + 买点 + 卖点**

---

## 数据文件

| 文件 | 用途 |
|---|---|
| `CLAUDE.md` | 人设 + 框架铁律 |
| `docs/analysis-framework.md` | 投资四问 + T 框架 + PEG + DCF |
| `data/events.json` | T 点事件库 |
| `data/watchlist.json` | 关注清单 + 笔记 |
| `data/sectors.json` | 板块 → ETF 映射 |
| `tools/batch/t_analyze_all.py` | 全量 verbose 脚本（正式入口）|
| `tools/render/report_renderer.py` | 22 section 渲染器 |
| `tools/analysis/analysis_engine.py` | AnalysisEngine 入口 |
| `tools/analysis/render_data.py` | RenderData dataclass + 9 派生字段 |
