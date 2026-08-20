# Mavis 信号存档系统 v1.0 (2026-07-20)

## 目录结构

```
data/signals/
├── 2026-07-20.jsonl       # 每日信号快照 (JSON Lines, 每行一个信号)
├── 2026-07-21.jsonl
├── ...
└── README.md              # 本文件
```

## signal 字段定义

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `signal_id` | str | ✅ | 唯一ID: `{code}_{date}_{signal_type}_{n}` |
| `code` | str | ✅ | 6位股票代码 |
| `name` | str | ✅ | 股票名称 |
| `date` | str | ✅ | 信号触发日期 (YYYY-MM-DD) |
| `signal_type` | str | ✅ | 信号类型: T+1/PEG_buy/PEG_sell/底背驰/顶背驰/SMC_Demand/SMC_Supply/威科夫_A/止跌/fflow_buy/fflow_sell/板块过热 |
| `source` | str | ✅ | 信号来源: /t-analyze /t-sector /t-watchlist /t-monitor |
| `trigger_price` | float | ✅ | 信号触发时股价 |
| `target_price` | float | ✅ | 目标价 |
| `stop_loss` | float | ✅ | 止损价 |
| `expected_direction` | str | ✅ | long / short |
| `expected_hold_days` | int | ✅ | 预期持仓天数 (默认 30) |
| `confidence` | str | ✅ | high / medium / low |
| `rationale` | str | ✅ | 信号理由 (一句话) |
| `key_signals` | obj | ✅ | 关键信号快照 {缠论, SMC, 威科夫, PEG, fflow} |
| `outcome` | str | - | pending / hit_target / hit_stop / expired |
| `outcome_date` | str | - | outcome 触发日期 |
| `outcome_price` | float | - | outcome 触发时股价 |
| `outcome_pnl_pct` | float | - | 实际 PnL (%) |

## outcome 标记规则 (signal_tracker.py)

- **hit_target**: 在 expected_hold_days 内,最高价 ≥ target_price
- **hit_stop**: 在 expected_hold_days 内,最低价 ≤ stop_loss
- **expired**: 超过 expected_hold_days,既未到目标也未到止损
  - expired 子标记: profitable (close > trigger) / unprofitable (close < trigger)
- **pending**: 未到 expected_hold_days,未触发任何条件

## 使用方式

### 手动记录信号
```bash
# 在 /t-analyze 或 /t-sector 输出后,运行:
python3 tools/analysis/signal_tracker.py --record --input my_signals.json
```

### 自动每日跟踪
```bash
# 每天收盘后跑一次,更新所有 pending 信号的 outcome
python3 tools/analysis/signal_tracker.py --update
```

### 查看胜率统计
```bash
# 所有信号
python3 tools/analysis/signal_tracker.py --stats

# 按 code
python3 tools/analysis/signal_tracker.py --stats --code 688256

# 按 signal_type
python3 tools/analysis/signal_tracker.py --stats --type 底背驰
```

## 设计原则

1. **JSONL 而非 JSON**: 方便追加 (append-only),每行独立可解析
2. **outcome 字段冗余存储**: 避免需要重新计算历史
3. **key_signals 快照**: 当时的市场状态完整保存,未来可以重演分析
4. **rationale 必填**: 强制 LLM 给出"为什么"这个信号,便于复盘
5. **expected_hold_days 强制**: 避免"永远 pending"的信号
