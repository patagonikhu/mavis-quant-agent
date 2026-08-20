---
name: t-analyze
description: 股票分析 + 批量扫描。单只：/t-analyze <code>；全量 watchlist：/t-analyze --all；板块：/t-analyze --sector AI；指定多只：/t-analyze 300308 600089。任何时候用户说"分析XX股票"、"XX怎么看"、"XX能买吗"、"批量分析"、"全部扫一遍"、"AI板块怎么样"都走这个 skill。替代原 t-watchlist / t-batch / t-sector。
user-invocable: true
allowed-tools:

> 🚨 **拉数据铁律 (2026-07-29 v3.4 固化)**
>
> **跑这个 skill 前, 必须先调 `t-pull` skill 拉数据** (走 `tools/dump_data.py`):
> /t-analyze 688017
/t-analyze 688017 绿的谐波
/t-analyze 特变电工
/t-analyze 688017 --no-news
/t-analyze --all
/t-analyze --all --no-news
/t-analyze --sector AI
/t-analyze 300308 600089 002028           ```

## 入口判断（最先做）

```
参数形式                          模式
/t-analyze 688017                → 单只模式
/t-analyze 688017 绿的谐波       → 单只模式（含名称）
/t-analyze --all                 → 全量模式（watchlist 全部）
/t-analyze --all --window 3      → 全量模式（仅 T±3 月窗口内）
/t-analyze --sector AI           → 板块模式（枚举板块股票）
/t-analyze 300308 600089         → 多只模式（指定列表）
```

**全量模式 / 板块模式 / 多只模式** → 走 [§批量模式] 流程
**单只模式** → 走 [§单只模式] 流程

## 批量模式（--all / --sector / 多 code）

### Step 1: 确定股票列表

```bash
# --all: 读 watchlist
bash tools/with_venv.sh python3 -c "
import json
stocks = json.load(open('data/watchlist.json'))['stocks']
for s in stocks:
    print(s['code'], s['name'])
"

# --sector AI: 读 sectors.json，没有则 LLM 枚举
bash tools/with_venv.sh python3 -c "
import json
sectors = json.load(open('data/sectors.json'))
codes = sectors.get('AI', [])
print(codes)
"
```

### Step 2: 批量拉数据 + 渲染

```bash
# 全量刷（推荐，4 worker 并行）
bash tools/refresh_all.sh

# 或逐只
for code in {codes}:
    bash tools/with_venv.sh python -m tools.dump_data {code} --render
```

### Step 3: 提取每只股票今日状态，写入 md 文件

```bash
bash tools/with_venv.sh python3 << 'PYEOF'
import json, os, sys, datetime
from pathlib import Path

sys.path.insert(0, '.')
from tools.analysis.analysis_engine import RawContext
from tools.analysis.factor_history import compute_factor_history, diff_rows, extract_signals, format_signals_for_render

watchlist = json.load(open('data/watchlist.json'))['stocks']
today = datetime.date.today().isoformat()
output_path = Path('docs') / 'signal-watchlist.md'  # 方案 A (2026-08-20): 单文件覆盖

lines = []
lines.append(f"# 全量扫描 {today}\n")
lines.append(f"> {len(watchlist)} 只票 | 数据来自 dump | 因子历史 diff\n")

# -------- buy/sell 信号汇总 --------
buy_rows, sell_rows = [], []
all_table_rows = []

for s in watchlist:
    code, name = s['code'], s['name']
    path = f'data/dump/{code}.json'
    if not os.path.exists(path):
        continue
    try:
        dump = json.load(open(path))
        ctx  = RawContext.from_dump(dump)
        rows = compute_factor_history(ctx, step=1, lookback=3)
        if len(rows) < 2:
            continue
        r        = rows[-1]
        changes   = diff_rows(rows[-2], rows[-1])
        sigs      = extract_signals(changes)           # [(type, detail, direction)]
        sig_fmtd  = format_signals_for_render(changes)  # emoji 字符串

        # 分类到 buy/sell
        for _, detail, direction in sigs:
            if direction == 'buy':
                buy_rows.append((code, name, detail))
            else:
                sell_rows.append((code, name, detail))

        # 完整表格行
        hub_d   = r.get('hub_daily') or {}
        hub_str  = f"¥{hub_d.get('low',0):.0f}~{hub_d.get('high',0):.0f}{hub_d.get('pos','')[:2]}" if hub_d.get('valid') else '—'
        wy       = (r.get('wyckoff_daily') or '?')[:10]
        ma       = f"{r.get('ma_dev_daily') or 0:+.1f}%"
        sig_str  = ' '.join(sig_fmtd) if sig_fmtd else '—'
        scene    = r.get('scene', '?')
        has_sig  = '⭐' if sig_fmtd else ''
        all_table_rows.append((code, name, scene, wy, ma, hub_str, sig_str, has_sig))
    except Exception:
        pass

# buy 表
lines.append("---\n\n## 底部信号（buy）\n\n")
lines.append("| 代码 | 名称 | 信号 |\n")
lines.append("|------|------|------|\n")
if buy_rows:
    for code, name, detail in buy_rows:
        lines.append(f"| {code} | {name} | {detail} |\n")
else:
    lines.append("| — | 无 | — |\n")

# sell 表
lines.append("\n---\n\n## 顶部/弱势信号（sell）\n\n")
lines.append("| 代码 | 名称 | 信号 |\n")
lines.append("|------|------|------|\n")
if sell_rows:
    for code, name, detail in sell_rows:
        lines.append(f"| {code} | {name} | {detail} |\n")
else:
    lines.append("| — | 无 | — |\n")

# 完整表格（⭐排前面）
lines.append("\n---\n\n## 完整状态表\n\n")
lines.append(f"| 代码 | 名称 | 场景 | 威科夫日 | MA日% | 日中枢 | 今日信号 |\n")
lines.append("|------|------|------|---------|-------|--------|----------|\n")
all_table_rows.sort(key=lambda x: (0 if x[7] == '⭐' else 1, x[0]))
for code, name, scene, wy, ma, hub_str, sig_str, has_sig in all_table_rows:
    lines.append(f"| {code} | {name} | {has_sig}{scene} | {wy} | {ma} | {hub_str} | {sig_str} |\n")

lines.append(f"\n---\n> 生成时间: {datetime.datetime.now().strftime('%H:%M:%S')}\n")

output_path.parent.mkdir(exist_ok=True)
output_path.write_text(''.join(lines), encoding='utf-8')

# chat 摘要
total   = len(all_table_rows)
with_sig = sum(1 for r in all_table_rows if r[7] == '⭐')
sig_codes = [r[0] for r in all_table_rows if r[7] == '⭐']
print(f'FILE: {output_path}')
print(f'SUMMARY: 共 {total} 只, {with_sig} 只有今日信号')
if sig_codes:
    print(f'SIG_CODES: {", ".join(sig_codes)}')
PYEOF
```

### Step 4: chat 输出（只给文件路径 + 摘要）

```
{Step 3 输出的 SUMMARY / FILE 行}
📄 完整报告: docs/signal-watchlist.md (方案 A, 单文件覆盖)
```

### Step 5: 深挖（可选）

```
💡 要对哪只深挖？（输入代码或名称，或 /t-analyze {code} 单只分析）
```

---


##
- `CLAUDE.md`（必读, 你的人设）
- `docs/analysis-framework.md#four-questions`（必读, 投资四问 + 龙头评分 + Priced-in 公式）
- `docs/analysis-framework.md#peg-ratio`（必读, PEG 比率 — 估值双检查）
- `docs/analysis-framework.md#t-framework`（必读, T 位置计算 + 10 阶段表）
- `docs/analysis-framework.md#decision-matrix`（必读, 综合决策矩阵, 含 PEG 降级规则）
- `data/events.json` → 过滤 `code` 匹配的事件
- `data/watchlist.json` → 找 `code` 匹配的笔记

如果用户没传 name, 从 `events.json` 或 `watchlist.json` 查。查不到就问。

## 单只模式（/t-analyze {code}）


```
60xxxx / 688xxx → 上交所 → secid = 1.{code},  SECUCODE = {code}.SH
00xxxx / 30xxxx / 002xxx / 003xxx → 深交所 → secid = 0.{code},  SECUCODE = {code}.SZ
```

###

###

###
```bash
# Step A: 拉数据 + 渲染完整 MD 报告（含因子历史走势 + 技术指标）
bash tools/with_venv.sh python -m tools.dump_data {code} --render

# Step B: 读 MD 报告（含所有技术指标，无需重复手算）
# 生成路径: docs/analyze-{code}-{name}.md
# 关键 section:
#   ## 📊 技术指标 (8 种) ⭐  → MACD/RSI/KDJ/BOLL/ATR/量比/OBV/EMA/ADX
#   ## 📈 因子历史走势（最近3个月） → 每日状态表，最后一行 = 今日
#   ## 🎯 5 方法 × 3 周期 综合矩阵 → 场景/共振数/行动建议
# 用 Read 工具读 docs/analyze-{code}-{name}.md 即可获取所有技术信号

# Step C: 提取今日触发信号（结构化）
bash tools/with_venv.sh python3 -c "
import json, sys
sys.path.insert(0, '.')
from tools.analysis.analysis_engine import RawContext
from tools.analysis.factor_history import compute_factor_history, diff_rows, extract_signals

dump = json.load(open('data/dump/{code}.json'))
ctx  = RawContext.from_dump(dump)
rows = compute_factor_history(ctx, step=1, lookback=5)
if len(rows) >= 2:
    changes = diff_rows(rows[-2], rows[-1])
    sigs = extract_signals(changes)
    r = rows[-1]
    print('=== 今日状态 ===')
    print(f'场景: {r[\"scene\"]}  威科夫日: {r[\"wyckoff_daily\"]}  威科夫周: {r[\"wyckoff_weekly\"]}')
    print(f'MA偏离: 日{r[\"ma_dev_daily\"]:+.1f}% 周{r[\"ma_dev_weekly\"]:+.1f}% 60m{r[\"ma_dev_60m\"]:+.1f}%')
    print(f'日背驰: {r[\"daily_beichi\"]}  周背驰: {r[\"weekly_beichi\"]}  60m背驰: {r[\"60m_beichi\"]}')
    h = r.get('hub_daily') or {}
    h60 = r.get('hub_60m') or {}
    print(f'日中枢: {h.get(\"low\",\"—\")}~{h.get(\"high\",\"—\")}  位置: {h.get(\"pos\",\"—\")}')
    print(f'60m中枢: {h60.get(\"low\",\"—\")}~{h60.get(\"high\",\"—\")}  位置: {h60.get(\"pos\",\"—\")}')
    print()
    print('=== 今日触发信号 ===')
    for sig_type, detail, direction in sigs:
        arrow = '⬆️买' if direction == 'buy' else '⬇️卖'
        print(f'{arrow} | {sig_type:<20} | {detail}')
    if not sigs:
        print('无新信号（今日延续昨日状态）')
elif rows:
    r = rows[-1]
    print('=== 当前状态（仅1行数据）===')
    print(f'场景: {r[\"scene\"]}  收盘: {r[\"close\"]}')
    print('（数据行数不足，无法计算 diff）')
else:
    print('ERROR: factor_history 返回空，请检查 dump 数据完整性')
"

# Step D: 读 dump 中的财务数据（不在因子历史里）
bash tools/with_venv.sh python3 -c "
import json, sys
sys.path.insert(0,'.')
from tools.analysis.analysis_data import AnalysisData

dump = json.load(open('data/dump/{code}.json'))
data = AnalysisData.from_raw(dump)

print('=== EPS 预测 ===')
eps = dump.get('eps_table', [])
for row in eps[-4:]:
    print(row)

print('=== 财务指标 ===')
fina = (dump.get('tushare') or {}).get('fina_rows', [])
for row in fina[:2]:
    print(row)
"
```

> **🚨 读 MD 而非手算（v3.4 新规）**
> `--render` 生成的 `docs/analyze-{code}-{name}.md` 已包含完整技术指标（MA/EMA/ADX/RSI/BB/OBV/ATR/量比）。
> **禁止在 skill 里重复手算这些指标**。直接用 Read 工具读 MD 报告对应 section 即可。

**为什么需要实际季报：**
- 机构 EPS 预测可能滞后（刚发业绩预告但机构未更新）
- 实际季报验证"净利率是否异常跳跃"（如兆易2026E净利率从9%→22%）
- 发现业绩预告：公司官方数字比机构更权威

**数据对比规则：**
```
若 实际最新季度年化净利 > 机构全年预测 × 0.8:
    → 机构预测可信，继续用机构数据
若 实际最新季度年化净利 < 机构全年预测 × 0.5:
    → ⚠️ 机构预测可能过于乐观，PEG/DCF用实际数据重算
若 发现业绩预告（新闻中）:
    → 优先用业绩预告数字替代机构预测
```

###
> **v4 核心更新 (2026-07-03):** 把 v3 框架的 **A 派 / C 派** (前视镜双视角) 与 v4 框架的 **真实 / 表观** (失真检测) **整合为 4 个 PEG 同时输出**。任何 /t-analyze 报告必须完整列出全部 4 个, 一个都不许藏。
> **历史教训:** 江波龙 v3 单一显示 PEG_A = 0.02x 误判低估, 但 PEG 真实 = 178x 极贵 → 单一 PEG 翻车
> **术语对照:** "PEG 表观" (原称"PEG 光学", 2026-07-03 user 更名为避免与"光学薄膜"撞名) = NTM 一致预期算的 PEG, 易失真; "PEG 真实" = 稳态 CAGR 算的, 可决策。

####
```
E0 = 上一财年实绩 EPS (dump 数据, 历史年报)
E1 = 本财年 NTM EPS (curry 一致预期均值, 当前财年)
E2 = 下一财年 EPS (curry 一致预期均值)
E3 = 再下一财年 EPS (curry 一致预期均值, 用于 DCF)

A 派 PEG (本财年, Backward / 后视镜):
  Forward_PE_A  = P / E1                          ← 当前价除以本财年 NTM EPS
  g_A           = (E1 / E0 - 1) × 100%            ← E1 相对 E0 的同比增速
  PEG_A         = Forward_PE_A / g_A                ← 后视镜 PEG

C 派 PEG (下一财年, Forward / 前视镜):
  Forward_PE_C  = P / E2                          ← 当前价除以下一财年 EPS
  g_C           = (E2 / E1 - 1) × 100%            ← E2 相对 E1 的预期增速
  PEG_C         = Forward_PE_C / g_C                ← 前视镜 PEG

PEG 真实 (稳态 CAGR, ✅ 决策依据):
  Forward_PE    = P / E1 (或 P / E3, 看数据期)
  CAGR          = (E3 / E0)^(1/3) - 1              ← 3 年稳态年化增速 (覆盖复苏期失真)
  PEG_真实      = Forward_PE / (CAGR × 100)

PEG 表观 (NTM YoY, ⚠️ 易受失真误导):
  Forward_PE    = P / E1
  g_NTM         = (E1 / E0 - 1) × 100%            ← 与 PEG_A 相同的 g
  PEG_表观      = Forward_PE / (g_NTM × 100)

```

####
```
步骤1: 检查是否存在"谷底复苏" / "周期顶部异常" 扭曲:
  - 任意历史年 EPS < 0 (亏损) 或 ROE < 0 → 含复苏弹性, NTM 增速虚高
  - NTM 净利率较历史均值跳跃 > 3 倍 → 数据异常, NTM 增速虚高 (例: 江波龙 2026E 净利率 35%)
  - 典型案例: 2024A EPS=-0.12 → 2026E +218% 是表观幻觉
  - 典型案例: 江波龙 2026E 净利率 35% vs 历史 6% = 5x 跳跃 → NTM 增速不可信

无失真: PEG_A = PEG_C = PEG_真实 = PEG_表观 (四件套一致, 输出单值即可)

有失真:
  - PEG_真实 (CAGR) 是 ✅ 决策依据
  - PEG_A / PEG_表观 看起来可能极便宜 (NTM 增速虚高) → ⚠️ 不参与决策
  - PEG_C (下一财年) 修正一部分失真但仍可能偏 → 🥈 辅助参考
  - 必须全部展示, 不藏
```

