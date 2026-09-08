---
name: t-quality-growth
description: 高质量高增长扫描. R3 v6.2.7 启动期模式: 营收 yoy>=25% + 净利 yoy>=50% + 毛利率 (升 OR 跌幅≤2pp, 环比+同比) + (净利 yoy 跳升>=50pp OR 持续高增龙头) + 位置过滤. 10x 票 T+0 命中 41%. 0 网络, 走 DataStore financials parquet. 触发词: "高质量高增长"、"基本面选股"、"扣非高增长"、"毛利率提升"、"启动期"、"净利 yoy 跳升"、"戴维斯双击"、"持续高增龙头"、"中际旭创".
user-invocable: true
allowed-tools:
  - Bash

## 原理

**R3 v6.2.7 启动期模式** (4 基础 + 1 触发, 触发二选一):

| # | 条件 | 公式 | 排除 |
|---|---|---|---|
| 1 | 营收 yoy | `or_yoy >= 25%` | 主业下滑 |
| 2 | 净利 yoy | `netprofit_yoy >= 50%` | 利润下滑 (Tushare VIP 无扣非 yoy, 用净利润代理) |
| 3 | 毛利率 (升 OR 跌幅≤2pp) | `(gm_t>gm_t-1 OR |gm_t-gm_t-1|≤2)` AND `(gm_t>gm_t-4 OR |gm_t-gm_t-4|≤2)` | 毛利率大幅压缩 (抓放量型科技股 + 保留毛利率大幅升的票) |
| 4a | **反转触发** (c_jump) | `np_yoy_t - np_yoy_t-1 >= 50pp` | 业务反转核心信号 (默认 either 模式) |
| 4b | **龙头触发** (c_leader) | `or_yoy>=80% AND np_yoy>=80% AND gm_t > gm_t-1` | 持续高增龙头分支 (抓中际旭创/新易盛/天孚通信) |

**触发模式 `--jump-mode`**:
- `reverse` (只看反转): 旧 R3 行为, 抓"上季还在泥潭/亏损, 本季突然转好"的票
- `leader` (只看龙头): 抓"连续 4-5 季高速增长"的真龙头 (中际旭创/新易盛/天孚通信/源杰科技)
- **`either` (默认, 反转或龙头任一)**: 同时抓两类, 最新 1 季命中 15 只 (位置过滤后)

**位置过滤 (默认开启, --no-position-filter 关闭)**:
- 距 1 年低点 <= 200% (避免追 7 倍以上的高位票)
- 距 1 年高点 >= -30% (至少回调 30%, 不追顶)

**周期股默认排除** (SW 周期类 + 电气设备) — `--include-cycle` 可开

**为什么用 R3 (净利 yoy 跳升) 替代"3 季 EBIT 累计 >= 2x"**:
- 10x 票**首次 R3 命中** vs **T+0 启动点**: 命中时间平均 **T-13 月** (启动前 1 年)
- 33 只 10x 票 R3 首次命中后 **1y 收益**: 中位 **+273% (近 4 倍)** / 胜率 **97%** / 翻倍 **73%** / 大亏 3%
- 10x 票从反转信号 → 启动 → 涨 10x 实际需要 **18-30 个月** 漫长主升浪, R3 在 T-12~-18 月预警, 正是**最佳埋伏期**
- 旧"3 季 EBIT 累计 >= 2x" 在 T+0 0% 命中 (10x 票起涨季 EBIT 累计中位仅 0.5x)

**R3 的真正定位: "启动前 1 年预警器" (T-12 ~ T-18 月命中高峰)**
- 命中时点: 业务反转初期 (营收/净利刚刚开始跳升)
- 兑现窗口: 1 年 (R3 命中后 1 年是中位 +273%)
- 操作建议: R3 命中 → 分批建仓 → 1.5 年持有等主升 → 缠论卖点 (1卖/2卖) 减仓
- **不是"启动期同步器"** (T+0 启动点 0 命中, 启动 1 年后才有信号), 启动期精确入场要靠**缠论 1买/2买**

**v6.2.7 改动 (毛利率双升 → 升 OR 跌幅≤2pp)**:
- 改前 (v6.2.6): 毛利率必须 `本季 > 上季` AND `本季 > 去年同季` (双升, 太严)
- 改后 (v6.2.7): `(本季 > 上季) OR |本季-上季|≤2pp` AND 同比同理 (毛利率升 OR 小幅波动)
- 含义: 保留"大幅升"+"小幅升"+"稳定"+"小幅降" 4 种, 排除"大幅降"
- 抓到寒武纪/中微公司 这种**毛利率长期稳定**的科技股龙头
- 效果: 18 季命中数 1718 → 2517 (+47%), 位置过滤后 179 → 263

