"""
report_renderer.py — 分析报告渲染器 (v1.0, 2026-07-21)

架构铁律 (三层分离):
  Render 层 = 纯渲染, 零网络请求
  ✅ 只读 RenderData 对象字段
  ❌ 禁止 import requests / subprocess curl / fetch_all / 任何网络调用

设计目标:
  1. 22 个 section 标题全部固定, 缺数据也输出
  2. 每个 section 头部显示数据状态 (OK/TIMEOUT/EMPTY/...)
  3. 报告头 + 报告尾各有一个完整性表格
  4. 给 LLM 看的"占位符"明确, 不会因 LLM 自由发挥而漏

使用方式 (必须先走 dump 层):
  # Step 1: dump 层拉数据 (唯一网络入口)
  # Step 2: 读数据 + 算 factor
  from tools.kline_store import DataStore
  from tools.analysis.analysis_engine import AnalysisEngine
  from tools.analysis.render_data import RenderData

  ctx = DataStore.get_ctx("002371")
  all_dates = [k['trade_date'].replace('-','')[:8] for k in ctx.kline]
  history = AnalysisEngine().analyze_history(ctx, all_dates[-120:])
  result = history[all_dates[-1]]
  data   = RenderData.from_result(ctx, result)

  # Step 3: render 层纯渲染
  from tools.render.report_renderer import render_report
  md = render_report(data)
  open("docs/analyze-002371-北方华创.md", "w").write(md)
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

from tools.analysis.render_data import RenderData
from tools.analysis.analysis_result_signals import obv_label


# ============================================================
# Section 渲染器
# ============================================================

def _section_eps(data: RenderData) -> str:
    """EPS + 财务数据 + Magic Formula (Greenblatt ROC + EY)
    三块数据都是"基本面估值"维度, 合一个 section 信息密度更高
    """
    parts = []

    # 1) EPS 表格
    if data.eps_table:
        rows = ["| 年份 | EPS | 净利(亿) | 营收(亿) | ROE |",
                "|---|---|---|---|---|"]
        for r in data.eps_table:
            rows.append(f"| {r.year} | {r.eps:.2f} | {r.net_profit_yi:.1f} | {r.revenue_yi:.1f} | {r.roe:.1f}% |")
        parts.append("\n".join(rows))
    else:
        parts.append("> **EPS 预测:** ❌ 未拉取 (PEG/DCF 无法计算, 标 N/A)")

    # 2) Magic Formula (Greenblatt ROC + EY) — 2026-09-02 改读 valuation_data
    _val = data.valuation_data or {}
    if _val:
        skip = _val.get("skip_reason")
        if skip:
            parts.append(
                f"\n\n### 💎 Magic Formula (Greenblatt ROC + EY)\n"
                f"> **跳过原因:** {skip} ({_val.get('industry', '—')})\n"
                f"> 行业失真 / 数据缺, 不参与排序"
            )
        else:
            roc_pct = _val.get("roc", "—")
            ey_pct  = _val.get("ey", "—")
            ev_yi   = _val.get("ev_yi", "—")
            industry = _val.get("industry", "—")
            period_label = _val.get("period_label", "—")
            seasonal = " ⚠️ 季节性 proxy" if _val.get("seasonal_warning") else ""
            ev_str = f"{ev_yi:,.0f}" if isinstance(ev_yi, (int, float)) else "—"
            parts.append(
                f"\n\n### 💎 Magic Formula (Greenblatt ROC + EY) (来源: {period_label}{seasonal})\n"
                f"| 指标 | 数值 | 说明 |\n"
                f"|---|---|---|\n"
                f"| 行业 | {industry} | — |\n"
                f"| EBIT (TTM) | {_val.get('ebit_yi', '—')} 亿 | Tushare fina_indicator |\n"
                f"| 净营运资本 + 固定资产 | {_val.get('capital_yi', '—')} 亿 | NWC + FA |\n"
                f"| **ROC (Return on Capital)** | **{roc_pct}%** | EBIT / (NWC + FA) |\n"
                f"| 市值 | {_val.get('market_cap_yi', '—')} 亿 | Tushare daily_basic |\n"
                f"| 净债务 | {_val.get('netdebt_yi', '—')} 亿 | Tushare fina_indicator |\n"
                f"| **EV (企业价值)** | **{ev_str} 亿** | 市值 + 净债务 |\n"
                f"| **EY (Earnings Yield)** | **{ey_pct}%** | EBIT / EV |\n"
                f"\n**判定:** ROC 越高越好 (高资本效率), EY 越高越好 (盈利对 EV 回报高), 联合排名 (Greenblatt 原版)"
            )

    return "\n".join(parts)


def _section_ma(data: RenderData) -> str:
    if not data.ma_table:
        return "> **数据状态:** ❌ MA 未计算 (K线不足)\n"
    rows = ["| 均线 | 数值 | 偏离 |", "|---|---|---|"]
    for r in data.ma_table:
        emoji = "🟠" if abs(r.deviation) > 20 else "✅"
        rows.append(f"| {r.period} | ¥{r.value} | {r.deviation:+.1f}% {emoji} |")
    # MA 排列
    if len(data.ma_table) >= 4:
        vals = [r.value for r in data.ma_table]
        current = data.current_price or 0
        if current > vals[0] > vals[1] > vals[2] > vals[3]:
            arrange = "🟢 多头排列 (MA5>MA20>MA60>MA120)"
        elif current < vals[0] < vals[1] < vals[2] < vals[3]:
            arrange = "🔴 空头排列"
        else:
            arrange = "⚠️ 混乱排列"
    elif len(data.ma_table) >= 2:
        arrange = f"⚠️ 仅 {len(data.ma_table)} 条 MA (K线不足 120 天，无法判断完整排列)"
    else:
        arrange = "❓ MA 数据不足"
    return "\n".join(rows) + f"\n\n**MA 排列:** {arrange}"


def _section_technical(data: RenderData) -> str:
    """8 个技术指标 (MACD/RSI/KDJ/BOLL/ATR/量比) — Wilder 标准公式"""
    if not data.technical or "error" in data.technical:
        return "> **数据状态:** ❌ 技术指标未计算 (K线不足或 compute_indicators 失败)\n"

    t = data.technical
    parts = []

    # 1. MACD
    if "macd" in t:
        m = t["macd"]
        parts.append(f"**MACD (12,26,9):**\n- DIF={m['DIF']:.4f}, DEA={m['DEA']:.4f}, BAR={m['BAR']:.4f}\n- {m['verdict']}")

    # 2. RSI
    if "rsi" in t:
        r = t["rsi"]
        parts.append(f"**RSI (6,12,24):**\n- RSI6={r['rsi6']:.1f}, RSI12={r['rsi12']:.1f}, RSI24={r['rsi24']:.1f}\n- {r['verdict']}")

    # 3. KDJ
    if "kdj" in t:
        k = t["kdj"]
        parts.append(f"**KDJ (9,3,3):**\n- K={k['K']:.1f}, D={k['D']:.1f}, J={k['J']:.1f}\n- {k['verdict']}")

    # 4. BOLL
    if "boll" in t and "error" not in t["boll"]:
        b = t["boll"]
        parts.append(f"**BOLL (20,2):**\n- 中轨=¥{b['mid']:.2f}, 上轨=¥{b['upper']:.2f}, 下轨=¥{b['lower']:.2f}\n- 带宽={b['width']:.2f}%\n- {b['verdict']}")

    # 5. ATR
    if "atr" in t and "error" not in t["atr"]:
        a = t["atr"]
        parts.append(f"**ATR (14):**\n- ATR14=¥{a['atr14']:.2f} ({a['atr_pct']:.2f}%)\n- {a['verdict']}")

    # 6. 量比
    if "vol_ma" in t and "error" not in t["vol_ma"]:
        v = t["vol_ma"]
        parts.append(f"**量比 (vol_ratio):**\n- 今日={v['vol_today']:.0f}, MA5={v['vol_ma5']:.0f}, ratio={v['vol_ratio']:.2f}\n- {v['verdict']}")

    # 7. 综合
    if "summary" in t:
        parts.append(f"\n**综合判定:** {t['summary']}")

    return "\n\n".join(parts) + "\n"


def _section_chan_full(data: RenderData) -> str:
    """缠论完整 4 级别 — 从 dump JSON 的 chan 字段生成 (2026-07-24 修复: 之前只放占位)"""
    if data.chan_data and "full_table" in data.chan_data:
        return data.chan_data["full_table"]

    # 2026-07-24: 从 chan_data 字段生成完整缠论表
    if data.chan_data:
        weekly = data.chan_data.get("weekly", {})
        daily = data.chan_data.get("daily", {})

        def _hub_str(hub):
            if not hub or not hub.get("valid"):
                return "—"
            lo, hi = hub.get("low", 0), hub.get("high", 0)
            return f"¥{lo:.2f}~¥{hi:.2f} ({hub.get('pos', '—')})"

        def _seg_count(level):
            segs = level.get("segs", [])
            return len(segs) if segs else 0

        def _segs_table(level):
            segs = level.get("segs", [])
            if not segs:
                return ""
            lines = ["| 起价 | 止价 | 笔数 | 方向 | 起日 | 止日 |",
                     "|---|---|---|---|---|---|"]
            for s in segs[-8:]:  # 最近 8 段
                lines.append(f"| ¥{s.get('sp', 0):.2f} | ¥{s.get('ep', 0):.2f} | {s.get('nb', 0)} | {s.get('dir', '—')} | {s.get('sdt', '—')} | {s.get('edt', '—')} |")
            return "\n".join(lines)

        out = [
            "| 级别 | 中枢 | 段数 | seg_idx |",
            "|---|---|---|---|",
            f"| 周 | {_hub_str(weekly.get('hub', {}))} | {_seg_count(weekly)} | {weekly.get('hub', {}).get('seg_idx', '—') or '—'} |",
            f"| 日 | {_hub_str(daily.get('hub', {}))} | {_seg_count(daily)} | {daily.get('hub', {}).get('seg_idx', '—') or '—'} |",
            "",
            "**中枢构成 (日线最近 8 段):**",
            _segs_table(daily) or "_无段数据_",
        ]
        return "\n".join(out)

    return """> **数据状态:** ⚠️ 缠论 4 级别表未传入
> **说明:** 完整缠论数据由 `/t-trigger` 计算, 见 `docs/analyze-{code}.md` 后续 section
> **占位:** 周/日 中枢列表 + 段列表 — 由 t-trigger 注入

| 级别 | 中枢数 | 段数 |
|---|---|---|
| 周 | — | — |
| 日 | — | — |
"""


def _section_four_questions(data: RenderData) -> str:
    if data.four_questions:
        fq = data.four_questions
        return f"""- **① 卡点:** {fq.get('chokepoint', '—')} {fq.get('chokepoint_reason', '')}
- **② TAM:** {fq.get('tam', '—')}
- **③ 龙头评分:** {fq.get('leader_score', '—')}/14 ({fq.get('leader_reason', '')})
- **④ 估值:** {fq.get('valuation', '—')}
- **综合:** {fq.get('verdict', '—')}"""
    return """> **数据状态:** ⚠️ 投资四问未评分
> **说明:** 由 mavis (LLM) 套框架评分, 需 `/t-analyze` 一次性完成
"""


def _section_t_frame(data: RenderData) -> str:
    if data.t_frame:
        tf = data.t_frame
        event     = tf.get('event') or tf.get('最近事件', '无近期事件')
        event_date= tf.get('event_date', '')
        t_pos     = tf.get('t_position') or tf.get('T_position', 'T+0.0')
        phase     = tf.get('phase', '待判定')
        strength  = tf.get('signal_strength', '需事件才能判断')
        action    = tf.get('action', '待 mavis LLM 算 T 位置')
        event_str = f"{event} ({event_date})" if event_date else event
        return f"""- **最近事件:** {event_str}
