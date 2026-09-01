# A股量化智能投顾 Agent (LLM-driven)

> 5 个 Claude Code Skill + 单次 O(n) 因子引擎 = 你在终端的投资分析师。
> 数据走本地 parquet (Tushare 同步) + LLM 训练知识 + 你手维护的 watchlist。

---

## 1. 5 个 Slash 命令

所有 skill 都在 `.claude/skills/` 下, 直接在终端敲命令触发。

| 命令 | 用途 | 示例 |
|---|---|---|
| `/t-analyze <code> [name]` | 单只详报 (22 section: 投资四问 + T 框架 + PEG + DCF + 缠论 4 级别) | `/t-analyze 688017 绿的谐波` |
| `/t-analyze --all` | 批量扫 watchlist 全部 (71 只, 含 4 指数), 写 `docs/{portfolio,watchlist}/analyze-*.md` + `docs/signal-watchlist.md` | `/t-analyze --all` |
| `/t-backtest <signal>` | 信号回测 — 5年历史扫描 + 30日最大涨幅命中率 (走 signal_cache 缓存) | `/t-backtest --signal Spring --threshold 10` |
| `/t-sync-cache` | 增量补全科技股 signal_cache.db (10分钟断点续跑) | `/t-sync-cache --portfolio` |
| `/t-near-low` | 监控"跌 70-80% + 距 5y 低 < 3%"清单 | `/t-near-low --gap 2` |
| `/t-bb-obv` | 科技股扫 BOLL<15% + BBW<10% + OBV 底背离 三重确认 (每天 0-2 只) | `/t-bb-obv --window 5` |

### 1.1 典型用法

```bash
# 单只分析 (60 行 22 section 详报)
/t-analyze 600089 特变电工
/t-analyze 688017 绿的谐波    # PEG 冲突案例

# 批量（后台跑 71 只约 90s）
/t-analyze --all              # 全 watchlist + 写 docs/{portfolio,watchlist}/analyze-*.md

# 信号回测（走 signal_cache 缓存，命中行 0.5s 出结果）
/t-backtest --signal Spring --days 30 --threshold 10 --portfolio
/t-backtest --signal Accumulation --signal fflow:强进货    # 组合信号 (AND)

# 缓存预热（10 分钟断点续跑，多次跑逐步覆盖 5 年）
/t-sync-cache --portfolio      # 仅持仓
/t-sync-cache --codes 300274 002371

# 超跌清单（反弹策略，跟价值投资无关）
/t-near-low --gap 2

# 全市场 A→M 三重确认扫描
/t-bb-obv --window 10
```

### 1.2 推荐工作流

```
/t-bb-obv                  ← 1. 科技股扫 BOLL<15% + BBW<10% + OBV 底背离 (近 2 日, 0-2 只)
              ↓
/t-analyze {code}                ← 2. 对候选标的做完整 22 section 详报
              ↓
/t-backtest --signal Accumulation  ← 3. 验证信号历史胜率 (signal_cache 加速)
              ↓
/t-sync-cache                    ← 4. 持续预热缓存，让 /t-analyze --all 也能加速
```

### 1.3 输出格式

`/t-analyze` 报告关键字段（22 section）:

```
**PEG 四件套** (v4 强制):
  - PEG_真实 (稳态 CAGR): X.X ✅
  - PEG_A (本财年, 后视镜): X.X
  - PEG_C (下一财年, 前视镜): X.X
  - PEG_表观 (NTM YoY): X.X

**DCF 隐含 L** (r=8/10/12% 三档, 板块-aware 假设):
  - r=10%  L=X亿  L/E3=X.Xx  L/可达利润=X.Xx

**5 方法 × 3 周期 矩阵** (linter 必查):
  - 场景: A/B/C/D/E
  - 共振数: N 重
  - 行动: 🥇/🥈/🥉/🟢/🟡/⬜/❌

**综合:** 🥇 / 🥈 / 🥉 / ⚠️ / ❌
```

---

## 2. 分析框架概览

### 2.1 投资四问 + T框架（核心）

任何标的必答 4 个问题:
1. **卡点** — 产业链上的不可替代环节? (⭐ 1-5)
2. **TAM** — 5 年总市场够大? (倍数)
3. **龙头评分** — 真龙头? (0-14, ≥ 11)
4. **估值** — PEG < 1.5 且 L/可达利润 < 1.0 才是双侧便宜

T框架口诀：**T-3 埋伏, T+0 加仓, T+6 跑路**

> 完整框架见 [`docs/analysis-framework.md`](docs/analysis-framework.md)

---

## 3. 项目结构

```
.
├── CLAUDE.md                              # Agent 人设 + 决策框架 + 铁律
├── docs/
│   ├── analysis-framework.md              # 投资四问 + T框架 + PEG + DCF
│   ├── AGENT_MEMORY.md                    # 项目记忆 (活跃 skill 列表)
│   ├── portfolio/analyze-*.md            # 持仓分析报告 (按 list_type 分流)
│   ├── watchlist/analyze-*.md            # 自选分析报告
│   ├── backtest-*.md                     # 回测报告
│   └── signal-watchlist.md               # 全量扫描信号表
│
├── data/                                  # 静态数据 (你/Claude 维护)
│   ├── events.json                        # T 事件库
│   ├── watchlist.json                     # 关注股 + 笔记 (71 只, 19 持仓)
│   ├── sectors.json                       # 板块/ETF → 成分股
│   ├── history/
│   │   ├── daily/                         # 日 K 线 parquet (DataStore)
│   │   ├── weekly/                        # 周 K 线 parquet
│   │   └── stock_basic/                   # 股票基础信息
│   └── analysis_cache.db                  # signal_cache (24 列 SQLite 缓存)
│
├── tools/                                 # 核心引擎
│   ├── sync_stock.py                      # 单只拉数据 (DataStore)
│   ├── sync_watchlist_fresh.py            # 批量同步新鲜度
│   ├── batch/t_analyze_all.py             # 1 键刷 watchlist (sync + 4 worker analyze + render)
│   ├── kline_store.py                     # DataStore (parquet reader)
│   ├── with_venv.sh                       # venv 包装 (必须走这个)
│   │
│   ├── analysis/                          # 分析引擎 (单次 O(n) 遍历)
│   │   ├── analysis_engine.py             # AnalysisEngine.analyze_history()
│   │   ├── analysis_result_signals.py      # compute_factor_history() 批量信号
│   │   ├── signal_cache.py                # SQLite 缓存 (wyckoff 9 bool + chan 5 bool + hub)
│   │   └── render_data.py                 # RenderData (9 派生字段)
│   │
│   ├── factors/                           # 7 strategy 算子
│   │   ├── kline_arrays.py                # build_kline_features() O(n) 预算层
│   │   ├── wyckoff/stage_factor.py
│   │   ├── chan/czsc_signals.py
│   │   ├── smc/                           # OB/FVG/Sweep
│   │   ├── volume/price_fflow.py          # OBV + fflow
│   │   └── valuation/multi.py             # PEG + DCF
│   │
│   ├── render/                            # 报告渲染 (22 section)
│   │   └── report_renderer.py
│   │
│   └── batch/                             # 批量脚本
│       ├── t_analyze_all.py               # /t-analyze --all (verbose + 异常立即抛)
│       ├── batch_backtest.py              # /t-backtest (走 signal_cache)
│       ├── signal_cache_warmup.py         # /t-sync-cache (增量断点续跑)
│       ├── find_near_low.py               # /t-near-low
│       └── bb_obv_scan.py               # /t-bb-obv
│
└── .claude/skills/                        # 5 个 slash 命令
    ├── t-analyze/SKILL.md
    ├── t-backtest/SKILL.md
    ├── t-sync-cache/SKILL.md
    ├── t-near-low/SKILL.md
    └── t-bb-obv/SKILL.md
```

