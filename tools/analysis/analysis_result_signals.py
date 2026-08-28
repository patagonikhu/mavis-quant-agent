"""
analysis_result_signals.py - 历史因子计算 + 信号 diff

核心函数:
  compute_factor_history(ctx, step, lookback) → list[dict]
  diff_rows(prev, curr)                       → dict  变化摘要
  backtest_signal(rows, signal_fn, direction, forward_days) → dict

render 和回测都调 compute_factor_history，保证一致性。
"""
from __future__ import annotations
from typing import Callable


def compute_factor_history(ctx, step: int = 1, lookback: int = 60,
                           strategies=None) -> list[dict]:
    """计算最近 lookback 天的因子历史，每 step 天一个节点。

    Args:
        ctx:         RawContext，含完整 K 线
        step:        采样间隔（render 用 5，回测用 1）
        lookback:    回看天数（默认 60 = 3个月）
        strategies:  传入 strategy 类列表，None 表示跑全部

    Returns:
        list[dict]，按日期升序
    """
    from tools.analysis.analysis_engine import AnalysisEngine
    engine = AnalysisEngine(strategies=strategies)

    kline = ctx.kline
    start = max(0, len(kline) - lookback)
    dates = [kline[i]["trade_date"] for i in range(start, len(kline), step)]

    # 批量历史计算：每个 strategy 自己决定怎么遍历，不在外部切片
    history = engine.analyze_history(ctx, dates)

    # 预建索引，避免 for-loop 里 O(n) 查找
    import bisect
    date_to_ki    = {k["trade_date"].replace("-","")[:8]: i for i, k in enumerate(kline)}
    date_to_close = {k["trade_date"].replace("-","")[:8]: k["close"] for k in kline}
    weekly_dates_sorted = [b["trade_date"].replace("-","")[:8] for b in (ctx.weekly or [])]

    # 从 ctx 预算数组里取各日期对应值（WyckoffStrategy 已算，O(1) 查询）
    d_arrs = ctx.kline_arrs        # 日线 arrs
    w_arrs = ctx.kline_arrs_weekly # 周线 arrs
    weekly_date_to_ki = {b["trade_date"].replace("-","")[:8]: i for i, b in enumerate(ctx.weekly or [])}

    rows = []
    for date in dates:
        date_clean = date.replace("-", "")[:8]
        result = history.get(date_clean)
        if result is None:
            continue
        close = date_to_close.get(date_clean, 0)

        ki = date_to_ki.get(date_clean, len(kline))
        wi = bisect.bisect_right(weekly_dates_sorted, date_clean) - 1

        # MA/Boll 从预算数组 O(1) 读，不重算
        obv_raw = (result.raw if hasattr(result, 'raw') else {}).get("obv") or {}
        ma20    = d_arrs.get('ma20',  [None] * (ki+2))
        ma120   = d_arrs.get('ma120', [None] * (ki+2))
        boll_upper = d_arrs.get('boll_upper', [None] * (ki+2))
        boll_lower = d_arrs.get('boll_lower', [None] * (ki+2))
        boll_mid   = d_arrs.get('boll_mid',   [None] * (ki+2))
        boll_pct   = d_arrs.get('boll_pct',   [None] * (ki+2))
        boll_width = d_arrs.get('boll_width',  [None] * (ki+2))
        w_ma20  = w_arrs.get('ma20', [None] * (wi+2))

        def _dev(price, ma):
            return round((price / ma - 1) * 100, 1) if ma and ma > 0 else None

        ma_devs = {
            "ma_dev_daily":  obv_raw.get("ma20_dev")  or _dev(close, ma20[ki]  if ki < len(ma20)  else None),
            "ma_dev_weekly": _dev(close, w_ma20[wi] if wi >= 0 and wi < len(w_ma20) else None),
            "ma120_dev":     obv_raw.get("ma120_dev") or _dev(close, ma120[ki] if ki < len(ma120) else None),
            "boll_upper":    boll_upper[ki] if ki < len(boll_upper) else None,
            "boll_lower":    boll_lower[ki] if ki < len(boll_lower) else None,
            "boll_mid":      boll_mid[ki]   if ki < len(boll_mid)   else None,
            "boll_pct":      boll_pct[ki]   if ki < len(boll_pct)   else None,
            "boll_width":    boll_width[ki] if ki < len(boll_width) else None,
        }

        rows.append({**_extract_row(result, date, close, ctx), **ma_devs})
        # 提取完立即释放，避免 AnalysisResult 大对象在循环结束前全部驻留内存
        del history[date_clean]

    # post-process: 计算三周期连续 Accum 天数（升序扫，遇非Accum重置）
    for field, out_key in [
        ('wyckoff_daily',  'accum_days_daily'),
        ('wyckoff_weekly', 'accum_days_weekly'),
    ]:
        count = 0
        for row in rows:
            val = row.get(field) or ''
            if val.startswith('Accum') or val == 'Accumulation':
                count += 1
            else:
                count = 0
            row[out_key] = count

    return rows