- **T 位置:** {t_pos}
- **阶段:** {phase}
- **信号强度:** {strength}
- **操作建议:** {action}"""
    return "> **数据状态:** ⚠️ T 框架未生成，需在 data/events.json 添加事件后重新 sync_watchlist_fresh\n"


def _section_ga_factor(data: RenderData) -> str:
    """🧪 GA 因子验证 (2026-07-27 加, 实验性)

    调 tools/factors/alpha101/alpha_ga_001 factor 算当前值
    显示: 当前值 + 跨票 |IC| 范围 + 方向 + 判定

    输入: data.kline (KLineBar 列表) → 转 DataFrame
    输出: 表格 (当前值 + 跨票 |IC| + 方向 + 判定)
    """
    try:
        from tools.factors.registry import FactorRegistry
        import pandas as pd

        reg = FactorRegistry()
        ga_factor = reg.get("alpha_ga_001")
        if ga_factor is None:
            return "> ⚠️ alpha_ga_001 factor 未注册\n"

        if not data.kline or len(data.kline) < 60:
            return "> ⚠️ K 线不足 60 根, 跳过 GA 因子\n"

        # KLineBar → DataFrame (跟 alpha_ga_001 需要的格式一致)
        kline_df = pd.DataFrame([{
            "date": k.date, "open": k.open, "high": k.high,
            "low": k.low, "close": k.close, "volume": k.vol,  # KLineBar 字段是 vol
        } for k in data.kline])

        factor_series = ga_factor(kline_df)
        current = float(factor_series.iloc[-1])

        # 跨票 |IC| 范围 (从 4 张票 GA 报告: 0.077~0.671, 多数反向)
        # 实际应跑全 57 只扫描, 这里先用 hardcoded 范围
        ic_range = "0.077 ~ 0.671"
        ic_typical = "0.40 ~ 0.50"

        # 方向判定: 多数票 (300274/300308/688012) 反向, 002371 正向
        # 阈值: 0.7 强, 0.3 中, 否则弱 (基于 4 票样本观察)
        if abs(current) > 0.7:
            direction = "反向 (高因子值→5d 跌)"
            signal = "🟢 5d 涨信号强 (取负)"
        elif abs(current) > 0.3:
            direction = "反向 (中等)"
            signal = "🟡 5d 涨信号弱 (取负)"
        else:
            direction = "中性"
            signal = "⚪ 无明确信号"

        return f"""| 因子 | 当前值 | 跨票 IC | 方向 | 判定 |
|---|---|---|---|---|
| **alpha_ga_001** | {current:+.3f} | {ic_range} (典型 {ic_typical}) | {direction} | {signal} |

> 📌 **2026-07-27 实验性**: GA 挖的量价背离因子, 公式见 `tools/factors/alpha101/alpha_ga_001.py`
> 跨票稳定性: 4 张票 (300274/688012/002371/300308) Top 1 公式完全一致
> 使用建议: |因子| > 0.3 时关注, > 1.0 时强信号
> 详细分析见 `docs/ga_results_*.md`
"""
    except Exception as e:
        return f"> ⚠️ GA 因子计算失败: {e}\n"


def _section_peg(data: RenderData) -> str:
    # 2026-09-02 改: 读 data.valuation_data (ValuationStrategy 输出)
    _v = data.valuation_data or {}
    if _v.get("PEG_真实") is not None and _v.get("PEG_真实") != "数据不足":
        return f"""| 指标 | 数值 |
|---|---|
| **PEG_真实** | **{_v.get('PEG_真实')}** ({_v.get('verdict', '—')}) |
| Forward PE | {_v.get('fwd_pe', '—')} |
| g (稳态 CAGR) | {_v.get('g', '—')} |
"""
    return """> **数据状态:** ⚠️ PEG 未计算 (ValuationStrategy 缺 EPS 数据)
> **降级:** 用 PE_TTM 替代, 标 ⚠️
"""


def _section_dcf(data: RenderData) -> str:
    # 2026-09-02 改: 读 data.valuation_data
    _v = data.valuation_data or {}
    if _v.get("L_r10") is not None:
        return f"""| 折现率 r | 隐含终局利润 L | L/E3 |
|---|---|---|
| 8% | {_v.get('L_r8', '—')} 亿 | — |
| 10% | {_v.get('L_r10', '—')} 亿 | {_v.get('L_E3_r10', '—')}x |
| 12% | {_v.get('L_r12', '—')} 亿 | — |
"""
    return """> **数据状态:** ⚠️ DCF L 未计算 (ValuationStrategy 缺 EPS 数据)
"""

def _section_magic_formula_unused(data: RenderData) -> str:
    """2026-09-02 废弃: Magic 合并到 _section_eps, 这个函数保留只是占位避免引用错误。
    实际主模板不再调用, 报告渲染从 _section_eps 拿 data.valuation_data。
    """
    return ""


def _section_fundamental(data: RenderData) -> str:
    if not data.fundamental or "error" in data.fundamental:
        return "> **数据状态:** ❌ 基本面未计算 (财务数据不足)\n> **降级:** 用 EPS 一致预期 + PE_TTM 间接算\n"
    f = data.fundamental
    parts = [
        f"**综合评分:** {f.get('summary', '—')}\n",
        "| 维度 | 评分 | 说明 |",
        "|---|---|---|",
    ]
    for key, label in [
        ("valuation", "估值"),
        ("profitability", "盈利"),
        ("growth", "成长"),
        ("safety", "安全"),
    ]:
        d = f.get(key, {})
        score = d.get("score", 0)
        comment = d.get("comment", "—")
        if score >= 75:
            emoji = "🟢"
        elif score >= 50:
            emoji = "🟡"
        elif score >= 25:
            emoji = "🟠"
        else:
            emoji = "🔴"
        parts.append(f"| {label} | {score}/100 {emoji} | {comment} |")
    if f.get("missing"):
        parts.append(f"\n**数据缺失:** {', '.join(f['missing'])} (用 ROE 间接判断)")
    return "\n".join(parts) + "\n"


def _section_signal_5cat(data: RenderData) -> str:
    if not data.signal_5cat or "error" in data.signal_5cat:
        return "> **数据状态:** ❌ 5 类 14 子信号未计算\n> **降级:** 用 K 线 + 量价 + fflow 自动算 (部分项用中性 5/10 分)\n"
    s = data.signal_5cat
    parts = [
        f"**总规则分:** {s.get('raw_score', '—')}/123 → {s.get('rating', '—')}\n",
        "| 类别 | 子信号 | Score | 触发 | 权重 | 说明 |",
        "|---|---|---|---|---|---|",
    ]
    for row in s.get("signals", []):
        triggered = "✅" if row.get("triggered") else "❌"
        parts.append(
            f"| {row.get('category', '—')} | {row.get('name', '—')} "
            f"| {row.get('score', 0)}/10 | {triggered} | {row.get('weight', 0)} "
            f"| {row.get('reason', '—')} |"
        )
    if s.get("missing"):
        parts.append(f"\n**数据缺失类:** {', '.join(s['missing'])}")
    return "\n".join(parts) + "\n"


def _section_strategy(data: RenderData) -> str:
    """4 套交易策略 (新增, 接通 compute_strategy_signals)"""
    if not data.strategy or "error" in data.strategy:
        return "> **数据状态:** ❌ 4 套策略未计算 (技术指标缺失)\n> **降级:** 用 MACD/KDJ/BOLL 间接判断\n"
    s = data.strategy
    parts = [
        f"**综合判定:** {s.get('verdict', '—')}\n",
        "| 策略 | 信号 | 说明 |",
        "|---|---|---|",
    ]
    for strat in s.get("strategies", []):
        signal = strat.get("signal", "hold")
        emoji = "🟢 买" if signal == "buy" else "🔴 卖" if signal == "sell" else "⚪ 持"
        parts.append(f"| {strat.get('name', '—')} | {emoji} | {strat.get('reason', '—')} |")
    return "\n".join(parts) + "\n"


def _section_xgboost(data: RenderData) -> str:
    if data.xgboost_prob is not None:
        return f"""- **启动概率:** {data.xgboost_prob:.2f}
- **状态:** {'🟢 高' if data.xgboost_prob > 0.7 else '🟡 中' if data.xgboost_prob > 0.4 else '🔴 低'}
"""
    return """> **数据状态:** ⚠️ XGBoost 校准未运行 (可选, 需训练数据)
> **降级:** 报告只显示规则分
"""


def _section_sector_overheat(data: RenderData) -> str:
    if data.sector_overheat:
        so = data.sector_overheat
        # 兼容两种字段格式: {1w/1m/3m/ma20_dev} 或 {1周涨幅/1月涨幅/3月涨幅/判定}
        w1  = so.get('1w')  or _parse_pct(so.get('1周涨幅',  '0'))
        m1  = so.get('1m')  or _parse_pct(so.get('1月涨幅',  '0'))
        m3  = so.get('3m')  or _parse_pct(so.get('3月涨幅',  '0'))
        ma20 = so.get('ma20_dev') or _parse_pct(so.get('MA20偏离', '0'))
        verdict = so.get('verdict') or so.get('判定', '未算')
        return f"""- 1 周涨幅: {w1:+.1f}% {'🟠>10% 关注' if w1 > 10 else '✅<10% 安全'}
- 1 月涨幅: {m1:+.1f}% {'🟠>30% 减仓1/3' if m1 > 30 else '🟡>20% 关注' if m1 > 20 else '✅<20% 安全'}
- 3 月涨幅: {m3:+.1f}% {'🔴>100% 减半' if m3 > 100 else '🟠>50% 关注' if m3 > 50 else '✅<50% 安全'}
- MA20 偏离: {ma20:+.1f}% {'🔴>30% 立即减仓1/3' if ma20 > 30 else '🟠>20% 关注' if ma20 > 20 else '✅<20% 安全'}
- **综合:** {verdict}
"""
    if not data.can_calc_sector_overheat():
        return "> **数据状态:** ❌ 板块过热无法计算 (K线不足 90 天)\n"
    return "> **数据状态:** ⚠️ 板块过热数据具备但未生成，需重新 sync_watchlist_fresh\n"


def _section_take_profit(data: RenderData) -> str:
    if data.take_profit:
        tp = data.take_profit
        return f"""| 涨幅 | 触发价 | 操作 | 卖多少 |
|---|---|---|---|
| 当前 → +20% | ¥{tp.get('t1_price', '—')} | 卖 1/3 | {tp.get('t1_pct', 33)}% |
| +20% → +50% | ¥{tp.get('t2_price', '—')} | 再卖 1/3 | {tp.get('t2_pct', 33)}% |
| +50% → +100% | ¥{tp.get('t3_price', '—')} | 全清 | {tp.get('t3_pct', 33)}% |
"""
    return """> **数据状态:** ⚠️ 止盈 3 层未计算 (需当前价 + 持仓成本)
> **说明:** 由 mavis 套公式算
"""


def _section_stop_loss(data: RenderData) -> str:
    # 2026-09-03 改: stop_loss 字段不存在, 读 take_profit 里的 s1_price/s2_price/...
    sl = data.take_profit or {}
    if sl and sl.get("s1_price"):
        return f"""| 跌幅 | 触发价 | 操作 |
|---|---|---|
| -3% | ¥{sl.get('s1_price', '—')} | ⚠️ 检查基本面 |
| -5% | ¥{sl.get('s2_price', '—')} | 卖 1/3 |
| -8% | ¥{sl.get('s3_price', '—')} | 减半仓 |
| -10% | ¥{sl.get('s4_price', '—')} | 🛑 清仓 |
"""
    return """> **数据状态:** ⚠️ 止损 4 档未计算
"""


def _section_exit_signals(data: RenderData) -> str:
    if data.exit_signals:
        es = data.exit_signals
        return "\n".join(f"- {k}: {v}" for k, v in es.items())
    return """> **数据状态:** ⚠️ 退场信号检查未完成