####
```
1. PEG_真实 (稳态 CAGR, ✅ 决策依据)
2. PEG_C (下一财年, 前视镜, 🥈 辅助)
3. PEG_A / PEG_表观 (后视镜, ⚠️ 失真场景下失真提示)
   注: PEG_A 数值 = PEG_表观 数值, 但语义不同
```

**🚨 强制要求 (v4 整合, 从江波龙事件学到):**
- **任何 PEG 输出都必须同时显示全部 4 个口径** (A 派, C 派, 真实, 表观), 一个都不许藏
- 即便 4 个值相互冲突或某些"看起来便宜", 全部保留原值输出
- 失真本身是数据信号 (周期顶部 / 数据异常), 必须留痕
- 不能用单一 PEG 做决策 — 单 PEG 翻车的本质原因

###
**无失真时 (四件套一致):**
```
**PEG 四件套:** X.X (全部一致, 决策依据 = PEG_真实)
  - PEG_A (本财年):  X.X (Fwd PE Y / g Z%)
  - PEG_C (下一财年): X.X (Fwd PE Y / g Z%)
  - PEG_真实 (稳态 CAGR): X.X (Fwd PE Y / CAGR Z%) ✅
  - PEG_表观 (NTM YoY):  X.X (Fwd PE Y / g Z%)
```

**有失真时 (必须全部显示):**
```
**PEG 四件套:**
  - PEG_A:        Y.Y (Fwd PE {pe} / g_NTM {g_a}%, ⚠️ 含{失真类型}, 后视镜)
  - PEG_C:        Z.Z (Fwd PE {pe2} / g_{next} {g_c}%, 🥈 前视镜, 失真已修正一部分)
  - PEG_真实:     X.X (Fwd PE {pe} / 稳态 CAGR {cagr}%, ✅ 决策依据)
  - PEG_表观:     Y.Y (Fwd PE {pe} / g_NTM {g_a}%, ⚠️ 表观值与 PEG_A 相同, ⚠️ 不参与决策)
```

**chat 报告头部强制示意 (4 个 PEG 全部显示):**
```
**PEG 四件套:** (v4 强制, 全部列出, 即便表观=0.02x 也照显示)
  - PEG_A (本财年, 后视镜): 0.02x ⚠️ (Fwd PE 18.5 / NTM YoY 865%, 含周期顶部异常)
  - PEG_C (下一财年, 前视镜): -1.01x ⚠️ (Fwd PE 17.6 / g next -18%, 含基数异常)
  - PEG_真实 (稳态 CAGR):    178x ❌❌ (Fwd PE 178 / CAGR 99.7%, ✅ 决策依据)
  - PEG_表观 (NTM YoY):      0.02x ⚠️ (与 PEG_A 相同, 标记失真)
```

###
**⚠️ Priced-in (TAM-based) 已废弃，改用 DCF L。**

```python
python3 << 'EOF'
GROWTH_YEARS = 5
def fair_value(L, e1, e2, e3, r_pct):
    r = r_pct/100.0
    pv = e1/(1+r)**1 + e2/(1+r)**2 + e3/(1+r)**3
    if e3>0 and L>0 and abs(L-e3)>1e-9:
        g = (L/e3)**(1.0/GROWTH_YEARS)-1.0
        for t in range(4,9): pv += e3*(1+g)**(t-3)/(1+r)**t
    elif e3>0 and L>0:
        for t in range(4,9): pv += e3/(1+r)**t
    pv += (L/r)/(1+r)**(3+GROWTH_YEARS)
    return pv

def implied_L(cap, e1, e2, e3, r_pct):
    r=r_pct/100.0; hi=max(cap*r*(1+r)**8*10,e3*100,1000.); lo=0.
    for _ in range(300):
        mid=(lo+hi)/2.
        if fair_value(mid,e1,e2,e3,r_pct)<cap: lo=mid
        else: hi=mid
    return (lo+hi)/2.

cap={市值亿元}; e1={净利润E1}; e2={净利润E2}; e3={净利润E3}
net_margin = {净利率_历史均值}  
for r in [8,10,12]:
    L = implied_L(cap,e1,e2,e3,r)
    g = ((L/e3)**0.2-1)*100 if e3>0 else 0
    reachable = {粗估营收天花板} * net_margin
    ratio = L/reachable if reachable>0 else 0
    print(f"r={r}%  L={L:.1f}亿  L/E3={L/e3:.2f}x  g={g:.1f}%  L/可达={ratio:.2f}x")
print(f"校正值(r=10%×0.7)={implied_L(cap,e1,e2,e3,10)*0.7:.1f}亿 ← 最接近真实")
EOF
```

**判断标准:**

```
L/E3:
  < 2   → 叙事未满，有空间
  2-5   → 较高预期，需验证
  > 5   → 叙事已满，警惕

L / 可达利润 (可达利润 = 粗估营收天花板 × curl净利率):
  < 0.8 → 叙事低估 ✅
  1-2   → 合理
  > 2   → 叙事透支 ❌
```

**报告输出格式 (table 展示 EPS + DCF):**

```
| 年份 | EPS | 净利润(亿) | 营收(亿) | ROE |
|------|-----|-----------|---------|-----|
| 2023A | X.XX | XX.X | XXX | X.X% |
| 2024A | X.XX | XX.X | XXX | X.X% |
| 2025A | X.XX | XX.X | XXX | X.X% |
| 2026E | X.XX | XX.X | XXX | X.X% |
| 2027E | X.XX | XX.X | XXX | X.X% |
| 2028E | X.XX | XX.X | XXX | X.X% |

DCF 隐含 L (市值=XXX亿):
| r    | 隐含L  | L/E3  | 隐含增速g | L/可达利润 |
|------|--------|-------|---------|-----------|
| 8%   | XX亿   | X.Xx  | X.X%    | X.Xx      |
| 10%  | XX亿   | X.Xx  | X.X%    | X.Xx      |
| 12%  | XX亿   | X.Xx  | X.X%    | X.Xx      |
| 校正 | XX亿   |       |         |           |
```

若 curl 失败: 标注 `(数据拉取失败，无法计算DCF L)`，不做估值判断。

###
> **核心目的:** PEG + DCF 解决"估值贵不贵", 但没解决"价格位置贵不贵"。**MA 均线** 给出技术层面的位置感, 防止"估值看似便宜但实际是拉高出货"的陷阱。
> **典型案例:** 立讯精密 v4 PEG 健康 (0.74x 🥇), 但 MA60=67.74 / MA120=59.81 = **拉高出货信号** → 应降为 ⚠️ 观察而非 🥈 标准。

> **🚨 v3.4 新规：MA 均线数据直接读 MD 报告，禁止手算**
> `--render` 生成的 MD 中 `## 📊 技术指标 (8 种) ⭐` section 已包含 MA5/MA20/MA60/MA120 偏离、BOLL、ATR 等。
> `## 📈 因子历史走势` 最后一行包含今日的 `ma_dev_daily` / `ma_dev_weekly` / `ma_dev_60m`。
> 用 Read 工具读 `docs/analyze-{code}-{name}.md` 对应行即可，无需重复 bash 计算。

####
状态 1: 多头排列 (强势上升通道)
  MA5 > MA20 > MA60 > MA120
  → 当前价在所有均线之上, 趋势健康
  → 加仓窗口 ✅ (PEG / DCF 仍要检查)

状态 2: 空头排列 (弱势下跌通道)
  MA5 < MA20 < MA60 < MA120
  → 当前价在所有均线之下, 趋势坏
  → 不接飞刀 ❌

状态 3: 拉高出货嫌疑 ⚠️ (立讯典型)
  MA60 > MA120 (中长期拉高)
  BUT 当前价 < MA5 (短期转弱)
  → 中期高位但近期回调, 是出货信号
  → ⚠️ 降级 (即便 PEG 健康, 也不应加仓)

状态 4: 健康调整 ✅ (可加仓)
  当前价 > MA120 但 < MA60
  AND MA120 仍在上行
  → 长期趋势健康, 中期回落, 标准加仓窗口
```

####
```
P / MA5   偏离 > 5%     短期超买
P / MA20  偏离 > 10%    短期严重超买, 大概率回调
P / MA60  偏离 > 20%    中期严重超买, 顶部信号
P / MA120 偏离 > 50%    长期估值透支

P < MA5   短期转弱
P < MA20  短期趋势坏
P < MA60  中期趋势坏
P < MA120 长期趋势坏
```

####
```
MA 多头排列 + PEG 健康       → 🥇 重仓 (估值+趋势双绿)
MA 多头排列 + PEG 透支       → ⚠️ 观察 (趋势好但估值贵)
MA 拉高出货 + PEG 健康       → ⚠️ 观察 (估值便宜但拉高出货, 立讯案例) ⚠️
MA 空头排列 + PEG 健康       → ⚠️ 观察 (估值便宜但趋势坏, 不接飞刀)
MA 健康调整 + PEG 健康       → 🥈 标准 (可加仓窗口)
MA 健康调整 + PEG 透支       → ⚠️ 观察
MA 混乱/无明确信号 + 任意    → 🥈 标准 (按估值决策, MA 不强制)
```

####
```
**MA 均线分析 (2026-07-02 收盘):**
| 均线 | 数值 | 偏离 | 数据源 |
|---|---|---|---|
| 当前价 | 60.90 | — | 🟢 qtimg 实时 (push2/push2his 已废弃 WAF 拦截) |
| MA5 (5日) | 66.17 | -7.96% | ⚪ 派生 (基于 dump['kline'] 603 条) |
| MA20 (20日) | 68.17 | -10.67% | ⚪ 派生 |
| MA60 (60日) | 67.74 | -10.10% | ⚪ 派生 |
| MA120 (120日) | 59.81 | +1.83% | ⚪ 派生 |

**MA 排列:** ⚠️ MA60>MA120 但 P<MA5/MA20 = **拉高出货嫌疑**
**MA 决策修正:** v4 原评级 🥈 标准 → **降为 ⚠️ 观察** (PEG 健康但拉高出货嫌疑)

**数据来源:**
- K-line 历史: 🟢 读 dump['kline'] (由 tools/dump_data.py 经腾讯 web.ifzq K线 拉取写入; ❌ push2his.eastmoney.com 已废弃 WAF 拦截)
- K-line 条数: 603 条 (2024-01-02 至 2026-07-02)
- 计算逻辑: MA = close 在 N 日内算术平均值 (Python statistics.mean)
每条 kline[i] = [date, open, close, high, low, volume, amount]
                 [0]   [1]   [2]    [3]    [4]    [5]      [6]
```
- `closes[i] = float(kline[i][2])` — 收盘价
- `highs[i] = float(kline[i][3])` — 最高价
- `lows[i] = float(kline[i][4])` — 最低价
- `vols[i] = float(kline[i][5])` — 成交量 (手)

---

> **🚨 v3.4 新规：以下 EMA / ADX / RSI / BB / 量价 / OBV 手算块已废弃**
>
> `--render` 生成的 MD 报告（`docs/analyze-{code}-{name}.md`）中 **`## 📊 技术指标 (8 种) ⭐`** section
> 已经由 `tools/render/report_renderer.py` 计算并写入，包含：
> - MACD (DIF/DEA/BAR + 金叉/死叉判定)
> - RSI (6/12/24 三周期)
> - KDJ (K/D/J + 死叉/金叉)
> - BOLL (中轨/上轨/下轨/带宽)
> - ATR (14日)
> - 量比 (vol_ratio)
> - OBV 趋势
> - EMA 金叉/死叉
>
> **禁止在 skill 里重复手算这些指标。直接用 Read 工具读 MD 报告的 `## 📊 技术指标` section。**
>
> 以下保留的内容：判读规则 + 信号矩阵（供 LLM 解读 MD 数据时参考）。

####
**EMA 判读规则（仅参考，数值从 MD 报告的 `## 📊 技术指标` section 读）:**
> 🚨 **禁止手算** — render_report 已计算，直接读 MD 即可

**判读规则 (死叉/金叉):**
```
条件                              评分  信号
EMA12 > EMA26 短期强                +1   ✅ 动量强
EMA12 < EMA26 短期弱                -1   ⚠️ 动量弱
EMA12 = EMA26  (公式不会真等)        0   中性
EMA12 - EMA26 偏离 > +5%            +1+  强多
EMA12 - EMA26 偏离 < -5%            -1+  强空
```

---

####
**ADX 判读规则（仅参考，数值从 MD 报告的 `## 📊 技术指标` section 读）:**
> 🚨 **禁止手算** — render_report 已计算，直接读 MD 即可

**判读规则:**
```
条件                              评分
ADX >= 25 强趋势 (无论方向)          0 (中性 — 看下面方向)
ADX < 20  弱趋势 / 震荡              0
ADX >= 25 AND +DI > -DI  强势向上    +1  ✅ 强多
ADX >= 25 AND +DI < -DI  强势向下    -1  ⚠️ 强空
ADX < 20  任何方向                    0   中性
```

---

####
**RSI 判读规则（仅参考，数值从 MD 报告的 `## 📊 技术指标` section 读）:**
> 🚨 **禁止手算** — render_report 已计算，直接读 MD 即可

**判读规则:**
```
条件                                评分
RSI > 70  超买                        -1  ⚠️ 警惕回调
RSI < 30  超卖                        +1  ✅ 反弹机会
30 <= RSI <= 70  中性                  0
50 <= RSI <= 70 健康多头区间           0  (不强加评分)
30 <= RSI <= 50 健康空头区间           0
```

---

####
**BOLL 判读规则（仅参考，数值从 MD 报告的 `## 📊 技术指标` section 读）:**
> 🚨 **禁止手算** — render_report 已计算，直接读 MD 即可

**判读规则:**
```
条件                                评分
BB position > 1.0  突破上轨超买      -1  ⚠️
BB position < 0.0  跌破下轨超卖      +1  ✅ 反弹机会
0 ≤ BB position ≤ 0.2 接近下轨       +1
0.2 < BB position < 0.8 中位          0
0.8 ≤ BB position < 1.0 接近上轨      0
BB 宽度 (BB上 - BB下) 扩张           (趋势确立标志, 不直接打分)
BB 宽度 收窄 / 挤压 < 5%              (准备突破 — 注意)
```

---

####
**量价 / OBV 判读规则（仅参考，数值从 MD 报告的 `## 📊 技术指标` section 读）:**
> 🚨 **禁止手算** — render_report 已计算，直接读 MD 即可

**vol_ratio 判读规则:**
```
vol_ratio > 1.5  放量                       (方向由价格决定)
vol_ratio 0.7-1.5 正常量
vol_ratio < 0.7  缩量

组合规则 (必须同时看价格方向):
vol_ratio > 1.5 + 价涨  → +1 真实上涨 (量价齐升)
vol_ratio > 1.5 + 价跌  → -1 恐慌出货
vol_ratio < 0.7 + 价涨  → ⚠️ 拉高出货嫌疑
vol_ratio < 0.7 + 价跌  → +1 健康回调 (卖压轻)
vol_ratio 中等           → 0 中性
```

#
> **v7 框架原理（参考）:** EMA 方向 + ADX 强度 + RSI 超买超卖 + BB 位置 + 量价配合，五维交叉验证。
> 数值已在 `docs/analyze-{code}-{name}.md` 的 `## 📊 技术指标 (8 种) ⭐` section 中计算完毕。
> 🚨 **禁止在 skill 里重复手算任何技术指标** — 直接 Read MD 报告对应 section 即可。

##
```
v7 评分矩阵（从 MD 报告的 ## 📊 技术指标 section 读数值后套用）:

指标      来源                        评分规则
EMA       MACD 金叉/死叉              金叉+1 / 死叉-1 / 中性0
ADX       ADX值+DI方向                ADX>=25+DI>0为+1 / ADX>=25-DI>0为-1 / ADX<20为0
RSI       RSI6/12/24均值              >70为-1 / <30为+1 / 中间0
BB        BOLL上下轨位置              跌破下轨+1 / 突破上轨-1 / 中间0
量价      vol_ratio+OBV趋势           放量涨+1 / 放量跌-1 / 缩量涨-1 / 缩量跌+1 / 其他0

总分 >= +3  强多 ✅  加仓
总分 +1~+2  偏多 🥈  标准持仓
总分  0     中性    观望
总分 -1~-2  偏空 ⚠️  减仓/不接刀
总分 <= -3  强空 ❌  不买
```

