"""
analysis_engine.py — analysis 层 (v5.10.35, 2026-08-26)

架构 (3 层):
- L1 dump 层: DataStore.get_ctx(code)
- L2 analysis 层 (本文件): AnalysisEngine.analyze(ctx) → AnalysisResult
- L3 render 层: render_report(analysis_data) → markdown

策略模式 (v5.10.35 简化):
- 3 个 Phase1 策略类: 返回 dict (含 score + 因子数据), 不再继承 AnalysisStrategy ABC
  (wyckoff / technical / finance)
- 2026-11-05 删: StructureStrategy (原 SMC 结构分析 OB/FVG/Sweep)
  → 报告层早已不消费, 只算不用; 因子库 tools/factors/structure/ 整目录删除
- 1 个 Phase2 派生函数 (模块级): 纯数据提取, weight=0
- AnalysisResult.raw: Dict[str, Any] 是唯一数据来源
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


# ============================================================
# 1. 数据类
# ============================================================

@dataclass
class RawContext:
    """原始数据容器 — AnalysisEngine 的唯一输入

    取代之前的 dump dict，Strategy 只从这里读数据，不读 dump['factor']。
    切片逻辑统一在 Engine 层通过 slice(as_of_date) 完成，Strategy 无需感知时间。
    """
    kline:      list        # 日线 K 线 [{date, open, close, high, low, vol, ...}]
    weekly:     list        # 周线 K 线（由日线合成，_synthesize_weekly，不额外联网）
    # 2026-11-04 删: eps_table (EPS 机构一致预期)
    #   EPS 预期是**预测值**, 11-02 删 PEG/DCF 后已无任何分析消费者。
    #   估值口径固化: 只看 PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆), 全部用已审计 TTM 财报算。
    #   注: 财报里的真实 EPS 仍在 financials parquet 里 (analysis_engine 算 np_yi 在用), 跟这里无关。
    # 2026-10-28 删: fflow (主力资金流) / moneyflow (Tushare money_flow 原始列表)
    #   实测 27 只核心票: 删掉后判定更准 (剔掉 2 只假建仓 + 1 只假减仓)
    #   详见 docs/fflow-removal-assessment.md
    # 2026-10-08 删: cyq (胜率/中位成本, 跟 RSI/MA 同步, 没新信息)
    current_price:  float = 0.0
    market_cap_yi:  float = 0.0
    industry: str = ""
    code: str = ""
    name: str = ""

    # Phase1 Strategy 算完后写这里，Phase2 函数直接读，不重算
    wyckoff_result: dict = field(default_factory=dict)   # 日线
    wyckoff_weekly: dict = field(default_factory=dict)
    technical_result: dict = field(default_factory=dict)  # 8 个技术指标 (v6.2.8 新增, 含 series)
    # 2026-11-05 删 resonance_result: resonance_3period 网络调用早已删除, 该字段从未被写入/读取
    kline_arrs:         dict = field(default_factory=dict)   # build_kline_features 预算结果，WyckoffStrategy 算完后写
    kline_arrs_weekly:  dict = field(default_factory=dict)   # 周线 arrs，WyckoffStrategy 写，factor_history 读 ma_dev_weekly

    def slice(self, as_of_date: str) -> "RawContext":
        """按日期截断 K 线，返回新 RawContext，原对象不变"""
        # 统一转成 YYYYMMDD 格式比较，避免 '2026-07-30' vs '20260601' 格式不一致
        as_of_clean = as_of_date.replace("-", "")[:8]

        def date_clean(b, key="trade_date"):
            return b.get(key, "").replace("-", "")[:8]

        k   = [b for b in self.kline  if date_clean(b) <= as_of_clean]
        w   = [b for b in self.weekly if date_clean(b) <= as_of_clean]

        # 切片正确性验证
        assert not k or date_clean(k[-1]) <= as_of_clean, \
            f"日线切片错误: 最后一根 {date_clean(k[-1])} > {as_of_clean}"
        assert not w or date_clean(w[-1]) <= as_of_clean, \
            f"周线切片错误: 最后一根 {date_clean(w[-1])} > {as_of_clean}"

        price = k[-1]["close"] if k else self.current_price
        sliced = RawContext(
            kline=k, weekly=w,
            current_price=price,
            market_cap_yi=self.market_cap_yi,
            code=self.code,
            name=self.name,
        )
        # 透传预计算缓存（factor_history 优化：全量信号预算一次，切片节点直接查表）
        for _attr in ('_precomputed_signals',):
            if hasattr(self, _attr):
                setattr(sliced, _attr, getattr(self, _attr))
        return sliced


@dataclass
class AnalysisResult:
    """1 个 dump 的完整 analysis 结果 (L2 → L3)

    raw 是唯一数据来源:
      raw["wyckoff"]  = {"score": ..., "stage": ..., "sub_events": ..., "3period": ...}
      raw["finance"]   = {"quarterly": [...], "roc": ..., "ey": ..., ...}
      raw["position"]  = {"close_pos_day": ..., ...}
      # Phase2 派生 (weight=0)
      # 2026-11-02 删: raw["peg"] / raw["dcf"] (PEG/DCF 整档已删)
      raw["sector_overheat"]     = {1周涨幅: ..., ...}
      # 2026-11-03 删: raw["exit_signals"] (退场信号检查整档已删 —
      #   与 监控触发点 重叠)
      # 2026-11-03 删: raw["stop_profit_loss"] (止盈 3 层 + 止损 4 档整档已删)
      # 2026-11-03 删: raw["three_layer_position"] (3 层仓位策略整档已删 —
      #   与 监控触发点 重叠; 止损/目标阶梯是当前价 × 固定百分比的机械外推)
      # 2026-11-03 删: raw["monitor_triggers"] (监控触发点整档已删 —
      #   时间触发恒为空(events=[]/code=""))
    """
    code: str
    name: str
    current_price: float
    raw: Dict[str, Any] = field(default_factory=dict)  # 唯一数据来源
    signals_active: list[str] = field(default_factory=list)
    action: str = ""

    def to_dict(self, ctx: "RawContext | None" = None) -> dict:
        """序列化为 dict (给 RenderData / render 用)

        以 raw 为基础，顶层补充 code/name/scene 等元字段。
        """
        d = dict(self.raw)
        d.update({
            "code":            self.code,
            "name":            self.name,
            "current_price":   self.current_price,
            "signals_active":  self.signals_active,
            "action":          self.action,
        })
        return d


# ============================================================
# 2. Phase1 策略类 (返回 dict, 含 score + 因子数据)
# ============================================================

class WyckoffStrategy:
    """威科夫 3 阶段 → 分数，从 ctx.kline 直接重算"""
    name = "wyckoff"

    def analyze(self, ctx: RawContext) -> dict:
        """Phase1 威科夫 (跟 analyze_history 同源: 走 wyckoff_judge + arrs 预算)

        2026-10-16 重构: 删 WyckoffStageFactor().compute() + _judge_full 慢路径,
        跟 analyze_history 共享 wyckoff_judge (3800-10000x 加速, 实测 /tmp/speed_judges.py).
        """
        from tools.factors.wyckoff.stage_factor import wyckoff_judge
        from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events as _scan
        from tools.factors.kline_arrays import build_kline_features as _build_kline_features

        score_map = {
            "Accumulation": 0.6,
            "Markup": 1.0,
            "Distribution": -0.6,
            "Markdown": 1.0,
            "?": 0.0,
        }

        sub_events_by_period = {}
        wyckoff_3period = {}
        _pre = getattr(ctx, 'cached_sub_events', {})

        for level, bars, label in [
            ("daily",  ctx.kline,  "daily"),
            ("weekly", ctx.weekly, "weekly"),
        ]:
            if not bars or len(bars) < 30:
                continue

            closes = [k["close"] for k in bars]
            highs  = [k["high"]  for k in bars]
            lows   = [k["low"]   for k in bars]
            vols   = [k.get("volume", 0) for k in bars]
            opens  = [k.get("open", k["close"]) for k in bars]
            n = len(closes)
            window = min(250, n)
            i_last = n - 1

            # 预算 arrs (O(n), 跟 analyze_history 一样)
            arrs = _build_kline_features(closes, highs, lows, vols, window=window)

            # sub_events 用 ctx 缓存或现扫 (缓存路径来自 analyze_history)
            sub_raw = _pre.get(level)
            if sub_raw is None:
                sub_raw = _scan(
                    closes, highs, lows, vols, None,
                    o=opens, pct_chg=[k.get("pct_chg", 0) for k in bars],
                    period_label=label, code=ctx.code, as_of_idx=i_last,
                    dates=[k["trade_date"].replace("-", "")[:8] for k in bars],
                )
            sub_set = {e["name"] for e in sub_raw}

            out = wyckoff_judge(i_last, arrs, sub_set,
                                window=arrs['window'], period_label=label)
            if out is None:
                continue

            # 与 compute() 对齐: sub_events 字段用 list[dict] (含 date/idx)
            out["sub_events"] = sub_raw
            # sub_event_count 已经被 wyckoff_judge 设过, 但 _judge_full 用 len(sub_events) 是 list 形式 — 字段保持一致
            # trend_30 / verdict 补齐
            c = closes[-window:]
            out["趋势_30日_pct"] = round((c[-1] / c[-31] - 1) * 100, 1) if len(c) > 30 else None
            out["verdict"] = f"{out['stage']} ({out['stage_name']}, {out['confidence']}%)"
            # sub_event_count 用 list 长度
            out["sub_event_count"] = len(sub_raw)

            sub_events_by_period[level] = sub_raw
            wyckoff_3period[level] = out

        # 主阶段用日线
        daily_out = wyckoff_3period.get("daily", {})
        stage = daily_out.get("stage", "?")
        score = score_map.get(stage, 0.0)

        signals = []
        for level, evs in sub_events_by_period.items():
            if evs:
                last = evs[-1]
                signals.append(f"威科夫 {level} 最近: {last['name']} {last.get('date', '?')}")

        # 写回 ctx 供 Phase2 函数使用
        ctx.wyckoff_result = wyckoff_3period.get("daily", {})
        ctx.wyckoff_weekly = wyckoff_3period.get("weekly", {})

        return {
            "score":   score,
            "signals": signals,
            "summary": f"威科夫 {stage}",
            **daily_out,
            "sub_events_by_period": sub_events_by_period,
            "3period": wyckoff_3period,
        }

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """O(n) 批量威科夫：kline_arrays 预算一次，wyckoff_judge O(1) per date。"""
        import bisect
        from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events as _scan
        from tools.factors.wyckoff.stage_factor import wyckoff_judge
        from tools.factors.kline_arrays import build_kline_features as _build_kline_features

        score_map = {"Accumulation": 0.6, "Markup": 1.0, "Distribution": -0.6, "?": 0.0}

        def _pre_scan(bars, period_label, arrs=None):
            if not bars or len(bars) < 30:
                return []
            return _scan(
                [k["close"] for k in bars],           [k["high"] for k in bars],
                [k["low"] for k in bars],              [k.get("volume", 0) for k in bars],
                None,
                o=[k.get("open", k["close"]) for k in bars],
                pct_chg=[k.get("pct_chg", 0) for k in bars],
                dates=[k["trade_date"].replace("-", "")[:8] for k in bars],
                period_label=period_label, code=ctx.code,
                precomputed_arrs=arrs,
            )

        # ── 一次性预算：kline_arrays 先算，再 pre_scan 传入 ─────────────────
        def _arrs_for(bars):
            if not bars or len(bars) < 30:
                return None
            closes = [k["close"] for k in bars]
            highs  = [k["high"]  for k in bars]
            lows   = [k["low"]   for k in bars]
            vols   = [k.get("volume", 0) for k in bars]
            return _build_kline_features(closes, highs, lows, vols, window=min(250, len(bars)))

        arrs_daily  = _arrs_for(ctx.kline)
        arrs_weekly = _arrs_for(ctx.weekly) if ctx.weekly else None
        # 写入 ctx, 避免下游重复计算 MA20/MA60
        ctx.kline_arrs        = arrs_daily  or {}
        ctx.kline_arrs_weekly = arrs_weekly or {}

        all_events = {
            "daily":  _pre_scan(ctx.kline,  "daily",  arrs_daily),
            "weekly": _pre_scan(ctx.weekly, "weekly", arrs_weekly) if ctx.weekly else [],
        }

        daily_dates  = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
        weekly_dates = [k["trade_date"].replace("-", "")[:8] for k in (ctx.weekly or [])]
        daily_idx    = {d: i for i, d in enumerate(daily_dates)}

        # ── 逐 date O(1) 判定 ─────────────────────────────────────────────
        results = {}
        for date in dates:
            date_clean = date.replace("-", "")[:8]
            d_idx = daily_idx.get(date_clean, -1)
            w_idx = bisect.bisect_right(weekly_dates, date_clean) - 1

            wyckoff_3period: dict = {}
            sub_events_by_period: dict = {}

            for level, arrs, abs_idx, label in (
                ("daily",  arrs_daily,  d_idx, "daily"),
                ("weekly", arrs_weekly, w_idx, "weekly"),
            ):
                if arrs is None or abs_idx < 1:
                    continue
                evs_raw = [e for e in all_events[level] if e["idx"] <= abs_idx]
                sub_set = {e["name"] for e in evs_raw}
                window  = arrs["window"]
                out = wyckoff_judge(abs_idx, arrs, sub_set, window=window,
                                    period_label=label)
                if out is None:
                    continue
                # attach full event list (render 层需要 date/idx 字段)
                out["sub_events"] = evs_raw
                out["sub_event_count"] = len(evs_raw)
                sub_events_by_period[level] = evs_raw
                wyckoff_3period[level] = out

            daily_out = wyckoff_3period.get("daily", {})
            stage = daily_out.get("stage", "?")
            score = score_map.get(stage, 0.0)

            signals = []
            for level, evs in sub_events_by_period.items():
                if evs:
                    last = evs[-1]
                    signals.append(f"威科夫 {level} 最近: {last['name']} {last.get('date', '?')}")

            results[date_clean] = {
                "score":   score,
                "signals": signals,
                "summary": f"威科夫 {stage}",
                **daily_out,
                "sub_events_by_period": sub_events_by_period,
                "3period": wyckoff_3period,
            }
        return results


class TechnicalStrategy:
    """8 个技术指标 (MACD/RSI/KDJ/BOLL/ATR/量比) → 分数，从 ctx.kline 直接重算 (v6.2.8 新增)

    "per-date O(1) 查信号" 模式:
    - analyze: 算全套 8 指标最新值, 跟原 compute_indicators 行为一致
    - analyze_history: 全 series 预算一次, per date O(1) 查信号

    来源: compute_indicators 已经预算完整 series (dif_series/rsi6_series/k_series/
          mid_series/upper_series/lower_series/pct_series/atr14_series/atr_pct_series/
          vol_ratio_series/vol_ma5_series), 这里把 series 对齐到 dates.

    weight=0.10, 跟结构持平.
    """
    name = "technical"

    def analyze(self, ctx: RawContext) -> dict:
        from tools.storage.sources.eastmoney import compute_indicators
        try:
            kline = ctx.kline or []
            result = compute_indicators(kline) or {}
        except Exception as e:
            return {"score": 0, "signals": [], "summary": "技术指标失败", "verdict": "—"}

        # 把完整 series 挂到 result (供 render_data 复用, 避免 render 端再算)
        ctx.technical_result = result

        # 提取关键信号 (单点, 跟因子历史表显示口径一致)
        signals = []
        if "macd" in result and "verdict" in result["macd"]:
            signals.append(result["macd"]["verdict"])
        if "rsi" in result and "verdict" in result["rsi"]:
            signals.append(result["rsi"]["verdict"])
        if "kdj" in result and "verdict" in result["kdj"]:
            signals.append(result["kdj"]["verdict"])
        if "boll" in result and "verdict" in result["boll"]:
            signals.append(result["boll"]["verdict"])
        if "atr" in result and "verdict" in result["atr"]:
            signals.append(result["atr"]["verdict"])
        if "vol_ma" in result and "verdict" in result["vol_ma"]:
            signals.append(result["vol_ma"]["verdict"])

        # 评分规则 (跟原 compute_indicators summary 一致):
        # bullish: 3+ 偏多 (DIF>DEA 金叉 / RSI 偏低 / KDJ 金叉 / BOLL 偏多 / 缩量)
        # bearish: 3+ 偏空 (DIF<DEA 死叉 / RSI 超买 / KDJ 死叉 / BOLL 偏空 / 放量)
        bullish_keywords = ["金叉多头", "DIF>DEA 但<0 金叉弱势", "🟢", "缩量", "偏低", "超卖", "反弹", "J<0", "低位金叉", "中轨上方"]
        bearish_keywords = ["死叉空头", "DIF<DEA 但>0 死叉强势", "🔴", "超买", "放量", "J>100", "高位死叉", "突破上轨", "中轨下方"]
        bullish = sum(1 for s in signals if any(k in s for k in bullish_keywords))
        bearish = sum(1 for s in signals if any(k in s for k in bearish_keywords))

        score = 0
        if bullish >= 3 and bearish <= 1:
            score = 2
        elif bearish >= 3 and bullish <= 1:
            score = -2
        elif bullish > bearish:
            score = 1
        elif bearish > bullish:
            score = -1

        return {
            "score":   float(score),
            "signals": signals,
            "summary": result.get("summary", "—"),
            "verdict": result.get("summary", "—"),
            **result,  # 透传 macd/rsi/kdj/boll/atr/vol_ma (含 series)
        }

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """per-date O(1) 查信号: 复用 analyze 已算的 result (含 series), 按 dates 取对应 index."""
        kline = ctx.kline or []
        if len(kline) < 20:
            return {d.replace("-","")[:8]: {"score":0,"signals":[],"summary":"技术指标 K线不足",
                                            "verdict":"—","source":"technical (K线不足)"}
                    for d in dates}

        # 单点算全套 (含 series) — analyze 内部已挂到 ctx.technical_result
        result = self.analyze(ctx)

        # dates → index 映射 (按 trade_date)
        dates_list = [k.get("trade_date","").replace("-","")[:8] for k in kline]
        date_to_idx = {d: i for i, d in enumerate(dates_list)}

        # 取 series 引用
        macd_dif = result.get("macd", {}).get("dif_series", [])
        macd_dea = result.get("macd", {}).get("dea_series", [])
        # v6.2.x 加: MACD 柱 + 柱变化 (板块合成 K线 / 个股 BARΔ 共用)
        macd_bar_series = []
        if macd_dif and macd_dea:
            for _i in range(len(macd_dif)):
                if _i < len(macd_dea) and macd_dif[_i] is not None and macd_dea[_i] is not None:
                    macd_bar_series.append(round((macd_dif[_i] - macd_dea[_i]) * 2, 4))
                else:
                    macd_bar_series.append(None)
        else:
            macd_bar_series = [None] * (len(macd_dif) if macd_dif else 0)
        # BARΔ = BAR[i] - BAR[i-1]
        macd_bar_delta_series = [None]
        for _i in range(1, len(macd_bar_series)):
            if macd_bar_series[_i] is not None and macd_bar_series[_i-1] is not None:
                macd_bar_delta_series.append(round(macd_bar_series[_i] - macd_bar_series[_i-1], 4))
            else:
                macd_bar_delta_series.append(None)
        rsi6     = result.get("rsi",  {}).get("rsi6_series", [])
        rsi12    = result.get("rsi",  {}).get("rsi12_series", [])
        kdj_k    = result.get("kdj",  {}).get("k_series", [])
        kdj_d    = result.get("kdj",  {}).get("d_series", [])
        kdj_j    = result.get("kdj",  {}).get("j_series", [])
        boll_mid = result.get("boll", {}).get("mid_series", [])
        boll_pct = result.get("boll", {}).get("pct_series", [])
        atr14    = result.get("atr",  {}).get("atr14_series", [])
        atr_pct  = result.get("atr",  {}).get("atr_pct_series", [])
        vol_r    = result.get("vol_ma", {}).get("vol_ratio_series", [])

        dates_clean = [d.replace("-","")[:8] for d in dates]
        results = {}
        for dc in dates_clean:
            i = date_to_idx.get(dc, -1)
            if i < 0:
                results[dc] = {"score":0,"signals":[],"summary":"—","verdict":"—","source":"technical (无日期)"}
                continue

            # 取该日各 series 值 (None 视为 0/中性)
            dif_v = macd_dif[i] if i < len(macd_dif) else None
            dea_v = macd_dea[i] if i < len(macd_dea) else None
            r6_v  = rsi6[i]     if i < len(rsi6)     else None
            r12_v = rsi12[i]    if i < len(rsi12)    else None
            kk_v  = kdj_k[i]    if i < len(kdj_k)    else None
            kd_v  = kdj_d[i]    if i < len(kdj_d)    else None
            kj_v  = kdj_j[i]    if i < len(kdj_j)    else None
            bm_v  = boll_mid[i] if i < len(boll_mid) else None
            bp_v  = boll_pct[i] if i < len(boll_pct) else None
            at_v  = atr14[i]    if i < len(atr14)    else None
            ap_v  = atr_pct[i]  if i < len(atr_pct)  else None
            vr_v  = vol_r[i]    if i < len(vol_r)    else None

            signals = []; score = 0

            # MACD: DIF/DEA 金叉死叉
            if dif_v is not None and dea_v is not None:
                if dif_v > dea_v and dif_v > 0:
                    signals.append("MACD金叉多头"); score += 1
                elif dif_v > dea_v and dif_v < 0:
                    signals.append("MACD弱势金叉"); score += 0
                elif dif_v < dea_v and dif_v < 0:
                    signals.append("MACD死叉空头"); score -= 1
                else:
                    signals.append("MACD强势死叉"); score -= 1

            # RSI: 超买超卖
            if r6_v is not None:
                if r6_v > 80:
                    signals.append(f"RSI6超买({r6_v:.0f})"); score -= 1
                elif r6_v < 20:
                    signals.append(f"RSI6超卖({r6_v:.0f})反弹机会"); score += 1
                elif r6_v > 70:
                    signals.append(f"RSI6偏高({r6_v:.0f})")
                elif r6_v < 30:
                    signals.append(f"RSI6偏低({r6_v:.0f})")

            # KDJ: K/D 金叉死叉
            if kk_v is not None and kd_v is not None:
                if kj_v is not None and kj_v < 0:
                    signals.append(f"KDJ J<0超卖(K={kk_v:.0f})"); score += 1
                elif kj_v is not None and kj_v > 100:
                    signals.append(f"KDJ J>100超买(K={kk_v:.0f})"); score -= 1
                elif kk_v > kd_v and kk_v < 30:
                    signals.append("KDJ低位金叉"); score += 1
                elif kk_v < kd_v and kk_v > 70:
                    signals.append("KDJ高位死叉"); score -= 1

            # BOLL: pct_series 位置 (0=下轨, 1=上轨)
            if bp_v is not None:
                if bp_v > 1.0:
                    signals.append("BOLL突破上轨")
                elif bp_v < 0.0:
                    signals.append("BOLL跌破下轨")
                elif bp_v > 0.5:
                    signals.append("BOLL中轨上方偏多")
                else:
                    signals.append("BOLL中轨下方偏空")

            # ATR: 波动率
            if ap_v is not None:
                if ap_v > 5:
                    signals.append(f"ATR高波动({ap_v:.1f}%)")
                elif ap_v > 3:
                    signals.append(f"ATR中波动({ap_v:.1f}%)")
                else:
                    signals.append(f"ATR低波动({ap_v:.1f}%)")

            # 量比
            if vr_v is not None:
                if vr_v > 2:
                    signals.append(f"放量({vr_v:.2f}x)关注"); score -= 1
                elif vr_v > 1.2:
                    signals.append(f"温和放量({vr_v:.2f}x)")
                elif vr_v < 0.7:
                    signals.append(f"缩量({vr_v:.2f}x)"); score += 1
                else:
                    signals.append(f"量比正常({vr_v:.2f}x)")

            if score >= 2:    verdict = "🟢技术偏多"
            elif score >= 1:  verdict = "🟡偏多"
            elif score == 0:  verdict = "⬜中性"
            elif score >= -1: verdict = "🟠偏空"
            else:             verdict = "🔴技术偏空"

            results[dc] = {
                "score":   score,
                "signals": signals,
                "summary": verdict,
                "verdict": verdict,
                "macd_dif": dif_v,
                "macd_dea": dea_v,
                "macd_bar": macd_bar_series[i] if i < len(macd_bar_series) else None,
                "macd_bar_delta": macd_bar_delta_series[i] if i < len(macd_bar_delta_series) else None,
                "rsi6":     r6_v,
                "rsi12":    r12_v,
                "kdj_k":    kk_v,
                "kdj_d":    kd_v,
                "kdj_j":    kj_v,
                "boll_pct": bp_v,
                "atr14":    at_v,
                "atr_pct":  ap_v,
                "vol_ratio":vr_v,
                "source":   "technical (K线)",
            }
        return results


class FinanceStrategy:
    """季度财务 + 估值 factor (合并原 ValuationStrategy, 2026-09-08)

    按季报日各算一次: Magic (ROC/EY/EBIT/EV/capital/netdebt/mc) + 静态 PE。
    2026-11-02 删: PEG + DCF (PEG 是假指标; 估值只看 PE + 盈利质量)。
    analyze_history 所有日期共享同一结果 (季报频率)。
    weight=0, 不参与 total_score。
    """
    name = "finance"

    def analyze_history(self, ctx: "RawContext", dates: list) -> dict:
        result = self.analyze(ctx)
        return {d: result for d in dates}

    def analyze(self, ctx: "RawContext") -> dict:
        from tools.storage.store import DataStore
        from tools.factors.valuation.factor_lib import (
            compute_magic_one_day,
        )
        _empty = {"quarterly": [], "score": 0.0, "signals": [], "summary": ""}

        try:
            financials = DataStore.get_financials(ctx.code, lookback_quarters=4)
        except Exception:
            return _empty
        if not financials:
            return _empty

        market_cap_wan = (ctx.market_cap_yi or 0) * 1e4

        # 2026-09-10 修 4 季大表市值 bug: 每季按 end_date 拿"季末后 30 天内最近 trade_date"的市值
        # 修前: 4 季循环都用 market_cap_wan (今日市值), 4 个 mc_yi 全一样 (bug)
        # 修后: 每季用自己的季末市值, ROC/EY 反映季报披露后真实市场估值
        end_dates = [str(r.get("end_date", ""))[:8] for r in financials[-4:]]
        mc_by_end_date = {}
        if end_dates and any(end_dates):
            try:
                mc_by_end_date = DataStore.get_market_cap_near_dates(ctx.code, end_dates, days_after=30)
            except Exception:
                mc_by_end_date = {}

        shares_wan = 0.0
        # v6.2.8 改: 多重 fallback (daily_basic 返空/stock_basic 拿不到, 用 market_cap_yi/current_price 反推 shares_wan)
        try:
            db = DataStore.get_daily_basic(ctx.code)
            if db and db.get("total_share"):
                shares_wan = float(db["total_share"])
        except Exception:
            pass
        # fallback 1: 从 market_cap_yi / current_price 反推 (daily_basic 返空/dump 缺数据时)
        if shares_wan <= 0 and market_cap_wan > 0 and ctx.current_price > 0:
            shares_wan = market_cap_wan / ctx.current_price  # 万股
        # fallback 2: 从 stock_basic 拿 (最稳的源, 30 天才更新一次)
        if shares_wan <= 0:
            try:
                sb = DataStore.get_stock_basic(ctx.code)
                if sb and sb.get("total_share"):
                    shares_wan = float(sb["total_share"])
            except Exception:
                pass

        # 2026-11-02 删: eps_table + PEG/DCF 计算 (整档已删 — PEG 是假指标)

        quarterly = []
        for r in reversed(financials[-4:]):
            q_str = str(r.get("end_date", ""))

            rev_yi = (float(r.get("total_revenue_ps") or 0) * shares_wan) / 1e4 if shares_wan > 0 else 0.0
            eps_val = float(r.get("eps") or r.get("diluted2_eps") or 0)
            np_yi  = (eps_val * shares_wan) / 1e4 if shares_wan > 0 else 0.0

            # 2026-09-10 修 4 季大表市值 bug: 每季用"季末市值"代替"今日市值"
            # 修前 4 个 mc_yi 全是 4346 亿 (今日) → 修后 4 个不同 (季末)
            q_mc_yi = mc_by_end_date.get(q_str[:8], 0.0) or 0.0
            # 退路: 该季拿不到市值时, 用今日市值 (避免 ROC/EY 完全无数据)
            if q_mc_yi <= 0:
                q_mc_yi = ctx.market_cap_yi or 0.0
            q_market_cap_wan = q_mc_yi * 1e4

            # 2026-09-09 改: 每季单独算 ROC/EY (用该季 EBIT + 当时 NWC+FA + 当时市值)
            # 设计意图: 4 季大表是"周期分析"工具, 每行代表该季的快照
            # 营收/净利 yoy/毛利率/ROE 是该季实际 → EBIT 也必须是该季实际
            # ROC = 该季 EBIT / 该季 NWC+FA (单季 TTM proxy)
            # EY = 该季 EBIT / 当时市值 (单季 TTM proxy, A 股 季报无 LTM EBIT)
            magic = {}
            if q_market_cap_wan > 0:
                try:
                    # 单季 [r] → _ttm_ebit fallback 用该季 EBIT (符合"周期分析"语义)
                    magic = compute_magic_one_day([r], q_str, q_market_cap_wan) or {}
                except Exception:
                    pass

            capital_yi = magic.get("capital_yi")
            if capital_yi is None:
                fa = float(r.get("fixed_assets") or 0)
                capital_yi = round(fa / 1e8, 2) if fa > 0 else 0.0

            # 强制用该季 EBIT (不取 magic 的全年 EBIT), 保持 4 季大表"周期分析"语义
            raw_ebit = r.get("ebit")
            ebit_yi = round(raw_ebit / 1e8, 1) if raw_ebit and raw_ebit > 0 else 0.0
            # 如果 magic 给了更好的 ebit (理论无, 但保底), 用 magic
            if ebit_yi == 0:
                ebit_yi = magic.get("ebit_yi") or 0.0

            # 2026-09-30 改: 静态 PE 用 YTD 倍率年化 (12-31 全年 ×1, 09-30 三季度 ×4/3, 06-30 半年 ×2)
            # 修前错把 Q1/Q2/Q3 当单季 ×4, 高估净利 4 倍 → PE 偏低 4 倍
            #
            # 2026-10-28 修 BUG: end_date 是 YYYYMMDD (无分隔符),
            # 原来用 q_str[5:10] 取 "06-30" 永远取不到 (实际是 "630"),
            # ytd_factor 恒等于 1.0 → 半年报 PE 少乘 2, 全部系统性偏高 1 倍.
            # 实测 22 只 A→M 命中票 PE 全部虚高 (600323 报 18.6, 实际 9.3).
            _md = q_str[4:8]  # YYYYMMDD → MM-DD
            if len(_md) == 4 and _md.isdigit():
                _mmdd = f"{_md[:2]}-{_md[2:]}"
            else:
                _mmdd = q_str[5:10]
            ytd_factor = {"03-31": 4.0, "06-30": 2.0, "09-30": 4.0/3.0, "12-31": 1.0}.get(_mmdd, 1.0)
            annualized_np_yi = np_yi * ytd_factor if np_yi else 0.0

            quarterly.append({
                "quarter":      q_str[:10],
                "or_yoy":       float(r.get("or_yoy") or 0),
                "np_yoy":       float(r.get("netprofit_yoy") or 0),
                "gm":           float(r.get("grossprofit_margin") or 0),
                "roe":          float(r.get("roe") or 0),
                "ebit_yi":      ebit_yi,
                "revenue_yi":   rev_yi,
                "netprofit_yi": np_yi,
                # 2026-09-10 修 4 季大表市值 bug: 用季末市值, 不用今日市值
                "mc_yi":        q_mc_yi,
                "netdebt_yi":   magic.get("netdebt_yi") or 0.0,
                "ev_yi":        round(q_mc_yi + (magic.get("netdebt_yi") or 0.0), 1),
                "capital_yi":   capital_yi,
                "roc":          magic.get("roc"),
                "ey":           magic.get("ey"),
                # 2026-11-02 删: peg / fwd_pe / g / peg_verdict / L_r8 / L_r10 / L_r12 / L_E3_r10
                #   (PEG/DCF 整档已删 — PEG 是假指标; 估值只看 PE + 盈利质量)
                # 2026-09-30 加: 静态 PE (YTD 净利按季报类型年化 / 该季末市值)
                # 公式: PE = mc_yi / (np_yi × ytd_factor), ytd_factor 修正为:
                #   Q1 (03-31) → ×4  Q2 (06-30) → ×2  Q3 (09-30) → ×4/3  Q4 (12-31) → ×1
                "static_pe":    round(q_mc_yi / annualized_np_yi, 1) if (q_mc_yi > 0 and annualized_np_yi > 0) else None,
            })

        # 最新季 Magic（用最后一个财报行重算，确保 period_label 等字段正确）
        latest_magic = {}
        if financials and market_cap_wan > 0:
            try:
                # 2026-09-09 修: 传完整 financials, 让 _ttm_ebit 找 12-31 全年 (与全市场排名口径一致)
                latest_magic = compute_magic_one_day(
                    financials, str(financials[-1].get("end_date", "")), market_cap_wan
                ) or {}
            except Exception:
                pass

        latest_q = quarterly[-1] if quarterly else {}
        return {
            "quarterly": quarterly,
            "score":   0.0,
            "signals": [
                f"ROC {latest_q.get('roc', '—')}% | EY {latest_q.get('ey', '—')}%",
            ],
            "summary": f"ROC {latest_q.get('roc','—')}% | EY {latest_q.get('ey','—')}%",
            # 顶层字段 (valuation_data: Magic 为主)
            # 2026-11-02 删: PEG_真实 / fwd_pe / g / verdict / L_r8 / L_r10 / L_r12 /
            #   L_E3_r10 / L_achievable (PEG/DCF 整档已删)
            "roc":       latest_magic.get("roc"),
            "ey":        latest_magic.get("ey"),
            "ev_yi":     latest_magic.get("ev_yi"),
            "industry":  latest_magic.get("industry"),
            "ebit_yi":   latest_magic.get("ebit_yi"),
            "capital_yi":latest_magic.get("capital_yi"),
            "netdebt_yi":latest_magic.get("netdebt_yi"),
            "market_cap_yi": ctx.market_cap_yi,
            "period_label":     latest_magic.get("period_label", "no_data"),
            # 2026-11-05 加: 把 Magic 没算出来的**原因**透传出去
            #   之前 valuation_data 里没有 skip_reason → 报告层只能拿 period_label 兜底,
            #   渲染成 "ROC/EY=20260930" (把日期当原因, 用户根本看不懂)。
            #   实际原因在 compute_magic_one_day 里就有 (revenue_profit_collapse /
            #   non_operating_loss / industry_excluded / no_data / capital_too_small)。
            "skip_reason":      latest_magic.get("skip_reason"),
            "seasonal_warning": latest_magic.get("seasonal_warning", False),
        }


#    参数: (ctx: RawContext, raw: dict) → dict
#    key = 函数名去掉 "_derive_" 前缀
# ============================================================
# _derive_dcf 删除: DCF 已合并到 ValuationStrategy (Phase1), 2026-09-02


def _derive_sector_overheat(ctx: RawContext, raw: dict) -> dict:
    """板块过热预警 (个股 K线代理)"""
    kline = ctx.kline or []
    if len(kline) < 64:
        return {}
    closes = [k["close"] for k in kline if "close" in k]
    if len(closes) < 64:
        return {}
    pct_1w = (closes[-1] / closes[-6]  - 1) * 100
    pct_1m = (closes[-1] / closes[-22] - 1) * 100
    pct_3m = (closes[-1] / closes[-64] - 1) * 100
    return {
        "1周涨幅": f"{pct_1w:+.1f}%",
        "1月涨幅": f"{pct_1m:+.1f}%",
        "3月涨幅": f"{pct_3m:+.1f}%",
        "source": "个股 K线代理 (industry={})".format(ctx.industry or "未知"),
        "_warning": "sector_overheat 来自个股 K线代理, 不是真实板块指数",
    }


# 2026-11-03 删: _derive_five_categories (5 类 14 子信号整档已删; multi.py 已不存在, 该函数恒返回 {})


# ============================================================
# 4. 调度器常量
# ============================================================

# Phase1 策略类列表
# 2026-09-08: 删 ValuationStrategy (合并进 FinanceStrategy)
# 2026-11-05: 删 StructureStrategy (原 SMC 结构分析 OB/FVG/Sweep — 报告层早已不消费)
PHASE1_STRATEGY_CLASSES = [
    WyckoffStrategy,
    TechnicalStrategy,  # v6.2.8 加: 8 个技术指标
    FinanceStrategy,
]

# Phase2 派生函数列表
PHASE2_FUNCTIONS = [
    _derive_sector_overheat,
]

# 2026-09-09 删: strategy 权重 (chan 0.20 / wyckoff 0.20 / structure 0.10)
#   (2026-11-05 structure 整个策略类已删)
#   (2026-11-06 chan 整个策略类已删)
# 决策走 威科夫阶段判定, 不再加权汇总.
# 保留空 dict 防止旧 import KeyError.
_STRATEGY_WEIGHTS: dict[str, float] = {}

# 向后兼容别名 (旧代码 import PHASE1_STRATEGIES / PHASE2_STRATEGIES)
PHASE1_STRATEGIES = PHASE1_STRATEGY_CLASSES
PHASE2_STRATEGIES: list = []   # Phase2 已改为函数，保留空列表避免 ImportError
DEFAULT_STRATEGIES = PHASE1_STRATEGY_CLASSES


# ============================================================
# 5. 调度器 (AnalysisEngine)
# ============================================================

class AnalysisEngine:
    """RawContext → AnalysisResult

    唯一的 factor 计算入口。Strategy 从 ctx 读原始 K 线计算，不读 dump['factor']。
    as_of_date 切片在 Engine 层统一做，Strategy 无需感知时间。
    """

    def __init__(self, strategies=None):
        """
        Args:
            strategies: Phase1 策略类列表 (可选, 用于按需加速)。
                        None = 跑全部 Phase1 + Phase2。
                        传入子集 (如 [WyckoffStrategy]) 只跑那几个 Phase1。
        """
        if strategies is None:
            self._phase1 = [s() for s in PHASE1_STRATEGY_CLASSES]
        else:
            self._phase1 = [s() for s in strategies
                            if s in PHASE1_STRATEGY_CLASSES]

    def analyze(self, ctx: "RawContext") -> "AnalysisResult":
        """单点分析: ctx 最后一日的 AnalysisResult

        2026-08-29 删 scene/resonance 后, 单点入口改回调 analyze_history 取末点
        (避免双份合并逻辑漂移)
        """
        if not ctx.kline:
            return AnalysisResult(code=ctx.code, name=ctx.name, current_price=0.0)
        last = ctx.kline[-1]["trade_date"].replace("-", "")[:8]
        results = self.analyze_history(ctx, [last])
        return results.get(last) or AnalysisResult(
            code=ctx.code, name=ctx.name, current_price=ctx.current_price,
        )

    def analyze_history(self, ctx: "RawContext", dates: list) -> "dict[str, AnalysisResult]":
        """批量历史计算：每个 strategy 用自己最优方式遍历，合并结果。

        Phase 2 (sector_overheat) 不在此处运行 — 由
        RenderData.from_result() 在 render 路径按需执行一次。

        Args:
            ctx:   全量 RawContext（不切片）
            dates: 日期列表，格式 'YYYYMMDD'
        Returns:
            dict[date_str, AnalysisResult]
        """
        dates_clean = [d.replace("-", "")[:8] for d in dates]

        # 每个 strategy 各自批量计算（所有 strategy 现在都有 analyze_history）
        strategy_results: dict[str, dict[str, dict]] = {}
        for inst in self._phase1:
            strategy_results[inst.name] = inst.analyze_history(ctx, dates_clean)

        # 预建 date → close 索引，避免合并阶段 O(n) 查找
        date_to_close = {k["trade_date"].replace("-", "")[:8]: k["close"] for k in ctx.kline}

        # 合并每个节点的所有 strategy 结果 → AnalysisResult
        results = {}
        for date in dates_clean:
            raw: Dict[str, Any] = {}
            for inst in self._phase1:
                raw[inst.name] = (strategy_results.get(inst.name) or {}).get(date, {})
            # 2026-08-29: 删 A/B/C/D/E scene 分类 (硬编码 if-else 不准)
            # 改: signals_active 直接从 raw 聚合 (LLM 自己判)
            signals_active = []
            for k, d in raw.items():
                if isinstance(d, dict):
                    for s in (d.get("signals") or []):
                        signals_active.append(f"{k}: {s}")
            results[date] = AnalysisResult(
                code=ctx.code, name=ctx.name,
                current_price=date_to_close.get(date, ctx.current_price),
                raw=raw, signals_active=signals_active,
            )
        return results

