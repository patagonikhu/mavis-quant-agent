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
  from tools.storage.store import DataStore
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
from tools.factors.wyckoff.helpers import _ma50_reached_ma200, MA50_MA200_TOLERANCE


# ============================================================
# Section 渲染器
# ============================================================

def _section_quarterly_table(data: RenderData) -> str:
    """4 季大表 (从 RenderData.quarterly_finance 读 14 列, 含 ROC/EY/季末市值/EV/投入资本)

    2026-09-11 拆出: 给外部摘要工具复用, 跟 /t-analyze 18 section 第 1 张表保持一致
    返回 markdown 字符串 (header + note + 表格 + 行业)
    """
    _val = data.valuation_data or {}
    has_magic = bool(_val and not _val.get("skip_reason"))

    if not data.quarterly_finance:
        return "> **最近 4 季实际财务:** ❌ 缺失"

    # period_label 复用 _section_eps 逻辑
    period_label = "—"
    industry = "—"
    if has_magic:
        period_label = _val.get("period_label") or "—"
        industry = _val.get("industry") or "—"
        if period_label in ("—", "no_data", None, "") and data.quarterly_finance:
            latest_q = data.quarterly_finance[-1].get("quarter", "")
            if latest_q:
                try:
                    y, m, _ = latest_q.split("-")
                    q_num = (int(m) - 1) // 3 + 1
                    period_label = f"{y}Q{q_num}"
                except Exception:
                    period_label = latest_q

    cols1 = ["季", "营收 yoy", "净利 yoy", "毛利率", "ROE", "营收 (亿)", "净利 (亿)",
             "EBIT (亿)", "ROC 资本回报率", "EY 收益率", "总市值 (亿)", "净负债 (亿)", "EV (亿)", "投入资本 (亿)",
             "PE"]
    sep1 = ["---"] * 15
    rows = [f"### 最近 4 季财务 + Magic 公式 ({period_label if has_magic else 'TTM'})", "",
            "**4 季财务 + Magic 估值 (营收/净利 yoy=单季同比; 营收/净利 (亿)=YTD 累计; Magic=TTM 单期; PE=季末市值/YTD净利年化 (Q1×4 / Q2×2 / Q3×4/3 / Q4×1)):**", "",
            "| " + " | ".join(cols1) + " |",
            "| " + " | ".join(sep1) + " |"]

    for r in data.quarterly_finance:
        def _fmt(v):
            return f"{v:,.0f}" if v else "—"
        roc_q = r.get('roc')
        ey_q = r.get('ey')
        roc_s = f"{roc_q:.1f}%" if roc_q is not None else "—"
        ey_s  = f"{ey_q:.2f}%" if ey_q is not None else "—"
        pe_q = r.get('static_pe')
        pe_s = f"{pe_q:.1f}" if pe_q is not None else "—"
        rows.append(
            f"| {r['quarter']} "
            f"| {r['or_yoy']:+.1f}% "
            f"| {r['np_yoy']:+.1f}% "
            f"| {r['gm']:.1f}% "
            f"| {r['roe']:.1f}% "
            f"| {r['revenue_yi']:.1f} "
            f"| {r['netprofit_yi']:.2f} "
            f"| {r.get('ebit_yi') or 0:.2f} "
            f"| {roc_s} "
            f"| {ey_s} "
            f"| {_fmt(r.get('mc_yi'))} "
            f"| {_fmt(r.get('netdebt_yi'))} "
            f"| {_fmt(r.get('ev_yi'))} "
            f"| {_fmt(r.get('capital_yi'))} "
            f"| {pe_s} |"
        )

    if has_magic:
        rows.extend(["", f"**行业:** {industry}"])
        rows.append(
            "\n**判定:** ROC 越高 = 资本效率越好, EY 越高 = EV 回报越好, "
            "联合排名 (Greenblatt 原版)"
        )

    return "\n".join(rows)


def _section_eps(data: RenderData) -> str:
    """财务数据 section (v6.2.7 改名 + 改中文 + 加最近 4 季财务 + Magic 整合)

    内容 (1 张大表):
    1. EPS 预测 (Tushare 一致预期) — 单独小表
    2. 最近 4 季 财务 + Magic (合并 1 表) — 复用 _section_quarterly_table()

    2026-09-11 改: 4 季大表拆到 _section_quarterly_table, 摘要也复用
    """
    parts = []

    # 1) EPS 预测 (Tushare 一致预期)
    if data.eps_table:
        rows = ["| 年份 | EPS (¥) | 净利润 (亿) | 营收 (亿) | ROE (%) |",
                "|---|---|---|---|---|"]
        for r in data.eps_table:
            rows.append(f"| {r.year} | {r.eps:.2f} | {r.net_profit_yi:.1f} | {r.revenue_yi:.1f} | {r.roe:.1f}% |")
        parts.append("### EPS 一致预期 (Tushare)\n\n" + "\n".join(rows))
    else:
        parts.append("> EPS 一致预期: ❌ 缺失")

    # 2) 4 季大表 (复用 helper)
    parts.append(_section_quarterly_table(data))

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
    return "> **数据状态:** ⚠️ T 框架未生成 (T 位置由 LLM 从外部源补: 年报/新闻/公告)\n"


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


def _section_signal_5cat(data: RenderData) -> str:
    if not data.signal_5cat or "error" in data.signal_5cat:
        return "> **数据状态:** ❌ 4 类 12 子信号未计算\n> **降级:** 用 K 线 + 技术指标自动算 (部分项用中性 5/10 分)\n"
    s = data.signal_5cat
    parts = [
        # 2026-09-09 删: 总规则分 (raw_score/123) + rating (🥇/🥈/🥉/⚠️)
        # 决策走 缠论 1买/2买/3买/1卖/2卖/3卖, 5 类信号只是参考子信号
        "| 类别 | 子信号 | Score | 触发 | 说明 |",
        "|---|---|---|---|---|",
    ]
    for row in s.get("signals", []):
        triggered = "✅" if row.get("triggered") else "❌"
        parts.append(
            f"| {row.get('category', '—')} | {row.get('name', '—')} "
            f"| {row.get('score', 0)}/10 | {triggered} "
            f"| {row.get('reason', '—')} |"
        )
    if s.get("missing"):
        parts.append(f"\n**数据缺失类:** {', '.join(s['missing'])}")
    return "\n".join(parts) + "\n"


def _section_xgboost(data: RenderData) -> str:
    if data.xgboost_prob is not None:
        return f"""- **启动概率:** {data.xgboost_prob:.2f}
- **状态:** {'🟢 高' if data.xgboost_prob > 0.7 else '🟡 中' if data.xgboost_prob > 0.4 else '🔴 低'}
"""
    return """> **数据状态:** ⚠️ XGBoost 校准未运行 (可选, 需训练数据)
> **降级:** 报告只显示规则分
"""


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
> **检查项:** 缠论 1卖/2卖/3卖 / MACD 高位死叉 / v11≤-3
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