---

## 4. 因子历史计算架构（单次 O(n) 遍历，无 slice）

### 4.0 核心设计

**v3.6 重构（2026-08-28 commit `7fd8e5d`）**：
- ❌ **删除** `ctx.slice(as_of_date)` 切片逻辑 — Strategy 不再感知时间
- ❌ **删除** `Strategy.analyze(ctx)` 单点路径 — 改用 `analyze_history(ctx, dates)` 批量
- ✅ **统一**：`analyze_history(ctx, dates)` 全 strategy 实现，O(n) 一次遍历出全历史
- ✅ **共享**：`build_kline_features()` 预算层（MA/vol/slope），O(n) 一次，所有 strategy 复用

**入口**：
```python
from tools.analysis.analysis_engine import AnalysisEngine
history = AnalysisEngine().analyze_history(ctx, dates)  # dates: list[str YYYYMMDD]
# 返回: dict[date_str, AnalysisResult]
result = history[dates[-1]]  # 最新一个节点的完整结果
```

**架构图**：
```
kline + dates
  │
  ▼
build_kline_features()                       O(n) 一次（预算层，共享）
  → arrs {ma20, ma60, vol20, slope_60, obv, ...}
  │
  ▼
WyckoffStrategy.analyze_history(ctx, dates)  O(n) — 1次循环: 每根bar O(1) 索引
ChanStrategy.analyze_history(ctx, dates)     O(n) — czsc 内部已 O(n) 优化
SmcStrategy.analyze_history(ctx, dates)      O(n) — OB/FVG/Sweep 全量扫一次
ObvStrategy.analyze_history(ctx, dates)      O(n) — OBV 数组预建
FflowStrategy.analyze_history(ctx, dates)     O(n) — Tushare money_flow 预扫描
PegStrategy.analyze_history(ctx, dates)       O(1) — 查表
ResonanceStrategy.analyze_history(ctx, dates) O(n) — 1d/5d/20d 共振
  │
  ▼
AnalysisResult 合并 (signals_active + action) — 2026-08-29 删 scene/resonance_count 硬编码 if-else
```

### 4.1 修复链（O(n²) → O(n)）

| 阶段 | 优化内容 | 耗时/只 |
|------|---------|---------|
| 基线（v2.x） | `for date: ctx.slice; strategy.analyze; scan_sub_events 每节点重算 MA/vol` | ~63s |
| +1 | `scan_sub_events` 预扫一次，per-date filter | ~6.6s |
| +2 | `kline_arrays.precompute()` 共享预算层 | ~3.6s |
| +3 | `WyckoffStrategy.analyze_history`: `wyckoff_judge(i, arrs)` O(1) per-bar | ~0.4s |
| +4 | `SmcStrategy.analyze_history`: OB/FVG/Sweep 全量扫一次 | ~0.4s |
| +5 | `ObvStrategy.analyze_history`: OBV 数组预建 O(n) | ~0.4s |
| +6 | `analysis_result_signals.py` 内 3 处 O(n) 查找改预建 dict/bisect | ~0.4s |

**总提升 ~150x**（63s → 0.4s，lookback=120 step=1）。

### 4.2 关键文件

| 文件 | 职责 |
|------|------|
| `tools/factors/kline_arrays.py::build_kline_features` | O(n) 预算层：MA/vol/slope/rolling min/max，返回 `arrs[key][i]` |
| `tools/analysis/analysis_engine.py::AnalysisEngine.analyze_history` | 7 strategy 并行调用，合并 scene/resonance/action |
| `tools/analysis/analysis_engine.py::WyckoffStrategy.analyze_history` | 用 `arrs` 预算层 + pre_scan sub_events，循环内 O(1) |
| `tools/analysis/analysis_engine.py::ChanStrategy.analyze_history` | czsc 批量，内部已 O(n) |
| `tools/analysis/analysis_engine.py::SmcStrategy.analyze_history` | OB/FVG/Sweep 全量跑一次，per-date 按 idx 过滤 |
| `tools/analysis/analysis_engine.py::ObvStrategy.analyze_history` | OBV 数组 + MA 数组 O(n) 预建 |
| `tools/analysis/analysis_result_signals.py` | 外层用 `date_to_ki` dict + `bisect` 替代 O(n) 查找 |

### 4.3 并发铁律

```
1. sync_incremental()           # 单线程，先跑，补齐本地 parquet
2. DataStore.list_codes()       # 获取全量代码
3. ThreadPoolExecutor(N)        # 再开多线程，每个线程只读 DataStore（0 网络）
```

`tools/batch/signal_cache_warmup.py` 4 worker 并发跑全市场 5783 只（lookback=1250 step=1）估计 ~50min；禁止在 worker 线程里调 sync 或任何网络请求。



### 4.1 watchlist.json（1 周改 1 次，30 秒）

```json
{
  "stocks": [
    {
      "code": "600089",
      "name": "特变电工",
      "sector": "电力变压器",
      "rating": "重仓",
      "notes": "PEG ~0.7, 龙头11分, 变压器物理瓶颈最硬, AI数据中心用电唯一解"
    }
  ]
}
```

### 4.2 events.json（滚动更新，LLM 主动建议）