def _extract_row(result, date: str, close: float, ctx=None) -> dict:
    """从 AnalysisResult 提取单行因子快照"""
    raw       = result.raw
    chan_raw   = raw.get("chan",             {}) or {}
    bsp_raw    = raw.get("buy_sell_points", {}) or {}
    wy_raw     = raw.get("wyckoff",         {}) or {}
    smc_raw    = raw.get("smc",             {}) or {}
    fflow_raw  = raw.get("fflow",           {}) or {}
    obv_raw    = raw.get("obv",             {}) or {}
    p3         = wy_raw.get("3period") or {}

    pos_raw = raw.get("position", {}) or {}

    return {
        # 基础
        "date":       date,
        "close":      close,
        "scene":      result.scene,
        "resonance":  result.resonance_count,

        # 移植因子: 价格位置 (WyckoffTradingAgent → mavis, 2026-08)
        "close_pos_day":       float(pos_raw.get("close_pos_day", 0.0))    if isinstance(pos_raw, dict) else 0.0,
        "close_pos_20":        float(pos_raw.get("close_pos_20", 0.0))     if isinstance(pos_raw, dict) else 0.0,
        "upper_shadow_pct":    float(pos_raw.get("upper_shadow_pct", 0.0)) if isinstance(pos_raw, dict) else 0.0,
        "upper_shadow_5d_avg": float(pos_raw.get("upper_shadow_5d_avg", 0.0)) if isinstance(pos_raw, dict) else 0.0,

        # 威科夫二주期
        "wyckoff_daily":  (p3.get("daily")  or {}).get("stage", "?"),
        "wyckoff_weekly": (p3.get("weekly") or {}).get("stage", "?"),

        # 威科夫 sub_event 当天新触发（顶/底都显示）
        "sub_event_daily":  _today_sub_events(wy_raw, "daily",  date),
        "sub_event_weekly": _today_sub_events(wy_raw, "weekly", date),

        # 中枢二周期（含价格）
        "hub_daily":  _extract_hub(chan_raw, "daily"),
        "hub_weekly": _extract_hub(chan_raw, "weekly"),

        # 买卖点二周期（含价格字符串）
        "bsp_daily":  _active_bsp(bsp_raw, "daily"),
        "bsp_weekly": _active_bsp(bsp_raw, "weekly", date.replace("-", "")[:8]),

        # SMC 日线/周线 OB/FVG/Sweep
        "smc_bull_ob":       (smc_raw.get("nearest_bull_ob")  or {}).get("bottom"),
        "smc_bear_ob":       (smc_raw.get("nearest_bear_ob")  or {}).get("top"),
        "smc_fvg_bull":      (smc_raw.get("nearest_fvg_bull") or {}).get("bottom"),
        "smc_fvg_bear":      (smc_raw.get("nearest_fvg_bear") or {}).get("top"),
        "smc_sweeps_today":  _today_sweeps(smc_raw, date),
        # 周线 SMC
        "smc_w_bull_ob":     ((smc_raw.get("smc_weekly") or {}).get("nearest_bull_ob")  or {}).get("bottom"),
        "smc_w_bear_ob":     ((smc_raw.get("smc_weekly") or {}).get("nearest_bear_ob")  or {}).get("top"),
        "smc_w_fvg_bull":    ((smc_raw.get("smc_weekly") or {}).get("nearest_fvg_bull") or {}).get("bottom"),
        "smc_w_fvg_bear":    ((smc_raw.get("smc_weekly") or {}).get("nearest_fvg_bear") or {}).get("top"),
        "smc_w_sweeps_today": _today_sweeps(smc_raw.get("smc_weekly") or {}, date),

        # fflow 主力资金流 (2026-08-17 拆分: 之前 vp_verdict/vp_score 混在一起)
        "fflow_verdict":  fflow_raw.get("verdict", "—"),
        "fflow_score":    fflow_raw.get("score", 0),
        "fflow_net_5d":   round(fflow_raw.get("fflow_net_5d", 0), 2),
        "fflow_trend_3d": fflow_raw.get("trend_3d", "—"),

        # OBV 经典量价 (2026-08-17 拆分: 独立 strategy)
        "obv_verdict":        obv_raw.get("verdict", "—"),
        "obv_strategy_score": obv_raw.get("score", 0),
        "obv_div_bot_60d":    obv_raw.get("obv_div_bot_60d", 0),
        "obv_div_top_60d":    obv_raw.get("obv_div_top_60d", 0),
    }