def _wy_criteria_md(stage: str, accum_d: dict, markup_d: dict, dist_d: dict, scores: dict,
                    factor_values: dict = None, closes: list = None) -> str:
    """
    威科夫 阶段判定细节 - 展示为什么判这个阶段

    3 大阶段各自的 AND 门条件 + 实际是否满足
    2026-10-17 加: Fallback 路径 + factor 实际值表 + 计算公式速查
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
        elif markup_d.get('wta_markup_hit', False):
            pass_method = f"WTA Layer2.5 通过 (MA50≥MA200×{MA50_MA200_TOLERANCE} + 金叉 + 连续5日 + 角度≥2%)"
        elif current_score > 0:
            # 2026-11-03: 区分是 fallback1 还是 fallback2 触发的, 避免报告跟代码说的不是一回事
            _slope_60 = (factor_values or {}).get("slope_60", 0)
            _trend_30 = (factor_values or {}).get("trend_30", 0)
            _ma50 = (factor_values or {}).get("ma50", 0)
            _ma200 = (factor_values or {}).get("ma200", 0)
            _fb1 = (_slope_60 > 0 and (factor_values or {}).get("dev_ma200", 0) > -15
                    and _ma50_reached_ma200(_ma50, _ma200) and _trend_30 > -10)
            _fb2 = (_trend_30 > 5 and _slope_60 > -20)
            if _fb2 and not _fb1:
                pass_method = "弱信号 / 起点 fallback2 (sub-event + 斜率 + WTA Layer2.5 判据)"
            elif _fb1:
                pass_method = "弱信号 / 起点 fallback1 (4 项软条件)"
            else:
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
    fv = factor_values or {}

    # 2026-10-17 v4: 准备历史值代入, 让 Fallback 等"实际"列能展开完整计算过程
    # closes 是完整 K 线 close 列表, i = len(closes) - 1
    if closes and len(closes) > 60:
        i = len(closes) - 1
        c_i_60 = closes[i - 60] if i >= 60 else 0
        c_i_31 = closes[i - 31] if i >= 31 else 0
    else:
        c_i_60 = c_i_31 = 0
    # 找 ma50[i-5]: arrs 没暴露, 用 closes 反推 (i-5 的 ma50 ≈ 当前 ma50 + 5 日斜率反推不精确)
    # 简化: 直接用 factor_values 里的 ma50_angle_5d 反算
    ma50_i = fv.get("ma50", 0)
    ma50_angle_5d_v = fv.get("ma50_angle_5d", 0)
    if ma50_angle_5d_v and ma50_i:
        # (ma50[i] / ma50[i-5] - 1) * 100 = ma50_angle_5d → ma50[i-5] = ma50[i] / (1 + ma50_angle_5d/100)
        ma50_i_5 = ma50_i / (1 + ma50_angle_5d_v / 100)
    else:
        ma50_i_5 = 0

    # 当前阶段总结
    rows.append(f"**当前阶段: {stage}** (score={current_score}) — 通过方式: **{pass_method}**")
    rows.append("")

    # 2026-10-17 v3: 把"实际"列改成"公式 + 代入值 + 计算结果"一格展示
    # 用户不用翻另一张表就能看到计算过程

    # Accumulation 累积条件
    rows.append("**Accumulation 累积** (AND 门, 需全满足才严格通过):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 (公式 + 代入值 + 结果) | 满足 |")
    rows.append("|---|---|---|---|")
    # base_low
    p = fv.get("p", 0)
    period_low = fv.get("period_low", 0)
    period_low_thr = period_low * 1.45 if period_low > 0 else 0
    rows.append(f"| base_low (现价在年内低点附近) | 现价 ≤ 年内 low × 1.45 | `p={p:.2f} ≤ period_low × 1.45 = {period_low:.2f} × 1.45 = {period_low_thr:.2f}` | {_yn(accum_d.get('base_low_ok', False))} |")
    # MA 胶着
    ma50 = fv.get("ma50", 0)
    ma200 = fv.get("ma200", 0)
    ma_gap = abs(ma50 - ma200) / ma200 * 100 if ma200 > 0 else 0
    rows.append(f"| MA 胶着 (短期/长期均线接近) | \\|MA50-MA_long\\|/MA_long ≤ 8% | `\\|{ma50:.2f} - {ma200:.2f}\\| / {ma200:.2f} × 100 = {ma_gap:.2f}%` | {_yn(accum_d.get('ma_gap_ok', False))} |")
    # 量能萎缩 (近期 N 日均量 / 基线 M 日均量 < 0.75)
    # 2026-10-19: 详报里直接显示窗口天数, 不再 "近期" / "参考" 含糊
    vol_recent_avg   = fv.get("vol_recent_avg", 0)
    vol_baseline_avg = fv.get("vol_baseline_avg", 1)
    vol_recent_days  = fv.get("vol_recent_days", "?")
    vol_baseline_days = fv.get("vol_baseline_days", "?")
    vol_ratio_accum  = vol_recent_avg / vol_baseline_avg if vol_baseline_avg > 0 else 0
    rows.append(f"| 量能萎缩 (近 {vol_recent_days}d < 基线 {vol_baseline_days}d) | vol_recent_avg / vol_baseline_avg < 0.75 | `{vol_recent_avg:.0f} / {vol_baseline_avg:.0f} = {vol_ratio_accum:.3f}` | {_yn(accum_d.get('volume_dry_ok', False))} |")
    rows.append(f"| **子阶段** | 累积 A: 前置满足 / 累积 B: ≥3次测试底部 / 累积 C: 不破低 | `b_test_count={accum_d.get('b_test_count', 0)} (近 60 日 abs(low-period_low)/period_low ≤ 5% 的天数), c_stage_ok={accum_d.get('c_stage_ok', '—')}` | — |")
    rows.append(f"| 综合通过 | 上面 +1 全满足 | `pass_all={accum_d.get('pass_all', '—')}` | {_yn(accum_d.get('pass_all', False))} |")
    rows.append("")

    # Markup 主升浪条件 (核心修改: 实际列展开公式)
    rows.append("**Markup 主升浪** (AND 门):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 (公式 + 代入值 + 结果) | 满足 |")
    rows.append("|---|---|---|---|")
    # MA50/MA200 金叉 (近 5 日内, 2026-10-17 改: 之前实现是"当天", 名字误导)
    ma50_prev = fv.get("ma50_prev", 0)
    ma200_prev = fv.get("ma200_prev", 0)
    gc_result = ma50_prev <= ma200_prev and ma50 > ma200
    # 2026-10-17 v4: 展示"近 5 日"完整判定过程 (5 对 ma50/ma200 检查)
    if closes and len(closes) > 200:
        i_last = len(closes) - 1
        check_lines = []
        for j_off in range(0, 5):
            j = i_last - (4 - j_off)  # i-4, i-3, ..., i
            if j < 1 or j >= len(closes):
                continue
            ma50_j = arrs_get(arrs_dummy, "ma50", j) if False else None  # placeholder
        # 简化: 直接用 wyckoff_judge 内部的检查结果, 显示有金叉的索引
        # 实际: golden_cross_recent 现在是"近 5 日内", 我们用 markup_d['golden_cross_recent'] 显示最终结果
    # 用 markup_d 的最终结果, 避免重复计算 5 对
    rows.append(f"| MA50/MA200 金叉 | 近 5 日内任一天『ma50_prev ≤ ma200_prev AND ma50 > ma200』| `遍历 j ∈ [i-4..i] (5 对), 命中 = True; 当前对: ma50_prev={ma50_prev:.2f} ≤ ma200_prev={ma200_prev:.2f} AND ma50={ma50:.2f} > ma200={ma200:.2f} → {gc_result}` (注: 当前对可能 False, 但近 5 日内可能 True) | {_yn(markup_d.get('golden_cross_recent', False))} |")
    # 持续 MA200 上方
    rows.append(f"| 持续 MA200 上方 | 5 日都在 | `above_ma200_sustained={markup_d.get('above_ma200_sustained', '—')} (近 5 日 close > ma_long*0.98 全满足)` | {_yn(markup_d.get('above_ma200_sustained', False))} |")
    # MA gap > 0.5%
    ma_gap_pct = (ma50 - ma200) / ma200 * 100 if ma200 > 0 else 0
    rows.append(f"| MA gap > 0.5% | (MA50-MA200)/MA200 > 0.5% | `({ma50:.2f} - {ma200:.2f}) / {ma200:.2f} × 100 = {ma_gap_pct:.2f}%` | {_yn(ma_gap_pct > 0.5)} |")
    # MA50 角度 — 完整展开公式 (3 步: 公式 + 代入值 + 计算)
    ma50_angle_5d = fv.get("ma50_angle_5d", 0)
    rows.append(f"| MA50 角度 | ≥2%/5日 (markup_ma_angle_min) | `(ma50[i]/ma50[i-5]-1)*100 = ({ma50_i:.2f}/{ma50_i_5:.2f}-1)*100 = {(ma50_i/ma50_i_5-1)*100:+.2f}%` | {_yn(ma50_angle_5d >= 2.0)} |")
    rows.append("")

    # ── 2026-11-03 加: WTA Layer 2.5 判定 (参考实现 _detect_markup_entry 1:1) ──
    # 背景: fallback2 原来只要 sub-event + 斜率 + 趋势就判 Markup, 完全不看 MA200/金叉,
    #   实测 443 只候选池里 10 只命中有 9 只靠这条兜底, 且被误判的 9 只
    #   MA50/MA200 全在 56.4%~94.7% (无一站上 MA200).
    # 2026-11-03 起 fallback2 加严: 必须通过参考实现 Layer 2.5 的 4 条判据.
    _wta_avail = markup_d.get('wta_ma50_gt_ma200') is not None
    if _wta_avail:
        _wta_ma50 = markup_d.get('wta_ma50', 0) or 0
        _wta_ma200 = markup_d.get('wta_ma200', 0) or 0
        _wta_ratio = (_wta_ma50 / _wta_ma200 * 100) if _wta_ma200 > 0 else 0
        _wta_angle = markup_d.get('wta_angle_5d', 0) or 0
        _wta_above = markup_d.get('wta_above_days', 0)
        rows.append("**WTA Layer 2.5 判定** (对齐 WyckoffTradingAgent `_detect_markup_entry`, 4 条全满足才算主升浪):")
        rows.append("")
        rows.append("| 判据 | 阈值 | 实际 | 满足 |")
        rows.append("|---|---|---|---|")
        _wta_thr = _wta_ma200 * MA50_MA200_TOLERANCE
        rows.append(f"| ① MA50 够到 MA200 | MA50 ≥ MA200 × {MA50_MA200_TOLERANCE} | `MA50={_wta_ma50:.2f} vs 门槛 {_wta_thr:.2f} (MA200={_wta_ma200:.2f}, 比值 {_wta_ratio:.1f}%)` | {_yn(markup_d.get('wta_ma50_gt_ma200', False))} |")
        rows.append(f"| ② 近 10 日发生过金叉 | MA50 上穿 MA200×{MA50_MA200_TOLERANCE} | `近 10 日内扫描 ma50[j-1] < ma200[j-1]×{MA50_MA200_TOLERANCE} AND ma50[j] ≥ ma200[j]×{MA50_MA200_TOLERANCE}` | {_yn(markup_d.get('wta_cross_recent', False))} |")
        rows.append(f"| ③ 连续 5 日够到 MA200 | 近 5 日 MA50 全 ≥ MA200×{MA50_MA200_TOLERANCE} | `above_days={_wta_above} / 5` | {_yn(markup_d.get('wta_sustained', False))} |")
        rows.append(f"| ④ MA50 角度 | ≥ 2.0% / 5 日 | `(ma50[i]/ma50[i-5]-1)*100 = {_wta_angle:+.2f}%` | {_yn(markup_d.get('wta_angle_ok', False))} |")
        rows.append(f"| **WTA 综合** | 4 条全满足 | — | {_yn(markup_d.get('wta_markup_hit', False))} |")
        rows.append("")
        if not markup_d.get('wta_markup_hit', False):
            _miss = []
            if not markup_d.get('wta_ma50_gt_ma200', False):
                _miss.append(f"① MA50 未达 MA200×{MA50_MA200_TOLERANCE}")
            if not markup_d.get('wta_cross_recent', False):
                _miss.append("② 近 10 日无金叉")
            if not markup_d.get('wta_sustained', False):
                _miss.append(f"③ MA50 未连续 5 日达 MA200×{MA50_MA200_TOLERANCE}")
            if not markup_d.get('wta_angle_ok', False):
                _miss.append("④ MA50 角度不足 2%")
            rows.append(f"> ❌ **未通过 WTA Layer 2.5** — 缺: {'; '.join(_miss)}。"
                       f"即: 均线结构不支持『主升浪』判定。")
            rows.append("")

    # Fallback 路径
    rows.append("**Fallback 路径** (4 项 AND 门, 严格判定 0 分时启动):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 (公式 + 代入值 + 结果) | 满足 |")
    rows.append("|---|---|---|---|")
    slope_60 = fv.get("slope_60", 0)
    dev_ma200 = fv.get("dev_ma200", 0)
    trend_30 = fv.get("trend_30", 0)
    _ma50_ma200_ok = _ma50_reached_ma200(ma50, ma200)
    # 60 日斜率 — 3 步展开
    rows.append(f"| 60 日斜率向上 | slope_60 > 0 | `(p/closes[i-60]-1)*100 = ({p:.2f}/{c_i_60:.2f}-1)*100 = {(p/c_i_60-1)*100:+.2f}%` | {_yn(slope_60 > 0)} |")
    # MA200 偏离
    rows.append(f"| MA200 偏离不深 | dev_ma200 > -15% | `(p/ma200-1)*100 = ({p:.2f}/{ma200:.2f}-1)*100 = {dev_ma200:+.2f}%` | {_yn(dev_ma200 > -15)} |")
    # MA50 接近 MA200
    rows.append(f"| MA50 接近 MA200 | MA50 ≥ MA200 × 0.95 | `{ma50:.2f} ≥ {ma200:.2f} × 0.95 = {ma200*0.95:.2f}` | {_yn(_ma50_ma200_ok)} |")
    # 30 日不崩 — 3 步展开
    rows.append(f"| 30 日不崩 | trend_30 > -10% | `(p/closes[i-31]-1)*100 = ({p:.2f}/{c_i_31:.2f}-1)*100 = {(p/c_i_31-1)*100:+.2f}%` | {_yn(trend_30 > -10)} |")
    fb_pass = slope_60 > 0 and dev_ma200 > -15 and _ma50_ma200_ok and trend_30 > -10
    rows.append(f"| **Fallback 触发** | 4 项全满足 | — | {_yn(fb_pass)} |")
    rows.append("")
    rows.append("> 📖 **Fallback 设计哲学**: 严格金叉漏检『已走强但金叉未完成』的票(主力压住空头但 MA50 还没破 MA200), "
               "4 项软条件捕获『大方向向上 + 接近金叉 + 短期不崩』的反弹主升。")
    rows.append("")

    # ── 2026-11-03 加: fallback2 路径 (stage_factor.py:236) ──
    # 背景: stage 判定实际有 3 条 fallback 路径, 报告原来只渲染 fallback1 (上面那张表),
    #   导致西麦食品这类票显示「Markup AND 门全 ❌ + Fallback 也 ❌」但结论是 Markup。
    #   真正做决定的是 fallback2, 必须渲染出来, 否则报告跟代码说的不是一回事。
    # 2026-11-03 再改: fallback2 加严 —— 必须同时通过 WTA Layer 2.5 的 4 条判据。
    #   (改前只要 sub-event + 斜率 + 趋势就判 Markup, 实测 443 只候选池 10 只命中有
    #    9 只靠这条兜底, 且被误判的 9 只 MA50/MA200 全在 56.4%~94.7%, 无一站上 MA200)
    n_sub = fv.get("active_sub_event_count")
    if n_sub is None:
        n_sub = len(fv.get("active_sub_events") or [])
    _active = fv.get("active_sub_events") or []
    fb2_slope_ok = slope_60 > 5 or slope_60 < -5
    fb2_trend_up = trend_30 > 5 and slope_60 > -20
    fb2_trend_dn = trend_30 < -5
    fb2_entry = fb2_slope_ok and (fb2_trend_up or fb2_trend_dn)
    _wta_gate = markup_d.get('wta_markup_hit', False)
    rows.append("**Fallback 路径 2** (sub-event + 斜率, fallback1 不满足时启动):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    _sub_desc = f"{n_sub} 个 ({', '.join(_active[:6])}{'...' if len(_active) > 6 else ''})" if _active else f"{n_sub} 个"
    rows.append(f"| 有 sub-event | ≥ 1 个 | 当前 {_sub_desc} | {_yn(n_sub >= 1)} |")
    rows.append(f"| 60 日斜率明显 | slope_60 > 5 或 < -5 | `slope_60 = {slope_60:+.2f}%` | {_yn(fb2_slope_ok)} |")
    rows.append(f"| 30 日趋势向上 | trend_30 > 5 且 slope_60 > -20 | `trend_30 = {trend_30:+.2f}%` | {_yn(fb2_trend_up)} |")
    rows.append(f"| 30 日趋势向下 | trend_30 < -5 | `trend_30 = {trend_30:+.2f}%` | {_yn(fb2_trend_dn)} |")
    rows.append(f"| **WTA Layer 2.5 门槛** | 4 条判据全满足 (见上表) | `MA50>MA200 + 金叉 + 连续5日上方 + 角度≥2%` | {_yn(_wta_gate)} |")
    fb2_result = ("→ Markup" if fb2_trend_up else "→ Accumulation" if fb2_trend_dn else "→ ?")
    if fb2_trend_up:
        fb2_result = "→ Markup" if _wta_gate else "→ ? (WTA Layer2.5 未过)"
    rows.append(f"| **Fallback2 判定** | 入口 + 趋势方向 + WTA 门槛 | `{fb2_result}` | {_yn(fb2_entry and (not fb2_trend_up or _wta_gate))} |")
    rows.append("")
    if fb2_entry and fb2_trend_up and not _wta_gate:
        rows.append("> ⚠️ **本次阶段判定走的是 Fallback 2 入口** —— 入口条件满足, "
                   "但 **WTA Layer 2.5 的 4 条均线判据未全通过**, 因此**不判 Markup**。")
        rows.append("")
    elif fb2_entry and fb2_trend_up and _wta_gate:
        rows.append(f"> ✅ **本次阶段判定走的是 Fallback 2**, 且通过了 **WTA Layer 2.5** 的 4 条判据 "
                   f"(MA50>MA200 + 近10日金叉 + 连续5日在上方 + MA50角度≥2%)。")
        rows.append("")

    # Distribution 派发条件
    rows.append("**Distribution 派发** (AND 门强信号 / 弱信号 fallback):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 (公式 + 代入值 + 结果) | 满足 |")
    rows.append("|---|---|---|---|")
    # bias_200 偏离
    bias_200_val = fv.get("dev_ma200", 0)
    rows.append(f"| bias_200 偏离 | bias_200 > 30% (强) / > 15% (弱) | `(p/ma200-1)*100 = {bias_200_val:+.2f}%` | {_yn(dist_d.get('bias_200_ok', False))} |")
    # 3 日连续缩量
    vol_5 = fv.get("vol_5", 0)
    vol_60 = fv.get("vol_60", 0)
    vol_shrink = vol_5 < vol_60 * 0.5
    rows.append(f"| 3 日连续缩量 | 3d 均量 < 60d × 0.5 | `vol_5={vol_5:.0f} vs vol_60 × 0.5 = {vol_60*0.5:.0f} → {vol_shrink}` | {_yn(dist_d.get('vol_dry_3d', False))} |")
    # 派发起点
    dist_start = bias_200_val > 30 and vol_shrink
    rows.append(f"| 派发起点 | bias_200>30% AND vol_dry_3d | `{bias_200_val:+.2f}% > 30 AND {vol_shrink} → {dist_start}` | {_yn(dist_d.get('distribution_start', False))} |")
    return "\n".join(rows)


def _wy_sub_events_md(events: list, glossary: dict, current_stage: str,
                       c: list = None, h: list = None, l: list = None,
                       v: list = None, o: list = None, pct: list = None) -> str:
    """
    9 种 sub-event 触发情况 + 中文释义 + 所属阶段
    2026-10-17 加: 每个 sub-event 的关键 factor 实际值 + 公式 (用户可手算验证)
    2026-10-17 v2: 公式和值排在一行: A (2) > B (1) + C (0.5)
    """
    ALL_EVENTS = ["Spring", "LPS", "EVR",
                  "SOS", "Compression", "TrendPullback", "MarkupEntry",
                  "DistributionStart", "UTAD"]
    STAGE_KEYWORDS = {
        "Accumulation": ["Accumulation", "累积"],
        "Markup": ["Markup", "主升浪", "起点触发"],
        "Distribution": ["Distribution", "派发"],
    }
    current_keywords = STAGE_KEYWORDS.get(current_stage, [current_stage])
    rows = []
    triggered = set()
    last_date_map = {}
    for e in events:
        if isinstance(e, dict):
            name = e.get("name", "")
            triggered.add(name)
            d = e.get("date", "—")
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
        stage_mark = ""
        if is_triggered and any(kw in stage_aff for kw in current_keywords):
            stage_mark = " ⭐"
        last_d = last_date_map.get(code, "—") if is_triggered else "—"
        rows.append(f"| {i} | {code} | {name} | {meaning} | {stage_aff} | {mark}{stage_mark} | {last_d} |")
    rows.append("")
    rows.append(f"**当前触发: {len(triggered)} / 9 种** — " +
                ("⚠️ 触发数量多说明主力操作密集, 配合当前阶段判定解读" if len(triggered) >= 6
                 else "✅ 触发数量正常"))
    rows.append("")

    # 2026-10-17 加: 9 种 sub-event 关键 factor 实际值 + 公式 (公式和值拼一行)
    # 没传 c/h/l/v 就不渲染这张表
    if not (c and h and l and v):
        return "\n".join(rows)

    from tools.factors.kline_arrays import build_kline_features
    n = len(c)
    if n < 220:
        return "\n".join(rows)
    i_last = n - 1
    window = min(250, n)
    arrs = build_kline_features(c, h, l, v, window=window)
    i = i_last

    def _atr(period=20, lookback=200):
        """ATR 近似: mean(true_range[max(0, i-lookback-period+1) : i+1])"""
        trs = []
        for j in range(max(1, i - lookback), i + 1):
            tr = max(
                h[j] - l[j],
                abs(h[j] - c[j - 1]) if j > 0 else 0,
                abs(l[j] - c[j - 1]) if j > 0 else 0,
            )
            trs.append(tr)
        return sum(trs[-period:]) / max(len(trs[-period:]), 1)

    # 当前 bar 的实际值
    p = c[i]
    ma20_i = arrs["ma20"][i]
    ma50_i = arrs["ma50"][i]
    ma60_i = arrs["ma60"][i]
    ma200_i = arrs["ma_window"][i]
    ma50_prev = arrs["ma50"][i - 1] if i >= 1 else ma50_i
    ma200_prev = arrs["ma_window"][i - 1] if i >= 1 else ma200_i
    bias_200 = (p / ma200_i - 1) * 100 if ma200_i > 0 else 0
    rmin60 = arrs["rmin_l_pos"][i]
    rmax60 = arrs["rmax_h_pos"][i]
    rmin250 = arrs["rmin_l_250"][i]
    rmin20 = arrs["rmin_l_20"][i]
    pos_pct = ((p - rmin60) / (rmax60 - rmin60) * 100) if rmax60 > rmin60 else 50.0

    # Spring
    support_zone_l = l[max(0, i - 60 - 2):i - 1] if i >= 1 else []
    support_level = min(support_zone_l) if support_zone_l else 0
    zone_max_h = max(h[max(0, i - 60 - 2):i - 1]) if i >= 1 else 0
    zone_width = (zone_max_h - support_level) / support_level * 100 if support_level > 0 else 0
    vol_5_avg = sum(v[max(0, i - 4):i + 1]) / 5
    vol_ratio_spring = v[i] / vol_5_avg if vol_5_avg > 0 else 0
    vol_ratio_prev = v[i - 1] / v[i] if i >= 1 and v[i] > 0 else 0
    atr_20 = _atr(20, 200)

    # LPS
    ma20_i_5 = arrs["ma20"][i - 5] if i >= 5 else ma20_i
    max_v_5 = max(v[max(0, i - 4):i + 1]) if i >= 0 else 0
    max_v_60 = max(v[max(0, i - 59):i + 1]) if i >= 0 else 0

    # EVR
    vol_20_avg = sum(v[max(0, i - 19):i + 1]) / 20
    vol_ratio_evr = v[i] / vol_20_avg if vol_20_avg > 0 else 0
    pct_chg = pct[i] if pct else 0

    # SOS
    vol_60_avg = sum(v[max(0, i - 59):i + 1]) / 60
    vol_ratio_sos = v[i] / vol_60_avg if vol_60_avg > 0 else 0
    sorted_v60 = sorted(v[max(0, i - 60):i + 1])
    p95 = sorted_v60[int(len(sorted_v60) * 0.95)] if sorted_v60 else 0
    h_60max = max(h[max(0, i - 60):i])

    # Compression
    vol_5_25_avg = sum(v[max(0, i - 4):i + 1]) / 5
    vol_25_5_avg = sum(v[max(0, i - 24):i + 1]) / 25
    vol_decline = vol_5_25_avg / vol_25_5_avg if vol_25_5_avg > 0 else 0

    # TrendPullback
    h_10max = max(h[max(0, i - 10):i + 1]) if i >= 0 else p
    pullback = (h_10max - p) / h_10max * 100 if h_10max > 0 else 0

    # MarkupEntry
    ma50_i_5 = arrs["ma50"][i - 5] if i >= 5 else ma50_i
    ma50_angle_5d = (ma50_i / ma50_i_5 - 1) * 100 if ma50_i_5 > 0 else 0
    golden_cross = (ma50_prev <= ma200_prev and ma50_i > ma200_i)

    # DistributionStart
    vol_3_avg = sum(v[max(0, i - 2):i + 1]) / 3

    # UTAD
    upper_shadow = (h[i] - max(o[i] if o else p, p)) / max(h[i] - l[i], 0.01)
    vol_21_avg = sum(v[max(0, i - 20):i + 1]) / 21
    vol_utad = v[i] / vol_21_avg if vol_21_avg > 0 else 0

    rows.append("**📊 9 种 sub-event 关键 factor 实际值** (公式 + 值 拼一行, 用户可手算验证):")
    rows.append("")
    rows.append("| 事件 | 关键条件 | 公式 + 实际值 (满足=✅ / 不满足=❌) |")
    rows.append("|---|---|---|")
    rows.append(f"| **Spring** | 60 日区间宽度 | `max(h[i-62..i-1]) - min(l[i-62..i-1]) = {zone_max_h:.2f} - {support_level:.2f} = {zone_max_h-support_level:.2f}` (≤ 4×ATR={4*atr_20:.2f}: {'✅' if (zone_max_h-support_level) <= 4*atr_20 else '❌'}) |")
    rows.append(f"| | bias_200 | `(c[i-1] / ma200 - 1) * 100 = ({c[i]:.2f} / {ma200_i:.2f} - 1) * 100 = {bias_200:+.2f}%` (≤ 15%: {'✅' if bias_200 <= 15 else '❌'}) |")
    rows.append(f"| | 跌破+收回 | `l[i-1] = {l[i-1]:.2f} < support_level = {support_level:.2f}` (至少一日: {'✅' if (l[i-1] < support_level or l[i-2] < support_level) else '❌'}); `c[i-1] = {c[i-1]:.2f} > {support_level:.2f}` (收回: {'✅' if c[i-1] > support_level else '❌'}) |")
    rows.append(f"| | vol_ratio / vol_ratio_prev | `v[i] / mean(v[i-5..i+1]) = {v[i]:.0f} / {vol_5_avg:.0f} = {vol_ratio_spring:.2f}` (≥ 1.3: {'✅' if vol_ratio_spring >= 1.3 else '❌'}); `v[i-1] / v[i] = {v[i-1]:.0f} / {v[i]:.0f} = {vol_ratio_prev:.2f}` (≥ 1.15: {'✅' if vol_ratio_prev >= 1.15 else '❌'}) |")
    rows.append(f"| **LPS** | close ≥ MA20 | `c[i] = {p:.2f} >= ma20 = {ma20_i:.2f}` (✅/❌) |")
    rows.append(f"| | bias_200 区间 | `(p / ma200 - 1) * 100 = {bias_200:+.2f}%` ∈ [-20%, +25%] ({'✅' if -20 <= bias_200 <= 25 else '❌'}) |")
    rows.append(f"| | pos_pct ≤ 65 | `(p - rmin_l_pos) / (rmax_h_pos - rmin_l_pos) * 100 = ({p:.2f} - {rmin60:.2f}) / ({rmax60:.2f} - {rmin60:.2f}) * 100 = {pos_pct:.1f}%` (≤ 65%: {'✅' if pos_pct <= 65 else '❌'}) |")
    rows.append(f"| | MA20 上升 | `ma20[i] = {ma20_i:.2f} > ma20[i-5] = {ma20_i_5:.2f}` ({'✅' if ma20_i > ma20_i_5 else '❌'}) |")
    rows.append(f"| | 缩量 | `max(v[i-4..i+1]) = {max_v_5:.0f} < 0.5 * max(v[i-59..i+1]) = {0.5*max_v_60:.0f}` ({'✅' if max_v_5 < 0.5 * max_v_60 else '❌'}) |")
    rows.append(f"| **EVR** | bias_200 区间 | `{bias_200:+.2f}%` ∈ [10%, 25%] ({'✅' if 10 <= bias_200 <= 25 else '❌'}) |")
    rows.append(f"| | vol_ratio ≥ 1.8 | `v[i] / mean(v[i-19..i+1]) = {v[i]:.0f} / {vol_20_avg:.0f} = {vol_ratio_evr:.2f}` ({'✅' if vol_ratio_evr >= 1.8 else '❌'}) |")
    rows.append(f"| | 涨跌幅 | `pct_chg[i] = {pct_chg:+.2f}%` ∈ [-2%, +2%] ({'✅' if -2 <= pct_chg <= 2 else '❌'}) |")
    rows.append(f"| **SOS** | bias_200 ≤ 25 | `{bias_200:+.2f}%` ({'✅' if bias_200 <= 25 else '❌'}) |")
    rows.append(f"| | 单日涨幅 ≥ 6% | `pct_chg[i] = {pct_chg:+.2f}%` ({'✅' if pct_chg >= 6 else '❌'}) |")
    rows.append(f"| | vol_ratio ≥ 3 | `v[i] / mean(v[i-59..i+1]) = {v[i]:.0f} / {vol_60_avg:.0f} = {vol_ratio_sos:.2f}` ({'✅' if vol_ratio_sos >= 3 else '❌'}) |")
    rows.append(f"| | 突破 60 日高 | `h[i] = {h[i]:.2f} > max(h[i-60..i]) = {h_60max:.2f}` ({'✅' if h[i] > h_60max else '❌'}) |")
    rows.append(f"| **Compression** | MA 方向 | `ma_short = {ma50_i:.2f} >= ma_long = {ma60_i:.2f}` ({'✅' if ma50_i >= ma60_i else '❌'}) |")
    rows.append(f"| | bias_200 ≤ 25 | `{bias_200:+.2f}%` ({'✅' if bias_200 <= 25 else '❌'}) |")
    rows.append(f"| | 缩量 ≤ 0.6 | `mean(v[i-4..i+1]) / mean(v[i-24..i+1]) = {vol_5_25_avg:.0f} / {vol_25_5_avg:.0f} = {vol_decline:.3f}` ({'✅' if vol_decline <= 0.6 else '❌'}) |")
    rows.append(f"| **TrendPullback** | bias_200 ≤ 35 | `{bias_200:+.2f}%` ({'✅' if bias_200 <= 35 else '❌'}) |")
    rows.append(f"| | 回调幅度 | `(peak - c[i]) / peak = ({h_10max:.2f} - {p:.2f}) / {h_10max:.2f} = {pullback:.2f}%` ∈ [5%, 20%] ({'✅' if 5 <= pullback <= 20 else '❌'}) |")
    rows.append(f"| **MarkupEntry** | MA50 > MA200 | `ma50 = {ma50_i:.2f} > ma200 = {ma200_i:.2f}` ({'✅' if ma50_i > ma200_i else '❌'}) |")
    rows.append(f"| | 金叉 (5 日内) | `ma50_prev = {ma50_prev:.2f} <= ma200_prev = {ma200_prev:.2f} AND ma50 = {ma50_i:.2f} > ma200 = {ma200_i:.2f}` ({'✅' if golden_cross else '❌'}) |")
    rows.append(f"| | MA50 5 日角度 | `(ma50[i] / ma50[i-5] - 1) * 100 = ({ma50_i:.2f} / {ma50_i_5:.2f} - 1) * 100 = {ma50_angle_5d:+.2f}%` (≥ 2%: {'✅' if ma50_angle_5d >= 2 else '❌'}) |")
    rows.append(f"| **DistributionStart** | bias_200 > 30 | `{bias_200:+.2f}%` ({'✅' if bias_200 > 30 else '❌'}) |")
    rows.append(f"| | 3 日缩量 | `mean(v[i-2..i+1]) = {vol_3_avg:.0f} < 0.5 * mean(v[i-59..i+1]) = {0.5*vol_60_avg:.0f}` ({'✅' if vol_3_avg < 0.5 * vol_60_avg else '❌'}) |")
    rows.append(f"| **UTAD** | 突破 1% | `h[i] = {h[i]:.2f} > max(h[i-60..i]) * 1.01 = {h_60max*1.01:.2f}` ({'✅' if h[i] > h_60max * 1.01 else '❌'}) |")
    rows.append(f"| | 收回 0.3% | `c[i] = {p:.2f} < max(h[i-60..i]) * 0.997 = {h_60max*0.997:.2f}` ({'✅' if p < h_60max * 0.997 else '❌'}) |")
    rows.append(f"| | 上影线 ≥ 0.35 | `(h[i] - max(o, c)) / (h[i] - l[i]) = ({h[i]:.2f} - {max(o[i] if o else p, p):.2f}) / ({h[i]:.2f} - {l[i]:.2f}) = {upper_shadow:.3f}` ({'✅' if upper_shadow >= 0.35 else '❌'}) |")
    rows.append(f"| | 放量 ≥ 1.5 | `v[i] / mean(v[i-20..i+1]) = {v[i]:.0f} / {vol_21_avg:.0f} = {vol_utad:.2f}` ({'✅' if vol_utad >= 1.5 else '❌'}) |")
    rows.append("")
    rows.append("> 💡 **手算提示**: 每行把 9 种 sub-event 的关键条件展开成 `公式 + 实际值 + 满足`,你直接照着验。")
    return "\n".join(rows)


# ============================================================
# SMC section 辅助函数 (跟威科夫 4 段式风格一致, 2026-07-25)
# ============================================================

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


def _section_period(data: RenderData, level: str, label: str, weight: str) -> str:
    """
    单周期 section：缠论详细数据 + 其余方法在该周期视角的数据
    level: 'weekly' | 'daily' | '60min'
    label: '周线' | '日线' | '60分'
    weight: '1.0x' (2026-09-09 改: 3 周期等权, 不再区分 1.5x/1.0x/0.5x)

    2026-10-28: 删 vp_windows 参数 (量价/fflow 维度整体删除)
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
    # v5 (2026-07-28): 9 种 sub-event, 跟 WyckoffTradingAgent L4 对齐
    from tools.factors.wyckoff.detectors import SUB_EVENT_GLOSSARY
    # 2026-10-17: 传 c/h/l/v 让 sub_event 表能列 factor 实际值
    # KLineBar 是 dataclass, 用属性访问 (b.close) 而非 dict (b["close"])
    _bars = data.kline or []
    def _attr(b, name, default=0):
        v = getattr(b, name, default)
        return v if v is not None else default
    _c = [_attr(b, "close") for b in _bars] if _bars else []
    _h = [_attr(b, "high") for b in _bars] if _bars else []
    _l = [_attr(b, "low") for b in _bars] if _bars else []
    _v = [_attr(b, "volume") for b in _bars] if _bars else []
    _o = [_attr(b, "open", _attr(b, "close")) for b in _bars] if _bars else []
    _pct = [_attr(b, "pct_chg") for b in _bars] if _bars else []
    wy_criteria_md = _wy_criteria_md(wy_stage, accum_d, markup_d, dist_d, scores,
                                      wy_data.get("factor_values", {}),
                                      closes=_c)
    wy_sub_events_md = _wy_sub_events_md(sub_events, SUB_EVENT_GLOSSARY, wy_stage,
                                          c=_c, h=_h, l=_l, v=_v, o=_o, pct=_pct)
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

    # ── 4. 多市场共振 (已移除网络数据源，显示占位)
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

    return f"""> 周期 **{label}** (3 周期等权, 2026-09-09 删权重展示) | 中枢: {hub_str} ({pos_e}{hub_pos_str}, {hub_dist}) | 段数: {cd['seg_count']}

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

**【多市场共振】**

{res_focus}
"""