> **检查项:** PEG>3 / L/E3>8 / fflow>30亿 / OBV 强背离 / MACD 高位死叉 / v11≤-3
"""


def _section_position_layer(data: RenderData) -> str:
    if data.position_layer:
        pl = data.position_layer
        # 兼容两种字段命名
        contrarian = pl.get('contrarian') or pl.get('逆势仓')
        base       = pl.get('base')       or pl.get('底仓', '未算')
        mid        = pl.get('mid')        or pl.get('中仓', '未算')
        wave       = pl.get('wave')       or pl.get('波动仓', '未算')
        summary    = pl.get('summary',    '未算')
        lines = []
        if contrarian:
            lines.append(f"- **逆势仓 10-15%:** {contrarian}")
        else:
            lines.append("- **逆势仓 10-15%:** 当前价格在中枢上方/内部，不适用逆势仓")
        lines += [f"- **底仓 25-30%:** {base}",
                  f"- **中仓 20-25%:** {mid}",
                  f"- **波动仓 20-25%:** {wave}",
                  f"\n**综合:** {summary}"]
        return "\n".join(lines) + "\n"
    return "> **数据状态:** ⚠️ 3 层仓位策略未生成，需重新 sync_watchlist_fresh\n"


def _fmt_date(d: str) -> str:
    """20260428 → 2026-04-28，带时间的取日期部分"""
    d = str(d).split(" ")[0].replace("-", "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) >= 8 else d


def _parse_pct(v) -> float:
    """把 '-7%' / '-7' / -7 统一转成 float"""
    try:
        return float(str(v).replace('%', '').strip())
    except Exception:
        return 0.0


def _chan_level_detail(chan_data: dict, level: str) -> dict:
    """从 chan_data 提取单个周期的缠论详细数据"""
    lv = chan_data.get(level) or {}
    hub = lv.get("hub") or {}
    segs = lv.get("segs") or []
    seg_idx = hub.get("seg_idx", [])
    # 中枢构成段：用 seg_idx 找到对应段的价格范围
    hub_segs = [segs[i] for i in seg_idx if 0 <= i < len(segs)]
    return {
        "hub_low": hub.get("low", 0),
        "hub_high": hub.get("high", 0),
        "hub_valid": hub.get("valid", False),
        "hub_pos": hub.get("pos", "—"),
        "seg_count": len(segs),
        "all_segs": segs,
        "hub_segs": hub_segs,
        "seg_idx": seg_idx,
    }


def _pos_emoji(pos: str) -> str:
    if "上方" in pos: return "✅"
    if "内部" in pos: return "⬜"
    if "跌穿" in pos: return "🔴"
    if "下方" in pos: return "⚠️"
    return "⬜"


def _wy_sub_indicators_md(wy_signals: list, level: str) -> str:
    """
    威科夫 5 子指标 → markdown 表格行
    5 子指标固定顺序 (wyckoff.py wyckoff_stage 输出): 120根斜率 / MA20偏 / MA60偏 / 60根位置 / 子事件
    """
    if not wy_signals:
        return f"| 1~5 | K线不足 (需≥60根) | — |"
    # 子事件行通常放最后, 单列展示
    rows = []
    for i, sig in enumerate(wy_signals, 1):
        # 截掉 (日线) 之类的标签, 因为已经按 level 区分
        sig_clean = sig.replace(f"({level if level!='daily' else '日线'})", "").replace("()", "").strip()
        if i == len(wy_signals):
            # 最后一行: 子事件
            rows.append(f"| {i} | {sig_clean.split(':', 1)[0] if ':' in sig_clean else '子事件'} | {sig_clean.split(':', 1)[1].strip() if ':' in sig_clean else sig_clean} |")
        else:
            name = sig_clean.split(":", 1)[0].strip() if ":" in sig_clean else sig_clean
            val  = sig_clean.split(":", 1)[1].strip() if ":" in sig_clean else "—"
            rows.append(f"| {i} | {name} | {val} |")
    return "\n".join(rows)


def _wy_criteria_md(stage: str, accum_d: dict, markup_d: dict, dist_d: dict, scores: dict) -> str:
    """
    威科夫 阶段判定细节 - 展示为什么判这个阶段

    3 大阶段各自的 AND 门条件 + 实际是否满足
    """
    # 判断每个条件
    def _yn(ok): return "✅" if ok else "❌"

    # 当前阶段的通过方式说明
    current_score = scores.get(stage, 0)
    pass_method = ""
    if stage == "Accumulation":
        if accum_d.get('pass_all', False):
            pass_method = "AND 门严格通过"
        elif current_score > 0:
            pass_method = "弱信号 fallback (横盘/低位 + 累积 sub-event)"
        else:
            pass_method = "未通过"
    elif stage == "Markup":
        if markup_d.get('golden_cross_recent', False) and markup_d.get('above_ma200_sustained', False):
            pass_method = "强信号 (金叉 + 持续上方)"
        elif current_score > 0:
            pass_method = "弱信号 / 起点 fallback"
        else:
            pass_method = "未通过"
    elif stage == "Distribution":
        if dist_d.get('distribution_start', False):
            pass_method = "AND 门严格通过 (派发起点)"
        elif current_score > 0:
            pass_method = "弱信号 (高位 + 派发 sub-event)"
        else:
            pass_method = "未通过"

    rows = []
    # 当前阶段总结
    rows.append(f"**当前阶段: {stage}** (score={current_score}) — 通过方式: **{pass_method}**")
    rows.append("")

    # Accumulation 累积条件
    rows.append("**Accumulation 累积** (AND 门, 需全满足才严格通过):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    rows.append(f"| base_low (现价在年内低点附近) | 现价 ≤ 年内 low × 1.45 | base_low_ok={accum_d.get('base_low_ok', '—')} | {_yn(accum_d.get('base_low_ok', False))} |")
    rows.append(f"| MA 胶着 (短期/长期均线接近) | \\|MA50-MA_long\\|/MA_long ≤ 8% | ma_gap_ok={accum_d.get('ma_gap_ok', '—')} | {_yn(accum_d.get('ma_gap_ok', False))} |")
    rows.append(f"| 量能萎缩 (近期 < 参考) | 20d 均量 / 120d 均量 < 75% | volume_dry_ok={accum_d.get('volume_dry_ok', '—')} | {_yn(accum_d.get('volume_dry_ok', False))} |")
    rows.append(f"| **子阶段** | 累积 A: 前置满足 / 累积 B: ≥3次测试底部 / 累积 C: 不破低 | b_test={accum_d.get('b_test_count', 0)} c_ok={accum_d.get('c_stage_ok', '—')} | — |")
    rows.append(f"| 综合通过 | 上面 +1 全满足 | pass_all={accum_d.get('pass_all', '—')} | {_yn(accum_d.get('pass_all', False))} |")
    rows.append("")

    # Markup 主升浪条件
    rows.append("**Markup 主升浪** (AND 门):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    rows.append(f"| MA50/MA200 金叉 | 最近 5 日内上穿 | golden_cross_recent={markup_d.get('golden_cross_recent', '—')} | {_yn(markup_d.get('golden_cross_recent', False))} |")
    rows.append(f"| 持续 MA200 上方 | 5 日都在 | above_ma200_sustained={markup_d.get('above_ma200_sustained', '—')} | {_yn(markup_d.get('above_ma200_sustained', False))} |")
    rows.append(f"| MA gap > 0.5% | (MA50-MA200)/MA200 > 0.5% | ma_gap_pct={markup_d.get('ma_gap_pct_above', 0):.2f}% | {_yn(markup_d.get('ma_gap_pct_above', 0) > 0.5)} |")
    rows.append(f"| MA50 角度 | ≥2%/5日 (markup_ma_angle_min) | ma50_angle_5d={markup_d.get('ma50_angle_5d', 0):.2f}% | {_yn(markup_d.get('ma50_angle_5d', 0) >= 2.0)} |")
    rows.append("")

    # Distribution 派发条件
    rows.append("**Distribution 派发** (AND 门强信号 / 弱信号 fallback):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    rows.append(f"| bias_200 偏离 | bias_200 > 30% (强) / > 15% (弱) | bias_200={dist_d.get('bias_200', 0):.1f}% | {_yn(dist_d.get('bias_200_ok', False))} |")
    rows.append(f"| 3 日连续缩量 | 3d 均量 < 60d × 0.5 | vol_dry_3d={dist_d.get('vol_dry_3d', '—')} | {_yn(dist_d.get('vol_dry_3d', False))} |")
    rows.append(f"| 派发起点 | bias_200>30% AND vol_dry_3d | distribution_start={dist_d.get('distribution_start', '—')} | {_yn(dist_d.get('distribution_start', False))} |")
    return "\n".join(rows)


def _wy_sub_events_md(events: list, glossary: dict, current_stage: str) -> str:
    """
    13 种 sub-event 触发情况 + 中文释义 + 所属阶段 (2026-07-28 加 JAC, LPSY→LPS 对齐 WyckoffTradingAgent)
    """
    # 按 sub_event 全集展示 (没触发的也展示, 用 ❌)
    # 9 sub-event 跟 WyckoffTradingAgent L4 完全对齐 (2026-07-28 清理)
    # 之前 13 个: 删 PSY/SC/AR/ST/BC/UT/SOW/JAC, 补 Compression/TrendPullback/MarkupEntry/DistributionStart
    ALL_EVENTS = ["Spring", "LPS", "EVR",
                  "SOS", "Compression", "TrendPullback", "MarkupEntry",
                  "DistributionStart", "UTAD"]
    # 阶段关键词匹配 (用关键词而非完全相等)
    STAGE_KEYWORDS = {
        "Accumulation": ["Accumulation", "累积"],
        "Markup": ["Markup", "主升浪", "起点触发"],
        "Distribution": ["Distribution", "派发"],
    }
    current_keywords = STAGE_KEYWORDS.get(current_stage, [current_stage])
    rows = []
    # v5.10.42 兼容: events 可能是 list[str] (老格式) 或 list[dict] (新格式, v5.10.42+)
    triggered = set()
    last_date_map = {}  # name -> 最近日期
    for e in events:
        if isinstance(e, dict):
            name = e.get("name", "")
            triggered.add(name)
            # 记录最近日期 (按 idx 倒序最后一个)
            d = e.get("date", "—")
            # v5.10.42: 统一日期格式 (周线是 20240607 没 -, 加上 -)
            if isinstance(d, str) and len(d) == 8 and d.isdigit():
                d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            last_date_map[name] = d
        elif isinstance(e, str):
            triggered.add(e)
    rows.append("| # | 事件 | 中文 | 含义 | 所属阶段 | 触发 | 最近 |")
    rows.append("|---|---|---|---|---|---|---|")
    for i, code in enumerate(ALL_EVENTS, 1):
        name, meaning, stage_aff = glossary.get(code, (code, "—", "—"))
        is_triggered = code in triggered
        mark = "✅" if is_triggered else "❌"
        # 标记当前阶段触发的关键 sub-event
        stage_mark = ""
        if is_triggered and any(kw in stage_aff for kw in current_keywords):
            stage_mark = " ⭐"  # 当前阶段触发的关键 sub-event
        # v5.10.42: 显示最近触发日期
        last_d = last_date_map.get(code, "—") if is_triggered else "—"
        rows.append(f"| {i} | {code} | {name} | {meaning} | {stage_aff} | {mark}{stage_mark} | {last_d} |")
    rows.append("")
    rows.append(f"**当前触发: {len(triggered)} / 9 种** — " +
                ("⚠️ 触发数量多说明主力操作密集, 配合当前阶段判定解读" if len(triggered) >= 6
                 else "✅ 触发数量正常"))
    return "\n".join(rows)


# ============================================================
# SMC section 辅助函数 (跟威科夫 4 段式风格一致, 2026-07-25)
# ============================================================

# SMC 释义 (跟 OpenMobius 命名 + 中文)
SMC_GLOSSARY = {
    "buy_side_sweep": ("buy-side 扫流", "扫 swing high 上方空头止损, 主力吸筹"),
    "sell_side_sweep": ("sell-side 扫流", "扫 swing low 下方多头止损, 主力派发"),
    "fvg_bull": ("看涨 FVG", "3 根 K 线 + 中间阳线, 价格真空在下方"),
    "fvg_bear": ("看跌 FVG", "3 根 K 线 + 中间阴线, 价格真空在上方"),
    "ob_bull": ("看涨 OB", "阴线 + 1.5×ATR 强势上涨, 主力成本区"),
    "ob_bear": ("看跌 OB", "阳线 + 1.5×ATR 强势下跌, 主力出货区"),
}


def _smc_criteria_md(bull_ob, bear_ob, fvg_bull, fvg_bear, sweeps, smc_score) -> str:
    """
    SMC 4 要素判定细节 (跟威科夫 AND 门风格)
    """
    def _yn(ok): return "✅" if ok else "❌"

    rows = []

    # FVG 判定
    rows.append("**FVG (缺口)** — 0.2×ATR gap 过滤 + 3 根 K 线 non-overlap:")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    if fvg_bull or fvg_bear:
        fvg = fvg_bull or fvg_bear
        gap = fvg.get('size_atr', 0)
        mit = fvg.get('mitigation_pct', 0)
        fvg_type = "看涨" if fvg_bull else "看跌"
        rows.append(f"| gap 尺寸 | ≥ 0.2×ATR | size={gap:.1f}×ATR | {_yn(gap >= 0.2)} |")
        rows.append(f"| 3 根 K 线 | K1.high<K3.low (bull) / K1.low>K3.high (bear) | {fvg_type} FVG | ✅ |")
        rows.append(f"| 中间推动 | bull=阳线, bear=阴线 | 当前{fvg_type} | ✅ |")
        # mitigation 关键解读
        if mit < 30:
            mit_status = f"回补 {mit:.0f}% (**最佳进场点**, 真空完整)"
        elif mit < 70:
            mit_status = f"回补 {mit:.0f}% (中段, 谨慎)"
        elif mit < 100:
            mit_status = f"回补 {mit:.0f}% (失效边缘, 不可进场)"
        else:
            mit_status = f"回补 {mit:.0f}% (真空已填满, 失效)"
        rows.append(f"| 回补进度 | 0-30%最佳 / 70%失效 | {mit_status} | — |")
    else:
        rows.append(f"| FVG 存在 | 3 根 K 线 + 0.2×ATR gap | 无 FVG | ❌ |")
    rows.append("")

    # OB 判定
    rows.append("**Order Block (订单块)** — 最后反向 K + 1.5×ATR displacement:")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    if bull_ob or bear_ob:
        ob = bull_ob or bear_ob
        disp = ob.get('displacement_atr', 0)
        ob_type = "看涨" if bull_ob else "看跌"
        rows.append(f"| displacement 强度 | 后续 1-3 根累计 ≥ 1.5×ATR | strength={disp:.1f}×ATR | {_yn(disp >= 1.5)} |")
        rows.append(f"| 反向 K 线 | bull=阴线, bear=阳线 | 当前{ob_type} OB | ✅ |")
        rows.append(f"| OB 区间 | bull=[low,open], bear=[open,high] | 主力真实成本区 | ✅ |")
    else:
        rows.append(f"| OB 存在 | 1.5×ATR displacement | 无 OB | ❌ |")
    rows.append("")

    # Sweep 判定
    rows.append("**Liquidity Sweep (扫流)** — 15 根 lookback + buy/sell-side 命名:")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    if sweeps:
        buy_n = len([s for s in sweeps if s.get('type') == 'buy_side_sweep'])
        sell_n = len([s for s in sweeps if s.get('type') == 'sell_side_sweep'])
        max_wick = max((s.get('wick_size', 0) for s in sweeps), default=0)
        last = sweeps[0]
        last_type = "buy_side 吸筹" if last.get('type') == 'buy_side_sweep' else "sell_side 派发"
        rows.append(f"| swing lookback | ≤ 15 根 (散户还记得) | 当前 {len(sweeps)} 个扫流 | ✅ |")
        rows.append(f"| K 线穿越+收回 | high>swing AND close<swing (buy) | 扫 {last.get('swept_level', 0):.2f} | ✅ |")
        rows.append(f"| 类型分布 | buy-side (吸筹) + sell-side (派发) | buy {buy_n} / sell {sell_n} | — |")
        rows.append(f"| 影线深度 | 长影线 = 主力深度扫流 | 最大 {max_wick:.2f} | — |")
        rows.append(f"| 当前主导 | 主力意图 | **{last_type}** | — |")
    else:
        rows.append(f"| 扫流存在 | swing 穿越 + 15 根内 | 无扫流 | ❌ |")
    rows.append("")

    # 4 要素综合判定
    rows.append("**4 要素综合:**")
    rows.append("")
    rows.append(f"- 3 类信号评分: **{smc_score}/3**")
    if smc_score == 3:
        rows.append("- 综合判定: **🟢 强 SMC 信号** — 3 类信号齐发, 主力意图明确")
    elif smc_score == 2:
        rows.append("- 综合判定: **🟠 标准 SMC 信号** — 2 类信号, 主力有动作")
    elif smc_score == 1:
        rows.append("- 综合判定: **🟡 弱 SMC 信号** — 1 类信号, 需其他方法配合")
    else:
        rows.append("- 综合判定: **✅ 无 SMC 信号** — 主力无明显动作")
    return "\n".join(rows)


def _smc_sub_events_md(sweeps, fvg_bull, fvg_bear, bull_ob, bear_ob,
                      total_obs, total_fvgs, total_sweeps) -> str:
    """
    3 类 SMC sub-event 触发情况 (跟威科夫 12 sub-event 同款, 3 类是 SMC 的核心)
    """
    rows = ["| # | 事件 | 中文 | 含义 | 所属类型 | 触发 |",
            "|---|---|---|---|---|---|"]

    # FVG 触发情况
    fvg_bull_ok = "✅ ⭐" if fvg_bull else "❌"
    fvg_bear_ok = "✅ ⭐" if fvg_bear else "❌"
    rows.append(f"| 1 | FVG_bull | 看涨 FVG | 3 根 K 线 + 中间阳线, 价格真空在下方 | FVG (缺口) | {fvg_bull_ok} |")
    rows.append(f"| 2 | FVG_bear | 看跌 FVG | 3 根 K 线 + 中间阴线, 价格真空在上方 | FVG (缺口) | {fvg_bear_ok} |")

    # OB 触发情况
    ob_bull_ok = "✅ ⭐" if bull_ob else "❌"
    ob_bear_ok = "✅ ⭐" if bear_ob else "❌"
    rows.append(f"| 3 | OB_bull | 看涨 OB | 阴线 + 1.5×ATR 强势上涨, 主力成本区 | Order Block | {ob_bull_ok} |")
    rows.append(f"| 4 | OB_bear | 看跌 OB | 阳线 + 1.5×ATR 强势下跌, 主力出货区 | Order Block | {ob_bear_ok} |")

    # Sweep 触发情况
    buy_sweeps = [s for s in sweeps if s.get('type') == 'buy_side_sweep']
    sell_sweeps = [s for s in sweeps if s.get('type') == 'sell_side_sweep']
    # buy-side sweep: ≥ 1 个触发, 多发 (≥2) 极强
    if len(buy_sweeps) >= 2:
        buy_mark = "✅ ⭐"
    elif len(buy_sweeps) >= 1:
        buy_mark = "✅"
    else:
        buy_mark = "❌"
    if len(sell_sweeps) >= 2:
        sell_mark = "✅ ⭐"
    elif len(sell_sweeps) >= 1:
        sell_mark = "✅"
    else:
        sell_mark = "❌"
    rows.append(f"| 5 | buy_side_sweep | buy-side 扫流 | 扫 swing high 上方空头止损, 主力吸筹 | Liquidity Sweep | {buy_mark} |")
    rows.append(f"| 6 | sell_side_sweep | sell-side 扫流 | 扫 swing low 下方多头止损, 主力派发 | Liquidity Sweep | {sell_mark} |")

    # 汇总
    triggered_count = sum([
        1 if fvg_bull else 0,
        1 if fvg_bear else 0,
        1 if bull_ob else 0,
        1 if bear_ob else 0,
        1 if buy_sweeps else 0,
        1 if sell_sweeps else 0,
    ])
    rows.append("")
    rows.append(f"**当前触发: {triggered_count} / 6 种** — " +
                ("⚠️ 触发多说明主力操作密集, 配合威科夫 5 阶段判定解读"
                 if triggered_count >= 4 else "✅ 触发数量正常"))
    return "\n".join(rows)


def _section_period(data: RenderData, level: str, label: str, weight: str,
                    vp_windows: tuple) -> str:
    """
    单周期 section：缠论详细数据 + 其余4方法在该周期视角的数据
    level: 'weekly' | 'daily' | '60min'
    label: '周线' | '日线' | '60分'
    weight: '1.5x' | '1.0x' | '0.5x'
    vp_windows: 量价关注的时间窗口 tuple，如 ('20d','30d','60d')
    """
    s5 = data.analysis or {}
    chan = data.chan_data or {}
    cd = _chan_level_detail(chan, level)
    # v5.10.42: 3 周期 sub_events 都从 s5.get("wyckoff").sub_events_by_period[level] 拿
    _wy_data_for_sub = s5.get("wyckoff") or {}
    _sub_by_period = _wy_data_for_sub.get("sub_events_by_period", {}) or {}
    _level_key_map = {"weekly": "weekly", "daily": "daily"}
    _level_sub_events = _sub_by_period.get(_level_key_map.get(level, level), []) or []

    # ── 1. 缠论 ─────────────────────────────────────────────
    hub_str = (f"¥{cd['hub_low']:.2f}~¥{cd['hub_high']:.2f}"
               if cd["hub_valid"] else "未形成中枢")
    pos_e = _pos_emoji(cd["hub_pos"]) if cd["hub_valid"] else ""
    hub_pos_str = cd["hub_pos"] if cd["hub_valid"] else f"无中枢({cd['seg_count']}段，段数不足3)"
    price = data.current_price or 0
    if cd["hub_valid"] and cd["hub_low"] > 0:
        dist = (price - cd["hub_low"]) / cd["hub_low"] * 100
        hub_dist = f"{dist:+.1f}% vs 下沿"
    else:
        hub_dist = "需形成中枢后判断"

    # 中枢构成段（展开价格）
    # 2026-07-25 修复: 标题与表格间空行 (让所有 markdown viewer 都能识别表格)
    # 不加表格前空行 — 上方已有 {hub_str} (位置说明) 自然隔开
    hub_segs_md = ""
    if cd["hub_segs"]:
        hub_segs_md = "\n**中枢构成段 (seg_idx={}):**\n\n| 方向 | 起价 | 止价 | 笔数 | 起日 | 止日 |\n|---|---|---|---|---|---|\n".format(cd["seg_idx"])
        for seg in cd["hub_segs"]:
            hub_segs_md += (f"| {seg.get('dir','—')} | ¥{seg.get('sp',0):.2f} | "
                            f"¥{seg.get('ep',0):.2f} | {seg.get('nb',0)} | "
                            f"{_fmt_date(seg.get('sdt','—'))} | {_fmt_date(seg.get('edt','—'))} |\n")

    # 最近走段（最多 6 段）
    # 2026-07-25 修复: 标题与表格间空行 (让所有 markdown viewer 都能识别)
    recent_segs = cd["all_segs"][-6:] if cd["all_segs"] else []
    segs_md = ""
    if recent_segs:
        segs_md = "\n**最近走段 (最近 {} 段/共 {} 段):**\n\n| 方向 | 起价 | 止价 | 笔数 | 起日 | 止日 |\n|---|---|---|---|---|---|\n".format(
            len(recent_segs), cd["seg_count"])
        for seg in recent_segs:
            segs_md += (f"| {seg.get('dir','—')} | ¥{seg.get('sp',0):.2f} | "
                        f"¥{seg.get('ep',0):.2f} | {seg.get('nb',0)} | "
                        f"{_fmt_date(seg.get('sdt','—'))} | {_fmt_date(seg.get('edt','—'))} |\n")

    # ── 2. 威科夫 ────────────────────────────────────────────
    wy = s5.get("wyckoff") or {}
    wy_stage = wy.get("stage", "?")
    wy_name = wy.get("stage_name", "—")
    wy_conf = wy.get("confidence", 0)
    wy_action = wy.get("action", "—")
    wy_signals = wy.get("signals", [])
    # 周期视角映射
    # v5.10.42 改: 3 周期都从 s5.get("wyckoff") 拿 (新格式 sub_events_by_period[level])
    if level == "daily":
        wy_data = s5.get("wyckoff") or {}
        wy_focus_label = wy_data.get("action", "等待")
    elif level == "weekly":
        wy_data = s5.get("wyckoff_weekly") or s5.get("wyckoff") or {}
        wy_focus_label = "长线趋势"
    else:
        wy_data = s5.get("wyckoff") or {}
        wy_focus_label = wy_data.get("action", "等待")
    wy_stage = wy_data.get("stage", "?")
    wy_name  = wy_data.get("stage_name", "未知")
    wy_conf  = wy_data.get("confidence", 0)
    wy_action= wy_data.get("action", "等待")
    wy_stage_detail = wy_data.get("stage_detail", "")
    # v5.10.42: 优先用本周期重算的 sub_events_by_period[level] (新格式 list[dict] 带日期)
    sub_events = _level_sub_events if _level_sub_events else wy_data.get("sub_events", [])
    scores = wy_data.get("scores", {})
    wy_signals = wy_data.get("signals", [])  # 5 子指标
    wy_progress = wy_data.get("phase_progress", 0)
    # v4: 阶段子标签
    wy_stage_detail_str = f"/{wy_stage_detail}" if wy_stage_detail else ""
    # v4: 阶段判定细节 (3 大阶段各自的 AND 门条件)
    accum_d = wy_data.get("accum_detail", {})
    markup_d = wy_data.get("markup_detail", {})
    dist_d = wy_data.get("distribution_detail", {})
    wy_criteria_md = _wy_criteria_md(wy_stage, accum_d, markup_d, dist_d, scores)
    # v5 (2026-07-28): 9 种 sub-event, 跟 WyckoffTradingAgent L4 对齐
    from tools.factors.wyckoff.detectors import SUB_EVENT_GLOSSARY
    wy_sub_events_md = _wy_sub_events_md(sub_events, SUB_EVENT_GLOSSARY, wy_stage)
    scores_str = " / ".join(f"{k}:{v}" for k, v in scores.items() if v > 0) if scores else "无得分"
    if wy_stage == "?" or wy_conf == 0:
        wy_focus = f"K线不足（需≥30根）— {wy_focus_label}"
    else:
        wy_focus = f"阶段 {wy_stage} ({wy_name}) {wy_conf}% — {wy_action if level=='daily' else wy_focus_label}"

    # ── 3. SMC (各周期用真实独立数据) ────────────────────────────
    if level == "weekly":
        smc = s5.get("smc_weekly") or s5.get("smc") or {}
    else:
        smc = s5.get("smc") or {}

    bull_ob  = smc.get("nearest_bull_ob")  or {}
    bear_ob  = smc.get("nearest_bear_ob")  or {}
    fvg_bull = smc.get("nearest_fvg_bull") or {}
    fvg_bear = smc.get("nearest_fvg_bear") or {}
    sweeps   = smc.get("recent_sweeps")    or []

    # v3.1 (2026-07-25): 字段名 OpenMobius 对齐 — top/bottom 替代 low/high
    # 老字段 (low/high) 也兼容 (兜底)
    def _ob_top(ob): return ob.get('top') if 'top' in ob else ob.get('high', 0)
    def _ob_bottom(ob): return ob.get('bottom') if 'bottom' in ob else ob.get('low', 0)
    def _ob_strength(ob): return ob.get('displacement_atr', ob.get('strength', 0))
    def _fvg_top(f): return f.get('top', 0)
    def _fvg_bottom(f): return f.get('bottom', 0)
    def _fvg_mitigation(f): return f.get('mitigation_pct', 0)
    def _fvg_size_atr(f): return f.get('size_atr', 0)
    def _sweep_level(s): return s.get('swept_level', s.get('level', 0))
    def _sweep_wick(s): return s.get('wick_size', 0)

    # 总览: 4 类信号统计
    smc_total_obs = smc.get("total_obs", 0)
    smc_total_fvgs = smc.get("total_fvgs", 0)
    smc_total_sweeps = len(sweeps)

    # 3 类信号评分 (FVG / OB / Sweep 各 1 分, 总分 0-3)
    smc_score = sum([
        1 if fvg_bull or fvg_bear else 0,
        1 if bull_ob or bear_ob else 0,
        1 if sweeps else 0,
    ])
    # 强弱判定
    if smc_score >= 3:
        smc_strength = "🟢 强"
    elif smc_score == 2:
        smc_strength = "🟠 标准"
    elif smc_score == 1:
        smc_strength = "🟡 弱"
    else:
        smc_strength = "✅ 无信号"

    # 扫流分组: buy-side (吸筹) / sell-side (派发)
    buy_side_sweeps = [s for s in sweeps if s.get('type') == 'buy_side_sweep']
    sell_side_sweeps = [s for s in sweeps if s.get('type') == 'sell_side_sweep']

    # 总览行
    lines_smc = []
    if bull_ob:
        ob = bull_ob
        lines_smc.append(f"多OB支撑 ¥{_ob_bottom(ob):.2f}~¥{_ob_top(ob):.2f}")
    if bear_ob:
        ob = bear_ob
        lines_smc.append(f"空OB压力 ¥{_ob_bottom(ob):.2f}~¥{_ob_top(ob):.2f}")
    if fvg_bull:
        f = fvg_bull
        lines_smc.append(f"多FVG缺口 ¥{_fvg_bottom(f):.2f}~¥{_fvg_top(f):.2f}")
    if fvg_bear:
        f = fvg_bear
        lines_smc.append(f"空FVG回补 ¥{_fvg_bottom(f):.2f}~¥{_fvg_top(f):.2f}")
    if sweeps:
        sw = sweeps[0]
        type_zh = "buy_side吸筹" if sw.get('type') == "buy_side_sweep" else "sell_side派发"
        lines_smc.append(f"近期{type_zh} {sw.get('date', '?')} ¥{_sweep_level(sw):.2f}")
    smc_focus = " / ".join(lines_smc) if lines_smc else smc.get("summary", "无明显 SMC 信号")

    # ── 4. 量价 fflow (2026-08-17 拆分: 跟 OBV 独立) ──────────
    fflow = s5.get("fflow") or {}
    fflow_verdict = fflow.get("verdict", "—")
    short_w, mid_w, long_w = vp_windows

    def _fflow_val(w: str) -> str:
        v = fflow.get(f"fflow_net_{w}")
        t = fflow.get(f"trend_{w}", "")
        if v is None: return "无数据"
        return f"{v:+.2f}亿 {t}"

    fflow_focus = f"{short_w}: {_fflow_val(short_w)} / {mid_w}: {_fflow_val(mid_w)} / {long_w}: {_fflow_val(long_w)}"

    # ── 4b. 量价 OBV (经典 Granville 1963, 简化: 只看 verdict) ──
    obv = s5.get("obv") or {}
    obv_verdict = obv.get("verdict", "")
    if obv_verdict:
        obv_focus = f"OBV {obv_verdict}"
    else:
        obv_focus = "OBV 无数据"

    # ── 5. 多市场共振 (已移除网络数据源，显示占位)
    res_focus = "多市场共振: 已移除 (resonance_3period 网络调用删除)"

    # 止跌信号（仅日线计算，周线显示说明）
    if level == "weekly":
        stop_signal = "周线不适用 (看日线止跌)"
    else:  # daily
        stop_signal = "❓ K线不足"
        if data.kline and len(data.kline) >= 2:
            kl = data.kline
            bar = kl[-1]
            try:
                rng = bar.high - bar.low
                lower_shadow = (min(bar.open, bar.close) - bar.low) / rng if rng > 0 else 0
                # 2026-09-03 修: KLineBar 字段是 volume 不是 vol
                vol_attr = getattr(bar, 'volume', None) or getattr(bar, 'vol', 0)
                vol_ma20 = sum(getattr(k, 'volume', None) or getattr(k, 'vol', 0) for k in kl[-21:-1]) / 20 if len(kl) >= 21 else 0
                shrink = vol_attr < vol_ma20 * 0.85 if vol_ma20 > 0 else False
                long_lower = lower_shadow > 0.40
                sigs = []
                if shrink: sigs.append("✅缩量")
                else: sigs.append("❌量未缩")
                if long_lower: sigs.append(f"✅长下影({lower_shadow:.0%})")
                else: sigs.append(f"❌下影短({lower_shadow:.0%})")
                sigs.append("⏳次日待确认")
                stop_signal = " ".join(sigs)
            except Exception:
                stop_signal = "❓ 计算异常"

    # 买卖点 (从 buy_sell_points 取对应周期)
    bsp_level_key = {"weekly": "weekly", "daily": "daily"}.get(level, "daily")
    bsp = (data.buy_sell_points or {}).get(bsp_level_key, {})
    bsp_action = bsp.get("action", "观察")
    bsp_active = [(k, v) for k, v in bsp.items()
                  if k != "action" and v and v != "无" and v != "—"]
    bsp_str = " / ".join(f"{k}={v}" for k, v in bsp_active) if bsp_active else "无有效买卖点"

    return f"""> 权重 **{weight}** | 中枢: {hub_str} ({pos_e}{hub_pos_str}, {hub_dist}) | 段数: {cd['seg_count']}

