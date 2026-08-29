"""
analysis_engine.py — analysis 层 (v5.10.35, 2026-08-26)

架构 (3 层):
- L1 dump 层: DataStore.get_ctx(code)
- L2 analysis 层 (本文件): AnalysisEngine.analyze(ctx) → AnalysisResult
- L3 render 层: render_report(analysis_data) → markdown

策略模式 (v5.10.35 简化):
- 6 个 Phase1 策略类: 返回 dict (含 score + 因子数据), 不再继承 AnalysisStrategy ABC
- 8 个 Phase2 派生函数 (模块级): 纯数据提取, weight=0
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
    weekly:     list        # 周线 K 线（Tushare 真实周线，非合成）
    eps_table:  list        # EPS 历史预测（季报，不随 as_of_date 切片）
    fflow:      dict        # 主力资金流（不切片）
    moneyflow:  list = field(default_factory=list)  # Tushare money_flow 原始列表（不切片）
    current_price:  float = 0.0
    market_cap_yi:  float = 0.0
    industry: str = ""
    code: str = ""
    name: str = ""

    # Phase1 Strategy 算完后写这里，Phase2 函数直接读，不重算
    chan_result:    dict = field(default_factory=dict)
    wyckoff_result: dict = field(default_factory=dict)   # 日线
    wyckoff_weekly: dict = field(default_factory=dict)
    smc_result:     dict = field(default_factory=dict)
    fflow_result:   dict = field(default_factory=dict)   # 主力资金流 (Tushare.money_flow)
    obv_result:     dict = field(default_factory=dict)   # 经典 OBV (Granville 1963, K线累计)
    resonance_result: dict = field(default_factory=dict)
    _bsp_for_data:    dict = field(default_factory=dict)   # ChanStrategy 写入，_derive_buy_sell_points 读取
    kline_arrs:         dict = field(default_factory=dict)   # build_kline_features 预算结果，WyckoffStrategy 算完后写，ObvStrategy 复用
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
            eps_table=self.eps_table,
            fflow=self.fflow,
            moneyflow=self.moneyflow,
            current_price=price,
            market_cap_yi=self.market_cap_yi,
            code=self.code,
            name=self.name,
        )
        # 透传预计算缓存（factor_history 优化：全量信号预算一次，切片节点直接查表）
        for _attr in ('_precomputed_signals', '_czsc_signals_obj',
                      '_czsc_signals_bars', '_czsc_signals_pos',
                      '_czsc_signals_start', '_czsc_sigs_window'):
            if hasattr(self, _attr):
                setattr(sliced, _attr, getattr(self, _attr))
        return sliced


@dataclass
class AnalysisResult:
    """1 个 dump 的完整 analysis 结果 (L2 → L3)

    raw 是唯一数据来源:
      raw["wyckoff"]  = {"score": ..., "stage": ..., "sub_events": ..., "3period": ...}
      raw["chan"]      = {"score": ..., "weekly": {...}, "daily": {...}}
      raw["smc"]       = {"score": ..., "ob": [...], "smc_weekly": {...}}
      raw["fflow"]     = {"score": ..., "verdict": ..., "main_yi": ...}
      raw["obv"]       = {"score": ..., "verdict": ..., "obv5": ..., "obv_trend": ...}
      raw["peg"]       = {"score": ..., "PEG_真实": ..., ...}
      raw["position"]  = {"close_pos_day": ..., ...}
      # Phase2 派生 (weight=0)
      raw["dcf"]                 = {r_8%: ..., r_10%: ..., r_12%: ...}
      raw["sector_overheat"]     = {1周涨幅: ..., ...}
      raw["five_categories"]     = {score: ..., verdict: ...}
      raw["buy_sell_points"]     = {daily: {...}, weekly: {...}}
      raw["exit_signals"]        = {绿信号数: ..., 红信号数: ..., ...}
      raw["stop_profit_loss"]    = {止盈3层: [...], 止损4档: [...]}
      raw["three_layer_position"]= {位置: ..., 市况: ...}
      raw["monitor_triggers"]    = {触发N项: ...}
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
        # Backward compat: smc_weekly 作为独立顶层 key (render 可能直接读 analysis["smc_weekly"])
        smc = self.raw.get("smc") or {}
        if smc.get("smc_weekly"):
            d.setdefault("smc_weekly", smc["smc_weekly"])
        return d


# ============================================================
# 2. Phase1 策略类 (返回 dict, 含 score + 因子数据)
# ============================================================

