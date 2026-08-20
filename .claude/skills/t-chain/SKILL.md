---
name: t-chain
description: 产业链映射 — 输入行业名 (如 AI/机器人/电动车), 输出该行业的所有子板块 + 每个子板块的龙头股 (1-3 只), 然后一键加入 watchlist.json。用法 /t-chain <industry>。任何时候用户说"AI 产业链有哪些环节"、"机器人有哪些公司"、"电动车上下游怎么分"触发。

> **v5 升级:** 加入产业链龙头时, 优先选取 PEG × MA 双绿的标的 (避免立讯式"PEG 健康但 MA 拉高出货"陷阱)。详见 t-analyze §2f。
user-invocable: true
allowed-tools:

> 🚨 **拉数据铁律 (2026-07-29 v3.4 固化)**
>
> **跑这个 skill 前, 必须先调 `t-pull` skill 拉数据** (走 `tools/dump_data.py`):
> ```bash
> # 单只: 拉 + 写 dump + 渲染
> bash tools/with_venv.sh python -m tools.dump_data {code} --render
>
> # watchlist 全部 (4 worker, 3-4 分钟):
> bash tools/refresh_all.sh
> ```
>
> 这个 skill 读 `data/dump/{code}.json` 的字段做分析 (5 方法 / 背驰 / 中枢)。
>

> **禁止**: 直 curl `web.ifzq.gtimg.cn` / `qt.gtimg.cn` / `push2his.eastmoney.com` / `datacenter-web.eastmoney` / `basic.10jqka.com.cn` / `money.finance.sina.com.cn` (WAF/GBK 拦截已废弃, 7-24 commit 标记)
> **唯一合法**: 走 `tools/dump_data.py` (经 `data_source` 统一入口) 或 `t-pull` skill
> 详见 `CLAUDE.md` §"🚫 数据拉取铁律"

---
  - Read
  - Edit
  - Write
  - Glob
  - Grep
  - WebSearch
---

# t-chain: 产业链映射 + 一键加入 watchlist

> ⚠️ **强制:** 每次跑完必须写 MD 到 `docs/chain-{industry}.md`, 然后才输出 chat 响应。
> **不能只在 chat 输出结果, 必须持久化。** 详见 §6。

## 触发

```
/t-chain AI
/t-chain 机器人
/t-chain 电动车
/t-chain 半导体
/t-chain 储能
/t-chain 光伏
/t-chain 风电
/t-chain 医药
/t-chain 军工
/t-chain NVIDIA供应链          ← 用具体公司名也行
/t-chain 数据中心              ← 用环节也行
```

## 工作流

### 1. 读上下文

- `CLAUDE.md`（必读, 决策矩阵 + 输出格式）
- `docs/analysis-framework.md#sectors`（按需, 已知板块映射）
- `data/watchlist.json`（**必读, 用于交叉检查 + 触发词提取**）
- `data/sectors.json`（按需, 检查是否有相关板块）
- `data/events.json`（按需, 用于交叉检查 T 位置）

### 2. 触发词提取 (v2.4 新功能 — 关键改进)

**从 `watchlist.json` 的 `notes` 字段提取关键词作为子板块线索:**

| 触发词 (notes 里出现) | 自动展开子板块 |
|---|---|
| "Rubin" / "NVIDIA" / "Blackwell" | CPO + 液冷 + HBM + 800V HVDC |
| "PUE" / "数据中心" / "新规" | 液冷 + UPS + 精密温控 |
| "CPO" / "光电共封装" / "光模块" | CPO 核心 + 光器件 |
| "HBM" / "内存" / "DRAM" | HBM 分销 + HBM 封装 + DRAM |
| "CoWoS" / "先进封装" / "混合键合" | 刻蚀 + 薄膜沉积 + 测试设备 |
| "稀土" / "钕铁硼" / "永磁" | 磁材 + 电机 |
| "减速器" / "Optimus" / "人形机器人" | 谐波 + RV 减速器 + 电机 |
| "变压器" / "特高压" / "AI 电力" | 变压器 + 配电 + 智能电网 |
| "储能" / "Megapack" / "电池" | 温控 + BMS + 集成系统 |