**【缠论详情】**

| 项目 | 内容 |
|---|---|
| 中枢区间 | {hub_str} |
| 价格位置 | {pos_e} {hub_pos_str} ({hub_dist}) |
| 止跌信号 | {stop_signal} |
| 总段数 | {cd['seg_count']} 段 |
| **买卖点** | **{bsp_action}** — {bsp_str} |
{hub_segs_md}{segs_md}
**【威科夫详情】** (对齐 WyckoffTradingAgent 3 大阶段: Accumulation / Markup / Distribution)

| 项目 | 内容 |
|---|---|
| 当前阶段 | **{wy_stage}** ({wy_name}) {wy_stage_detail_str} — 置信度 {wy_conf}% |
| 阶段进度 | {wy_progress}% |
| 操作建议 | {wy_action if level=='daily' else wy_focus_label} |

**📊 阶段判定细节 (为什么判这个阶段?):**

{wy_criteria_md}

**🎯 3 大阶段评分:**

| Accumulation 累积 | Markup 主升浪 | Distribution 派发 |
|---|---|---|
| {scores.get('Accumulation', 0)} | {scores.get('Markup', 0)} | {scores.get('Distribution', 0)} |

**📈 基础指标 (5 维度):**

| # | 指标 | 数值 |
|---|---|---|
{_wy_sub_indicators_md(wy_signals, level)}