class WyckoffStrategy:
    """威科夫 3 阶段 → 分数，从 ctx.kline 直接重算"""
    name = "wyckoff"

    def analyze(self, ctx: RawContext) -> dict:
        from tools.factors.wyckoff.stage_factor import WyckoffStageFactor
        import pandas as _pd

        score_map = {
            "Accumulation": 0.6,
            "Markup": 1.0,
            "Distribution": -0.6,
            "Markdown": 1.0,
            "?": 0.0,
        }

        def _make_df(bars):
            return _pd.DataFrame({
                "close":   [k["close"] for k in bars],
                "high":    [k["high"]  for k in bars],
                "low":     [k["low"]   for k in bars],
                "volume":  [k.get("volume", 0) for k in bars],
                "open":    [k.get("open", k["close"]) for k in bars],
                "pct_chg": [k.get("pct_chg", 0) for k in bars],
                "date":    [k.get("trade_date", "") for k in bars],
            })

        sub_events_by_period = {}
        wyckoff_3period = {}
        _pre = getattr(ctx, 'cached_sub_events', {})
        for level, bars, label in [
            ("daily",  ctx.kline,  "daily"),
            ("weekly", ctx.weekly, "weekly"),
        ]:
            if bars and len(bars) >= 30:
                as_of_idx = len(bars) - 1
                out = WyckoffStageFactor().compute(
                    _make_df(bars), period_label=label,
                    window=min(250, len(bars)),
                    market_cap_yi=ctx.market_cap_yi,
                    code=ctx.code,
                    as_of_idx=as_of_idx,
                    precomputed_sub_events_raw=_pre.get(level),
                )
                sub_events_by_period[level] = out.get("sub_events", [])
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
        # 写入 ctx 供 ObvStrategy 复用，避免重复计算 MA20/MA60
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