def _today_sweeps(smc_raw: dict, date: str) -> list:
    """返回当天新触发的 sweep（buy_side/sell_side）"""
    sweeps = smc_raw.get("recent_sweeps") or []
    date_clean = date.replace("-", "")[:8]
    today = []
    seen = set()
    for s in sweeps:
        if str(s.get("date", "")).replace("-", "")[:8] == date_clean:
            key = (s["type"], s.get("swept_level"))
            if key not in seen:
                seen.add(key)
                today.append(s)
    return today


def _today_sub_events(wy_raw: dict, level: str, date: str) -> str:
    """返回当天新触发的 sub_events (去重)"""
    evs = (wy_raw.get("sub_events_by_period") or {}).get(level, [])
    if not evs:
        return "—"
    date_clean = date.replace("-", "")[:8]
    names = sorted({e["name"] for e in evs
                    if str(e.get("date", "")).replace("-", "")[:8] == date_clean})
    return " ".join(names) if names else "—"


def _extract_hub(chan_raw: dict, level: str) -> dict:
    h = (chan_raw.get(level) or {}).get("hub") or {}
    return {
        "low":   h.get("low"),
        "high":  h.get("high"),
        "pos":   h.get("pos", "—"),
        "valid": h.get("valid", False),
    }


def _active_bsp(bsp_raw: dict, level: str, as_of: str = "") -> dict:
    """返回有触发的买卖点（过滤掉 '—'）"""
    pts = bsp_raw.get(level) or {}
    return {k: v for k, v in pts.items()
            if v and v != "—" and k != "action"}


# ============================================================
# diff：相邻两行的变化
# ============================================================


def obv_label(row: dict) -> str:
    """OBV 标签 (2026-08-18 统一)

    输入 row 字段 (来自 factor_history.compute_factor_history 输出):
      - obv_verdict: 5 档 (🟢主力进货 / 🟡偏进货 / ⬜中性 / 🟠偏出货 / 🔴主力出货)
      - obv_div_top_60d: 60 日扫描 顶背离 15日窗口命中数 (0-4)
      - obv_div_bot_60d: 60 日扫描 底背离 15日窗口命中数 (0-4)

    输出格式: "🟡偏进货  OBV[顶×1]  OBV[底×3]" / "—" (无信号)
      - verdict 用 emoji (除非中性/无数据)
      - 顶/底段背离 分别用方括号显示 (≥1 才显示)
      - 视觉分离: emoji vs 方括号

    复用方:
      - tools/batch/batch_summary.py: signal-watchlist.md OBV 列
      - tools/render/report_renderer.py: analyze-*.md 因子历史走势表 OBV 列
    """
    parts = []
    verdict = row.get("obv_verdict", "—") or "—"
    div_top = row.get("obv_div_top_60d", 0) or 0
    div_bot = row.get("obv_div_bot_60d", 0) or 0
    if verdict not in ("—", "中性", "无数据", "⬜中性"):
        parts.append(verdict)
    if div_top >= 1:
        parts.append(f"OBV[顶×{div_top}]")
    if div_bot >= 1:
        parts.append(f"OBV[底×{div_bot}]")
    return "  ".join(parts) if parts else "—"


# ============================================================