def _section_weekly(data: RenderData) -> str:
    """📋 周线分析 (因子)"""
    if not data.chan_data and not data.analysis:
        return "> **数据状态:** ⚠️ 缠论/5方法数据未生成\n"
    return _section_period(data, "weekly", "周线", "1.0x")


def _section_daily(data: RenderData) -> str:
    """📋 日线分析 (因子)"""
    if not data.chan_data and not data.analysis:
        return "> **数据状态:** ⚠️ 缠论/5方法数据未生成\n"
    return _section_period(data, "daily", "日线", "1.0x")


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
    """监控触发点

    2026-10-30 改: 之前 renderer 读 mt.get('add'/'reduce'/'clear') 这种扁平 key,
      但 compute_monitor_triggers 实际产出的是"触发项列表"结构:
        {触发项名: {"条件": ..., "操作": ..., "已触发": bool}, ...}
      → 字段名对不上, 一直显示"未设定".
      现在直接按触发项渲染 (加仓/减仓/清仓分类), 数据里有的就显示, 没有的不编.
    """
    if not data.monitor_triggers:
        return "> **数据状态:** ⚠️ 监控触发点未生成\n"

    mt = data.monitor_triggers
    lines = []

    # 老格式兼容 (万一以后改成扁平结构)
    if any(k in mt for k in ("add", "reduce", "clear", "加仓信号", "减仓信号", "清仓信号")):
        add    = mt.get('add')    or mt.get('加仓信号') or mt.get('加仓触发', '未设定')
        reduce = mt.get('reduce') or mt.get('减仓信号') or mt.get('减仓触发', '未设定')
        clear  = mt.get('clear')  or mt.get('清仓信号') or mt.get('清仓触发', '未设定')
        time_t = mt.get('time')   or mt.get('时间触发',  '见 T 框架事件')
        return f"""- **加仓触发:** {add}
- **减仓触发:** {reduce}
- **清仓触发:** {clear}
- **时间触发:** {time_t}
"""

    # 新格式: 按触发项分类渲染
    # 加仓类: 含 "加仓" / "建底仓" / "建仓"
    # 减仓类: 含 "减仓" / "清仓"
    buckets = {"加仓": [], "减仓": [], "清仓": [], "其他": []}
    for name, spec in mt.items():
        if not isinstance(spec, dict):
            continue
        op = str(spec.get("操作", ""))
        if "加仓" in op or "建仓" in op or "底仓" in op:
            buckets["加仓"].append((name, spec))
        elif "清仓" in op:
            buckets["清仓"].append((name, spec))
        elif "减仓" in op:
            buckets["减仓"].append((name, spec))
        else:
            buckets["其他"].append((name, spec))

    for label in ("加仓", "减仓", "清仓"):
        items = buckets[label]
        if not items:
            continue
        lines.append(f"**{label}触发**")
        for name, spec in items:
            fired = "✅ 已触发" if spec.get("已触发") else "⏳ 未触发"
            lines.append(f"- **{name}** — {fired}")
            lines.append(f"  - 条件: {spec.get('条件', '—')}")
            lines.append(f"  - 操作: {spec.get('操作', '—')}")
        lines.append("")

    if buckets["其他"]:
        lines.append("**其他触发**")
        for name, spec in buckets["其他"]:
            fired = "✅ 已触发" if spec.get("已触发") else "⏳ 未触发"
            lines.append(f"- **{name}** — {fired}")
            if spec.get("条件"):
                lines.append(f"  - 条件: {spec['条件']}")
            if spec.get("操作"):
                lines.append(f"  - 操作: {spec['操作']}")
        lines.append("")

    # 时间触发
    time_t = mt.get("时间触发")
    if time_t:
        lines.append(f"**时间触发:** {time_t}")
    else:
        lines.append("**时间触发:** 无")

    # 缠论综合判定
    overall = mt.get("缠论_综合判定")
    if overall and overall != "—":
        lines.append(f"**缠论综合判定:** {overall}")

    return "\n".join(lines).rstrip() + "\n"