> **v7 数据来源声明:**
> - 🟢 所有技术指标: 读 `docs/analyze-{code}-{name}.md` 中 `## 📊 技术指标 (8 种)` section（由 render_report 写入）
> - ⚪ EMA/ADX/RSI/BB/OBV: Wilder 1978 / Granville 1963 标准公式，已在 render 阶段计算完毕
> - 🟡 评分阈值: 行业约定 (vol_ratio 0.7/1.5, RSI 30/70, ADX 25)

---

####
**评分规则总结表 (强制):**

| 指标 | 评分映射 |
|---|---|
| **EMA** | EMA12>EMA26 → +1, EMA12<EMA26 → -1, 等 → 0 |
| **ADX** | (ADX>=25 AND +DI>+DI → +1) OR (ADX>=25 AND -DI>+DI → -1) OR (ADX<20 → 0) |
| **RSI** | RSI>70 → -1, RSI<30 → +1, 中间 → 0 |
| **BB** | BB_pos>1.0 → -1, BB_pos<0.0 → +1, 中间 → 0 |
| **量价** | 见上 (vol_ratio × 价格方向) |

**总分评级:**
```
总分      评级           操作建议
+5 ~ +3  强多 ✅        加仓窗口 (通用交叉验证框架 等 3 信号汇合)
+2 ~ +1  偏多 🥈        标准持仓
 0       中性           观望
-1 ~ -2  偏空 ⚠️        减仓/不接刀
-3 ~ -5  强空 ❌        不买
```

> 🚨 **用法**: 从 `docs/analyze-{code}-{name}.md` 的 `## 📊 技术指标 (8 种)` section 读各指标值，套用上表得总分。禁止手算。

---

####
```
v5 MA 排列        | v7 复合评分 | 含义           | 操作
─────────────────────────────────────────────────────
✅ 多头 (5/20/60/120 上行) | +5/+3  | 强多         | 🥇 重仓
✅ 多头                | +1/+2  | 偏多           | 🥈 标准
✅ 健康调整 (回踩MA60) | +3     | 回调确认      | 🥈 标准 (右加仓窗口)
⚠️ 拉高出货             | -1     | 拉高嫌疑      | ⚠️ 观察 (立讯案例)
⚠️ 混乱/全跌破          | 0     | 中期转折     | ⚠️ 观察
❌ 空头                  | < -2 | 强空           | ❌ 不买 (不接刀)
```

> v5 MA 排列从 `## 📈 因子历史走势` 最后一行的 `ma_dev_daily` 读，v7 总分从 `## 📊 技术指标` section 综合判断。

---

####
```
✅ 信号 1: MA 多头排列 (v5)
✅ 信号 2: 量价齐升 vol_ratio > 1.0 持续 (v7)
✅ 信号 3: PEG 健康 < 1.5 (v4)
✅ 信号 4: 基本面催化 (中报 / 政策 / 大订单)
✅ 信号 5: 行业周期位置 ✅ 多

≥3/5 信号成立 = 强多 → 加仓窗口 (来自 通用研究报告)
≤1/5 信号成立 = 弱多 → 不行动

阳光电源当前: v5 ❌ 混乱 + v7 部分 +1 + 信号 4/5 缺失
  → ⚠️ 观察 (2-3 信号), 不行动
```

---

####
```
v5 MA 框架 (2026-07-02 阳光电源):
  P=127.84 / MA5=146 / MA20=150 / MA60=148 / MA120=153
  排列: ⚠️ 混乱 (全面跌破) → v5 评级 ⚠️ 观察

v7 复合信号评分:
  EMA12 vs EMA26:        EMA12 < EMA26 (短期走弱)        -1
  ADX(14):               ADX ≈ 30-35 (趋势强但向下)      +1 (但向下)
  RSI(14):               RSI ≈ 35-40 (接近超卖, 中性)      0
  BB position:           0.0-0.2 (跌破下轨或接近下轨)     -1 (超卖但弱势)
  量价配合:              vol_ratio > 1.5 (急跌放量)        -1 (恐慌出货)
  ──────────────────────────────────────────────────
  复合评分:              -2 / 5 = 偏空

v7 综合: v5 评级 ⚠️ + v7 评分 -2 = ⚠️ 观察偏强 (但不应加仓)
```

**v7 vs v5 区别:** v5 看位置 (P vs MA), v7 看动量 + 量价 (EMA/RSI/BB/Volume)。
v5 可能给出"回踩机会", v7 给出"真实趋势强度", 两个一起才能识别"健康回踩 vs 拉高出货"。

###
> **触发场景:** 用户问"X 年会翻 N 倍吗?","这只股能涨几倍?"等长周期概率问题。
> **核心方法论:** PEG 健康 ≠ 价格会涨; 需要从 4 个独立维度交叉验证, 最后给出综合概率。
> **典型案例:** 立讯精密 v5 — 用户问"翻一番?", 4 维度分析后综合概率 25-30%。

####
```
维度 1: 📊 DCF L 隐含缺口
  - 当前市值 / DCF L(校正值) = 隐含 P/L ratio
  - 翻倍要求: 市值/L ratio 翻倍 (= 隐含终局利润需翻倍)
  - 难度评级:
    ratio ≤ 3    → ✅ 易 (市场没充分 priced)
    ratio 3-5    → 🥈 中 (适度 priced, 翻倍需新催化剂)
    ratio 5-10   → ⚠️ 难 (已经 priced, 翻倍需行业大变化)
    ratio > 10   → ❌ 极难 (透支严重, 翻倍概率 < 10%)

维度 2: 🎯 可达利润路径
  - 当前可达利润 (TAM × 市占率 × 净利率)
  - 翻倍需: 可达利润涨 N 倍 (隐含 EPS 也涨 N 倍, PE 不变)
  - 关键问题: 哪些催化剂必须同时发生?
  - 概率 = 各催化剂独立概率乘积
  - 难度评级:
    1 个催化剂      → ✅ 易 (单因素事件概率 > 70%)
    2 个协同        → 🥈 中 (单因素 50% × 协同 70% ≈ 35%)
    3+ 个协同      → ❌ 难 (3 因素乘积 < 20%)

维度 3: 🍎 行业周期位置
  - 看 ROE 趋势 / PE 历史分位 / 行业 TAM 增速 / 政策周期
  - 周期顶部: ROE 已经在历史最高 25% 分位 + PE 突破历史 95% 分位 → 翻倍难
  - 周期底部: ROE 历史底部 + PE 历史 10% 分位 → 翻倍易
  - 难度评级 (行业周期位置):
    周期底部 (PE < 25%分位)         → ✅ 易 (翻倍空间大)
    周期中段 (PE 25-75%分位)         → 🥈 中 (估值合理, 翻倍需催化剂)
    周期顶部 (PE > 75%分位, ROE 顶)  → ❌ 难 (估值已 priced 顶部)

维度 4: 📈 PE 估值扩张可能性
  - 当前 PE vs 历史 PE 区间
  - 翻倍路径: PE 不变 + EPS 翻倍 (需利润翻倍) OR PE 翻倍 + EPS 不变 (估值扩张)
  - 单独靠 PE 扩张概率极低, PE 翻倍 (>50%) 在 A 股仅出现于:
    - 牛市泡沫 (2015 / 2020-2021)
    - 重大行业变革 (如新能源 2020)
  - 难度评级:
    PE 处于历史 10-30% 分位         → ✅ 易 (估值扩张空间大)
    PE 处于历史 30-70% 分位         → 🥈 中 (中等空间)
    PE > 70% 分位 + 历史高估        → ❌ 难 (估值已满, 翻倍只能靠 EPS)
```

####
```
最难维度 = 概率上限
  - 如果维度 1 (DCF L) 显示 ❌ 极难, 综合概率 ≤ ❌ 维度的预测范围 (≤ 10%)
  - 如果维度 2 (可达利润) 需要 3+ 催化剂, 综合概率 ≤ 30%
  - 如果维度 3 (周期顶) 显示 ❌ 难, 综合概率 ≤ 20%
  - 如果维度 4 (PE) 显示 ✅ 易 + 其他维度都是 ⚠️, 综合概率 ≤ 50%

公式 (粗略):
  P(翻倍) = min(P_dim1, P_dim2, P_dim3, P_dim4) × 0.5 ~ 1.0
  其中 0.5-1.0 是"4 维度是否协调"系数 (都难则 1.0; 一难三易则 0.5)
```

####
| 用户问题 | N | X 年 | 难度换算 |
|---|---|---|---|
| 翻一番? | 2x | 默认 5 年 | 基础难度 |
| 翻 3 翻? | 3x | 默认 5 年 | 难度 +50% |
| 1 年翻倍? | 2x | 1 年 | 难度 ×2 (短期股价震荡主导, 不是基本面) |
| 10 年 10x? | 10x | 10 年 | 难度 /3 (长期 TAM 摊平) |

####
```
##
**前置数据:**
- 当前价: ¥{P} / 总市值 ¥{cap}亿 / DCF L 校正: ¥{L}亿
- 翻 {N} 倍后: 市值 ¥{cap×N} / 需要市场 priced ¥{L×N}亿 终局利润

**维度 1: DCF L 隐含缺口**
  当前市值/L = {ratio}x, 翻倍需要 {N}x
  难度评级: ✅易 / 🥈中 / ⚠️难 / ❌极难

**维度 2: 可达利润路径**
  当前可达: ¥{reachable}亿, 翻倍需要: ¥{reachable×N}亿
  关键催化剂: 列出 N 个具体事件及概率
  综合催化剂概率: {P}%

**维度 3: 行业周期位置**
  当前 PE/ROE 历史分位: {quantile}
  周期位置: 顶/中/底
  难度评级: ✅易 / 🥈中 / ⚠️难 / ❌极难

**维度 4: PE 估值扩张**
  当前 PE vs 历史: {ratio}
  翻倍靠 PE 还是 EPS: 主要靠 ___ (EPS 翻倍 / PE 翻倍 / 两者各半)
  难度评级: ✅易 / 🥈中 / ⚠️难 / ❌极难

**综合概率:**
  min(4 维度概率) × 协调系数 = P(翻 {N} 倍) ≈ {P}%
  评级: 🎲 高概率 / ⚠️ 中等 / ❌ 低概率 / 🚫 极低

**案例:** 立讯 5 年翻 2 倍 → 4 维度分析后 25-30%
```

####
| 维度 | 数值 | 难度 |
|---|---|---|
| 维度 1: DCF L 缺口 | 市值/L = 7.8x, 翻倍需 15.6x | ❌ 极难 |
| 维度 2: 可达利润路径 | 翻倍需 600亿+ 净利, 需 Apple Vision Pro+汽车连接器+AirPods 三件同时 (15-20%) | ❌ 难 |
| 维度 3: 周期位置 | ROE 21% (顶部), PE TTM 25.9x (历史中位偏高) | ⚠️ 难 |
| 维度 4: PE 扩张 | 当前 PE 已处历史中位, 翻倍只能靠 EPS | ⚠️ 难 |
| **综合** | min = 15% (维度 2 决定上限) × 协调 0.7 | **🎲 25-30%** |

####
- **不允许只显示一个概率数字**: 必须 4 维度全部分析, 用户能看到 reasoning
- **不允许忽略难维度**: 即便有些维度是 ✅, 也要找到最难的维度作为综合上限
- **不允许用乐观周期作默认**: 必须具体分析 ROE / PE 分位 / TAM 增速
- **不允许跳过可达利润**: 即便难算, 也要粗估 (TAM × 市占 × 净利率)
- 标注时间维度: 1 年翻倍跟 5 年翻倍完全不是一回事, 必须分清

####
> **触发场景:** 用户问"5 年翻番候选", "找 PEG 健康 + 业绩爆发 + 板块 β 的票"
> **原理:** 3 独立硬信号 (估值便宜 + 业绩确定 + 板块 β) → 历史回测显示 3/3 信号满足 = 5 年 2x 概率 30-50%

#####
```
信号 A (估值便宜):
  PEG_真实 < 1.0x
  含义: 市场对增长有疑虑, 估值低估
  历史匹配: A 股 2019-2024 翻番股起涨点 PEG 普遍 < 1.0x

信号 B (业绩确定):
  g_NTM ≥ 30% AND CAGR(2025A→2028E, 3 年) ≥ 20%
  含义: 业绩预期明确, 不是画饼 (避免 NTM 100% 但实际是 2026E 异常)
  历史匹配: 翻番股业绩期 g_NTM 普遍 30-300%, CAGR 20-100%

信号 C (板块 β):
  板块 ∈ {AI 芯片, 半导体设备, 半导体设计, 半导体材料, 半导体封测, 先进封装,
          AI 服务器, 新能源, 稀土永磁, 人形机器人, 光学}
  含义: 行业有 3-5 年增长支撑, 不是夕阳行业
  历史匹配: A 股 2019-2024 翻番股都在主线 β 上
```

#####
```
3/3 信号全满足: 5 年 2x 概率 30-50%  → 重点加仓候选 (5-8% 仓位/只)
2/3 信号:       5 年 2x 概率 15-25%  → 小仓位 (2-5% 仓位/只)
1/3 信号:       5 年 2x 概率 5-15%   → 观望, 不加仓
0/3 信号:       5 年 2x 概率 < 5%    → 不投
```

#####
```
类型 1: 业绩爆发型 (净利 +100%+)
  必要条件: 行业 β 顺风 + 公司 share ↑ + 价格弹性
  时间窗口: 6-18 月
  案例: 海光信息, 寒武纪 (2023-24 AI 芯片主线)

类型 2: 估值修复型 (PE 1x → 2.5x)
  必要条件: 资金流入, 行业从冷到热
  时间窗口: 12-24 月
  案例: 中际旭创, 新易盛 (2023 AI 光模块)

类型 3: 周期反转型 (P/B 0.5 → 1.5)
  必要条件: 周期底部反弹 + 公司优势
  时间窗口: 12-24 月
  案例: 猪肉股某些周期反转

类型 4: 重估事件型 (国资/AI 主线首批)
  必要条件: 重大资产重组 / 政策催化 / 主题资金
  时间窗口: 1-3 月
  案例: 多个 2020-2024 借壳/重组股
```

#####
```bash
# 用 /t-analyze --all 扫全 watchlist，按 PEG < 1.0 + g_NTM ≥ 30% 筛选
```

#####
| 工具 | 用途 | 时间窗 |
|---|---|---|
| /t-analyze --all | 找翻番候选 (PEG + 业绩 + 板块 β) | 2-5 年 |
| v8 加权预期 | 当前价 → 加权预期 (估值显示 3-5 年位置) | 3-5 年 |
| v9.1 板块 DCF | 当前价 → 板块-aware 内在价值 (悲观假设) | 5+ 年 |

**总结**: /t-analyze --all 扫全 watchlist，v8/v9.1 算"几年后大概多少价"。

#####
```
⚠️ PEG 健康 (信号 A) 是个反向指标:
  翻番股起涨时 PEG 普遍 < 1.0x (市场没充分 priced)
  翻番股到顶部时 PEG 普遍 2-3x (市场情绪化)
  卖信号: PEG > 2.0x → 市场已经 priced 完了
  买信号: PEG < 1.0x → 还有空间

⚠️ 业绩高增长 (信号 B) 持续性是关键:
  g_NTM ≥ 50% 是好事, 但要查:
  - 行业是否有持续性 (半导体/AI 是, 周期股不是)
  - 公司 share 是否能维持 (龙头可以, 二线不行)
  - 竞争格局是否稳定 (没有新进入者)
```

####
> **触发场景:** 用户问"老业务会不会因为新叙事切换估值?" (特变电工 → AI 卖水人、海光 → DC/AI 算力)
> **核心方法:** 业绩增长 + 估值重估 = 翻番. 业绩驱动 + 估值重估 缺一不可.
> **vs §2g:** §2g 是"业绩驱动翻番" (3 硬信号), §2g.2 是"叙事切换 + 估值重估" 翻番