def diff_rows(prev: dict, curr: dict) -> dict:
    """对比相邻两行，返回变化摘要。

    Returns:
        dict，只包含有变化的字段，如:
          {"scene": "C→A", "wyckoff_daily": "Accumulation→Markup",
           "new_bsp_daily": {"🔴2卖": "¥258.02 接近"}}
    """
    changes = {}

    # 标量字段：直接比
    for field in ("scene", "wyckoff_daily", "wyckoff_weekly"):
        if prev.get(field) != curr.get(field):
            changes[field] = f"{prev.get(field)}→{curr.get(field)}"

    # sub_event：只记录与前一天不同的新事件（避免同一事件连续多天重复记录）
    for field in ("sub_event_daily", "sub_event_weekly"):
        p = prev.get(field, "—") or "—"
        c = curr.get(field, "—") or "—"
        if c != "—" and c != p:
            changes[field] = c

    # 买卖点：新出现的点
    for level in ("bsp_daily", "bsp_weekly"):
        prev_pts = prev.get(level) or {}
        curr_pts = curr.get(level) or {}
        new_pts  = {k: v for k, v in curr_pts.items() if k not in prev_pts}
        gone_pts = {k: v for k, v in prev_pts.items() if k not in curr_pts}
        if new_pts:
            changes[f"new_{level}"]  = new_pts
        if gone_pts:
            changes[f"gone_{level}"] = gone_pts

    # 中枢变化（区间变 or 位置变，任一触发）
    for level in ("hub_daily", "hub_weekly"):
        p_hub = prev.get(level) or {}
        c_hub = curr.get(level) or {}
        p_pos = p_hub.get("pos", "—")
        c_pos = c_hub.get("pos", "—")
        p_range = (p_hub.get("low"), p_hub.get("high"))
        c_range = (c_hub.get("low"), c_hub.get("high"))
        range_changed = p_range != c_range and c_range != (None, None)
        pos_changed = p_pos != c_pos
        if range_changed and pos_changed:
            changes[f"{level}_pos"] = f"区间变+位置{p_pos}→{c_pos}"
        elif range_changed:
            changes[f"{level}_pos"] = f"中枢区间变"
        elif pos_changed:
            changes[f"{level}_pos"] = f"{p_pos}→{c_pos}"

    # MA20 偏离穿越（跨越 ±10% / ±20% / ±30% 阈值时记录）
    _MA_THRESHOLDS = [30, 20, 10, -10, -20, -30]

    def _ma_zone(v):
        """把 MA 偏离值归入区间标签"""
        if v is None: return None
        if v >=  30: return "≥+30%🔴"
        if v >=  20: return "≥+20%🟠"
        if v >=  10: return "≥+10%🟡"
        if v <= -30: return "≤-30%🔴"
        if v <= -20: return "≤-20%🟠"
        if v <= -10: return "≤-10%🟡"
        return "正常"

    for field in ("ma_dev_daily", "ma_dev_weekly"):
        p_v = prev.get(field)
        c_v = curr.get(field)
        if p_v is None or c_v is None:
            continue
        p_zone = _ma_zone(p_v)
        c_zone = _ma_zone(c_v)
        if p_zone != c_zone:
            level = field.replace("ma_dev_", "")  # daily / weekly
            changes[field] = f"{p_zone}→{c_zone}({c_v:+.1f}%)"

    return changes


# 信号提取 (diff_rows 输出 → 结构化信号列表)
# 唯一真源：render 和 backtest 都调这里，不重复实现
# ============================================================

_BSP_BUY  = {"1买", "2买", "3买", "趋势1买", "趋势2买", "盘整1买", "盘整2买"}
_BSP_SELL = {"1卖", "2卖", "3卖", "趋势1卖", "趋势2卖", "盘整1卖", "盘整2卖"}
_WY_TOP   = {"DistributionStart", "UTAD", "EVR", "LPSY", "BC", "UT"}
_WY_BOT   = {"Spring", "LPS", "SOS", "SC", "AR", "Compression"}
_SCENE_BUY  = {"A", "D"}   # A=主升, D=底部
_SCENE_SELL = {"B", "C"}   # B=派发, C=震荡转弱


def _bsp_direction(label: str):
    clean = label.split("(")[0].strip()
    for k in _BSP_BUY:
        if k in clean: return "buy"
    for k in _BSP_SELL:
        if k in clean: return "sell"
    return None


def _scene_direction(val: str):
    s = val.split("→")[-1].strip() if "→" in val else val
    s = s[0] if s else ""
    if s in _SCENE_BUY:  return "buy"
    if s in _SCENE_SELL: return "sell"
    return None


def _hub_direction(val: str):
    new_pos = val.split("→")[-1].strip() if "→" in val else val
    if "上方" in new_pos: return "buy"
    if "下方" in new_pos or "跌穿" in new_pos: return "sell"
    return None