```json
{
  "events": [
    {
      "code": "688017",
      "name": "绿的谐波",
      "event_type": "量产",
      "event_date": "2026-07-15",
      "description": "特斯拉 Optimus V3 量产 + 国产人形机器人万台交付",
      "impact": "正",
      "confidence": 0.9
    }
  ]
}
```

T+0 之后信号自动折扣，T+12 之后不再作为买入参考。

### 4.3 sectors.json（LLM 首次填充，手动维护）

`/t-bottleneck` 发现的 Layer 2/3 公司会自动建议补充到对应板块。

---

## 5. 设计原则

1. **数据本地化优先** — 所有 K 线/EPS 走 parquet (`DataStore` 读)，避免运行时网络调用
2. **三层分离** — `tools/sync_stock.py` (数据) → `tools/analysis/` (引擎) → `tools/render/` (报告)
3. **O(n) 单次遍历** — 7 strategy 共享 `build_kline_features()` 预算层，单次遍历出全历史（无需 slice / 切片）
4. **缓存优先** — `signal_cache` SQLite 让回测/分析秒级返回；`t-sync-cache` 增量预热
5. **异常立即抛** — 禁止 `except Exception` 吞错；渲染失败立即 raise 停下整批

---

## 6. 快速开始

```bash
# 1. 拉代码
git clone <repo> && cd mavis-quant-agent

# 2. 建 .venv (自动 uv sync)
bash tools/with_venv.sh python3 -c "import tushare, pandas; print('OK')"

# 3. 配 .env
cp .env.example .env
# 编辑 .env, 填 TUSHARE_TOKEN (https://tushare.pro 注册)

# 4. 拉数据 (单只 / 批量)
bash tools/with_venv.sh python -m tools.sync_stock 600089
bash tools/with_venv.sh python tools/batch/t_analyze_all.py  # 全部 watchlist

# 5. 跑 skill
/t-analyze 600089 特变电工
/t-analyze --all
```

**所有 Python 命令必须走 `bash tools/with_venv.sh` 包装** (自动激活 .venv, 避免污染系统 Python + 错版本)

> 📌 详见 CLAUDE.md "🐍 Python 环境固化" 段 — `.venv` + `uv` 永久固化, 任何机器一气呵成

**数据源**：Tushare 2000 积分档（日线 + EPS + 资金流 + 业绩预告），写入 `data/history/` parquet。

---

## 7. 开发

```bash
pytest tests/ -v
ruff check tools/ tests/
black tools/ tests/
```

---

## 8. 技术分析框架 — 中枢 + 背驰 + 多级别

> 基于缠论核心概念量化实现，用北方华创(002371)真实数据验证。

### 8.1 三个核心概念

```
MACD柱子  ≈  加速度（价格变化的速度在加快还是减慢）
背驰      =  两段走势的MACD面积对比（∫MACD dt ≈ ΔDIF）
中枢      =  三段走势重叠的价格区间（支撑/压力的物理来源）
```

**运动员跑步类比：**

```
MACD  =  裁判秒表每秒记录的加速度数值
背驰  =  裁判赛后翻记录本：
         "第一段他加速了6，第二段只加速了2，
          虽然速度还在创纪录，但他快跑不动了"
中枢  =  运动员在某个速度区间反复徘徊的区域
```

---

### 8.2 背驰公式

```python
def calc_area(hist, start, end):
    # 面积 = 正柱求和（只统计加速的部分）
    return sum(h for h in hist[start:end+1] if h > 0)

def beichi(hist, closes, t0, p1, t1, p2):
    area1 = calc_area(hist, t0, p1)   # 段1：谷0→峰1
    area2 = calc_area(hist, t1, p2)   # 段2：谷1→峰2
    ratio = area2 / area1
    new_hi = closes[p2] > closes[p1]
    # 背驰 = 价格创新高 但 动力不到上段50%
    return new_hi and ratio < 0.5
```

**CPO板块实证（中际旭创 3-5月）：**

```
上涨段1 (03-31→04-17)：面积=267  价格+49%  ← 主升浪核心，不该卖
上涨段2 (04-28→05-14)：面积=72   价格+31%  面积比27% ⚠️顶背驰
上涨段3 (05-21→05-28)：面积=18   价格+21%  面积比25% ⚠️二次背驰

→ 背驰后继续涨了一段（惯性），但力量已经耗竭
→ 正确操作：背驰触发→减波动仓1/3，底仓继续持
```

---

### 8.3 中枢：价格区间的物理意义

**中枢定义：三段走势重叠的价格区间**

```
三段走势：段1（上涨） / 段2（下跌） / 段3（上涨）

下沿 = max(三段各自最低价)   ← 三段都去过的最低位置
上沿 = min(三段各自最高价)   ← 三段都去过的最高位置
上沿 > 下沿 → ✅有效中枢
上沿 < 下沿 → ❌无重叠，不构成中枢
```

**北方华创日线中枢实例：**

```
段1 (426→668)：低=426  高=668
段2 (668→585)：低=585  高=668
段3 (585→935)：低=585  高=935

下沿 = max(426, 585, 585) = 585
上沿 = min(668, 668, 935) = 668
日线中枢 = [585, 668]  宽度=83
```

---

### 8.4 三个级别的中枢

| 级别 | 每段时长 | 中枢有效期 | 宽度 | 用途 |
|------|---------|-----------|------|------|
| **60分钟** | 数小时~1天 | 1-3天 | 窄（几十点） | 短线进出场位 |
| **日线** | 1-4周 | 1-3个月 | 中等（几十~百点） | 波段支撑压力 |
| **周线** | 1-3个月 | 3-12个月 | 宽（数百点） | 主升浪结构 |

**价格在中枢内/外的含义：**

```
在中枢内部（下沿~上沿之间）：
  → 震荡，等方向确认
  → 不加仓，不减仓

突破中枢上沿（强势离开）：
  → MACD面积扩张（无背驰）= 趋势继续
  → 目标 = 上沿 + 中枢宽度（等幅）
  → 上沿变支撑

跌破中枢下沿：
  → 60分钟中枢破 = 短线弱，关注日线
  → 日线中枢破  = 中线趋势改变，减仓
  → 周线中枢破  = 主升浪结束，底仓也走
```

---

### 8.5 北方华创完整案例（2024-2026）

**浪结构（周线）：**