**🔍 9 种 sub-event 触发情况 (2026-07-28 跟 WyckoffTradingAgent L4 对齐):**

{wy_sub_events_md}

**【SMC Order Block / FVG】** (对齐 OpenMobius: Order Block + FVG + Liquidity Sweep + ATR 过滤)

| 项目 | 内容 |
|---|---|
| 3 类信号评分 | **{smc_strength}** ({smc_score}/3) — OB / FVG / Sweep 各 1 分 |
| OB 总数 | {smc_total_obs} (1.5×ATR displacement 过滤) |
| FVG 总数 | {smc_total_fvgs} (0.2×ATR 过滤) |
| 扫流总数 | {smc_total_sweeps} (15 根 lookback) |
| 扫流分布 | buy-side吸筹 {len(buy_side_sweeps)} 个 / sell-side派发 {len(sell_side_sweeps)} 个 |
| 核心结论 | {smc_focus} |

**📊 4 个核心要素判定细节 (跟威科夫一样的 AND 门风格):**

{_smc_criteria_md(bull_ob, bear_ob, fvg_bull, fvg_bear, sweeps, smc_score)}

**🎯 3 类信号评分:**

| FVG (缺口) | OB (订单块) | Liquidity Sweep (扫流) |
|---|---|---|
| {1 if fvg_bull or fvg_bear else 0} (gap ≥ 0.2×ATR + mitigation) | {1 if bull_ob or bear_ob else 0} (displacement ≥ 1.5×ATR) | {1 if sweeps else 0} (15 根 lookback + buy/sell_side) |

