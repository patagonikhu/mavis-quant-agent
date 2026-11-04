"""
report_renderer.py — 分析报告渲染器 (v1.0, 2026-07-21)

架构铁律 (三层分离):
  Render 层 = 纯渲染, 零网络请求
  ✅ 只读 RenderData 对象字段
  ❌ 禁止 import requests / subprocess curl / fetch_all / 任何网络调用

设计目标:
  1. 7 个 section 标题全部固定, 缺数据也输出
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
from tools.factors.wyckoff.helpers import (
    _ma50_reached_ma200, MA50_MA200_TOLERANCE, FB1_TREND30_MIN, MARKUP_ANGLE_MIN,
    strength_label, strength_token,
)


# ============================================================
# Section 渲染器
# ============================================================

def _section_quarterly_table(data: RenderData) -> str:
    """4 季大表 (从 RenderData.quarterly_finance 读 14 列, 含 ROC/EY/季末市值/EV/投入资本)

    2026-09-11 拆出: 给外部摘要工具复用, 跟 /t-analyze 7 section 第 1 张表保持一致
    返回 markdown 字符串 (header + note + 表格 + 行业)
    """
    _val = data.valuation_data or {}
    has_magic = bool(_val and not _val.get("skip_reason"))

    if not data.quarterly_finance:
        return "> **最近 4 季实际财务:** ❌ 缺失"

    # period_label 复用 _section_finance_quarterly 逻辑
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


def _section_finance_quarterly(data: RenderData) -> str:
    """财务数据 section (最近 4 季财务 + Magic 整合)

    内容: 最近 4 季 财务 + Magic (1 张大表) — 复用 _section_quarterly_table()

    2026-09-11 改: 4 季大表拆到 _section_quarterly_table, 摘要也复用
    2026-11-04 删: EPS 预测小表 (EPS 机构一致预期整档已删)
    """
    return _section_quarterly_table(data)


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


def _section_xgboost(data: RenderData) -> str:
    if data.xgboost_prob is not None:
        return f"""- **启动概率:** {data.xgboost_prob:.2f}
- **状态:** {'🟢 高' if data.xgboost_prob > 0.7 else '🟡 中' if data.xgboost_prob > 0.4 else '🔴 低'}
"""
    return """> **数据状态:** ⚠️ XGBoost 校准未运行 (可选, 需训练数据)