def _section_four_q_short(data: RenderData) -> str:
    """报告头的简短四问 + 评级"""
    if data.four_questions:
        fq = data.four_questions
        # 2026-11-02 删: PEG 展示 (PEG 是假指标, 整档已删)
        return f"""**评级:** {fq.get('verdict', '待判定')} | **龙头:** {fq.get('leader_score', '?')}/14"""
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


# 因子历史走势 — 22 列 header / sep, 单一真源, t-analyze-all 等 batch 入口直接复用
# 2026-09-08 加 6 列: MACD/RSI/KDJ/ATR/量比/MACD状态 (TechnicalStrategy 时序, v6.2.8)
# 2026-09-08 删 2 列: ROC% / EY% (季报粒度, 跨季才变, 跟"## 财务数据" section 的 4 季财务 + Magic 表重复)
# 2026-10-08 删 2 列: 胜率%/中位成本 (cyq_perf, 跟 RSI/MA 同步, 没新信息, 删除)
FACTOR_HISTORY_HEADER = "| 日期 | 收盘 | RSI6 | MACD | DIF | DEA | BARΔ | 量比 | MA20斜率 | MA偏离(MA5/20/60) | 威科夫(日/周) | 子事件(日/周) | 日中枢 | 周中枢 | 买卖点 | 变化 | A天(日/周) | 布林% | BBW | KDJ(K/D/J) | KDJ金叉 | ATR% | MACD状态 | PE |"
# 2026-10-14 修: sep 从 header 自动算列数, 防止加列时 sep 跟 header 脱节
# (昨天 KDJ 加 2 列漏改 sep 25 cell vs 24 cell → GFM 表格不渲染)
FACTOR_HISTORY_SEP    = "|" + "---|" * (FACTOR_HISTORY_HEADER.count("|") - 1)


