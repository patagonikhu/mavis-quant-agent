# AGENT_MEMORY.md — Mavis + Claude Code 共享 Memory (真源)

> 📍 **位置**: 项目根 `docs/AGENT_MEMORY.md`
> 🎯 **用途**: 跨工具共享 memory — Claude Code 读这个,mavis 也读这个
> 🔄 **跨机器**: git 跟踪, `git push/pull` 自动同步
>
> ## 使用规则
> 1. **新规则加在文末** (按时间倒序, 最新的在最上面)
> 2. **mavis 端 mirror**: 我每次更新本文件后,会同步到 `~/.minimax/agents/mavis/memory/MEMORY.md`
> 3. **不要手动改 `~/.minimax/`** — 那只是 mavis 启动用的镜像, 真源是本文件
> 4. **跨机器**: 换电脑 `git pull` 拉最新版本
> 5. **CLAUDE.md 引用**: 项目 `CLAUDE.md` 引用本文件, 让 Claude Code 自动读

---

## 📅 2026-07-24 依赖固化 (解决 "每次忘装 tushare")

### 🎯 状态变更: Tushare **已接入** (2026-07-24)
- **变更**: 2026-07-22 用户"暂缓买积分" → 2026-07-24 用户已加 token 到 `.env` (但包未装)
- **当前真实状态**: `tushare-1.4.29` 已装, 验证 dump 成功 (300274 8.1s 跑通)
- **Akshare: 2026-07-24 已彻底卸载 + 全部代码/文档痕迹清理, 纯 tushare 路径**
- **Akshare: 不需要** (项目纯 tushare 路径, 2026-07-24 用户确认)
- **关键修正**: 不要默认"tushare 没接入" — 每次跑数据工具时直接尝试, 失败再装

### 🎯 自动依赖检查机制 (2026-07-24 新增)
- **工具**: `tools/check_deps.py` — 静默 import 检查 + 自动 pip install
- **挂载点**:
  - `tools/ensure_fresh.py` 开头 (单只/批量刷新入口)
  - `tools/dump_data.py` 开头 (直接 dump 入口)
- **效果**: 以后跑数据工具不再撞 "No module named 'tushare'"
- **幂等**: 已装就跳过, 不刷屏; 装失败不阻塞主流程
- **测试**: `python3 tools/check_deps.py` 输出 `READY` 或 `FAILED`

### 🎯 LLM 行为规则 (本项目, 2026-07-24 固化)
- **看到 `No module named 'X'` 错误时**: 不要甩给用户, 直接 `pip install X` 修
- **跑 dump / render 失败时**: 第一反应是装包/查 token, 不是 "环境问题"
- **`.env` 已有 token 但包装没装** = 常见场景, 默认处理

---

## 📅 2026-07-22 投资分析关键经验

### 🎯 Tushare Pro 接入方案 (待接入, 用户暂缓 2026-07-22)
- **价格表** (从 mhtml 提取):
  - 2000 积分 **¥200/年** — 解锁 weekly/monthly + 财务三大表 + 龙虎榜 + 北向 + 融资融券 + 指数成分股 (项目够用)
  - 5000 积分 ¥500/年 — 高频
  - 10000 积分 ¥1000/年 — 量化级
- **建议**: 先开 **2000 积分 ¥200/年** (性价比最高, 解决项目 6 项缺数据)
- **接入计划** (用户决定时执行):
  1. `pip install tushare`
  2. `tools/fetch/tushare_fetcher.py` (9 个函数)
  3. 集成 `dump_data.py` + `render_report.py`
  4. 跑阳光电源 + 京东方A 验证
- **不要买**: A股分钟RT (¥1000/月太贵), 港股/美股/期货 (项目只做 A 股), 公告/新闻 (LLM WebSearch 替代)

### 🎯 数据源矩阵 (2026-07-24 09:53 重测, 实测更新)
- **Tushare Pro (2000 积分档)** ✅ **单一权威源** — K线/行情/fflow/EPS/财务/股本全可用
  - K线: `tushare_fetcher.get_daily/get_weekly/get_monthly`
  - fflow: `tushare_fetcher.get_fund_flow` (2000 积分档 24h 稳定)
  - EPS/财报: `tushare_fetcher.get_fina_indicator/get_income`
  - 实时价: `tushare_fetcher.get_daily_basic` (pe_ttm/pb/turnover_rate)
