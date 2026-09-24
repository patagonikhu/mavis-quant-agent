# 命名规范 (Naming Convention)

> **本文件:** v6.3.0 项目级代码命名规范 (2026-09-24 固化)
> **作用域:** 所有 skill / batch 脚本 / 任何 Python 代码
> **维护者:** 项目 owner + LLM
> **铁律违反者:** 不接受 commit

---

## 为什么要这个规范 (背景)

过去我们用过 `c_jump`/`c_leader`/`5a`/`5b`/`4c`/`rev_growth`/`n_lags` 等工程缩写 / 数字索引 / 工程术语词, 用户反馈"我也看不懂 lag 是什么意思".
v6.3.0 起所有命名强制走业务语义. 7 个 commit (`de239ee` → `4d0d923`) 在 `tools/batch/finance_earnings_blowout.py` 上完成了清理, 是参考样板.

---

## 行业基础 (3 个标准锚定)

| 标准 | 关键点 |
|---|---|
| **PEP 8** (Python 官方) | 函数/变量 `snake_case`, 类 `PascalCase`, 常量 `UPPER_SNAKE_CASE` |
| **Google Python Style Guide** | 业务命名优先, 避免无意义缩写 |
| **Tushare / AkShare / Eastmoney** 字段名 | 业务全称 (`netprofit_yoy` 不用 `np_yoy`) |

---

## 7 条硬规则

### 1. 业务名优先, 不用工程缩写

✅ `revenue_growth` `gross_margin` `net_profit_yoy` `operating_revenue`
❌ `rev_growth` `gm` `np_yoy` `or_yoy`

**例外:** tushare / akshare / eastmoney 原生字段名是上游 API 约束, 不动 (如 `or_yoy`).

### 2. 不用工程术语词 (用户可能看不懂)

✅ `_shift_quarters` (按季回溯)
❌ `_add_lags` `n_lags` `lags`
✅ `cumulative_decline` (累计下滑)
❌ `lag` `lookback`

**为什么:** `lag` / `lookback` / `add_lags` 是时序分析工程术语, 用户/业务方看不懂; 用业务意图直接命名.

### 3. 不用数字索引 / 数量后缀做功能代号

✅ `_rule_main_path` `_rule_reversal` `_rule_profit_surge`
❌ `c1` `c2` `c3a` `c3b` `5a` `5b` `4c` `4_conditions`
✅ `ebit_one_year_ago` (4 季 = 1 年, 业务名)
❌ `ebit_prev4` `ebit_4q_ago` `decline_4q`

**为什么:** 数字索引只是"工程实现顺序", 业务上没意义; 4 = 1 年也是业务知识, 直接写 "one_year_ago".

### 4. pandas lag 后缀走业务名 (不写 prev2 / prev4)

✅ `last_quarter` / `two_quarters_ago` / `three_quarters_ago` / `one_year_ago`
❌ `prev` / `prev2` / `prev3` / `prev4`

**注意:** 如果写一个 `_add_lags` 风格的函数, 内部用 `if/elif` 显式分支生成业务名, 不用 `f"{col}_prev{n}"` 模板.

### 5. 死代码立即删

不留无人调用的函数 / 列 / 变量 (即使注释说 "保留派生").

**验证方法:**
```bash
# 找所有 def, 检查调用计数
for fn in <all_def_funcs>; do grep -c "$fn" file.py; done
# 任何函数 def 1 + 调用 0 → 死代码, 删之
```

**例外:** 死代码可以删, 即使是 API 兼容层; 不允许"留作纪念".

### 6. CLI flag 保留 (用户长期沿用)

✅ `--rev-yoy` `--np-yoy` `--gm-tol` `--np-jump` (不动)
❌ 改 argparse `--foo` 字面参数 (会断 n8n / cronjob / 外部集成)

**业务层改名:** 改 `args.xxx` 内部属性名, 不动 argparse `--foo` 字面值.

### 7. SKILL.md 跟代码同步

任何变量 / 函数 / 列 / CLI flag 改名:
- 派生 Flag 表 (`rev_growth` 等) 必须更新
- 参数速查表 (CLI flag 默认值列表) 必须更新
- Hit Pipeline 段 (流程图) 必须更新
- 历史叙述段 (v1/v2/v3/v6.3.0 改名记录) 保留作为变更追踪

---

## 5 项 grep 验证 (Linter 自检)

新增 / 修改代码后, 跑这 5 行 grep, 命中即改:

```bash
# 1. 工程缩写 (c_jump / c_leader / 5a / 5b / 4c / 4_conditions 等)
grep -nE "\bc[1-9]_|\b[1-9]a\b|\b[1-9]b\b|\bc_jump|\bc_leader|\bc_lead|\brule_[1-9]" file.py

# 2. 工程术语 (用户可能看不懂)
grep -nE "\blag[s]?\b|\badd_lag|\bn_lag\b|\blookback" file.py

# 3. 数字后缀命名
grep -nE "[a-z]_[1-9]\b|\b[a-z]+_qoq_rising_prev[1-9]" file.py

# 4. 死代码 (def 了但 0 引用)
for fn in <all_def_funcs>; do grep -c "$fn" file.py; done

# 5. CLI flag vs 内部属性名 (CLI 不动, 内部动)
grep -nE "\-\-(np|rev|gm)" file.py    # CLI flag 保留
grep -nE "args\.(np|rev|gm)_" file.py  # 内部属性改业务名
```

---

## 改名决策流程 (改 1 个命名前)

```
1. 提出现状 (旧名) + 候选名 (业务名)
2. 用本文件 7 条规则逐条核查, 决定符合哪条
3. 跑 5 项 grep 验证没问题
4. 写改动: code + SKILL.md + AGENT_MEMORY.md (历史叙述)
5. 跑全量回归 (总命中数 + 关键样本)
6. commit + push
```

---

## 参考实现样板

`tools/batch/finance_earnings_blowout.py` v6.3.0 (2026-09-24) 是参考样板:

| Commit | 改动 |
|---|---|
| `de239ee` | 5 闸 prefilter + EBIT 业绩杀预警 |
| `8600ea1` | `5a` / `5b` 改业务名 (`ebit_crash` / `ebit_peak_down`) |
| `1693c5f` | `c_jump` / `c_leader` / `c_lead_*` 改业务名 |
| `8af9fd6` | 删死代码 (check_4_conditions / check_4q_monotonic) |
| `bd15323` | `rev_growth` / `np_growth` / `gm_*_stable` 改业务名 |
| `60bbf77` | `prev` / `prev2` / `prev3` / `prev4` / `4q` 改业务名 |
| `4d0d923` | `n_lags` / `_add_lags` / `lag` 改业务名 |
| `13cb146` | 沉淀命名规范到 handbook |

**验证不破行为:**
- 13 季总命中 891 只次 (不变)
- 业绩杀 5/5 全踢 (双林/华纬/富临/中熔/中科星图)
- 好票 3/3 全留 (中际旭创/兆易创新/拓荆)

---

## 新 skill / 脚本流程 (开新项目时)

```
1. 写完代码, 先跑 5 项 grep 自检 (改了再加)
2. 任何导致业务命名的变量, 按本规范定义
3. SKILL.md 顶部加交叉引用: '📐 命名规范: handbook/naming-convention.md'
4. 跑全量业务回归, 不破命名规范才能 commit
```