def _format_factor_row(rows: list[dict], idx: int) -> str | None:
    """单行因子历史 markdown 表格行 (22 列, header 跟 _section_factor_history 完全一致)

    Args:
        rows: compute_factor_history 输出, 按日期升序 (含 ma20_slope 字段)
        idx:  当前行索引

    Returns:
        "| date | ¥close | ... | 22 列" markdown 行 (无错位防护, 调用方负责)
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

    # MA 偏离 (MA5/20/60 三档日偏离) — v6.2.8 改: 标题"MA偏离(MA5/20/60)", cell 只显示 3 个分数
    ma5_dev  = row.get('ma5_dev')
    ma20_dev = row.get('ma_dev_daily')   # ma20 日偏离 (字段复用)
    ma60_dev = row.get('ma60_dev')
    ma_parts = []
    for v in (ma5_dev, ma20_dev, ma60_dev):
        if v is not None:
            ma_parts.append(f"{v:+.1f}%")
    ma_s = "/".join(ma_parts) if ma_parts else "—"

    # MA20 斜率 (从 row['ma20_slope'] 读, 由 compute_factor_history 预算好, 0 重算)
    slope = row.get('ma20_slope')
    if slope is not None:
        if slope > 0.05:    arrow = "↗"
        elif slope < -0.05: arrow = "↘"
        else:               arrow = "→"
        slope_s = f"{arrow}{slope:+.2f}%/日"
    else:
        slope_s = "—"

    # MACD 4 列变量 (line 拼接前必须先算, 2026-09-15 把 MACD 挪到第 3 列)
    macd_dif_v = row.get('macd_dif')
    macd_dea_v = row.get('macd_dea')
    # MACD 列: DIF-DEA 差值 (金叉死叉强度)
    if macd_dif_v is not None and macd_dea_v is not None:
        macd_diff = macd_dif_v - macd_dea_v
        _mfmt = ".4f" if abs(macd_diff) < 0.1 else ".2f"
        if macd_diff > 0:
            macd_s = f"🟢+{macd_diff:{_mfmt}}"
        elif macd_diff < 0:
            macd_s = f"🔴{macd_diff:{_mfmt}}"
        else:
            macd_s = "⚪0"
    else:
        macd_s = "—"
    # DIF / DEA
    if macd_dif_v is not None:
        dif_str = f"{macd_dif_v:+.3f}"
    else:
        dif_str = "—"
    if macd_dea_v is not None:
        dea_str = f"{macd_dea_v:+.3f}"
    else:
        dea_str = "—"
    # BARΔ = bars[-1] - bars[-2], 第一行为 "—"
    if macd_dif_v is not None and macd_dea_v is not None:
        cur_bar = (macd_dif_v - macd_dea_v) * 2
        if idx > 0 and rows[idx-1].get('macd_dif') is not None and rows[idx-1].get('macd_dea') is not None:
            prev_bar = (rows[idx-1]['macd_dif'] - rows[idx-1]['macd_dea']) * 2
            bar_delta = cur_bar - prev_bar
            if bar_delta > 0:
                bar_d_s = f"🟢+{bar_delta:.3f}"
            elif bar_delta < 0:
                bar_d_s = f"🔴{bar_delta:.3f}"
            else:
                bar_d_s = "⚪0"
        else:
            bar_d_s = "—"
    else:
        bar_d_s = "—"

    # RSI6 列 (2026-09-15 挪到 MACD 左边, line 拼接前必须先算)
    rsi6_v = row.get('rsi6')
    if rsi6_v is not None:
        if rsi6_v > 80:   rsi6_s = f"🔴{rsi6_v:.0f}"
        elif rsi6_v > 70: rsi6_s = f"🟠{rsi6_v:.0f}"
        elif rsi6_v < 20: rsi6_s = f"🟢{rsi6_v:.0f}"
        elif rsi6_v < 30: rsi6_s = f"🟡{rsi6_v:.0f}"
        else:             rsi6_s = f"{rsi6_v:.0f}"
    else:
        rsi6_s = "—"

    # 量比列 (2026-09-15 挪到 BARΔ 后, line 拼接前必须先算)
    vol_r_v = row.get('vol_ratio')
    if vol_r_v is not None:
        if vol_r_v > 2.0:   vol_s = f"🔴{vol_r_v:.1f}x"
        elif vol_r_v > 1.2: vol_s = f"🟡{vol_r_v:.1f}x"
        elif vol_r_v < 0.7: vol_s = f"🟢{vol_r_v:.1f}x"
        else:               vol_s = f"{vol_r_v:.1f}x"
    else:
        vol_s = "—"

    close_v = row['close']
    close_fmt = f"{close_v:.3f}" if close_v is not None and float(close_v) < 10 else f"{close_v:.1f}"
    line = (
        f"| {row['date']} | ¥{close_fmt} "
        f"| {rsi6_s} | {macd_s} | {dif_str} | {dea_str} | {bar_d_s} | {vol_s} "
        f"| {slope_s} | {ma_s} "
        f"| {wy} | {se} "
        f"| {_hub_str(row['hub_daily'])} | {_hub_str(row['hub_weekly'])} "
        f"| {b3} | {chg_str} | {accum_str} "
    )
    bpct  = row.get('boll_pct')
    bwid  = row.get('boll_width')
    bpct_s = f"{bpct:.0f}%"  if bpct  is not None else "—"
    bwid_s = f"{bwid:.1f}%"  if bwid  is not None else "—"

    # 2026-09-08 删 2 列: ROC% / EY% (季报粒度, 跟"## 财务数据" section 的 4 季表重复, 历史表里跨季才变没日价值)
    # 2026-09-15 注: MACD 4 列已挪到 line 拼接前, 这里只算 RSI6/KDJ/ATR/量比/MACD状态

    rsi6_v     = row.get('rsi6')
    kdj_k_v    = row.get('kdj_k')
    kdj_d_v    = row.get('kdj_d')
    kdj_j_v    = row.get('kdj_j')
    atr_pct_v  = row.get('atr_pct')
    vol_r_v    = row.get('vol_ratio')

    # RSI6 列: 4 档 (超卖/偏低/正常/超买)
    if rsi6_v is not None:
        if rsi6_v > 80:   rsi6_s = f"🔴{rsi6_v:.0f}"
        elif rsi6_v > 70: rsi6_s = f"🟠{rsi6_v:.0f}"
        elif rsi6_v < 20: rsi6_s = f"🟢{rsi6_v:.0f}"
        elif rsi6_v < 30: rsi6_s = f"🟡{rsi6_v:.0f}"
        else:             rsi6_s = f"{rsi6_v:.0f}"
    else:
        rsi6_s = "—"

    # KDJ(K/D/J) 列: 三值合 1 列, 用全角竖线 ｜ 分隔 (避免 markdown 表格 '|' 分列)
    if kdj_k_v is not None and kdj_d_v is not None and kdj_j_v is not None:
        kdj_s = f"{kdj_k_v:.0f}｜{kdj_d_v:.0f}｜{kdj_j_v:.0f}"
    else:
        kdj_s = "—"
    # KDJ 金叉/死叉 列: 4 档 (基于 K-D 关系 + K 位置)
    # 金叉 = K 上穿 D, 死叉 = K 下穿 D
    if kdj_k_v is not None and kdj_d_v is not None:
        if kdj_k_v > kdj_d_v and kdj_k_v < 30:
            kdj_cross = "🟢金叉低"
        elif kdj_k_v > kdj_d_v and kdj_k_v > 70:
            kdj_cross = "🟠金叉高"
        elif kdj_k_v < kdj_d_v and kdj_k_v > 70:
            kdj_cross = "🔴死叉高"
        elif kdj_k_v < kdj_d_v and kdj_k_v < 30:
            kdj_cross = "🟡死叉低"
        elif kdj_k_v > kdj_d_v:
            kdj_cross = "K>D"
        else:
            kdj_cross = "K<D"
    else:
        kdj_cross = "—"

    # ATR% 列: 波动率
    atr_s = f"{atr_pct_v:.1f}%" if atr_pct_v is not None else "—"

    # 2026-10-12 加: 每日 PE (走 batch_load_daily_basic)
    # stk_factor 数据晚 1 天, 但日度变化能直接看 (user 想看 PE 走势)
    # 2026-10-12 删: PE_TTM 和 PB (用户不要, 表头减列)
    pe_v       = row.get('pe')
    pe_s       = f"{pe_v:.1f}"      if pe_v     is not None else "—"

    # MACD 状态列: DIF/DEA 关系 + 正负区
    if macd_dif_v is not None and macd_dea_v is not None:
        if macd_dif_v > macd_dea_v and macd_dif_v > 0:
            macd_state = "金叉↑"
        elif macd_dif_v > macd_dea_v and macd_dif_v <= 0:
            macd_state = "弱金叉"
        elif macd_dif_v < macd_dea_v and macd_dif_v < 0:
            macd_state = "死叉↓"
        else:
            macd_state = "强死叉"
    else:
        macd_state = "—"

    return line.rstrip(" |") + f" | {bpct_s} | {bwid_s} | {kdj_s} | {kdj_cross} | {atr_s} | {macd_state} | {pe_s} |"


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

def _section_market_context(data: RenderData) -> str:
    """🌍 大盘 + 美股背景 (2026-09-03 v6.1.1 改: 读本地 parquet, 不直连 tushare)

    之前: from tools.storage.sources.tushare import _safe_call; _safe_call("index_daily", ...)
    现在: DataStore.get_kline(ts_code, limit=2) 走本地 parquet (2026-09-17 改: 统一接口)
    """
    try:
        from tools.storage.store import DataStore
    except Exception:
        return "> **数据状态:** ⚠️ kline_store 不可用\n"
    indices = [("000001.SH", "上证指数"), ("000300.SH", "沪深300"),
               ("399006.SZ", "创业板指"), ("399001.SZ", "深证成指")]
    rows = ["| 指数 | 价格 | 涨跌幅 | 状态 |", "|---|---|---|---|"]
    for tc, iname in indices:
        try:
            # tc 是 ts_code 格式, DataStore.get_kline 接受 ts_code 字符串
            # 但 sync 存的是 '000001.SH' 形式, 直接传 tc 即可
            code_6 = tc[:6] if '.' in tc else tc
            bars = DataStore.get_kline(code_6, limit=2)
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
        yaml_path = Path(__file__).parent.parent.parent / "config" / "sources.yaml"
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
    """📊 基础信息 (Tushare) — 从 DataStore 读

    v6.2.4 修: 表达式优先级 bug — `sb.get(...) or data.industry if hasattr(...) else "未知"`
    Python 优先级实际是 `(sb.get(...) or data.industry) if hasattr(...) else "未知"`,
    永远走 else 分支 → 行业/总股本/流通股本全显示"未知/0.00"

    现在拆成显式 if/else, 优先用 stock_basic 真实值
    """
    from tools.storage.store import DataStore
    sb = DataStore.get_stock_basic(data.code)
    # 行业: 优先 stock_basic, fallback data.industry, fallback "未知"
    industry = sb.get("industry") or "未知"
    name     = sb.get("name") or getattr(data, "name", None) or "未知"
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
    """🎯 T 框架事件 (T 位置由 LLM 从外部源补: 年报/新闻/公告)"""
    return (
        f"> **数据状态:** ⚠️ {data.code} T 框架事件待 LLM 补 (年报/新闻/公告)\n"
    )


# 2026-08-31: 删除 _section_ts_money_flow
# 2026-10-28: 主力资金流 (fflow) 维度整体删除, 详见 docs/fflow-removal-assessment.md
# (量价维度噪音大, 用户要求)


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

    # 完整性表 (横向紧凑: 列=数据源, 行=状态/详情)
    comp = data.completeness_report()
    keys = list(comp.keys())
    # 第一行: 表头 (数据源名)
    comp_table = "| 维度 | " + " | ".join(keys) + " |\n"
    # 第二行: 分隔符 (跟表头列数对齐)
    comp_table += "|---|" + "|".join(["---"] * len(keys)) + "|\n"
    # 第三行: 状态 (emoji)
    statuses = [comp[k][0] for k in keys]
    comp_table += "| 状态 | " + " | ".join(statuses) + " |\n"
    # 第四行: 详情
    details = [comp[k][1] for k in keys]
    comp_table += "| 详情 | " + " | ".join(details) + " |\n"

    # 报告主体 (按 CLAUDE.md 铁律顺序: 1️⃣缠论 → 2️⃣补充 → 3️⃣板块 → 4️⃣大盘 → 6️⃣仓位)
    # 2026-11-02: 5️⃣ PEG/DCF 整档已删
    # 2026-10-28: 主力 fflow 维度整体删除, 6️⃣ 位取消
    report = f"""# {code} {name} | {today}