- **push2his** ❌ WAF 拒接 (HTTP 000) — 任何时候都拿不到
- **push2delay** ⚠️ **曾经是首选, 2026-07-24 09:53 实测被限流 (rc=102)**
  - 老规则: 24h 稳定 200, 响应 38-121ms — **已失效, 别再信 memory 老版本**
  - 建议: 不要再当首选, 限流时连 fflow daykline 都不返回
  - 旧 URL 留作参考: `https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}&klt=101&lmt=1`
  - 备用方案: Tushare.moneyflow (24h 稳定, 数据更全)
- **push2 实时行情** ❌ push2 主域拒接, push2delay 限流 → 改用 Tushare.daily_basic
- **qtimg (腾讯)** ⚠️ 实时价偶尔可用, 但 2026-07-24 高频请求会触发 WAF
- **web.ifzq (腾讯)** ⚠️ K线 API 2026-07-24 09:53 实测 WAF 拒接 (空响应) — **不再是稳定源**
  - 之前写"24h 200"是错的, 实际有 WAF 限流
  - 备用: Tushare.daily (qfq 用不复权也能算大部分技术指标)
- **datacenter-web.eastmoney.com** ✅ 24h 200 — EPS/财报 (Tushare 优先, 这个留作 cross-check)
- **OBV 派生** ✅ 24h 可算 (基于 K线) — 仅方向, 非真实主力净额
- **雪球/网易/同花顺/新浪** ❌ 全部拒直连 (cookie/timeout/404)

### 🎯 决策原则 (2026-07-24 固化, 别再混淆)
1. **新代码统一走 Tushare** — 不要在新增/修复代码里加 push2/ifzq/qtimg 的 curl
2. **现存调用不要乱删** — 旧代码里的 ifzq/qtimg/datacenter 留着, 当 Tushare 失败时的次选
3. **删/留的理由 = 实测是否可用**, 不是"原则违规"
4. **memory 必须跟现实同步** — 每次测出新结论, 立刻更新 AGENT_MEMORY.md
5. **当 chat 里说"违规/违反原则"时, 一定先确认 memory 是不是真的这么写** — 防止拿陈旧规则当理由

### 🎯 fflow 组合方案 (分 column 升级 2026-07-22)
- 工具: `tools/fetch/tushare_fetcher.get_fund_flow_combined(code, days=10)`
- 返回结构: `data_columns.real` (push2delay 真实, 亿元) + `data_columns.derived` (OBV 派生, 万手方向)
- 报告渲染: 两个子表分开展示, **不混行** (避免 OBV 派生被误读为真实数字)
- 逻辑: push2delay 拿当日真实 (亿元) + OBV 派生拿历史方向 (万手/日)
- 优势: 解决 push2delay 只返回 1 天的限制
- 实战: 阳光电源 7-21 真实 +4.05亿流入 + 超大单 +3.33亿 → 触底反弹信号
  - ⚠️ 修正: OBV 派生单独判断"出货"是错的, push2delay 真实 + 组合方案才准
  - ⚠️ OBV 派生不能算"主力净额" (量纲不同: 万手 vs 亿元) — 只能算"量价方向"

### 🎯 OBV 派生准确度 (2026-07-22 实测)
- **单日主力方向**: ~50% (跟抛硬币一样) — **不替代真实 fflow**
- **多日趋势方向**: ~60%
- **数字精度 (亿元)**: 0% (OBV 是"万手",不是"亿元",量纲不同)
- **机构行为识别**: ~30% (看不出大单/超大单)
- **3 个能用场景**: 量价背离 / 多日趋势 / 没有真实数据时的兜底
- **不能用的场景**: 单日主力方向 / 主力净额数字 / 机构行为

### 🎯 数据可靠性原则 (5 层防线)
1. **来源层**: 选权威 + 24h 200 + 公开 API (东方财富 / 腾讯 / datacenter)
2. **传输层**: ≥3 层 fallback (push2his → push2delay → OBV 派生)
3. **解析层**: 字段映射要校验 (单位/编码/日期格式)
4. **计算层**: PEG/DCF L/中枢算完要异常检查 (PEG<0 或 >10 异常)
5. **呈现层**: 报告标注"push2delay 真实"vs"OBV 派生", 真实优先

### 🎯 报告必备标注
```markdown
✅ push2delay 真实: 主力 +4.05亿 (亿元, 准确)
⚠️ OBV 派生: 历史方向 (万手, 仅参考)
⏰ 数据时间戳: 2026-07-21 (距今 14h)
```
**为什么**: 之前阳光电源报告"出货 -14.3亿"无标注 → 错判; 加上 push2delay 真实标注后 → 真相浮出

