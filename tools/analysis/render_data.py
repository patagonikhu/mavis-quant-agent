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
    """5 方法总分 → 三层仓位"""
    try:
        score = (signals_5 or {}).get("total_score", 0)
        if score >= 4:
            return {
                "底仓": "25% (日线中枢下沿止损)",
                "中仓": "20% (日线中枢站稳加)",
                "波动仓": "15% (60分底背驰加)",
                "summary": "🥇 5方法总分 ≥ 4 → 三层全开",
            }
        if score >= 3:
            return {
                "底仓": "20% (日线中枢下沿止损)",
                "中仓": "10% (日线中枢站稳加)",
                "波动仓": "0%",
                "summary": "🥈 5方法总分 ≥ 3 → 底+中仓",
            }
        if score >= 1.5:
            return {
                "底仓": "10% (轻仓试探)",
                "中仓": "0%",
                "波动仓": "0%",
                "summary": "🟡 5方法总分 ≥ 1.5 → 观察仓",
            }
        return {
            "底仓": "0%",
            "中仓": "0%",
            "波动仓": "0%",
            "summary": "❌ 5方法总分 < 1.5 → 不建仓",
        }
    except Exception:
        return None


def _mech_exit_signals(signals_5, ma_table) -> dict:
    """机械退场信号 (5方法总分 + MA20 偏离)"""
    try:
        score = (signals_5 or {}).get("total_score", 0)
        ma20_dev = ma_table[1].deviation if len(ma_table) > 1 else 0
        triggers = []
        if score < 1.5:
            triggers.append(f"5方法总分 < 1.5 (当前 {score:.1f})")
        if ma20_dev > 30:
            triggers.append(f"MA20 偏离 > 30% (当前 {ma20_dev:.1f}%)")
        if not triggers:
            return {
                "triggers": "✅ 无清仓信号",
                "summary": "✅ 安全持有",
            }
        return {
            "triggers": "🟠 " + " / ".join(triggers),
            "summary": "🟠 部分减仓 1/3",
        }
    except Exception:
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
    """机械监控触发点"""
    try:
        score = (signals_5 or {}).get("total_score", 0)
        return {
            "加仓信号": f"5方法总分 > 6.0 (当前 {score:.1f})" if score < 6 else f"已加仓 ({score:.1f})",
            "减仓信号": f"5方法总分 < 1.5 (当前 {score:.1f})" if score > 1.5 else f"已减仓 ({score:.1f})",
            "清仓信号": "fflow 5日主力净流出 > 10亿 / OBV 顶背离 / MA20 偏离 > 30%",
        }
    except Exception:
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
    fundamental: Optional[dict] = None  # {valuation, profitability, growth, safety, total_score}
    fundamental_status: Optional[DataStatus] = None

    # ===== 5 方法 × 3 周期 矩阵 (2026-07-24 固化) =====
    analysis: Optional[dict] = None  # v5.10.26+: 替代 signals_5method, AnalysisEngine 输出 dict

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
    def sector_overheat(self) -> Optional[dict]:
        return (self.analysis or {}).get("sector_overheat")

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

    strategy: Optional[dict] = None  # {strategies, buy_count, sell_count, verdict}
    strategy_status: Optional[DataStatus] = None

    # ===== 扩展 (mavis LLM 算) =====
    chan_data: Optional[dict] = None
    chan_status: Optional[DataStatus] = None
    four_questions: Optional[dict] = None
    t_frame: Optional[dict] = None
    # 2026-09-02 合并: peg_detail / dcf_detail / magic_formula_detail → valuation_data 1 个
    valuation_data: Optional[dict] = None  # ValuationStrategy 输出 (PEG + DCF + Magic)
    signal_5cat: Optional[dict] = None
    xgboost_prob: Optional[float] = None
    sector_overheat: Optional[dict] = None
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
            # fflow_data: 从 ctx.moneyflow (Tushare money_flow 原始 list) 转 FflowRow
            # 之前漏了, 一直空, MD 永远显示"❌ fflow 未拉取"
            fflow_data=[FflowRow(
                date=k.get("trade_date",""),
                main_net=float(k.get("net_mf_amount", 0)) / 1e8,  # 元 → 亿
                small=float(k.get("buy_sm_amount", 0) - k.get("sell_sm_amount", 0)) / 1e8,
                mid=float(k.get("buy_md_amount", 0) - k.get("sell_md_amount", 0)) / 1e8,
                big=float(k.get("buy_lg_amount", 0) - k.get("sell_lg_amount", 0)) / 1e8,
                super_big=float(k.get("buy_elg_amount", 0) - k.get("sell_elg_amount", 0)) / 1e8,
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
        """从 AnalysisResult.raw["valuation"] 拿 ValuationStrategy 算的 4 指标 (2026-09-02 新)

        返回: {PEG_真实, fwd_pe, g, verdict, L_r8/r10/r12, L_E3_r10, roc, ey, ev_yi,
                period_label, seasonal_warning, magic_ey_series, magic_roc_series}
        """
        try:
            if not result or not result.raw:
                return None
            return result.raw.get("valuation", {}) or None
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

        # 1) 基本面 (4 维) — 估值/盈利/增长/安全 评分
        val = raw.get("valuation", {}) or {}
        out["fundamental"] = cls._compute_fundamental_4d(ctx, raw, val)
        out["fundamental_status"] = _ok("基本面")
        # 2) 5 类信号 — 5 个 strategy 信号聚合
        out["signal_5cat"] = cls._agg_5_categories(raw, signals_5, ctx)
        out["signal_5cat_status"] = _ok("5类信号")
        # 3) 策略 — 综合 5 strategy 给出买/卖/观望
        out["strategy"] = cls._agg_strategy(raw, signals_5)
        out["strategy_status"] = _ok("策略")
        # 4) 板块过热 — 简单算: 1 周 / 1 月 / 3 月涨幅 (个股代理)
        out["sector_overheat"] = cls._compute_sector_overheat(ctx, raw)
        # 5) 缠论补充 — 从 chan 取 (bsp + hub 已经算)
        out["supplement"] = cls._extract_chan_supplement(raw)
        # 6) 止盈 3 层 + 止损 4 档 (合在 take_profit 里, render 拆开)
        out["take_profit"] = cls._compute_stop_pl(ctx, raw)

        return out

    @staticmethod
    def _agg_5_categories(raw: dict, signals_5, ctx) -> dict:
        """聚合 5 类信号 (量价/资金/龙头/政策/情绪) — Renderer 期望 list[dict]"""
        total_score = 0
        signals: list[dict] = []

        # 1) 量价 (fflow + OBV)
        fflow = raw.get("fflow", {})
        if fflow.get("score", 0) > 0:
            signals.append({
                "category": "量价", "name": "fflow_score", "score": 8, "weight": 1,
                "triggered": True, "reason": f"fflow 评分 {fflow.get('score', 0)}",
            })
            total_score += 8
        else:
            signals.append({
                "category": "量价", "name": "fflow_score", "score": 3, "weight": 1,
                "triggered": False, "reason": "fflow 评分 ≤0",
            })
            total_score += 3
        # 资金 (fflow 5 日分)
        if fflow.get("fflow_net_5d", 0) > 0:
            signals.append({
                "category": "资金", "name": "fflow_5d", "score": 7, "weight": 1,
                "triggered": True, "reason": f"5 日净流入 {fflow.get('fflow_net_5d', 0):.2f}亿",
            })
            total_score += 7
        else:
            signals.append({
                "category": "资金", "name": "fflow_5d", "score": 3, "weight": 1,
                "triggered": False, "reason": "5 日净流出",
            })
            total_score += 3
        # 龙头/政策/情绪: 暂用中性 5/10
        for cat in ["龙头", "政策", "情绪"]:
            signals.append({
                "category": cat, "name": f"{cat}_neutral", "score": 5, "weight": 1,
                "triggered": False, "reason": f"需 {cat} 数据 (中性 5/10)",
            })
            total_score += 5
        # 估值: PEG
        val = raw.get("valuation", {})
        peg = val.get("PEG_真实")
        if isinstance(peg, (int, float)) and peg < 1.5:
            signals.append({
                "category": "估值", "name": "PEG", "score": 9, "weight": 1,
                "triggered": True, "reason": f"PEG {peg} (<1.5 合理)",
            })
            total_score += 9
        else:
            signals.append({
                "category": "估值", "name": "PEG", "score": 4, "weight": 1,
                "triggered": False, "reason": f"PEG {peg or '—'} (偏贵)",
            })
            total_score += 4
        return {
            "score": total_score,
            "raw_score": total_score,
            "rating": f"🟢 {total_score}/100" if total_score >= 60 else ("🟡 中" if total_score >= 40 else "🔴 弱"),
            "signals": signals,
            "missing": ["龙头", "政策", "情绪"],
        }

    @staticmethod
    def _agg_strategy(raw: dict, signals_5) -> dict:
        """综合 5 strategy 给出买/卖/观望 (2026-09-03 修)

        Renderer 期望 strategies = [{name, signal, reason}, ...]
        """
        strategies: list[dict] = []
        buy = 0
        sell = 0

        # 1) Chan
        chan = raw.get("chan", {}) or {}
        bsp = chan.get("buy_sell_points", {}) or {}
        chan_signal = "hold"
        if bsp.get("daily"):
            if any("🟢" in str(k) for k in bsp["daily"].keys() if k != "action"):
                chan_signal = "buy"; buy += 1
            elif any("🔴" in str(k) for k in bsp["daily"].keys() if k != "action"):
                chan_signal = "sell"; sell += 1
        strategies.append({"name": "缠论 (Chan)", "signal": chan_signal, "reason": f"BSP 触发 {chan_signal}"})

        # 2) Wyckoff
        wy = raw.get("wyckoff", {}) or {}
        wy_action = wy.get("action", "hold")
        if wy_action == "buy": buy += 1
        elif wy_action == "sell": sell += 1
        strategies.append({"name": "威科夫 (Wyckoff)", "signal": wy_action, "reason": f"阶段 {wy.get('stage_name', '?')}"})

        # 3) Obv
        obv = raw.get("obv", {}) or {}
        obv_verdict = obv.get("verdict", "")
        obv_signal = "buy" if obv_verdict.startswith("🟢") else ("sell" if obv_verdict.startswith("🔴") else "hold")
        if obv_signal == "buy": buy += 1
        elif obv_signal == "sell": sell += 1
        strategies.append({"name": "量价 (OBV)", "signal": obv_signal, "reason": obv_verdict or "—"})

        # 4) Fflow
        fflow = raw.get("fflow", {}) or {}
        fflow_verdict = fflow.get("verdict", "")
        fflow_signal = "buy" if fflow_verdict.startswith("🟢") else ("sell" if fflow_verdict.startswith("🔴") else "hold")
        if fflow_signal == "buy": buy += 1
        elif fflow_signal == "sell": sell += 1
        strategies.append({"name": "资金 (FFlow)", "signal": fflow_signal, "reason": fflow_verdict or "—"})

        # 5) 估值
        val = raw.get("valuation", {}) or {}
        peg = val.get("PEG_真实")
        val_signal = "hold"
        if isinstance(peg, (int, float)):
            if peg < 1.0: val_signal = "buy"; buy += 1
            elif peg > 2.0: val_signal = "sell"; sell += 1
        strategies.append({"name": "估值 (Valuation)", "signal": val_signal, "reason": f"PEG {peg or '—'}"})

        # 综合 verdict
        if buy >= 3 and sell <= 1:
            verdict = "🟢 买入"
        elif sell >= 3 and buy <= 1:
            verdict = "🔴 卖出"
        else:
            verdict = "🟡 观望"

        return {
            "buy_count": buy,
            "sell_count": sell,
            "verdict": verdict,
            "strategies": strategies,
        }

    @staticmethod
    def _compute_sector_overheat(ctx, raw: dict) -> dict:
        """板块过热 (个股 K 线代理: 1 周 / 1 月 / 3 月涨幅)"""
        kline = ctx.kline or []
        if len(kline) < 64:
            return {}
        closes = [k["close"] for k in kline if "close" in k]
        if len(closes) < 64:
            return {}
        pct_1w  = (closes[-1] / closes[-6]  - 1) * 100 if len(closes) >= 6  else 0
        pct_1m  = (closes[-1] / closes[-22] - 1) * 100 if len(closes) >= 22 else 0
        pct_3m  = (closes[-1] / closes[-64] - 1) * 100 if len(closes) >= 64 else 0
        return {
            "1周涨幅": f"{pct_1w:+.1f}%",
            "1月涨幅": f"{pct_1m:+.1f}%",
            "3月涨幅": f"{pct_3m:+.1f}%",
            "source": f"个股 K线代理 (industry={ctx.industry or '未知'})",
        }

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

    @staticmethod
    def _compute_stop_pl(ctx, raw: dict) -> dict:
        """止盈 3 层 + 止损 4 档 (基于 MA20/MA60 + 当前价)

        2026-09-03 修: 返 Renderer 兼容结构
        - tp1_price/tp2_price/tp3_price (兼容旧 _section_xxx 字段)
        - 止损4档: s1_price/s2_price/s3_price/s4_price (兼容 _section_stop_loss)
        """
        kline = ctx.kline or []
        if len(kline) < 60:
            return {}
        closes = [k["close"] for k in kline if "close" in k]
        current = closes[-1]
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-60:]) / 60
        # 止盈 3 层: 5% / 10% / 20%
        tp1 = round(current * 1.05, 2)
        tp2 = round(current * 1.10, 2)
        tp3 = round(current * 1.20, 2)
        # 止损 4 档: -3% / -5% / -8% / -10%
        sl1 = round(current * 0.97, 2)
        sl2 = round(current * 0.95, 2)
        sl3 = round(current * 0.92, 2)
        sl4 = round(current * 0.90, 2)
        return {
            "current_price": current,
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            # 止盈 (Renderer 旧字段名)
            "tp1_price": tp1, "tp2_price": tp2, "tp3_price": tp3,
            "止盈3层": [
                {"档位": "1档 (+5%)", "价位": tp1, "建议操作": "卖 1/3 锁利"},
                {"档位": "2档 (+10%)", "价位": tp2, "建议操作": "卖 1/3 显著利润"},
                {"档位": "3档 (+20%)", "价位": tp3, "建议操作": "清仓"},
            ],
            # 止损 (Renderer 旧字段名 s1_price/s2_price/...)
            "s1_price": sl1, "s2_price": sl2, "s3_price": sl3, "s4_price": sl4,
            "止损4档": [
                {"档位": "1档 (-3%)", "价位": sl1, "建议操作": "⚠️ 检查基本面"},
                {"档位": "2档 (-5%)", "价位": sl2, "建议操作": "卖 1/3"},
                {"档位": "3档 (-8%)", "价位": sl3, "建议操作": "减半仓"},
                {"档位": "4档 (-10%)", "价位": sl4, "建议操作": "🛑 清仓"},
            ],
        }

    @staticmethod
    def _compute_fundamental_4d(ctx, raw: dict, val: dict) -> dict:
        """基本面 4 维: 估值 / 盈利 / 增长 / 安全 (2026-09-03 修)

        Renderer 期望 d = {key: {score, comment}}, 所以返 dict 嵌套.
        """
        dims = {}
        # 1) 估值 (PEG)
        peg = val.get("PEG_真实")
        if isinstance(peg, (int, float)) and peg < 1.0:
            dims["valuation"] = {"score": 75, "comment": f"✅ PEG {peg} (Lynch 买入区)"}
        elif isinstance(peg, (int, float)) and peg < 1.5:
            dims["valuation"] = {"score": 50, "comment": f"🟡 PEG {peg} (合理)"}
        else:
            dims["valuation"] = {"score": 25, "comment": f"🟠 PEG {peg or '—'} (偏贵)"}
        # 2) 盈利 (ROC)
        roc = val.get("roc")
        if isinstance(roc, (int, float)) and roc > 25:
            dims["profitability"] = {"score": 75, "comment": f"✅ ROC {roc}% (>25% 优秀)"}
        elif isinstance(roc, (int, float)) and roc > 10:
            dims["profitability"] = {"score": 50, "comment": f"🟡 ROC {roc}%"}
        else:
            dims["profitability"] = {"score": 25, "comment": f"⚠️ ROC {roc or '—'}"}
        # 3) 增长 (EY)
        ey = val.get("ey")
        if isinstance(ey, (int, float)) and ey > 8:
            dims["growth"] = {"score": 75, "comment": f"✅ EY {ey}% (>8% 便宜)"}
        elif isinstance(ey, (int, float)) and ey > 5:
            dims["growth"] = {"score": 50, "comment": f"🟡 EY {ey}%"}
        else:
            dims["growth"] = {"score": 25, "comment": f"⚠️ EY {ey or '—'}"}
        # 4) 安全 (行业)
        industry = ctx.industry or "未知"
        dims["safety"] = {"score": 50, "comment": f"⚠️ 行业 {industry} (待 LLM 细化)"}
        # 总分 (4 维平均)
        total = sum(d["score"] for d in dims.values()) // 4
        return {
            "valuation": dims["valuation"],
            "profitability": dims["profitability"],
            "growth": dims["growth"],
            "safety": dims["safety"],
            "total_score": total,
            "dims": dims,
        }

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
        line("基本面", self.fundamental_status, f"{self.fundamental.get('total_score', '—')}/100" if self.fundamental else "—")
        line("5类信号", self.signal_5cat_status, f"{self.signal_5cat.get('raw_score', '—')}/123" if self.signal_5cat else "—")
        line("策略", self.strategy_status, self.strategy.get('verdict', '—') if self.strategy else "—")
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
        report["板块过热"] = ("✅" if self.sector_overheat else "❓", "OK" if self.sector_overheat else "未计算")
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

    def can_calc_sector_overheat(self) -> bool:
        return len(self.kline) >= 90

    def can_calc_supplement(self) -> bool:
        return len(self.kline) >= 30