#####
```
维度 1: 业绩驱动 (业绩增长能翻番吗?)
  必要条件: 5 年内 EPS 能涨 1.5-2x (CAGR 15-20%+)
  检查: 一致预期 EPS 5 年 CAGR ≥ 15%

维度 2: 估值切换 (PE 能重估吗?) ⭐ 关键
  必要条件: 旧 PE 25% 分位 + 新叙事 PE 90% 分位
  重估空间 = PE 分位变化 (25% → 90% = +50% 估值扩张)
  触发因素: 叙事切换 catalyst (业绩大爆发 / 行业地位提升 / AI 等新故事)

维度 3: 板块 β (市场愿意给溢价吗?)
  必要条件: 当前板块在 AI/新能源/国产替代主线上
  检查: 板块是否是 AI/半导体/新能源/机器人/稀土/光学

维度 4: 龙头地位 (估值切换的先决条件)
  必要条件: 卡点 ⭐⭐⭐⭐+ + 龙头评分 ≥ 11/14
  检查: 行业 Top 1-3, 不可替代性
```

#####
```
业绩贡献: EPS_5y / EPS_now = 1.5-2x (CAGR 15-20%)
估值贡献: PE_new / PE_now = 1.3-1.8x (重估 30-80%)
合 计: 翻番 = 业绩 × 估值 = 2-3.6x

5 年翻番概率 (4 维度打分):
  维度 1 ✅ (CAGR ≥ 15%):  业绩基础好
  维度 2 ✅ (重估空间 ≥ 30%): 估值扩张
  维度 3 ✅ (板块 β):       行业有溢价
  维度 4 ✅ (龙头 ≥ 11/14):   估值切换的硬条件

  4 维全 ✅:  5 年 2x 概率 30-50%  (估值切换型)
  3 维 ✅:   5 年 2x 概率 15-25%
  2 维 ✅:   5 年 2x 概率 5-15%
  ≤1 维 ✅:  5 年 2x 概率 < 5%
```

#####
| 票 | 切换前 | 切换后 | 涨幅 | 触发因素 |
|---|---|---|---|---|
| 海光信息 688041 | CPU 厂商 (PE 50x) | DC 算力 (PE 200x) | 4x | 2023 AI 算力 + 信创 |
| 寒武纪 688256 | AI 芯片 (PE 100x) | DC 算力 (PE 300x) | 5x | 2023 ChatGPT + 算力中心 |
| 立讯精密 002475 | Apple 代工 (PE 20x) | AI 服务器 (PE 35x) | 3x | 2024 AI 服务器 ODM |
| 工业富联 601138 | ODM 代工 (PE 15x) | AI 服务器 ODM (PE 30x) | 2.5x | 2024 NVIDIA H100 量产 |

**共同模式**:
- 公司主业没变 (还是代工/芯片/算力)
- 但市场给估值从"传统" → "AI 链" 切换
- 触发: AI 主线资金 + 业绩订单兑现

#####
```
当前状态:
  PE TTM = 18x (历史分位 ~50%)
  5 年 CAGR EPS = 36% (2025→2028)
  板块: 电力变压器 (传统)

叙事切换路径:
  旧叙事: 电力设备 (PE 12-15x typical)
  新叙事: AI 数据中心卖水人 (PE 18-25x)

估值贡献: 18x → 25x = +39% (重估)
业绩贡献: 5 年 1.5-2x 增长 = +50-100%
合计: 1.39 × 1.5 = 2.1x ≈ 翻番 ✅

4 维度:
  维度 1 (业绩 CAGR 36% ≥ 15%): ✅
  维度 2 (重估 18 → 25 = 39% ≥ 30%): ✅
  维度 3 (板块 = 新能源/变压器, AI 卖水关联): ✅
  维度 4 (龙头 12/14): ✅

→ 4/4 ✅ = 5 年 2x 概率 30-50% ⭐
```

#####
| 框架 | 类型 | 关键信号 | 翻番路径 |
|---|---|---|---|
| **§2g** 业绩驱动 | EPS 增长 | g_NTM ≥ 30% + CAGR ≥ 20% | 业绩 5x → 股价 2x (业绩部分) |
| **§2g.2** 估值重估 | PE 扩张 | 旧 PE 25% → 新 PE 90% 分位 | 估值 1.5x + 业绩 1.5x = 2.25x |

**两个互补**:
- 业绩驱动: 中小盘 (PCB/封装) 翻番主要靠业绩
- 估值重估: 大盘 (变压器/电力) 翻番主要靠估值切换 + 业绩

**实战**:
- 胜宏科技 (§2g 业绩驱动): 加仓 5-8%
- 特变电工 (§2g.2 估值重估): 加仓 3-5% (等待叙事切换订单兑现)

#####
```
⚠️ 估值切换 ≠ 业绩爆发:
  估值切换 (PE 上调 30%+) 不需要业绩翻倍
  只需要市场改变 "看这只股票的分类" 
  例: 海光 CPU → DC 算力, PE 从 50x → 200x (4x 重估)
  业绩实际只涨 50% → 股价 4x = 业绩 50% × 估值 4x / 1.5 (上涨) 

⚠️ 估值切换常见陷阱:
  - 切换没有订单兑现 → 估值不切换, 跌回原位
  - 切换到位后估值已 Priced-in → 后续无空间
  - 切换叙事破裂 (AI 泡沫) → 暴跌 50%+
```

#####
```
加仓候选 (估值切换型, 概率 30-50%):
  - 强龙头 (11/14+) + PE < 历史 50% 分位 + 板块在 AI/新能源主线
  - 加仓 3-5% (叙事切换未 fully-priced)

观察候选 (估值切换, 等待确认):
  - 中等龙头 + PE 50-70% 分位
  - 加仓 2-3% (切换已部分 priced, 但有空间)

不做候选 (估值切换已 fully-priced):
  - 龙头 + PE > 90% 分位 + 业绩兑现
  - 跳过 (无空间)
```

---

###
> **触发场景:** 用户问"X 年会涨到多少?", "这只股合理价位?", "我应该设止损/止盈在哪?", "N 年后我能赚多少?" 等具体价格预测问题。
> **v6 是 "X 年翻 N 倍概率" (单一事件), v8 是 "X 年股价分布" (多个概率场景)**, 两者互补。
> **典型案例:** 立讯精密 v8 — 用户问"立讯会涨到多少? 给我个具体价位。"

####
```
3 个场景 (基于 v4 + v5 + v7 + v6 综合判断):

  1. 乐观 (bull)
     - 触发条件 (满足 ≥3 个): PEG 真实 < 1.0x / 5 维总分 ≥ +3 / MA 多头 / DCF L < 0.8x
     - 价格预测: P_bull = 当前价 × (1 + bull_return_pct)
     - 默认 bull_return_pct = +50% (参考大牛股 1-2 年涨 50% 是常态)

  2. 中性 (base) — 最可能的现实场景
     - 触发条件: 其他
     - 价格预测: P_base = DCF L 校正值 / 总股本 = "公允价值"
     - 跟当前价对比 → "低估 %" / "高估 %"

  3. 悲观 (bear)
     - 触发条件 (满足 ≥3 个): PEG 真实 > 2.0x / 5 维总分 ≤ -2 / MA 空头 / DCF L > 2.0x
     - 价格预测: P_bear = 当前价 × (1 - bear_drop_pct)
     - 默认 bear_drop_pct = -40% (参考腰斩是常见风险)

概率 (P_bull / P_base / P_bear) 动态调整:
  基础值: P_bull=20% / P_base=50% / P_bear=30% (行业均值)
  
  触发条件 → 概率调整:
    + MA 多头排列      → P_bull +10%, P_bear -5%
    + MA 空头排列      → P_bear +10%, P_bull -5%
    + PEG 真实 < 1.0x  → P_bull +10%, P_bear -5%
    + PEG 真实 > 2.0x  → P_bear +10%, P_bull -5%
    + 5 维总分 ≥ +3    → P_bull +10%, P_bear -5%
    + 5 维总分 ≤ -2    → P_bear +10%, P_bull -5%
    + DCF L < 0.8x     → P_bull +5%, P_bear -3%
    + DCF L > 2.0x     → P_bear +5%, P_bull -3%
    + 行业周期顶部嫌疑  → P_bear +10%, P_bull -5%
    + 行业周期底部      → P_bull +10%, P_bear -5%
  
  最终 P_bull + P_base + P_bear = 100% (强制归一化)

加权预期价格:
  P_expected = P_bull × P_bull_price + P_base × P_base_price + P_bear × P_bear_price
  
  置信区间 (概率 80%):
    下沿 = P_bull_price × 0.8 + P_bear_price × 0.2 (保守情形)
    上沿 = P_bull_price × 0.2 + P_bear_price × 0.8 (激进情形)
    中位 = P_base_price (最有可能)
```

####
```
行业分类 → 默认 bull_return / bear_drop:
  一线科技龙头 (如 Apple 链):  bull +30% / bear -25%
  强周期股 (存储/面板):         bull +50% / bear -40%
  真龙头 (市占 30%+):          bull +60% / bear -30%
  小盘股 (市值 < 200亿):       bull +80% / bear -50%
  AI/新能源主线:                bull +80% / bear -40%
  困境反转股:                  bull +100% / bear -30%
  蓝筹/低估值:                  bull +25% / bear -20%

框架默认用: bull +50% / bear -40% (中等假设)

可调参数:
  - 时间维度 (1 年 / 3 年 / 5 年): 时间越长 → bull/bear 越大
    1 年: bull +30% / bear -25%
    3 年: bull +50% / bear -35%
    5 年: bull +80% / bear -40%
```

####
```
##
**当前价:** ¥{P}
**总市值:** ¥{cap}亿 / 总股本: {shares}亿股

###
| 场景 | 价格 | 概率 | 触发信号 |
|---|---|---|---|
| 乐观 (bull) | ¥{P×1.5} | {P_bull}% | PEG < 1.0x, 5 维 ≥+3, MA 多头 |
| 中性 (base) | ¥{P×1.0}~{P×0.95} | {P_base}% | 默认情形 |
| 悲观 (bear) | ¥{P×0.6} | {P_bear}% | PEG > 2.0x, 5 维 ≤-2, MA 空头 |
| 加权预期 | ¥{P_expected} | 100% | — |

###- **保守:** ¥{P_bull×0.8 + P_bear×0.2} (预期下行风险大)
- **乐观:** ¥{P_bull×0.2 + P_bear×0.8} (预期上行空间大)

###- 加权预期 1 年回报: (P_expected / P) - 1 = {(P_expected/P-1)*100:+.1f}%
- 加权预期 3 年回报: 复利年化 = {(P_expected/P)^(1/3)-1)*100:+.1f}%/年

###| 加权预期回报 | 操作建议 |
|---|---|
| > +20% | 🥇 加仓窗口 |
| +5% ~ +20% | 🥈 标准持仓 |
| -10% ~ +5% | ⚠️ 观望 |
| < -10% | ❌ 减仓 / 不开仓 |
```

####
```
当前价 P = ¥60.90
总市值 = 4457亿 / 总股本 = 73.16亿股
DCF L 校正 = 570亿 → 目标价 = 570 / 73.16 = ¥7.79 (但 DCF L 是隐含 L 不是目标价)

PEG 真实 = 0.74x ✅
5 维 = +0/5 中性
MA 排列 = ⚠️ 拉高出货嫌疑
DCF L/可达 = 5.7x ❌ (隐含终局利润透支)
```

**应用 v8 框架 (3-5 年视角, 默认 bull +50% / bear -40%):**

```
3 场景价格 (基础假设):
  乐观 = 60.90 × 1.5 = ¥91.35
  中性 = 60.90 × 1.0 = ¥60.90 (假设 PEG 健康兑现)
  悲观 = 60.90 × 0.6 = ¥36.54

概率基础值: P_bull=20% / P_base=50% / P_bear=30%

触发条件调整:
  + PEG 健康 (< 1.5x): +P_bull 10%, -P_bear 5%
  + 5 维 +0 (中性): 不调整
  + MA 拉高出货 (⚠️): -P_bull 5%, +P_bear 5%
  + DCF L 透支 (> 2x): -P_bull 5%, +P_bear 5%

调整后:
  P_bull = 20 + 10 - 5 - 5 = 20% (仍基础值附近)
  P_base = 50% (不变)
  P_bear = 30 - 5 + 5 + 5 = 35%

归一化: 20% + 50% + 35% = 105%, → 19% / 48% / 33% (接近基础, 略偏熊)

加权预期价格 = 0.19 × 91.35 + 0.48 × 60.90 + 0.33 × 36.54
            = 17.36 + 29.23 + 12.06
            = ¥58.65

期望回报 = (58.65 / 60.90) - 1 = -3.7%
```

**结论: 立讯 v8 加权预期 3-5 年回报 -3.7%, 微跌。配合 v6 翻倍概率 25-30% 一起看 → 这只股在中性假设下"不赚不赔", 期望值是正负 0 附近, 不是好的集中仓位候选。**

####
```
🚨 任何 /t-analyze 报告都必须包含 v8 股价预测 (在 v4 / v5 / v6 / v7 之后):

✅ 必要元素:
  - 3 场景价格 + 概率 (用脚本动态调整, 不许拍脑袋)
  - 加权预期价格
  - 80% 置信区间
  - 期望回报率
  - 操作映射 (按回报率)

❌ 禁止:
  - 只显示一个数字 (如"我看多", "我看 ¥X")
  - 不显示概率直接给价位
  - 用"乐观情绪"等非数学语言
  - 忽略基线 (基础概率 20/50/30 必须显式提到)
```

####
```
v6 (X 年翻 N 倍概率):  单一事件概率 (翻倍或不翻倍) → 4 维度分析
v8 (X 年股价预测):    价格分布 + 加权预期 → 3 场景概率

举例 (立讯 5 年):
  v6: "翻 2 倍概率 = 25-30%"
  v8: "加权预期 5 年回报 = -3.7% × 期望价格 ¥58.65"

两个互补:
  v6 回答"翻倍难不难"
  v8 回答"几年后大概多少钱"
  一起用 = 既有定性概率, 又有定量价位
```

####
```
生成日期:    2026-07-03 (v8 加 "X 年股价预测 + 概率加权预期")
借鉴:        PE / PEG / DCF / MA / 5 维 + 行业分类假设
适用:        用户问"涨到多少" / "合理价位" / "止盈止损"
强制:        3 场景 + 概率 + 加权预期 + 置信区间 + 期望回报率 + 操作建议
禁止:        单一价格 / 单一概率 / 拍脑袋
```

---

###
> **触发场景:** 任何 /t-analyze 计算 DCF 估值时, **必须使用板块查表**而不是统一 FCF_factor=0.80
> **历史教训:** v9 (一刀切 FCF=0.80) 的系统偏差
>   - 京东方A (面板) 真实 OCF/NI = 0.55 → v9 高估 45%
>   - 立讯精密 (代工) 真实 OCF/NI = 0.88 → v9 低估 12%
>   - 半导体设备 真实 OCF/NI = 0.80 → v9 准确
> **修正方案:** 按行业 hardcode (WACC, FCF_factor, g) — 估值精度 ±15% → ±5%

####
| 维度 | 来源 | 类型 |
|---|---|---|
| 板块归属 (code → 板块名) | `data/sectors.json` ("sector_index_map") | 🟢 手工 + 半自动 |
| 板块假设 (板块名 → (WACC, FCF, g)) | `tools/sector_assumptions.py` | 🟡 行业 typical (硬编码) |

####
```python
SECTOR_DCF_ASSUMPTIONS = {
        "半导体设备":   (0.110, 0.80, 0.030),      "先进封装":     (0.110, 0.85, 0.030),
    "半导体材料":   (0.120, 0.75, 0.025),
    "半导体封测":   (0.110, 0.85, 0.025),
    "半导体设计":   (0.120, 1.20, 0.040),      "AI 芯片":      (0.130, 1.30, 0.050),      
        "消费电子代工": (0.100, 0.88, 0.030),      
        "AI 服务器":    (0.110, 0.90, 0.040),      
        "新能源":       (0.120, 0.85, 0.035),      
        "稀土永磁":     (0.130, 0.85, 0.035),
    
        "人形机器人":   (0.130, 1.00, 0.040),      
        "光学":         (0.110, 0.90, 0.035),
}

DEFAULT_ASSUMPTIONS = (0.100, 0.85, 0.030)  ```