### 🎯 24h 数据可用性矩阵
✅ **24h 全可用**: 历史 K线 (250根) / EPS / 财报 / 实时价 (昨收) / push2delay 当日 fflow / push2delay 实时行情 / 板块指数 / 股本
❌ **24h 不能拿**:
- 当日盘中 OHLCV (9:30 前空)
- 当日分笔成交 (9:30-15:00 才有)
- 当日量比/换手率 (依赖当日成交)
- push2his 历史 fflow (WAF 永远拒接)
- 雪球 fflow (需 cookie)
- 北向资金/龙虎榜/融资融券 (Tushare 2000 积分档, 按需接入)

### 🎯 9:30 之前的用法
9:30 之前能拿昨日所有数据 (K线/价格/EPS/OBV 派生 fflow), 用于今日开盘前策略准备:
1. 跑 `get_fund_flow_combined('300274')` → 拿昨日 OBV + 今日(如果已开盘) push2delay 真实
2. 跑 `analyze_three_levels` → 算 60 分中枢 + 背驰
3. 看板块 MA20 偏离 → 判断市况
4. 综合判断 → 决定 9:30 开盘挂单

### 🎯 报告输出顺序 (CLAUDE.md 红字硬约束, 2026-07-22 重排)
1️⃣ 缠论三要素 (中枢+背驰+止跌) — 一等公民 ← **最前, 不能挪**
2️⃣ 4 个缠论补充策略 (SMC-OB+量价+威科夫+多市场共振) — 一等公民 ← **紧跟 1️⃣**
3️⃣ 市场状态定量 (三指标 0-9 分 / 板块过热) — 二等
4️⃣ 大盘+美股背景 — 二等 (跳过, 没数据)
5️⃣ PEG/DCF L (基本面对冲) — **必须在 1️⃣2️⃣ 之后!** 二等
6️⃣ 主力 fflow (push2delay + OBV 组合) — 验证
7️⃣ 三层仓位+买卖点 — 综合 (止盈/止损/退场/仓位/监控)

**实战模板** (阳光电源报告 2026-07-22 重排后):
```
## 📊 数据完整性
## EPS + 财务
## MA
## 技术指标
## 🧠 缠论三要素 (中枢+背驰+止跌)         ← 1️⃣
## 📐 缠论完整数据 (4 个级别)              ← 1️⃣
## 🔍 缠论补充 (4 方法)                   ← 2️⃣
## 基本面
## 4 套策略
## 投资四问
## T 框架
## 📈 板块过热预警                         ← 3️⃣
## 💰 PEG 实算                             ← 5️⃣
## 📊 DCF L 实算                           ← 5️⃣
## 🟢 主力分析 (fflow) 组合方案            ← 6️⃣
## 5 类 14 子信号
## 止盈/止损/退场/仓位/监控                 ← 7️⃣
```
---

### datalen 收敛配比 — 5方法×3周期 (2026-07-28 固化, 002371 实测)
Type: config_decision
**日常 dump 配比 250/156/200 (日/周/60分) — 5方法×3周期 实测收敛点**

| 周期 | 根数 | 覆盖 | 决策依据 |
|---|---|---|---|
| 日线 | 250 | 1 年 | 缠论段数 9, 威科夫/SMC 跟 750 根完全一致, 不浪费 |
| **周线** | **156** ⬆️ | 3 年 | 100 根只 1 段"波段不足", 156 根 9 段触发弱背驰 (从 0 → 信号) |
| 60分 | 200 | 10 周 | 80/100 根 SMC 看错方向 (顶部 vs 底部), 200 是甜点 |

**回测模式**: 750/156/3795 (3年全量, 60分吃满新浪硬上限)

**实测耗时 (002371 北方华创)**: 单票全量 2.7-3s, 57 只 watchlist 4 并发 43s

**关键反直觉发现**:
- 60分 80/100/200/3795 根 缠论背驰判定**完全一致** (32%) — 80 根就够
- 但 60分 SMC 在 100 根看顶部, 200 根看底部 — **时间窗口决定 SMC 方向**
- 周线 100 根 = 段 1 "波段不足", 156 根 = 段 9 弱背驰 56-74% — **+56 根决定性**

**代码位置**: `config/project.yaml:data` (在 .gitignore, 本地私有)