def extract_signals(changes: dict) -> list[tuple[str, str, str]]:
    """diff_rows 输出 → [(signal_type, detail, direction), ...]

    signal_type: bsp_new / bsp_gone /
                 wyckoff_event_top / wyckoff_event_bot /
                 scene_change / hub_pos_change
    direction:   "buy" | "sell"

    render 和 backtest 都调这个函数，保持一致。
    """
    signals = []

    # 买卖点（新出现 / 消失）
    for level in ("bsp_daily", "bsp_weekly"):
        period = level.split("_")[1]
        for label in changes.get(f"new_{level}", {}):
            d = _bsp_direction(label)
            if d: signals.append(("bsp_new", f"{label}({period})", d))
        for label in changes.get(f"gone_{level}", {}):
            orig = _bsp_direction(label)
            if orig:
                signals.append(("bsp_gone", f"{label}消失({period})",
                                 "sell" if orig == "buy" else "buy"))

    # 威科夫子事件
    for field in ("sub_event_daily", "sub_event_weekly"):
        event = changes.get(field, "")
        if not event or event == "—":
            continue
        period = field.split("_")[2]
        for ev in str(event).split(","):
            ev = ev.strip()
            if ev in _WY_TOP: signals.append(("wyckoff_event_top", f"{ev}({period})", "sell"))
            elif ev in _WY_BOT: signals.append(("wyckoff_event_bot", f"{ev}({period})", "buy"))

    # 场景切换
    if "scene" in changes:
        d = _scene_direction(changes["scene"])
        if d: signals.append(("scene_change", changes["scene"], d))

    # 中枢位置变化
    for field in ("hub_daily_pos", "hub_weekly_pos"):
        if field not in changes:
            continue
        d = _hub_direction(changes[field])
        if d:
            level = field.split("_")[1]
            signals.append(("hub_pos_change", f"{changes[field]}({level})", d))

    # MA20 偏离穿越
    for field in ("ma_dev_daily", "ma_dev_weekly"):
        if field not in changes:
            continue
        val = changes[field]   # 例: "正常→≥+20%🟠(+21.3%)"
        new_zone = val.split("→")[-1] if "→" in val else val
        level = field.replace("ma_dev_", "")  # daily / weekly
        # 正偏离穿越 → 过热警告（卖）; 负偏离穿越 → 超跌（买）
        if any(t in new_zone for t in ("+10%", "+20%", "+30%")):
            signals.append(("ma_dev_cross", f"MA过热{new_zone}({level})", "sell"))
        elif any(t in new_zone for t in ("-10%", "-20%", "-30%")):
            signals.append(("ma_dev_cross", f"MA超跌{new_zone}({level})", "buy"))
        # 从过热/超跌回归正常
        elif "正常" in new_zone:
            old_zone = val.split("→")[0]
            if any(t in old_zone for t in ("+10%", "+20%", "+30%")):
                signals.append(("ma_dev_cross", f"MA回落→正常({level})", "buy"))
            elif any(t in old_zone for t in ("-10%", "-20%", "-30%")):
                signals.append(("ma_dev_cross", f"MA回升→正常({level})", "sell"))

    return signals


def format_signals_for_render(changes: dict) -> list[str]:
    """extract_signals 结果 → render 用的 emoji 字符串列表（变化列）"""
    parts = []
    for sig_type, detail, direction in extract_signals(changes):
        icon = "🟢" if direction == "buy" else "🔴"
        if sig_type == "bsp_new":
            parts.append(f"🆕{detail}")
        elif sig_type == "bsp_gone":
            parts.append(f"❌{detail}")
        elif sig_type == "wyckoff_event_top":
            parts.append(f"🔴{detail}")
        elif sig_type == "wyckoff_event_bot":
            parts.append(f"✅{detail}")
        elif sig_type == "scene_change":
            parts.append(f"🔄场景{detail}")
        elif sig_type == "hub_pos_change":
            parts.append(f"🔄中枢{detail}")
        elif sig_type == "ma_dev_cross":
            parts.append(f"📊{detail}")
    # wyckoff stage 变化（不在 extract_signals 里，单独处理）
    for field in ("wyckoff_daily", "wyckoff_weekly"):
        if field in changes:
            parts.append(f"🔄wy{field.split('_')[1]}:{changes[field]}")
    return parts


# ============================================================
# 当日顶信号评分 (2026-08-01 提到 analysis_result_signals.py, 单一真源)
# 供 render (写md表格) 和 weekly_top_score (按周聚合) 共同调用
# ============================================================