**逻辑:** watchlist notes 是用户的关注点 → 直接展开为子板块,不会漏。

### 3. 知识查找 (行业 → 子板块 → 龙头)

**优先级 (更新):**
1. **触发词展开** (v2.4 新) — 从 watchlist notes 提取
2. **框架 doc §7** (6 已知板块) — 如果 industry 匹配
3. **LLM 训练知识** — 主流行业 (AI/机器人/电动车/半导体 等)
4. **WebSearch** (按需) — 用户问小众行业 (如 商业航天/量子计算)

**每个子板块找 1-3 只龙头:**
- 优先 A 股 (主板/创业板/科创板)
- 海外龙头可加 (NVDA, TSMC, ASML 等) 但不强求加进 watchlist
- 排除已退市 / 长期停牌 / 重组中

### 3. 输出格式 (分层树)

```
[industry] 产业链

├─ [子板块 1] ([上游/中游/下游])
│   ├─ [龙头 1] ([代码])  ← 龙头 / 二线 / 黑马
│   ├─ [龙头 2] ([代码])
│   └─ [龙头 3] ([代码])
│
├─ [子板块 2] ([上游/中游/下游])
│   ├─ [龙头 1] ([代码])
│   └─ [龙头 2] ([代码])
│
└─ ...

📊 候选龙头总数: N (A 股 X 只 + 海外 Y 只)
🔍 已在你 watchlist: [代码列表] (免重复)
⚠️ Priced-in 高 (>1.5) 或 Leader 弱 (<8): [标记]
```

**输出示例 (AI):**

```
AI 产业链

├─ AI 芯片 (上游)
│   ├─ NVIDIA (NVDA.US)              ← 海外龙头
│   ├─ 寒武纪 (688256.SH)            ← 国产 ASIC
│   └─ 海光信息 (688041.SH)          ← 国产 x86
│
├─ AI 内存 (中游)
│   ├─ 兆易创新 (603986.SH)          ← DRAM/NOR ⚠️ 已减持
│   └─ 北京君正 (300223.SZ)          ← 车规存储
│
├─ AI 服务器/计算 (中游)
│   ├─ 浪潮信息 (000977.SZ)          ← 国内服务器龙头
│   └─ 中科曙光 (603019.SH)          ← 高性能计算
│
├─ AI 网络-光模块 (中游)
│   ├─ 中际旭创 (300308.SZ)          ← 800G 光模块龙头
│   └─ 新易盛 (300502.SZ)            ← 数据中心光模块
│
├─ AI 电力 (中游)
│   ├─ 特变电工 (600089.SH)          ← 变压器 🥇 你已关注
│   └─ 科华数据 (002335.SZ)          ← UPS
│
├─ AI 散热 (中游)
│   ├─ 高澜股份 (300499.SZ)          ← 液冷 🥇 你已关注
│   └─ 英维克 (002837.SZ)            ← 精密温控
│
├─ AI 材料 (上游)
│   └─ 沃特股份 (002886.SZ)          ← PEEK 材料
│
└─ AI 应用 (下游)
    ├─ 科大讯飞 (002230.SZ)          ← 语音/大模型
    └─ 商汤 (00020.HK)               ← 视觉 AI (海外)

📊 候选龙头: 14 只 (A 股 12 只 + 海外 2 只)
🔍 已在你 watchlist: 600089 特变电工, 300499 高澜股份 (免重复)
⚠️ 兆易创新 (603986): 已减持顶部信号, Priced-in 关注
```

### 4. 候选输出 (仅展示, 不加 watchlist)

**末尾问:**