### factor_history.py 字典字面量塞赋值 (2026-08-15 修复)
Type: bugfix
**症状:** `tools/analysis/factor_history.py:114` 报 `SyntaxError: ':' expected after dictionary key`
**原因:** 有人在 return { ... } 字典字面量里塞了 `pos_fs = result.factor_scores.get("position")` 这种赋值, Python 不允许
**修复:** 把两行 pos_fs / pos_raw 赋值提到 `return {` 之前 (line 113 之前)
**触发场景:** 任何 `compute_factor_history()` 调用 → 整个 factor_history 模块无法 import → batch 脚本全军覆没
**教训:** dump 路径修过但 factor_history 这种分析层文件没跑过就直接 push 了, 任何代码改动必须先 `python -c "import xxx"` smoke test

### md 报告残留旧报错 (2026-08-15)
Type: workflow_bug
**症状:** `docs/analyze-*.md` 里"📈 因子历史走势"section 显示 `❌ 历史计算失败: ':' expected ...` 但代码已修
**原因:** `tools/refresh_all.sh` 有 dump + render 两阶段,代码 bug 修之前 render 已跑完 57 份报告,bug 修后必须**重跑 render** 才能覆盖
**教训:** 任何 factor 改动 (factor_history / factor_scores / analysis_engine) 必须跟一次 `bash tools/refresh_all.sh` 重渲染, 不能只 dump
**检测命令:** `grep -c "历史计算失败" docs/analyze-*.md | grep -v ":0$"` 应当返回空

### refresh_all.sh smoke test + linter 兜底 (2026-08-15 v3 防护)
Type: workflow_decision
**触发背景:** factor_history.py 字典字面量塞赋值 bug → 57 份 md 残留"❌ 历史计算失败"

**两层防护 (v3 启用):**

1. **预防** — `tools/refresh_all.sh` 启动时 import 4 个核心模块, 失败立即 exit 1
   - 路径: `tools/refresh_all.sh` line 21-43
   - 检查: tools.analysis.factor_history / analysis_engine / tools.render.report_renderer / tools.batch.batch_summary
   - 任何 .py 改动 (factor / renderer / batch) 都会在第一秒被拦

2. **检测** — `tools/render/report_linter.py` 占位符检测加 "历史计算失败"
   - 路径: KEY_DATA_PATTERNS 下方 placeholder_patterns 列表
   - 任何 md 含"历史计算失败" → 自动追加 warning, 强制触发重渲染

**为什么需要两层:**
- smoke test 拦 .py 改坏的源码 (新 bug)
- linter 拦已经生成的脏 md (历史残留 / 跨环境同步)
- 任意一层失效另一层兜底

**验证记录:**
- 故意注入 syntax 错 → smoke test EXIT 1 ✅
- 假报告含残行 → linter 抓到 ✅
- 修复后刷 57 只 → 0 残行, batch_summary 57/57 读成功 ✅

### SMC 调参 v2 (2026-08-15) — lookback + 自适应 displacement
Type: config_decision
**症状:** SMC 找的 OB 距现价要么 -46% (太远挂不到) 要么 -1% (贴在脑门上), 没操作价值
**根因:**
  1. lookback=50 太短 (250 根 dump 里只覆盖 2-3 月)
  2. displacement_atr_mult=0.8 在寒武纪这种高波动股等于无过滤 (日 ATR~¥80, 0.8x=¥64 几乎总满足)

**改动:** `tools/factors/smc/analysis.py:smc_analysis()`
  - `lookback` 默认 50 → 120 (日线级), 周线/60m 由调用方传 200
  - `displacement_atr_mult` 改自适应: 取前 50 根 abs(close-open)/ATR 的 70% 分位
    - 高波动股自然阈值上移到 1.5-2.5x ATR
    - 低波动股自动放宽到 0.8-1.2x ATR
    - 调用方仍可传固定值 (回测用)
  - `max_ob_age_bars` 60 → 80 跟 lookback 配套

**验证 (5 只典型票):**
  - 002371 北方华创: OB支¥400 (-46%) → ¥453 (-36%) ✅
  - 300274 阳光电源: OB支¥113 (-3%, 伪) → OB压¥161 (+38%, 真压力) ✅
  - 300308 中际旭创: FVG支¥280 (-70%, 太远) → ¥494 (-47%, 可挂) ✅
  - 300748 金力永磁: OB支¥27 (-1%, 伪) → OB压¥34 (+26%) ✅
  - 688256 寒武纪: 抓出真实压力区 OB压¥1320 (+21%) ✅

**57 只全量 OB 距现价分布 (新参数):**
  - 无 OB: 50% (SMC 稀疏性, 跟市场状态有关)
  - <5% (伪 OB): 9% 5 只
  - 5-15% (可挂但要等回调): 14%
  - 15-30% (标准区间): 9%
  - >30% (高波动股/低价股 OB 天然宽, 需结合质量判断): 19% 11 只

