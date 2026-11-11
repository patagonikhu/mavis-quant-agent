"""
tools/render/industry_renderer.py — 行业版报告渲染 (2026-11-12 加, /t-analyze --sw)

跟个股报告**同款结构**, 但把 2 个对行业不适用的段换掉:

| 个股段 | 行业版 | 为什么 |
|---|---|---|
| 「财务数据」 | **「行业估值 (PE/PB/市值)」** | 行业指数没有财报, 但申万指数自带 PE/PB/总市值/流通市值 |
| 「🏆 行业地位 (龙头判定)」 | **「🏆 行业间地位」** + **「👑 行业内龙头榜」** | 个股看"我在行业里排第几"; 行业看"我在所有行业里排第几" + "这个行业里谁是大票谁在涨" |

复用 (零改动, 直接调 report_renderer 的 section 函数):
  MA 均线 / 因子历史走势 / 因子 × 2 周期 矩阵 / 因子详情 (日线+周线) / XGBoost / 数据源矩阵

架构 (规则下沉):
  渲染器**只读** data 里的字段 —— 行业间排名 / 龙头榜 都在生产侧
  (tools/analysis/industry_analysis.py) 算好塞进来, 这里不 import 生产侧模块。
"""
from __future__ import annotations

from datetime import datetime

from tools.render.report_renderer import (
    _section_daily,
    _section_data_sources,
    _section_factor_history,
    _section_factor_matrix,
    _section_weekly,
    _section_xgboost,
)


# ============================================================
# 行业专属段
# ============================================================

def _section_sw_valuation(data) -> str:
    """行业估值 (PE / PB / 总市值 / 流通市值) —— 替代个股的「财务数据」段"""
    v = getattr(data, "sw_valuation", None) or {}
    pe, pb = v.get("pe"), v.get("pb")
    tmv, fmv = v.get("total_mv_yi"), v.get("float_mv_yi")
    amt = v.get("amount")

    def _f(x, unit="", nd=2):
        return f"{x:,.{nd}f}{unit}" if isinstance(x, (int, float)) and x else "—"

    lines = [
        "> 行业指数没有财报 —— 这一段用**申万指数自带**的估值口径。",
        "",
        "| 项 | 值 | 说明 |",
        "|---|---|---|",
        f"| PE (静态) | {_f(pe)} | 申万指数口径 |",
        f"| PB | {_f(pb)} | 申万指数口径 |",
        f"| 总市值 | {_f(tmv, ' 亿')} | 成分股总市值合计 |",
        f"| 流通市值 | {_f(fmv, ' 亿')} | 成分股流通市值合计 |",
        f"| 成交额 | {_f(amt)} | 申万指数口径 (万元) |",
    ]
    return "\n".join(lines)


def _section_sw_industry_position(data) -> str:
    """行业间地位 —— 该行业在全部二级行业里的市值 / 涨幅排名"""
    p = getattr(data, "sw_peer_position", None) or {}
    if not p:
        return "> ⚠️ 无行业间排名数据 (该行业没有可比区间快照)"

    mv_rank, mv_total = p.get("market_value_rank"), p.get("market_value_rank_total")
    g_rank, g_total = p.get("gain_rank"), p.get("gain_rank_total")
    mv_pct = p.get("market_value_percentile")
    g_pct = p.get("gain_percentile")
    verdict = p.get("verdict") or "—"

    def _rk(r, t):
        return f"{r}/{t}" if r and t else "—"

    lines = [
        f"> 比较对象: **全部 {mv_total or '—'} 个申万二级行业** (有最新交易日快照的才参与排名) · "
        f"区间 {p.get('start_date', '—')} → {p.get('end_date', '—')} "
        f"({p.get('trading_days', '—')} 交易日)",
        "",
        "| 维度 | 排名 | 分位 | 数值 |",
        "|---|---|---|---|",
        f"| 总市值 | {_rk(mv_rank, mv_total)} | {f'{mv_pct:.1f}%' if isinstance(mv_pct, (int, float)) else '—'} | "
        f"{f'{p.get('market_value_yi'):,.1f} 亿' if isinstance(p.get('market_value_yi'), (int, float)) else '—'} |",
        f"| 区间涨幅 | {_rk(g_rank, g_total)} | {f'{g_pct:.1f}%' if isinstance(g_pct, (int, float)) else '—'} | "
        f"{f'{p['gain_pct']:+.1f}%' if isinstance(p.get('gain_pct'), (int, float)) else '—'} |",
        "",
        f"**判定: {verdict}**",
    ]
    return "\n".join(lines)


