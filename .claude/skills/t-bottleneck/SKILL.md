---
name: t-bottleneck
description: 瓶颈猎手 — 从产业趋势出发，拆解供应链四层结构，识别S/A/B级瓶颈，找出被低估的Layer 2/3公司。来源：通用交叉验证框架 bottleneck-hunter skill。
v3.0-data: 2026-07-01 升级到 dump_data 字段 (PEG + DCF L 实数据, fetch_financial.py 7-30 已删)
v5-update: 2026-07-03 加入 MA5/20/60/120 均线价格位置检查 (立讯案例: PEG 健康但拉高出货, 应排除)
→ 详见 t-analyze §2f MA 框架

> 🚨 **拉数据铁律 (2026-07-29 v3.4 固化)**
>
> **跑这个 skill 前, 必须先调 `t-pull` skill 拉数据** (走 `tools/dump_data.py`):
> ```bash
> bash tools/with_venv.sh python -m tools.dump_data {code} --render
> ```
> 然后这个 skill 读 `data/dump/{code}.json` 的字段做分析 (PEG / DCF L / 5 方法矩阵).
>
> **禁止**: 直 curl `web.ifzq.gtimg.cn` / `qt.gtimg.cn` / `push2his.eastmoney.com` / `datacenter-web.eastmoney` / `basic.10jqka.com.cn` (WAF/GBK 拦截已废弃, 7-24 commit 标记)
> **唯一合法**: 走 `tools/dump_data.py` (经 `data_source` 统一入口) 或 `t-pull` skill
> 详见 `CLAUDE.md` §"🚫 数据拉取铁律"
---

# `/t-bottleneck` — 瓶颈猎手

对 `$ARGUMENTS`（超级趋势名称）执行供应链瓶颈扫描，识别 Layer 2/3 的被低估卡脖子公司。

## 使用方式

```
/t-bottleneck AI基础设施
/t-bottleneck 半导体国产替代
/t-bottleneck 机器人
/t-bottleneck 能源转型
```

## 核心理念

**不追 Layer 1 龙头（已充分定价），找 Layer 2/3 的"没人注意但一旦断货整个行业停下来等"的公司。**

超额收益来源：GPU/HBM/云厂商（Layer 1）已被充分定价，真正的 alpha 在光模块激光器、InP衬底、CoWoS载板、特种气体、高纯靶材等 Layer 2/3。

## 执行流程

### Step 1：趋势确认

确认趋势满足以下全部条件才继续：
- 持续性：至少3-5年确定性增长
- 物理性：需要实际硬件/材料/设备建设
- 规模性：全球资本开支 > 500亿美元/年
- 加速性：需求增速 > 供给扩产速度

### Step 2：供应链四层拆解

```
Layer 1（核心）：已充分定价，信息填写但不重点推荐
   ↓
Layer 2（子组件/材料）：← 重点扫描区
Layer 3（上游设备/原料）：← 重点扫描区
Layer 4（基础设施）：电力/冷却/认证
```

用 `docs/chain-半导体.md`、`docs/chain-robotics.md` 等现有链图为起点，补充 Layer 2/3 细节。

### Step 3：瓶颈评级（6条标准）

对每个 Layer 2/3 环节逐条评估：

| # | 标准 | 🔴严重 | 🟡中等 | 🟢轻微 |
|--|--|--|--|--|
| 1 | 供给集中度 | ≤2家 | 3-5家 | >5家 |
| 2 | 扩产周期 | >2年 | 1-2年 | <1年 |
| 3 | 替代难度 | 不可替代 | 部分替代 | 易替代 |
| 4 | 产能利用率 | >90% | 70-90% | <70% |
| 5 | 需求增速 | >50%/年 | 20-50% | <20% |
| 6 | 客户验证周期 | >1年 | 6-12月 | <6月 |

评级：
- 🔴×4+ → S级（最高优先级）
- 🔴×3 → A级
- 🔴×1-2 → B级

### Step 4：上市公司筛选 + 估值检查

对每个 S/A 级瓶颈，找出相关上市公司，必须过估值门槛：

**估值红灯（信号强度上限 ★★）：**
- PS > 30x 且收入增速 < 100%
- 市值 > TAM 20%
- 市值 > 5年乐观收入预测 10倍