**实战效果 (002371):** 综合判定 🟡 观察 → 🥈 标准建仓 (SMC 给的支撑位更合理)

**遗留问题:**
  - 11 只 >30% 的"远 OB" 大多是高波动股 (寒武纪/北方华创), 需进一步用 OB 的
    tested_count / 未被回测次数判断质量
  - SMC 跟缠论中枢没联动 → OB 是否落在中枢下沿 ±5% 内 = 强支撑信号 (待实现)

### SMC OB 胜率回测 (2026-08-15, 57 只 / 416 个 OB, 后续 20 根看表现)
Type: data_finding
**核心结论: tested_count 是 OB 质量的关键指标**

| 距现价 | OB数 | 止跌率 |
|---|---|---|
| <5% (贴在脑门) | 72 | 68% (跟噪声差不多) |
| **5-15% (甜区)** | 112 | **83%** ⭐ |
| 15-30% | 143 | 73% |
| >30% (远 OB) | 89 | 56% (接近随机) |

| tested_count | OB数 | 止跌率 |
|---|---|---|
| 0 (无测试) | 101 | 0% (定义问题, 形成后 20 根都没触到) |
| 1 | 74 | 98.6% |
| 2+ | 241 | 92.5% (强 OB) |
| < 2 (弱 OB) | 175 | 41.7% |

| 联动维度 | OB数 | 止跌率 |
|---|---|---|
| 落在已有缠论中枢 ±5% 内 | 54 | **79.6%** (用真中枢算法) |
| 不在中枢附近 | 362 | 69.9% |
| 联动增益 | — | **+9.7 百分点** |

**强 OB = tested_count ≥ 2, 止跌率 92.5% (vs 弱 OB 41.7%, 增益 +50.8pp)**

**工程化优先级:**
1. tested_count (核心, +50pp)
2. 距现价 5-15% (甜区, +15pp vs <5%)
3. 缠论中枢联动 (+10pp)

**待实现:** `tools/factors/smc/analysis.py:smc_analysis()` 加 `tested_count` 字段,
nearest_bull_ob 只保留 tested_count ≥ 2 的 (强 OB 优先)

### OBV 段背离 (60 日 15 日窗口多次确认) 工程化 (2026-08-15)
Type: feature
**问题:** 之前 `price_fflow_factor` 跟 `VolumeOBVFactor` 都不支持 asof,fflow/OBV 互斥 (二选一)
**改动:**
  1. `_obv_factor` 加 asof + 段背离多次确认 (60 日内不重叠 15 日窗口, 阈值 -2% / +3%)
  2. `VolumeOBVFactor.compute` 加 asof_date kwarg, 切片 fflow_data
  3. `price_fflow_factor` 主函数: fflow + OBV 并联, 输出 fflow+OBV 同向/矛盾信号
  4. `analysis_engine.VolumePriceStrategy.analyze` 传 dates+asof
  5. `report_renderer.py` 5 方法矩阵新增"量价 OBV 段背离"section

**为什么 15 日窗口而不是 20 日:**
  - 20 日窗口阈值 -3% / +5%: 57 只 0 触发
  - 15 日窗口阈值 -2% / +3%: 57 只 6 只强底背离 (10.5% 合理信号密度)

**核心数据点 (57 只扫描, 2026-08-15):**
  - 强底背离 (≥2/4 窗口): 几只 (具体每只数不一样)
  - 单次底背离 (1/4): 一些
  - 中际旭创 300308: 3/4 窗口底背离 + fflow 偏进货 = 双重确认
  - 北方华创 002371: 2/4 窗口底背离 + fflow 中性 = OBV 在吸筹 fflow 没看到

**输出字段 (vp_result 新增):**
  - obv_verdict: OBV 独立判定
  - obv_score: OBV 因子分
  - obv_div_bot_60d: 60 日内底背离窗口数
  - obv_div_top_60d: 60 日内顶背离窗口数
  - asof: 切片日期
  - signals 列表里追加 "✅ fflow+OBV 同向" / "⚠️ fflow vs OBV 矛盾"

**Smoke test:** `tools/refresh_all.sh` 第一行 import 4 个核心模块, 任何 .py 语法错立即 exit 1