**🔍 3 类 sub-event 触发情况 (跟威科夫 12 sub-event 同款):**

{_smc_sub_events_md(sweeps, fvg_bull, fvg_bear, bull_ob, bear_ob, smc_total_obs, smc_total_fvgs, smc_total_sweeps)}

**【量价 fflow】**

{fflow_focus}

  > 综合判定: {fflow_verdict}

**【量价 OBV 段背离】** (2026-08-17 拆分独立, Granville 1963 + Lee-Swaminathan 2000)

{obv_focus}

**【多市场共振】**

{res_focus}
"""


def _section_weekly(data: RenderData) -> str:
    """📋 周线分析 (5 方法)"""
    if not data.chan_data and not data.analysis:
        return "> **数据状态:** ⚠️ 缠论/5方法数据未生成\n"
    return _section_period(data, "weekly", "周线", "1.5x", ("20d", "30d", "60d"))


def _section_daily(data: RenderData) -> str:
    """📋 日线分析 (5 方法)"""
    if not data.chan_data and not data.analysis:
        return "> **数据状态:** ⚠️ 缠论/5方法数据未生成\n"
    return _section_period(data, "daily", "日线", "1.0x", ("5d", "10d", "20d"))


def _section_factor_matrix(data: RenderData) -> str:
    """
    🎯 因子 × 3周期 综合矩阵 (2026-07-25 重构: 用 factor_matrix 模块, 2026-08-17 改名)

    1 个 section 包含:
      - 因子 × 3 周期 矩阵 (含中枢 + 123 买卖点 + 建议买入/卖出价)
      - 实战建议 (中枢下沿买入 / 上沿卖出)

    跨 4 批 38 只 388 样本验证 (2026-07-24)

    注意: 复用 analysis 已算好的数据, render 阶段 0 重复计算
    """
    if not data.analysis:
        return "> **❌ 数据缺失:** 因子矩阵未生成\n"

    try:
        from tools.analysis.factor_matrix import (
            build_factor_matrix,
            render_factor_matrix_md,
        )
        matrix = build_factor_matrix(
            code=data.code,
            name=data.name,
            current_price=data.current_price or 0,
            signals_5method=data.analysis,  # 参数名暂保留, v5.10.26+ 内部就是 analysis dict
            chan_data=data.chan_data or {},
            buy_sell_points=data.buy_sell_points or {},
        )
        return render_factor_matrix_md(matrix)
    except Exception as e:
        # 降级 fallback: 老的因子渲染 (避免报告空白)
        return f"> **❌ factor_matrix 调用失败:** {e}\n"


def _section_monitor(data: RenderData) -> str:
    if data.monitor_triggers:
        mt = data.monitor_triggers
        # 兼容两种字段命名
        add    = mt.get('add')    or mt.get('加仓信号') or mt.get('加仓触发', '未设定')
        reduce = mt.get('reduce') or mt.get('减仓信号') or mt.get('减仓触发', '未设定')
        clear  = mt.get('clear')  or mt.get('清仓信号') or mt.get('清仓触发', '未设定')
        time_t = mt.get('time')   or mt.get('时间触发',  '见 T 框架事件')
        return f"""- **加仓触发:** {add}