TOP_SIGNAL_WEIGHTS: dict[str, int] = {
    # 买卖点 (2026-08-21 调: 2卖 4→2, 3卖 1→0, 按 10% 阈值 38%/21% 弱)
    "sell_1卖⭐":             10,
    "sell_1卖":                7,
    "sell_2卖":                2,   # 2026-08-21: 4→2, 10% 阈值 胜率 38.3% (<60% 调低)
    "sell_3卖":                0,   # 2026-08-21: 1→0, 10% 阈值 胜率 21.1% (失效)
    # 威科夫子事件
    "wy_DistributionStart":    2,   # 2026-08-20: 6→2, 8-19 没单测
    "wy_UTAD":                 10,  # 2026-08-20: 8→10, 8-19 14:40 bt_top.log 胜率 100.0% (33 触发, th=3%)
    "wy_EVR":                  6,   # 2026-08-21: 1→6, user 拍板"调回去", 长期 85% 极强 (跟 Spring 持平)
    "wy_LPSY":                 2,   # 2026-08-20: 4→2, 8-19 没单测
    "wy_SOW":                  2,   # 2026-08-20: 4→2, 8-19 没单测
    # 中枢
    "hub_跌进中枢":             3,
    "hub_跌出中枢":             7,   # 5→7, 中枢真破位最强信号
    "hub_深跌":                 1,   # 2026-08-20: 3→1, 8-19 没单测
    # MA 偏离
    "ma_日线≥+20%":             2,
    "ma_日线≥+30%":             4,
}


def score_top_signals(
    changes: dict,
    row: dict,
    prev_row: dict | None,
) -> dict:
    """从 diff_rows 输出 + 当前行, 算当日顶信号总分 + 触发列表。

    Returns:
        {
            "score": int,                        # 当日总分
            "signals": [(signal_key, weight, label), ...],  # 触发的信号, 去重
        }

    同一 signal_key 当天只算一次。
    """
    found: dict[str, tuple[int, str]] = {}

    def add(key: str, label: str = ""):
        w = TOP_SIGNAL_WEIGHTS.get(key, 0)
        if w > 0 and key not in found:
            found[key] = (w, label or key)

    # 1. 买卖点 1卖/2卖/3卖 (新出现才计分, 跟其他信号一致)
    # 2026-08-01: 改规则 — 之前用 row[level] "持续加分"导致重复
    for level in ("bsp_daily", "bsp_weekly"):
        new_pts = changes.get(f"new_{level}", {}) or {}
        for k in new_pts:
            if "1卖⭐" in k:
                add("sell_1卖⭐", k)
            elif "1卖" in k:
                add("sell_1卖", k)
            elif "2卖" in k:
                add("sell_2卖", k)
            elif "3卖" in k:
                add("sell_3卖", k)

    # 2. 威科夫子事件 (新出现)
    _WY_TOP = {"DistributionStart", "UTAD", "EVR", "LPSY", "SOW"}
    for level in ("sub_event_daily", "sub_event_weekly"):
        curr_ev = row.get(level, "—") or "—"
        prev_ev = (prev_row or {}).get(level, "—") or "—"
        if curr_ev != "—" and curr_ev != prev_ev:
            for ev in curr_ev.split():
                if ev in _WY_TOP:
                    add(f"wy_{ev}", ev)

    # 4. 中枢位置变化 (3 周期都算, 同类只算最高分)
    hub_seen = set()
    for level in ("hub_daily_pos", "hub_weekly_pos"):
        hub_pos = changes.get(level, "")
        if not hub_pos:
            continue
        new_pos = hub_pos.split("→")[-1] if "→" in hub_pos else hub_pos
        old_pos = hub_pos.split("→")[0] if "→" in hub_pos else ""
        # 跌进中枢: 上方 → 内部/下方/跌穿
        if "内部" in new_pos and "上方" in old_pos and "hub_跌进中枢" not in hub_seen:
            add("hub_跌进中枢", hub_pos)
            hub_seen.add("hub_跌进中枢")
        # 跌出中枢: 上方/内部 → 下方/跌穿 (跌穿→下方 是止跌, 排除)
        elif "上方" in old_pos and any(x in new_pos for x in ("下方", "跌穿")) and "hub_跌出中枢" not in hub_seen:
            add("hub_跌出中枢", hub_pos)
            hub_seen.add("hub_跌出中枢")
        elif "内部" in old_pos and any(x in new_pos for x in ("下方", "跌穿")) and "hub_跌出中枢" not in hub_seen:
            add("hub_跌出中枢", hub_pos)
            hub_seen.add("hub_跌出中枢")
        # 内部→跌穿 极弱, 加 1 分
        elif "跌穿" in new_pos and "内部" in old_pos and "hub_深跌" not in hub_seen:
            add("hub_深跌", hub_pos)
            hub_seen.add("hub_深跌")

    # 5. MA20 偏离穿越
    ma_val = changes.get("ma_dev_daily", "")
    if ma_val:
        new_zone = ma_val.split("→")[-1] if "→" in ma_val else ma_val
        if "+30%" in new_zone or "≥+30" in new_zone:
            add("ma_日线≥+30%", ma_val)
        elif "+20%" in new_zone or "≥+20" in new_zone:
            add("ma_日线≥+20%", ma_val)

    signals = [(k, w, label) for k, (w, label) in found.items()]
    return {
        "score": sum(w for _, w, _ in signals),
        "signals": signals,
    }