> **降级:** 报告只显示规则分
"""


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
                    factor_values: dict = None, closes: list = None,
                    sub_events: list = None, stage_detail: str = "",
                    channel: str = "", strength: str = "",
                    period_unit: str = "日") -> str:
    """
    威科夫 阶段判定细节 - 展示为什么判这个阶段

    3 大阶段各自的 AND 门条件 + 实际是否满足
    2026-10-17 加: Fallback 路径 + factor 实际值表 + 计算公式速查
    2026-11-03 加: Accumulation 弱信号路径 (严格门没过但仍给分时显示)
    2026-11-03 加: channel 参数 —— **生产侧直接给出的实际命中通道**,
                    渲染器不再"显示侧反推"(之前反推跟实际分支对不上)。
    2026-11-05: AST 插桩实测 (门槛 -5): 生产 channel=fallback1 的 3 只,
                    标签与实际分支一一对应, 不存在错标。
    2026-11-05 加: strength 参数 —— **生产侧给出的判定强度档位**
                    (strict/weak/extra_only/fallback/none)。
                    解决「报告只写『判 Markup』, 不告诉读者走的是哪一层门」——
                    extra_only (仅加分线, 连 MA200 都不看) 必须显式暴露。
    2026-11-05 加: period_unit 参数 —— 窗口单位的说法要跟周期一致。
                    生产侧的 slope_60 / trend_30 是**按周期 bars** 算的
                    (周线段 = 60 周 / 30 周), 表里写死「日」会误导读者。
    """
    # 判断每个条件
    def _yn(ok): return "✅" if ok else "❌"

    # 窗口单位: 日线段 = 日, 周线段 = 周
    _u = period_unit

    # 2026-11-03: 通道标签表 (生产侧 channel → 中文)。
    #   channel 可能是多值 ("main_weak+main_extra"), 因为 Markup/Distribution 的
    #   加分线在代码里是**独立 if**, 可以和主判据同时成立 → 分数累加。
    #   所以解析时按 "+" 拆开, 逐段映射后用 " + " 连接。
    _CH_LABEL = {
        "main_strict": "主路径强信号 (金叉 + 持续 MA200 上方 + MA gap > 0.5% + 上升趋势)",
        "main_weak": "主路径弱信号 (价格在 MA200 上方 + MA gap > 0.5% + 上升趋势)",
        "main_extra": "主路径加分线 (MA20 偏离 > 5% + 价格位置 > 60% + 上升趋势)",
        "fallback1": f"fallback1 (4 项软条件: slope_60>0 + dev_ma200>-15% + MA50 够到 MA200 + trend_30>{FB1_TREND30_MIN:g})",
        "fallback2_markup": f"fallback2 → Markup (sub-event + 60{_u}斜率向上 + 30{_u}趋势向上)",
    }

    def _channel_label(ch: str) -> str:
        """channel → 中文标签; 多值按 '+' 拆开逐段映射。"""
        parts = [p for p in (ch or "").split("+") if p]
        labels = [_CH_LABEL[p] for p in parts if p in _CH_LABEL]
        if labels:
            return " + ".join(labels)
        return f"未识别通道 ({ch or '空'})"

    # 当前阶段的通过方式说明
    current_score = scores.get(stage, 0)
    pass_method = ""
    if stage == "Accumulation":
        if accum_d.get('pass_all', False):
            pass_method = "AND 门严格通过"
        elif current_score > 0:
            # 2026-11-03: 用生产侧 channel 判定来源, 不再自己反推
            if channel == "fallback2_accum":
                pass_method = f"fallback2 → Accumulation (60{_u}+30{_u}双向下)"
            elif channel.startswith("main_weak_"):
                _h = channel[len("main_weak_"):] or "?"
                pass_method = f"弱信号路径 — 命中分支 {_h} (A OR B OR C, 任一成立即 +1)"
            else:
                pass_method = "弱信号路径 (A OR B OR C, 任一成立即 +1)"
        else:
            pass_method = "未通过"
    elif stage == "Markup":
        if current_score > 0:
            # 2026-11-03: 通道标签改读**生产侧 channel**, 不再显示侧反推。
            #   旧逻辑重算 fb1/fb2/main_weak 条件, 跟生产实际分支对不上
            #   (旧口径: 标 fallback1 的 45 只里只有 6 只真走 fallback1)。
            #   现标签直接来自 stage_factor 的 channel 输出, 与实际分支一致。
            pass_method = _channel_label(channel)
        else:
            pass_method = "未通过"
    elif stage == "Distribution":
        if dist_d.get('distribution_start', False):
            pass_method = "AND 门严格通过 (派发起点)"
        elif current_score > 0:
            pass_method = _channel_label(channel) or "弱信号 (高位 + 派发 sub-event)"
        else:
            pass_method = "未通过"
    elif stage == "?":
        # 2026-11-03: '?' 阶段原本 pass_method 留空 → 渲染成 "****" (空加粗)。
        #   none = 三阶段都没给出得分; fb2_base_unresolved = fallback2 基础命中但方向不明。
        if channel == "fb2_base_unresolved":
            pass_method = f"fallback2 基础命中但阶段未解 (有 sub-event + |slope_60|>5, 但 30 {_u}方向不明确)"
        else:
            pass_method = "未判定 — 三阶段均未给出得分"

    rows = []
    fv = factor_values or {}
    # ⚠️ 2026-11-05 修 BUG (被周线口径改动暴露): 历史不足 200 根时,
    #   生产侧的 ma200_prev / ma200 等字段是 **None** (取不到均线)。
    #   渲染层直接 f"{None:.2f}" 或拿 None 做比较 → TypeError, **整份报告渲染失败**。
    #   统一把 None 规整成 0: 只影响展示, 不影响判定 (判定读生产 bool 与数值)。
    fv = {k: (0 if v is None else v) for k, v in fv.items()}

    # 2026-10-17 v4: 准备历史值代入, 让 Fallback 等"实际"列能展开完整计算过程
    # closes 是完整 K 线 close 列表, i = len(closes) - 1
    if closes and len(closes) > 60:
        i = len(closes) - 1
        c_i_60 = closes[i - 60] if i >= 60 else 0
        c_i_31 = closes[i - 31] if i >= 31 else 0
    else:
        c_i_60 = c_i_31 = 0
    # ma50[i-5]: 2026-11-03 改成**直接读生产值** fv['ma50_5d_ago']。
    #   原来是用 ma50_angle_5d 反算: ma50[i-5] = ma50[i] / (1 + angle/100) ——
    #   那是用「已被四舍五入到 2 位」的角度反解, 误差被放大, 且 angle=0 时直接置 0。
    #   规则下沉到代码: 生产侧 stage_factor 已直接输出 ma50_5d_ago。
    ma50_i = fv.get("ma50", 0)
    ma50_angle_5d_v = fv.get("ma50_angle_5d", 0)
    ma50_i_5 = fv.get("ma50_5d_ago") or 0

    # 当前阶段总结
    # 2026-11-05 加: 判定强度 (生产侧 strength, 渲染器只读不算)。
    #   文案按 stage 取 (strength_label) —— 同一个 channel token 在不同 stage
    #   语义不同 (main_strict 在 Accumulation = 累积 AND 门, 在 Markup = 主升 AND 门)。
    #   '?' 阶段不显示 —— 那时 strength 恒为 none, 显示出来是噪音。
    _strength_txt = strength_label(stage, strength)
    if _strength_txt:
        rows.append(f"**当前阶段: {stage}** (score={current_score}) — 通过方式: **{pass_method}** — "
                    f"判定强度: **{_strength_txt}**")
    else:
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
    # ⚠️ 2026-11-03: 三个 MA 基准必须分清, 报告里不能混用
    #   ma200      = window(250) 均线  → Accumulation MA 胶着 / fallback1 用的是它
    #   ma200_true = 真 200 日均线     → Markup 主路径判据用真 MA200
    #   ma200_gap  = Markup gap / above_ma200 的基准 (= 真 MA200, warm-up 退回 ma60)
    ma200_true = fv.get("ma200_true", 0)
    ma200_gap = fv.get("ma200_gap", ma200_true) or ma200_true
    ma_gap = abs(ma50 - ma200) / ma200 * 100 if ma200 > 0 else 0
    rows.append(f"| MA 胶着 (短期/长期均线接近) | \\|MA50-MA250\\|/MA250 ≤ 8% | `\\|{ma50:.2f} - {ma200:.2f}\\| / {ma200:.2f} × 100 = {ma_gap:.2f}%` | {_yn(accum_d.get('ma_gap_ok', False))} |")
    # 量能萎缩 (近期 N 日均量 / 基线 M 日均量 < 0.75)
    # 2026-10-19: 详报里直接显示窗口天数, 不再 "近期" / "参考" 含糊
    vol_recent_avg   = fv.get("vol_recent_avg", 0)
    vol_baseline_avg = fv.get("vol_baseline_avg", 1)
    vol_recent_days  = fv.get("vol_recent_days", "?")
    vol_baseline_days = fv.get("vol_baseline_days", "?")
    vol_ratio_accum  = vol_recent_avg / vol_baseline_avg if vol_baseline_avg > 0 else 0
    rows.append(f"| 量能萎缩 (近 {vol_recent_days}{_u} < 基线 {vol_baseline_days}{_u}) | vol_recent_avg / vol_baseline_avg < 0.75 | `{vol_recent_avg:.0f} / {vol_baseline_avg:.0f} = {vol_ratio_accum:.3f}` | {_yn(accum_d.get('volume_dry_ok', False))} |")
    rows.append(f"| **子阶段** | 累积 A: 前置满足 / 累积 B: ≥3次测试底部 / 累积 C: 不破低 | `b_test_count={accum_d.get('b_test_count', 0)} (近 60 {_u} abs(low-period_low)/period_low ≤ 5% 的{_u}数), c_stage_ok={accum_d.get('c_stage_ok', '—')}` | — |")
    rows.append(f"| 综合通过 | 上面 +1 全满足 | `pass_all={accum_d.get('pass_all', '—')}` | {_yn(accum_d.get('pass_all', False))} |")
    rows.append("")

    # 2026-11-03 加: Accumulation 是**评分制**, 严格 AND 门只是其中一条路径。
    #   严格门没过 (pass_all=False) 但仍判 Accumulation 时, 必须显示**实际给分的弱信号路径**,
    #   否则读者看到 AND 门 ❌❌❌ 却判 Accumulation, 会以为报告自相矛盾。
    #   Accumulation 分数的**两个来源** (互斥):
    #     (1) 主路径弱信号 (stage_factor.py L274-279, 与严格门互斥的 elif 链):
    #           A: abs(slope_60) < 8 且 25 < pos_pct < 75 且有 Spring/LPS/EVR   → +1
    #           B: pos_pct < 30 且 dev_ma200 < -5 且有 Spring/LPS/EVR           → +1
    #           C: pos_pct < 30 且 dev_ma200 < -5 且 len(sub_events) >= 1        → +1
    #         (三条是 OR: 任一满足即 +1; 但按 elif 顺序只命中第一条)
    #     (2) fallback2 → Accumulation 分支 (L394-398):
    #           有 sub_event 且 slope_60 < -5 且 trend_30 < -5 → stage='Accumulation', scores['Accumulation']=1
    #         (主路径三个分数全 0 时才轮得到; 实测 443 只池里 13 只走这条)
    if stage == "Accumulation" and not accum_d.get("pass_all", False) and current_score > 0:
        _sl60 = fv.get("slope_60", 0)
        _tr30 = fv.get("trend_30", 0)
        _pos = fv.get("pos_pct", 0)
        _dev200 = fv.get("dev_ma200", 0)
        _snames = set()
        for _e in (sub_events or []):
            if isinstance(_e, str):
                _snames.add(_e)
            elif isinstance(_e, dict):
                _nm = _e.get("name", "")
                if _nm:
                    _snames.add(_nm)
        _accum_ev = sorted(_snames & {"Spring", "LPS", "EVR"})
        # 2026-11-03: A/B/C 判定**读生产侧 accum_detail**, 渲染器不再自己复刻
        #   (复刻一旦跟生产漂移, 报告就会说谎 —— fallback 标签就是这么错的)。
        _wA = bool(accum_d.get("weak_A_ok", False))
        _wB = bool(accum_d.get("weak_B_ok", False))
        _wC = bool(accum_d.get("weak_C_ok", False))
        _hit = accum_d.get("weak_hit") or ""
        # fallback2 → Accumulation 分支: 同样以生产侧 channel 为准
        _fb2_accum = (channel == "fallback2_accum")

        if not (_wA or _wB or _wC) and _fb2_accum:
            # 分数来自 fallback2, 不是主路径弱信号
            rows.append(f"> ⚠️ **严格 AND 门未通过 (pass_all=False), 但仍判 Accumulation** —— "
                        f"这一分**不是**主路径弱信号给的, 而是 **fallback2 → Accumulation 分支** "
                        f"(主路径累积/派发/主升三个分数全为 0 时才轮得到):")
            rows.append("")
            rows.append("| fallback2 → Accumulation | 阈值 | 实际 | 满足 |")
            rows.append("|---|---|---|---|")
            rows.append(f"| 真跌势 (60{_u}+30{_u}双向下) | 有 sub_event 且 `slope_60 < -5` 且 `trend_30 < -5` | `slope_60={_sl60:+.2f}`, `trend_30={_tr30:+.2f}`, sub_event 数={len(_snames)} | ✅ |")
            rows.append("")
            rows.append(f"> **该分支语义**: 60 {_u}与 30 {_u}**双向下** → 判定为「真跌势中的底部累积区」, "
                        f"而不是主升浪。注意它**不看 MA200 位置 / 不看量能 / 不要求近期事件**。")
            rows.append("")
        else:
            _hit = _hit or "—"
            rows.append(f"> ⚠️ **严格 AND 门未通过 (pass_all=False), 但仍判 Accumulation** —— "
                        f"Accumulation 是**评分制不是 AND 门**, 实际给分的是下面的**弱信号路径** (score={current_score}):")
            rows.append("")
            rows.append("**弱信号路径 — 逻辑关系: `A OR B OR C`** (三条并列, 任一成立即 +1 分; "
                        "**可同时成立**, 不互斥)")
            rows.append("")
            rows.append("| 弱信号分支 | 阈值 | 实际 | 条件成立 | 实际计分 |")
            rows.append("|---|---|---|---|---|")
            rows.append(f"| **A. 横盘/低位 + 累积 sub-event** ⭐ | `abs(slope_60) < 8` 且 `25 < pos_pct < 75` 且有 Spring/LPS/EVR | `slope_60={_sl60:+.2f}` → `abs={abs(_sl60):.2f}` (需 <8), `pos_pct={_pos:.1f}` (需 25~75), 累积类事件={_accum_ev or '无'} | {_yn(_wA)} | {'✅ **+1 (命中分支)**' if _hit == 'A' else '⬜ 未走'} |")
            rows.append(f"| B. 深跌 + 累积 sub-event | `pos_pct < 30` 且 `dev_ma200 < -5` 且有 Spring/LPS/EVR | `pos_pct={_pos:.1f}` (需 <30), `dev_ma200={_dev200:+.2f}%` (需 <-5%), 累积类事件={_accum_ev or '无'} | {_yn(_wB)} | {'✅ **+1 (命中分支)**' if _hit == 'B' else '⬜ 未走'} |")
            rows.append(f"| C. 深跌 + 任意 sub-event | `pos_pct < 30` 且 `dev_ma200 < -5` 且 `len(sub_events) >= 1` | `pos_pct={_pos:.1f}` (需 <30), `dev_ma200={_dev200:+.2f}%` (需 <-5%), sub_event 数={len(_snames)} (需 >=1) | {_yn(_wC)} | {'✅ **+1 (命中分支)**' if _hit == 'C' else '⬜ 未走'} |")
            rows.append("")
            _also = [n for n, ok in (("A", _wA), ("B", _wB), ("C", _wC)) if ok and n != _hit]
            _extra = (f" (同时成立但被 elif 顺序挡在后面的: {', '.join(_also)})" if _also else "")
            rows.append(f"> **实际命中分支: {_hit}** —— 得分来自 {_hit} 条 (条件成立 → +1 分){_extra}。")
            rows.append(">")
            rows.append("> **⚠️ 「条件成立」≠「实际计分」**: 代码是 `if/elif` 链, A→B→C 顺序**只记第一条**成立的分支; "
                        "所以「条件成立」列可能有多条 ✅, 但只有命中分支拿到那 +1 分。")
            rows.append(">")
            rows.append(f"> 「子阶段」({stage_detail}) 由另一条链判定 (L407-416), 与 AND 门无关。")
            rows.append("")

    # Markup 主升浪条件 (核心修改: 实际列展开公式)
    rows.append("**Markup 主升浪** (AND 门):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 (公式 + 代入值 + 结果) | 满足 |")
    rows.append("|---|---|---|---|")
    # MA50/MA200 金叉
    # ⚠️ 2026-11-03 修 BUG: 原来这一格同格混用两个 MA200 源 ——
    #   ma200_prev 取自真 MA200 (i-1), 而 ma50 > ma200 比的是 250 日均线,
    #   读者看到 "ma200_prev=22.65 但 ma200=23.65" 完全对不上。
    #   现在两边统一用**真 MA200** (与 stage_factor 代码一致)。
    ma50_prev = fv.get("ma50_prev", 0)
    ma200_prev = fv.get("ma200_prev", 0)
    gc_result = ma50_prev <= ma200_prev and ma50 > ma200_true
    # ⚠️ 2026-11-03 修 BUG: 文案写 "近 5 日 ... (5 对)", 代码是
    #   `for j in range(max(1, i-9), i+1)` = 近 10 日 / 10 对。改成 10 日 / 10 对。
    #   (原来这下面还有一段 placeholder 死代码, 一并清掉)
    rows.append(f"| MA50/MA200 金叉 | 近 10 {_u}内任一天『ma50_prev ≤ ma200_prev AND ma50 > ma200』| `遍历 j ∈ [i-9..i] (10 对), 命中 = True; 当前对: ma50_prev={ma50_prev:.2f} ≤ ma200_prev={ma200_prev:.2f} AND ma50={ma50:.2f} > MA200={ma200_true:.2f} → {gc_result}` (注: 当前对可能 False, 但近 10 {_u}内可能 True) | {_yn(markup_d.get('golden_cross_recent', False))} |")
    # 持续 MA200 上方
    # ⚠️ 2026-11-03 修 BUG: 说明文字写 "近 5 日 close > ma_long*0.98 全满足",
    #   但 stage_factor.py L195-199 实际判的是
    #   `all(arrs['ma50'][j] > arrs['ma200'][j] for j in 最近5日)` —— **MA50 vs 真 MA200**,
    #   不是 close 也不是 ma_long(=MA60)。说明文字跟代码对不上。
    rows.append(f"| 持续 MA200 上方 | 近 5 {_u} MA50 均 > 真 MA200 | `above_ma200_sustained={markup_d.get('above_ma200_sustained', '—')} (MA50 > MA200, 连续 5 {_u})` | {_yn(markup_d.get('above_ma200_sustained', False))} |")
    # MA gap > 0.5%
    # ⚠️ 2026-11-03 修 BUG: 原来展示 (MA50-MA200)/MA200 并用它判满足与否,
    #   但 stage_factor.py L288 实际判定用的是 **ma_gap_pct_above = (p - ma200)/ma200**
    #   (价格在 MA200 上方的偏离), 两个是不同的量。
    #   实测 301109 乔锋智能: (MA50-MA200)/MA200 = -10.38% (不满足),
    #   但 (p-ma200)/ma200 = -1.51% 也不满足 → 两边一致; 而 300864 南大环境等票
    #   两值差异巨大, 报告结论会跟代码说的不是一回事。
    #   现在统一用 ma_gap_pct_above (代码实际判定值)。
    _ma_gap_above = fv.get("ma_gap_pct_above", 0)
    _p_now = fv.get("p", 0)
    # ⚠️ 2026-11-03 修 BUG: 基准改为**真 MA200** (ma200_gap),
    #   原来用 250 日均线 (ma200) —— 同一组判据里金叉/持续上方都用真 MA200。
    # 「满足」列由渲染侧按数值算 (>0.5) —— 生产侧 markup_detail 只输出数值
    #   ma_gap_pct_above, 没有对应 bool, 强行"读生产 bool"会渲染成「—」。
    rows.append(f"| MA gap > 0.5% | (价格-MA200)/MA200 > 0.5% | `({_p_now:.2f} - {ma200_gap:.2f}) / {ma200_gap:.2f} × 100 = {_ma_gap_above:.2f}%` | {_yn(_ma_gap_above > 0.5)} |")
    # MA50 角度 — 完整展开公式 (3 步: 公式 + 代入值 + 计算)
    # ⚠️ 2026-11-03 修 BUG: 分母 ma50[i-5] 现在直接读生产值 fv['ma50_5d_ago'],
    #   原来是"5 天前收盘均价" (sum(closes[i-54:i-49])/5) —— 公式和数值对不上。
    ma50_angle_5d = fv.get("ma50_angle_5d", 0)
    if ma50_i_5 > 0:
        _angle_now = (ma50_i / ma50_i_5 - 1) * 100
        _angle_cell = (f"`(ma50[i]/ma50[i-5]-1)*100 = "
                       f"({ma50_i:.2f}/{ma50_i_5:.2f}-1)*100 = {_angle_now:+.2f}%`")
    else:
        _angle_cell = f"`(ma50[i]/ma50[i-5]-1)*100 = — (i<54, MA50 未满 50 {_u})`"
    # 阈值用共享常量 MARKUP_ANGLE_MIN (helpers) —— 不写死 2.0。
    rows.append(f"| MA50 角度 | ≥{MARKUP_ANGLE_MIN:g}%/5{_u} (markup_ma_angle_min) | {_angle_cell} | {_yn(ma50_angle_5d >= MARKUP_ANGLE_MIN)} |")
    rows.append("")

    # ── Markup 三层门判定 (2026-11-05) ──
    # 背景: Markup 有 3 层强弱不同的门, 报告原来只写「判 Markup」, 不告诉读者
    #   走的是哪一档 → 600763 通策医疗 / 301109 军信股份只靠**加分线**成立
    #   (连 MA200 都不看), 门槛最低, 但报告长得跟严格门命中的票一样。
    #
    # ⚠️ 本块**完全由生产侧 channel 驱动** —— 渲染器不自己 AND/比较任何条件。
    #   channel 里的 main_strict / main_weak / main_extra 三个 token 就是生产侧
    #   实际走过的分支 (stage_factor 的 channel 段), 「本次判定」列直接由 token
    #   有无决定 → 渲染器与生产判定**不可能漂移**。
    #   「生产侧实际值」列只读 factor_values 既有字段, 不做任何派生计算。
    if stage == "Markup":
        _ch_parts = [p for p in (channel or "").split("+") if p]

        def _layer_mark(token: str) -> str:
            return "✅ 走" if token in _ch_parts else "⬜ 未走"

        _gc = fv.get("golden_cross_recent")
        _ams = fv.get("above_ma200_sustained")
        _gap = fv.get("ma_gap_pct_above") or 0
        _up = fv.get("is_uptrend")
        _am200 = fv.get("above_ma200")
        _d20 = fv.get("dev_ma20") or 0
        _pp = fv.get("pos_pct") or 0

        rows.append("**Markup 三层门判定** (本次判定走的是哪一层):")
        rows.append("")
        rows.append("| 判定层 | 条件 | 生产侧实际值 | 本次判定 |")
        rows.append("|---|---|---|---|")
        rows.append(
            f"| 严格门 | 金叉 + 持续 MA200 上方 + MA gap>0.5% + 上升趋势 | "
            f"`golden_cross_recent={_gc}` / `above_ma200_sustained={_ams}` / "
            f"`ma_gap_pct_above={_gap:.2f}%` / `is_uptrend={_up}` | {_layer_mark('main_strict')} |")
        rows.append(
            f"| 弱信号门 | MA200 上方 + MA gap>0.5% + 上升趋势 | "
            f"`above_ma200={_am200}` / `ma_gap_pct_above={_gap:.2f}%` / `is_uptrend={_up}` | "
            f"{_layer_mark('main_weak')} |")
        rows.append(
            f"| **加分线** | MA20 偏离>5% + 价格位置>60% + 上升趋势 | "
            f"`dev_ma20={_d20:+.2f}%` / `pos_pct={_pp:.1f}%` / `is_uptrend={_up}` | "
            f"{_layer_mark('main_extra')} |")
        rows.append("")
        rows.append("> ℹ️ **「⬜ 未走」= 本次判定不是靠这一层成立**, 不代表该层条件不满足。"
                    "严格门与弱信号门是**互斥 elif**(先满足者生效), 加分线是**独立 if**(可与前两层叠加)。")
        rows.append("")

        if strength == "extra_only":
            rows.append("> ⚠️ **仅加分线成立 (extra_only)** — 严格门与弱信号门**均未走**, "
                        "本次 Markup 判定**完全由加分线**给出。")
            rows.append("> 加分线只看 MA20 偏离 / 价格位置 / 上升趋势, **不看 MA200 / 金叉**, "
                        "门槛明显低于前两层 → 权重应低于严格门命中的票。")
            rows.append("")

    # ── 均线结构参考判据的独立表: 2026-11-03 整块删除 ──
    # 理由: 该组 4 条判据 (对齐外部参考实现的 _detect_markup_entry) 当前状态版
    #   (只看最后一根 K 线) 在 443 只生产池里 **0/443 命中**, 瓶颈是判据②
    #   「近 10 日金叉」只 4/443 通过 → 表里永远是 4 条全 ❌, 零信息量。
    #   而本项目的 Markup 判定用的是 stage_factor 的 AND 门 + fallback1/2,
    #   这 4 条判据**不参与判定** (commit c5cf58a 起已降级为参考信号)。
    #   → 展示一个「永远不通过、又不决定结论」的表 = 纯噪音 + 重复。
    #   生产侧仍保留 markup_detail.wta_* 输出 (detector 依赖), 只是报告不再展示。
    # ⚠️ 报告 md 里不再出现任何 "WTA" 字样 (用户 2026-11-03 明确要求)。

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
    rows.append(f"| 60 {_u}斜率向上 | slope_60 > 0 | `(p/closes[i-60]-1)*100 = ({p:.2f}/{c_i_60:.2f}-1)*100 = {(p/c_i_60-1)*100:+.2f}%` | {_yn(slope_60 > 0)} |")
    # MA250 偏离 — fallback1 代码用的就是 window(250) 均线 (stage_factor.py L152),
    #   不是真 MA200 → 标签必须写 MA250, 否则跟 Markup 段的真 MA200 混淆。
    rows.append(f"| MA250 偏离不深 | dev_ma200 > -15% | `(p/ma250-1)*100 = ({p:.2f}/{ma200:.2f}-1)*100 = {dev_ma200:+.2f}%` | {_yn(dev_ma200 > -15)} |")
    # MA50 接近 MA250 — 阈值必须用 MA50_MA200_TOLERANCE (代码实际用的值),
    #   不能写死 0.95 (容差调参后展示会跟代码对不上)
    _ma50_thr = ma200 * MA50_MA200_TOLERANCE
    rows.append(f"| MA50 接近 MA250 | MA50 ≥ MA250 × {MA50_MA200_TOLERANCE} | "
                f"`{ma50:.2f} ≥ {ma200:.2f} × {MA50_MA200_TOLERANCE} = {_ma50_thr:.2f}` | {_yn(_ma50_ma200_ok)} |")
    # 30 日不崩 — 3 步展开
    rows.append(f"| 30 {_u}不崩 | trend_30 > {FB1_TREND30_MIN:g}% | `(p/closes[i-31]-1)*100 = ({p:.2f}/{c_i_31:.2f}-1)*100 = {(p/c_i_31-1)*100:+.2f}%` | {_yn(trend_30 > FB1_TREND30_MIN)} |")
    fb_pass = slope_60 > 0 and dev_ma200 > -15 and _ma50_ma200_ok and trend_30 > FB1_TREND30_MIN
    rows.append(f"| **Fallback 触发** | 4 项全满足 | — | {_yn(fb_pass)} |")
    rows.append("")
    rows.append("> 📖 **Fallback 设计哲学**: 严格金叉漏检『已走强但金叉未完成』的票(主力压住空头但 MA50 还没破 MA200), "
               "4 项软条件捕获『大方向向上 + 接近金叉 + 短期不崩』的反弹主升。")
    rows.append("")

    # ── fallback2 路径 (stage_factor.py) ──
    # 背景: stage 判定实际有 3 条 fallback 路径, 报告原来只渲染 fallback1 (上面那张表),
    #   导致西麦食品这类票显示「Markup AND 门全 ❌ + Fallback 也 ❌」但结论是 Markup。
    #   真正做决定的是 fallback2, 必须渲染出来, 否则报告跟代码说的不是一回事。
    # 2026-11-03 修正: fallback2 是「主路径全 0 时的**兜底**」, 逻辑上必须比主路径宽松。
    #   之前误把那组外部参考判据当 fallback2 门槛 → fallback2 放行 0 只 (死代码),
    #   且语义上比主路径还严。现改为只要求 60 日方向向上 (slope_60 > 5),
    #   那组判据不参与判定, 报告也不展示。
    n_sub = fv.get("active_sub_event_count")
    if n_sub is None:
        n_sub = len(fv.get("active_sub_events") or [])
    _active = fv.get("active_sub_events") or []
    fb2_slope_ok = slope_60 > 5 or slope_60 < -5
    # 2026-11-03 同步 stage_factor.py 的修复: Accumulation 分支要求**双向下**
    #   (原来只有 trend_30 < -5, 跟代码里的 slope_60 < -5 && trend_30 < -5 不一致)
    fb2_trend_up = trend_30 > 5 and slope_60 > 5
    fb2_trend_dn = trend_30 < -5 and slope_60 < -5
    fb2_entry = fb2_slope_ok and (fb2_trend_up or fb2_trend_dn)
    rows.append("**Fallback 路径 2** (sub-event + 斜率, fallback1 不满足时启动):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 | 满足 |")
    rows.append("|---|---|---|---|")
    _sub_desc = f"{n_sub} 个 ({', '.join(_active[:6])}{'...' if len(_active) > 6 else ''})" if _active else f"{n_sub} 个"
    rows.append(f"| 有 sub-event | ≥ 1 个 | 当前 {_sub_desc} | {_yn(n_sub >= 1)} |")
    rows.append(f"| 60 {_u}斜率明显 (入口) | \\|slope_60\\| > 5 | `slope_60 = {slope_60:+.2f}%` | {_yn(fb2_slope_ok)} |")
    # 2026-11-03 改渲染: 「30日趋势向上 / 向下」是 **互斥分支** (if/elif),
    #   原来两条都列出来, 未走的那条显示 ❌ 容易被读成「有条件没过」→
    #   误以为 fallback2 不成立。改成只渲染**实际走的那个分支**, 另一条注明「未走」。
    if fb2_trend_up:
        rows.append(f"| **30 {_u}趋势向上** (→ Markup 分支) | trend_30 > 5 且 slope_60 > 5 | "
                    f"`trend_30 = {trend_30:+.2f}%`, `slope_60 = {slope_60:+.2f}%` | ✅ |")
        rows.append(f"| 30 {_u}趋势向下 (→ Accumulation 分支) | trend_30 < -5 且 slope_60 < -5 | "
                    f"`trend_30 = {trend_30:+.2f}%`, `slope_60 = {slope_60:+.2f}%` | ⬜ 未走 |")
        fb2_result = "→ Markup"
    elif fb2_trend_dn:
        rows.append(f"| 30 {_u}趋势向上 (→ Markup 分支) | trend_30 > 5 且 slope_60 > 5 | "
                    f"`trend_30 = {trend_30:+.2f}%`, `slope_60 = {slope_60:+.2f}%` | ⬜ 未走 |")
        rows.append(f"| **30 {_u}趋势向下** (→ Accumulation 分支) | trend_30 < -5 且 slope_60 < -5 | "
                    f"`trend_30 = {trend_30:+.2f}%`, `slope_60 = {slope_60:+.2f}%` | ✅ |")
        fb2_result = "→ Accumulation"
    else:
        rows.append(f"| 30 {_u}趋势向上 (→ Markup 分支) | trend_30 > 5 且 slope_60 > 5 | "
                    f"`trend_30 = {trend_30:+.2f}%`, `slope_60 = {slope_60:+.2f}%` | ⬜ 未走 |")
        rows.append(f"| 30 {_u}趋势向下 (→ Accumulation 分支) | trend_30 < -5 且 slope_60 < -5 | "
                    f"`trend_30 = {trend_30:+.2f}%`, `slope_60 = {slope_60:+.2f}%` | ⬜ 未走 |")
        fb2_result = "→ ?"
    rows.append(f"| **Fallback2 判定** | 入口 + 趋势方向 | `{fb2_result}` | {_yn(fb2_entry)} |")
    rows.append("")
    rows.append("> ℹ️ 表中「⬜ 未走」= 该互斥分支不成立 (不是『条件没过』); "
               "**Fallback2 判定** 那行才是最终结论。")
    rows.append("")
    if fb2_entry and fb2_trend_up:
        rows.append(f"> ✅ **本次阶段判定走的是 Fallback 2** (兜底路径): 有威科夫 sub-event + 60 {_u}斜率向上 + 30 {_u}趋势向上。")
        rows.append("")

    # Distribution 派发条件
    rows.append("**Distribution 派发** (AND 门强信号 / 弱信号 fallback):")
    rows.append("")
    rows.append("| 条件 | 阈值 | 实际 (公式 + 代入值 + 结果) | 满足 |")
    rows.append("|---|---|---|---|")
    # bias_200 偏离
    bias_200_val = fv.get("dev_ma200", 0)
    rows.append(f"| bias_200 偏离 (MA250) | bias_200 > 30% (强) / > 15% (弱) | `(p/ma250-1)*100 = {bias_200_val:+.2f}%` | {_yn(dist_d.get('bias_200_ok', False))} |")
    # 3 日连续缩量
    vol_5 = fv.get("vol_5", 0)
    vol_60 = fv.get("vol_60", 0)
    vol_shrink = vol_5 < vol_60 * 0.5
    rows.append(f"| 3 {_u}连续缩量 | 3{_u} 均量 < 60{_u} × 0.5 | `vol_5={vol_5:.0f} vs vol_60 × 0.5 = {vol_60*0.5:.0f} → {vol_shrink}` | {_yn(dist_d.get('vol_dry_3d', False))} |")
    # 派发起点
    dist_start = bias_200_val > 30 and vol_shrink
    rows.append(f"| 派发起点 | bias_200>30% AND vol_dry_3d | `{bias_200_val:+.2f}% > 30 AND {vol_shrink} → {dist_start}` | {_yn(dist_d.get('distribution_start', False))} |")
    return "\n".join(rows)


def _wy_sub_events_md(events: list, glossary: dict, current_stage: str,
                       c: list = None, h: list = None, l: list = None,
                       v: list = None, o: list = None, pct: list = None,
                       period_unit: str = "日") -> str:
    """
    9 种 sub-event 触发情况 + 中文释义 + 所属阶段
    2026-10-17 加: 每个 sub-event 的关键 factor 实际值 + 公式 (用户可手算验证)
    2026-10-17 v2: 公式和值排在一行: A (2) > B (1) + C (0.5)
    2026-11-05 加: period_unit —— 窗口单位跟周期一致 (周线段 = 周)。
                    公式里的 60/30/5/10 都是**当前周期的 bar 数**, 不是固定天数。
    """
    _u = period_unit
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
        # 2026-11-05: 周线段 bars 改成周线口径后, 短历史标的 (上市 < 5 年)
        #   周线根数可能不足 220 → 没有 MA200 基准, 手算表会给出假数值。
        #   与其编数字, 不如说明为什么没有这张表。
        rows.append("")
        rows.append(f"> ℹ️ **factor 手算表略去**: 当前周期仅 {n} 根 K 线 (需 ≥220 根才有 MA200 基准), 不做不可靠的代入。")
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

    # MarkupEntry — 2026-11-03 规则下沉: 直接读生产 detector 的逐条判据结果,
    #   报告不再自己手算一遍 (手算 = 两份实现必然漂移)。
    #   ⚠️ detector 里的 MA200 是**真 200 日均线**, 不是 bias_200 用的 250 日均线。
    from tools.factors.wyckoff.detectors.markup_entry import markup_entry_conditions
    _me = markup_entry_conditions(c, h, l, v, o, i, precomputed_arrs=arrs)
    ma200_true_i = _me["ma200"]
    ma50_i_5 = _me["ma50_5d_ago"]
    ma50_angle_5d = _me["angle"]
    _tol = MA50_MA200_TOLERANCE
    me_reached = _me["ma50_reached"]
    golden_cross = _me["golden_cross"]
    above_ma200_5d = _me["above_5d"]
    _me_thr = _me["ma50_thr"]
    _above5 = _me["above_days"]
    _me_pairs = _me["pairs"]

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
    rows.append(f"| **Spring** | 60 {_u}区间宽度 | `max(h[i-62..i-1]) - min(l[i-62..i-1]) = {zone_max_h:.2f} - {support_level:.2f} = {zone_max_h-support_level:.2f}` (≤ 4×ATR={4*atr_20:.2f}: {'✅' if (zone_max_h-support_level) <= 4*atr_20 else '❌'}) |")
    rows.append(f"| | bias_200 | `(c[i-1] / ma200 - 1) * 100 = ({c[i]:.2f} / {ma200_i:.2f} - 1) * 100 = {bias_200:+.2f}%` (≤ 15%: {'✅' if bias_200 <= 15 else '❌'}) |")
    rows.append(f"| | 跌破+收回 | `l[i-1] = {l[i-1]:.2f} < support_level = {support_level:.2f}` (至少一{_u}: {'✅' if (l[i-1] < support_level or l[i-2] < support_level) else '❌'}); `c[i-1] = {c[i-1]:.2f} > {support_level:.2f}` (收回: {'✅' if c[i-1] > support_level else '❌'}) |")
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
    rows.append(f"| | 单{_u}涨幅 ≥ 6% | `pct_chg[i] = {pct_chg:+.2f}%` ({'✅' if pct_chg >= 6 else '❌'}) |")
    rows.append(f"| | vol_ratio ≥ 3 | `v[i] / mean(v[i-59..i+1]) = {v[i]:.0f} / {vol_60_avg:.0f} = {vol_ratio_sos:.2f}` ({'✅' if vol_ratio_sos >= 3 else '❌'}) |")
    rows.append(f"| | 突破 60 {_u}高 | `h[i] = {h[i]:.2f} > max(h[i-60..i]) = {h_60max:.2f}` ({'✅' if h[i] > h_60max else '❌'}) |")
    rows.append(f"| **Compression** | MA 方向 | `ma_short = {ma50_i:.2f} >= ma_long = {ma60_i:.2f}` ({'✅' if ma50_i >= ma60_i else '❌'}) |")
    rows.append(f"| | bias_200 ≤ 25 | `{bias_200:+.2f}%` ({'✅' if bias_200 <= 25 else '❌'}) |")
    rows.append(f"| | 缩量 ≤ 0.6 | `mean(v[i-4..i+1]) / mean(v[i-24..i+1]) = {vol_5_25_avg:.0f} / {vol_25_5_avg:.0f} = {vol_decline:.3f}` ({'✅' if vol_decline <= 0.6 else '❌'}) |")
    rows.append(f"| **TrendPullback** | bias_200 ≤ 35 | `{bias_200:+.2f}%` ({'✅' if bias_200 <= 35 else '❌'}) |")
    rows.append(f"| | 回调幅度 | `(peak - c[i]) / peak = ({h_10max:.2f} - {p:.2f}) / {h_10max:.2f} = {pullback:.2f}%` ∈ [5%, 20%] ({'✅' if 5 <= pullback <= 20 else '❌'}) |")
    rows.append(f"| **MarkupEntry** | MA50 够到 MA200 (×{_tol}) | `ma50 = {ma50_i:.2f} ≥ ma200 × {_tol} = {ma200_true_i:.2f} × {_tol} = {_me_thr:.2f}` ({'✅' if me_reached else '❌'}) |")
    rows.append(f"| | 金叉 (近 {_me['lookback']} {_u}内, {_me_pairs} 对) | 前一{_u}不够到 → 当{_u}够到, 遍历 j ∈ [i-{_me['lookback'] - 1}..i] ({'✅' if golden_cross else '❌'}) |")
    rows.append(f"| | 连续 5 {_u}够到 | 最近 5 根 (i-5..i-1) 全部够到 = {_above5}/5 ({'✅' if above_ma200_5d else '❌'}) |")
    rows.append(f"| | MA50 5 {_u}角度 | `(ma50[i] / ma50[i-5] - 1) * 100 = ({ma50_i:.2f} / {ma50_i_5:.2f} - 1) * 100 = {ma50_angle_5d:+.2f}%` (≥ 2%: {'✅' if _me['angle_ok'] else '❌'}) |")
    rows.append(f"| **DistributionStart** | bias_200 > 30 | `{bias_200:+.2f}%` ({'✅' if bias_200 > 30 else '❌'}) |")
    rows.append(f"| | 3 {_u}缩量 | `mean(v[i-2..i+1]) = {vol_3_avg:.0f} < 0.5 * mean(v[i-59..i+1]) = {0.5*vol_60_avg:.0f}` ({'✅' if vol_3_avg < 0.5 * vol_60_avg else '❌'}) |")
    rows.append(f"| **UTAD** | 突破 1% | `h[i] = {h[i]:.2f} > max(h[i-60..i]) * 1.01 = {h_60max*1.01:.2f}` ({'✅' if h[i] > h_60max * 1.01 else '❌'}) |")
    rows.append(f"| | 收回 0.3% | `c[i] = {p:.2f} < max(h[i-60..i]) * 0.997 = {h_60max*0.997:.2f}` ({'✅' if p < h_60max * 0.997 else '❌'}) |")
    rows.append(f"| | 上影线 ≥ 0.35 | `(h[i] - max(o, c)) / (h[i] - l[i]) = ({h[i]:.2f} - {max(o[i] if o else p, p):.2f}) / ({h[i]:.2f} - {l[i]:.2f}) = {upper_shadow:.3f}` ({'✅' if upper_shadow >= 0.35 else '❌'}) |")
    rows.append(f"| | 放量 ≥ 1.5 | `v[i] / mean(v[i-20..i+1]) = {v[i]:.0f} / {vol_21_avg:.0f} = {vol_utad:.2f}` ({'✅' if vol_utad >= 1.5 else '❌'}) |")
    rows.append("")
    rows.append("> 💡 **手算提示**: 每行把 9 种 sub-event 的关键条件展开成 `公式 + 实际值 + 满足`,你直接照着验。")
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
    # ⚠️ 2026-11-05 修 BUG (既有): 周线段原来取 `s5.get("wyckoff_weekly")`,
    #   但 s5 (analysis dict) 里**从来没有** wyckoff_weekly 这个顶层 key
    #   (它只是 RawContext 上的字段, 没进 raw) → 永远 fallback 到 s5["wyckoff"]
    #   = **日线**结果。后果: 周线段的阶段/置信度/判定强度/基础指标/公式代入值
    #   全部是日线数值, 跟同段的周线中枢、sub-event 自相矛盾。
    #   实测 67 只样本: 31 只 stage 不一致、33 只 strength 不一致
    #   (600763 日线 Markup/extra_only, 周线真值 Accumulation/weak_B)。
    #   真源: raw["wyckoff"]["3period"][level] (analysis_engine.py L236)。
    _wy_root = s5.get("wyckoff") or {}
    _wy_3period = _wy_root.get("3period") or {}
    if level == "daily":
        wy_data = _wy_root
        wy_focus_label = wy_data.get("action", "等待")
    elif level == "weekly":
        wy_data = _wy_3period.get("weekly") or {}
        wy_focus_label = "长线趋势"
    else:
        wy_data = _wy_root
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
    # 2026-11-05: 周线判定不可用时的显式说明 (周线 bars < 30 → 生产侧不产出周线判定)。
    #   旧逻辑会退回日线结果, 让人以为「周线也判 Markup」—— 现在明确说不可用。
    _wy_unavail = ""
    if level == "weekly" and not wy_data:
        _wy_unavail = ("> ⚠️ **周线威科夫判定不可用**: 周线 K 线不足 30 根 (或无周线数据), "
                       "生产侧不产出周线判定 → 本段阶段/强度留空, **不回填日线结果**。\n\n")
    # v4: 阶段判定细节 (3 大阶段各自的 AND 门条件)
    accum_d = wy_data.get("accum_detail", {})
    markup_d = wy_data.get("markup_detail", {})
    dist_d = wy_data.get("distribution_detail", {})
    # v5 (2026-07-28): 9 种 sub-event, 跟 WyckoffTradingAgent L4 对齐
    from tools.factors.wyckoff.detectors import SUB_EVENT_GLOSSARY
    # 2026-10-17: 传 c/h/l/v 让 sub_event 表能列 factor 实际值
    # KLineBar 是 dataclass, 用属性访问 (b.close) 而非 dict (b["close"])
    # ⚠️ 2026-11-05 修 BUG: bars 必须**按周期取** —— 周线段的威科夫公式
    #   代入值要用周线 bars, 原来恒用日线 bars (data.kline),
    #   跟同段已改成周线口径的 factor_values 对不上 (周线事件 idx 会被
    #   当成日线 idx 用, 数值完全错位)。
    #   周线 bars 缺失时**不退回日线** —— 宁可少渲染公式表, 也不能给错数值。
    if level == "weekly":
        _bars = data.weekly_kline or []
    else:
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
                                      closes=_c, sub_events=sub_events,
                                      stage_detail=wy_data.get("stage_detail", ""),
                                      channel=wy_data.get("channel", ""),
                                      strength=wy_data.get("strength", ""),
                                      period_unit=("周" if level == "weekly" else "日"))
    wy_sub_events_md = _wy_sub_events_md(sub_events, SUB_EVENT_GLOSSARY, wy_stage,
                                          c=_c, h=_h, l=_l, v=_v, o=_o, pct=_pct,
                                          period_unit=("周" if level == "weekly" else "日"))
    scores_str = " / ".join(f"{k}:{v}" for k, v in scores.items() if v > 0) if scores else "无得分"
    if wy_stage == "?" or wy_conf == 0:
        wy_focus = f"K线不足（需≥30根）— {wy_focus_label}"
    else:
        wy_focus = f"阶段 {wy_stage} ({wy_name}) {wy_conf}% — {wy_action if level=='daily' else wy_focus_label}"

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

{_wy_unavail}> ℹ️ **置信度 = 该阶段得分 / 三阶段得分总和** (stage_factor.py: `max_score / sum(scores)`)。
> 只有单个阶段有分时必然是 100%, 含义是「**其它阶段都没意见**」, **不代表**「严格条件全通过」。
> 严格门是否通过看下面的「综合通过 pass_all」行 —— 没过但仍判此阶段的, 说明是**弱信号路径**给的 1 分。

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


# 因子历史走势 — 24 列 header / sep, 单一真源, t-analyze-all 等 batch 入口直接复用
# 2026-09-08 加 6 列: MACD/RSI/KDJ/ATR/量比/MACD状态 (TechnicalStrategy 时序, v6.2.8)
# 2026-09-08 删 2 列: ROC% / EY% (季报粒度, 跨季才变, 跟"## 财务数据" section 的 4 季财务 + Magic 表重复)
# 2026-10-08 删 2 列: 胜率%/中位成本 (cyq_perf, 跟 RSI/MA 同步, 没新信息, 删除)
# 2026-11-05 改: 「A天(日/周)」列 → 「威科夫强度」列, 内容改为简写记法 (m/m1/m2/A/D/—),
#   记法映射单一事实源 = tools.factors.wyckoff.helpers.strength_token, 渲染器只读不算。
# 2026-11-04 改列序: 威科夫 3 列 (威科夫(日/周) / 威科夫强度 / 子事件(日/周))
#   从「MA偏离」右边移到「收盘」右边 —— 用户要求威科夫信息靠前, 紧跟价格看。
#   列序: 日期 → 收盘 → 威科夫3列 → RSI6 → MACD... → PE
FACTOR_HISTORY_HEADER = "| 日期 | 收盘 | 威科夫(日/周) | 威科夫强度 | 子事件(日/周) | RSI6 | MACD | DIF | DEA | BARΔ | 量比 | MA20斜率 | MA偏离(MA5/20/60) | 日中枢 | 周中枢 | 买卖点 | 变化 | 布林% | BBW | KDJ(K/D/J) | KDJ金叉 | ATR% | MACD状态 | PE |"
# 2026-10-14 修: sep 从 header 自动算列数, 防止加列时 sep 跟 header 脱节
# (昨天 KDJ 加 2 列漏改 sep 25 cell vs 24 cell → GFM 表格不渲染)
FACTOR_HISTORY_SEP    = "|" + "---|" * (FACTOR_HISTORY_HEADER.count("|") - 1)


def _format_factor_row(rows: list[dict], idx: int) -> str | None:
    """单行因子历史 markdown 表格行 (24 列, header 跟 _section_factor_history 完全一致)

    Args:
        rows: compute_factor_history 输出, 按日期升序 (含 ma20_slope 字段)
        idx:  当前行索引

    Returns:
        "| date | ¥close | ... | 24 列" markdown 行 (无错位防护, 调用方负责)
        数据不足返 None
    """
    from tools.analysis.analysis_result_signals import diff_rows, format_signals_for_render
    if not rows or idx < 0 or idx >= len(rows):
        return None
    row = rows[idx]
    changes = diff_rows(rows[idx - 1], row) if idx > 0 else {}

    wy  = f"{row['wyckoff_daily'][0]}/{row['wyckoff_weekly'][0]}"

    # 威科夫强度列 (2026-11-05): 简写记法 m/m1/m2/mf1/mf2/A/D/—, 日/周两格。
    #   记法映射下沉到 helpers.strength_token, 渲染器只读不算 (规则下沉范式)。
    #   channel 必传: strength 把 fallback1 / fallback2 都塌成 "fallback",
    #   要分成 mf1 / mf2 只能靠生产侧 channel。
    def _tok(stage, strength, channel):
        return strength_token(stage or "?", strength, channel)
    strength_s = (f"{_tok(row.get('wyckoff_daily'),  row.get('wyckoff_strength_daily'),  row.get('wyckoff_channel_daily'))}"
                  f"/{_tok(row.get('wyckoff_weekly'), row.get('wyckoff_strength_weekly'), row.get('wyckoff_channel_weekly'))}")

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
        f"| {wy} | {strength_s} | {se} "
        f"| {rsi6_s} | {macd_s} | {dif_str} | {dea_str} | {bar_d_s} | {vol_s} "
        f"| {slope_s} | {ma_s} "
        f"| {_hub_str(row['hub_daily'])} | {_hub_str(row['hub_weekly'])} "
        f"| {b3} | {chg_str} "
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


# 2026-11-03: 删除 _section_ts_basic (📊 基础信息 (Tushare))
#   内容与报告头/板块行/估值段重复度极高 (代码/名称/行业/股本/市值)
#   数据源 DataStore.get_stock_basic 保留 (valuation / analysis_engine / batch 仍在用)
# 2026-11-03: 删除 _section_t_events (🎯 T 框架事件)
#   正文只剩一句 "⚠️ {code} T 框架事件待 LLM 补" 占位符, LLM 从未填充
#   事件清单改由 LLM 在生成报告时从外部查 (年报/新闻/公告) 后直接写入正文
# 2026-08-31: 删除 _section_ts_money_flow
# 2026-10-28: 主力资金流 (fflow) 维度整体删除, 详见 docs/fflow-removal-assessment.md
# (量价维度噪音大, 用户要求)


# ============================================================
# 主渲染器
# ============================================================

def render_report(data: RenderData, sector: str = "—") -> str:
    """
    渲染完整分析报告 (7 section 全部输出, 缺数据也保留)

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

**板块:** {sector} | **股价:** {price_str}

---

## 📊 数据完整性 (开篇即知, 一目了然)
{comp_table}
**完整度:** {data.completeness_pct()}% ({sum(1 for e, _ in comp.values() if e == '✅')}/{len(comp)} 项)

---

## 财务数据
{_section_finance_quarterly(data)}

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

> 📌 **结构说明**: 上面矩阵是 1 眼总览 (因子 × 3 周期各 1 行概要), 下面是每个周期的详情 (段表 / 9 子事件等矩阵没包含的独有信息)
> 不重复: 矩阵已包含的概要不在详情里再写

### 📋 日线分析 (因子 × 日线视角)
{_section_daily(data)}

---

### 📋 周线分析 (因子 × 周线视角)
{_section_weekly(data)}

---

## 🧪 GA 因子验证 (2026-07-27 加, 实验性)
{_section_ga_factor(data)}

---

## 🤖 XGBoost 校准
{_section_xgboost(data)}

---

## 📡 数据源矩阵 (类型/主源/备源/状态) — 固化不丢
{_section_data_sources(data)}

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