- **减仓触发:** {reduce}
- **清仓触发:** {clear}
- **时间触发:** {time_t}
"""
    return "> **数据状态:** ⚠️ 监控触发点未生成，需重新 sync_watchlist_fresh\n"


def _section_four_q_short(data: RenderData) -> str:
    """报告头的简短四问 + 评级"""
    if data.four_questions:
        fq = data.four_questions
        # 2026-09-02 改: 读 data.valuation_data.PEG_真实 (旧 peg_detail 字段已合并)
        peg_str = fq.get('peg_summary')
        if not peg_str:
            _v = data.valuation_data or {}
            peg_val = _v.get('PEG_真实')
            if isinstance(peg_val, (int, float)):
                peg_str = f"{peg_val:.2f}"
            else:
                peg_str = "未算"
        return f"""**评级:** {fq.get('verdict', '待判定')} | **龙头:** {fq.get('leader_score', '?')}/14 | **PEG:** {peg_str}"""
    return "**评级:** ⏳ 等待四问评分"


# ============================================================
# Tushare 补充段 + 缠论三要素
# ============================================================

def _hub_str(h: dict) -> str:
    """中枢字符串: ¥low~high{✅/⚠️/🔴/⬜}, 上方✅/下方⚠️/跌穿🔴/内部⬜
    2026-08-26 改: 加 "跌穿🔴" 映射, 之前落 "⬜" 是 bug
    """
    if h and h.get("valid") and h.get("low") and h.get("high"):
        pos = h.get("pos", "")
        if "上方" in pos:
            icon = "✅"
        elif "跌穿" in pos:
            icon = "🔴"
        elif "下方" in pos:
            icon = "⚠️"
        else:
            icon = "⬜"
        return f"¥{h['low']:.0f}~{h['high']:.0f}{icon}"
    return "—"


def _bsp_str(pts: dict) -> str:
    """买卖点字符串: "0买 1买⭐ 2买" (按级别排, 不带 timeframe label)
    2026-07-31 提到模块级
    2026-08-26 改: 加上其他 czsc BSP 类型 (吞没/MACD底背/双中枢/笔结束 等)

    2026-08-01: 硬 assert 禁 '|' — markdown 表格里 | 是 cell 分隔符, 用 | join 必错位
    """
    if not pts: return "—"
    out = []
    seen = set()
    def _add(label):
        if label not in seen:
            seen.add(label)
            out.append(label)
    for k in pts:
        # 1买/2买/3买/1卖/2卖/3卖 (123 买卖点, 优先识别)
        if "1买⭐" in k:   _add("1买⭐")
        elif "1买" in k:   _add("1买")
        elif "2买" in k:   _add("2买")
        elif "3买" in k:   _add("3买")
        elif "0买" in k:   _add("0买")
        elif "1卖⭐" in k:  _add("1卖⭐")
        elif "1卖" in k:   _add("1卖")
        elif "2卖" in k:   _add("2卖")
        elif "3卖" in k:   _add("3卖")
        # 2026-08-26 改: 其他 czsc BSP 类型 (抄底/逃顶/形态/笔信号)
        # DIF走平/MACD开仓 是连续状态信号，不在买卖点列显示（在"变化"列显示）
        elif "MACD底背" in k:    _add("底背")
        elif "MACD顶背" in k:    _add("顶背")
        elif "笔结束" in k:      _add("笔结束")
        elif "双中枢" in k:      _add("双中枢")
        elif "吞没" in k:        _add("吞没")
    result = " ".join(out) if out else "—"
    assert "|" not in result, f"_bsp_str 禁 '|' 字符 (markdown cell 分隔符), got: {result!r}"
    return result


def _smc_str(row: dict) -> str:
    """SMC 字符串: OB支¥X / OB压¥X / FVG支¥X / FVG压¥X / 🟢🔴扫¥X
    2026-07-31 提到模块级
    """
    parts = []
    bull = row.get("smc_bull_ob")
    bear = row.get("smc_bear_ob")
    fvg_b = row.get("smc_fvg_bull")
    fvg_r = row.get("smc_fvg_bear")
    sweeps = row.get("smc_sweeps_today") or []
    if bull:  parts.append(f"OB支¥{bull:.0f}")
    if bear:  parts.append(f"OB压¥{bear:.0f}")
    if fvg_b: parts.append(f"FVG支¥{fvg_b:.0f}")
    if fvg_r: parts.append(f"FVG压¥{fvg_r:.0f}")
    for s in sweeps:
        icon = "🟢" if "buy" in s.get("type","") else "🔴"
        parts.append(f"{icon}扫¥{s.get('swept_level',0):.0f}")
    # 2026-08-01: 硬 assert — markdown 表格里 | 是 cell 分隔符
    for p in parts:
        assert "|" not in p, f"_smc_str parts 禁 '|', got: {p!r} in {parts!r}"
    return " ".join(parts) if parts else "—"


def _ob_str(row: dict, prefix: str = "") -> str:
    """OB 日/周: 支¥X/支¥X"""
    def fmt(bull, bear):
        parts = []
        if bull: parts.append(f"支¥{bull:.0f}")
        if bear: parts.append(f"压¥{bear:.0f}")
        return " ".join(parts) if parts else "—"
    d = fmt(row.get("smc_bull_ob"),   row.get("smc_bear_ob"))
    w = fmt(row.get("smc_w_bull_ob"), row.get("smc_w_bear_ob"))
    return f"{d} / {w}"


def _fvg_str(row: dict) -> str:
    """FVG 日/周: 支¥X/支¥X"""
    def fmt(bull, bear):
        parts = []
        if bull: parts.append(f"支¥{bull:.0f}")
        if bear: parts.append(f"压¥{bear:.0f}")
        return " ".join(parts) if parts else "—"
    d = fmt(row.get("smc_fvg_bull"),  row.get("smc_fvg_bear"))
    w = fmt(row.get("smc_w_fvg_bull"), row.get("smc_w_fvg_bear"))
    return f"{d} / {w}"


def _sweep_str(row: dict) -> str:
    """Sweep 日/周: 🟢扫¥X/🔴扫¥X"""
    def fmt(sweeps):
        if not sweeps: return "—"
        parts = []
        for s in sweeps:
            icon = "🟢" if "buy" in s.get("type","") else "🔴"
            parts.append(f"{icon}扫¥{s.get('swept_level',0):.0f}")
        return " ".join(parts)
    d = fmt(row.get("smc_sweeps_today") or [])
    w = fmt(row.get("smc_w_sweeps_today") or [])
    if d == "—" and w == "—": return "—"
    return f"{d} / {w}"


def _ma_str(row: dict) -> str:
    """MA 두 주기 편차: "+4.0% / -8.8%"
    2026-07-31 提到模块级
    """
    d = row.get('ma_dev_daily')
    w = row.get('ma_dev_weekly')
    d_s = f"{d:+.1f}%" if d is not None else "—"
    w_s = f"{w:+.1f}%" if w is not None else "—"
    return f"{d_s} / {w_s}"


def _pos_str(row: dict) -> str:
    """价格位置: d0.78/p0.92/u2.1/U3.5
    d=close_pos_day (日内位置), p=close_pos_20 (20日位置),
    u=upper_shadow_pct (今日上影%), U=upper_shadow_5d_avg (5日上影均值%)
    """
    d = row.get('close_pos_day')
    p = row.get('close_pos_20')
    u = row.get('upper_shadow_pct')
    U = row.get('upper_shadow_5d_avg')
    if all(v is None for v in (d, p, u, U)):
        return "—"
    d_s = f"{d:.2f}" if d is not None else "—"
    p_s = f"{p:.2f}" if p is not None else "—"
    u_s = f"{u:.1f}" if u is not None else "—"
    U_s = f"{U:.1f}" if U is not None else "—"
    return f"d{d_s}/p{p_s}/u{u_s}/U{U_s}"


def _has_signal(row: dict) -> bool:
    """行是否有信号 (用于 _section_factor_history 过滤 '无变化也无信号' 的行)
    2026-07-31 提到模块级
    """
    se_d  = row.get("sub_event_daily", "—")
    return (
        bool(row.get("bsp_daily")) or bool(row.get("bsp_weekly")) or
        se_d != "—" or
        bool(row.get("smc_sweeps_today"))
    )


# 因子历史走势 — 14 列 header / sep, 单一真源, t-analyze-all 等 batch 入口直接复用
# 2026-09-02 加 2 列: ROC% + EY% (ValuationStrategy 时序, 给回测用)
FACTOR_HISTORY_HEADER = "| 日期 | 收盘 | MA偏离(日/周) | MA20斜率 | 威科夫(日/周) | 子事件(日/周) | 日中枢 | 周中枢 | 买卖点 | 变化 | A天(日/周) | OBV | 布林% | BBW | ROC% | EY% |"
FACTOR_HISTORY_SEP    = "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"


def _format_factor_row(rows: list[dict], idx: int) -> str | None:
    """单行因子历史 markdown 表格行 (14 列, header 跟 _section_factor_history 完全一致)

    Args:
        rows: compute_factor_history 输出, 按日期升序 (含 ma20_slope 字段)
        idx:  当前行索引

    Returns:
        "| date | ¥close | ... | 14 列" markdown 行 (无错位防护, 调用方负责)
        数据不足返 None
    """
    from tools.analysis.analysis_result_signals import diff_rows, format_signals_for_render
    if not rows or idx < 0 or idx >= len(rows):
        return None
    row = rows[idx]
    changes = diff_rows(rows[idx - 1], row) if idx > 0 else {}

    wy  = f"{row['wyckoff_daily'][0]}/{row['wyckoff_weekly'][0]}"
    def se_short(s):
        if not s or s == "—":
            return "—"
        return s.split(" (")[0]
    se = f"{se_short(row.get('sub_event_daily'))}/{se_short(row.get('sub_event_weekly'))}"

    # 买卖点列只显示当天首次出现的信号 (new_bsp_daily/weekly = diff 新出现)
    bsp_parts = []
    for k, lbl in (("new_bsp_daily", "日"), ("new_bsp_weekly", "周")):
        new_pts = changes.get(k, {})
        for l in _bsp_str(new_pts).split():
            if l and l != "—":
                bsp_parts.append(f"{l}({lbl})")
    b3 = " ".join(bsp_parts) if bsp_parts else "—"

    chg = format_signals_for_render(changes)
    chg = [c for c in chg if not c.startswith('📊MA')]
    chg_str = ' '.join(chg) or '—'

    ad = row.get('accum_days_daily', 0)
    aw = row.get('accum_days_weekly', 0)
    accum_str = f"{ad}/{aw}" if any([ad, aw]) else "—"

    # MA 分两列: MA偏离(日/周) + MA20斜率
    ma_d = row.get('ma_dev_daily')
    ma_w = row.get('ma_dev_weekly')
    ma_parts = []
    for v in (ma_d, ma_w):
        if v is not None:
            ma_parts.append(f"{v:+.1f}%")
    ma_s = " / ".join(ma_parts) if ma_parts else "—"

    # MA20 斜率 (从 row['ma20_slope'] 读, 由 compute_factor_history 预算好, 0 重算)
    slope = row.get('ma20_slope')
    if slope is not None:
        if slope > 0.05:    arrow = "↗"
        elif slope < -0.05: arrow = "↘"
        else:               arrow = "→"
        slope_s = f"{arrow}{slope:+.2f}%/日"
    else:
        slope_s = "—"

    line = (
        f"| {row['date']} | ¥{row['close']:.1f} "
        f"| {ma_s} | {slope_s} "
        f"| {wy} | {se} "
        f"| {_hub_str(row['hub_daily'])} | {_hub_str(row['hub_weekly'])} "
        f"| {b3} | {chg_str} | {accum_str} "
        f"| {obv_label(row)} |"
    )
    bpct  = row.get('boll_pct')
    bwid  = row.get('boll_width')
    bpct_s = f"{bpct:.0f}%"  if bpct  is not None else "—"
    bwid_s = f"{bwid:.1f}%"  if bwid  is not None else "—"

    # 2026-09-02 加 2 列: ROC% + EY% (ValuationStrategy 时序)
    roc  = row.get('roc_daily')
    ey   = row.get('ey_daily')
    roc_s = f"{roc:.0f}%" if roc is not None else "—"
    ey_s  = f"{ey:.1f}%" if ey is not None else "—"

    return line.rstrip(" |") + f" | {bpct_s} | {bwid_s} | {roc_s} | {ey_s} |"


def _section_factor_history(data: RenderData, lookback: int = 120) -> str:
    """因子历史走势 — 每天一行，全部显示

    Args:
        lookback: 回看交易日数 (默认 120 ≈ 6 个月, t-history skill 用 1250=5年)
    """
    if not data.ctx:
        return "> ⚠️ ctx 未设置，无法计算历史\n"
    # 严格依赖 data.factor_history_rows (调用方必须传)
    # 不再 fallback 调 compute_factor_history, 避免重复计算 + 防止调用方忘传
    from tools.analysis.analysis_result_signals import diff_rows
    rows = data.factor_history_rows or []
    if not rows:
        return "> ⚠️ factor_history_rows 未传, 请调用方传 list[dict]\n"

    if not rows:
        return "> 数据不足\n"

    HEADER = FACTOR_HISTORY_HEADER  # 模块级常量, t-analyze-all 等 batch 入口复用
    SEP    = FACTOR_HISTORY_SEP
    HEADER_N = HEADER.count("|") - 1
    lines = [HEADER, SEP]

    for i, row in enumerate(rows):
        line = _format_factor_row(rows, i)
        if line is None:
            continue
        n_cells = line.count("|") - 1
        if n_cells != HEADER_N:
            # 自动把多出的 '|' 替换成 '/' (只对 cell 内部)
            # 先按表头列数 split, 多余的合并到最后一列用 ' / ' join
            parts = line.strip("|").split("|")
            if len(parts) > HEADER_N:
                head = parts[:HEADER_N-1]
                tail = " | ".join(parts[HEADER_N-1:])
                line = "| " + " | ".join(head) + " | " + tail + " |"
                n_cells = line.count("|") - 1
            if n_cells != HEADER_N:
                # 实在救不回来, 跳过这行
                print(f"  ⚠️ 因子历史 跳过错位行 date={row['date']} cells={n_cells}/{HEADER_N}", file=sys.stderr)
                continue
        # 任何 cell 内部含未转义 ' | ' (即 ' |' 或 '| ') 也会被 markdown 误解析
        # 但空格 join 是允许的, 所以这个 assert 不强制
        lines.append(line)

    if len(lines) <= 2:
        lines.append("| " + " | ".join(["—"] * HEADER_N) + " |")

    # 逆序：表头保留，数据行反转（最新日期在最上面）
    data_lines = lines[2:]
    data_lines.reverse()
    lines = lines[:2] + data_lines

    return "\n".join(lines) + "\n"

def _section_chan_three_elements(data: RenderData) -> str:
    """🧠 缠论三要素 (中枢+背驰+止跌)"""
    if not data.chan_data:
        return "> **数据状态:** ⚠️ 缠论数据未生成\n"
    chan = data.chan_data
    daily = chan.get("daily", {})
    hub = daily.get("hub", {})
    hub_low = hub.get("low", 0)
    hub_high = hub.get("high", 0)
    raw_pos = hub.get("pos", "—")
    current_price = data.current_price or 0
    if hub_low > 0 and hub_high > hub_low and current_price > 0:
        if current_price > hub_high:
            calc_pos, dist_pct = "上方✅", (current_price / hub_high - 1) * 100
            extra = f"突破 +{dist_pct:.1f}%"
            action = "⚠️ 高位, 关注是否回落中枢 (追高风险大)"
        elif current_price >= hub_low:
            calc_pos, dist_pct = "内部⬜", (current_price / ((hub_low + hub_high) / 2) - 1) * 100
            extra = f"中枢内 (距中枢中心 {dist_pct:+.1f}%)"
            action = "🟡 中枢震荡, 关注突破或跌破方向"
        elif current_price >= hub_low * 0.95:
            calc_pos, dist_pct = "下方⚠️", (current_price - hub_low) / hub_low * 100
            extra = f"破位下沿 {dist_pct:.1f}%"
            action = "🟢 关注止跌信号 (缩量+长下影), 触发后底仓建"
        else:
            calc_pos, dist_pct = "跌穿🔴", (current_price - hub_low) / hub_low * 100
            extra = f"**严重跌穿 -5%+ (实际 {dist_pct:.1f}%)** ⚠️"
            action = "🔴 严重跌穿, 等止跌 (缩量+底背驰) 才考虑建仓, 严禁猜底"
    else:
        calc_pos = raw_pos if raw_pos and raw_pos != "—" else "未形成中枢"
        extra = "周线段数不足，中枢尚未形成" if not hub_low else "(无中枢数据)"
        action = "⏳ 等待中枢形成后判断方向"
    return f"""**当前价格:** ¥{current_price:.2f}
**中枢区间:** {'¥{:.2f}~¥{:.2f}'.format(hub_low, hub_high) if hub_low else '未形成中枢'} (位置: {calc_pos} — {extra})

| 要素 | 状态 | 说明 |
|---|---|---|
| **中枢位置** | {calc_pos} | {extra} |

**操作建议:** {action}
"""


def _section_chan_signals(data: RenderData) -> str:
    """📊 缠论信号汇总 (中枢+买卖点)"""
    if not data.chan_data:
        return "> **数据状态:** ⚠️ 缠论信号未生成\n"
    chan = data.chan_data
    buy_points, sell_points = [], []
    for level in ["weekly", "daily"]:
        segs = (chan.get(level) or {}).get("segs", []) or []
        if segs:
            last = segs[-1]
            if last.get("dir") == "↓" and last.get("nb", 0) >= 3:
                buy_points.append(f"{level} 末段 ↓ nb={last['nb']}")
            elif last.get("dir") == "↑" and last.get("nb", 0) >= 5:
                sell_points.append(f"{level} 末段 ↑ nb={last['nb']}")

    def _hub_str(lv: str) -> str:
        h = (chan.get(lv) or {}).get("hub", {})
        return f"¥{h.get('low',0):.2f}~¥{h.get('high',0):.2f}" if h.get('valid') else '未形成'

    def _last_dir(lv: str) -> str:
        segs = (chan.get(lv) or {}).get("segs", []) or []
        return segs[-1].get("dir", "无段") if segs else "无段"

    def _signal_str(level: str, direction: str) -> str:
        found = any(level in p for p in buy_points)
        if found: return f"📉 关注买入"
        found = any(level in p for p in sell_points)
        if found: return f"📈 关注卖出"
        return "持续观察"

    return f"""**2 级别中枢/段汇总 (周/日):**

| 级别 | 中枢 | 段数 | 末段方向 | 潜在信号 |
|---|---|---|---|---|
| 周 | {_hub_str('weekly')} | {len((chan.get('weekly') or {}).get('segs', []) or [])} | {_last_dir('weekly')} | {_signal_str('weekly', _last_dir('weekly'))} |
| 日 | {_hub_str('daily')} | {len((chan.get('daily') or {}).get('segs', []) or [])} | {_last_dir('daily')} | {_signal_str('daily', _last_dir('daily'))} |