class SmcStrategy:
    """SMC (OB/FVG/Sweep) → 分数，日线 + 周线两周期"""
    name = "smc"

    def analyze(self, ctx: RawContext) -> dict:
        from tools.factors.smc.analysis import smc_analysis as _smc

        def _run(kline, label):
            if not kline or len(kline) < 20:
                return {}
            try:
                return _smc(
                    opens=  [k.get("open", k["close"]) for k in kline],
                    highs=  [k["high"]  for k in kline],
                    lows=   [k["low"]   for k in kline],
                    closes= [k["close"] for k in kline],
                    dates=  [k.get("trade_date", k.get("date", "")) for k in kline],
                    vols=   [k.get("volume", 0) for k in kline],
                    current_price=ctx.current_price,
                ) or {}
            except Exception:
                return {}

        smc_d  = _run(ctx.kline,  "daily")
        smc_w  = _run(ctx.weekly, "weekly")

        # 合并存储（daily 向后兼容，双周期独立）
        smc = {**smc_d, "smc_weekly": smc_w}
        ctx.smc_result = smc

        total_obs = (smc_d.get("total_obs", 0) or 0) + \
                    (smc_w.get("total_obs", 0) or 0)
        sweeps = len(smc_d.get("recent_sweeps") or [])
        score = min(total_obs * 0.1, 0.5) if total_obs > 0 else 0.0
        signals = []
        if total_obs > 3:
            signals.append(f"SMC OB {total_obs}个(日/周)")
        if sweeps > 0:
            signals.append(f"SMC 扫流 ×{sweeps}")
        return {
            "score":   score,
            "signals": signals,
            "summary": f"SMC OB={total_obs} 扫流={sweeps}",
            **smc,
        }

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """O(n): OB/FVG/Sweep 预扫一次，per date O(小常数) 过滤。"""
        from tools.factors.smc.order_blocks import find_order_blocks
        from tools.factors.smc.fvg import find_fvg
        from tools.factors.smc.swings_sweeps import find_liquidity_sweeps
        from tools.factors.smc.atr import calc_atr

        dates_clean = set(d.replace("-", "")[:8] for d in dates)
        kline = ctx.kline or []
        if len(kline) < 20:
            return {d.replace("-","")[:8]: {"score":0,"signals":[],"summary":"SMC 数据不足"} for d in dates}

        opens  = [k.get("open", k["close"]) for k in kline]
        highs  = [k["high"]  for k in kline]
        lows   = [k["low"]   for k in kline]
        closes = [k["close"] for k in kline]
        dates_list = [k.get("trade_date","").replace("-","")[:8] for k in kline]
        vols   = [k.get("volume", 0) for k in kline]

        # displacement_atr_mult 自适应（与 smc_analysis 逻辑一致）
        atr = calc_atr(highs, lows, closes) or 0
        n_pre = min(50, len(closes) - 1)
        if atr > 0 and n_pre >= 5:
            bar_amp = sorted(abs(closes[i] - opens[i]) / atr for i in range(-n_pre, 0) if atr > 0)
            idx70 = int(len(bar_amp) * 0.7)
            displacement_atr_mult = max(0.8, min(2.5, bar_amp[idx70]))
        else:
            displacement_atr_mult = 1.0

        # 全量扫一次
        obs_all  = find_order_blocks(opens, highs, lows, closes, dates_list, 120,
                                     displacement_atr_mult=displacement_atr_mult)
        fvgs_all = find_fvg(opens, highs, lows, closes, dates_list, 120)
        swps_all = find_liquidity_sweeps(opens, highs, lows, closes, dates_list, lookback=30)

        date_to_idx = {d: i for i, d in enumerate(dates_list)}
        max_ob_age = 80

        results = {}
        for date in dates:
            date_clean = date.replace("-","")[:8]
            i = date_to_idx.get(date_clean, -1)
            if i < 0:
                results[date_clean] = {"score": 0, "signals": [], "summary": "—"}
                continue

            cp = closes[i]

            def _age(ob): return i - ob.get("formed_at_index", i)
            def _sweep_age(s): return i - s.get("sweep_candle_index", i)

            bull_obs = [ob for ob in obs_all["bull"]
                        if ob.get("formed_at_index", i) <= i and _age(ob) <= max_ob_age]
            bear_obs = [ob for ob in obs_all["bear"]
                        if ob.get("formed_at_index", i) <= i and _age(ob) <= max_ob_age]
            fvgs = [f for f in fvgs_all if f.get("formed_at_index", i) <= i]
            sweeps = [s for s in swps_all if s.get("sweep_candle_index", i) <= i]

            nearest_bull = max((ob for ob in bull_obs if ob["bottom"] < cp), key=lambda ob: ob["bottom"], default=None)
            nearest_bear = min((ob for ob in bear_obs if ob["top"] > cp), key=lambda ob: ob["top"], default=None)

            total_obs  = len(bull_obs) + len(bear_obs)
            total_fvgs = len(fvgs)
            n_sweeps   = len([s for s in sweeps if _sweep_age(s) <= 30])

            score   = min(total_obs * 0.1, 0.5) if total_obs > 0 else 0.0
            signals = []
            if total_obs > 3:  signals.append(f"SMC OB {total_obs}个")
            if n_sweeps > 0:   signals.append(f"SMC 扫流 ×{n_sweeps}")

            results[date_clean] = {
                "score": score, "signals": signals,
                "summary": f"SMC OB={total_obs} 扫流={n_sweeps}",
                "nearest_bull_ob": nearest_bull, "nearest_bear_ob": nearest_bear,
                "total_obs": total_obs, "total_fvgs": total_fvgs,
                "recent_sweeps": sweeps[-3:],
            }
        return results


class FflowStrategy:
    """fflow (主力资金流) → 分数，从 ctx.moneyflow 直接计算

    2026-08-17 拆分: 跟 ObvStrategy 解耦, fflow 单独跑. 之前是 VolumePriceStrategy
    把 fflow + OBV 混在一起算, 现在 fflow 走 Tushare.money_flow (dump 预拉), OBV 走
    经典 Granville 1963 K线累计 (ObvStrategy), 各自独立 strategy.

    双判定同向/矛盾信号在这里算 (ObvStrategy 已先跑过, ctx.obv_result 有结果).
    """
    name = "fflow"

    def analyze(self, ctx: RawContext) -> dict:
        from tools.factors.volume.price_fflow import fflow_factor
        ff = {}
        try:
            ff = fflow_factor(
                ctx.code,
                moneyflow_list=ctx.moneyflow,
                dates=None,    # moneyflow 自带 trade_date
                asof=None,     # 最新 (未来可从 ctx 读 asof)
            ) or {}
        except Exception:
            pass
        ctx.fflow_result = ff

        score   = float(ff.get("score", 0) or 0)
        signals = list(ff.get("signals", []) or [])

        # === fflow + OBV 双判定 (2026-08-17 拆分后挪到 strategy 层) ===
        # ObvStrategy 先于 FflowStrategy 跑 (PHASE1_STRATEGY_CLASSES 顺序), ctx.obv_result 已就绪
        obv = ctx.obv_result or {}
        obv_score = float(obv.get("score", 0) or 0)
        fflow_dir = 1 if score > 0 else (-1 if score < 0 else 0)
        obv_dir   = 1 if obv_score > 0 else (-1 if obv_score < 0 else 0)
        if fflow_dir != 0 and obv_dir != 0:
            if fflow_dir == obv_dir:
                fflow_label = ff.get("verdict", "").replace("主力", "")
                obv_label   = obv.get("verdict", "").replace("主力", "")
                signals.append(f"✅ fflow+OBV 同向 ({fflow_label}+{obv_label})")
                # 同向时给个小加权 (+0.5), 让 total_score 更敏感
                score = score + 0.5
            else:
                signals.append("⚠️ fflow vs OBV 方向矛盾 (数据冲突)")

        summary = ff.get("verdict", "—")
        return {
            "score":   score,
            "signals": signals,
            "summary": summary,
            **ff,
        }

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """fflow 不依赖历史切片，直接对全量 ctx 跑一次，所有 date 共享同一结果。"""
        result = self.analyze(ctx)
        return {d: result for d in dates}


