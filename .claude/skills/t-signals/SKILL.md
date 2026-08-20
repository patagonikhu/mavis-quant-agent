---
name: t-signals
description: 信号存档统一管理 v1.0 (2026-07-20) — 记录/更新/统计 signal_tracker 的一站式入口。子命令: record (记录新信号) / update (拉K线更新outcome) / stats (胜率统计) / verify (stats的别名)。任何时候用户说"记录这个信号"、"更新信号outcome"、"看信号胜率"触发。
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Bash
  - Write
  - Edit
---

# t-signals: 信号存档统一管理 (v1.0, 2026-07-20)

> 📊 **核心目的:** 一站式管理所有 signal_tracker 操作
> 🔧 **数据源:** `data/signals/*.jsonl` (signal_tracker.py 维护)
> 🔗 **数据来源:** /t-analyze /t-sector /t-monitor /t-trigger 自动写入

---

## 4 个子命令

### 1. record — 记录新信号

把当前分析的信号写入 `data/signals/YYYY-MM-DD.jsonl`

```bash
# 从 JSON 文件批量记录
python3 tools/analysis/signal_tracker.py record --input my_signals.json

# 交互式记录单条
python3 tools/analysis/signal_tracker.py record --interactive
```

**signal 字段定义 (与 README.md 一致):**
| 字段 | 必填 | 说明 |
|---|---|---|
| `signal_id` | ✅ | `{code}_{date}_{signal_type}_{n}` 唯一ID |
| `code` / `name` | ✅ | 股票代码 + 名称 |
| `date` | ✅ | 触发日期 YYYY-MM-DD |
| **`model`** | ✅ | **5 大类模型 (自动从 signal_type 推断)**: 缠论 / SMC / 威科夫 / PEG / fflow / T框架 / 板块 |
| `signal_type` | ✅ | 具体信号: T+1 / 底背驰 / 60分底背驰 / 止跌 / SMC_Demand / 威科夫A / fflow_buy / 板块过热 |
| `source` | ✅ | /t-analyze /t-sector /t-monitor /t-trigger |
| `trigger_price` / `target_price` / `stop_loss` | ✅ | 关键价位 |
| `expected_direction` | ✅ | long / short |
| `expected_hold_days` | ✅ | 预期持仓天数 (默认 30) |
| `confidence` | ✅ | high / medium / low |
| `rationale` | ✅ | 一句话理由 |
| `key_signals` | ✅ | 当时信号快照 {缠论, SMC, 威科夫, PEG, fflow} |
| `outcome` 等 | - | 初始 pending, update 时填充 |

### 5 大类模型 (model 字段)

| model | 子信号示例 | 验证周期 | 实战意义 |
|---|---|---|---|
| **缠论 (channel)** | 中枢突破 / 底背驰 / 顶背驰 / 60分底背驰 / 止跌 / 1买 / 2卖 | 3-30 天 | 一等公民,最高优先级 |
| **SMC** | Demand OB / Supply OB / BOS / CHoCH | 5-15 天 | 震荡市背驰补 |
| **威科夫 (wyckoff)** | Accumulation / Markup / Distribution | 30-90 天 | 主升浪级别 |
| **PEG** | PEG_buy (PEG<1.5) / PEG_sell (PEG>3) / L_undervalued | 30-180 天 | 基本面对冲 |
| **fflow** | fflow_buy (5日>+5亿) / fflow_sell (5日<-10亿) | 3-14 天 | 验证信号 |
| **T框架 (T_frame)** | T-1 / T+0 / T+3 / T+6 | 1-90 天 | 时机框架 |
| **板块 (sector)** | 板块过热 / 板块MA20偏离 | 1-14 天 | 板块整体信号 |

**自动推断规则 (signal_tracker.py):**
```python
# signal_type 子串匹配, 顺序重要
缠论 ← '中枢','背驰','止跌','1买','2买','3买','1卖','2卖','3卖','60分底/顶','日线底/顶'
SMC  ← 'Demand','Supply','BOS','CHoCH','OB'
威科夫 ← '威科夫','Accumulation','Markup','Distribution','Spring','SOS','ST','BC','UT','LPS'
PEG  ← 'PEG_','L_可达','PEG<','PEG>'
fflow ← 'fflow_','主力_','主力净','5日净','进货','出货'
T框架 ← 'T+','T-','T_'
板块  ← '板块','sector'
其他  ← 未匹配 (建议手动加 model 字段)
```

---

### 2. update — 更新 pending 信号的 outcome

拉今日价格 + K线, 比对 target/stop, 自动标记 outcome。

```bash
# 更新所有 pending (拉K线, 标记 outcome)
python3 tools/analysis/signal_tracker.py update

# 只更新单个
python3 tools/analysis/signal_tracker.py update --signal-id 688256_2026-07-20_T+1
```

**outcome 判定规则:**
- **hit_target**: hold 期内,最高价 ≥ target_price (long) 或最低价 ≤ target_price (short)
- **hit_stop**: hold 期内,最低价 ≤ stop_loss (long) 或最高价 ≥ stop_loss (short)
- **expired**: 超过 hold 期,既未到目标也未到止损
  - 子标记: profitable / unprofitable (看 close vs trigger)
- **pending**: 未到 hold 期,未触发任何条件