```
💾 共 12 个 A 股龙头候选 (海外已跳过), 加哪些进 watchlist?

   1) 寒武纪 (688256.SH)        2) 海光信息 (688041.SH)
   3) 北京君正 (300223.SZ)      4) 浪潮信息 (000977.SZ)
   5) 中科曙光 (603019.SH)      6) 中际旭创 (300308.SZ)
   7) 新易盛 (300502.SZ)        8) 科华数据 (002335.SZ)
   9) 英维克 (002837.SZ)       10) 沃特股份 (002886.SZ)
  11) 科大讯飞 (002230.SZ)

选项:
  选编号 (空格分隔): 1 3 5
  全选: a
  跳过: n
  自定义代码: 直接输入 (如: 600089)
```

**v2.7 新: t-chain 不再加 watchlist** — 只输出候选清单, 用户看完决定哪些值得深挖。

```
💾 共 12 个 A 股龙头候选 (海外已跳过), 看完后用 /t-analyze <code> 逐个深挖:

   1) 寒武纪 (688256.SH)        2) 海光信息 (688041.SH)
   3) 北京君正 (300223.SZ)      4) 浪潮信息 (000977.SZ)
   ...

下一步:
  - 跑 /t-analyze <code> 深挖 (会自动加入 watchlist)
  - 或 /t-watchlist 批量扫描
```

**为什么 t-chain 不加 watchlist:**
- t-chain 输出几十只候选, 不应一次性进 watchlist
- 真正深挖后才该加 (用户已经做了投资决策)
- /t-analyze 是"投资决策点", /t-chain 是"研究方向"

### 5. 加完后建议

```
✅ 已输出候选清单到 docs/chain-{industry}.md (v1 baseline)

💡 建议下一步:
  1) 跑 /t-analyze <code> 深挖 Top 5 候选 (会自动加入 watchlist)
  2) 跑 /t-watchlist 批量扫描事件窗口
```

### 6. 持久化分析报告 (v2.6 — 强制 + ASCII)

**⚠️ 强制步骤:** 每次跑完 `/t-chain` 必须先写 MD 到 `docs/chain-{industry}.md`, **然后**才在 chat 输出响应。

如果用户没看到 MD 写入动作, 就视为没跑完。

#### 文件命名 (单文件累积)

**文件命名:** `docs/chain-{industry}.md` (**无日期**, 累积同一文件)
- 例: `docs/chain-机器人.md`
- 例: `docs/chain-AI.md`
- 例: `docs/chain-cpb.md`

每次跑追加 `v{N+1}` 版本, 不创建新文件。

#### MD 格式 (v2.6 — ASCII 对齐 + Priced-in 三场景)

**禁止使用 markdown 表格** (触发 IDE linter 警告)。所有表格必须用 ASCII 对齐, 放在 ` ``` ` 代码块中。

**Priced-in 必须给三场景** (乐观/中性/保守, 见 framework §2.4.1)。

**MD 必须包含 9 个章节:**

1. **行业总览** — 主 T 点 / 当前 T 位置 / 行业判断 / 触发词 / 重点子板块
2. **子板块龙头清单** — 每子板块一张 ASCII 表, 9 列 (代码/名称/评级/Leader/乐观/中性/保守/PEG/卡点/建议)
3. **v1 → v2 对比模板** — 下次跑时自动填充
4. **已深挖清单** — `/t-analyze` 跑完后打 ✅
5. **下次复盘优先级** — P0/P1/P2/P3 排序
6. **T 框架事件清单** — events.json 中该产业链的事件
7. **关键风险** — 风险事件 + 监控指标
8. **与其他产业链的协同** — 共享标的
9. **元数据** — 创建日期 / 候选总数 / 跳过标的 / 应补标的 / 下次复盘建议日期

**ASCII 表格示例 (双环传动):**

```
代码      名称        评级     Leader  乐观   中性   保守   PEG    T       卡点      建议
002472    双环传动    🥈标准   8/14    0.3    0.8    2.0    2.0⚠️  T-0.6   ⭐⭐⭐⭐⭐   🥈 标准 (单笔≤25%)
```

#### 版本管理 (核心)

**每次 `/t-chain {industry}` 跑完时:**

1. **读取** `docs/chain-{industry}.md` 看是否已有 v1
2. **如果 v1 已存在:**
   - 在第 3 节填充 `v{N} → v{N+1}` 对比 (Priced-in/Leader/PEG/关键事件/建议的变化)
   - 在每个子板块表底部追加 `### v{N+1} — 待补` 占位