# ============================================================
# 当日底信号评分 (2026-08-01 新增, 镜像 score_top_signals)
# 底信号: 1买/2买/3买 (买点) + 底背驰 (买入信号) + 威科夫底部事件 + 中枢突破 + MA 超跌
# ============================================================

BOTTOM_SIGNAL_WEIGHTS: dict[str, int] = {
    # 买卖点 (2026-08-20 调: 3买 3→5, 0买 6→7, 按 8-19 14:40 bt_top.log 胜率 87%+)
    "buy_1买⭐":             10,
    "buy_1买":                7,
    "buy_2买":                6,   # 4→6
    "buy_3买":                5,   # 2026-08-20: 3→5, 8-19 14:40 bt_top.log 胜率 87.1% (815 触发) 严重低估
    "buy_0买":                7,   # 2026-08-20: 6→7, 8-19 14:40 bt_top.log 胜率 87.8% (765 触发)
    # 威科夫底部子事件 (2026-08-21 调: Spring 6→2, 10% 阈值 38.9% 暴跌)
    "wy_Spring":              2,   # 2026-08-21: 6→2, 10% 阈值 胜率 38.9% (<60% 调低, 3% 阈值仍 83% 但 10% 失效)
    "wy_LPS":                 8,   # 2026-08-20: 5→8, 8-19 14:40 bt_top.log 胜率 92.4% (238 触发) 极强
    "wy_SOS":                 2,   # 2026-08-20: 4→2, 8-19 没单测
    "wy_SC":                  2,   # 2026-08-20: 4→2, 8-19 没单测
    "wy_AR":                  3,
    "wy_Compression":         1,   # 2026-08-20: 2→1, 8-19 没单测
    # 中枢
    "hub_涨出中枢":            7,   # 5→7
    "hub_涨进中枢":            3,
    "hub_止跌":                1,
    # MA 偏离
    "ma_日线≤-20%":            2,
    "ma_日线≤-30%":            4,
}


def score_bottom_signals(
    changes: dict,
    row: dict,
    prev_row: dict | None,
) -> dict:
    """从 diff_rows + row 算当日底信号总分 + 触发列表。

    Returns:
        {"score": int, "signals": [(signal_key, weight, label), ...]}
    """
    found: dict[str, tuple[int, str]] = {}

    def add(key: str, label: str = ""):
        w = BOTTOM_SIGNAL_WEIGHTS.get(key, 0)
        if w > 0 and key not in found:
            found[key] = (w, label or key)

    # 1. 买卖点 1买/2买/3买/0买 (新出现才计分, 跟其他信号一致)
    # 2026-08-01: 改规则 — 之前用 row[level] "持续加分"导致重复
    for level in ("bsp_daily", "bsp_weekly"):
        new_pts = changes.get(f"new_{level}", {}) or {}
        for k in new_pts:
            if "1买⭐" in k:
                add("buy_1买⭐", k)
            elif "1买" in k:
                add("buy_1买", k)
            elif "2买" in k:
                add("buy_2买", k)
            elif "3买" in k:
                add("buy_3买", k)
            elif "0买" in k:
                add("buy_0买", k)

    # 2. 威科夫底部子事件 (新出现)
    _WY_BOT = {"Spring", "LPS", "SOS", "SC", "AR", "Compression"}
    for level in ("sub_event_daily", "sub_event_weekly"):
        curr_ev = row.get(level, "—") or "—"
        prev_ev = (prev_row or {}).get(level, "—") or "—"
        if curr_ev != "—" and curr_ev != prev_ev:
            for ev in curr_ev.split():
                if ev in _WY_BOT:
                    add(f"wy_{ev}", ev)

    # 4. 中枢位置变化 (3 周期都算, 底方向: 止跌 / 反弹)
    hub_seen = set()
    for level in ("hub_daily_pos", "hub_weekly_pos"):
        hub_pos = changes.get(level, "")
        if not hub_pos:
            continue
        old_pos = hub_pos.split("→")[0] if "→" in hub_pos else ""
        new_pos = hub_pos.split("→")[-1] if "→" in hub_pos else hub_pos
        # 涨出中枢 (下方/跌穿 → 上方) 强买信号
        if "上方" in new_pos and ("下方" in old_pos or "跌穿" in old_pos) and "hub_涨出中枢" not in hub_seen:
            add("hub_涨出中枢", hub_pos)
            hub_seen.add("hub_涨出中枢")
        # 涨进中枢 (下方/跌穿 → 内部) 弱买信号
        elif "内部" in new_pos and ("下方" in old_pos or "跌穿" in old_pos) and "hub_涨进中枢" not in hub_seen:
            add("hub_涨进中枢", hub_pos)
            hub_seen.add("hub_涨进中枢")
        # 止跌 (跌穿 → 下方) 极弱反弹, 1 分
        elif "下方" in new_pos and "跌穿" in old_pos and "hub_止跌" not in hub_seen:
            add("hub_止跌", hub_pos)
            hub_seen.add("hub_止跌")

    # 5. MA20 偏离穿越 (超跌)
    ma_val = changes.get("ma_dev_daily", "")
    if ma_val:
        new_zone = ma_val.split("→")[-1] if "→" in ma_val else ma_val
        if "-30%" in new_zone or "≤-30" in new_zone:
            add("ma_日线≤-30%", ma_val)
        elif "-20%" in new_zone or "≤-20" in new_zone:
            add("ma_日线≤-20%", ma_val)

    signals = [(k, w, label) for k, (w, label) in found.items()]
    return {
        "score": sum(w for _, w, _ in signals),
        "signals": signals,
    }


