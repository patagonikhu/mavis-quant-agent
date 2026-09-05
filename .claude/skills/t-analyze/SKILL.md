---
name: t-analyze
description: 股票分析 + 批量扫描. 单只 /t-analyze <code>; 批量 /t-analyze --all; 板块 /t-analyze --sector AI. 触发词: "分析XX股票"、"XX能买吗"、"批量分析"、"全部扫一遍"、"XX板块怎么样".
user-invocable: true
allowed-tools:
  - Bash

## 核心原则

- **read-only**, 0 网络, 走 DataStore
- 跑前用户自己 `sync_data` (skill 不知道, 也不偷偷调)
- 缺数据时报 "请先 /t-sync-data", 不兜底拉

## 用法

```bash
# 单只
bash tools/with_venv.sh python3 tools/batch/t_analyze_one.py --code 300274
bash tools/with_venv.sh python3 tools/batch/t_analyze_one.py --code 300274 --name 阳光电源

# 批量 (watchlist 全部)
bash tools/with_venv.sh python3 tools/batch/t_analyze_all.py

# 4 worker 并发
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python3 tools/batch/t_analyze_all.py
```

## 数据源

`DataStore` (tools/storage/store.py) → 22 section 详报, 0 网络

| 输入 | 字段 |
|---|---|
| DataStore.get_ctx(code) | K线 + 财务 + EPS + 行业 + 市值 |
| AnalysisEngine.analyze_history(ctx, dates) | 5 策略 (chan/wyckoff/smc/obv/fflow/valuation) |
| render_report(data) | Markdown 渲染 |

## 输出

- `docs/portfolio/analyze-{code}-{name}.md` — 持仓票 (22 section 详报, ~830 行)
- `docs/watchlist/analyze-{code}-{name}.md` — 自选 / Magic初筛票
- `docs/signal-watchlist.md` — 批量模式 (`t_analyze_all.py` 汇总信号表)

## 报告 22 section

1. 数据完整性 (17 项检查)
2. EPS + 财务数据
3. MA 均线 + 偏离
4. 8 种技术指标 (MACD/RSI/KDJ/BOLL/ATR/量比)
5. 5 方法 × 3 周期 矩阵 (场景/共振/行动)
6. 5 方法详情 (周/日/60分)
7. PEG 估值 (4 口径: 后视镜/前视镜/真实/表观)
8. DCF L (r=8/10/12% 三档)
9. 4 问 (卡点/TAM/龙头/估值)
10. T 框架 (event 触发)
11. 5 类 14 子信号
12. 止盈 3 层 / 止损 4 档 / 退场信号
13. 3 层仓位 (底/中/波动)
14. 监控触发点
15. 基础信息 (Tushare)
... 共 22 section

## 投资四问 (投资前必答)

1. **卡点** ⭐⭐⭐⭐⭐ (1-5) — 不可替代环节?
2. **TAM** — 5 年总市场增长够大?
3. **龙头** 0-14 分 (≥11 才是) — 市占/技术/客户/产能 4 维
4. **估值** — DCF L + PEG 双指标

| 4 问结果 | 行动 |
|---|---|
| 全 ✅ | 🥇 重仓 |
| 任一 ❌ | ❌ 不买 |

## 决策框架 (PE/DCF/Magic 3 维)

| 指标 | 阈值 | 解读 |
|---|---|---|
| **PEG 真实** | <1.0 / 1.0-1.5 / 1.5-2.0 / >2.0 | Lynch 买入 / 合理 / 偏贵 / 高估 |
| **DCF L/E3** | <2 / 2-5 / >5 | 叙事未满 / 较高 / 叙事已满 |
| **DCF L/可达** | <0.8 / 1-2 / >2 | 低估 ✅ / 合理 / 透支 ❌ |

## 实战信号 (5 方法 × 3 周期)

5 重保险, 必须输出:
- **场景**: A 主升 / B 高位 / C 震荡 / D 弱势 / E 下跌
- **共振数**: N 重 (5 方法里 N 个看多/空)
- **行动**: 🥇 加仓 / 🥈 持有 / 🟡 观察 / ⬜ 不动 / ❌ 减仓

## 相关

- `/t-sync-data` — 跑前必跑 (数据来源)
- `/t-bb-obv` — 短期吸筹形态
- `/t-near-low` — 距 5y 低 < 3% 清单
- `/t-magic` — Magic 排名 (Top 20 双优)
- `tools/batch/t_analyze_one.py` — 单只脚本
- `tools/batch/t_analyze_all.py` — 批量 4 worker 并发