3. **如果 v1 不存在 (首次):**
   - 创建 `docs/chain-{industry}.md`, 完整 9 章节

**保留最近 2-3 版**, 自动清理更早。

**为什么要保留对比:**
- 看 Priced-in 趋势 (是否在涨)
- 看龙头评分是否被证实
- 看 PEG 变化 (估值在便宜还是变贵)
- 看事件触发 (从"未识别"→"具体事件")

#### 执行顺序 (chat 输出之前必做)

1. 读上下文 (CLAUDE.md, watchlist.json, events.json, framework §7)
2. 触发词提取 + 知识查找
3. **写入 docs/chain-{industry}.md** (Write 或 Edit 工具)
4. 在 chat 输出响应 (含简版树状图 + 添加 watchlist 询问)

**注意:**
- 数据未确认的标 "(估算, 中置信度)" 符合 CLAUDE.md 工作纪律 §1
- 海外/未上市股票可写但 Priced-in/PEG 标 N/A
- 已深挖的股票跑完 `/t-analyze` 后, 关键发现写到第 4 节"已深挖清单"

## 注意事项

- 行业名支持中英文 (AI / 机器人 / 电动车 / 半导体 / 储能 / 光伏 / 风电 / 医药 / 军工 / 商业航天 等)
- 也支持具体公司名 (`NVIDIA供应链`) 或环节 (`数据中心`, `CPO`) — 触发词展开
- v2.4 新: **触发词从 watchlist notes 提取**, 避免漏掉新兴子板块 (CPO/HBM/液冷等)
- **v2.5.1 新:** 持久化到 `docs/chain-{industry}.md` (无日期, 累积)
- **v2.6 新:** ASCII 对齐格式 (无 markdown 表格) + Priced-in 三场景 (乐观/中性/保守)
- 海外龙头 (NVDA, TSMC, ASML) 默认**不加入** watchlist (你大概率不交易)
- ⚠️ 标 Priced-in 高 / Leader 弱 的股票 (降低用户添加的冲动)
- 已在 watchlist 的股票**标 🥇 你已关注**,避免重复
- 输出控制在 80 行内 (子板块多时用 tree 而不是列表)
- 不修改其他文件,只可能修改 `data/watchlist.json` (用户确认后) + `docs/chain-*.md` (持久化)
- WebSearch 仅在用户问小众行业时启用 (节省 Token)

## 与现有 skill 的关系

| Skill | 用法 | 输出 |
|---|---|---|
| `/t-sector <name>` | 一个板块 → 10-15 只股票 (mini 分析) | 详报 |
| **`/t-chain <industry>`** | **一个行业 → 多个子板块 + 每个子板块龙头** | **树状图 + 一键加入 watchlist** |
| `/t-watchlist` | 当前 watchlist 扫描 | 表 |
| `/t-analyze <code>` | 单只深挖 | 60 行报告 |

`/t-chain` 是 `/t-sector` 的"上游" — 先用 `/t-chain` 看行业全貌, 再用 `/t-sector` 深入某个子板块。

---

## 数据源图例 (v3.0 — 报告里每个数字都标来源)

| 图例 | 含义 | 来源 |
|---|---|---|
| 🟢 | **实数据** (dump 层拉的真值) | `data/dump/{code}.json` (由 tools/dump_data.py 写入) |
| 🟡 | **硬编码** (LLM 训练知识) | STOCK_REGISTRY 里的卡点/leader/板块等元数据 |
| ⚪ | **计算派生** (从实数据公式算出) | PEG / DCF L / 六关评估 |

> 所有表格的列都加数据源标识, 让用户知道哪个数是真数据哪个是估算。