# ============================================================
# 回测
# ============================================================

def backtest_signal(rows: list[dict],
                    signal_fn: Callable[[dict], bool],
                    direction: str = "top",
                    forward_days: int = 10,
                    threshold_pct: float = 3.0) -> dict:
    """信号回测：精准率 / 召回率 / 假阳分析。

    Args:
        rows:          compute_factor_history(step=1) 输出
        signal_fn:     row -> bool，判断是否触发信号
        direction:     "top"（做空，后续跌算对）/ "bot"（做多，后续涨算对）
        forward_days:  后验窗口（默认 10 天）
        threshold_pct: 认定"方向正确"的涨跌幅阈值（默认 3%）

    Returns:
        {tp, fp, fn, precision, recall, total_days, triggered}
    """
    tp, fp, fn = [], [], []
    n = len(rows)

    for i, row in enumerate(rows):
        if i + forward_days >= n:
            break

        fwd_pct   = (rows[i + forward_days]["close"] / row["close"] - 1) * 100
        triggered = signal_fn(row)

        if direction == "top":
            actual_correct = fwd_pct < -threshold_pct
        else:
            actual_correct = fwd_pct > threshold_pct

        entry = {**row, "forward_pct": round(fwd_pct, 2)}

        if triggered and actual_correct:
            tp.append({**entry, "result": "TP"})
        elif triggered and not actual_correct:
            fp.append({**entry, "result": "FP"})
        elif not triggered and actual_correct:
            fn.append({**entry, "result": "FN"})

    total_triggered = len(tp) + len(fp)
    precision = len(tp) / total_triggered        if total_triggered    else 0.0
    recall    = len(tp) / (len(tp) + len(fn))    if (tp or fn)         else 0.0

    return {
        "tp":        tp,
        "fp":        fp,
        "fn":        fn,
        "precision": round(precision, 3),
        "recall":    round(recall, 3),
        "total_days":  n,
        "triggered":   total_triggered,
    }


def analyze_fp(fp_cases: list[dict]) -> dict:
    """分析假阳性的共同特征，找出信号失效条件"""
    if not fp_cases:
        return {}

    from collections import Counter

    avg_fwd  = sum(r["forward_pct"] for r in fp_cases) / len(fp_cases)
    stages   = Counter(r["wyckoff_daily"] for r in fp_cases)
    scenes   = Counter(r["scene"]         for r in fp_cases)
    hub_pos  = Counter((r["hub_daily"] or {}).get("pos", "—") for r in fp_cases)
    resonance= Counter(r["resonance"]     for r in fp_cases)

    return {
        "count":     len(fp_cases),
        "avg_fwd":   round(avg_fwd, 2),
        "wyckoff":   stages.most_common(),
        "scene":     scenes.most_common(),
        "hub_pos":   hub_pos.most_common(),
        "resonance": resonance.most_common(),
    }