### factor_history 表最后一列: 价格位置 → OBV(30d%) (2026-08-15)
Type: feature
**原因:** 价格位置 (close_pos_day/20, upper_shadow) 是静态位置标签, 没动态量价信息
**替代:** OBV 30 日累计 (净增量 / 30日总成交%), 每天算一次, 自动显示段背离标志

**改动:**
  1. `tools/analysis/factor_history.py`:
     - 新增 `_compute_obv_30d(ctx, asof_date)` 函数 — 截至当日 30 日 OBV 净增量
     - `_extract_row` 加 obv_30d_pct / obv_30d_strength / obv_30d_div 3 字段
     - `_extract_row` 签名加 ctx 参数 (因 OBV 需要 K 线)
     - ⚠️ 修 dict literal 塞赋值的 syntax bug (跟之前 factor_history.py 114 行同型)
  2. `tools/render/report_renderer.py`:
     - 表头 "价格位置" → "OBV(30d%)"
     - 行构造改用 `_obv_30d_str(row)`
     - 加 `_obv_30d_str` 渲染函数

**输出格式:** `+X.X% ↑吸筹/↓派发/— [底/顶背离]`
  例: `+11.8% ↑吸筹 底背离` / `-7.4% ↓派发` / `+2.4% —`

**验证 (002371 北方华创):**
  8/10 → 8/14 OBV 30d%: -6.6% → -11.6% → -7.4% → -5.5% → -0.2%
  → 主力派发 → 减弱 → 中性, 价格位置看不出的动态变化

**验证 (300308 中际旭创):**
  8/12 → 8/14 OBV 30d%: +2.4% — → +7.5% ↑吸筹 底背离 → +11.8% ↑吸筹 底背离
  → 8/12 后主力吸筹加速 (2.4% → 11.8%), 跟 60 分钟底背驰同步

**为什么 OBV 比价格位置好:**
  - 动态: 每天累计量价变化, 反映"主力在做什么"
  - 可比: OBV 30d% 是百分比, 跨股票/跨时间可比
  - 可证伪: 段背离 (底/顶) 是 Granville 1963 学术定义
  - 信息密度: 1 列显示 3 个独立信号 (pct/strength/div)

### OBV 30d% 分母 vs 分子时间窗不一致 (2026-08-15 修)
Type: bugfix
**症状:** pct 偏低 1-4%
**根因:**
  - 净增量 (分子): 用 wv[1..29] (30 天有 29 次价比较)
  - 总成交 (分母): 之前用 sum(wv[0..29]) — 起点当天成交不该进分母
**修法:** `tv = sum(wv[1:])` 排除起点当天
**修后偏差:** ~0.3pp, 信号方向不变 (阈值 3% 都不受 1pp 误差影响)
**验证 (中际旭创 8/14):** +11.81% → +12.13% (四舍五入 +12.1%)

### OBV 段背离 60d 扫描分母同 bug (2026-08-15 一起修)
Type: bugfix
**位置:** `tools/factors/volume/price_fflow.py:246` `_scan_obv_divergence_60d`
**根因:** 跟 factor_history 一样, tv = sum(wv) 包含起点当天
**修法:** 改 `tv = sum(wv[1:])` 排除起点
**影响:** 段背离 3/4 → 还是 3/4 (偏差 < 1pp, 不影响 count)

---

## 📅 2026-08-22 实际架构地图 (user 拍板, 防乱拉数据/乱改代码)

Type: architecture_lock
**触发背景:** 今天我先跑 9 detector 回测 (5y × 60 只, 5 min), 后跑 A→M 回测 (~30s), 然后提议改 analysis_engine.py 加 MacdDivergenceStrategy + 改 dump_data.py — user 怒了: **"我都做完了你还在改啥"**

**核心结论:** 实际架构跟我想的差 10 倍, 8-22 上午我完全错过了 user 9 个 commits。

### 🏗️ 实际数据架构 (2026-08-22 现状)

**唯一数据源: `data/history/daily/{year}.parquet`**
- 同步: `tools/history_sync.py` (sync_init 5y / sync_incremental 日常)
- 读: `DataStore.get_kline/get_weekly/get_ctx/list_codes` (parquet + duckdb)
- Tushare 走 `daily(trade_date=...)` 批量 (250 req = 2.5 min 拉完 1y 全市场)