####
| 图例 | 含义 | 验证 |
|---|---|---|
| 🟡 WACC | 行业 typical (Damodaran NYU 数据库 + A股实证) | 公开学术数据可查 |
| 🟡 FCF_factor | A股实证 (CSMAR 数据库, OCF/NI 行业均值) | 公开学术数据可查 |
| 🟡 g 永续增速 | 中国 GDP 长期增速 + 行业 share 调整 | IMF + 国务院数据 |
| 🟢 板块归属 | sectors.json (手工维护 + 自动反转索引) | 读本地文件 |

####
```
Step 1: 拿到 stock code (e.g. "002475")
  ↓
Step 2: 查 data/sectors.json["sector_index_map"]["002475"]
        → 找到 ["消费电子代工"]
  ↓ (如果没找到 → 用 DEFAULT_ASSUMPTIONS)
Step 3: 查 tools/sector_assumptions.get_assumptions("消费电子代工")
        → (WACC=0.10, FCF_factor=0.88, g=0.030)
  ↓
Step 4: 用板块-aware 假设算 DCF 矩阵:
        FCF(e1, e2, e3) = NI × 0.88 (板块特化系数)
        WACC = 10.0% (板块特化)
        g = 3.0% (板块特化)
```

####
```python
F1, F2, F3 = e1 * fcf_factor, e2 * fcf_factor, e3 * fcf_factor

PV_forecast = (
    F1 / (1 + wacc) +
    F2 / (1 + wacc)**2 +
    F3 / (1 + wacc)**3
)

for t in range(4, 9):
    PV_forecast += F3 * (1 + g) ** (t - 3) / (1 + wacc)**t

TV = F3 * (1 + g) / (wacc - g) / (1 + wacc)**8
total_value = PV_forecast + TV
```

####
| 票 | 行业 | v9 FCF 系数 | v9.1 FCF 系数 | 估值偏差改善 |
|---|---|---|---|---|
| 立讯精密 | 消费电子代工 | 0.80 (统一) | **0.88** | -12% → -5% |
| 阳光电源 | 新能源 | 0.80 | **0.85** | -6% → -3% |
| 中微公司 | 半导体设备 | 0.80 | **0.80** | 0% (一致) |
| 京东方A | 面板 (未 hardcode) | 0.80 | 0.85 (默认) | +45% → +30% |

####
```bash
python3 tools/sector_assumptions.py add {code} {sector_name}

python3 tools/sector_assumptions.py add 301308 半导体设备


```

####
```
🎯 v9.1 板块-aware DCF 矩阵 ({code} {name}):

**板块归属:** 🟢 {sector_name} (来自 data/sectors.json)
**DCF 假设 (板块 hardcode):** 🟡 (WACC={wacc}, FCF_factor={fcf}, g={g})
  - 来源: tools/sector_assumptions.py / Damodaran / A股实证
  - 用户可审计: 改 sector_assumptions.py 中假设即改变输出

| WACC \ g | g={g-1}% | g={g}% | g={g+1}% | g={g+2}% |
|----------|---------|---------|----------|----------|
| WACC-1%  | ¥XX/股  | ¥XX/股  | ¥XX/股  | ¥XX/股  |
| WACC%    | ¥XX/股  | ¥XX/股  | ¥XX/股  | ¥XX/股  |  ← 概率最高 (typical)
| WACC+1%  | ¥XX/股  | ¥XX/股  | ¥XX/股  | ¥XX/股  |
| WACC+2%  | ¥XX/股  | ¥XX/股  | ¥XX/股  | ¥XX/股  |

**当前价:** ¥{P} (位置: 高于/低于/接近 中位估值?)
**对应市值/股权价值:** ¥XX亿 (DCF 估算)
**估值偏差:** ±X%
```

####
```
✅ 任何 /t-analyze 必须包含:

1. v4 PEG 四件套 (§2d)
2. v3 DCF L 三锁 (§2e)
3. v5 MA 5/20/60/120 (§2f)
4. v7 5 维技术指标 (§2f.1)
5. v6 翻 N 倍概率 (§2g) — 可选
6. v8 加权股价预测 (§2h) — 可选
7. **v9.1 板块-aware DCF 矩阵 (§2i) — 强制**  🆕
8. 末尾 v9.1 数据透明度声明 (板块归属 + 假设来源)

❌ 任何 /t-analyze 不允许:
- 用统一 FCF_factor=0.80 (一刀切, 已废弃)
- 输出"公允价值 ¥X" 单值 (不显示矩阵)
- 隐藏板块归属 (必须标 🟢 sectors.json 来源)
- 隐藏假设来源 (必须标 🟡 sector_assumptions.py)
```

####
```
⚠️ 已知局限性:
  - 12 个成长板块 hardcode, 不是 30+ 行业全覆盖
  - 没 hardcode 的板块 → 用 DEFAULT_ASSUMPTIONS
  - 个股 β / leverage / CapEx 仍未知 (用行业 typical)

✅ 反 dirt 策略:
  - 任何时候显示"板块"和"假设来源", 用户可审计
  - 板块归属跨多板块时, 取第一个 (例如 立讯 同时在 消费电子代工 + AI 服务器, 取第一个)
  - 数据变化时, assumption 表同步更新
```

##
```
T = (event_date - today) / 30
today = 以系统日期为准
```

events.json 里可能有多条事件, 取**最近**的或**置信度最高**的做主分析, 其它作为"后续事件"列在报告末尾。

##
**默认行为:** 自动拉最近 3 个月关于该股的新闻, 发现 events.json 没收录的新 T 点。

**搜索 query 模板:**
- `"{name} {sector} 2026 最新"`
- `"{code} {name} 公告"`
- `"{name} 量产 订单 财报"`

**严格筛选标准 (噪音很大):**
- ✅ 真实关键事件 (财报披露日 / 大订单 / 政策落地 / 产品发布)
- ❌ 分析师观点 / 媒体猜测 / 股价评论

**输出格式 (在"💡 我注意到"之前):**

```
🔍 Web 发现新事件 (最近 3 个月新闻, 未在 events.json 中):

  - 688017 绿的谐波
    事件: 2026-Q2 财报披露
    日期: 2026-08-25 (推测, 待确认)
    来源: 公司公告 / 巨潮资讯网
    confidence: 0.5

💡 共搜到 1 个新事件, 要不要加进 events.json? [y/N]
```

**`--no-news`:** 跳过 step 4, 直接输出报告。

##
报告控制在 60 行内。包含:

1. 头部：代码 + 名称 + 日期
2. 四问结论（一行一个 ✅/❌/⚠️）
3. T 框架（T 位置、阶段、操作建议）
4. 龙头评分（0-14, 拆 4 个维度）
5. **Priced-in 三场景**（用真实市值）
6. **PEG**（用真实 EPS，防复苏扭曲）
7. **估值双检查**（三锁规则）
8. **📐 背驰 + 中枢分析（v15强制）** ← 见§2m
9. **📋 三层仓位策略（v15强制）** ← 必须包含以下所有价位：
   - 底仓：买入价/止损价/离场信号/目标价
   - 中仓：买入信号（3条件）/买入价区间/止损价/目标价
   - 波动仓：买入信号/减仓信号（MA20偏离>30%价位）/止损价
   - 止损阶梯：4档（60分/日线上沿/日线下沿/极端）
   - 中枢重心趋势：上移/横盘/下移
10. 综合建议（🥇/🥈/🥉/⚠️/❌）
11. 监控指标 + 风险
12. 🔍 Web 发现新事件 + y/N 询问
13. 💡 我注意到

**PEG + DCF L 输出格式 (必须包含 EPS table):**

```
| 年份  | EPS   | 净利(亿) | 营收(亿) | ROE   |
|-------|-------|---------|---------|-------|
| 2024A | 0.899 | 16.10   | 359.6   | 6.0%  |
| 2025A | 0.875 | 15.65   | 388.7   | 5.6%  |
| 2026E | 1.128 | 20.21   | 436.6   | 6.5%  |
| 2027E | 1.390 | 24.88   | 488.2   | 7.4%  |
| 2028E | 1.663 | 29.76   | 540.3   | 8.3%  |

**PEG:** 3.2 (NTM PE 93 / 稳态CAGR 21.4%，真实数据)

DCF 隐含 L (市值=1805亿, 净利率5.6%):
| r    | 隐含L   | L/E3  | 隐含增速g | L/可达利润 |
|------|---------|-------|---------|-----------|
| 8%   | 260亿   | 8.7x  | 54.6%   | >2x ❌    |
| 10%  | 374亿   | 12.6x | 66.3%   | >2x ❌    |
| 校正 | 262亿   |       |         |           |
粗估可达利润: OSAT营收天花板×净利率 ≈ XXX亿
```

##
报告末尾**必须**问:

```
💡 我注意到 <事件>, 要不要加进 data/events.json? [y/N]
```

#
- **PEG/Priced-in 必须用真实数据** — dump 里有什么用什么，dump 缺字段才降级为 LLM 估算并标注
- **复苏扭曲必须识别** — 前年亏损/ROE<0 时 NTM 增速不可信，必须算稳态 CAGR
- **4 PEG 必须同时显示** — A 派 + C 派 + 真实 + 表观 (v4 强制, 江波龙教训), 即便失真也要展示
- **MA 均线必须显示** — MA5/20/60/120 必算, v5 强制 (立讯/拉高出货教训), 与 PEG 联动决策
- **DCF 必须用 v9.1 板块-aware 框架** — 禁止用统一 FCF_factor=0.80 (v9 已废弃), 必须查 sectors.json + sector_assumptions.py
- **X 年翻 N 倍概率必须用 4 维度框架** — 用户问"翻倍吗"时, v6 强制: DCF L 缺口 + 可达利润路径 + 周期位置 + PE 扩张 4 维度全分析, 不允许单一概率数字
- **X 年股价预测必须用 v8 概率加权框架** — 用户问"涨到多少", v8 强制: 3 场景 (乐观/中性/悲观) + 动态概率 + 加权预期价格 + 置信区间 + 期望回报率
- **T 位置必须算** — events.json 里没这个 code 就说"未识别到 T 点", 然后继续分析
- **投资四问 + T 框架必须都套**, 不允许只给一个
- **DCF L 必须算** — 替代已废弃的 Priced-in, 三档折现率 + 校正值
- **WebSearch 默认开启** — 不要拉数据的用户传 `--no-news`
- **WebSearch 不自动写 events.json** — 必须用户 y/N 确认才加

#
**所有表格的列标签都加数据源标识, 让用户一眼知道是真数据还是 LLM 估的:**

| 图例 | 含义 | 来源 |
|---|---|---|
| 🟢 | **实数据** (dump 层拉的真值) | `data/dump/{code}.json` (由 tools/dump_data.py 写入) |
| 🟡 | **硬编码** (LLM 训练知识) | STOCK_REGISTRY 里的卡点/leader/板块等元数据 |
| ⚪ | **计算派生** (从实数据公式算出) | PEG / DCF L / 六关评估 |

**示例 (综合表格 E):**

```
| 维度 | 数值 | 数据源 |
| 卡点 | ⭐⭐⭐⭐⭐ | 🟡 STOCK_REGISTRY |
| TAM 倍数 | 5x | 🟡 STOCK_REGISTRY |
| 龙头评分 | 11/14 | 🟡 STOCK_REGISTRY |
| PEG (A 派) | 0.71x | ⚪ dump['eps_table'] + dump['current_price'] 派生 |
| DCF L/E3 | 1.06x | 🟢 P×shares + dcf_implied.py |
| 六关 #2 (好生意) | 5年NP均正 | 🟢 dump tushare.fina_rows |
```

**六关实数据化:**
- 关 1 (能力圈): 硬编码 🟡 (kpoint)
- 关 2 (好生意): 实数据 🟢 (dump tushare.fina_rows — 5年NP/营收/增速)
- 关 3 (护城河): 实数据 🟢 (dump tushare.fina_rows — 5年毛利率)
- 关 4 (管理层): 硬编码 🟡 (假设, 需人工查巨潮减持 + 分红)
- 关 5 (安全边际): 实数据 🟢 (PEG)
- 关 6 (决策纪律): 实数据 🟢 + 硬编码 🟡 (PEG + leader)

##
**⚠️ 强制步骤:** 跑完必须先写 MD，然后才在 chat 输出响应。

**文件命名:** `docs/analyze-{code}-{name}.md` (无日期, 累积同一文件)

每次跑追加 `v{N+1}` 版本，**只保留最近 2 版**。

###
```
A. 报告头部 (板块/卡点/TAM/Leader/Priced-in 三场景/PEG)
B. T 框架 (最近事件/T位置/阶段/操作建议)
C. 投资四问
D. 估值双检查 (三锁)
E. 数据来源 (真实 curl / LLM估算，标注置信度)
F. 关键看点
G. 监控指标
H. 风险
I. 对比 (vs 同行业已关注标的)
J. 🔍 Web 发现
K. 💡 我注意到
L. v1 → v2 对比
M. 元数据 (含 EPS 快照)
```

###
1. 读上下文 (events.json / watchlist.json / framework)
2. **先调 t-pull 拉数据**: `bash tools/with_venv.sh python -m tools.dump_data {code}` — 写入 data/dump/{code}.json
3. **读 dump 获取股价 + EPS**: `load_dump('{code}')` → dump['realtime'] / dump['eps_table'] (step 2b/2c)
4. **读 dump['kline'] 计算 MA5/20/60/120** (step 2f) — 禁止 curl K线接口
4. **Bash 计算 PEG 四件套 + DCF L + 5维技术指标** (step 2d/2e/2f.1)
5. WebSearch (默认开启)
6. **写入 docs/analyze-{code}-{name}.md** (实战行动信号写在 "🎯 5 方法 × 3 周期" 矩阵的"行动"字段: 🥇 大底建仓 / 🟢 主升持有 / ⬜ 震荡观望 / 🔴 减仓回避)
7. **更新 data/watchlist.json** (notes, 可选追加本次分析摘要)
8. 在 chat 输出响应 (60 行内)

> **v10+ 改动 (2026-08-13)**: 删除 watchlist `rating` + `rating_history` 字段 (57 只 × 2 = 114 处),
> 实战信号源统一为 report 5 方法矩阵的"行动"字段, 不再二次评级.
> **新增股票**: watchlist 已有该 code → 更新 notes; 没有 → 新增, notes 默认空.

###
- 实战信号源 = report 5 方法 × 3 周期 矩阵的"行动"字段 (🥇/🟢/⬜/🟡/🔴)
- 估值锚 (PEG/L/E3/Leader) 写在 report 的"🎯 综合判定"行, 不进 watchlist

###
> **触发场景:** 任何 /t-analyze 都必须运行主力分析，判断当前是主力进货、出货还是中性。
> **数据源:** 🟢 腾讯 K-line (OBV + vol_ratio + MA偏离)
> **核心逻辑:** 价是表象，量是真相。OBV背离 + 放量方向 + MA偏离 三维度综合判断。

####
```python
def analyze_smart_money(closes, vols):
    if len(closes) < 60: return {"verdict":"数据不足"}
    p = closes[-1]
    def ma(n): return statistics.mean(closes[-n:]) if len(closes)>=n else None
    m5,m20,m60,m120 = ma(5),ma(20),ma(60),ma(120)

        obv=[0]
    for i in range(1,len(closes)):
        if closes[i]>closes[i-1]: obv.append(obv[-1]+vols[i])
        elif closes[i]<closes[i-1]: obv.append(obv[-1]-vols[i])
        else: obv.append(obv[-1])
    obv_ma20 = statistics.mean(obv[-20:])
    obv_trend = (obv[-1]-obv[-20])/max(abs(obv_ma20),1)

        pct5  = (closes[-1]/closes[-6]-1)*100  if len(closes)>=6  else 0
    pct20 = (closes[-1]/closes[-21]-1)*100 if len(closes)>=21 else 0
    vr = vols[-1]/statistics.mean(vols[-20:]) if len(vols)>=20 else 1.0
    d120 = (p/m120-1)*100 if m120 else 0

    signals=[]; score=0

        if d120 > 50:   signals.append(f"MA120偏离+{d120:.0f}%（高位，需警惕）"); score-=1
    elif d120 < -20: signals.append(f"MA120偏离{d120:.0f}%（低位蓄势）"); score+=1

        if pct20>5 and obv_trend<-0.05:
        signals.append(f"⚠️OBV背离: 价涨+{pct20:.0f}%但OBV下行 → 拉高出货"); score-=2
    elif pct20<-5 and obv_trend>0.05:
        signals.append(f"✅OBV底背离: 价跌{pct20:.0f}%但OBV上行 → 主力吸筹"); score+=2

        if vr>1.5 and pct5>2:   signals.append(f"✅放量上涨(vol={vr:.2f}x)"); score+=1
    elif vr>1.5 and pct5<-2: signals.append(f"⚠️放量下跌(vol={vr:.2f}x)恐慌出货"); score-=1
    elif vr<0.5 and pct5>3:  signals.append(f"⚠️缩量拉高(vol={vr:.2f}x)出货嫌疑"); score-=1
    elif vr<0.7 and pct5<-2: signals.append(f"✅缩量回调(vol={vr:.2f}x)卖压轻"); score+=1

        if m60 and m120 and m60>m120 and m5 and p<m5:
        signals.append("⚠️MA60>MA120但价格转弱: 拉高出货嫌疑"); score-=1
    elif m5 and m20 and m60 and m120 and p>m5>m20>m60>m120:
        signals.append("✅多头排列: 趋势健康"); score+=1

    if score>=3:    verdict="🟢 主力进货(强)"
    elif score>=1:  verdict="🟡 偏进货"
    elif score==0:  verdict="⬜ 中性"
    elif score>=-1: verdict="🟠 偏出货"
    else:           verdict="🔴 主力出货(强)"

    return {"score":score,"verdict":verdict,"signals":signals,
            "d120":d120,"vr":vr,"obv_trend":obv_trend}
```