class ObvStrategy:
    """OBV (经典 Granville 1963) → 分数, 从 ctx.kline 直接计算

    2026-08-17 拆分: 之前 OBV 在 price_fflow_factor 里作为并联/兜底, 现在拆成独立
    strategy. OBV 跟 fflow 走的是不同数据源 (K线 vs Tushare.money_flow), 各自独立:

    - fflow 优势: 真实主力净额 (亿元), 但需要 Tushare.money_flow dump
    - OBV 优势: 纯 K 线累计, 无需额外数据源, 任何 dump 都能跑, 段背离多次确认
                  适合兜底 (fflow 无数据时 OBV 仍能跑)

    段背离阈值: 60 日内 4 个 15 日窗口, 底 `pct<-2% 且 OBV净增>+3%`,
                顶 `pct>+2% 且 OBV净增<-3%`. ≥2 窗口 = 强, =1 = 单次.
    """
    name = "obv"

    def analyze(self, ctx: RawContext) -> dict:
        from tools.factors.volume.price_fflow import obv_factor
        obv = {}
        try:
            kline = ctx.kline or []
            obv = obv_factor(
                closes=[k["close"] for k in kline],
                vols=  [k.get("volume", 0) for k in kline],
                dates= [k.get("trade_date", k.get("date", "")) for k in kline],
                asof=None,
            ) or {}
        except Exception:
            pass
        ctx.obv_result = obv

        score   = float(obv.get("score", 0) or 0)
        signals = list(obv.get("signals", []) or [])
        summary = obv.get("verdict", "—")
        return {
            "score":   score,
            "signals": signals,
            "summary": summary,
            **obv,
        }

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """O(n): OBV 数组预建一次，per date O(1) 查信号。复用 ctx.kline_arrs（WyckoffStrategy 已算）。"""
        from tools.factors.kline_arrays import sliding_ma

        kline = ctx.kline or []
        if len(kline) < 2:
            return {d.replace("-","")[:8]: {"score":0,"signals":[],"summary":"OBV 数据不足"} for d in dates}

        closes = [k["close"]          for k in kline]
        vols   = [k.get("volume", 0)  for k in kline]
        dates_list = [k.get("trade_date","").replace("-","")[:8] for k in kline]
        date_to_idx = {d: i for i, d in enumerate(dates_list)}

        # OBV 数组：一次遍历 O(n)
        obv = [0.0]
        for i in range(1, len(closes)):
            if closes[i] > closes[i-1]:   obv.append(obv[-1] + vols[i])
            elif closes[i] < closes[i-1]: obv.append(obv[-1] - vols[i])
            else:                          obv.append(obv[-1])

        obv_ma20_arr = sliding_ma(obv, 20)
        # 复用 WyckoffStrategy 已算的 arrs（ma20/ma60），只补 ma5/ma120
        arrs = ctx.kline_arrs
        ma5_arr   = arrs.get('ma5')   or sliding_ma(closes, 5)
        ma20_arr  = arrs.get('ma20')  or sliding_ma(closes, 20)
        ma60_arr  = arrs.get('ma60')  or sliding_ma(closes, 60)
        ma120_arr = arrs.get('ma120') or sliding_ma(closes, 120)

        dates_clean = [d.replace("-","")[:8] for d in dates]
        results = {}
        for date_clean in dates_clean:
            i = date_to_idx.get(date_clean, -1)
            if i < 1:
                results[date_clean] = {"score": 0, "signals": [], "summary": "—", "verdict": "—",
                                       "obv": 0, "obv5": 0, "obv_trend": 0, "source": "OBV 派生 (K线)"}
                continue

            p       = closes[i]
            m5      = ma5_arr[i]
            m20     = ma20_arr[i]
            m60     = ma60_arr[i]
            m120    = ma120_arr[i]
            obv_now = obv[i]
            obv_ma20 = obv_ma20_arr[i]
            obv_trend = (obv_now - obv_ma20) / max(abs(obv_ma20), 1) if obv_ma20 else 0

            pct5  = (closes[i] / closes[i-5]  - 1) * 100 if i >= 5  else 0
            pct20 = (closes[i] / closes[i-20] - 1) * 100 if i >= 20 else 0
            vr    = vols[i] / (sum(vols[i-19:i+1]) / 20) if i >= 20 else 1.0
            d120  = (p / m120 - 1) * 100 if m120 else 0

            signals = []; score = 0
            if d120 > 5:    signals.append(f"MA120偏{d120:.0f}%高位"); score -= 1
            elif d120 < -5: signals.append(f"MA120偏{d120:.0f}%低位蓄势"); score += 1
            if pct20 > 10 and obv_trend < -0.05:  signals.append("OBV背离:价涨OBV降→出货"); score -= 2
            elif pct20 < -5 and obv_trend > 0.05: signals.append("OBV底背离:价跌OBV升→吸筹"); score += 2
            if vr > 1.5 and pct5 > 2:   signals.append(f"放量上涨vol={vr:.2f}"); score += 1
            elif vr > 1.5 and pct5 < -2: signals.append(f"放量下跌vol={vr:.2f}"); score -= 1
            elif vr < 0.5 and pct5 > 3:  signals.append(f"缩量拉高vol={vr:.2f}出货嫌疑"); score -= 1
            elif vr < 0.7 and pct5 < -2: signals.append("缩量回调卖压轻"); score += 1
            if m60 and m120 and m60 > m120 and m5 and p < m5:
                signals.append("拉高出货型"); score -= 1
            elif m5 and m20 and m60 and m120 and p > m5 > m20 > m60 > m120:
                signals.append("多头排列"); score += 1

            # 段背离删除 (2026-08-29, 太滞后); 改用 obv5 (5日价跌+OBV涨) + obv_trend (OBV>MA20) 实战信号
            # (在 obv5 / obv_trend 字段输出, 由 cache 消费)

            if score >= 3:    verdict = "🟢主力进货"
            elif score >= 1:  verdict = "🟡偏进货"
            elif score == 0:  verdict = "⬜中性"
            elif score >= -2: verdict = "🟠偏出货"
            else:             verdict = "🔴主力出货"

            ma20_dev  = round((closes[i] / m20  - 1) * 100, 1) if m20  else None
            ma120_dev = round((closes[i] / m120 - 1) * 100, 1) if m120 else None

            # OBV 实战信号: 5 日价跌+OBV 涨 (obv5), OBV>MA20 (obv_trend)
            obv5 = 1 if (i >= 5 and closes[i] < closes[i-5] and obv[i] > obv[i-5]) else 0
            obv_trend = 1 if (obv_ma20_arr[i] and obv[i] > obv_ma20_arr[i]) else 0

            results[date_clean] = {
                "score": score, "signals": signals, "summary": verdict, "verdict": verdict,
                "obv": obv[i],
                "obv5": obv5,            # 5 日价跌 + OBV 涨
                "obv_trend": obv_trend,  # OBV > MA20
                "ma20_dev": ma20_dev, "ma120_dev": ma120_dev,
                "source": "OBV 派生 (K线)",
            }
        return results


