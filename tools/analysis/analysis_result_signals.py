"""
analysis_result_signals.py - 历史因子计算 + 信号 diff

核心函数:
  compute_factor_history(ctx, step, lookback) → list[dict]
  diff_rows(prev, curr)                       → dict  变化摘要

render 和回测都调 compute_factor_history，保证一致性。

2026-10-30 删: score_top_signals / score_bottom_signals / backtest_signal / analyze_fp
  (旧的加权信号打分, 决策已走 factor_matrix 的 4 类等权投票)
"""
from __future__ import annotations
from typing import Callable


def compute_factor_history(ctx, step: int = 1, lookback: int = 60,
                           strategies=None, history=None) -> list[dict]:
    """把 AnalysisResult 列表转成 flat dict 列表 (拼装, 不调 analyze_history)

    Args:
        ctx:         RawContext，含完整 K 线
        step:        采样间隔（render 用 5，回测用 1）
        lookback:    回看天数（默认 60 = 3个月）
        strategies:  保留参数 (兼容旧调用, 不再使用)
        history:     必填, dict[date, AnalysisResult], 调用方预算好传进来
                     (不传会 raise, 强制避免重跑 analyze_history)

    Returns:
        list[dict]，按日期升序
    """
    if history is None:
        raise ValueError(
            "compute_factor_history 必须传 history (调用方先 engine.analyze_history 算好)"
        )

    kline = ctx.kline
    start = max(0, len(kline) - lookback)
    dates = [kline[i]["trade_date"] for i in range(start, len(kline), step)]

    # 预建索引，避免 for-loop 里 O(n) 查找
    import bisect
    date_to_ki    = {k["trade_date"].replace("-","")[:8]: i for i, k in enumerate(kline)}
    date_to_close = {k["trade_date"].replace("-","")[:8]: k["close"] for k in kline}
    weekly_dates_sorted = [b["trade_date"].replace("-","")[:8] for b in (ctx.weekly or [])]

    # 2026-10-12 加: 批量读 daily_basic (含 pe/pe_ttm pb) 注入每行
    # 1 次 SQL 拿 N 天数据, 0 网络 (走 DataStore 落盘 parquet)
    # FinanceStrategy 只算季报, 但用户要看"每日 PE" 走势
    from tools.storage.store import batch_load_daily_basic
    daily_basic_map = batch_load_daily_basic(
        ctx.code,
        [d.replace("-", "")[:8] for d in dates],
    )

    # 2026-10-08 删: cyq_perf (胜率/中位成本, 跟 RSI/MA 同步, 没新信息)

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
        ma5     = d_arrs.get('ma5',   [None] * (ki+2))
        ma20    = d_arrs.get('ma20',  [None] * (ki+2))
        ma60    = d_arrs.get('ma60',  [None] * (ki+2))
        ma120   = d_arrs.get('ma120', [None] * (ki+2))
        boll_upper = d_arrs.get('boll_upper', [None] * (ki+2))
        boll_lower = d_arrs.get('boll_lower', [None] * (ki+2))
        boll_mid   = d_arrs.get('boll_mid',   [None] * (ki+2))
        boll_pct   = d_arrs.get('boll_pct',   [None] * (ki+2))
        boll_width = d_arrs.get('boll_width',  [None] * (ki+2))

        def _dev(price, ma):
            return round((price / ma - 1) * 100, 1) if ma and ma > 0 else None

        # v6.2.8 加: MA5/MA20/MA60 偏离 (cell 显示用 3 个分数)
        ma5_v  = ma5[ki]  if ki < len(ma5)  else None
        ma20_v = ma20[ki] if ki < len(ma20) else None
        ma60_v = ma60[ki] if ki < len(ma60) else None
        ma5_dev  = _dev(close, ma5_v)
        # 2026-10-27: 原先读 obv_raw.get("ma20_dev") (藏在 ObvStrategy 输出里),
        #   OBV 删除后直接用 _dev(close, ma20_v) — 数值完全一致 (都是 round((c/ma-1)*100, 1))
        ma20_dev_v = _dev(close, ma20_v)
        ma60_dev  = _dev(close, ma60_v)

        ma_devs = {
            "ma_dev_daily":  ma20_dev_v,    # v6.2.8 改: ma20 日偏离 (不是别的)
            "ma5_dev":       ma5_dev,
            "ma60_dev":      ma60_dev,
            "ma120_dev":     _dev(close, ma120[ki] if ki < len(ma120) else None),
            "boll_upper":    boll_upper[ki] if ki < len(boll_upper) else None,
            "boll_lower":    boll_lower[ki] if ki < len(boll_lower) else None,
            "boll_mid":      boll_mid[ki]   if ki < len(boll_mid)   else None,
            "boll_pct":      boll_pct[ki]   if ki < len(boll_pct)   else None,
            "boll_width":    boll_width[ki] if ki < len(boll_width) else None,
        }

        # 2026-10-08 删: cyq_perf (胜率/中位成本) 字段

        # 2026-10-12 加: 每日 PE / PE_TTM / PB (走 batch_load_daily_basic, 0 网络)
        # 注: stk_factor 数据晚 1 天 (T+1 同步), 但用户想看日度变化
        db_row = daily_basic_map.get(date_clean) or {}
        pe_v = db_row.get("pe")
        pe_ttm_v = db_row.get("pe_ttm")
        pb_v = db_row.get("pb")
        daily_val = {
            "pe":       round(float(pe_v), 2)     if pe_v is not None else None,
            "pe_ttm":   round(float(pe_ttm_v), 2) if pe_ttm_v is not None else None,
            "pb":       round(float(pb_v), 2)     if pb_v is not None else None,
        }

        rows.append({**_extract_row(result, date, close, ctx), **ma_devs, **daily_val})
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

    # post-process: MA20 5日斜率 (per-row, 行内字段 ma20_slope)
    # 斜率 = (MA20[i] - MA20[i-5]) / MA20[i-5] / 5 * 100
    # MA20 通过 close / (1 + ma_dev_daily/100) 反算 (跟 render 端同算法)
    # 不足 5 天的行 (含首 5 行) 写 None
    for i, row in enumerate(rows):
        if i < 5:
            row['ma20_slope'] = None
            continue
        c0  = row.get('close')
        md0 = row.get('ma_dev_daily')
        c5  = rows[i - 5].get('close')
        md5 = rows[i - 5].get('ma_dev_daily')
        if not (c0 and md0 is not None and c5 and md5 is not None and c5 > 0 and md5 != 0):
            row['ma20_slope'] = None
            continue
        try:
            ma0 = c0 / (1 + md0 / 100)
            ma5 = c5 / (1 + md5 / 100)
            row['ma20_slope'] = (ma0 - ma5) / ma5 / 5 * 100 if ma5 > 0 else None
        except (ZeroDivisionError, TypeError):
            row['ma20_slope'] = None

    return rows