####
```
**主力资金分析:**
  结论: 🔴 主力出货(强)  (score=-2)
  信号:
    MA120偏离+54%（高位拉高）
    ⚠️ OBV背离: 价涨+27%但OBV下行 → 拉高出货
  数据: 🟢 腾讯K线 | vol_ratio=0.8 | OBV趋势=下行
```

####
```
score  含义                操作含义
≥+3    主力明显进货        可跟进，量价齐升
+1~+2  偏进货信号          标准持有
  0    中性/观望           不动
-1~-2  偏出货信号          减仓/注意
≤-3    主力明显出货        强烈减仓，不追高
```

####
```
PEG健康 + 主力进货  → 🥇 双重确认，最强信号
PEG健康 + 主力出货  → ⚠️ 估值便宜但主力在跑，轻仓观察
PEG透支 + 主力进货  → ⚠️ 贵但有人买，短线博弈
PEG透支 + 主力出货  → ❌ 双重卖出，坚决不买
```

**数据来源:** 🟢 dump['kline'] (由 dump_data.py 经腾讯 K-line 拉取写入) | ⚪ Python派生 (OBV/vol_ratio)


###
> **触发场景:** 任何 /t-analyze 必须计算 ATR 止损线，给出动态止损位和下周波动区间。
> **原理:** ATR = Average True Range（Wilder 1978），衡量股票真实波动幅度，避免被"日常噪音"扫出。
> **数据源:** 腾讯K线（同 v7 数据源，无需额外请求）

####
```python
def calc_atr_daily(closes, highs, lows, n=14):
    tr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i],
                 abs(highs[i]-closes[i-1]),
                 abs(lows[i]-closes[i-1]))
        tr_list.append(tr)
        atr = [sum(tr_list[:n])/n]
    for tr in tr_list[n:]:
        atr.append((atr[-1]*(n-1) + tr)/n)
    return atr[-1]  
def calc_atr_weekly(closes, n_weeks=4):
            ...
    return weekly_atr_mean

def atr_stop(current_price, atr, n=1.5):
    return current_price - n * atr
```

####
```
**ATR 止盈/止损分析 (v12):**
| 参数 | 数值 | 数据源 |
|---|---|---|
| 日线 ATR(14) | ¥0.82 (4.3%) | ⚪ 腾讯K线派生 |
| 周线 ATR(近4周) | ¥2.02 (10.5%) | ⚪ 腾讯K线派生 |
| 1.5x ATR 止损线 | ¥18.07 (-6.4%) | ⚪ 19.30 - 1.5×0.82 |
| 锁住利润 | +12.8% | 相对成本¥16.01 |

**下周波动区间（ATR统计，仅参考）:**
  68%置信(±1周ATR): ¥17.28 ~ ¥21.32
  95%置信(±2周ATR): ¥15.27 ~ ¥23.34

**ATR 止损设置建议:**
  N=1.5x（推荐）: ¥18.07 — 过滤日常震荡，趋势反转才触发
  N=2.0x（宽松）: ¥17.66 — 适合高波动股，拿大行情
  ⚠️ 止损线只上移不下移（移动止损）
```

####
```
N=1.0x：收益落袋快，但容易被震出（适合已大幅盈利想快锁）
N=1.5x：标准（推荐），日常波动通不过，真反转才触发
N=2.0x：宽松，适合高波动股/趋势股，牺牲部分利润拿大行情
N=2.5x：极宽，只做大级别止损，短线回调不管

经验规律（A股实证）：
  周ATR > 8%（高波动）→ 用 N=2.0
  周ATR 5-8%（中等）  → 用 N=1.5
  周ATR < 5%（低波动）→ 用 N=1.0
```

####
```
ATR 止损的已知局限：
1. ATR 描述历史波动，不预测未来 — 异常事件（黑天鹅）会突破任何ATR止损
2. 开盘跳空 — A股涨跌停机制下，实际成交可能比止损线差很多
3. 周ATR样本少 — 近4周只有4个数据点，统计稳定性有限
4. 不区分上涨波动和下跌波动 — ATR是双向的，上涨快的股票ATR也大
```


###
> **每次 /t-analyze 必须按阳光电源格式输出完整缠论分析。**
> **禁止使用 format_three_hubs，改用 format_chan_output。**

####
每份报告缠论部分必须包含以下 5 个章节，顺序不可变：

```

    #```

**命名规则（固化）：**
- 段: `段-{级别简称}-{编号}` 例: `段-60分-10`
- 中枢: `中枢-{级别简称}-{编号}` 例: `中枢-周-1`
- 级别简称: `周` / `日` / `60分` / `30分`
- ⭐真 = 宽度<25% 的中枢（质量好）

**4 个级别（固化，不可变）：** 周线 / 日线 / 60分 / 30分（不含月线）

####
```python

def seg_red_area(seg, hist, dt2i):
    if seg['sdt'] not in dt2i or seg['edt'] not in dt2i: return 0.0
    i1=dt2i[seg['sdt']]; i2=dt2i[seg['edt']]
    return sum(x for x in hist[i1:i2+1] if x>0)

def find_all_hubs_full(segs):
    """找所有中枢，含段编号和宽度"""
    hubs=[]
    for i in range(len(segs)-2):
        s1,s2,s3=segs[i],segs[i+1],segs[i+2]
        if not(s1['sst']==s3['sst'] and s1['sst']!=s2['sst']): continue
        hl=max(s1['lo'],s2['lo'],s3['lo']); hh=min(s1['hi'],s2['hi'],s3['hi'])
        if hh<=hl: continue
        center=(hl+hh)/2; width=(hh-hl)/center*100
        if width<1: continue
        hubs.append({'low':hl,'high':hh,'width':width,
                     's1_idx':i+1,'s3_idx':i+3,
                     's1_sdt':s1['sdt'],'s3_edt':s3['edt']})
    return hubs

def find_beichi_signals(segs, hist, dt2i, direction='top'):
    """找所有背驰信号
    direction='top': 顶背驰（上涨段，后段面积<前段50%，价格新高）
    direction='bot': 底背驰（下跌段，后段面积<前段50%，价格新低）
    """
    signals=[]
    if direction=='top':
        same=[(i,s) for i,s in enumerate(segs) if s['sst']=='B']
        def new_extreme(s1,s2): return s2['ep']>s1['ep']
        def area(seg): return seg_red_area(seg,hist,dt2i)
    else:
        same=[(i,s) for i,s in enumerate(segs) if s['sst']=='T']
        def new_extreme(s1,s2): return s2['ep']<s1['ep']
        def area(seg):
            if seg['sdt'] not in dt2i or seg['edt'] not in dt2i: return 0.0
            i1=dt2i[seg['sdt']]; i2=dt2i[seg['edt']]
            return sum(abs(x) for x in hist[i1:i2+1] if x<0)
    for k in range(1,len(same)):
        i1,s1=same[k-1]; i2,s2=same[k]
        if not new_extreme(s1,s2): continue
        a1=area(s1); a2=area(s2)
        if a1<=0: continue
        ratio=a2/a1
        if ratio<0.5:
            signals.append({'s1_idx':i1+1,'s2_idx':i2+1,'s1':s1,'s2':s2,
                            'a1':a1,'a2':a2,'ratio':ratio,
                            'trigger_date':s2['edt'],'trigger_price':s2['ep'],
                            'direction':direction})
    return signals

def format_chan_output(code, name, data):
    """
    缠论输出 — 读 data.analysis['chan']，禁止在此重复实现缠论计算。
    缠论计算已由 tools/dump_data.py → AnalysisData.from_raw() 完成并写入 analysis.chan。

    data: AnalysisData 实例
    用法:
        from tools.dump_data import load_dump
        from tools.analysis.analysis_data import AnalysisData
        dump = load_dump(code)
        data = AnalysisData.from_raw(dump)
        chan = data.analysis.get('chan', {})

    chan 字段结构 (由 dump 提供):
        chan['levels']   — dict: {'日': {...}, '周': {...}, '60分': {...}, '30分': {...}}
        chan['levels'][lbl]['segs']        — 段列表
        chan['levels'][lbl]['hubs']        — 中枢列表
        chan['levels'][lbl]['top_signals'] — 顶背驰信号列表
        chan['levels'][lbl]['bot_signals'] — 底背驰信号列表
        chan['levels'][lbl]['hub_latest']  — 最近中枢
    """
    chan = data.analysis.get('chan', {})
    if not chan:
        return "**⚠️ 缠论数据缺失** — 请先运行 `bash tools/with_venv.sh python -m tools.dump_data {code}`"

    levels = chan.get('levels', {})
    p = data.price
    lines = []

    # 60分背驰信号（最高优先级）
    lbl_60 = levels.get('60分', {})
    top_60 = lbl_60.get('top_signals', [])
    bot_60 = lbl_60.get('bot_signals', [])
    hubs_60 = lbl_60.get('hubs', [])

    lines.append("## 🚨 60 分钟级背驰信号")
    for kind, sigs, icon, label in [('top', top_60, '🔴', '顶背驰'), ('bot', bot_60, '🟢', '底背驰')]:
        if not sigs:
            continue
        sig = sigs[-1]
        s1, s2 = sig['s1'], sig['s2']
        a1, a2, ratio = sig['a1'], sig['a2'], sig['ratio']
        lines.append(f"**最新 {icon}{label}**: 触发 {s2['edt']} ¥{s2['ep']:.2f}")
        lines.append(f"  段比: {a2:.1f}/{a1:.1f} = {ratio:.0%} << 50% ✅")
    if not top_60 and not bot_60:
        lines.append("**当前无60分背驰信号** — 趋势延伸中")

    lines.append("\n---\n")

    # 4级别中枢 + 段 + 背驰汇总
    lines.append("## 📐 缠论完整数据 (4 个级别)")
    lines.append("> 段名格式: `段-{级别}-{编号}` | 中枢名格式: `中枢-{级别}-{编号}`")
    lines.append("> 级别: `周` `日` `60分` `30分`\n")

    lbl_map = {'周': '周', '日': '日', '60分': '60分', '30分': '30分'}
    for lbl in ['周', '日', '60分', '30分']:
        lv = levels.get(lbl, {})
        segs = lv.get('segs', [])
        hubs = lv.get('hubs', [])
        top_sigs = lv.get('top_signals', [])
        bot_sigs = lv.get('bot_signals', [])

        lines.append(f"### {lbl}线 ({len(segs)} 段 / {len(hubs)} 中枢)")

        # 最近真中枢
        true_hubs = [h for h in hubs if h.get('width', 100) < 25] or hubs[-1:]
        if true_hubs:
            hub = true_hubs[-1]
            hub_name = f"中枢-{lbl_map[lbl]}-{len(hubs)}"
            pos = "上方✅" if p > hub['high'] else ("下方⚠️" if p < hub['low'] else "内部⬜")
            lines.append(f"**最近真中枢**: {hub_name} ¥{hub['low']:.2f}–¥{hub['high']:.2f} 当前{pos}")

        # 最近10段
        recent_segs = segs[-10:]
        start_idx = len(segs) - len(recent_segs) + 1
        for k, s in enumerate(recent_segs):
            idx = start_idx + k
            arrow = '↑' if s.get('sst') == 'B' else '↓'
            chg = (s['ep']/s['sp']-1)*100
            lines.append(f"  段-{lbl_map[lbl]}-{idx}: {arrow} {s['sdt'][:10]}~{s['edt'][:10]} ¥{s['sp']:.0f}→¥{s['ep']:.0f} ({chg:+.1f}%)")

        # 背驰信号
        lines.append(f"**背驰**: 顶{len(top_sigs)}个 / 底{len(bot_sigs)}个")
        for s in top_sigs[-2:]:
            lines.append(f"  🔴顶背驰 段-{lbl_map[lbl]}-{s['s1_idx']} vs {s['s2_idx']}: 段比{s['ratio']:.0%} | {s['trigger_date'][:10]} ¥{s['trigger_price']:.2f}")
        for s in bot_sigs[-2:]:
            lines.append(f"  🟢底背驰 段-{lbl_map[lbl]}-{s['s1_idx']} vs {s['s2_idx']}: 段比{s['ratio']:.0%} | {s['trigger_date'][:10]} ¥{s['trigger_price']:.2f}")
        lines.append("")

    return '\n'.join(lines)


# 调用方式:
# from tools.dump_data import load_dump
# from tools.analysis.analysis_data import AnalysisData
# dump = load_dump(code)
# data = AnalysisData.from_raw(dump)
# chan_section = format_chan_output(code, name, data)

```

####
```
半导体设备 / AI芯片 / AI服务器: 背驰失效 → 改用板块MA20偏离>30%
CPO / 光学 / 半导体封测: 背驰有效
其他板块: 参考CLAUDE.md §5.2
```

##
> **原理：A股个股受大盘影响约60-70%，先看大盘再看个股是基本纪律。**
> **数据源：** 腾讯 K-line（同个股 K-line，字段相同，指数代码前缀 sh/sz）

###
```bash
```

###
```
大盘信号 → 个股仓位上限
───────────────────────────────
三指数全多头排列           → 满仓（80%+）
创业板+科创多头，沪深混乱  → 偏成长仓位（70%，多配科技）
沪深多头，创业板空头       → 偏价值仓位（70%，多配蓝筹）
三指数全空头排列           → 防御（30%以下）
三指数RSI全<35（超卖）     → 可轻仓抄底（逆向信号）
```

###
```python
rel_strength = 创业板近20日涨跌幅 - 沪深300近20日涨跌幅

rel > +3%  → 资金偏向成长/科技，利好 AI/半导体/机器人等持仓
rel < -3%  → 资金偏向价值/周期，利好银行/能源/消费等
中性区间   → 无明显风格偏好
```

###
```
📊 大盘背景 (今日必查)
  创业板指: 3851  MA20偏-5.9%  RSI=37  ⚠️跌破MA60  EMA死叉
  科创50:   2010  MA20偏+0.0%  RSI=51  →混乱       EMA金叉  近60日+43%
  沪深300:  4797  MA20偏-1.7%  RSI=43  ⚠️跌破MA20   EMA死叉

  风格：创业板 vs 沪深300 近20日 = -2.6%（价值偏强）
  仓位建议：⚠️ 大盘偏弱，个股仓位控制在50%以下
  ATR止损：创业板ATR=169(4.4%)，持成长股止损宽度参考此值
```

###
```
创业板跌破MA60 + RSI<40 + 成交量萎缩（vol<0.9）:
  → 通常是短暂超卖，3-5个交易日内反弹概率>70%
  → 但趋势未变（空头排列），反弹后可能继续跌

创业板多头排列 + RSI 50-70 + 北向资金净流入:
  → 科技成长股最强进攻窗口
  → 持仓可以扩张至满仓

创业板 vs 沪深300 差值连续2周 > +5%:
  → 成长股主升浪，不要买价值股
  → 优先 AI/半导体/机器人等主线

当前状态（2026-07-14）:
  创业板 跌破MA60，RSI=37，缩量（0.85）
  → 技术面弱势整理，但未到恐慌
  科创50 MA20附近，RSI=51，相对强（近60日+43%）
  → 科创主线依然健康，只是短期回调
  结论: 个股优先选科创50成分股，创业板整体偏弱
```