class ChanStrategy:
    """缠论 (中枢+背驰+买卖点)，日线+周线统一用一个 CzscSignals 对象。"""
    name = "chan"

    # ── 共享工具 ──────────────────────────────────────────────

    @staticmethod
    def _build_cs(ctx: RawContext, warmup_to: int = 0):
        """建含日线+周线的 CzscSignals，返回 (cs, bars_d)。"""
        from tools.factors.chan.czsc_signals import build_incremental_signals
        from tools.factors.chan.czsc_adapter import dates_closes_to_raw_bars
        import czsc as _czsc

        week = ctx.weekly if ctx.weekly else []
        weekly_bars = None
        if len(week) >= 30:
            d_w = [w.get('trade_date', '') for w in week]
            c_w = [w.get('close', 0)       for w in week]
            h_w = [w.get('high', 0)         for w in week]
            l_w = [w.get('low', 0)          for w in week]
            weekly_bars = dates_closes_to_raw_bars(d_w, c_w, h_w, l_w, symbol=ctx.code)

        ks = [{'date': k['trade_date'], 'open': k['open'], 'close': k['close'],
               'high': k['high'], 'low': k['low'],
               'vol': k.get('vol', 0), 'amount': k.get('amount', 0)}
              for k in ctx.kline]
        cs, bars, _, _ = build_incremental_signals(ks, ctx.code,
                                                    warmup_to=warmup_to,
                                                    weekly_bars=weekly_bars)
        return cs, bars

    @staticmethod
    def _hub_result(czsc_obj, p_now, label, dates):
        """从 CZSC/kas 对象提取中枢+段信息，统一格式。bis 不存储（render 不需要笔列表）。"""
        from tools.factors.chan.czsc_adapter import bis_to_segs_format, czsc_zss_to_hub_format
        from czsc import get_zs_seq
        bis  = czsc_obj.bi_list
        zss  = get_zs_seq(bis)
        segs = bis_to_segs_format(bis, dates)
        hubs = czsc_zss_to_hub_format(zss)
        if hubs:
            h = hubs[-1]
            hl, hh = h['low'], h['high']
            if p_now > hh:            h['pos'] = "上方✅"
            elif p_now < hl * 0.95:   h['pos'] = "跌穿🔴"
            elif p_now < hl:           h['pos'] = "下方⚠️"
            else:                      h['pos'] = "内部⬜"
        seg_status = '未知'
        if segs:
            last = segs[-1]
            if last['sst'] == 'T' and p_now < last['ep']:
                seg_status = f"下跌段延伸中（¥{last['sp']:.0f}→¥{p_now:.0f}，未结束）"
            elif last['sst'] == 'B' and p_now > last['ep']:
                seg_status = f"上涨段延伸中（¥{last['sp']:.0f}→¥{p_now:.0f}，未结束）"
            else:
                seg_status = f"最后段结束¥{last['ep']:.0f}，当前¥{p_now:.0f}震荡"
        supports = sorted([s['lo'] for s in segs[-8:] if s['lo'] < p_now], reverse=True)[:3]
        return {
            'hub': hubs[-1] if hubs else {'valid': False},
            'hubs': hubs, 'segs': segs,
            'n_strokes': len(bis), 'n_segs': len(segs),
            'seg_status': seg_status, 'supports': supports,
            'p': p_now, 'label': label,
        }

    @staticmethod
    def _snapshot_chan_state(cs, sigs_win, p_now, d_d, bar_dt):
        """从当前 cs 状态快照日线+周线中枢和买卖点，返回 chan dict。"""
        from tools.factors.chan.czsc_signals import extract_points_from_sigs
        kas = cs.kas or {}
        day_czsc = kas.get('日线')
        wk_czsc  = kas.get('周线')
        res_d = ChanStrategy._hub_result(day_czsc, p_now, '日线', d_d) if day_czsc else {}
        res_w = ChanStrategy._hub_result(wk_czsc,  p_now, '周线', []) if wk_czsc else {}
        bsp_d = extract_points_from_sigs(sigs_win, bar_dt, days=1)
        return {
            "daily":  {**res_d, "buy_sell_points": bsp_d.get('points', {})},
            "weekly": {**res_w, "buy_sell_points": {}},
            "buy_sell_points": {
                "daily": bsp_d.get('points', {}), "weekly": {}, "60min": {}, "30min": {},
            },
        }

    # ── 单次分析 ─────────────────────────────────────────────

    def analyze(self, ctx: RawContext) -> dict:
        chan = {}
        try:
            if ctx.kline and len(ctx.kline) >= 30:
                last_date = ctx.kline[-1]['trade_date'].replace("-", "")[:8]
                results = self.analyze_history(ctx, [last_date])
                chan = results.get(last_date, {})
                ctx._bsp_for_data = chan.get("buy_sell_points", {})
        except Exception:
            pass
        ctx.chan_result = chan

        score_total, cnt = 0.0, 0
        for level in ["weekly", "daily"]:
            pos = (chan.get(level, {}).get("hub") or {}).get("pos", "—")
            if "下方" in str(pos):   score_total -= 0.3; cnt += 1
            elif "上方" in str(pos): score_total += 0.3; cnt += 1

        bsp = getattr(ctx, "_bsp_for_data", {})
        return {
            "score":   score_total / cnt if cnt > 0 else 0.0,
            "signals": [],
            "summary": f"缠论 {cnt}/2 级别",
            **chan,
            "buy_sell_points": bsp,
        }

    # ── 批量历史 ─────────────────────────────────────────────

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """一次遍历全量 bars，在每个 date 节点记录中枢+买卖点。"""
        from tools.factors.chan.czsc_signals import extract_points_from_sigs  # noqa
        dates_clean = [d.replace("-", "")[:8] for d in dates]
        dates_set   = set(dates_clean)
        if not ctx.kline or len(ctx.kline) < 30:
            return {}

        cs, bars = self._build_cs(ctx, warmup_to=0)
        if cs is None:
            return {}

        d_d = [k.get('trade_date', '') for k in ctx.kline]
        sigs_win = []
        results  = {}

        for bar in bars:
            bar_dt = str(bar.dt)[:10].replace("-", "")[:8]
            cs.update_signals(bar)
            snap = dict(cs.s); snap['dt'] = bar_dt
            sigs_win.append(snap)
            if len(sigs_win) > 60: sigs_win.pop(0)

            if bar_dt not in dates_set:
                continue

            p_now = float(snap.get('close') or 0) or ctx.current_price
            node  = self._snapshot_chan_state(cs, sigs_win, p_now, d_d, bar_dt)
            results[bar_dt] = {**node, "score": 0.0, "signals": [], "summary": "缠论历史"}

        return results


