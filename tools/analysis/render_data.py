"""
render_data.py — 分析数据契约 (v1.0, 2026-07-21)

架构铁律 (三层分离):
  AnalysisEngine 层 = 纯计算 factor, 零网络请求
  ✅ 只从 DataStore.get_ctx() + AnalysisEngine.analyze() 构建
  ❌ 禁止 import requests / subprocess curl / fetch_all / 任何网络调用

设计目标:
  1. 强约束: 所有字段有/无都明确, 不会"有时候有有时候没"
  2. 可追踪: 每个数据源的状态 (OK/TIMEOUT/EMPTY/PARSE_FAIL/NET_*) 都记录
  3. 可计算: completeness_report() 一行告诉 LLM 哪些数据缺
  4. 可扩展: 后续加模型/算法只需要新加字段, 不破坏现有 schema
  5. 零依赖: 用标准库 dataclass, 不需要 pydantic

使用方式:
  from tools.storage.store import DataStore
  from tools.analysis.analysis_engine import AnalysisEngine
  from tools.analysis.render_data import RenderData

  ctx    = DataStore.get_ctx("002371")          # L1: 读数据
  result = AnalysisEngine().analyze(ctx)        # L2: 算分析
  data   = RenderData.from_result(ctx, result) # L3: 渲染容器
"""
from __future__ import annotations
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ============================================================
# 状态
# ============================================================


def _mech_four_questions(raw, signals_5) -> dict:
    """机械算投资四问基础分 (PE/ROE/EPS CAGR)"""
    try:
        pe = raw.get("pe_ttm") or 0
        eps_table = raw.get("eps_table") or []
        roe = eps_table[-1].get("ROE", 0) if eps_table and isinstance(eps_table[-1], dict) else 0
        # 龙头评分 0-14: 估值(3) + 盈利(4) + 成长(4) + 安全(3)
        score = 0
        if 0 < pe < 30: score += 3
        elif 0 < pe < 50: score += 2
        if roe > 25: score += 4
        elif roe > 15: score += 3
        elif roe > 8: score += 2
        # 成长: 用 EPS 隐含增速
        if len(eps_table) >= 2 and eps_table[0].get("EPS", 0) > 0 and eps_table[-1].get("EPS", 0) > 0:
            cagr = (eps_table[-1]["EPS"] / eps_table[0]["EPS"]) ** (1 / max(len(eps_table) - 1, 1)) - 1
            if cagr > 0.20: score += 4
            elif cagr > 0.10: score += 3
        # 安全
        if pe > 0: score += 3
        leader = min(14, max(0, score))
        return {
            "chokepoint": "⭐⭐⭐ (产业链中游, 转换环节, 机械占位)",
            "tam": "5 年 TAM 增长 50-100% (定性, 待 LLM 精调)",
            "leader_score": f"{leader}/14",
            "leader_reason": f"PE(TTM)={pe:.1f}, ROE={roe:.1f}%, 机械算基础分",
            "valuation": f"PE(TTM)={pe:.1f}, {'合理' if pe < 30 else '偏高' if pe < 50 else '过高'}",
            "verdict": "🥈 标准 (机械算基础分, LLM 待精调)",
        }
    except Exception:
        return None


def _mech_t_frame(raw) -> dict:
    """机械 T 框架占位 (v6.2 起: T 位置由 LLM 从外部源补, 不依赖本地 events.json)"""
    return {
        "T_position": "T-? (v6.2 占位, 待 LLM 查年报/新闻/公告补)",
        "phase": "🟡 待判定",
        "action": "待 mavis LLM 算 T 位置",
    }


def _mech_position_layer(signals_5, ma_table) -> dict:
    """三层仓位 (2026-09-09 改: 走纯缠论 1买/2买/3买 + 风控, 不走 5方法总分)

    signals_5 仍传入, 但 total_score 字段已无意义, 决策全部走缠论.
    真正的仓位建议见 /t-analyze 报告的 "三层仓位" section (走 factor_basic.compute_three_layer_position).
    本函数保留仅供向后兼容, 永远返回 None (不参与决策).
    """
    return None


