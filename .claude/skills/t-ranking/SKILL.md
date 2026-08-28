---
name: t-ranking
description: 投资组合综合排序 — 扫描 docs/analyze-*.md，按评级 + PEG + MA 排列排序，输出可操作的持仓建议。用户说"全部排序"、"持仓推荐"、"组合盘点"时触发。
user-invocable: true
allowed-tools:
  - Read
  - Glob
  - Grep
  - Bash
  - Write

## 用法

```bash
/t-ranking                    # 全标的排序，输出所有
/t-ranking --top 20          # 只输出 Top 20
/t-ranking --filter 半导体   # 只看半导体板块
/t-ranking --min-leader 11   # 只看 Leader ≥ 11 (真龙头)
```

## 执行流程

1. **扫描**: `ls docs/{portfolio,watchlist}/analyze-*.md` + `docs/analyze-*.md`
2. **提取关键字段** (从 MD 报告读, 不重算):
   - 评级（报告头部"🥇/🥈/🥉/⚠️/❌"）
   - PEG_真实 / PEG_A / PEG_C
   - DCF L/E3 (r=10%)
   - MA 排列（多头/拉高出货/空头/健康调整）
3. **排序优先级**:
   - 评级: 🥇🥇 > 🥇 > 🥈 > 🥉 > ⚠️ > ❌
   - 同评级内: PEG_真实 升序
   - **MA 拉高出货降权** (立讯案例: 4 PEG 全绿但 MA60>MA120 + P<MA5 = ⚠️ 观察)

## 输出格式

```markdown
# 📊 投资组合综合排序

## 📈 评级分布 (评级 → 数量)

## 🥇 核心推荐 Top 15
| 排名 | 代码 | 名称 | 板块 | 卡点 | Leader | PEG_真实 | DCF L/E3 | MA | 评级 |

## 📋 全部标的 (按评级分组)
### 🥇🥇 深度低估 (N 只)
### 🥇 重仓 (N 只)
### 🥈 标准 (N 只)
### ⚠️ 警示 / ❌ 不买

## 💡 投资建议
- 强推荐仓位 (🥇 重仓 + 🥇🥇 深度低估): 半导体设备 N 只
- 标准仓位 (🥈): ...
- 警示 / 不买: ...
```

## 数据来源

- `docs/{portfolio,watchlist}/analyze-*.md` — 个股完整 PEG + 六关 + 综合表
- 100% 真实数据 (DataStore parquet + tushare)

## 局限

- 只覆盖已生成 MD 报告的标的
- 没报告的标的需要先跑 `/t-analyze`

## 关联 Skill

- `/t-analyze` 生成个股分析报告 → `/t-ranking` 综合排序
- `/t-sync-cache` 补信号缓存 → 加速 `/t-ranking` 数据源