**买点:** {'无' if not buy_points else ', '.join(buy_points)} | **卖点:** {'无' if not sell_points else ', '.join(sell_points)}
"""


def _section_buy_sell_points(data: RenderData) -> str:
    """🟢 三买三卖操作点 (来自 buy_sell_points)"""
    bsp = data.buy_sell_points
    if not bsp:
        return "> **数据状态:** ⚠️ 买卖点数据未生成，需重新 sync_watchlist_fresh\n"

    lines = ["| 周期 | 0买(逆势) | 1买 | 2买 | 3买 | 1卖 | 2卖 | 3卖 | 操作 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for lv, label in [("weekly","周线"), ("daily","日线")]:
        lv_data = bsp.get(lv, {})
        if not lv_data:
            continue
        def _v(key):
            v = lv_data.get(key, "无")
            return v if v and v != "—" else "无"
        action = lv_data.get("action", "观察")
        lines.append(
            f"| {label} | {_v('🟢0买')} | {_v('🟢1买')} | {_v('🟢2买')} | {_v('🟢3买')} "
            f"| {_v('🔴1卖')} | {_v('🔴2卖')} | {_v('🔴3卖')} | {action} |"
        )
    return "\n".join(lines) + "\n"


def _section_market_context(data: RenderData) -> str:
    """🌍 大盘 + 美股背景 (2026-09-03 v6.1.1 改: 读本地 parquet, 不直连 tushare)

    之前: from tools.fetch.tushare_fetcher import _safe_call; _safe_call("index_daily", ...)
    现在: read_kline(ts_code, limit=2) 走本地 parquet
    """
    try:
        from tools.kline_store import read_kline
    except Exception:
        return "> **数据状态:** ⚠️ kline_store 不可用\n"
    indices = [("000001.SH", "上证指数"), ("000300.SH", "沪深300"),
               ("399006.SZ", "创业板指"), ("399001.SZ", "深证成指")]
    rows = ["| 指数 | 价格 | 涨跌幅 | 状态 |", "|---|---|---|---|"]
    for tc, iname in indices:
        try:
            bars = read_kline(tc, limit=2)
            if bars and len(bars) >= 1:
                cur = bars[0]
                prev = bars[1] if len(bars) > 1 else {}
                cur_close = float(cur.get("close", 0) or 0)
                prev_close = float(prev.get("close", 0) or cur.get("pre_close", 0) or 0)
                pct = ((cur_close / prev_close - 1) * 100) if prev_close else 0
                emoji = "🟢" if pct > 0 else "🔴" if pct < 0 else "⬜"
                rows.append(f"| {iname} | {cur_close:.2f} | {pct:+.2f}% | {emoji} |")
            else:
                rows.append(f"| {iname} | — | — | ❌ EMPTY (本地无指数) |")
        except Exception:
            rows.append(f"| {iname} | — | — | ❌ EXC |")
    rows += ["\n**美股:** | 指数 | 状态 |", "|---|---|",
             "| 纳斯达克 | ⚠️ 需 /t-sync-data (未来加 us index sync) |",
             "| 标普500 | ⚠️ 需 /t-sync-data (未来加 us index sync) |"]
    return "\n".join(rows) + "\n"


def _section_data_sources(data: RenderData) -> str:
    """📡 数据源矩阵 (2026-08-26: 内联 _load_config, 删 data_source 间接层)"""
    try:
        import yaml
        from pathlib import Path
        yaml_path = Path(__file__).parent.parent.parent / "data" / "sources.yaml"
        cfg = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) if yaml_path.exists() else {}
    except Exception:
        return "> **数据状态:** ⚠️ sources.yaml 不可用\n"
    lines = ["| 数据 | 主源 | 备源 | 单位 | 说明 |", "|---|---|---|---|---|"]
    for name, sec in (cfg or {}).items():
        if name == "banned":
            continue
        lines.append(f"| {name} | {sec.get('primary','—')} | {sec.get('fallback','—')} | {sec.get('unit','—')} | {sec.get('note','')[:50]} |")
    return "\n".join(lines) + "\n"


def _section_ts_basic(data: RenderData) -> str:
    """📊 基础信息 (Tushare) — 从 DataStore 读"""
    from tools.kline_store import DataStore
    sb = DataStore.get_stock_basic(data.code)
    name     = sb.get("name")     or data.name or "未知"
    industry = sb.get("industry") or data.industry if hasattr(data, "industry") else "未知"
    list_date= sb.get("list_date", "未知")
    total_sh = (sb.get("total_share") or 0) / 1e4   # 万股 → 亿股
    float_sh = (sb.get("float_share") or 0) / 1e4
    market   = sb.get("market", "A股")
    if not sb:
        return f"**代码:** {data.code}  **名称:** {name}  **数据源:** parquet (tushare.stock_basic 未存入，需重新 sync_watchlist_fresh)\n"
    return (f"**代码:** {data.code}  **名称:** {name}  "
            f"**行业:** {industry}  **上市日期:** {list_date}  "
            f"**总股本:** {total_sh:.2f}亿股  **流通股本:** {float_sh:.2f}亿股  **市场:** {market}\n\n"
            "> **数据源:** Tushare.stock_basic (dump)\n")


def _section_t_events(data: RenderData) -> str:
    """🎯 T 框架事件"""
    import json as _json
    from pathlib import Path
    from datetime import datetime as _dt
    events_path = Path("data/events.json")
    events = []
    if events_path.exists():
        try:
            all_ev = _json.loads(events_path.read_text(encoding="utf-8"))
            events = [e for e in all_ev.get("events", []) if e.get("code") == data.code]
        except Exception:
            pass
    if not events:
        return f"> **数据状态:** ⚠️ {data.code} 无 T 框架事件，可在 data/events.json 手动添加\n"
    today = _dt.now()
    lines = ["| 事件 | 日期 | 性质 | 影响 | 距今 |", "|---|---|---|---|---|"]
    for e in events[:10]:
        try:
            ev_date = e.get("event_date") or e.get("date", "")
            delta = (_dt.strptime(ev_date, "%Y-%m-%d") - today).days if ev_date else 0
            delta_str = f"T{delta//30:+d}月" if abs(delta) > 30 else f"T{delta:+d}天"
        except Exception:
            delta_str = "未知"
            ev_date = e.get("event_date") or e.get("date", "未知")
        name = e.get("description") or e.get("name") or e.get("event_type", "未知")
        impact = e.get("impact", "中性")
        etype = e.get("event_type") or e.get("type", "未知")
        impact_emoji = "🟢" if impact == "正" else "🔴" if impact == "负" else "⬜"
        lines.append(f"| {name[:30]} | {ev_date} | {etype} | {impact_emoji}{impact} | {delta_str} |")
    return "\n".join(lines) + "\n\n> **数据源:** data/events.json\n"


# 2026-08-31: 删除 _section_ts_money_flow, fflow section 整体停用
# (OBV 噪声大, CLAUDE.md 板块适用性限制, 用户要求)



# ============================================================
# 主渲染器
# ============================================================

def render_report(data: RenderData, sector: str = "—") -> str:
    """
    渲染完整分析报告 (22 section 全部输出, 缺数据也保留)

    Args:
        data: RenderData 实例
        sector: 板块名 (可由调用方传入)

    Returns:
        完整 Markdown 字符串
    """
    today = datetime.now().strftime("%Y-%m-%d")
    price_str = f"¥{data.current_price:.2f}" if data.current_price else "¥—"
    name = data.name or "—"
    code = data.code

    # 完整性表
    comp = data.completeness_report()
    comp_table = "| 数据源 | 状态 | 详情 |\n|---|---|---|\n"
    for k, (emoji, detail) in comp.items():
        comp_table += f"| {k} | {emoji} | {detail} |\n"

    # 报告主体 (按 CLAUDE.md 铁律顺序: 1️⃣缠论 → 2️⃣补充 → 3️⃣板块 → 4️⃣大盘 → 5️⃣PEG → 6️⃣fflow → 7️⃣仓位)
    # 2026-08-31: 6️⃣fflow section 已停用 (OBV 噪声大, CLAUDE.md 板块适用性限制)
    report = f"""# {code} {name} | {today}

**板块:** {sector} | **股价:** {price_str} | {_section_four_q_short(data)}

---

## 📊 数据完整性 (开篇即知, 一目了然)
{comp_table}
**完整度:** {data.completeness_pct()}% ({sum(1 for e, _ in comp.values() if e == '✅')}/{len(comp)} 项)

---

## EPS + 财务数据
{_section_eps(data)}

---

## MA 均线
{_section_ma(data)}

---

## 📊 技术指标 (8 种) ⭐

> Wilder 标准公式 (MACD/RSI/KDJ/BOLL/ATR/量比)

{_section_technical(data)}

---

## 📈 因子历史走势
{_section_factor_history(data)}

---

## 🎯 5 方法 × 3 周期 综合矩阵 (2026-07-25 合并: 整合原 5 合 1 顶部预警)
{_section_factor_matrix(data)}

---

## 🔍 5 方法详情 — 周期独立展开 (2026-07-29 简版: 矩阵 + 详情并存, 矩阵简版/详情展开)

> 📌 **结构说明**: 上面矩阵是 1 眼总览 (5 方法 × 3 周期各 1 行概要), 下面是每个周期的详情 (段表 / 9 子事件 / OB 列表等矩阵没包含的独有信息)
> 不重复: 矩阵已包含的概要不在详情里再写

### 📋 周线分析 (5 方法 × 周线视角)
{_section_weekly(data)}

---

### 📋 日线分析 (5 方法 × 日线视角)
{_section_daily(data)}

---

## 🧪 GA 因子验证 (2026-07-27 加, 实验性)
{_section_ga_factor(data)}

---

## 📈 板块过热预警
{_section_sector_overheat(data)}

---

## 🌍 大盘 + 美股背景
{_section_market_context(data)}

---

## 💰 PEG 实算
{_section_peg(data)}

---

## 📊 DCF L 实算
{_section_dcf(data)}

---

## 💎 基本面 (4 维) — 自动评估
{_section_fundamental(data)}

---

## 🎯 4 套交易策略 — 自动评估
{_section_strategy(data)}

---

## 🎯 投资四问
{_section_four_questions(data)}

---

## ⏰ T 框架
{_section_t_frame(data)}

---

## 🚨 5 类 14 子信号
{_section_signal_5cat(data)}

---

## 🤖 XGBoost 校准
{_section_xgboost(data)}

---

## 🎯 止盈 3 层
{_section_take_profit(data)}

---

## 🛑 止损 4 档
{_section_stop_loss(data)}

---

## 🟢 退场信号检查
{_section_exit_signals(data)}

---

## 📋 3 层仓位策略
{_section_position_layer(data)}

---

## 📌 监控触发点
{_section_monitor(data)}

---

## 📡 数据源矩阵 (类型/主源/备源/状态) — 固化不丢
{_section_data_sources(data)}

---

## 📊 基础信息 (Tushare)
{_section_ts_basic(data)}

---

## 🎯 T 框架事件 (业绩自动 + 非业绩手维护, 2026-07-23 升级)
{_section_t_events(data)}

---

## 🔍 Linter 校验报告 (增强模式自动追加)
- **完整度:** {data.completeness_pct()}% ({sum(1 for e, _ in comp.values() if e == '✅')}/{len(comp)} 项)
- **缺失项:** {', '.join(k for k, (e, _) in comp.items() if e != '✅') or '无 ✅'}

> 本报告由 `tools/render/report_renderer.render_report()` 全量渲染生成
"""
    # 2026-07-25: 注入 section id 注释 (Linter 用 id 匹配, 标题改了不影响)
    from tools.render.report_schema import REPORT_SECTIONS
    id_map = {s["title"]: s["id"] for s in REPORT_SECTIONS}
    for title, sid in id_map.items():
        # 在 "## {title}" 前插入 <!-- id:{sid} -->
        report = report.replace(f"## {title}", f"<!-- id:{sid} -->\n## {title}")
    return report


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys
    import logging
    logging.basicConfig(level=logging.WARNING)

    test_code = sys.argv[1] if len(sys.argv) > 1 else "002371"

    from tools.kline_store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.render_data import RenderData
    ctx    = DataStore.get_ctx(test_code)
    result = AnalysisEngine().analyze_history(ctx, [ctx.kline[-1]["trade_date"].replace("-","")[:8]]).get(ctx.kline[-1]["trade_date"].replace("-","")[:8]) if ctx.kline else None
    data   = RenderData.from_result(ctx, result)
    md = render_report(data)
    print(md[:2000])
    print(f"\n\n... 总长度 {len(md)} 字符 ...")