**僵尸文件: `tools/dump_data.py` (661 行)**
- commit `6bcb30c 彻底清除 dump json 残留代码` 删了 `analyze/dump_code/save_dump` 函数
- **data/dump/ 目录已不存在** (该 commit 一起删的)
- 但 file 还在, 唯一用处: `tools/data_store.py:39` 还在 `from tools.dump_data import _PROJECT_CFG` 拿 config
- `tools/batch/regression_test.py:104` `from tools.dump_data import analyze` —— **已坏, regression_test 跑不起来**
- ⚠️ 下次大扫除: 删 dump_data.py (把 _PROJECT_CFG 移到 data_store.py 即可) + 修 regression_test

**没有"老路径 / 新路径"——只有 parquet 一条路。所有 skill 走 DataStore。**

### 📦 DataStore 统一访问层 (tools/data_store.py)

**所有新代码必须走 DataStore, 不要再直接读文件:**

```python
from tools.data_store import DataStore

DataStore.get_kline(code)         # 日线 K线 (从 parquet)
DataStore.get_weekly(code)         # 周线 (从日线聚合)
DataStore.get_ctx(code, kline_only=True)  # RawContext (kline_only=True 跳过网络)
DataStore.get_stock_basic(code)    # 名称/行业 (从 static_cache)
DataStore.get_daily_basic(code)    # PE/PB/市值
DataStore.get_eps(code)            # EPS 一致预期
DataStore.list_codes()             # 全市场股票 (duckdb 查询 parquet)
DataStore.watchlist_codes()        # watchlist.json
```

**kline_only=True 关键:** 全市场扫描时不调网络 (stock_basic/daily_basic), 5000 只从 50 min 降到 2 min

### 🔌 Tushare 接口清单 (tools/fetch/tushare_fetcher.py)

| 函数 | 用途 | 限流档 |
|---|---|---|
| `get_daily(code, start, end)` | 单只 K线 | 100 req/min (单接口) |
| **`get_daily_by_date(date)`** | **一天全市场 (5000 只)** | 100 req/min (单接口) |
| `get_daily_basic(...)` | PE/PB | 100 req/min |
| `get_fund_flow(...)` | 资金流 | 100 req/min |
| `stock_basic()` | 全市场股票列表 | 1 次拿全部 |

**关键发现:** `daily(trade_date=...)` 一次拿一天全市场 (commit 0045a86 之前我不知道), 用这个拉 5000 只 × 1y = 250 req = 2.5 min, 比 per-stock (5000 req = 50 min) 快 20x

### 🎯 AnalysisEngine + 8 Strategies (tools/analysis/analysis_engine.py)

**PHASE1_STRATEGIES 列表 (按顺序):**
```python
ChanStrategy,           # 0.20  → ctx.chan_result
WyckoffStrategy,        # 0.20  → ctx.wyckoff_result / wyckoff_weekly / wyckoff_60m
SmcStrategy,            # 0.10
ObvStrategy,            # 0.10
FflowStrategy,          # 0.10
ResonanceStrategy,      # 0.15
PegStrategy,            # 0.15
MacdDivergenceStrategy, # 0.05  (新加, line 239-316, 检测前 30d MACD 底背驰)
```

**新策略怎么加 (正确流程):**
1. 写新 Strategy class (继承 AnalysisStrategy, name/weight/analyze 三个方法)
2. 在 PHASE1_STRATEGIES 注册
3. **如果新策略输出 ctx.xxx_result, 改 factor_history.py 让 _extract_row 能读**
4. (历史回测) `compute_factor_history(ctx, step, lookback, strategies=[...])` 支持策略子集

### 📊 factor_history.py (tools/analysis/factor_history.py)

**新字段 `macd_div_bot` (line 177-178):**
```python
"macd_div_bot": (result.factor_scores.get("macd_div").raw or {}).get("has_bot_div", False)
if result.factor_scores.get("macd_div") else False
```

**这是 am_divergence 的核心数据源** — 每天一行记录是否 MACD 底背驰, 用 `compute_factor_history` 算

### 🔧 t-am-divergence (commit 0045a86)

**完整链路:**
1. `sync_incremental()` 补缺失交易日
2. `DataStore.list_codes()` 拿全市场代码
3. 每只只跑 3 个 strategy: `[WyckoffStrategy, ChanStrategy, MacdDivergenceStrategy]`
4. `compute_factor_history(lookback=window+30, strategies=[...])` 算历史
5. 找最近 window 天内的 A→M 切换 (Accumulation → Markup)
6. 检查切换日前 30d 内:
   - 缠论底背驰: `daily_beichi.direction == "bot"`
   - MACD 底背驰: `macd_div_bot == True`