```
起点 ¥216 (2024年)
│
第1浪: 216→298  +38%   试探，MACD面积小
第2浪: 298→303  回调极浅
第3浪: 303→523  +73%   最强，MACD面积177（最大）  ← 不该卖
第4浪: 523→426  -19%   回调
第5浪: 426→935  +120%  MACD面积287（>第3浪）→ 周线无背驰
                                              → 主升浪未结束
```

**关键价位与操作：**

```
¥1250  📐 5浪延伸目标(161.8%)
¥1073  📐 第一目标(127.2%)
¥ 935  ★ 5浪顶（暂定）
════════════════════════════
¥ 835  · 60分钟中枢上沿  短线压力
¥ 755  · 60分钟中枢下沿  短线支撑
════════════════════════════
¥ 668  ══ 日线中枢上沿   中线支撑
¥ 585  ══ 日线中枢下沿   中线止损线
════════════════════════════
¥ 426  ── 4浪底/周线支撑  长线止损线
```

**三层仓位框架：**

| 层级 | 仓位 | 买入 | 止损 | 减仓信号 |
|------|------|------|------|---------|
| 底仓 | 40% | 日线中枢上方随时建 | 周线中枢破¥426 | 周线背驰触发 |
| 中仓 | 30% | 60分钟底背驰+缩量长下影 | 日线中枢破¥585 | 日线背驰触发 |
| 波动仓 | 30% | 回调到位后加 | 60分钟中枢破 | MA20偏离>30%减 |

---

### 8.6 背驰分板块有效性（实证）

> 背驰**不是通用信号**，必须分板块使用

| 板块 | 背驰有效? | 原因 | 替代信号 |
|------|----------|------|---------|
| **CPO（主升浪晚期）** | ✅有效 | 面积逐段递减（267→72→18） | — |
| **半导体设备** | ❌失效 | 每段都在加速（面积85%-258%） | **单股MA20偏离>30%** |
| **AI芯片/服务器** | ❌失效 | 板块强势，每段更猛 | **板块MA20偏离>30%** |
| **主升浪早中期** | ❌失效 | 第1-4浪，面积在扩张 | MA20偏离 |

**背驰失效的三个原因：**
1. 市场狂热（新增量不断涌入，每段由新入场资金推动）
2. 基本面持续升级（每段有新催化剂，不是惯性消耗）
3. 主升浪早中期（第1-4浪，运动员还没累）

---

### 8.7 多级别操作流程

```
第一步（周线）：确认主升浪方向
  周线无背驰（面积扩张）→ 主升浪进行中，回调是买点
  周线顶背驰（面积<50%）→ 主升浪结束，底仓也减

第二步（日线）：判断当前子浪位置
  日线无背驰 + MA20上方 → 子浪上涨中，持有
  日线顶背驰 + 周线无背驰 → 子浪顶，减波动仓1/3

第三步（60分钟）：精确进场时机
  日线确认底部区域
  等60分钟底背驰触发 → 这一刻买入

优先级：周线 > 日线 > 60分钟
单独看任何一个级别都可能误判
```

---

## 9. 补充分析框架 — SMC + 量价 + 多市场共振 + 威科夫

> 缠论补充模块，解决缠论失效场景（主升浪加速/震荡市/跳空）。
> 全部基于腾讯K线OHLCV数据，无需新数据源，可审计复现。

### 9.0 统一数据源

所有补充分析的数据来自同一个API：

```
https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sh/sz}{code},day,,,250,qfq
```

每根K线7个字段：
```
[日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额]
 [0]   [1]   [2]   [3]   [4]   [5]     [6]
```

---

### 9.1 SMC（Smart Money Concept）— 解决震荡市无结构

**适用场景**：涨幅<20%、缠论无明显段结构时，识别机构下单区域

#### Order Block（OB）— 机构下单区域

```python
# 看涨OB：阴线被后续K线突破 → 回踩时是支撑
if closes[i] < opens[i]:                          # 阴线
    if any(highs[j] > highs[i] for j in range(i+1,i+10)):
        bullish_ob = (lows[i], highs[i])           # 支撑区间

# 看跌OB：阳线被后续K线跌破 → 反弹时是压力
if closes[i] > opens[i]:                          # 阳线
    if any(lows[j] < lows[i] for j in range(i+1,i+10)):
        bearish_ob = (lows[i], highs[i])           # 压力区间

# 数据：开盘[1] + 收盘[2] + 最高[3] + 最低[4]
```

#### FVG（Fair Value Gap）— 价格缺口，必须回填

```python
# 三根K线，第1根最高 < 第3根最低 → 上涨缺口
if lows[i+2] > highs[i]:
    fvg_zone = (highs[i], lows[i+2])              # 支撑区

# 数据：最高[3] + 最低[4]
```

#### BOS/CHoCH — 趋势延续/反转信号

```python
swing_highs = [i where highs[i] == max(highs[i-5:i+6])]

新高 (sh2 > sh1) → BOS↑ 趋势延续
高点降低 (sh2 < sh1) → CHoCH↓ 趋势可能反转  # 类似缠论的段转折

# 数据：最高[3] + 最低[4]
```

---

### 9.2 量价综合 — 区分真突破 vs 假突破

```python
vol_ratio = 今日成交量[5] / 近20日均量

# 放量突破 → 真信号
if highs[-1] > recent_high and vol_ratio > 1.5:
    signal = "✅ 真突破"

# 缩量突破 → 假信号
if highs[-1] > recent_high and vol_ratio < 0.7:
    signal = "⚠️ 假突破（量不配合）"

# OBV背离（主力出货/吸筹）
obv = cumsum(vol × sign(close.diff()))            # Granville 1963
if price_up and obv_down: signal = "🔴 拉高出货"
if price_down and obv_up:  signal = "🟢 主力吸筹"

# 数据：收盘[2] + 最高[3] + 最低[4] + 成交量[5]
```

---

### 9.3 多市场共振 — 过滤单股假信号

```python
# 个股K线（同上API）
# 大盘/板块K线（同API，换指数代码）
#   创业板: sz399006  科创50: sh000688  沪深300: sh000300

stock_trend  = (closes[-1] / closes[-6] - 1) * 100
market_trend = (market[-1] / market[-6] - 1) * 100

# 三向同多 → 信号最强
# 个股逆市 → 降仓

# 数据：收盘[2]（个股 + 大盘 + 板块指数）
```

---

### 9.4 威科夫 3 大阶段 + 9 子事件 — 主力在干嘛 (v4 对齐 WyckoffTradingAgent)