**v3.0 估值硬数据 (整合 dump_data 字段, 7-30 删 fetch_financial.py):**
- 当前价 P → `dump_data.get('current_price')` (走 Tushare.daily_basic)
- 总股本 → `dump_data.get('shares_yi')` (Tushare.stock_basic, 走 data_source)
- 2025A EPS (E0) + 净利润 (NP0) → `dump_data.get('eps_table')` (Tushare.fina_indicator)
- 2026E/2027E/2028E EPS (E1/E2/E3) + 净利润 (NP1/NP2/NP3) → `dump_data.get('eps_table')` (Tushare.forecast)
- **注意**: 7-29 v3.4 全面弃用 腾讯 qtimg / 东财 datacenter / 同花顺 F10, 全部走 `data_source` 统一入口
- **PEG_A (本财年)**: (P / E1) / ((E1 / E0 - 1) × 100%) < 1.0 = 低估
- **PEG_C (前视镜)**: (P / E2) / ((E2 / E1 - 1) × 100%) < 1.0 = 低估
- **DCF L 反算**: `scripts/dcf_implied.py` r=8/10/12% (用真实总股本)

**估值绿灯（信号强度 +1）：**
- PS < 10x 且收入增长
- PE < 30x 且有护城河

**10年退出检验（必做）：**
```
以当前市值买入，10年后25x PE退出，年化回报 < 10% → 无安全边际
```

### Step 5：芒格式反向验证

对每个候选标的强制回答：
- 聪明人为什么不买？
- 这个瓶颈能被技术替代绕过吗？
- 中国/其他玩家能不能很快复制产能？
- 终端需求放缓50%，这家公司会怎样？

### Step 6：输出格式

**瓶颈地图：**
```
# 瓶颈猎手 — {趋势} — {日期}

## S级瓶颈
1. {环节名} — {一句话原因} — 供应商：{公司列表}

## A级瓶颈
1. ...

## 候选标的排名

| 排名 | 公司 | 代码 | 市值 | PS | PE | 瓶颈层级 | 瓶颈评级 | 信号强度 | 估值判断 |
|--|--|--|--|--|--|--|--|--|--|
| 1 | | | | | | Layer 2 | S | ★★★★ | 绿灯 |
```

**每个 ★★★+ 标的的一页纸摘要：**
```
🎯 {公司名}（{代码}）— {一句话瓶颈定位}

为什么是瓶颈：（2-3句）
为什么是这家：（2-3句）
催化剂：近期/中期
主要风险：1. 2.
10年退出年化：XX%（有/无安全边际）
建议：执行 /t-analyze 深入 / 加入 watchlist / 暂不追踪
```

### Step 7：更新 data/sectors.json

将新发现的 Layer 2/3 公司代码补充到 `data/sectors.json` 对应板块：

```bash
# 例：将新发现的InP衬底公司加入半导体材料板块
# 在 data/sectors.json 的 "半导体材料" sectors 下追加代码
```

## 与现有文档的对接

- `docs/chain-半导体.md` — 半导体完整链图，作为 Layer 1-3 的起点
- `docs/chain-robotics.md` — 机器人链图
- `docs/analysis-framework.md §16` — 框架说明
- `data/sectors.json` — 输出结果写入这里
- `docs/analyze-*.md` — 对 ★★★★+ 标的执行 `/t-analyze` 生成完整报告

## 关键原则

1. **物理优先**：只关注需要实际硬件/材料/设备的环节
2. **第二层优先**：Layer 1 大多已被充分定价
3. **瓶颈真实 ≠ 投资机会**：估值是硬门槛，不可被叙事覆盖
4. **交叉验证**：每个结论至少2个独立信源
5. **诚实**：数据不足就写"数据不足"，不用推测填充

---

## 数据源图例 (v3.0 — 报告里每个数字都标来源)

| 图例 | 含义 | 来源 |
|---|---|---|
| 🟢 | **实数据** (dump 层拉的真值) | `data/dump/{code}.json` (由 tools/dump_data.py 写入) |
| 🟡 | **硬编码** (LLM 训练知识) | STOCK_REGISTRY 里的卡点/leader/板块等元数据 |
| ⚪ | **计算派生** (从实数据公式算出) | PEG / DCF L / 六关评估 |

> 所有表格的列都加数据源标识, 让用户知道哪个数是真数据哪个是估算。