###
```python
python3 -c "
import subprocess,json,statistics
indices=[(sz399006,创业板),(sh000688,科创50),(sh000300,沪深300)]
results={}
for idx,name in indices:
    try:
        kd=json.loads(r.stdout.decode('utf-8','ignore'))[\data'][idx][\day']
        closes=[float(x[2]) for x in kd];vols=[float(x[5]) for x in kd];p=closes[-1]
        def ma(n):return statistics.mean(closes[-n:]) if len(closes)>=n else None
        m20=ma(20);m60=ma(60)
        gains=[max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
        losses=[max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
        ag=statistics.mean(gains[-14:]);al=statistics.mean(losses[-14:])
        rsi=100-100/(1+ag/al) if al>0 else 100
        pct20=(closes[-1]/closes[-21]-1)*100 if len(closes)>=21 else 0
        if m20 and p>m20 and m60 and p>m60:arr='✅均上'
        elif m20 and p<m20:arr='⚠️跌破MA20'
        elif m60 and p<m60:arr='⚠️跌破MA60'
        else:arr='→混乱'
        results[name]=dict(p=p,rsi=rsi,arr=arr,pct20=pct20,d20=(p/m20-1)*100 if m20 else 0)
    except:pass
print('📊 大盘背景:')
for name,r in results.items():print(f'  {name}: {r[\"p\"]:.0f}  MA20偏{r[\"d20\"]:+.1f}%  RSI={r[\"rsi\"]:.0f}  {r[\"arr\"]}'  )
if '创业板' in results and '沪深300' in results:
    rel=results['创业板']['pct20']-results['沪深300']['pct20']
    print(f'  风格: 创业板vsA50={rel:+.1f}% ({\"成长强\" if rel>3 else \"价值强\" if rel<-3 else \"中性\"})' )
rsi_avg=statistics.mean([r['rsi'] for r in results.values()])
n_warn=sum('⚠️' in r['arr'] or '❄️' in r['arr'] for r in results.values())
if rsi_avg<38:print('  🟡 三指数偏超卖，关注反弹机会')
elif n_warn>=2:print('  ⚠️ 大盘偏弱，仓位控制50%以下')
else:print('  🟢 大盘尚可，正常持仓')
"
```



###
```python
python3 -c "
import subprocess, json
stocks=[('usNVDA','英伟达'),('usSMH','费城半导体ETF'),('usQQQ','纳斯达克QQQ')]
print('🌏 美股前夜:')
for code,name in stocks:
    try:
        p=r.stdout.decode('utf-8','ignore').split('~')
        price=float(p[3]) if len(p)>3 else 0
        chg=float(p[32]) if len(p)>32 else 0
        flag='🟢' if chg>0 else '🔴' if chg<0 else '⬜'
        alert=' ⚡高开预警' if (code=='usNVDA' and chg>3) else ''
        print(f'  {flag}{name}: \${price:.1f}  {chg:+.2f}%{alert}')
    except: print(f'  {name}: 获取失败')
try:
    nvda_chg=float(nvda_r.stdout.decode('utf-8','ignore').split('~')[32])
    smh_chg=float(smh_r.stdout.decode('utf-8','ignore').split('~')[32])
    if nvda_chg>3 and smh_chg>2:print('  → 算力/半导体情绪强，A股科创板今日大概率高开')
    elif nvda_chg<-3 or smh_chg<-2:print('  → 美股半导体大跌，科创50今日注意低开风险')
    else:print('  → 美股平稳，无特殊信号')
except:pass
"
```

**输出示例：**
```
🌏 美股前夜:
  🟢英伟达: $211.8  +4.06% ⚡高开预警
  🟢费城半导体ETF: $600.3  +2.51%
  🟢纳斯达克QQQ: $719.7  +1.12%
  → 算力/半导体情绪强，A股科创板今日大概率高开
```

**注意：** 美股数据为前一交易日收盘价（A股开盘前参考），非实时。英伟达>+3%为高开预警阈值。

"""
缠论补充分析模块 v1.0
包含：SMC / 量价综合 / 多市场共振 / 威科夫
"""
import statistics


def find_order_blocks(dates, opens, closes, highs, lows, lookback=50):
    """
    Order Block：机构下单区域
    定义：最后一根反向K线（之后价格突破该方向的高/低点）
    
    看涨OB (Bullish OB)：
      下跌段最后一根阴线，之后价格突破该阴线最高价 → 回踩时是支撑
    看跌OB (Bearish OB)：
      上涨段最后一根阳线，之后价格突破该阳线最低价 → 反弹时是压力
    """
    obs = []
    n = min(len(closes), lookback)
    
    for i in range(1, n-2):
                if closes[i] < opens[i]:                          ob_high = highs[i]
            broken = any(highs[j] > ob_high for j in range(i+1, min(i+10, n)))
            if broken:
                obs.append({
                    'type': 'bullish',
                    'date': dates[i],
                    'high': highs[i],
                    'low': lows[i],
                    'desc': f"看涨OB ¥{lows[i]:.2f}-¥{highs[i]:.2f} ({dates[i][:10]})"
                })
        
                if closes[i] > opens[i]:              ob_low = lows[i]
            broken = any(lows[j] < ob_low for j in range(i+1, min(i+10, n)))
            if broken:
                obs.append({
                    'type': 'bearish',
                    'date': dates[i],
                    'high': highs[i],
                    'low': lows[i],
                    'desc': f"看跌OB ¥{lows[i]:.2f}-¥{highs[i]:.2f} ({dates[i][:10]})"
                })
    
    return obs[-6:]  

def find_fvg(dates, highs, lows, lookback=30):
    """
    Fair Value Gap（公允价值缺口）：
    三根K线中，第1根高点 < 第3根低点 → 看涨FVG（上涨缺口）
    三根K线中，第1根低点 > 第3根高点 → 看跌FVG（下跌缺口）
    价格回填FVG区域时是重要支撑/压力
    """
    fvgs = []
    n = min(len(dates), lookback)
    
    for i in range(n-2):
                if lows[i+2] > highs[i]:
            fvgs.append({
                'type': 'bullish',
                'date': dates[i+1],
                'high': lows[i+2],
                'low': highs[i],
                'filled': False,
                'desc': f"看涨FVG ¥{highs[i]:.2f}-¥{lows[i+2]:.2f}"
            })
                if highs[i+2] < lows[i]:
            fvgs.append({
                'type': 'bearish',
                'date': dates[i+1],
                'high': lows[i],
                'low': highs[i+2],
                'filled': False,
                'desc': f"看跌FVG ¥{highs[i+2]:.2f}-¥{lows[i]:.2f}"
            })
    
    return fvgs[-4:]


def find_bos_choch(dates, closes, highs, lows, window=5):
    """
    BOS（Break of Structure）：结构突破，趋势延续信号
    CHoCH（Change of Character）：性格改变，趋势反转信号
    
    BOS：价格突破前一个摆动高/低点，方向不变
    CHoCH：价格反向突破，趋势可能反转（类似缠论的段转折）
    """
    signals = []
    n = len(closes)
    
        swing_highs = [i for i in range(window, n-window)
                   if highs[i] == max(highs[i-window:i+window+1])]
    swing_lows  = [i for i in range(window, n-window)
                   if lows[i]  == min(lows[i-window:i+window+1])]
    
    if len(swing_highs) >= 2:
                sh1, sh2 = swing_highs[-2], swing_highs[-1]
        if highs[sh2] > highs[sh1]:
            signals.append({'type':'BOS_up', 'date':dates[sh2],
                'msg':f"BOS↑ 新高¥{highs[sh2]:.2f}>前高¥{highs[sh1]:.2f} 趋势延续"})
        else:
            signals.append({'type':'CHoCH_down', 'date':dates[sh2],
                'msg':f"CHoCH↓ 高点降低¥{highs[sh2]:.2f}<¥{highs[sh1]:.2f} 趋势可能反转"})
    
    if len(swing_lows) >= 2:
        sl1, sl2 = swing_lows[-2], swing_lows[-1]
        if lows[sl2] < lows[sl1]:
            signals.append({'type':'BOS_down', 'date':dates[sl2],
                'msg':f"BOS↓ 新低¥{lows[sl2]:.2f}<前低¥{lows[sl1]:.2f} 趋势延续"})
        else:
            signals.append({'type':'CHoCH_up', 'date':dates[sl2],
                'msg':f"CHoCH↑ 低点抬高¥{lows[sl2]:.2f}>¥{lows[sl1]:.2f} 趋势可能反转"})
    
    return signals


def smc_summary(dates, opens, closes, highs, lows, p):
    """SMC综合判断，输出关键支撑/压力位"""
    obs = find_order_blocks(dates, opens, closes, highs, lows)
    fvgs = find_fvg(dates, highs, lows)
    bos  = find_bos_choch(dates, closes, highs, lows)
    
        near_bull_ob = [o for o in obs if o['type']=='bullish' and o['low']<p<o['high']*1.05]
    near_bear_ob = [o for o in obs if o['type']=='bearish' and o['low']*0.95<p<o['high']]
    near_bull_fvg= [f for f in fvgs if f['type']=='bullish' and f['low']<p<f['high']*1.05]
    
    lines = ["**SMC分析:**"]
    
        for s in bos[-2:]:
        lines.append(f"  {s['msg']}")
    
        if near_bull_ob:
        o=near_bull_ob[-1]
        lines.append(f"  📦 价格在看涨OB内 ¥{o['low']:.1f}-¥{o['high']:.1f} → 支撑区域")
    if near_bear_ob:
        o=near_bear_ob[-1]
        lines.append(f"  📦 价格在看跌OB内 ¥{o['low']:.1f}-¥{o['high']:.1f} → 压力区域")
    if near_bull_fvg:
        f=near_bull_fvg[-1]
        lines.append(f"  🔲 价格在看涨FVG内 ¥{f['low']:.1f}-¥{f['high']:.1f} → 缺口支撑")
    
        if obs:
        lines.append(f"  最近OB: {' | '.join(o['desc'] for o in obs[-3:])}")
    
    return '\n'.join(lines)



def volume_price_analysis(closes, highs, lows, vols):
    """
    量价综合分析：
    - 缩量突破：假信号概率高
    - 放量突破：真信号
    - 放量顶部（量价背离）：见顶预警
    - 缩量回调：健康整理
    """
    if len(closes) < 20: return "数据不足"
    
    p = closes[-1]
    vol_ma20 = statistics.mean(vols[-20:])
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 > 0 else 1
    
        pct5 = (closes[-1]/closes[-6]-1)*100 if len(closes)>=6 else 0
        recent_high = max(highs[-10:-1]) if len(highs)>=10 else highs[-1]
    recent_low  = min(lows[-10:-1]) if len(lows)>=10 else lows[-1]
    
        obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:   obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i-1]: obv.append(obv[-1] - vols[i])
        else:                          obv.append(obv[-1])
    obv_ma5  = statistics.mean(obv[-5:])
    obv_now  = obv[-1]
    obv_trend = "上行" if obv_now > obv_ma5 else "下行"
    
    signals = []
    
        if highs[-1] > recent_high:
        if vol_ratio > 1.5:
            signals.append(f"✅ 放量突破({vol_ratio:.1f}x) → 真突破概率高")
        else:
            signals.append(f"⚠️ 缩量突破({vol_ratio:.1f}x) → 假突破警惕")
    
    if lows[-1] < recent_low:
        if vol_ratio > 1.5:
            signals.append(f"⚠️ 放量跌破({vol_ratio:.1f}x) → 真破位，止损")
        else:
            signals.append(f"🟡 缩量跌破({vol_ratio:.1f}x) → 可能是洗盘")
    
        if pct5 > 3 and vol_ratio < 0.7:
        signals.append(f"⚠️ 价涨量缩({vol_ratio:.1f}x) → 上涨乏力，拉高出货嫌疑")
    
    if pct5 < -3 and vol_ratio < 0.7:
        signals.append(f"✅ 价跌量缩({vol_ratio:.1f}x) → 健康回调，卖压不重")
    
        if pct5 > 5 and obv_trend == "下行":
        signals.append(f"🔴 OBV背离：价涨但OBV{obv_trend} → 主力出货")
    elif pct5 < -5 and obv_trend == "上行":
        signals.append(f"🟢 OBV底背离：价跌但OBV{obv_trend} → 主力吸筹")
    
    result = f"**量价分析:** vol_ratio={vol_ratio:.2f}x  OBV={obv_trend}  近5日{pct5:+.1f}%\n"
    result += '\n'.join(f"  {s}" for s in signals) if signals else "  无异常信号"
    return result



SECTOR_INDEX_MAP = {
        '半导体设备': 'sh000688',       'CPO':        'sz399006',       'AI服务器':   'sz399006',
    '新能源':     'sz399006',
    'PCB':        'sh000300',       '封测':       'sh000688',
    '变压器':     'sh000300',
    '稀土':       'sh000300',
    '机器人':     'sz399006',
}

def market_resonance(code, sector, d_stock, c_stock,
                     d_market=None, c_market=None,
                     d_sector=None, c_sector=None):
    """
    多市场共振分析：
    个股方向 × 板块方向 × 大盘方向 → 信号可靠性
    
    三者同向 → 信号最强
    仅个股 → 可能是板块轮动，谨慎
    个股逆市 → 降低仓位
    """
    def trend(closes, n=5):
        if not closes or len(closes)<n+1: return 0
        return (closes[-1]/closes[-n-1]-1)*100
    
    def ma_pos(closes):
        if len(closes)<20: return "未知"
        ma20=statistics.mean(closes[-20:])
        return "上方✅" if closes[-1]>ma20 else "下方⚠️"
    
    stock_trend  = trend(c_stock)
    market_trend = trend(c_market) if c_market else None
    sector_trend = trend(c_sector) if c_sector else None
    
    stock_pos  = ma_pos(c_stock)
    market_pos = ma_pos(c_market) if c_market else "—"
    sector_pos = ma_pos(c_sector) if c_sector else "—"
    
    lines = ["**多市场共振:**"]
    lines.append(f"  个股 {code}: 5日{stock_trend:+.1f}%  MA20{stock_pos}")
    if market_trend is not None:
        lines.append(f"  大盘:     5日{market_trend:+.1f}%  MA20{market_pos}")
    if sector_trend is not None:
        lines.append(f"  板块({sector}): 5日{sector_trend:+.1f}%  MA20{sector_pos}")
    
        directions = [stock_trend]
    if market_trend is not None: directions.append(market_trend)
    if sector_trend is not None: directions.append(sector_trend)
    
    positives = sum(1 for d in directions if d > 0)
    negatives = sum(1 for d in directions if d < 0)
    
    if len(directions) >= 2:
        if positives == len(directions):
            lines.append(f"  🟢 三向共振向上 → 信号最强，可加仓")
        elif negatives == len(directions):
            lines.append(f"  🔴 三向共振向下 → 不抄底，等反转")
        elif positives > negatives:
            lines.append(f"  🟡 偏多但不共振 → 轻仓，信号折半")
        else:
            lines.append(f"  🟠 偏空但个股逆市 → 等大盘配合")
    
    return '\n'.join(lines)



