"""
analysis_engine.py — analysis 层 (策略模式) (2026-07-30 v5.10.25)

**架构 (3 层)**:
- L1 dump 层: DataStore.get_ctx(code)
- L2 analysis 层 (本文件): AnalysisEngine.analyze(dump) → analysis dict
- L3 render 层: render_report(analysis, dump) → markdown

**策略模式**:
- AnalysisStrategy 抽象基类 (1 策略 = 1 因子 + 1 聚合规则)
- 6 个具体策略: WyckoffStrategy / SmcStrategy / VolumePriceStrategy / ResonanceStrategy / ChanStrategy / PegStrategy
- AnalysisEngine 调度器, 跑所有策略聚合 1 个 dump

**render 字段** (不存 dump, render 时算):
- factor_scores (6 个策略各自分数)
- total_score (加权聚合)
- scene (A 主升 / B 派发 / C 震荡 / D 底部 / E 弱势)
- resonance_count (多少策略一致)
- signals_active (当前激活的因子)
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ============================================================
# 1. 数据类 (dataclass, render 友好)
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

    # Phase1 Strategy 算完后写这里，Phase2 Strategy 直接读，不重算
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
        return RawContext(
            kline=k, weekly=w,
            eps_table=self.eps_table,
            fflow=self.fflow,
            moneyflow=self.moneyflow,
            current_price=price,
            market_cap_yi=self.market_cap_yi,
            code=self.code,
            name=self.name,
        )


@dataclass
class FactorScore:
    """1 个策略的分析结果"""
    name: str
    score: float  # -1.0 ~ +1.0
    weight: float  # 0.0 ~ 1.0
    signals: list[str] = field(default_factory=list)  # 激活的具体信号
    summary: str = ""  # 一句话描述
    raw: dict = field(default_factory=dict)  # 原始结果, render 用


@dataclass
class AnalysisResult:
    """1 个 dump 的完整 analysis 结果 (L2 → L3)"""
    code: str
    name: str
    current_price: float
    factor_scores: dict[str, FactorScore]  # name → FactorScore
    total_score: float = 0.0
    scene: str = "?"  # A/B/C/D/E
    scene_name: str = "未知"
    resonance_count: int = 0
    signals_active: list[str] = field(default_factory=list)  # 当前激活的所有因子
    action: str = ""  # 综合行动建议

    def to_dict(self, ctx: "RawContext | None" = None) -> dict:
        """序列化为 dict (给 AnalysisData / render 用)"""
        d = {
            "code": self.code,
            "name": self.name,
            "current_price": self.current_price,
            "factor_scores": {k: vars(v) for k, v in self.factor_scores.items()},
            "total_score": self.total_score,
            "scene": self.scene,
            "scene_name": self.scene_name,
            "resonance_count": self.resonance_count,
            "signals_active": self.signals_active,
            "action": self.action,
        }
        # 各 Strategy raw 输出提升到顶层（render 读 analysis.peg / analysis.chan 等）
        for k, fs in self.factor_scores.items():
            if fs.raw and isinstance(fs.raw, dict):
                d[k] = fs.raw

        # ctx 共享结果补充到顶层（5方法矩阵 / render 周期数据用）
        if ctx is not None:
            if ctx.wyckoff_result: d.setdefault("wyckoff",        ctx.wyckoff_result)
            if ctx.wyckoff_weekly: d.setdefault("wyckoff_weekly", ctx.wyckoff_weekly)
            if ctx.smc_result:
                d.setdefault("smc",        ctx.smc_result)
                d.setdefault("smc_weekly", ctx.smc_result.get("smc_weekly") or {})
            if ctx.fflow_result:   d.setdefault("fflow",           ctx.fflow_result)
            if ctx.obv_result:     d.setdefault("obv",             ctx.obv_result)
            if ctx.resonance_result: d.setdefault("resonance",    ctx.resonance_result)
            if ctx.chan_result:     d.setdefault("chan",           ctx.chan_result)
        return d


# ============================================================
# 2. 抽象策略 (Strategy Pattern)
# ============================================================

class AnalysisStrategy(ABC):
    """分析策略: 1 因子 → 1 聚合分数

    子类实现:
    - name (str): 因子名 (跟 dump['factor.<name>'] 1:1)
    - weight (float): 聚合权重 0-1
    - analyze(dump) → FactorScore
    """
    name: str = ""
    weight: float = 0.0

    @abstractmethod
    def analyze(self, dump: dict) -> FactorScore:
        """1 个 dump → 1 个 FactorScore"""
        raise NotImplementedError


# ============================================================
# 3. 6 个具体策略
# ============================================================

class WyckoffStrategy(AnalysisStrategy):
    """威科夫 3 阶段 → 分数，从 ctx.kline 直接重算（不读 dump['factor']）"""
    name = "wyckoff"
    weight = 0.20

    def analyze(self, ctx: RawContext) -> FactorScore:
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

        # 写回 ctx 供 Phase2 Strategy 使用
        ctx.wyckoff_result = wyckoff_3period.get("daily", {})
        ctx.wyckoff_weekly = wyckoff_3period.get("weekly", {})

        raw = {**daily_out, "sub_events_by_period": sub_events_by_period, "3period": wyckoff_3period}
        return FactorScore(
            name=self.name, score=score, weight=self.weight,
            signals=signals, summary=f"威科夫 {stage}", raw=raw,
        )


class MacdDivergenceStrategy(AnalysisStrategy):
    """MACD 底背驰 (前 30d 内) → 分数, 与 A→M + 缠论 底背驰组成三重确认
       底背驰定义: 价创新低 但 MACD hist 没创新低 (底部反转信号)
       权重 0.05 (辅助确认, 不主导总分)
    """
    name = "macd_div"
    weight = 0.05
    LOOKBACK = 30  # 30d 内有底背驰即可

    def _ema(self, arr, n):
        alpha = 2 / (n + 1)
        out = list(arr)
        for i in range(1, len(out)):
            out[i] = alpha * out[i] + (1 - alpha) * out[i-1]
        return out

    def _macd_hist(self, closes):
        if len(closes) < 35:
            return [0.0] * len(closes)
        ema12 = self._ema(closes, 12)
        ema26 = self._ema(closes, 26)
        diff = [a - b for a, b in zip(ema12, ema26)]
        dea = self._ema(diff, 9)
        return [2 * (d - e) for d, e in zip(diff, dea)]

    def _detect_bot_div(self, lows, hist, i_trigger, lookback=30):
        """检查 i_trigger 前 lookback 天内是否有底背驰"""
        if i_trigger < lookback + 5:
            return False, None
        win_lo = lows[i_trigger - lookback: i_trigger + 1]
        win_hi = hist[i_trigger - lookback: i_trigger + 1]
        cur_idx = win_lo.index(min(win_lo))
        if cur_idx < 3:
            return False, None
        prev_lo = win_lo[:cur_idx]
        if not prev_lo:
            return False, None
        prev_idx = prev_lo.index(min(prev_lo))
        # 底背驰: 价创新低 + hist 没新低
        if win_lo[cur_idx] < win_lo[prev_idx] and win_hi[cur_idx] > win_hi[prev_idx]:
            return True, {
                'cur_idx': cur_idx,
                'prev_idx': prev_idx,
                'price_drop_pct': (win_lo[cur_idx] / win_lo[prev_idx] - 1) * 100,
                'hist_rise': win_hi[cur_idx] - win_hi[prev_idx],
            }
        return False, None

    def analyze(self, ctx: RawContext) -> FactorScore:
        kline = ctx.kline
        if len(kline) < 60:
            return FactorScore(name=self.name, score=0.0, weight=self.weight,
                               signals=[], summary="MACD背驰 数据不足", raw={})

        closes = [k["close"] for k in kline]
        lows = [k["low"] for k in kline]
        dates = [k.get("trade_date", "") for k in kline]
        hist = self._macd_hist(closes)

        i_now = len(kline) - 1
        has_div, detail = self._detect_bot_div(lows, hist, i_now, self.LOOKBACK)

        signals = []
        if has_div:
            signals.append(f"MACD 底背驰 ({self.LOOKBACK}d内) 价{detail['price_drop_pct']:.1f}%/hist+{detail['hist_rise']:.2f}")
            score = 0.6
        else:
            score = 0.0

        ctx.macd_div_result = {
            'has_bot_div': has_div,
            'detail': detail,
            'lookback_days': self.LOOKBACK,
        }
        summary = "MACD底背驰 ✅" if has_div else "MACD底背驰 —"
        return FactorScore(name=self.name, score=score, weight=self.weight,
                           signals=signals, summary=summary,
                           raw=ctx.macd_div_result)


class SmcStrategy(AnalysisStrategy):
    """SMC (OB/FVG/Sweep) → 分数，日线 + 60分 + 周线三周期"""
    name = "smc"
    weight = 0.10

    def analyze(self, ctx: RawContext) -> FactorScore:
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

        smc_d  = _run(ctx.kline,     "daily")
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
        return FactorScore(
            name=self.name, score=score, weight=self.weight,
            signals=signals, summary=f"SMC OB={total_obs} 扫流={sweeps}", raw=smc,
        )


class FflowStrategy(AnalysisStrategy):
    """fflow (主力资金流) → 分数，从 ctx.moneyflow 直接计算

    2026-08-17 拆分: 跟 ObvStrategy 解耦, fflow 单独跑. 之前是 VolumePriceStrategy
    把 fflow + OBV 混在一起算, 现在 fflow 走 Tushare.money_flow (dump 预拉), OBV 走
    经典 Granville 1963 K线累计 (ObvStrategy), 各自独立 strategy.

    双判定同向/矛盾信号在这里算 (ObvStrategy 已先跑过, ctx.obv_result 有结果).
    """
    name = "fflow"
    weight = 0.10

    def analyze(self, ctx: RawContext) -> FactorScore:
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
        # ObvStrategy 先于 FflowStrategy 跑 (PHASE1_STRATEGIES 顺序), ctx.obv_result 已就绪
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
        return FactorScore(
            name=self.name, score=score, weight=self.weight,
            signals=signals, summary=summary, raw=ff,
        )


class ObvStrategy(AnalysisStrategy):
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
    weight = 0.10

    def analyze(self, ctx: RawContext) -> FactorScore:
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
        return FactorScore(
            name=self.name, score=score, weight=self.weight,
            signals=signals, summary=summary, raw=obv,
        )


class ChanStrategy(AnalysisStrategy):
    """缠论三级别 (中枢+背驰) → 分数，从 ctx.kline 直接计算"""
    name = "chan"
    weight = 0.20

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.chan.three_levels import build_chan_levels

        kline = ctx.kline

        chan = {}
        try:
            result = build_chan_levels(
                ctx.code, ctx.name,
                lambda c: kline,
            )
            if result:
                res_w, res_d, bc_w, bc_d = result
                chan = {
                    "weekly": {**(res_w or {}), "beichi": bc_w},
                    "daily":  {**(res_d or {}), "beichi": bc_d},
                }
        except Exception:
            pass
        ctx.chan_result = chan

        signals = []
        score_total = 0.0
        cnt = 0
        for level in ["weekly", "daily"]:
            d   = chan.get(level, {}) or {}
            pos = d.get("pos", "—") or (d.get("hub", {}) or {}).get("pos", "—")
            beichi = d.get("beichi", {})
            bc_dir = beichi.get("direction", "none") if isinstance(beichi, dict) else (
                "bot" if "底背" in str(beichi) else ("top" if "顶背" in str(beichi) else "none")
            )
            bc_str = beichi.get("strength", "none") if isinstance(beichi, dict) else (
                "strong" if any(x in str(beichi) for x in ["底背驰", "顶背驰"]) else
                "weak" if "弱背驰" in str(beichi) else "none"
            )
            if bc_dir == "bot" and bc_str in ("strong", "weak"):
                score_total += 1.0
                signals.append(f"{level} 底背")
                cnt += 1
            elif bc_dir == "top" and bc_str in ("strong", "weak"):
                score_total -= 1.0
                signals.append(f"{level} 顶背")
                cnt += 1
            elif "下方" in str(pos):
                score_total -= 0.3
                cnt += 1
            elif "上方" in str(pos):
                score_total += 0.3
                cnt += 1

        avg = score_total / cnt if cnt > 0 else 0.0
        return FactorScore(
            name=self.name, score=avg, weight=self.weight,
            signals=signals, summary=f"缠论 {cnt}/3 级别", raw=chan,
        )


class PegStrategy(AnalysisStrategy):
    """PEG 估值 → 分数"""
    name = "peg"
    weight = 0.15

    def analyze(self, ctx: RawContext) -> FactorScore:
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

        return FactorScore(
            name=self.name,
            score=score,
            weight=self.weight,
            signals=signals,
            summary=summary,
            raw=peg,
        )


class DcfStrategy(AnalysisStrategy):
    """DCF 折现 → 分数 (v5.10.34 新增)

    调 DcfFactor (3 档 r=8/10/12%), 返 L_隐含(亿) / L/E3
    """
    name = "dcf"
    weight = 0.0  # 估值类参考, 不计入 scene/total_score 加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.valuation.multi import DcfFactor
        dcf_factor = DcfFactor()
        dcf = dcf_factor(df=None, eps_table=ctx.eps_table,
                         current_price=ctx.current_price,
                         market_cap_yi=ctx.market_cap_yi) or {}
        signals = []
        score = 0.0
        summary = "—"
        # 拿 r_10% 当代表 (PEG 思路: 取中间档)
        # v5.10.75 修复: 之前阈值 [10, 20, 30] 跟 L/E3 实际范围 [0, 3+] 不匹配, 几乎所有票都拿正分
        # 正确逻辑: L/E3 > 1.0 表示市场隐含终局利润 > E3, 估值已透支 → 应该负分
        r10 = dcf.get("r_10%") or {}
        l_e3 = r10.get("L/E3(每share)", 0) if isinstance(r10, dict) else 0
        if l_e3 and isinstance(l_e3, (int, float)):
            if l_e3 < 0.8:
                score = +0.5  # 隐含 < 0.8x E3 = 市场比分析师更悲观 = 估值便宜
            elif l_e3 < 1.2:
                score = 0.0   # 接近合理
            elif l_e3 < 2.0:
                score = -0.5  # 隐含 1.2-2.0x = 偏乐观
            elif l_e3 < 3.0:
                score = -1.0  # 隐含 2-3x = 严重透支
            else:
                score = -1.0  # 隐含 > 3x = 极度透支 (柯力/绿的 L/E3 在这)
            signals.append(f"DCF L/E3={l_e3:.2f}")
            summary = f"DCF L/E3 {l_e3:.2f}"
        else:
            signals.append("DCF 数据不足")
            summary = "DCF 数据不足"

        return FactorScore(name=self.name, score=score, weight=self.weight,
                           signals=signals, summary=summary, raw=dcf)


class SectorOverheatStrategy(AnalysisStrategy):
    """板块过热预警 (v5.10.34 新增)

    调 SectorOverheatFactor, 1周/1月/3月涨幅 → 是否过热
    """
    name = "sector_overheat"
    weight = 0.0  # 预警类参考, 不计入加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        signals = []
        score = 0.0
        # 2026-08-06 修复: 之前 hardcode (-7/-22/-30) 永远是"板块弱势", 改成从 K线 真实算个股代理
        so = {}
        kline = ctx.kline or []
        if len(kline) >= 64:
            closes = [k["close"] for k in kline if "close" in k]
            if len(closes) >= 64:
                pct_1w = (closes[-1] / closes[-6] - 1) * 100
                pct_1m = (closes[-1] / closes[-22] - 1) * 100
                pct_3m = (closes[-1] / closes[-64] - 1) * 100
                so = {
                    "1周涨幅": f"{pct_1w:+.1f}%",
                    "1月涨幅": f"{pct_1m:+.1f}%",
                    "3月涨幅": f"{pct_3m:+.1f}%",
                    "source": "个股 K线代理 (industry={})".format(ctx.industry or "未知"),
                    "_warning": "sector_overheat 来自个股 K线代理, 不是真实板块指数",
                }
        if not so:
            return FactorScore(name=self.name, score=0, weight=self.weight,
                               signals=["板块 K线数据不足"], summary="数据不足", raw={})
        # 判定逻辑: 3月跌幅 > 20% = 板块弱势 (减分)
        pct_3m = so.get("3月涨幅", "—")
        try:
            pct_3m_f = float(str(pct_3m).rstrip("%")) if pct_3m != "—" else 0
        except ValueError:
            pct_3m_f = 0
        if pct_3m_f < -20:
            score = -0.5
            signals.append(f"板块 3 月跌 {pct_3m_f:.0f}%")
            summary = f"板块弱势 ({pct_3m_f:.0f}%)"
        elif pct_3m_f > 30:
            score = 0.3
            signals.append(f"板块 3 月涨 {pct_3m_f:.0f}%")
            summary = f"板块过热 ({pct_3m_f:.0f}%)"
        else:
            summary = f"板块正常 ({pct_3m_f:.0f}%)"

        return FactorScore(name=self.name, score=score, weight=self.weight,
                           signals=signals, summary=summary, raw=so)


class FiveCategoriesStrategy(AnalysisStrategy):
    """5 类 14 子信号 (v5.10.34 新增)

    调 FiveCategoriesFactor (缠论+止跌+fflow+估值 5 类综合)
    """
    name = "five_categories"
    weight = 0.0  # 综合类参考, 不计入加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.valuation.multi import FiveCategoriesFactor
        fc_factor = FiveCategoriesFactor()
        fc = fc_factor(df=None, fflow=ctx.fflow,
                       eps_table=ctx.eps_table,
                       current_price=ctx.current_price) or {}
        score = 0.0
        signals = []
        summary = "—"
        # score 在 0 附近震荡, 1=强触发, -1=减仓
        fc_score = fc.get("score", 0) if isinstance(fc, dict) else 0
        if isinstance(fc_score, (int, float)):
            if fc_score > 0.5:
                score = 0.5
            elif fc_score > 0:
                score = 0.2
            elif fc_score > -0.5:
                score = -0.2
            else:
                score = -0.5
            signals.append(f"5 类 score {fc_score}")
            summary = f"5 类 {fc.get('verdict', '—')}"
        else:
            summary = "5 类 数据不足"
            signals.append("5 类 数据不足")

        return FactorScore(name=self.name, score=score, weight=self.weight,
                           signals=signals, summary=summary, raw=fc)


class BuySellPointsStrategy(AnalysisStrategy):
    """缠论 1买/1卖/2买/3买 (v5.10.34 新增)

    调 BuySellPointsFactor, 3 级别 (周/日/60分) 缠论 9 个买卖点
    """
    name = "buy_sell_points"
    weight = 0.0  # 缠论买卖点参考, 不计入加权 (跟 chan factor 重复)

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.registry import FactorRegistry
        reg = FactorRegistry()
        bsp_factor = reg.get("buy_sell_points")
        if bsp_factor is None:
            return FactorScore(name=self.name, score=0.0, weight=self.weight,
                               signals=["BuySellPointsFactor 未注册"], summary="—", raw={})

        # 读 ctx.chan_result（由 ChanStrategy Phase1 算好写入）
        chan = ctx.chan_result
        bsp = {}
        for level_key in ["weekly", "daily"]:
            res = chan.get(level_key) or {}
            beichi_str = res.get("beichi", "")
            klines = ctx.kline if level_key == "daily" else None
            try:
                out = bsp_factor(df=None, res=res, beichi_str=beichi_str,
                                 klines=klines, level_key=level_key)
                bsp[level_key] = out.get("points", {}) if isinstance(out, dict) else {}
            except Exception as e:
                bsp[level_key] = {"error": str(e)}

        # 简化 score: 1买=+0.5, 1卖=-0.5
        score = 0.0
        signals = []
        for level, points in bsp.items():
            if not isinstance(points, dict) or "error" in points:
                continue
            if points.get("🟢1买") not in (None, "—", ""):
                score += 0.3
                signals.append(f"{level} 1买")
            if points.get("🟢1买⭐") not in (None, "—", ""):
                score += 0.4
                signals.append(f"{level} 1买⭐")
            if points.get("🔴1卖") not in (None, "—", ""):
                score -= 0.3
                signals.append(f"{level} 1卖")
        score = max(-1.0, min(1.0, score))
        summary = f"缠论 1买/1卖 {len(signals)} 个"
        return FactorScore(name=self.name, score=score, weight=self.weight,
                           signals=signals, summary=summary, raw=bsp)


class ExitSignalsStrategy(AnalysisStrategy):
    """退场信号 9 项 (v5.10.34 新增)

    调 ExitSignalsFactor (PEG + L_E3 + MA120 + 板块 + 缠论 综合)
    """
    name = "exit_signals"
    weight = 0.0  # 退场信号参考, 不计入加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.registry import FactorRegistry
        reg = FactorRegistry()
        exit_factor = reg.get("exit_signals")
        if exit_factor is None:
            return FactorScore(name=self.name, score=0.0, weight=self.weight,
                               signals=["ExitSignalsFactor 未注册"], summary="—", raw={})
        try:
            out = exit_factor(df=None, fflow=ctx.fflow,
                              eps_table=ctx.eps_table,
                              current_price=ctx.current_price,
                              sector_ma20_dev=-22,
                              chan_signals=ctx.chan_result)
        except Exception as e:
            out = {"error": str(e)}

        signals = []
        score = 0.0
        summary = "—"
        # 退场信号: 绿信号多=hold (0), 红信号多=exit (-0.5)
        if isinstance(out, dict) and "error" not in out:
            green = out.get("绿信号数", 0)
            red = out.get("红信号数", 0)
            score = max(-1.0, min(1.0, (green - red) * 0.15))
            signals.append(f"绿 {green} / 红 {red}")
            summary = f"{out.get('综合', '—')}"
        else:
            signals.append("退场信号 计算失败")
            summary = "—"

        return FactorScore(name=self.name, score=score, weight=self.weight,
                           signals=signals, summary=summary, raw=out if isinstance(out, dict) else {})


class StopProfitLossStrategy(AnalysisStrategy):
    """止盈 3 层 + 止损 4 档 (v5.10.34 新增)

    调 StopProfitLossFactor (用中枢上下沿, 不再纯价格)
    """
    name = "stop_profit_loss"
    weight = 0.0  # 风控类参考, 不计入加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.registry import FactorRegistry
        reg = FactorRegistry()
        spl_factor = reg.get("stop_profit_loss")
        if spl_factor is None:
            return FactorScore(name=self.name, score=0.0, weight=self.weight,
                               signals=["StopProfitLossFactor 未注册"], summary="—", raw={})
        try:
            out = spl_factor(df=None, price=ctx.current_price,
                             factor=ctx.chan_result)
        except Exception as e:
            out = {"error": str(e)}

        signals = []
        summary = "—"
        if isinstance(out, dict) and "error" not in out:
            tp3 = out.get("止盈3层", []) or []
            sl4 = out.get("止损4档", []) or []
            signals.append(f"止盈 {len(tp3)} 层 / 止损 {len(sl4)} 档")
            summary = f"止盈 {len(tp3)} 层 / 止损 {len(sl4)} 档"
        else:
            signals.append("止盈止损 计算失败")
        return FactorScore(name=self.name, score=0.0, weight=self.weight,
                           signals=signals, summary=summary, raw=out if isinstance(out, dict) else {})


class ThreeLayerPositionStrategy(AnalysisStrategy):
    """三层仓位策略 (v5.10.34 新增)

    调 ThreeLayerPositionFactor (基于日线中枢+缠论+fflow+PEG)
    """
    name = "three_layer_position"
    weight = 0.0  # 仓位类参考, 不计入加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.registry import FactorRegistry
        reg = FactorRegistry()
        pos_factor = reg.get("three_layer_position")
        if pos_factor is None:
            return FactorScore(name=self.name, score=0.0, weight=self.weight,
                               signals=["ThreeLayerPositionFactor 未注册"], summary="—", raw={})
        try:
            res_d = ctx.chan_result.get("daily") or {}
            out = pos_factor(df=None, price=ctx.current_price, chan_d=res_d,
                             fflow=ctx.fflow, peg=0.76,
                             factor=ctx.chan_result)
        except Exception as e:
            out = {"error": str(e)}

        signals = []
        summary = "—"
        if isinstance(out, dict) and "error" not in out:
            signals.append(f"位置: {out.get('位置', '—')}")
            signals.append(f"市况: {out.get('市况', '—')}")
            summary = f"{out.get('市况', '—')} / {out.get('位置', '—')}"
        else:
            signals.append("三层仓位 计算失败")
        return FactorScore(name=self.name, score=0.0, weight=self.weight,
                           signals=signals, summary=summary, raw=out if isinstance(out, dict) else {})


class MonitorTriggersStrategy(AnalysisStrategy):
    """监控触发点 5 类 14 子 (v5.10.34 新增)

    调 MonitorTriggersFactor (缠论背驰+止跌+fflow+事件)
    """
    name = "monitor_triggers"
    weight = 0.0  # 监控类参考, 不计入加权

    def analyze(self, ctx: RawContext) -> FactorScore:
        from tools.factors.registry import FactorRegistry
        reg = FactorRegistry()
        mon_factor = reg.get("monitor_triggers")
        if mon_factor is None:
            return FactorScore(name=self.name, score=0.0, weight=self.weight,
                               signals=["MonitorTriggersFactor 未注册"], summary="—", raw={})
        try:
            res_d = ctx.chan_result.get("daily") or {}
            out = mon_factor(df=None, price=ctx.current_price, chan_d=res_d,
                             fflow=ctx.fflow, events=[],
                             factor=ctx.chan_result)
        except Exception as e:
            out = {"error": str(e)}

        signals = []
        summary = "—"
        if isinstance(out, dict) and "error" not in out:
            triggered = sum(1 for v in out.values() if isinstance(v, dict) and v.get("已触发"))
            signals.append(f"触发 {triggered} 项")
            summary = f"5 类 14 子触发 {triggered} 项"
        else:
            signals.append("监控触发 计算失败")
        return FactorScore(name=self.name, score=0.0, weight=self.weight,
                           signals=signals, summary=summary, raw=out if isinstance(out, dict) else {})


# ============================================================
# 4. 调度器 (AnalysisEngine)
# ============================================================

# Phase1：基础因子，互不依赖，结果写入 ctx 供 Phase2 读
PHASE1_STRATEGIES: list[type[AnalysisStrategy]] = [
    ChanStrategy,           # 0.20
    WyckoffStrategy,        # 0.20
    SmcStrategy,            # 0.10
    ObvStrategy,            # 0.10
    FflowStrategy,          # 0.10
    PegStrategy,            # 0.15
    MacdDivergenceStrategy, # 0.05
]

# Phase2：依赖 Phase1 的 ctx 共享结果，不重算
PHASE2_STRATEGIES: list[type[AnalysisStrategy]] = [
    DcfStrategy,                 # 0.0
    SectorOverheatStrategy,      # 0.0
    FiveCategoriesStrategy,      # 0.0
    BuySellPointsStrategy,       # 0.0  读 ctx.chan_result
    ExitSignalsStrategy,         # 0.0  读 ctx.chan_result
    StopProfitLossStrategy,      # 0.0  读 ctx.chan_result
    ThreeLayerPositionStrategy,  # 0.0  读 ctx.chan_result
    MonitorTriggersStrategy,     # 0.0  读 ctx.chan_result
]

DEFAULT_STRATEGIES = PHASE1_STRATEGIES + PHASE2_STRATEGIES


class AnalysisEngine:
    """RawContext → AnalysisResult

    唯一的 factor 计算入口。Strategy 从 ctx 读原始 K 线计算，不读 dump['factor']。
    as_of_date 切片在 Engine 层统一做，Strategy 无需感知时间。
    """

    def __init__(self, strategies: list[type[AnalysisStrategy]] | None = None):
        all_s = strategies or DEFAULT_STRATEGIES
        self._phase1 = [s() for s in all_s if s in PHASE1_STRATEGIES or
                        (strategies and s not in PHASE2_STRATEGIES)]
        self._phase2 = [s() for s in all_s if s in PHASE2_STRATEGIES]
        self._instances = self._phase1 + self._phase2

    def analyze(self, ctx: "RawContext", as_of_date: str | None = None) -> "AnalysisResult":
        """核心入口: RawContext → AnalysisResult

        Args:
            ctx:         RawContext 实例
            as_of_date:  "2026-07-01" 格式，传入时对 K 线做截断（回测用）
        """
        # 统一切片，Strategy 收到的永远是已截断的 ctx
        if as_of_date:
            ctx = ctx.slice(as_of_date)

        code  = ctx.code
        name  = ctx.name
        price = ctx.current_price

        # Phase1：基础因子，结果写回 ctx
        factor_scores: dict[str, FactorScore] = {}
        for inst in self._phase1:
            try:
                fs = inst.analyze(ctx)
                factor_scores[inst.name] = fs
            except Exception as e:
                factor_scores[inst.name] = FactorScore(
                    name=inst.name, score=0.0, weight=inst.weight,
                    signals=[f"❌ {type(e).__name__}: {str(e)[:80]}"],
                    summary=f"策略失败: {str(e)[:100]}",
                )

        # Phase2：读 ctx 共享结果
        for inst in self._phase2:
            try:
                fs = inst.analyze(ctx)
                factor_scores[inst.name] = fs
            except Exception as e:
                factor_scores[inst.name] = FactorScore(
                    name=inst.name, score=0.0, weight=inst.weight,
                    signals=[f"❌ {type(e).__name__}: {str(e)[:80]}"],
                    summary=f"策略失败: {str(e)[:100]}",
                )

        # 2. 加权聚合 total_score
        total = 0.0
        for inst in self._instances:
            fs = factor_scores[inst.name]
            total += fs.score * inst.weight

        # 3. 场景判定 (5 选 1 互斥, 严格化 D 真大底)
        scene, scene_name, signals_active = self._decide_scene(factor_scores, ctx)

        # 4. 共振数 (多少策略同向)
        resonance_count = self._count_resonance(factor_scores, scene)

        # 5. 行动建议 (previously step 7)
        action = self._decide_action(scene, total, resonance_count)

        # 移植因子 (WyckoffTradingAgent → mavis-quant-agent, 2026-08)
        # 价格位置 (close_pos_day / close_pos_20 / upper_shadow), 纯 OHLCV 算
        # 走 factor_scores 路径, 跟 chan/wyckoff/smc 一致, _extract_row 直接读 raw
        try:
            from tools.factors.price.position import PricePositionFactor
            pos_raw = PricePositionFactor()(ctx.kline)
            # position 返回 4 个 Series, 转成 dict{key: 最新值}
            pos_last = {
                k: float(v.iloc[-1])
                for k, v in pos_raw.items()
                if hasattr(v, "iloc") and len(v) > 0
            }
            factor_scores["position"] = FactorScore(
                name="position", score=0.0, weight=0.0,
                signals=[], raw=pos_last,
            )
        except Exception as e:
            factor_scores["position"] = FactorScore(
                name="position", score=0.0, weight=0.0,
                signals=[f"❌ {type(e).__name__}"], raw={"error": str(e)[:100]},
            )

        return AnalysisResult(
            code=code,
            name=name,
            current_price=price,
            factor_scores=factor_scores,
            total_score=round(total, 2),
            scene=scene,
            scene_name=scene_name,
            resonance_count=resonance_count,
            signals_active=signals_active,
            action=action,
        )

    # --- 内部: 场景判定 / 共振 / 行动 ---

    def _decide_scene(self, fs: dict[str, FactorScore], ctx: "RawContext") -> tuple[str, str, list[str]]:
        """场景判定 A/B/C/D/E (5 选 1 互斥)"""
        wy = fs.get("wyckoff")
        chan = fs.get("chan")
        signals = []
        for name, f in fs.items():
            for s in f.signals:
                signals.append(f"{name}: {s}")

        stage    = wy.raw.get("stage", "?") if wy else "?"
        wy_score = wy.score if wy else 0.0
        # 2026-08-17 拆分: 量价拆成 fflow + obv 两个 strategy, scene 判定用两个分数的较小值 (任一出货都算弱)
        fflow = fs.get("fflow")
        obv   = fs.get("obv")
        fflow_score = fflow.score if fflow else 0.0
        obv_score   = obv.score   if obv   else 0.0
        vp_score    = min(fflow_score, obv_score) if (fflow or obv) else 0.0

        if vp_score < -0.5 and wy_score <= 0:
            return ("E", "弱势", signals)

        if stage == "Accumulation":
            ma60_dev = self._calc_ma60_dev(ctx)
            chan_res  = ctx.chan_result
            bc_d = (chan_res.get("daily") or {}).get("beichi", {})
            bot_d = (bc_d.get("direction") == "bot" and bc_d.get("strength") in ("strong", "weak")) \
                     if isinstance(bc_d, dict) else "底背" in str(bc_d)
            daily_pos = str((chan_res.get("daily") or {}).get("hub", {}).get("pos", "") or "")
            hub_below = any(t in daily_pos for t in ["下方", "跌穿"])
            # 共振 1d 从 ctx.resonance_result 读
            res_1d_positive = (ctx.resonance_result.get("1d") or {}).get("score", 0) > 0

            if ma60_dev < -0.05 and (bot_d or hub_below) and res_1d_positive:
                return ("D", "底部建仓", signals)

        chan_score = chan.score if chan else 0.0
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

    def _count_resonance(self, fs: dict[str, FactorScore], scene: str) -> int:
        """共振数: 多少策略跟场景方向一致"""
        # 简化: 算 score > 0 的策略数 (正向共振)
        # 真实可以按 scene 算 (A 场景: 多少策略说"买")
        count = 0
        for f in fs.values():
            if f.score > 0.2:
                count += 1
        return count

    def _decide_action(self, scene: str, total: float, resonance_count: int) -> str:
        """根据场景 + 总分 + 共振数, 输出行动建议"""
        scene_actions = {
            "A": f"🟢 主升持有 (总分 {total:+.1f}, {resonance_count} 重共振)",
            "B": f"🔴 派发减仓 (总分 {total:+.1f}, {resonance_count} 重共振)",
            "C": f"⬜ 震荡观望 (总分 {total:+.1f}, {resonance_count} 重共振)",
            "D": f"🥇 大底建仓 (总分 {total:+.1f}, {resonance_count} 重共振)",
            "E": f"⚠️ 弱势规避 (总分 {total:+.1f}, {resonance_count} 重共振)",
        }
        return scene_actions.get(scene, f"未知 (总分 {total:+.1f})")


# ============================================================
# 5. 便捷函数 (util)
# ============================================================

_engine = AnalysisEngine()


# ============================================================
# 6. 测试
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
    print(f"   总分: {result.total_score:+.2f}")
    print(f"   共振: {result.resonance_count} 重")
    print(f"   行动: {result.action}")
    print()
    print("   6 因子分数:")
    for name, fs in result.factor_scores.items():
        bar = "🟢" if fs.score > 0.2 else ("🔴" if fs.score < -0.2 else "⬜")
        print(f"     {bar} {name:14s} {fs.score:+.2f} ×{fs.weight:.2f} = {fs.score * fs.weight:+.2f}  {fs.summary}")
    print()