class PegStrategy:
    """PEG 估值 → 分数"""
    name = "peg"

    def analyze(self, ctx: RawContext) -> dict:
        from tools.factors.valuation.multi import PegFactor
        peg_factor = PegFactor()
        peg = peg_factor(df=None, eps_table=ctx.eps_table,
                         current_price=ctx.current_price) or {}
        peg_real = peg.get("PEG_真实")
        signals = []
        score = 0.0
        summary = "—"
        if peg_real is not None and isinstance(peg_real, (int, float)):
            # PEG < 0.5 = 低估 (强买)
            # PEG 0.5-1 = 合理 (持有)
            # PEG 1-2 = 偏贵
            # PEG > 2 = 高估 (卖)
            if peg_real < 0.5:
                score = 1.0
            elif peg_real < 1.0:
                score = 0.5
            elif peg_real < 2.0:
                score = 0.0
            else:
                score = -1.0
            signals.append(f"PEG 真实 {peg_real}")
            summary = f"PEG {peg_real}"
        else:
            signals.append("PEG 数据不足")
            summary = "PEG 数据不足"

        return {
            "score":   score,
            "signals": signals,
            "summary": summary,
            **peg,
        }

    def analyze_history(self, ctx: RawContext, dates: list) -> dict:
        """PEG 不依赖历史切片，对全量 ctx 跑一次，所有 date 共享同一结果。"""
        result = self.analyze(ctx)
        return {d: result for d in dates}