def _mech_exit_signals(signals_5, ma_table) -> dict:
    """机械退场信号 (2026-09-09 改: 走纯缠论, 不走 5方法总分)

    真正的退出信号见 /t-analyze 报告的 "退出信号" section (走 factor_risk.compute_exit_signals).
    本函数保留仅供向后兼容, 永远返回 None (不参与决策).
    """
    return None


# ============================================================
# 工具函数 (2026-07-25 加)
# ============================================================

# fflow 单位校验阈值: 亿元单位不可能 > 1e6 (=100 万亿)
# 超过即单位错, 标 0 + warn (幂等修复: 不让错误数据流到下游)
_FFLOW_UNIT_MAX = 1e6


def _unit_safe(value, field_name: str = "fflow") -> float:
    """
    fflow 字段单位校验 (幂等修复: data_fetcher 之前 main_net 是万元,
    analysis_data 错当成亿用, 偏大 1e4 倍 — 显式校验拦截)

    任何 abs(val) > 1e6 视为单位错误, 返回 0 + 警告
    """
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if abs(v) > _FFLOW_UNIT_MAX:
        # 单位错 (常见: 万元被当亿), 幂等: 不抛错, 标 0 + warn
        import warnings
        warnings.warn(
            f"{field_name} 单位异常: abs({v}) > {_FFLOW_UNIT_MAX:.0e} 亿, 标 0",
            stacklevel=2,
        )
        return 0.0
    return v


def _dict_to_rows(value) -> list:
    """
    2026-07-25 加: 老 data 工具 存的 north_flow 格式是 dict-of-lists
    (e.g. {trade_date: [...], hgt: [...], ...}), 但 report_renderer 期望
    list[dict] (每条记录一个 dict). 自动转, 幂等.
    """
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or not value:
        return []
    keys = list(value.keys())
    n = max((len(v) for v in value.values() if hasattr(v, '__len__')), default=0)
    rows = []
    for i in range(n):
        row = {}
        for k in keys:
            v = value[k]
            row[k] = v[i] if i < len(v) else None
        rows.append(row)
    return rows


def _mech_monitor_triggers(signals_5, ma_table) -> dict:
    """机械监控触发点 (2026-09-09 改: 走纯缠论, 不走 5方法总分)

    真正的监控触发点见 /t-analyze 报告的 "监控触发点" section (走 factor_risk.compute_monitor_triggers).
    本函数保留仅供向后兼容, 永远返回 None (不参与决策).
    """
    return None


@dataclass
class DataStatus:
    """单个数据源的获取状态"""
    name: str
    status: str  # OK | TIMEOUT | EMPTY | PARSE_FAIL | NET_* | ALL_FAILED
    duration_ms: int = 0
    fallback_used: Optional[str] = None
    error_msg: Optional[str] = None

    @property
    def emoji(self) -> str:
        if self.status == "OK":
            return "✅"
        if self.status.startswith("NET_") or self.status == "ALL_FAILED":
            return "❌"
        if self.status == "TIMEOUT":
            return "⏱"
        if self.status == "EMPTY":
            return "📭"
        if self.status == "PARSE_FAIL":
            return "🔧"
        if self.status.startswith("HTTP_"):
            return "🚫"
        return "❓"


# ============================================================
# 子模型
# ============================================================

@dataclass
class EpsRow:
    year: str
    year_mark: str
    eps: float
    net_profit_yi: float
    revenue_yi: float
    roe: float


@dataclass
class KLineBar:
    trade_date: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float = 0.0  # 成交额 (元), 2026-07-24 加 (兼容 老 data 工具 字段)
    pct_chg: float = 0.0  # 日涨幅 (%), 2026-07-28 加 (tushare 预计算, 跟 WyckoffTradingAgent 1:1)


@dataclass
class FflowRow:
    date: str
    main_net: float
    small: float
    mid: float
    big: float
    super_big: float
    derived: bool = False  # True = OBV 派生 (Tushare 不可用时的兜底)


@dataclass
class MaRow:
    period: str
    value: float
    deviation: float


# ============================================================
# 主模型
# ============================================================