> 📊 **来源**: `tools/factors/wyckoff/stage_factor.py` (3 大阶段) + `tools/factors/wyckoff/detectors/` (9 子事件)
> 🎯 **核心目标**: 跟缠论互补 — 缠论看"空间结构 (中枢/段)",威科夫看"时间阶段 (主力走到建仓→派发的哪一步)"
> ⚠️ **v4 重大变更 (2026-07-25)**: 5 阶段 A/B/C/D/E → 3 大阶段 Accumulation/Markup/Distribution (跟 WyckoffTradingAgent 1:1 对齐)
> 📈 **单方法胜率**: 威 C 派发 (事前) **10d 真阳率 93%** (n=232), 20d 真阳率 98% (n=102) — 4 合 1 顶部预警里最强单信号

#### 9.4.1 3 大阶段 (跟老 5 阶段已废弃)

| 阶段 | 中文 | 含义 | 项目里判定 (AND 门) |
|---|---|---|---|
| **Accumulation** | 累积 | 主力在低位吸筹, 准备拉升 | base_low (现价 ≤ 年内 low ×1.45) + MA50/MA200 胶着 (gap ≤8%) + 量能萎缩 (20d/120d <75%) + 触发 Spring/LPS/EVR |
| **Markup** | 主升浪 | 主力拉升, 趋势确立 | MA50/200 金叉 + 持续在 MA200 上方 + MA gap >0.5% + MA50 角度 ≥2%/5日 + SOS 触发 |
| **Distribution** | 派发 | 主力高位卖给散户 | bias_200 >30% (强) 或 >15% (弱) + 3 日缩量 (<60d × 0.5) + UTAD/EVR 触发 |

**子阶段**:
- **Accum_A** 前置 / **Accum_B** ≥3 次测底 / **Accum_C** C 阶段不破低
- **UTAD** 派发后再次上探前期阻力 (强顶部信号, bias_200>15%)

#### 9.4.2 9 个 sub-event (实战中真正的"信号", 跟 WyckoffTradingAgent L4 1:1)

**Accumulation 末段 → Markup 起点 (3 个)**:

| 事件 | 中文 | 触发条件 (核心) | 实战意义 |
|---|---|---|---|
| **Spring** | 终极震仓 (假跌破) | 60 日支撑位被跌破 + 收盘收回 + 量能 ≥5d 均量 × 1.3 + 当日量 ≥前日 × 1.15 | 最强吸筹信号 (主力故意洗盘) |
| **LPS** | 最后支撑点 (回踩买点) | MA20 上升 + 缩量回踩 + 不破支撑 | 派发后反弹结束 / 跌势反弹结束 |
| **EVR** | 巨量无结果 (Effort vs Result) | 放量但价格不动, 典型"主力意图"信号 | Distribution 关键 / Accumulation 末段 |

**Markup 阶段 (4 个)**:

| 事件 | 中文 | 触发条件 (核心) | 实战意义 |
|---|---|---|---|
| **SOS** | 强势信号 (主升浪启动) | 单日 ≥6% + 量比 ≥3.0× + 60 日新高 OR MA50 交叉 | 主升浪起点, 强买点 |
| **Compression** | 压缩蓄势 (爆发前夜) | ATR 收窄到 20% 分位 + 缩量 | 突破前夜, 准备加仓 |
| **TrendPullback** | 趋势回踩 (入场机会) | 上升趋势回踩 5-20% + 企稳 + 缩量 | 中段入场点 (注意大市值阈值放宽) |
| **MarkupEntry** | 主升浪入场 | MA50/200 金叉 + 持续 + 量能确认 | 突破后第一次回踩入场 |

**Distribution 阶段 (2 个)**:

| 事件 | 中文 | 触发条件 (核心) | 实战意义 |
|---|---|---|---|
| **DistributionStart** | 派发起点 | bias_200>30% + 顶部放量 + 趋势转弱 | 派发开始, 准备减仓 |
| **UTAD** | 派发后上探 (强顶部) | bias_200>15% + 突破前期阻力 + 上影线 >35% + 量比 1.5× | 派发末期最强顶部信号, **10d 真阳率 93%** |

#### 9.4.3 数据要求 (K 线 + 字段)

| 周期 | 最小 K 线根数 | 字段 (除 OHLCV 外) | 用途 |
|---|---|---|---|
| 日线 | ≥30 (推荐 250) | open/close/high/low/vol + **pct_chg** | 主判定, 跟 WyckoffTradingAgent 1:1 |
| 周线 | ≥20 (推荐 250) | 同上 | 长线趋势, 周线中枢对齐 |
| 60 分 | ≥60 (推荐 200) | 同上 (60m 无 pct_chg 走 fallback) | 5 合 1 顶部预警 |

**OHLCV 之外还需要** (现状, 2026-07-29 v5.6):

| 字段 | 用在 | 当前 | 影响 |
|---|---|---|---|
| `pct_chg` | SOS 单日涨幅 | 日线/周线 ✅, 60 分 ✅ (本地算) | 100% |
| `market_cap_yi` | TrendPullback 大市值缩放 | ✅ 当日 + 250 天历史 | ✅ v5.6 修 |
| `turnover_rate` | EVR 流动性检查 (默认 0=skip) | ✅ 1 条 | 极小 |

**dump 字段** (v5.6 新加): `data/dump/{code}.json` 顶层:
- `kline` (日线 250 根) / `kline_60m` (60 分 200 根 + pct_chg) / `weekly` (周线 250 根)
- `daily_basic_long` (250 天 PE/PB/市值/换手率历史)
- `fflow` (60 日主力净额) / `eps_table` (4 期 EPS)

#### 9.4.4 实战读法 (报告里的输出示例)

```markdown
【威科夫详情】 (对齐 WyckoffTradingAgent 3 大阶段: Accumulation / Markup / Distribution)

| 项目 | 内容 |
|---|---|
| 当前阶段 | Markup (主升浪, 100%) |
| 阶段进度 | 80% |
| 操作建议 | 持有, 主升浪中 |

🔍 9 种 sub-event 触发情况 (2026-07-28 跟 WyckoffTradingAgent L4 对齐):
| # | 事件 | 中文 | 含义 | 所属阶段 | 触发 |
|---|---|---|---|---|---|
| 1 | Spring | 终极震仓 | 假跌破后快速收回 | Accumulation 末段 → Markup 起点 | ❌ |
| 4 | SOS | 强势信号 | 放量突破 + 单日≥6% | Markup 起点 | ✅ ⭐ |
| ... | ... | ... | ... | ... | ... |
```