def _section_sw_leaders(data) -> str:
    """行业内龙头榜 —— 成分股市值前 10 + 涨幅前 10"""
    b = getattr(data, "sw_leader_board", None) or {}
    if not b:
        return "> ⚠️ 无龙头榜数据 (成分表或市值快照缺失)"

    peer_total = b.get("peer_total") or 0
    lines = [
        f"> 行业: **{b.get('l2_name') or '—'}** ({b.get('l2_code') or '—'}) · "
        f"成分股 {peer_total} 只 · 区间 {b.get('start_date', '—')} → {b.get('end_date', '—')} "
        f"({b.get('trading_days', '—')} 交易日)",
        "",
        "### 👑 市值前 10 (谁是大票)",
        "",
        "| 排名 | 代码 | 名称 | 总市值 | 市值分位 | 区间涨幅 |",
        "|---|---|---|---|---|---|",
    ]
    for r in b.get("mv_top") or []:
        mv = r.get("market_value_yi")
        pct = r.get("market_value_percentile")
        g = r.get("gain_pct")
        lines.append(
            f"| {r.get('market_value_rank')} | `{r.get('code')}` | {r.get('name')} | "
            f"{f'{mv:,.1f} 亿' if isinstance(mv, (int, float)) else '—'} | "
            f"{f'{pct:.1f}%' if isinstance(pct, (int, float)) else '—'} | "
            f"{f'{g:+.1f}%' if isinstance(g, (int, float)) else '—'} |"
        )
    lines += [
        "",
        "### 🚀 涨幅前 10 (谁在涨)",
        "",
        "| 排名 | 代码 | 名称 | 总市值 | 区间涨幅 | 涨幅分位 |",
        "|---|---|---|---|---|---|",
    ]
    for r in b.get("gain_top") or []:
        mv = r.get("market_value_yi")
        g = r.get("gain_pct")
        gp = r.get("gain_percentile")
        lines.append(
            f"| {r.get('gain_rank')} | `{r.get('code')}` | {r.get('name')} | "
            f"{f'{mv:,.1f} 亿' if isinstance(mv, (int, float)) else '—'} | "
            f"{f'{g:+.1f}%' if isinstance(g, (int, float)) else '—'} | "
            f"{f'{gp:.1f}%' if isinstance(gp, (int, float)) else '—'} |"
        )
    lines.append("")
    lines.append("> 涨幅口径: **总市值比值** (= 股东实际收益, 送转不失真), 跟个股版 🏆行业地位 同源。")
    return "\n".join(lines)


def _sw_completeness_table(data) -> tuple[str, int, int]:
    """行业版数据完整性表 (跟个股同结构, 维度换掉行业不适用的)"""
    rows = [
        ("实时价", bool(data.current_price), f"¥{data.current_price:.2f}" if data.current_price else "—"),
        ("K线", len(data.kline) > 0, f"{len(data.kline)} 条"),
        ("MA", len(data.ma_table) > 0, f"{len(data.ma_table)} 条"),
        ("技术指标", bool(data.technical), "8 种 (MACD/RSI/KDJ/BOLL/ATR/量比)"),
        ("威科夫", bool(data.analysis), "日线 + 周线"),
        ("行业估值", bool(getattr(data, "sw_valuation", None)), "PE/PB/市值"),
    ]
    keys = [r[0] for r in rows]
    table = "| 维度 | " + " | ".join(keys) + " |\n"
    table += "|---|" + "|".join(["---"] * len(keys)) + "|\n"
    table += "| 状态 | " + " | ".join("✅" if r[1] else "❌" for r in rows) + " |\n"
    table += "| 详情 | " + " | ".join(r[2] for r in rows) + " |\n"
    ok = sum(1 for r in rows if r[1])
    return table, ok, len(rows)


# ============================================================
# 主渲染入口
# ============================================================

def render_industry_report(data, sector: str = "—") -> str:
    """渲染行业版分析报告

    Args:
        data:   RenderData (行业版: ctx = 行业指数, 额外带 sw_* 字段)
        sector: 所属一级行业名 (标题里的「板块」)

    Returns:
        完整 Markdown 字符串
    """
    today = datetime.now().strftime("%Y-%m-%d")
    price_str = f"{data.current_price:,.2f}" if data.current_price else "—"
    name = data.name or "—"
    code = data.code

    comp_table, ok_n, tot_n = _sw_completeness_table(data)

    report = f"""<!-- report_type: industry -->
# {code} {name} | {today}

**板块:** {sector} | **指数点位:** {price_str}

---

<!-- id:data_completeness -->
## 📊 数据完整性 (开篇即知, 一目了然)
{comp_table}
**完整度:** {int(ok_n / max(tot_n, 1) * 100)}% ({ok_n}/{tot_n} 项)

---

<!-- id:sw_valuation -->
## 📈 行业估值 (PE / PB / 市值)
{_section_sw_valuation(data)}

---

<!-- id:sw_industry_position -->
## 🏆 行业间地位 (全行业排名)
{_section_sw_industry_position(data)}

---

<!-- id:sw_leaders -->
## 👑 行业内龙头榜 (市值前 10 + 涨幅前 10)
{_section_sw_leaders(data)}


---

## 📈 因子历史走势
{_section_factor_history(data)}

---

<!-- id:method_matrix -->
## 🎯 因子 × 2 周期 矩阵 (威科夫)
{_section_factor_matrix(data)}

---

## 🔍 因子详情 — 周期独立展开

> 📌 **结构说明**: 上面矩阵是 1 眼总览 (威科夫 × 2 周期各 1 行概要), 下面是每个周期的详情
> 不重复: 矩阵已包含的概要不在详情里再写

### 📋 日线分析 (因子 × 日线视角)
{_section_daily(data)}

---

### 📋 周线分析 (因子 × 周线视角)
{_section_weekly(data)}

---

<!-- id:xgboost -->
## 🤖 XGBoost 校准
{_section_xgboost(data)}

---

<!-- id:data_sources -->
## 📡 数据源矩阵 (数据/主源/备源/单位/说明) — 固化不丢
{_section_data_sources()}

---

<!-- id:linter -->
## 🔍 Linter 校验报告 (增强模式自动追加)
- **完整度:** {int(ok_n / max(tot_n, 1) * 100)}% ({ok_n}/{tot_n} 项)

> 本报告由 `tools/render/industry_renderer.render_industry_report()` 全量渲染生成
"""
    return report
