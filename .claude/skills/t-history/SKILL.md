---
name: t-history
description: 单只股票历史因子导出。生成 docs/factor-history-{code}-{name}-{N}year.md (5年/3年/自定义年数的因子历史走势)。底层调 _section_factor_history (0 重复代码, 复用 report_renderer)。任何时候用户说"生成某只股票的历史因子"、"导出5年因子历史"、"看长期走势"、"factor-history-5year" 都走这个 skill。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```
/t-history 300308                       # 单只, 默认 5 年
/t-history 300308 --years 3             # 单只 3 年
/t-history 300308 002028 688981         # 多只批量
/t-history 300308 --out /tmp/x.md       # 自定义输出路径 (单只时生效)
```

## Step 1: 检查 dump 数据

```bash
ls data/dump/300308.json 2>&1
```

dump 不存在的话, 提示用户先跑 dump:
```bash
bash tools/with_venv.sh python3 -m tools.dump_data 300308
```

## Step 2: 跑 export 脚本

```bash
bash tools/with_venv.sh python3 -m tools.batch.factor_history_export 300308 [其他code...] [--years N] [--out PATH]
```

**参数**:
- `codes`: 1+ 股票代码 (必填)
- `--years`: 年数 (默认 5, 1 年=250 交易日)
- `--out`: 单只时指定输出路径 (多只时忽略)

**耗时**: 5 年 ≈ 5-15 秒/只, 3 年 ≈ 3-8 秒/只

## Step 3: 验证输出

```bash
ls -la docs/factor-history-{code}-{name}-{N}year.md
head -3 docs/factor-history-{code}-{name}-{N}year.md  # 确认 header
wc -l docs/factor-history-{code}-{name}-{N}year.md    # 行数 (5年 ~250-400 行, 3年 ~150-200 行)
```

## Step 4: chat 摘要

```
✅ {code} {name} {N}年历史因子: docs/factor-history-{code}-{name}-{N}year.md
   - {row_count} 行 ({size} KB)
   - 数据范围: {最早日期} ~ {最晚日期}
   - 耗时: {elapsed}s
```

## 关键设计

- **0 重复代码**: 100% 复用 `tools/render/report_renderer._section_factor_history(data, lookback=N)`
  - 加了 `lookback: int = 120` 参数 (默认 120=3个月, 跟 analyze report 里的"📈 因子历史走势"段完全一样, 只是窗口更长)
  - 表格格式 / 信号 emoji / 过滤逻辑 全部跟原段一致
- **数据源**: `data/dump/{code}.json` → `AnalysisData.from_raw()` → 渲染
- **不接 refresh_all**: 按需跑, 不跟每天 watchlist refresh 混
- **不写 dump**: 历史数据每次跑现算, ~5-15s/只, 比 cache 简单

## 跟 `/t-analyze` 的区别

| | `/t-analyze 300308` | `/t-history 300308` |
|---|---|---|
| 输出 | `docs/analyze-300308-中际旭创.md` (22 section 报告, ~50KB) | `docs/factor-history-300308-中际旭创-5year.md` (只有因子历史表, 30-100KB) |
| 窗口 | lookback=120 (3个月) | 默认 lookback=1250 (5年) |
| 用途 | 投资决策 (4问 + T 框架 + PEG/DCF + 5方法) | 看长期因子历史 (威科夫子事件触发点/缠论买卖点/OBV背离/背驰/中枢变化) |
| 触发 | 每次 watchlist refresh (阶段 2 render) | 按需跑 (单次 export) |

## 例子

```bash
# 跑 5 只 watchlist 票 5 年历史
/t-history 300308 002028 688981 300274 600089
# → 5 个 docs/factor-history-{code}-{name}-5year.md 文件

# 跑 1 只 3 年
/t-history 300308 --years 3
# → docs/factor-history-300308-中际旭创-3year.md
```

## 出错处理

- **dump 不存在**: 提示先跑 `python3 -m tools.dump_data {code}`
- **dump 缺数据 (EPS/财务)**: 不影响, 历史因子只需要 K线
- **跑超时 (>5分钟/只)**: 默认 5 年, 跑 5-15 秒; 超过考虑 `--years 3` 缩短
