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
      raw["obv"]       = {"score": ..., "verdict": ..., "obv_div_bot_60d": ...}
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
    scene: str = "?"  # A/B/C/D/E
    scene_name: str = "未知"
    resonance_count: int = 0
    signals_active: list[str] = field(default_factory=list)
    action: str = ""

    def to_dict(self, ctx: "RawContext | None" = None) -> dict:
        """序列化为 dict (给 AnalysisData / render 用)

        以 raw 为基础，顶层补充 code/name/scene 等元字段。
        """
        d = dict(self.raw)
        d.update({
            "code":            self.code,
            "name":            self.name,
            "current_price":   self.current_price,
            "scene":           self.scene,
            "scene_name":      self.scene_name,
            "resonance_count": self.resonance_count,
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
        for level, bars, label in [
            ("daily",  ctx.kline,  "daily"),
            ("weekly", ctx.weekly, "weekly"),
        ]:
            if bars and len(bars) >= 30:
                out = WyckoffStageFactor().compute(
                    _make_df(bars), period_label=label,
                    window=min(250, len(bars)),
                    market_cap_yi=ctx.market_cap_yi,
                    code=ctx.code,
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
        """批量历史：逐节点切片，复用 analyze。Wyckoff 无状态，切片后全量扫。"""
        results = {}
        for date in dates:
            date_clean = date.replace("-", "")[:8]
            sliced = ctx.slice(date_clean)
            results[date_clean] = self.analyze(sliced)
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
        """从 CZSC/kas 对象提取中枢+笔+段，统一格式。"""
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
            'p': p_now, 'label': label, 'bis': bis,
        }

    @staticmethod
    def _read_node(cs, sigs_win, p_now, d_d, bar_dt):
        """从当前 cs 状态读日线+周线中枢和买卖点，返回 chan dict。"""
        from tools.factors.chan.czsc_signals import extract_points_from_sigs
        kas = cs.kas or {}
        day_czsc = kas.get('日线')
        wk_czsc  = kas.get('周线')
        res_d = ChanStrategy._hub_result(day_czsc, p_now, '日线', d_d) if day_czsc else {}
        res_w = ChanStrategy._hub_result(wk_czsc,  p_now, '周线', []) if wk_czsc else {}
        bsp_d = extract_points_from_sigs(sigs_win, bar_dt)
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
        dates_set = set(d.replace("-", "")[:8] for d in dates)
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
            node  = self._read_node(cs, sigs_win, p_now, d_d, bar_dt)
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
        self._phase2_fns = PHASE2_FUNCTIONS

    def analyze(self, ctx: "RawContext", as_of_date: str | None = None) -> "AnalysisResult":
        """核心入口: RawContext → AnalysisResult

        Args:
            ctx:         RawContext 实例
            as_of_date:  "2026-07-01" 格式，传入时对 K 线做截断（回测用）
        """
        if as_of_date:
            ctx = ctx.slice(as_of_date)

        code  = ctx.code
        name  = ctx.name
        price = ctx.current_price

        raw: Dict[str, Any] = {}

        # Phase1: 基础因子，结果写回 ctx
        for inst in self._phase1:
            try:
                raw[inst.name] = inst.analyze(ctx)
            except Exception as e:
                raw[inst.name] = {
                    "score":   0.0,
                    "signals": [f"❌ {type(e).__name__}: {str(e)[:80]}"],
                    "summary": f"策略失败: {str(e)[:100]}",
                }

        # 价格位置因子 (WyckoffTradingAgent → mavis, 2026-08)
        try:
            from tools.factors.price.position import PricePositionFactor
            pos_raw = PricePositionFactor()(ctx.kline)
            raw["position"] = {
                k: float(v.iloc[-1])
                for k, v in pos_raw.items()
                if hasattr(v, "iloc") and len(v) > 0
            }
        except Exception as e:
            raw["position"] = {"error": str(e)[:100]}

        # Phase2: 派生数据 (读 ctx 共享结果, weight=0)
        for fn in self._phase2_fns:
            key = fn.__name__.replace("_derive_", "")
            try:
                raw[key] = fn(ctx, raw)
            except Exception as e:
                raw[key] = {"error": f"❌ {type(e).__name__}: {str(e)[:80]}"}

        # 加权聚合 total (Phase1 only, 不存入 AnalysisResult)
        total = 0.0
        n_effective = 0
        for k, w in _STRATEGY_WEIGHTS.items():
            d = raw.get(k) or {}
            sc = d.get("score")
            # v3.6.1 改: score > 0 才算 effective, score=0 (默认 0) 视为缺失
            if sc is not None and float(sc) > 0:
                n_effective += 1
            total += float(sc or 0) * w
        n_total = len(_STRATEGY_WEIGHTS)

        # 场景判定
        scene, scene_name, signals_active = self._decide_scene(raw, ctx)

        # 共振数
        resonance_count = self._count_resonance(raw, scene)

        # 行动建议 (v3.6.1: data_completeness < 50% 时显示"数据不足", 不显示假分数)
        action = self._decide_action(scene, total, resonance_count, n_effective, n_total)

        return AnalysisResult(
            code=code,
            name=name,
            current_price=price,
            raw=raw,
            scene=scene,
            scene_name=scene_name,
            resonance_count=resonance_count,
            signals_active=signals_active,
            action=action,
        )

    def analyze_history(self, ctx: "RawContext", dates: list) -> "dict[str, AnalysisResult]":
        """批量历史计算：每个 strategy 用自己最优方式遍历，合并结果。

        Args:
            ctx:   全量 RawContext（不切片）
            dates: 日期列表，格式 'YYYYMMDD'

        Returns:
            dict[date_str, AnalysisResult]
        """
        dates_clean = [d.replace("-", "")[:8] for d in dates]

        # 每个 strategy 各自批量计算
        # 有 analyze_history 的用批量版，否则降级逐节点切片
        strategy_results: dict[str, dict[str, dict]] = {}
        for inst in self._phase1:
            if hasattr(inst, 'analyze_history'):
                strategy_results[inst.name] = inst.analyze_history(ctx, dates_clean)
            else:
                per_date = {}
                for date in dates_clean:
                    sliced = ctx.slice(date)
                    try:
                        per_date[date] = inst.analyze(sliced)
                    except Exception as e:
                        per_date[date] = {"score": 0.0, "signals": [], "summary": str(e)}
                strategy_results[inst.name] = per_date

        # 合并每个节点的所有 strategy 结果 → AnalysisResult
        results = {}
        for date in dates_clean:
            sliced = ctx.slice(date)
            raw: Dict[str, Any] = {}
            for inst in self._phase1:
                raw[inst.name] = (strategy_results.get(inst.name) or {}).get(date, {})
            # _derive_buy_sell_points 读 ctx._bsp_for_data，从 chan 结果补填
            chan_bsp = raw.get('chan', {}).get('buy_sell_points') or {}
            sliced._bsp_for_data = chan_bsp
            # Phase2
            for fn in self._phase2_fns:
                key = fn.__name__.replace("_derive_", "")
                try:
                    raw[key] = fn(sliced, raw)
                except Exception:
                    pass
            scene, scene_name, signals_active = self._decide_scene(raw, sliced)
            resonance_count = self._count_resonance(raw, scene)
            action = self._decide_action(scene, 0.0, resonance_count, 0, len(self._phase1))
            results[date] = AnalysisResult(
                code=ctx.code, name=ctx.name,
                current_price=sliced.current_price,
                raw=raw, scene=scene, scene_name=scene_name,
                resonance_count=resonance_count,
                signals_active=signals_active, action=action,
            )
        return results

    # --- 内部: 场景判定 / 共振 / 行动 ---

    def _decide_scene(self, raw: dict, ctx: "RawContext") -> tuple[str, str, list[str]]:
        """场景判定 A/B/C/D/E (5 选 1 互斥)"""
        wy   = raw.get("wyckoff") or {}
        chan = raw.get("chan")    or {}

        signals = []
        for k, d in raw.items():
            if isinstance(d, dict):
                for s in (d.get("signals") or []):
                    signals.append(f"{k}: {s}")

        stage    = wy.get("stage", "?")
        wy_score = float(wy.get("score", 0) or 0)

        # 2026-08-17 拆分: 量价拆成 fflow + obv, scene 判定用两个分数的较小值 (任一出货都算弱)
        fflow_score = float((raw.get("fflow") or {}).get("score", 0) or 0)
        obv_score   = float((raw.get("obv")   or {}).get("score", 0) or 0)
        vp_score    = min(fflow_score, obv_score) if (raw.get("fflow") or raw.get("obv")) else 0.0

        if vp_score < -0.5 and wy_score <= 0:
            return ("E", "弱势", signals)

        if stage == "Accumulation":
            ma60_dev = self._calc_ma60_dev(ctx)
            chan_res  = ctx.chan_result
            daily_pos = str((chan_res.get("daily") or {}).get("hub", {}).get("pos", "") or "")
            hub_below = any(t in daily_pos for t in ["下方", "跌穿"])
            # 共振 1d 从 ctx.resonance_result 读
            res_1d_positive = (ctx.resonance_result.get("1d") or {}).get("score", 0) > 0

            if ma60_dev < -0.05 and hub_below and res_1d_positive:
                return ("D", "底部建仓", signals)

        chan_score = float(chan.get("score", 0) or 0)
        if stage == "Distribution" and chan_score < -0.3:
            return ("B", "派发减仓", signals)
        if stage == "Markup" and wy_score > 0.5:
            return ("A", "主升持有", signals)
        return ("C", "震荡观望", signals)

    @staticmethod
    def _calc_ma60_dev(ctx: "RawContext") -> float:
        """从 ctx.kline 算 MA60 偏离"""
        kline = ctx.kline
        if len(kline) < 60:
            return 0.0
        closes = [r["close"] for r in kline[-60:]]
        ma60 = sum(closes) / len(closes)
        if ma60 <= 0:
            return 0.0
        return (kline[-1]["close"] - ma60) / ma60

    def _count_resonance(self, raw: dict, scene: str) -> int:
        """共振数: 多少策略 score > 0.2 (正向共振)"""
        count = 0
        for d in raw.values():
            if isinstance(d, dict) and float(d.get("score", 0) or 0) > 0.2:
                count += 1
        return count

    def _decide_action(self, scene: str, total: float, resonance_count: int, n_effective: int = 0, n_total: int = 1) -> str:
        """根据场景 + 总分 + 共振数, 输出行动建议

        v3.6.1 改: 不显示 total_score 数字 (fallback 0.2 误导, dump 缺失时总分无效)
        改显示 data_completeness (多少 strategy 跑成功, 0-100%)
        """
        if n_total > 0 and n_effective / n_total < 0.5:
            return f"⬜ 数据不足 (仅 {n_effective}/{n_total} 方法有效), 震荡观望"
        scene_actions = {
            "A": f"🟢 主升持有 ({resonance_count} 重共振)",
            "B": f"🔴 派发减仓 ({resonance_count} 重共振)",
            "C": f"⬜ 震荡观望 ({resonance_count} 重共振)",
            "D": f"🥇 大底建仓 ({resonance_count} 重共振)",
            "E": f"⚠️ 弱势规避 ({resonance_count} 重共振)",
        }
        return scene_actions.get(scene, f"未知 ({resonance_count} 重共振)")


# ============================================================
# 6. 模块级单例
# ============================================================

_engine = AnalysisEngine()


# ============================================================
# 7. 测试
# ============================================================

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python -m tools.analysis_engine 002028")
        sys.exit(1)

    code = sys.argv[1]
    from tools.data_store import DataStore
    ctx = DataStore.get_ctx(code)
    result = AnalysisEngine().analyze(ctx)

    print(f"📊 {result.code} {result.name} (¥{result.current_price})")
    print(f"   场景: {result.scene} ({result.scene_name})")
    print(f"   共振: {result.resonance_count} 重")
    print(f"   行动: {result.action}")
    print()
    print("   raw keys:", list(result.raw.keys()))
    if "wyckoff" in result.raw:
        print("   wyckoff score:", result.raw["wyckoff"].get("score"))
    if "chan" in result.raw:
        print("   chan score:", result.raw["chan"].get("score"))