**读法**:
- ✅ Spring 触发 = **主力吸筹完毕, 准备拉升, 可加仓**
- ✅ SOS 触发 = **主升浪启动, 强买入信号**
- ✅ UTAD 触发 = **派发末段, 强顶部信号, 减仓**
- ❌ 0/9 触发 = 数据不足 或 阶段不明, 观望

#### 9.4.5 与缠论的配合 (5 方法矩阵的核心)

| 场景 | 缠论信号 | 威科夫 | 联合判断 |
|---|---|---|---|
| 底部建仓 | 底背驰 | 累积 C / Spring | 双重确认, 强买点 |
| 主升浪启动 | 段面积扩张 | Markup + SOS | 加仓 |
| 主升浪顶部 | 背驰失效 | 派发 / UTAD | **MA20 偏离 >20% + UTAD = 必减** |
| 强势股回踩 | 60 分底背 | TrendPullback | 入场机会 |

**4 合 1 顶部预警** (回测 388 样本 10d 真阳率 92%):
> BC 背驰 + 中枢突破 + MA 偏离 + **威 C 派发** — 任一周期 ≥2/4 满足, 减仓 1/3
> 其中**威 C 派发单方法 10d 真阳率 93%**, 4 合 1 里最强单信号

---

### 9.5 与缠论的配合关系

| 场景 | 缠论信号 | 补充信号 | 联合判断 |
|------|---------|---------|---------|
| 震荡市 | 背驰面积比噪音 | SMC Order Block | 用OB替代背驰做支撑/压力 |
| 突破判断 | 段面积扩张 | 量价：放量突破 | 量价确认真突破 |
| 主升浪 | 背驰失效 | 威科夫阶段D/E | MA20偏离>20%替代 |
| 底部确认 | 底背驰触发 | 威科夫阶段C | 双重确认最强买点 |
| 信号过滤 | 单股背驰 | 多市场共振 | 三向同向才操作 |

---

## ⚠️ 免责声明

本项目所有分析仅基于 LLM 训练知识 + 用户笔记，不构成任何投资建议。
投资有风险，入市需谨慎。


---


---

## 附录: 数据源图例 (v3.6)

| 图例 | 含义 | 来源 |
|---|---|---|
| 🟢 | 实数据 (parquet) | Tushare 同步 → `data/history/` → DataStore |
| 🟡 | 硬编码 (LLM/STOCK_REGISTRY) | 卡点/leader/板块等元数据 |
| ⚪ | 计算派生 | PEG / DCF L / MA / 5 方法×3 周期 |

> 报告里每个数字都标来源, 用户一眼能看出真数据 vs 估算。


---

## 🌍 跨机器运行指南 (2026-07-27 兼容)

项目**已去除硬编码路径**, 在新机器上跑通 3 步:

### Step 1: 装 Python 3.11+ 和依赖

```bash
# macOS
brew install python@3.11 libomp git

# Linux (Ubuntu/Debian)
sudo apt install python3 python3-venv libgomp1 git

# Windows (PowerShell)
# 装 Python 3.11+ from python.org
```

### Step 2: 拉代码 + 装 Python 包

```bash
git clone <repo-url> mavis-quant-agent
cd mavis-quant-agent
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # 详见下
```

### Step 3: 配凭证 + 跑

```bash
手动创建 .env
# 编辑 .env, 填 TUSHARE_TOKEN (注册: https://tushare.pro/register)
python3 tools/batch/t_analyze_all.py    # 一键刷全 watchlist
```

### requirements.txt 依赖清单

```
lightgbm>=4.0
gplearn>=0.4
pandas>=2.0
numpy>=1.24
pyyaml>=6.0
requests>=2.31
```

### 跨机器兼容性清单