**v6.2.6 新增龙头分支的原因**:
- 中际旭创 2026Q2: 营收 yoy 182% / 净利 yoy 242% / 毛利率 46.3% (环比+0.2pp), 净利 yoy 跳升 -20.6pp (从 262% 略降到 242%, 因为基数已大)
- 4 条件 + 反转触发: ❌ 跳升 -20.6pp 不达 50pp
- 4 条件 + 龙头触发: ✅ 营收/净利双 80%+ + 毛利率环比升
- 反转或龙头任一 = 既抓业务真反转, 也不漏掉已显王者相的持续高增龙头

**信号意义**: 营收 + 净利 + 毛利率 同向 + 反转或龙头 = 业务量增长 + 议价能力提升 + (业务反转 | 龙头加速) 同时发生, 是基本面最强的"戴维斯双击"形态。

## 用法

```bash
# 默认参数 (R3 启动期模式: 4 条件 + 位置过滤)
bash tools/with_venv.sh python -m tools.batch.quality_growth_scan

# 含周期股 (周期反转行业用, 例: 半导体周期反转 / 锂电材料涨价)
bash tools/with_venv.sh python -m tools.batch.quality_growth_scan --include-cycle

# 关闭位置过滤 (10x 命中率更高但顶段多, 抓量)
bash tools/with_venv.sh python -m tools.batch.quality_growth_scan --no-position-filter

# 调高净利 yoy 跳升门槛 (更严, 100pp)
bash tools/with_venv.sh python -m tools.batch.quality_growth_scan --np-jump 100

# Top 10
bash tools/with_venv.sh python -m tools.batch.quality_growth_scan --limit 10

# 不写 md (只 stdout 速览)
bash tools/with_venv.sh python -m tools.batch.quality_growth_scan --no-md
```

## 数据源

`DataStore` (financials parquet, 0 网络) → `data/history/financials/YYYYQN.parquet`

**字段依赖 (Tushare `fina_indicator_vip` 109 字段)**:
- `or_yoy` (营业总收入同比, %)
- `netprofit_yoy` (净利润同比, %)
- `netprofit_yoy_prev` (上季净利润同比, %, 用于跳升)
- `grossprofit_margin` (毛利率, %)
- `industry` (从 stock_basic 拷过来, 写盘时填充)

**Tushare 限制**: VIP 接口不返 `profit_dedt_yoy` (扣非 yoy), 本工具用 `netprofit_yoy` 代理,严格扣非需求请联系 Tushare 开通。

**缺数据**: 请先 `python -m tools.storage.sync --financials` (v6.2.5 起 1 次 API 拉 1 季 5555 只, 109 字段, 5 季 0.3 分钟)

## 输出

- `docs/quality-growth-watchlist.md` — 命中列表 (含 营收 yoy / 净利 yoy / 净利跳升 / 毛利率 / 位置过滤 等)
- stdout: 命中数 + Top N 速览 (代码 / 名称 / 行业 / 营收 yoy / 净利 yoy / 净利跳升 / 毛利率)

## 实战策略

**回测结果 (R3 算法, 10x 票 T+0 起涨季验证)**:
- T+0 启动期命中 41% (73/180)
- T+1 确认期命中 33% (57/174)
- T+2 加速期命中 27% (42/158)
- 旧 5 条件 T+0 5% → R3 T+0 41% (**8 倍提升**)

**建议**:
- 命中 130-180 只/13 季为正常 (R3 比旧规则松, 启动期更全)
- 出现 30+ 只同时命中时, 通常是行业 β (例: 半导体周期反转 → 2025Q1 大量存储股齐涨)
- **必设 -15% 止损 / +50% 止盈** (R3 抓启动期, 票波动大)
- 选 1-2 只最强 (高 净利 yoy 跳升 + 高 ROE + 强毛利率双升) 深入研究, 配合 `/t-analyze <code>` 看 22 section 详报
- 季度再扫一次, 持续命中 = 真反转, 单季命中 = 周期性

## 适用性

- ✅ 适合: 寻找基本面反转 / 行业拐点 / 中长线持仓候选 / 启动期捕获
- ✅ 半导体/通信设备/元器件 等真成长行业 命中率高
- ❌ 默认排除: 周期股 (SW 周期类 + 电气设备, 用 `--include-cycle` 可开)
- ❌ 不适合: 题材股 / 重组股 / 一次性损益 / 疫情红利股
- ⚠️ R3 信号多, 假阳性也多, 必设止损 (R3 抓的是"反转", 失败率高于 5 条件稳态)
- ⚠️ 净利 yoy 跳升 50pp 是核心, 替代了 3 季单调 (10x 票启动期 EBIT 是 V 型)

## 相关

- `/t-magic` — Magic Formula 找"好公司+便宜股"双优 (估值 + 质量)
- `/t-bb-obv` — 短期 BOLL+OBV 反弹 (技术面)
- `/t-near-low` — 距 5y 低 < 3% 清单 (极端超跌)
- `/t-analyze <code>` — 命中票深挖 22 section
- `/t-backtest` — 信号回测 5 年历史