def wyckoff_phase(closes, highs, lows, vols, lookback=60):
    """
    威科夫三大阶段 (对齐 WyckoffTradingAgent v4):
    Accumulation: 底部吸筹 — 低位横盘 + MA gap ≤8% + 量能萎缩
    Markup:       主升浪   — MA50/MA200 金叉 + 上升趋势 + bias_200 > -10%
    Distribution: 派发阶段 — 高位 bias_200 > 15% + 缩量 + 派发 sub-event

    注: 旧五阶段 A/B/C/D/E 已废弃, 不存在独立的 D/E 阶段
    Spring 是 Markup 阶段的 sub-event, 不是独立阶段
    """
    n = min(len(closes), lookback)
    c = closes[-n:]; h = highs[-n:]; l = lows[-n:]; v = vols[-n:]
    p = c[-1]

    hi = max(h); lo = min(l)
    range_pct = (hi-lo)/lo*100

    pos_pct = (p-lo)/(hi-lo)*100 if hi>lo else 50

    vol_ma20 = statistics.mean(v[-20:]) if len(v)>=20 else 1
    recent_vol = statistics.mean(v[-5:])
    vol_ratio = recent_vol / vol_ma20

    # bias_200: 现价相对200日均线偏离
    ma200 = statistics.mean(closes[-200:]) if len(closes)>=200 else statistics.mean(closes)
    ma50  = statistics.mean(closes[-50:])  if len(closes)>=50  else statistics.mean(closes)
    bias_200 = (p / ma200 - 1) * 100

    # MA gap: |MA50-MA200|/MA200
    ma_gap = abs(ma50 - ma200) / ma200 * 100

    first_half = c[:n//2]; second_half = c[n//2:]
    slope = (statistics.mean(second_half) / statistics.mean(first_half) - 1) * 100

    lines = [f"**威科夫阶段分析:** 区间¥{lo:.1f}-¥{hi:.1f} ({range_pct:.0f}%) 当前位置{pos_pct:.0f}% bias_200={bias_200:+.1f}%"]

    if bias_200 > 30 and vol_ratio < 0.5:
        lines.append(f"  🔴 Distribution (派发): bias_200={bias_200:.0f}%>30% + 缩量{vol_ratio:.2f}x → 主力高位派发，减仓")
    elif bias_200 > 15 and vol_ratio < 0.7:
        lines.append(f"  🟠 Distribution (弱信号): bias_200={bias_200:.0f}%>15% + 量能萎缩 → 关注派发迹象")
    elif ma50 >= ma200 * 0.95 and slope > 0 and bias_200 > -15:
        lines.append(f"  🟢 Markup (主升浪): MA50/MA200 {'金叉' if ma50>=ma200 else '趋近金叉'} + 上升趋势slope={slope:+.1f}%")
    elif ma_gap <= 8 and vol_ratio < 0.75 and pos_pct < 45:
        lines.append(f"  🔵 Accumulation (吸筹): MA gap={ma_gap:.1f}%≤8% + 缩量{vol_ratio:.2f}x + 低位{pos_pct:.0f}%")
    elif bias_200 < -5 and pos_pct < 30:
        lines.append(f"  🔵 Accumulation (弱信号/大跌后): bias_200={bias_200:.0f}% + 低位{pos_pct:.0f}% → 可能底部吸筹")
    else:
        lines.append(f"  ⬜ 阶段不明确 → 等待更清晰的量价信号 (pos={pos_pct:.0f}% slope={slope:+.1f}% gap={ma_gap:.1f}%)")

    lines.append(f"  量能比: {vol_ratio:.2f}x（>1.5放量 / <0.7缩量）")

    return '\n'.join(lines)



def supplement_analysis(code, name, sector,
                        dates, opens, closes, highs, lows, vols,
                        d_market=None, c_market=None,
                        d_sector_idx=None, c_sector_idx=None):
    """
    四种补充分析综合输出
    在 format_chan_output 之后调用
    """
    lines = ["\n        lines.append(smc_summary(dates, opens, closes, highs, lows, closes[-1]))
    lines.append("")
    
        lines.append(volume_price_analysis(closes, highs, lows, vols))
    lines.append("")
    
        lines.append(market_resonance(code, sector,
                                   dates, closes,
                                   d_market, c_market,
                                   d_sector_idx, c_sector_idx))
    lines.append("")
    
        lines.append(wyckoff_phase(closes, highs, lows, vols))
    
    return '\n'.join(lines)

###
> **必须在缠论分析之前运行**，根据状态决定用哪些方法。
> 三指标打分（0-9）→ 主升浪/过渡回调/震荡下跌 → 方法优先级矩阵

```python
"""
市场状态定量判断模块 v1.0
三指标组合打分（0-9分）→ 主升浪/过渡回调/震荡下跌
"""
import statistics

def seg_red_area_ms(seg, hist, dt2i):
    if seg['sdt'] not in dt2i or seg['edt'] not in dt2i: return 0.0
    i1=dt2i[seg['sdt']]; i2=dt2i[seg['edt']]
    return sum(x for x in hist[i1:i2+1] if x>0)

def market_state(closes, highs, lows, segs, hist, dt2i):
    """
    定量判断市场状态，返回:
    {
        'score': 0-9,
        'state': '主升浪'/'过渡回调'/'震荡下跌',
        'method': 推荐方法列表,
            '✅主用'/'⚠️辅助'/'❌禁用' 对应 缠论/威科夫/SMC/量价
        'detail': 三指标明细,
    }
    """
    score = 0
    detail = []

        up_segs = [s for s in segs if s['sst']=='B'][-3:]
    area_score = 0
    if len(up_segs) >= 2:
        areas = [seg_red_area_ms(s, hist, dt2i) for s in up_segs]
        if len(areas) >= 3 and areas[-1] > areas[-2] > areas[-3]:
            area_score = 3
            detail.append(f"MACD面积持续扩张({areas[-3]:.0f}→{areas[-2]:.0f}→{areas[-1]:.0f}) +3")
        elif len(areas) >= 2 and areas[-1] > areas[-2]:
            area_score = 2
            detail.append(f"MACD面积扩张({areas[-2]:.0f}→{areas[-1]:.0f}) +2")
        elif len(areas) >= 2 and areas[-1] > areas[-2]*0.5:
            area_score = 1
            detail.append(f"MACD面积平稳({areas[-2]:.0f}→{areas[-1]:.0f}) +1")
        else:
            detail.append(f"MACD面积收缩/无 +0")
    else:
        detail.append("段不足，MACD面积不可算 +0")
    score += area_score

        ma20_score = 0
    if len(closes) >= 40:
        ma20_now = statistics.mean(closes[-20:])
        ma20_old = statistics.mean(closes[-40:-20])
        slope = (ma20_now/ma20_old - 1)*100 if ma20_old > 0 else 0
        if slope > 5:
            ma20_score = 3
            detail.append(f"MA20斜率+{slope:.1f}%(强上升) +3")
        elif slope > 2:
            ma20_score = 2
            detail.append(f"MA20斜率+{slope:.1f}%(温和上升) +2")
        elif slope > 0:
            ma20_score = 1
            detail.append(f"MA20斜率+{slope:.1f}%(微上升) +1")
        elif slope > -2:
            ma20_score = 0
            detail.append(f"MA20斜率{slope:.1f}%(横盘) +0")
        else:
            ma20_score = 0
            detail.append(f"MA20斜率{slope:.1f}%(下行) +0")
    score += ma20_score

        gain_score = 0
    if len(closes) >= 61:
        gain60 = (closes[-1]/closes[-61] - 1)*100
        if gain60 > 50:
            gain_score = 3
            detail.append(f"60日涨幅{gain60:+.0f}%(强) +3")
        elif gain60 > 20:
            gain_score = 2
            detail.append(f"60日涨幅{gain60:+.0f}%(中) +2")
        elif gain60 > 5:
            gain_score = 1
            detail.append(f"60日涨幅{gain60:+.0f}%(弱) +1")
        elif gain60 > -10:
            gain_score = 0
            detail.append(f"60日涨幅{gain60:+.0f}%(平) +0")
        else:
            gain_score = 0
            detail.append(f"60日涨幅{gain60:+.0f}%(下跌) +0")
    score += gain_score

        if score >= 7:
        state = '主升浪'
        emoji = '🚀'
        methods = {
            '缠论背驰':   '❌ 禁用（面积扩张，背驰不触发）',
            '缠论中枢':   '✅ 用（判断位置，不做背驰）',
            '威科夫Markup': '✅ 主用（Markup主升浪中缩量回调是买点）',
            'MA20偏离':   '✅ 主用（>20%开始止盈）',
            'SMC-OB':     '⚠️ 辅助（回踩OB是加仓点）',
            '量价确认':   '✅ 放量才是真突破',
        }
    elif score >= 4:
        state = '过渡回调'
        emoji = '🔄'
        methods = {
            '缠论背驰':   '✅ 主用（底背驰=1买，顶背驰=1卖）',
            '缠论中枢':   '✅ 主用（中枢上下是支撑压力）',
            '威科夫Accumulation': '✅ 辅助确认（Accumulation末段配合缠论）',
            'MA20偏离':   '⚠️ 参考（非主升浪有效）',
            'SMC-OB':     '⚠️ 辅助（找回踩买点）',
            '量价确认':   '✅ 必须（缩量回调才健康）',
        }
    else:
        state = '震荡下跌'
        emoji = '⬇️'
        methods = {
            '缠论背驰':   '⚠️ 谨慎（震荡市假信号多）',
            '缠论中枢':   '✅ 用（找震荡区间边界）',
            '威科夫Accumulation': '⚠️ 等 Accumulation_C 末段再用（等底部吸筹信号）',
            'MA20偏离':   '❌ 无意义（横盘无偏离）',
            'SMC-OB':     '✅ 主用（OB是震荡市核心支撑）',
            '量价确认':   '✅ 缩量=洗盘，放量=真破位',
        }

    return {
        'score': score,
        'state': state,
        'emoji': emoji,
        'methods': methods,
        'detail': detail,
        'area_score': area_score,
        'ma20_score': ma20_score,
        'gain_score': gain_score,
    }


def format_market_state(code, name, result, p):
    """格式化输出，放在缠论分析上方"""
    r = result
    lines = []
    lines.append(f"    lines.append(f"  (MACD面积{r['area_score']}/3 + MA20斜率{r['ma20_score']}/3 + 60日涨幅{r['gain_score']}/3)\n")

    lines.append("**三指标明细:**")
    for d in r['detail']:
        lines.append(f"  - {d}")

    lines.append("\n**分析方法优先级:**")
    lines.append("| 方法 | 本状态下的用法 |")
    lines.append("|------|--------------|")
    for method, usage in r['methods'].items():
        lines.append(f"| {method} | {usage} |")

    lines.append("")
    return '\n'.join(lines)

print("市场状态模块加载完成")

```

**调用方式（在 format_chan_output 之前）：**
```python
# 读市场状态：从 data.analysis 读（不用 exec /tmp/*.py）
scene = (data.analysis or {}).get('scene')
score = (data.analysis or {}).get('total_score', 0)
state_section = f"场景: {scene}  总分: {score:.2f}"
```

print("补充分析模块加载完成")

"""
持仓状态矩阵输出 v1.0
两张表：① 状态+5模型矩阵  ② 买卖点+新信号
"""
import statistics

def calc_obv_trend(closes, vols):
    """OBV趋势：上行/下行/中性"""
    if len(closes)<20 or len(vols)<20: return "—"
    obv=[0]
    for i in range(1,len(closes)):
        if closes[i]>closes[i-1]: obv.append(obv[-1]+vols[i])
        elif closes[i]<closes[i-1]: obv.append(obv[-1]-vols[i])
        else: obv.append(obv[-1])
    obv_ma5=statistics.mean(obv[-5:])
    return "↑" if obv[-1]>obv_ma5*1.01 else ("↓" if obv[-1]<obv_ma5*0.99 else "→")

def calc_vol_ratio(vols):
    if len(vols)<21: return 1.0
    return vols[-1]/statistics.mean(vols[-21:-1]) if statistics.mean(vols[-21:-1])>0 else 1.0

def resonance_label(stock_c, market_c, sector_c):
    """多市场共振：三向判断"""
    def trend5(c): return (c[-1]/c[-6]-1)*100 if len(c)>=6 else 0
    st=trend5(stock_c); mt=trend5(market_c) if market_c else 0; sect=trend5(sector_c) if sector_c else 0
    sources=[st]
    if market_c: sources.append(mt)
    if sector_c: sources.append(sect)
    pos=sum(1 for x in sources if x>0); neg=sum(1 for x in sources if x<0)
    if pos==len(sources): return "🟢三向↑"
    if neg==len(sources): return "🔴三向↓"
    if pos>neg: return "🟡偏多"
    return "🟠偏空"

def wyckoff_label(closes, highs, lows, vols):
    """威科夫阶段简标 (三阶段: Accumulation/Markup/Distribution, 对齐 WyckoffTradingAgent v4)"""
    if len(closes)<30: return "—"
    n=min(60,len(closes))
    hi=max(highs[-n:]); lo=min(lows[-n:])
    pos=(closes[-1]-lo)/(hi-lo)*100 if hi>lo else 50
    vr=statistics.mean(vols[-5:])/statistics.mean(vols[-20:]) if len(vols)>=20 and statistics.mean(vols[-20:])>0 else 1
    slope=(statistics.mean(closes[-20:])/statistics.mean(closes[-40:-20])-1)*100 if len(closes)>=40 else 0
    ma200=statistics.mean(closes[-200:]) if len(closes)>=200 else statistics.mean(closes)
    ma50 =statistics.mean(closes[-50:])  if len(closes)>=50  else statistics.mean(closes)
    bias_200=(closes[-1]/ma200-1)*100
    ma_gap=abs(ma50-ma200)/ma200*100
    if bias_200>30 and vr<0.5: return "Distribution"
    if bias_200>15 and vr<0.7: return "Dist弱"
    if ma50>=ma200*0.95 and slope>0 and bias_200>-15: return "Markup"
    if ma_gap<=8 and vr<0.75 and pos<45: return "Accumulation"
    if bias_200<-5 and pos<30: return "Accum弱"
    return "?"

def smc_label(closes, highs, lows, opens):
    """SMC简标：有无OB支撑/压力"""
    p=closes[-1]
        n=min(50,len(closes))
    bull_obs=[]; bear_obs=[]
    for i in range(1,n-2):
        if closes[i]<opens[i] and any(highs[j]>highs[i] for j in range(i+1,min(i+8,n))):
            bull_obs.append((lows[i],highs[i]))
        if closes[i]>opens[i] and any(lows[j]<lows[i] for j in range(i+1,min(i+8,n))):
            bear_obs.append((lows[i],highs[i]))
    near_bull=[o for o in bull_obs[-5:] if o[0]<p<o[1]*1.05]
    near_bear=[o for o in bear_obs[-5:] if o[0]*0.95<p<o[1]]
    if near_bull: return f"✅OB支撑¥{near_bull[-1][0]:.0f}"
    if near_bear: return f"⚠️OB压力¥{near_bear[-1][1]:.0f}"
    return "⚠️无近OB"

def format_matrix(stocks_data):
    """
    stocks_data: list of dict，每个dict包含：
    {
        'code','name','score','state','emoji',
        'chan_label',          'wyckoff',             'smc',                 'vol_label',           'resonance',           'buy1','buy2','sell1','sell2',          'findings',        }
    """
    lines=[]
    lines.append("##
        lines.append("**① 状态 + 分析方法矩阵**\n")
    lines.append("| 代码 | 名称 | 分/状态 | 缠论背驰 | 威科夫 | SMC | 量价OBV | 共振 |")
    lines.append("|------|------|---------|---------|--------|-----|---------|------|")
    for s in stocks_data:
        score_str = f"{s['score']}/9{s['emoji']}{s['state'][:2]}"
        lines.append(
            f"| {s['code']} | {s['name'][:5]} | {score_str} "
            f"| {s['chan_label']} | {s['wyckoff']} | {s['smc'][:8]} "
            f"| {s['vol_label']} | {s['resonance']} |"
        )
    lines.append("")

        lines.append("**② 买卖点 + 今日新信号**\n")
    lines.append("| 代码 | 名称 | 1买 | 2买 | 1卖 | 2卖 | 今日新信号 |")
    lines.append("|------|------|-----|-----|-----|-----|-----------|")
    for s in stocks_data:
        sig_str = ' '.join(f"{f['level']}{f['type'][:6]}" for f in s['findings'][:2]) if s['findings'] else "✅无"
        b1=s.get('buy1','—'); b2=s.get('buy2','—')
        s1=s.get('sell1','—'); s2=s.get('sell2','—')
        if b1!='—' or s1!='—' or s['findings']:
            lines.append(f"| {s['code']} | {s['name'][:5]} | {b1} | {b2} | {s1} | {s2} | {sig_str} |")

    lines.append("")
    return '\n'.join(lines)

print("matrix_output 模块加载完成")