| 项 | 状态 | 说明 |
|---|---|---|
| 路径 (`/Users/I514959/...`) | ✅ 已修 | 改用 `Path(__file__).parent.parent` |
| LightGBM libomp | ✅ 自动 | macOS 自动检测 homebrew 路径, Linux 用系统 libgomp |
| Tushare token | ⚠️ 各人 | 手动创建 .env, 填自己的 token (注册: https://tushare.pro/register) |
| 网络 (东财/腾讯 API) | ⚠️ 看环境 | 公司网/家里网可能 WAF 拒接, 跟代理无关 |
| 数据 CWD | ✅ 已修 | 全部用相对路径 + `Path(__file__)` |

### 已知坑

1. **libomp 路径**: Apple Silicon 默认 `/opt/homebrew/opt/libomp/lib`, Intel Mac 是 `/usr/local/opt/libomp/lib`, `tools/lgb_ranker.py` 自动检测两个
2. **CWD**: 大部分脚本依赖 `cwd=项目根目录`, `cd mavis-quant-agent && python3 tools/xxx.py`
3. **Python 3.14**: 已验证, 但部分老库 (pandas/numpy) 装新版即可
4. **网络受限**: 东方财富 push2his 永久 WAF 拒接, 已有 push2delay 替代, 不影响

### 快速验证 (新机器跑通测试)

```bash
python3 tools/batch/regression_test.py test
# 期望: 17/17 ✅
```

---

## 信号胜率回测 (57只 × 3个月, 2026-08-04)

> **数据:** 57只 watchlist × lookback=60根K线 × 250根日线  
> **胜率定义:** 触发后 N 天收盘价下跌

### 最强顶部信号组合

**核心发现: 日线威科夫 Markup 阶段下, 威科夫+缠论信号同时触发是最可靠的减仓信号**

| 信号组合 | n | 5d跌率 | 5d均 | 10d跌率 | 10d均 | 评级 |
|---|---|---|---|---|---|---|
| **日=Markup 威科夫+缠论** | **77** | **65%** | **-1.8%** | **68%** | **-1.7%** | ⭐⭐ 最大样本双稳定 |
| 日=Markup 威科夫+SMC | 28 | 67% | -5.9% | 61% | -6.7% | ⭐ |
| 日=Markup 强信号≥2个(日线) | 21 | 71% | -4.7% | 75% | -2.1% | ⭐⭐ |
| 日=Markup 强信号≥2个(60m) | 27 | 70% | -3.3% | 62% | +0.7% | ⭐ |
| 日=Markup 仅SMC(卖侧扫) | 164 | 62% | -2.0% | 57% | -0.7% | ⭐ |
| SMC卖侧扫(weekly) | 55 | 64% | -2.3% | 62% | -1.6% | ⭐ |
| 缠论 1卖(daily) | 13 | 82% | -4.5% | 89% | -13.5% | ⭐⭐ 样本少 |

#### 威科夫+缠论同时触发的完整样本 (77条)

触发条件：
- **威科夫:** `🔴UTAD` 或 `🔴EVR` 出现在变化列
- **缠论:** `🆕🔴1卖` 或 `🆕🔴2卖` 或 `背驰🔴⚠️顶背驰` 出现在变化列
- **背景:** 日线威科夫阶段 = Markup

| 日期 | 代码 | 收盘 | 5d% | 10d% | 结果 | 威科夫 | 缠论 |
|---|---|---|---|---|---|---|---|
| 20260512 | 000970 | ¥12.5 | -4.3% | -3.9% | ✅ | EVR | 2卖(daily) |
| 20260512 | 002463 | ¥108.7 | -5.6% | +22.7% | ✅ | EVR | 1卖(60m) |
| 20260512 | 300476 | ¥375.4 | -10.2% | +4.7% | ✅ | EVR | 1卖(60m) 顶背驰(60m) |
| 20260512 | 688981 | ¥122.0 | -2.6% | +22.3% | ✅ | EVR | 1卖(60m) |
| 20260513 | 300285 | ¥42.6 | -0.9% | +14.9% | ✅ | EVR | 1卖(60m) |
| 20260514 | 688361 | ¥204.0 | +18.4% | +14.1% | ❌ | EVR | 2卖(daily) |
| 20260515 | 688120 | ¥267.5 | +3.3% | -2.3% | ❌ | UTAD | 1卖(60m) |
| 20260515 | 688146 | ¥145.6 | -10.2% | +20.0% | ✅ | UTAD | 1卖(60m) |
| 20260519 | 688082 | ¥196.8 | +30.6% | +14.4% | ❌ | EVR | 顶背驰(60m) |
| 20260520 | 002472 | ¥43.1 | -1.3% | -6.6% | ✅ | EVR | 2卖(daily) |
| 20260520 | 002747 | ¥26.1 | +8.9% | +11.3% | ❌ | EVR | 1卖(60m) |
| 20260521 | 002273 | ¥36.7 | +19.3% | +1.4% | ❌ | EVR | 2卖(60m) |
| 20260521 | 300990 | ¥94.0 | -5.1% | -12.8% | ✅ | EVR | 2卖(daily) 2卖(60m) |
| 20260525 | 002475 | ¥74.7 | -5.3% | -11.5% | ✅ | EVR | 1卖⭐(60m) 2卖(60m) |
| 20260525 | 601138 | ¥70.3 | +4.9% | +0.3% | ❌ | EVR | 2卖(60m) |
| 20260526 | 002049 | ¥83.0 | -8.1% | -11.2% | ✅ | EVR | 2卖(60m) |
| 20260526 | 688256 | ¥1411.0 | -7.9% | -10.0% | ✅ | EVR | 2卖(60m) |
| 20260528 | 688256 | ¥1391.5 | -2.3% | -12.4% | ✅ | EVR | 1卖(daily) |
| 20260529 | 002475 | ¥73.3 | -6.2% | -13.0% | ✅ | EVR | 1卖⭐(60m) 2卖(60m) |
| 20260529 | 688099 | ¥105.9 | -9.5% | -17.3% | ✅ | UTAD | 1卖(60m) |
| 20260529 | 688187 | ¥61.0 | -9.2% | -13.0% | ✅ | EVR | 2卖(60m) |
| 20260529 | 688361 | ¥217.9 | -8.2% | +3.9% | ✅ | EVR | 2卖(daily) |
| 20260603 | 688099 | ¥101.0 | -9.9% | -4.3% | ✅ | EVR | 1卖(60m) |
| 20260605 | 002273 | ¥36.2 | -10.6% | -1.9% | ✅ | UTAD+EVR | 2卖(60m) |
| 20260605 | 300476 | ¥338.9 | -3.4% | +7.8% | ✅ | UTAD | 2卖(daily) |
| 20260605 | 688019 | ¥273.0 | -17.6% | -5.8% | ✅ | UTAD+EVR | 顶背驰(weekly) |
| 20260609 | 002472 | ¥44.9 | -5.7% | -10.7% | ✅ | EVR | 2卖(daily) |
| 20260610 | 002371 | ¥619.2 | +13.7% | +28.9% | ❌ | EVR | 2卖(60m) |
| 20260610 | 600176 | ¥39.4 | +38.7% | +79.8% | ❌ | EVR | 2卖(60m) |
| 20260610 | 603662 | ¥68.9 | +2.1% | -2.5% | ❌ | EVR | 2卖(daily) 2卖(60m) |
| 20260611 | 300274 | ¥146.8 | +0.2% | +2.9% | ❌ | EVR | 2卖(60m) |
| 20260612 | 688082 | ¥304.7 | +23.5% | +47.0% | ❌ | UTAD | 1卖(60m) 顶背驰(60m) |
| 20260615 | 688233 | ¥111.8 | +11.7% | +91.8% | ❌ | UTAD+EVR | 2卖(60m) |
| 20260616 | 688120 | ¥201.2 | +20.5% | +64.0% | ❌ | EVR | 2卖(60m) |
| 20260617 | 603662 | ¥70.3 | -4.5% | +5.0% | ✅ | EVR | 2卖(60m) |
| 20260618 | 300567 | ¥213.9 | +28.6% | +23.4% | ❌ | EVR | 2卖(60m) |
| 20260618 | 301308 | ¥577.8 | +17.6% | +7.0% | ❌ | EVR | 2卖(60m) |
| 20260618 | 688082 | ¥378.8 | +9.3% | -4.0% | ❌ | UTAD | 1卖(60m) |
| 20260618 | 688361 | ¥252.2 | +40.8% | +35.6% | ❌ | EVR | 2卖(60m) |
| 20260622 | 601138 | ¥78.9 | -11.8% | -19.4% | ✅ | EVR | 2卖(60m) |
| 20260622 | 688041 | ¥327.3 | +8.4% | +3.6% | ❌ | EVR | 2卖(60m) |
| 20260623 | 688256 | ¥1413.0 | +12.9% | -1.8% | ❌ | EVR | 2卖(daily) |
| 20260625 | 002273 | ¥36.4 | +7.2% | -9.7% | ❌ | EVR | 2卖(60m) |
| 20260625 | 002475 | ¥74.4 | -18.2% | -13.4% | ✅ | EVR | 2卖(60m) |
| 20260626 | 002049 | ¥84.0 | -2.6% | +2.2% | ✅ | EVR | 2卖(60m) |
| 20260630 | 603662 | ¥71.2 | -2.2% | -17.6% | ✅ | EVR | 2卖(daily) |
| 20260630 | 688012 | ¥468.5 | -10.3% | -13.5% | ✅ | EVR | 2卖(60m) |
| 20260701 | 002049 | ¥86.4 | -3.4% | -16.4% | ✅ | EVR | 2卖(60m) |
| 20260702 | 301308 | ¥599.2 | +3.5% | -22.6% | ❌ | UTAD | 2卖(daily) |
| 20260702 | 603290 | ¥136.9 | -2.5% | -28.5% | ✅ | UTAD | 2卖(60m) |
| 20260702 | 688123 | ¥196.0 | +10.8% | -19.2% | ❌ | UTAD | 2卖(daily) |
| 20260703 | 002463 | ¥135.3 | -4.4% | -5.6% | ✅ | UTAD | 2卖(60m) |
| 20260703 | 601958 | ¥25.0 | -10.4% | -21.0% | ✅ | UTAD | 2卖(60m) |
| 20260703 | 688041 | ¥325.4 | +8.5% | -6.3% | ❌ | UTAD | 2卖(60m) |
| 20260706 | 688041 | ¥339.0 | +1.8% | -5.6% | ❌ | EVR | 2卖(daily) |
| 20260707 | 002472 | ¥45.0 | -5.7% | -16.2% | ✅ | EVR | 2卖(60m) |
| 20260708 | 300990 | ¥97.8 | +16.4% | -1.9% | ❌ | EVR | 2卖(daily) 2卖(60m) |
| 20260708 | 688019 | ¥316.1 | -10.5% | -20.0% | ✅ | EVR | 1卖(60m) |
| 20260709 | 002463 | ¥137.3 | -0.4% | -15.7% | ✅ | EVR | 2卖(60m) |
| 20260709 | 688099 | ¥102.4 | -4.3% | -10.1% | ✅ | EVR | 2卖(60m) |
| 20260710 | 002049 | ¥85.8 | -25.3% | -24.9% | ✅ | EVR | 2卖(60m) |
| 20260710 | 002475 | ¥62.1 | -6.6% | -2.5% | ✅ | EVR | 2卖(daily) |
| 20260710 | 300604 | ¥352.3 | -20.9% | -13.9% | ✅ | UTAD | 顶背驰(60m) |
| 20260710 | 301308 | ¥587.6 | -32.6% | -37.0% | ✅ | UTAD+EVR | 2卖(daily) |
| 20260710 | 688008 | ¥268.1 | -31.5% | -12.8% | ✅ | UTAD+EVR | 2卖(60m) |
| 20260710 | 688012 | ¥434.5 | -19.3% | -10.6% | ✅ | EVR | 顶背驰(60m) |
| 20260710 | 688041 | ¥353.0 | -13.6% | -11.0% | ✅ | UTAD | 2卖(60m) |
| 20260710 | 688120 | ¥324.6 | -25.5% | -18.7% | ✅ | UTAD | 1卖(60m) 顶背驰(60m) |
| 20260713 | 688041 | ¥345.0 | -7.2% | -9.1% | ✅ | UTAD | 2卖(daily) |
| 20260713 | 688123 | ¥185.0 | -35.9% | -29.1% | ✅ | UTAD | 2卖(daily) |
| 20260714 | 000725 | ¥7.0 | -10.1% | -20.5% | ✅ | EVR | 2卖(60m) |
| 20260714 | 002463 | ¥137.1 | -10.6% | -22.9% | ✅ | EVR | 2卖(60m) |
| 20260714 | 300604 | ¥335.6 | -5.2% | -17.6% | ✅ | EVR | 1卖(60m) |
| 20260715 | 688072 | ¥774.0 | +2.2% | -9.4% | ❌ | UTAD | 2卖(60m) |
| 20260717 | 300604 | ¥278.8 | +8.9% | -6.9% | ❌ | UTAD | 1卖(60m) |
| 20260722 | 300604 | ¥309.1 | -12.3% | N/A | ✅ | EVR | 1卖(60m) |
| 20260727 | 688008 | ¥227.4 | -16.5% | N/A | ✅ | EVR | 1卖⭐(daily) 2卖(60m) |

### 单信号胜率汇总

| 信号 | n | 5d跌率 | 10d跌率 | 备注 |
|---|---|---|---|---|
| 威科夫强 UTAD/DistributionStart | 163 | 53% | 63% | UTAD 10d最佳 |
| 威科夫中 EVR | 527 | 55% | 50% | 高频低质 |
| 缠论 1卖(daily) | 13 | **82%** | **89%** | 样本少极强 |
| 缠论 1卖(60m) | 58 | 59% | 50% | |
| 缠论 2卖(daily) | 122 | 57% | 57% | 大样本稳定 |
| 缠论 2卖(60m) | 208 | 57% | 56% | 大样本稳定 |
| 缠论 顶背驰(daily) | 18 | 47% | 65% | 10d有效 |
| SMC 卖侧扫(daily) | 323 | 59% | 54% | |
| SMC 卖侧扫(weekly) | 55 | **64%** | **62%** | 周线最稳 |

### 底部信号胜率汇总

| 信号 | n | 5d涨率 | 10d涨率 | 备注 |
|---|---|---|---|---|
| 🟢3买 周线=Markup | 244 | **54%** | 48% | 唯一大样本有效买入 |
| 🟢3买 日线=Markup | 240 | **53%** | 49% | |
| 🔴2买 日线=Accum | 115 | 46% | 48% | 底部2买勉强 |
| 🟢1买(任意) | 52 | **27%** | 40% | **失效，不要用** |
| 底背驰 周线=Markup | 34 | **30%** | 31% | **失效** |
| LPS 周线=Accum | 10 | **0%** | 40% | **失效** |

### 关键规则

1. **减仓规则:** 日线 Markup + 威科夫(UTAD/EVR) + 缠论(1卖/2卖) 同时触发 → 减仓1/3（65%/68% 跌率）
2. **加仓规则:** 3买信号在周线 Markup 背景下才有效（54%），其他买入信号基本失效
3. **周线威科夫最重要:** 周线 Markup 时信号有效，周线 Accum 时大部分信号失效
4. **EVR 是噪声:** 单独 EVR 胜率只有 55%，必须配合缠论卖点才有效
5. **信号叠加不是越多越好:** 3类全满足时胜率反而下降（信号滞后于价格顶部）