@dataclass
class RenderData:
    """单只标的的完整分析数据契约"""
    code: str = ""
    name: str = ""
    generate_time: datetime = field(default_factory=datetime.now)

    # ===== 核心 =====
    current_price: Optional[float] = None
    price_status: Optional[DataStatus] = None
    pe_ttm: Optional[float] = None
    shares_yi: Optional[float] = None
    market_cap_yi: Optional[float] = None

    # ===== 原始 K 线上下文（供历史回算/因子历史 section 用）=====
    ctx: Optional[object] = None   # RawContext，避免循环 import 用 object 类型标注

    eps_table: list[EpsRow] = field(default_factory=list)
    eps_status: Optional[DataStatus] = None

    kline: list[KLineBar] = field(default_factory=list)
    kline_status: Optional[DataStatus] = None

    ma_table: list[MaRow] = field(default_factory=list)
    ma_status: Optional[DataStatus] = None

    fflow_data: list[FflowRow] = field(default_factory=list)
    fflow_status: Optional[DataStatus] = None

    # ===== 技术指标 (8 个, 从 K线自动算) =====
    technical: Optional[dict] = None  # {macd, rsi, kdj, boll, atr, vol_ma, summary}
    technical_status: Optional[DataStatus] = None

    # ===== 自动评估器 (Phase 1 自动算, 不再是占位符) =====
    # 2026-09-09 改: 删 total_score 加权字段, 4 维独立 dict
    # 2026-09-09 删: fundamental 字段 (4 维基本面, 走 ValuationStrategy PEG+DCF+Magic)
    # fundamental: Optional[dict] = None
    # fundamental_status: Optional[DataStatus] = None


    # ===== 5 方法 × 3 周期 矩阵 (2026-07-24 固化) =====
    analysis: Optional[dict] = None  # v5.10.26+: 替代 signals_5method, AnalysisEngine 输出 dict

    # ===== 季度财务 (最近 2 季, 营收/净利/毛利率/ROE/EBIT) — render 用 =====
    quarterly_finance: list[dict] = field(default_factory=list)  # [{quarter, or_yoy, np_yoy, gm, roe, ebit_yi, revenue_yi, netprofit_yi}, ...]

    # 因子历史缓存 — 计算一次后由 render/_section_factor_history 复用，避免重复 analyze_history
    factor_history_rows: Optional[list] = field(default=None, repr=False)

    # v5.10.35: 9 个派生字段兼容层 (peg/dcf/exit_signals/...) — render 读 data.<字段> 现在自动从 analysis 拿
    # 之前 v5.10.34 前: 这 9 个字段从 dump 顶层读; 现在挪到 analysis 层
    # render 改读 data.analysis.<字段> 太散, 加 property 兼容老代码
    @property
    def peg(self) -> Optional[dict]:
        return (self.analysis or {}).get("peg")

    @property
    def dcf(self) -> Optional[dict]:
        return (self.analysis or {}).get("dcf")

    @property
    def five_categories(self) -> Optional[dict]:
        return (self.analysis or {}).get("five_categories")

    @property
    def buy_sell_points(self) -> Optional[dict]:
        # 多源合并: 1) analysis.buy_sell_points, 2) factor_scores.buy_sell_points.raw
        bsp = (self.analysis or {}).get("buy_sell_points")
        if bsp:
            return bsp
        # 兜底: 从 factor_scores.buy_sell_points 拿
        fs = (self.analysis or {}).get("factor_scores") or {}
        bsp_fc = fs.get("buy_sell_points") or {}
        raw = bsp_fc.get("raw") if isinstance(bsp_fc, dict) else None
        if raw and isinstance(raw, dict):
            return raw
        return None

    # 移植因子 (WyckoffTradingAgent → mavis, 2026-08)
    # 走 factor_scores 路径, to_dict() 已自动提升 raw 到顶层
    @property
    def position(self) -> dict:
        return (self.analysis or {}).get("position") or {}

    @property
    def exit_signals(self) -> Optional[dict]:
        return (self.analysis or {}).get("exit_signals")

    @property
    def stop_profit_loss(self) -> Optional[dict]:
        return (self.analysis or {}).get("stop_profit_loss")

    @property
    def three_layer_position(self) -> Optional[dict]:
        return (self.analysis or {}).get("three_layer_position")

    @property
    def monitor_triggers(self) -> Optional[dict]:
        return (self.analysis or {}).get("monitor_triggers")

    # v5.10.35: peg_detail/dcf_detail 优先用 analysis 层 factor 库算的真值
    @classmethod
    def _merge_peg_detail(cls, old_detail, analysis_dict):
        """合并 peg_detail: analysis.peg (factor 库) 真值优先"""
        factor_peg = (analysis_dict or {}).get("peg") or {}
        if not factor_peg or "PEG_真实" not in factor_peg or not isinstance(factor_peg.get("PEG_真实"), (int, float)):
            return old_detail
        # factor 库有真值, 转成 render 期望的 peg_detail 格式
        return {
            "price": old_detail.get("price") if old_detail else None,
            "E0": factor_peg.get("E0_本年"),
            "E1": factor_peg.get("E1_NTM"),
            "E2": factor_peg.get("E2"),
            "E3": factor_peg.get("E3"),
            "fwd_pe": factor_peg.get("Forward PE"),
            "g": str(factor_peg.get("g_CAGR", "—")).rstrip("%"),
            "peg": factor_peg.get("PEG_真实"),
            "verdict": factor_peg.get("PEG_判定", "—"),
        }

    @classmethod
    def _merge_dcf_detail(cls, old_detail, analysis_dict):
        """合并 dcf_detail: analysis.dcf (factor 库) 真值优先"""
        factor_dcf = (analysis_dict or {}).get("dcf") or {}
        if not factor_dcf or not any(k.startswith("r_") for k in factor_dcf):
            return old_detail
        # factor 库有真值, 转成 render 期望的 dcf_detail 格式
        # render 期望 L_r8/L_r10/L_r12 + L_E3_r8/10/12
        def _val(rate, key):
            d = factor_dcf.get(f"r_{rate}%") or {}
            return d.get(key) if isinstance(d, dict) else None
        return {
            "L_r8": _val(8, "L_隐含(亿)"),
            "L_r10": _val(10, "L_隐含(亿)"),
            "L_r12": _val(12, "L_隐含(亿)"),
            "L_E3_r8": _val(8, "L/E3(每share)"),
            "L_E3_r10": _val(10, "L/E3(每share)"),
            "L_E3_r12": _val(12, "L/E3(每share)"),
            "L_actual": old_detail.get("L_actual") if old_detail else "—",
            "L_achievable": old_detail.get("L_achievable") if old_detail else "—",
            "verdict": old_detail.get("verdict") if old_detail else "—",
        }

    signal_5cat: Optional[dict] = None  # {signals, raw_score, rating, missing}
    signal_5cat_status: Optional[DataStatus] = None

    # 2026-09-09 删: strategy 字段 (5 strategy 投票, 走缠论)
    # strategy: Optional[dict] = None
    # strategy_status: Optional[DataStatus] = None

    # ===== 扩展 (mavis LLM 算) =====
    chan_data: Optional[dict] = None
    chan_status: Optional[DataStatus] = None
    four_questions: Optional[dict] = None
    t_frame: Optional[dict] = None
    # 2026-09-02 合并: peg_detail / dcf_detail / magic_formula_detail → valuation_data 1 个
    valuation_data: Optional[dict] = None  # ValuationStrategy 输出 (PEG + DCF + Magic)
    signal_5cat: Optional[dict] = None
    xgboost_prob: Optional[float] = None
    supplement: Optional[dict] = None
    take_profit: Optional[dict] = None
    stop_loss: Optional[dict] = None
    exit_signals: Optional[dict] = None
    position_layer: Optional[dict] = None
    monitor_triggers: Optional[dict] = None

    # ===== Tushare 扩展数据 (幂等性: 存入 dump，不在 renderer 实时拉) =====
    ts_weekly: list = field(default_factory=list)       # 周线 K
    ts_monthly: list = field(default_factory=list)      # 月线 K
    ts_north_flow: list = field(default_factory=list)   # 北向资金
    ts_margin: list = field(default_factory=list)       # 融资融券
    ts_top_list: list = field(default_factory=list)     # 龙虎榜
    ts_dividend: list = field(default_factory=list)     # 分红
    ts_fina_rows: list = field(default_factory=list)    # 财务指标多期

    # ============================================================
    # 构造
    # ============================================================

    @staticmethod
    def _extract_quarterly_finance(ctx: "RawContext", valuation_data: "Optional[dict]" = None, n: int = 4) -> list[dict]:
        """2026-09-08 废弃: 逻辑已迁移到 FinanceStrategy。
        保留存根避免外部引用报错。实际数据来自 result.raw['finance']['quarterly']。
        """
        return []


    @classmethod
    def from_result(cls, ctx: "RawContext", result: "AnalysisResult") -> "RenderData":
        """从 RawContext (L1) + AnalysisResult (L2) 构造 RenderData (L3)。

        这是三层分离后的正确入口，不再自己跑 analysis。
        """
        from tools.analysis.analysis_engine import AnalysisResult as AR

        # K线相关辅助数据
        kline_raw = ctx.kline or []
        ma_table = []
        technical = None
        if kline_raw:
            closes = [bar["close"] for bar in kline_raw]
            current = closes[-1] if closes else 0
            for period, name in [(5, "MA5"), (20, "MA20"), (60, "MA60"), (120, "MA120")]:
                if len(closes) >= period:
                    ma = sum(closes[-period:]) / period
                    ma_table.append(MaRow(
                        period=name,
                        value=round(ma, 2),
                        deviation=round((current / ma - 1) * 100, 2),
                    ))
            # v6.2.8 改: 从 ctx.technical_result 拿 (TechnicalStrategy 已算, 含 series)
            # 避免 render 端再调 compute_indicators 二次计算
            technical = ctx.technical_result or {}
            if not technical:
                # 兜底: 如果 strategy 没跑 (kline_only=True), 临时算一次
                try:
                    from tools.storage.sources.eastmoney import compute_indicators
                    technical = compute_indicators(kline_raw)
                except Exception as e:
                    technical = {"error": str(e)}

        # EPS
        eps_raw = ctx.eps_table or []
        eps_table = []
        for r in eps_raw:
            try:
                eps_table.append(EpsRow(**{k: r[k] for k in EpsRow.__dataclass_fields__ if k in r}))
            except Exception:
                pass

        # analysis dict（L2 结果）
        signals_5 = result.to_dict(ctx)

        # Phase 2 派生字段 (render 路径一次性运行，engine 不再负责)
        from tools.analysis.analysis_engine import PHASE2_FUNCTIONS
        _chan_bsp = (signals_5.get("chan") or {}).get("buy_sell_points") or {}
        ctx._bsp_for_data = _chan_bsp  # _derive_buy_sell_points 需要
        for _fn in PHASE2_FUNCTIONS:
            _key = _fn.__name__.replace("_derive_", "")
            try:
                signals_5[_key] = _fn(ctx, signals_5)
            except Exception:
                pass

        # chan_data
        chan_raw = signals_5.get("chan", {})
        chan_data = None
        if chan_raw:
            chan_data = {
                "weekly": chan_raw.get("weekly", {}),
                "daily":  chan_raw.get("daily", {}),
                "beichi": {
                    "weekly": (lambda b: b.get("display", "") if isinstance(b, dict) else b)(
                        (chan_raw.get("weekly") or {}).get("beichi", "")),
                    "daily":  (lambda b: b.get("display", "") if isinstance(b, dict) else b)(
                        (chan_raw.get("daily")  or {}).get("beichi", "")),
                },
            }

        # 机械推导字段
        raw_for_mech = {
            "close":    ctx.current_price,
            "total_mv": ctx.market_cap_yi,
            "eps_table": eps_raw,
            "industry": ctx.industry,
            "code":     ctx.code,
            "name":     ctx.name,
        }

        return cls(
            code=ctx.code,
            name=ctx.name,
            current_price=ctx.current_price,
            price_status=DataStatus(name="price", status="OK" if ctx.current_price else "EMPTY"),
            pe_ttm=None,
            kline=[KLineBar(**{k: bar.get(k, 0) for k in KLineBar.__dataclass_fields__})
                   for bar in kline_raw],
            kline_status=DataStatus(name="kline", status="OK" if kline_raw else "EMPTY"),
            ma_table=ma_table,
            ma_status=DataStatus(name="ma", status="OK" if ma_table else "EMPTY"),
            eps_table=eps_table,
            eps_status=DataStatus(name="eps", status="OK" if eps_table else "EMPTY"),
            # 季度财务 (最近 4 季) — 直接从 FinanceStrategy 结果取
            quarterly_finance=(result.raw.get("finance") or {}).get("quarterly") or [],
            # fflow_data: 从 ctx.moneyflow 转 FflowRow
            # v6.2.5 改: ctx.moneyflow 来自 eastmoney.get_fund_flow, 字段是 main_net(万)/main_yi(亿)/small/mid/big/super_big
            # 之前误用 buy_lg_amount/sell_lg_amount 老字段 → MD 永远"无数据"
            fflow_data=[FflowRow(
                date=k.get("trade_date",""),
                main_net=float(k.get("main_net", 0)) / 1e8,  # 万 → 亿
                small=float(k.get("small", 0)) / 1e8,
                mid=float(k.get("mid", 0)) / 1e8,
                big=float(k.get("big", 0)) / 1e8,
                super_big=float(k.get("super_big", 0)) / 1e8,
                derived=False,
            ) for k in (ctx.moneyflow or [])],
            fflow_status=DataStatus(name="fflow", status="OK" if (ctx.moneyflow or []) else "EMPTY"),
            technical=technical,
            technical_status=DataStatus(name="technical",
                                        status="OK" if technical and "error" not in technical else "EMPTY"),
            analysis=signals_5,
            chan_data=chan_data,
            ctx=ctx,
            four_questions=_mech_four_questions(raw_for_mech, signals_5),
            t_frame=_mech_t_frame(raw_for_mech),
            position_layer=_mech_position_layer(signals_5, ma_table),
            exit_signals=_mech_exit_signals(signals_5, ma_table),
            monitor_triggers=_mech_monitor_triggers(signals_5, ma_table),
            market_cap_yi=ctx.market_cap_yi,
            # Magic Formula: 跟 PEG/DCF 一样, 在 from_result 里直接算, 不留 N/A
            # market_cap 入参单位"万" (跟 Tushare daily_basic 一致), 内部 / 1e4 转亿
            # 2026-09-02 改: 删 magic_formula_detail, 用 valuation_data 1 个字段
            valuation_data=cls._compute_valuation(ctx, result),
            # 2026-09-03 修 12 处 Phase2 降级: 7 个 schema 字段填值
            # _compute_phase2 返 dict 包含: fundamental / signal_5cat / strategy / sector_overheat / supplement / take_profit
            **cls._compute_phase2(ctx, result, signals_5, ma_table),
        )

    @classmethod
    def _compute_magic(cls, ctx: "RawContext") -> Optional[dict]:
        """旧 _compute_magic: 2026-09-02 保留兼容, 实际数据从 valuation_data 读。

        报告渲染层已经从 data.valuation_data 读 (合并 PEG+DCF+Magic)。
        这个方法保持存在但不再被 from_result 调用, 避免删了破坏向后兼容。
        """
        return None  # 已废弃, 用 _compute_valuation 替代

    @classmethod
    def _compute_valuation(cls, ctx: "RawContext", result) -> Optional[dict]:
        """从 AnalysisResult.raw["finance"] 拿 FinanceStrategy 算的 4 指标 (2026-09-08 改)"""
        try:
            if not result or not result.raw:
                return None
            return result.raw.get("finance", {}) or None
        except Exception:
            return None

    @classmethod
    def _compute_phase2(cls, ctx: "RawContext", result, signals_5, ma_table) -> dict:
        """算 7 个 Phase2 派生字段 (2026-09-03 修)

        之前 schema 字段在, from_result 没人调, 12 个 section 显示"❌ 未计算"。
        现在从 result.raw 里读 (Phase1 strategy 已算的) 或现场算 (Phase1 没算的)。
        """
        out: dict = {}
        raw = (result.raw if result and hasattr(result, "raw") else {}) or {}

        def _ok(name: str) -> DataStatus:
            return DataStatus(name=name, status="OK")

        # 2026-09-09 删: 基本面 (4 维) — 估值/盈利/增长/安全 评分
        # 走 缠论 + ValuationStrategy (PEG+DCF+Magic ROC/EY), 报告 section 整个删掉
        # 2) 5 类信号 — 5 个 strategy 信号聚合
        out["signal_5cat"] = cls._agg_5_categories(raw, signals_5, ctx)
        out["signal_5cat_status"] = _ok("5类信号")
        # 2026-09-09 删: 策略 (5 strategy 投票, 走缠论)
        # 4) 缠论补充 — 从 chan 取 (bsp + hub 已经算)
        out["supplement"] = cls._extract_chan_supplement(raw)
        # 6) 止盈 3 层 + 止损 4 档 (合在 take_profit 里, render 拆开)
        # v6.2.8 改: 业务规则抽到 tools/analysis/mech.py, render_data 0 计算
        from tools.analysis.mech import compute_stop_pl
        out["take_profit"] = compute_stop_pl(ctx.kline or [])

        return out

    @staticmethod
    def _agg_5_categories(raw: dict, signals_5, ctx) -> dict:
        """聚合 5 类信号 (量价/资金/龙头/政策/情绪) — 2026-09-09 删 total_score 加权

        决策走 缠论 1买/2买/3买/1卖/2卖/3卖 + 风控, 不再按 5 类信号加权汇总.
        signals 字段保留 (子信号独立展示), 删 score/raw_score/rating 加权字段.
        """
        signals: list[dict] = []

        # 1) 量价 (fflow + OBV, 子信号独立)
        fflow = raw.get("fflow", {})
        signals.append({
            "category": "量价", "name": "fflow_score", "score": 8 if fflow.get("score", 0) > 0 else 3,
            "triggered": fflow.get("score", 0) > 0,
            "reason": f"fflow 评分 {fflow.get('score', 0)}" if fflow.get("score", 0) > 0 else "fflow 评分 ≤0",
        })
        # 资金 (fflow 5 日分)
        fflow_5d = fflow.get("fflow_net_5d", 0)
        signals.append({
            "category": "资金", "name": "fflow_5d", "score": 7 if fflow_5d > 0 else 3,
            "triggered": fflow_5d > 0,
            "reason": f"5 日净流入 {fflow_5d:.2f}亿" if fflow_5d > 0 else "5 日净流出",
        })
        # 龙头/政策/情绪: 数据未接入, 标记
        for cat in ["龙头", "政策", "情绪"]:
            signals.append({
                "category": cat, "name": f"{cat}_neutral", "score": 5,
                "triggered": False, "reason": f"需 {cat} 数据 (暂未接入)",
            })
        # 估值: PEG (子信号独立)
        val = raw.get("finance", {})
        peg = val.get("PEG_真实")
        peg_score = 9 if isinstance(peg, (int, float)) and peg < 1.5 else 4
        signals.append({
            "category": "估值", "name": "PEG", "score": peg_score,
            "triggered": isinstance(peg, (int, float)) and peg < 1.5,
            "reason": f"PEG {peg} (<1.5 合理)" if isinstance(peg, (int, float)) and peg < 1.5 else f"PEG {peg or '—'} (偏贵)",
        })
        # 2026-09-09 删: score/raw_score/rating 加权汇总
        return {
            "signals": signals,
            "missing": ["龙头", "政策", "情绪"],
        }

    # 2026-09-09 删: _agg_strategy (5 strategy 投票, 67 行)
    # 原因: 决策走 缠论 1买/2买/3买/1卖/2卖/3卖, 不再单独跑 5 strategy 投票
    # 渲染端: 4 套交易策略 section 整个删掉 (走缠论, 走 TechnicalStrategy 8 个技术指标)

    @staticmethod
    @staticmethod
    def _extract_chan_supplement(raw: dict) -> dict:
        """缠论补充: 2 买 / 3 买 / 类二买 / 中枢突破"""
        bsp = (raw.get("buy_sell_points", {}) or {})
        out = {"daily": {}, "weekly": {}}
        for level in ("daily", "weekly"):
            pts = bsp.get(level, {}) or {}
            if not isinstance(pts, dict):
                continue
            for k, v in pts.items():
                if k == "action":
                    continue
                if "🟢2买" in str(k):
                    out[level]["2买"] = v
                elif "🟢3买" in str(k):
                    out[level]["3买"] = v
                elif "🟢双中枢" in str(k):
                    out[level]["双中枢"] = v
                elif "🟢笔结束" in str(k):
                    out[level]["笔结束"] = v
        return out

    # ============================================================
    # 完整性
    # ============================================================

    def completeness_report(self) -> dict[str, tuple[str, str]]:
        report = {}

        def line(key, status_obj, count_str):
            if not status_obj:
                report[key] = ("❓", "未计算")
            elif status_obj.status == "OK":
                report[key] = ("✅", f"{status_obj.status} ({count_str})")
            else:
                report[key] = (status_obj.emoji, status_obj.status)

        line("实时价", self.price_status, f"¥{self.current_price}" if self.current_price else "")
        line("K线", self.kline_status, f"{len(self.kline)} 条")
        line("MA", self.ma_status, f"{len(self.ma_table)} 条")
        line("技术指标", self.technical_status, "8 种 (MACD/RSI/KDJ/BOLL/ATR/量比)")
        # 2026-09-09 删: 基本面 status 行 (4 维基本面, 走 ValuationStrategy PEG+DCF+Magic)
        # 2026-09-09 改: 5类信号 删 raw_score/rating, 改子信号数
        s5_summary = f"{len(self.signal_5cat.get('signals', []))} 子信号" if self.signal_5cat else "—"
        line("5类信号", self.signal_5cat_status, s5_summary)
        # 2026-09-09 删: 策略 status 行 (走缠论, 不再单独展示 5 strategy 投票)
        # 2026-08-31: fflow section 已停用 (CLAUDE.md 板块适用性限制), 不展示
        line("EPS", self.eps_status, f"{len(self.eps_table)} 条")

        report["缠论"] = ("✅" if self.chan_data else "❓", "OK" if self.chan_data else "未计算")
        report["四问"] = ("✅" if self.four_questions else "❓", "OK" if self.four_questions else "未计算")
        report["T框架"] = ("✅" if self.t_frame else "❓", "OK" if self.t_frame else "未计算")
        # 2026-09-02 改: 合并 PEG/DCF/Magic 1 个 valuation_data
        # 2026-09-03 改: 区分"完全没字段"(❓ 未计算) vs "字段在但 None"(❌ 原因)
        _val = self.valuation_data or {}
        # PEG
        if "PEG_真实" in _val:
            peg_v = _val.get("PEG_真实")
            if peg_v is not None and peg_v != "数据不足":
                report["PEG"] = ("✅", f"={peg_v}")
            else:
                report["PEG"] = ("❌", f"={_val.get('verdict', '数据不足')}")
        else:
            report["PEG"] = ("❓", "未计算")
        # DCF
        if "L_r10" in _val:
            dcf_v = _val.get("L_r10")
            if dcf_v is not None:
                report["DCF L"] = ("✅", f"L={dcf_v}亿")
            else:
                report["DCF L"] = ("❌", "L=E0 缺/EPS 缺")
        else:
            report["DCF L"] = ("❓", "未计算")
        # Magic — 区分 roc/ey 是否在字段
        if "roc" in _val or "ey" in _val:
            roc_v, ey_v = _val.get("roc"), _val.get("ey")
            if roc_v is not None or ey_v is not None:
                report["Magic"] = ("✅", f"ROC={roc_v}% EY={ey_v}%")
            else:
                # 字段在但 None: 区分 industry_excluded / no_data
                reason = _val.get("skip_reason") or _val.get("period_label") or "数据缺"
                industry = _val.get("industry") or ""
                if reason == "industry_excluded":
                    report["Magic"] = ("❌", f"行业 {industry} 已排除")
                else:
                    report["Magic"] = ("❌", f"ROC/EY={reason}")
        else:
            report["Magic"] = ("❓", "未计算")
        report["5类信号"] = ("✅" if self.signal_5cat else "❓", "OK" if self.signal_5cat else "未计算")
        report["缠论补充"] = ("✅" if self.supplement else "❓", "OK" if self.supplement else "未计算")
        report["止盈止损"] = ("✅" if self.take_profit else "❓", "OK" if self.take_profit else "未计算")

        return report

    def completeness_pct(self) -> int:
        report = self.completeness_report()
        ok = sum(1 for emoji, _ in report.values() if emoji == "✅")
        return int(ok / len(report) * 100)

    def can_calc_peg(self) -> bool:
        return (
            self.current_price is not None
            and len(self.eps_table) > 0
            and any(r.year_mark in ("E", "A") and r.eps > 0 for r in self.eps_table)
        )

    def can_calc_dcf(self) -> bool:
        e_count = sum(1 for r in self.eps_table if r.year_mark == "E" and r.eps > 0)
        return e_count >= 2 and self.current_price is not None

    def can_calc_supplement(self) -> bool:
        return len(self.kline) >= 30