def _extract_row(result, date: str, close: float, ctx=None) -> dict:
    """从 AnalysisResult 提取单行因子快照"""
    raw       = result.raw
    chan_raw   = raw.get("chan",             {}) or {}
    bsp_raw    = raw.get("buy_sell_points", {}) or {}
    wy_raw     = raw.get("wyckoff",         {}) or {}
    smc_raw    = raw.get("smc",             {}) or {}
    val_raw    = raw.get("finance",           {}) or {}  # 2026-09-08 改: ValuationStrategy → FinanceStrategy
    tech_raw   = raw.get("technical",        {}) or {}  # v6.2.8 改: TechnicalStrategy 8 指标
    p3         = wy_raw.get("3period") or {}

    pos_raw = raw.get("position", {}) or {}

    return {
        # 基础
        "date":       date,
        "close":      close,

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

        # 估值 (FinanceStrategy 季报粒度, 共享最新季值)
        # v6.2.8 删: 历史表里不再有 ROC%/EY% 列 (跨季才变, 跟"## 财务数据" section 重复)

        # Technical 8 指标 (v6.2.8 加: TechnicalStrategy 时序)
        # 从 tech_raw 抽 per-date 字段 (TechnicalStrategy.analyze_history 算出)
        "macd_dif":   tech_raw.get("macd_dif"),
        "macd_dea":   tech_raw.get("macd_dea"),
        "macd_bar":   tech_raw.get("macd_bar"),       # v6.2.x 加: MACD 柱
        "macd_bar_delta": tech_raw.get("macd_bar_delta"),  # v6.2.x 加: 柱变化
        "rsi6":       tech_raw.get("rsi6"),
        "rsi12":      tech_raw.get("rsi12"),          # 2026-09-17 加: 中期 RSI
        "kdj_k":      tech_raw.get("kdj_k"),
        "kdj_d":      tech_raw.get("kdj_d"),     # 2026-10-14 加: KDJ D 值 (金叉死叉用)
        "kdj_j":      tech_raw.get("kdj_j"),     # 2026-10-14 加: KDJ J 值 (超买超卖)
        "atr_pct":    tech_raw.get("atr_pct"),
        "vol_ratio":  tech_raw.get("vol_ratio"),
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


def diff_rows(prev: dict, curr: dict) -> dict:
    """对比相邻两行，返回变化摘要。

    Returns:
        dict，只包含有变化的字段，如:
          {"wyckoff_daily": "Accumulation→Markup",
           "new_bsp_daily": {"🔴2卖": "¥258.02 接近"}}
    """
    changes = {}

    # 标量字段: 直接比
    for field in ("wyckoff_daily", "wyckoff_weekly"):
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


def _bsp_direction(label: str):
    clean = label.split("(")[0].strip()
    for k in _BSP_BUY:
        if k in clean: return "buy"
    for k in _BSP_SELL:
        if k in clean: return "sell"
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
                 wyckoff_stage_change /
                 hub_pos_change / ma_dev_cross
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

    # 威科夫阶段转变 (2026-11-03 加)
    #   A→M (Accumulation→Markup) = 主升浪启动 = 买入信号
    #   M→D (Markup→Distribution) = 派发开始 = 卖出信号
    #   其他转变 (A→D / D→A / 进出 ?) 不算方向信号, 只在渲染层显示
    # 改动前: 这里什么都不加, A→M 只在 format_signals_for_render 里显示成
    #   被动文字 "🔄wydaily:A→M" → 进不了 buy/sell 汇总表, 用户看不到
    for field in ("wyckoff_daily", "wyckoff_weekly"):
        raw = changes.get(field)
        if not raw or "→" not in raw:
            continue
        old, new = raw.split("→", 1)
        old, new = old.strip(), new.strip()
        period = field.split("_")[1]
        if old == "Accumulation" and new == "Markup":
            signals.append(("wyckoff_stage_change", f"A→M主升启动({period})", "buy"))
        elif old == "Markup" and new == "Distribution":
            signals.append(("wyckoff_stage_change", f"M→D派发开始({period})", "sell"))

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
        elif sig_type == "wyckoff_stage_change":
            parts.append(f"🔄{detail}")
        elif sig_type == "hub_pos_change":
            parts.append(f"🔄中枢{detail}")
        elif sig_type == "ma_dev_cross":
            parts.append(f"📊{detail}")
    # wyckoff stage 变化
    # 2026-11-03: A→M / M→D 已由 extract_signals 单独产出方向信号 (wyckoff_stage_change),
    #   这里跳过它们, 避免同一转变在"变化"列出现两次。
    #   其余转变 (A→D / D→A / 进出 ?) 仍走这里显示。
    for field in ("wyckoff_daily", "wyckoff_weekly"):
        if field not in changes:
            continue
        raw = changes[field]
        if "→" in raw:
            old, new = (x.strip() for x in raw.split("→", 1))
            if (old == "Accumulation" and new == "Markup") or \
               (old == "Markup" and new == "Distribution"):
                continue  # 已作为方向信号输出
        v = raw
        # 2026-09-15 缩写: Accumulation=A / Markup=M / Distribution=D / TradingRange=TR
        v = (v.replace("Accumulation", "A")
              .replace("Markup", "M")
              .replace("Distribution", "D")
              .replace("TradingRange", "TR"))
        parts.append(f"🔄wy{field.split('_')[1]}:{v}")
    return parts


