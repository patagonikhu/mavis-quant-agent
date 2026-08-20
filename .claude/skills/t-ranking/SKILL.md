---
name: t-ranking
description: 投资组合综合排序 — 扫描 docs/analyze-*.md, 按评级 + PEG 排序, 输出可操作的持仓建议。用法 /t-ranking [--top N] [--filter 板块名]。任何时候用户说"全部排序"、"持仓推荐"、"组合盘点"、"看好哪些股"触发。
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write
---

# `/t-ranking` — 投资组合综合排序

对 `docs/analyze-*.md` 全部 v3.0/v4 报告扫描, 按 **评级 + PEG + MA 排列** 排序, 生成可操作的持仓建议。

> **v5 更新:** 排序新增 MA 排列维度 — 拉高出货标的即便 PEG 健康也要降权 (立讯案例教训: 4 PEG 全绿但 MA60=67.74 / MA120=59.81 = 拉高后大跌, 应从 🥈 标准降为 ⚠️ 观察)

## 使用方式

```
/t-ranking                    # 全标的排序, 输出所有
/t-ranking --top 20          # 只输出 Top 20
/t-ranking --filter 半导体   # 只看半导体板块
/t-ranking --filter 机器人   # 只看机器人板块
/t-ranking --min-leader 11    # 只看 Leader ≥ 11 (真龙头)
```

## 执行流程

### Step 1: 扫描所有 analyze-*.md
```bash
ls docs/analyze-*.md
```

### Step 2: 提取关键字段 (PEG_A / PEG_C / DCF / 卡点 / Leader)
- PEG: 从 `data.analysis.get('peg')` 读 (AnalysisData.from_raw(dump).analysis), 不由 LLM 自己算
- DCF L/E3 (r=10%): 从 `data.analysis.get('dcf')` 读, 不调 dcf_implied.py

### Step 3: 按优先级排序
1. **评级优先级**: 🥇🥇 > 🥇 > 🥈 > 🥉 > ⚠️ > ❌
2. **PEG (A 派)**: 升序 (低到高)

### Step 4: 生成报告 (含强推荐 / 标准 / 警示 三档)

## 输出格式

```markdown
# 📊 投资组合综合排序

## 📈 评级分布概览 (评级 → 数量)

## 🥇 核心推荐 Top 15 (评级 + PEG 综合)
| 排名 | 代码 | 名称 | 板块 | 卡点 | Leader | PEG_A | PEG_C | DCF L/E3 | 评级 |

## 📋 全部标的 (按评级分组)
### 🥇🥇 深度低估 (N 只)
- ...
### 🥇 重仓 (N 只)
- ...

## 💡 投资建议总览
### 强推荐仓位 (🥇 重仓 + 🥇🥇 深度低估)
- **半导体设备** (N 只): 北方华创(P 1.5x), 中微公司(PEG 1.5x)
### 标准仓位 (🥈)
### 警示 / 不买
```

## 数据来源
- `docs/analyze-*.md` (每个标的完整 PEG 双派 + 六关 + 综合表)
- v3.0 格式: 100% 真实数据 (腾讯 + 东财 + 同花顺)

## 局限
- 只覆盖 docs/ 下有分析报告的标的
- v3.0 报告 (27 只) 数据完整; v1/v2 报告 (52 只) 部分字段缺失
- 没报告的标的需要先跑 /t-analyze

## 关联 Skill
- `/t-analyze` 生成个股分析报告 → `/t-ranking` 综合排序
- `/t-watchlist` 同批量跑 /t-analyze → 之后 /t-ranking 看 Top 选
- `/t-checklist` (已整合到 t-analyze v3.0)
- `/t-monitor` 持续监控 T 位置
- `/t-bottleneck` 找 Layer 2/3 卡脖子
- `/t-sector` 板块内批量详报
- `/t-chain` 产业链映射