# ============================================================
# 3. Phase2 派生函数 (weight=0, 纯数据提取)
#    参数: (ctx: RawContext, raw: dict) → dict
#    key = 函数名去掉 "_derive_" 前缀
# ============================================================

def _derive_dcf(ctx: RawContext, raw: dict) -> dict:
    """DCF 折现估值 (3 档 r=8/10/12%)"""
    from tools.factors.valuation.multi import DcfFactor
    try:
        return DcfFactor()(df=None, eps_table=ctx.eps_table,
                           current_price=ctx.current_price,
                           market_cap_yi=ctx.market_cap_yi) or {}
    except Exception:
        return {}


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


def _derive_five_categories(ctx: RawContext, raw: dict) -> dict:
    """5 类 14 子信号 (缠论+止跌+fflow+估值)"""
    from tools.factors.valuation.multi import FiveCategoriesFactor
    try:
        return FiveCategoriesFactor()(df=None, fflow=ctx.fflow,
                                      eps_table=ctx.eps_table,
                                      current_price=ctx.current_price) or {}
    except Exception:
        return {}


def _derive_buy_sell_points(ctx: RawContext, raw: dict) -> dict:
    """缠论 1买/1卖/2买/3买 + 双中枢/笔结束 (3 级别)

    v3.0: 直接读 ChanStrategy 算的 ctx._bsp_for_data (czsc_signals 输出, 100% czsc)
    不再调老的 BuySellPointsFactor (tools/factors/chan/_deprecated/buy_sell.py)
    """
    bsp = getattr(ctx, "_bsp_for_data", None) or {}
    if not bsp:
        return {}
    # 补一个 action 字段 (取最强信号), 保持 flat dict 格式 (跟 czsc_signals 一致)
    def _action(points):
        if not isinstance(points, dict):
            return "—"
        for k in ['🟢1买⭐', '🟢1买', '🟢3买', '🟢双中枢', '🟢笔结束',
                  '🔴1卖⭐', '🔴1卖', '🔴2卖', '🔴3卖',
                  '🟢吞没', '🔴三只乌鸦']:
            if k in points and points[k] not in (None, '—', ''):
                return k
        return "—"
    out = {}
    for level in ['weekly', 'daily']:
        pts = bsp.get(level, {})
        if pts and isinstance(pts, dict):
            out[level] = dict(pts)  # flat dict (跟 bsp 一样的 emoji keys)
            out[level]['action'] = _action(pts)
        else:
            out[level] = {}
    return out