**使用建议:**
- 跑频次: 每周 1-2 次 (A 股 5 天/周, 1 周跑 1-2 次足够)
- 必须在收盘后跑 (否则 K 线数据不准)
- 拉 K 线数据量大, 5 只票约 1-2 秒, 70 只 watchlist 约 20-30 秒

---

### 3. stats — 信号胜率统计

```bash
# 全部
python3 tools/analysis/signal_tracker.py stats

# 按 code
python3 tools/analysis/signal_tracker.py stats --code 688256

# 按 signal_type
python3 tools/analysis/signal_tracker.py stats --type 底背驰

# 按 model (5 大类模型) 🆕
python3 tools/analysis/signal_tracker.py stats --model 缠论
python3 tools/analysis/signal_tracker.py stats --model 威科夫
python3 tools/analysis/signal_tracker.py stats --model PEG

# 7d 窗口
python3 tools/analysis/signal_tracker.py stats --window 7

# 组合
python3 tools/analysis/signal_tracker.py stats --code 688256 --type 60分底背驰 --window 30
python3 tools/analysis/signal_tracker.py stats --model 缠论 --window 30
```

**关键指标:**
| 指标 | 含义 | 健康阈值 |
|---|---|---|
| 胜率 (hit_target/all) | 触达目标比例 | > 50% ✅ |
| 平均 PnL | 所有完成信号平均 | > 0% ✅ |
| hit_target 平均 PnL | 盈利信号平均 | > +5% |
| hit_stop 平均 PnL | 亏损信号平均 | < -10% (止损要狠) |
| expired 比例 | 过期未触发 | < 30% |
| 样本量 | 已完成 | > 20 才有意义 |

**Stats 输出分组 (按优先级):**
1. 总体 (胜率/平均 PnL)
2. 按 model 分组 (5 大类模型排名) 🆕
3. 按 signal_type 分组
4. 按 code 分组

**典型输出 (累计 50 条信号后):**
```
📊 按 model 分组 (5 大类模型):
  缠论         样本=18  胜率=72% ⭐  平均PnL=+5.3%
  威科夫       样本=8   胜率=75% ⭐  平均PnL=+6.1%
  SMC          样本=5   胜率=60% 🟡  平均PnL=+3.0%
  T框架        样本=10  胜率=70% ⭐  平均PnL=+4.5%
  PEG          样本=6   胜率=33% ❌  平均PnL=-2.8%
  fflow        样本=3   胜率=33% ❌  平均PnL=-3.5%
  板块         样本=0   —      —    —
```

---

### 4. verify — stats 的别名 (向后兼容)

完全等同于 `stats`, 但语义更明确 (验证历史信号对错)。

```bash
python3 tools/analysis/signal_tracker.py verify --code 688256
```

---

## 典型工作流

### 场景 1: 跑完 /t-sector 后归档
```bash
# /t-sector 自动调用 (或我手动调用)
python3 tools/analysis/signal_tracker.py record --input /tmp/today_signals.json
```

### 场景 2: 1 周后, 想看信号对错
```bash
# 1. 先 update 拉最新 K 线
python3 tools/analysis/signal_tracker.py update

# 2. 看胜率
python3 tools/analysis/signal_tracker.py stats

# 3. 看具体某只票
python3 tools/analysis/signal_tracker.py stats --code 688256
```

### 场景 3: 找最可靠的信号类型
```bash
python3 tools/analysis/signal_tracker.py stats
# 输出按 signal_type 排名, 找胜率 > 60% 的类型
```

### 场景 4: 单条信号追踪
```bash
# 查某条信号
python3 tools/analysis/signal_tracker.py stats --signal-id 688256_2026-07-20_T+1
# (注: 当前版本 stats 不支持 --signal-id, 用 --code 过滤)
```

---

## 与其他 skill 的关系

| Skill | 关系 |
|---|---|
| `/t-analyze` | 输出报告时, 同时 `t-signals record` 写入 |
| `/t-sector` | 板块批量分析, 每只股票都 `t-signals record` |
| `/t-monitor` | 跨扫建仓/减仓窗口, 也是 `t-signals record` 来源 |
| `/t-trigger` | v3.1+ 自动 `t-signals record` (强信号 priority ≤ 2) |
| `/t-signals` | **本 skill**, 统一管理 record/update/stats |

---

## 数据维护规则

### 何时调 record?
- 跑完 /t-analyze 或 /t-sector **必须** record (用户/我手动)
- /t-trigger v3.1+ 自动 record (强信号)

### 何时调 update?
- **不自动** (按用户要求)
- 建议: 每周 1-2 次, 手动跑
- 跑时机: 周末/周中收盘后

### 何时调 stats?
- 想看胜率时
- 任何 /t-analyze 引用历史数据时

---

## 局限性 (重要! 长期需关注)

1. **样本量小**: A股 5 千只票, 但有效信号 < 100 时结论不可靠
2. **幸存者偏差**: 倾向记录"看起来会赢"的信号, 失败的可能漏记
3. **look-ahead bias**: 记录时已知当时数据, 验证时也用同样数据
4. **过拟合风险**: 按 signal_type 分组后, 每组样本 < 10 可能凑巧
5. **市场环境变化**: 2024 信号在 2026 可能失效 (结构性变化)

**缓解方法:**
- 强制记录**所有**信号, 包括看起来会失败的
- 7d/30d/90d 多窗口验证
- 区分牛市/熊市/震荡市, 分环境看胜率
- 信号 + 后续 fflow 二次确认 才算"实战信号"