**板块:** {sector} | **股价:** {price_str} | {_section_four_q_short(data)}

---

## 📊 数据完整性 (开篇即知, 一目了然)
{comp_table}
**完整度:** {data.completeness_pct()}% ({sum(1 for e, _ in comp.values() if e == '✅')}/{len(comp)} 项)

---

## 财务数据
{_section_eps(data)}

---

## MA 均线
{_section_ma(data)}

---

## 📈 因子历史走势
{_section_factor_history(data)}

---

## 🎯 因子 × 3 周期 综合矩阵 (2026-07-25 合并: 整合原 5 合 1 顶部预警, 2026-09-09 改)
{_section_factor_matrix(data)}

---

## 🔍 因子详情 — 周期独立展开 (2026-07-29 简版: 矩阵 + 详情并存, 矩阵简版/详情展开)

> 📌 **结构说明**: 上面矩阵是 1 眼总览 (因子 × 3 周期各 1 行概要), 下面是每个周期的详情 (段表 / 9 子事件 / OB 列表等矩阵没包含的独有信息)
> 不重复: 矩阵已包含的概要不在详情里再写

### 📋 周线分析 (因子 × 周线视角)
{_section_weekly(data)}

---

### 📋 日线分析 (因子 × 日线视角)
{_section_daily(data)}

---

## 🧪 GA 因子验证 (2026-07-27 加, 实验性)
{_section_ga_factor(data)}

---

## 🌍 大盘 + 美股背景
{_section_market_context(data)}

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

    from tools.storage.store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.render_data import RenderData
    ctx    = DataStore.get_ctx(test_code)
    result = AnalysisEngine().analyze_history(ctx, [ctx.kline[-1]["trade_date"].replace("-","")[:8]]).get(ctx.kline[-1]["trade_date"].replace("-","")[:8]) if ctx.kline else None
    data   = RenderData.from_result(ctx, result)
    md = render_report(data)
    print(md[:2000])
    print(f"\n\n... 总长度 {len(md)} 字符 ...")