def _derive_exit_signals(ctx: RawContext, raw: dict) -> dict:
    """退场信号 9 项 (PEG + L_E3 + MA120 + 板块 + 缠论)"""
    from tools.factors.registry import FactorRegistry
    reg = FactorRegistry()
    exit_factor = reg.get("exit_signals")
    if exit_factor is None:
        return {}
    try:
        out = exit_factor(df=None, fflow=ctx.fflow,
                          eps_table=ctx.eps_table,
                          current_price=ctx.current_price,
                          sector_ma20_dev=-22,
                          chan_signals=ctx.chan_result)
        return out if isinstance(out, dict) else {}
    except Exception as e:
        return {"error": str(e)}


def _derive_stop_profit_loss(ctx: RawContext, raw: dict) -> dict:
    """止盈 3 层 + 止损 4 档 (基于中枢上下沿)"""
    from tools.factors.registry import FactorRegistry
    reg = FactorRegistry()
    spl_factor = reg.get("stop_profit_loss")
    if spl_factor is None:
        return {}
    try:
        out = spl_factor(df=None, price=ctx.current_price, factor=ctx.chan_result)
        return out if isinstance(out, dict) else {}
    except Exception as e:
        return {"error": str(e)}


def _derive_three_layer_position(ctx: RawContext, raw: dict) -> dict:
    """三层仓位 (日线中枢+缠论+fflow+PEG)"""
    from tools.factors.registry import FactorRegistry
    reg = FactorRegistry()
    pos_factor = reg.get("three_layer_position")
    if pos_factor is None:
        return {}
    try:
        res_d = ctx.chan_result.get("daily") or {}
        out = pos_factor(df=None, price=ctx.current_price, chan_d=res_d,
                         fflow=ctx.fflow, peg=0.76, factor=ctx.chan_result)
        return out if isinstance(out, dict) else {}
    except Exception as e:
        return {"error": str(e)}


def _derive_monitor_triggers(ctx: RawContext, raw: dict) -> dict:
    """监控触发点 5 类 14 子 (缠论背驰+止跌+fflow+事件)"""
    from tools.factors.registry import FactorRegistry
    reg = FactorRegistry()
    mon_factor = reg.get("monitor_triggers")
    if mon_factor is None:
        return {}
    try:
        res_d = ctx.chan_result.get("daily") or {}
        out = mon_factor(df=None, price=ctx.current_price, chan_d=res_d,
                         fflow=ctx.fflow, events=[], factor=ctx.chan_result)
        return out if isinstance(out, dict) else {}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# 4. 调度器常量
# ============================================================

# Phase1 策略类列表 (顺序重要: ChanStrategy 先跑, ObvStrategy 在 FflowStrategy 之前)
PHASE1_STRATEGY_CLASSES = [
    ChanStrategy,
    WyckoffStrategy,
    SmcStrategy,
    ObvStrategy,
    FflowStrategy,
    PegStrategy,
]

# Phase2 派生函数列表
PHASE2_FUNCTIONS = [
    _derive_dcf,
    _derive_sector_overheat,
    _derive_five_categories,
    _derive_buy_sell_points,
    _derive_exit_signals,
    _derive_stop_profit_loss,
    _derive_three_layer_position,
    _derive_monitor_triggers,
]

# Phase1 权重 (用于 total_score 加权)
_STRATEGY_WEIGHTS: dict[str, float] = {
    "chan":     0.20,
    "wyckoff": 0.20,
    "smc":     0.10,
    "obv":     0.10,
    "fflow":   0.10,
    "peg":     0.15,
}

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
                        传入子集 (如 [WyckoffStrategy, ChanStrategy]) 只跑那几个 Phase1。
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

        Phase 2 (DCF/exit_signals/...) 不在此处运行 — 由 RenderData.from_result() 在
        render 路径按需执行一次。

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
            chan_bsp = (raw.get('chan') or {}).get('buy_sell_points') or {}
            ctx._bsp_for_data = chan_bsp
            raw["buy_sell_points"] = _derive_buy_sell_points(ctx, raw)
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