7. 三重确认 → 输出清单 (表格 + docs/*.md)

**性能声明 (SKILL.md):** 5000 只 × 3 strategy × 35 天 ÷ 8 并发 ≈ 2-3 min (但 **未实测验证**)

### 🚫 严禁 (我之前犯的错)

| 错 | 后果 | 正确做法 |
|---|---|---|
| 跑 per-stock `dump_data.py` 拉全市场 | 5000 req × 0.3s = 50 min 限流 | 走 `history_sync.py` 按天拉, 250 req = 2.5 min |
| 直接读 `data/dump/{code}.json` | **目录已不存在, 直接爆 FileNotFound** | 走 `DataStore.get_kline/get_ctx` |
| 直接改 `analysis_engine.py` 加 strategy | 没在 PHASE1_STRATEGIES 注册 / factor_history 没对应字段 | 先看现有 strategy 怎么写, 加完必须改 factor_history.py + 跑 smoke test |
| 写新 skill 不用 DataStore | 跟现有架构脱节 | 先看最近 1-2 天的 commit 实际做了什么, 别凭印象改 |
| 跑 9 detector backtest (5y × 60只) | 8 min, 跟现状无关 | 看 8-22 凌晨已跑的结果, 改用它 |
| **跑 `/tmp/bt_*.py` 临时回测脚本** | 跟 t-analyze 走两套代码, 触发数对不上 (8-21 教训) | 走 `Engine.analyze()` 或 `WyckoffStageFactor` (跟 t-analyze 一致) |
| **跑 `/tmp/bt_ULTIMATE.py` 等早写脚本** | 老 API, 跟现状不对齐 | 重写走 `DataStore + AnalysisEngine + compute_factor_history` |

### ✅ 加新东西的正确流程 (防再犯)

1. **先 `git log --oneline -20` 看最近 commit 实际做了什么** (我之前完全错过 8-22 上午 9 个 commit)
2. **先看 SKILL.md 和 t-analyze 的代码再动手** (不要凭印象写)
3. **新 strategy 必须 3 步走:** 写 class → PHASE1_STRATEGIES 注册 → factor_history.py 加字段
4. **新数据走 history_sync.py + DataStore, 不要碰 dump_data.py** (僵尸, 仅供 _PROJECT_CFG 导入)
5. **任何 .py 改动先 `python -c "import xxx"` smoke test** (8-15 factor_history.py bug 教训)
6. **加 markdown skill 之前先看 t-near-low 实际怎么写的** (我之前 t-near-low 还在引用 `data/dump_oversold/`, 已迁移 DataStore, 但 SKILL.md 没更新)
7. **写 `/tmp/bt_*.py` 之前先想:** 这个回测能不能加到 `tools/batch/*.py` 走 `compute_factor_history`? 一致性 > 临时快

### 📁 当前文件路径速查 (2026-08-22 现状)

| 用途 | 路径 |
|---|---|
| 项目 memory (真源) | `docs/AGENT_MEMORY.md` |
| Agent memory (mirror) | `~/.minimax/agents/mavis/memory/MEMORY.md` |
| CLAUDE.md (Claude Code 入口) | `CLAUDE.md` (项目根) |
| Skills | `.claude/skills/*/SKILL.md` |
| **唯一数据源** | `data/history/daily/{year}.parquet` (duckdb 读) |
| DataStore 入口 | `tools/data_store.py` |
| Tushare fetch | `tools/fetch/tushare_fetcher.py` (含 `get_daily_by_date` 批量) |
| 同步脚本 | `tools/history_sync.py` |
| 全市场扫脚本 | `tools/batch/am_divergence.py` |
| 三层分析入口 | `tools/analysis/analysis_engine.py` (8 strategies) |
| 因子历史计算 | `tools/analysis/factor_history.py` (含 `macd_div_bot` 字段) |
| ~~老的 watchlist dump~~ | ⚠️ `tools/dump_data.py` 是僵尸 (data/dump/ 已删) |

### 🎯 接下来要做的 (8-22 evening 拍板)

- [ ] 修 `t-near-low/SKILL.md` (引用 `dump_oversold` 已过期)
- [ ] 跑一次 `/t-am-divergence --limit 100` 验证性能 (2-3 min 是否真)
- [ ] 跑全市场 5000 只验证 (sync_init 5y 后跑扫描)
- [ ] (清理) 删 `tools/dump_data.py` 僵尸, 把 `_PROJECT_CFG` 移到 `data_store.py` + 修 `regression_test.py:104` 断引用
- [ ] 决定 `config/project.yaml kline_days: 1300` 留还是改 (5y 给 dump_data.py 僵尸, 但 dump_data 死了)
