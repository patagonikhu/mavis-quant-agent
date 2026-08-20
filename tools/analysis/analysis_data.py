"""
analysis_data.py — 分析数据契约 (v1.0, 2026-07-21)

架构铁律 (三层分离):
  AnalysisEngine 层 = 纯计算 factor, 零网络请求
  ✅ 只读 dump dict 字段 (由 dump_data.py 写入 data/dump/{code}.json)
  ❌ 禁止 import requests / subprocess curl / fetch_all / 任何网络调用

设计目标:
  1. 强约束: 所有字段有/无都明确, 不会"有时候有有时候没"
  2. 可追踪: 每个数据源的状态 (OK/TIMEOUT/EMPTY/PARSE_FAIL/NET_*) 都记录
  3. 可计算: completeness_report() 一行告诉 LLM 哪些数据缺
  4. 可扩展: 后续加模型/算法只需要新加字段, 不破坏现有 schema
  5. 零依赖: 用标准库 dataclass, 不需要 pydantic

使用方式 (必须先走 dump 层):
  # Step 1: dump 层拉数据 (唯一网络入口)
  # bash tools/with_venv.sh python -m tools.dump_data 002371

  # Step 2: analysisengine 层读 dump + 算 factor (零网络)
  from tools.dump_data import load_dump
  from tools.analysis.analysis_data import AnalysisData
  dump = load_dump("002371")
  data = AnalysisData.from_dump(dump)
  print(data.completeness_report())
  if data.can_calc_peg():
      ...
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

# 2026-07-24: 机械算基础分 helper (LLM 后续在 chat 里精调)
# 替代 enhance_report 阶段 LLM 没跑的占位符, 让 linter 不警告
# 2026-07-25: enhance_report.py 已删, 注释保留供历史追溯

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
    """机械 T 框架占位 (实际 T 位置需 LLM 查 events.json 算)"""
    return {
        "T_position": "T-? (机械占位, 待 LLM 查 events.json 算 T 位置)",
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
    2026-07-25 加: dump_data 存的 north_flow 格式是 dict-of-lists
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
    amount: float = 0.0  # 成交额 (元), 2026-07-24 加 (兼容 dump_data 字段)
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
class AnalysisData:
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
    kline_60m: list[dict] = field(default_factory=list)  # 60分 K 线 (给 5 合 1 顶部预警, 2026-07-25 加)

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
        return (self.analysis or {}).get("buy_sell_points")

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
    peg_detail: Optional[dict] = None
    dcf_detail: Optional[dict] = None
    signal_5cat: Optional[dict] = None
    xgboost_prob: Optional[float] = None
    sector_overheat: Optional[dict] = None
    supplement: Optional[dict] = None
    take_profit: Optional[dict] = None
    stop_loss: Optional[dict] = None
    exit_signals: Optional[dict] = None
    position_layer: Optional[dict] = None
    monitor_triggers: Optional[dict] = None

    # ===== 买卖点 (dump 里的 buy_sell_points, 三买三卖) =====
    buy_sell_points: Optional[dict] = None  # {weekly, daily, 60min, 30min} 各含 0买/1买/2买/3买/1卖/2卖/3卖/action

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
    def from_raw(cls, raw: dict) -> "AnalysisData":
        statuses = raw.get("statuses") or {}

        # statuses 可能来自 tushare_fetcher (只记录 tushare 源的状态)
        # dump 文件里没有 price/kline/eps 的 status — 根据实际数据是否存在来推断
        def _infer_status(key: str, has_data: bool) -> str:
            """statuses 有明确记录就用，否则根据数据是否存在推断"""
            if key in statuses:
                return statuses[key]
            return "OK" if has_data else "ALL_FAILED"

        # MA 表 + 技术指标 (从 K线算)
        ma_table = []
        technical = None
        kline_raw = raw.get("kline") or []
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

            # 算 8 个技术指标
            try:
                from tools.fetch.data_fetcher import compute_indicators
                technical = compute_indicators(kline_raw)
            except Exception as e:
                technical = {"error": f"compute_indicators 失败: {e}"}

        # ===== 自动评估器 (Phase 1 自动化) =====
        # 1. 4 维基本面
        fundamental = None
        if raw.get("eps_table"):
            try:
                from tools.analysis_evaluators import compute_fundamental_score
                fundamental = compute_fundamental_score(
                    eps_table=raw["eps_table"],
                    current_price=raw.get("close"),
                    pe_ttm=raw.get("pe_ttm"),
                )
            except Exception as e:
                fundamental = {"error": str(e)}

        # 1.5 AnalysisEngine — 算全部因子，保存 ctx 供历史回算
        signals_5 = None
        ctx = None
        try:
            from tools.analysis.analysis_engine import AnalysisEngine, RawContext
            ctx = RawContext.from_dump(raw)
            engine = AnalysisEngine()
            result = engine.analyze(ctx)
            signals_5 = result.to_dict(ctx)
        except Exception as e:
            signals_5 = {"error": str(e)}
            print(f"⚠️ AnalysisEngine 错误: {e}", file=sys.stderr)
            traceback.print_exc()
        # 2. 5 类 14 子信号
        signal_5cat = None
        if kline_raw:
            try:
                # 2026-08-06 修复: 之前调 compute_signal_5cat (老公式, 字段格式经常对不上)
                # 改走 AnalysisEngine.FiveCategoriesStrategy (单一真源, v5.10.34+)
                # 2026-08-17 修复 BUG: 之前这里又重建 ctx, 漏传 moneyflow/name, 把 line 507 拿到的完整 ctx 覆盖
                # 变成"无 moneyflow + 空 name"的残缺 ctx, FflowStrategy 跑时拿到空数据, 显示"无数据"
                # 修复: 复用 line 507 已有的 ctx, 不要再造一遍
                from tools.analysis.analysis_engine import AnalysisEngine
                _fc_result = AnalysisEngine().analyze(ctx)
                _fc = _fc_result.factor_scores.get("five_categories") if hasattr(_fc_result, "factor_scores") else None
                # FactorScore 是 dataclass, 不是 dict, 读属性
                if _fc:
                    _fc_raw = getattr(_fc, "raw", None) or {}
                    _fc_signals = getattr(_fc, "signals", []) or []
                    _fc_summary = getattr(_fc, "summary", "—")
                    signal_5cat = {
                        "signals": [
                            {"category": "5类综合", "name": s, "score": 5, "triggered": True, "weight": 1, "comment": ""}
                            for s in _fc_signals
                        ],
                        "raw_score": (_fc_raw.get("score", 0) if isinstance(_fc_raw, dict) else 0) * 20,
                        "rating": _fc_summary,
                        "missing": [],
                    }
            except Exception as e:
                signal_5cat = {"error": str(e)}

        # 3. 4 套交易策略
        strategy = None
        if technical and "error" not in technical:
            try:
                from tools.analysis_evaluators import compute_strategy_signals
                strategy = compute_strategy_signals(technical)
            except Exception as e:
                strategy = {"error": str(e)}

        # 4. PEG 实算 (Phase 1 自动化)
        peg_detail = None
        if raw.get("eps_table") and raw.get("close"):
            try:
                from tools.analysis_evaluators import compute_peg
                peg_detail = compute_peg(
                    eps_table=raw["eps_table"],
                    current_price=raw["close"],
                )
            except Exception as e:
                peg_detail = {"error": str(e)}

        # 5. DCF L 实算 (Phase 1 自动化, 需股本算市值)
        dcf_detail = None
        if raw.get("eps_table") and raw.get("total_mv"):
            try:
                from tools.analysis_evaluators import compute_dcf_l
                dcf_detail = compute_dcf_l(
                    eps_table=raw["eps_table"],
                    market_cap_yi=raw["total_mv"],
                )
            except Exception as e:
                dcf_detail = {"error": str(e)}

        # 6. 止盈止损
        take_profit = None
        stop_loss = None
        if raw.get("close") and kline_raw:
            try:
                from tools.analysis_evaluators import compute_take_profit_stop_loss
                tp_sl = compute_take_profit_stop_loss(
                    current_price=raw["close"],
                    kline=kline_raw,
                )
                take_profit = tp_sl
                stop_loss = tp_sl
            except Exception as e:
                take_profit = {"error": str(e)}

        # 7. 板块过热
        # 2026-07-24 修复: 优先用 dump 里的 sector_overheat (硬编码但至少有数据),
        # 机械算函数 (compute_sector_overheat) 走 ETF K线, 2000 积分档拉不到 (走 daily 不是 index_daily)
        sector_overheat = None
        if raw.get("sector_overheat") and not raw["sector_overheat"].get("error"):
            sector_overheat = raw["sector_overheat"]
        elif raw.get("sector"):
            try:
                from tools.analysis_evaluators import compute_sector_overheat
                sector_overheat = compute_sector_overheat(raw["sector"])
            except Exception as e:
                sector_overheat = {"error": str(e)}
        else:
            # 2026-08-06 修复: dump 顶层 19 字段不存 sector_overheat, 也不存 sector
            # 改走 analysis_engine.SectorOverheatStrategy (但那也 hardcode -7/-22/-30, 见 analysis_engine.py:531)
            # 这里直接读 industry + K线 算 1月/3月涨幅 (个股代理, 优于 hardcode)
            if kline_raw and len(kline_raw) >= 60 and raw.get("industry"):
                from datetime import datetime
                closes = [k["close"] for k in kline_raw]
                dates = [k.get("trade_date", k.get("date", ""))[:10] for k in kline_raw]
                # 1月 ≈ 21 个交易日, 3月 ≈ 63 个交易日
                pct_1m = (closes[-1] / closes[-22] - 1) * 100 if len(closes) >= 22 else 0
                pct_3m = (closes[-1] / closes[-64] - 1) * 100 if len(closes) >= 64 else 0
                # 1周 ≈ 5 个交易日
                pct_1w = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
                # 判定
                if pct_3m > 30:
                    verdict = f"🟠 板块过热 (3 月 +{pct_3m:.0f}%)"
                elif pct_3m < -20:
                    verdict = f"🟦 板块超跌 (3 月 {pct_3m:.0f}%)"
                elif pct_1m > 20:
                    verdict = f"🟡 板块偏热 (1 月 +{pct_1m:.0f}%)"
                else:
                    verdict = f"✅ 板块安全 (3 月 {pct_3m:+.0f}%)"
                sector_overheat = {
                    "1周涨幅": f"{pct_1w:+.1f}%",
                    "1月涨幅": f"{pct_1m:+.1f}%",
                    "3月涨幅": f"{pct_3m:+.1f}%",
                    "判定": verdict,
                    "source": "个股代理 (industry={})".format(raw["industry"]),
                    "_warning": "sector_overheat 来自个股 K线代理, 不是真实板块指数",
                }

        # 8. 缠论补充 (SMC + 量价 + 威科夫)
        supplement = None
        if kline_raw and len(kline_raw) >= 30:
            try:
                from tools.analysis_evaluators import compute_supplement
                supplement = compute_supplement(kline_raw)
            except Exception as e:
                supplement = {"error": str(e)}

        # chan 数据从 analysis 层读（AnalysisEngine 的 ChanStrategy 计算后写入 to_dict）
        chan_data = None
        chan_raw = (signals_5 or {}).get("chan", {})
        if chan_raw:
            chan_data = {
                "weekly": chan_raw.get("weekly", {}),
                "daily":  chan_raw.get("daily", {}),
                "60min":  chan_raw.get("60min", {}),
                "beichi": {
                    "weekly": (lambda b: b.get("display", "") if isinstance(b, dict) else b)(
                        (chan_raw.get("weekly") or {}).get("beichi", "")),
                    "daily":  (lambda b: b.get("display", "") if isinstance(b, dict) else b)(
                        (chan_raw.get("daily")  or {}).get("beichi", "")),
                    "60min":  (lambda b: b.get("display", "") if isinstance(b, dict) else b)(
                        (chan_raw.get("60min")  or {}).get("beichi", "")),
                },
            }

        return cls(
            code=raw.get("code", ""),
            name=raw.get("name", ""),
            current_price=raw.get("close"),
            price_status=DataStatus(name="price", status=_infer_status("price", bool(raw.get("close")))),
            pe_ttm=raw.get("pe_ttm"),
            kline=[KLineBar(**bar) for bar in kline_raw],
            kline_status=DataStatus(name="kline", status=_infer_status("kline", bool(kline_raw))),
            # 2026-07-25 加: 60分 K 线 (给 5 合 1 顶部预警, 避免 render 阶段拉数据)
            kline_60m=list(raw.get("kline_60m") or []),
            ma_table=ma_table,
            ma_status=DataStatus(name="ma", status="OK" if ma_table else "EMPTY"),
            fflow_data=[
                FflowRow(
                    date=row.get("trade_date", row.get("date", "")),
                    # FflowRow 字段单位 = 亿元 (统一, 不再做二次转换)
                    # tushare_fetcher.get_fund_flow_combined() 已输出 main_yi 单位 = 亿
                    # main_net / small / mid / big / super_big 直接存亿, 下游显示不除
                    main_net=float(row.get("main_net", row.get("main_yi", 0)) or 0),
                    small=float(row.get("small", row.get("small_yi", 0)) or 0),
                    mid=float(row.get("mid", row.get("medium_yi", 0)) or 0),
                    big=float(row.get("big", row.get("large_yi", 0)) or 0),
                    super_big=float(row.get("super_big", row.get("xlarge_yi", 0)) or 0),
                    derived=bool(row.get("_derived", False)),
                )
                for row in (raw.get("fflow_data") or (raw.get("fflow") if isinstance(raw.get("fflow"), list) else (raw.get("fflow") or {}).get("data") or []))
            ],
            fflow_status=DataStatus(name="fflow", status=_infer_status("fflow", bool(raw.get("fflow_data") or raw.get("fflow")))),
            eps_table=[EpsRow(**row) for row in (raw.get("eps_table") or [])],
            eps_status=DataStatus(name="eps", status=_infer_status("eps", bool(raw.get("eps_table")))),
            technical=technical,
            technical_status=DataStatus(
                name="technical",
                status="OK" if technical and "error" not in technical else "EMPTY",
            ),
            fundamental=fundamental,
            fundamental_status=DataStatus(
                name="fundamental",
                status="OK" if fundamental and "error" not in fundamental else "EMPTY",
            ),
            signal_5cat=signal_5cat,
            signal_5cat_status=DataStatus(
                name="signal_5cat",
                status="OK" if signal_5cat and "error" not in signal_5cat else "EMPTY",
            ),
            strategy=strategy,
            strategy_status=DataStatus(
                name="strategy",
                status="OK" if strategy and "error" not in strategy else "EMPTY",
            ),
            # v5.10.35 改: peg_detail/dcf_detail 优先用 analysis 层 factor 库算的真值
            # 之前用 analysis_evaluators.compute_peg (老公式, 跟 PegFactor 公式不一致: 几何平均 vs 算术平均)
            # 现在 PegFactor/DcfFactor 是 2026-07-27 重构时抽的"标准"实现, 优先用
            peg_detail=cls._merge_peg_detail(peg_detail, signals_5),
            dcf_detail=cls._merge_dcf_detail(dcf_detail, signals_5),
            shares_yi=raw.get("shares_yi"),
            market_cap_yi=raw.get("total_mv"),
            take_profit=take_profit,
            stop_loss=stop_loss,
            sector_overheat=sector_overheat,
            supplement=supplement,
            chan_data=chan_data,
            ctx=ctx,  # RawContext，供历史回算/因子历史 section 用
            analysis=signals_5,
            # 2026-07-24: 机械算基础分 (替代 LLM 没跑的占位), 让 linter 不警告
            four_questions=_mech_four_questions(raw, signals_5),
            t_frame=_mech_t_frame(raw),
            position_layer=_mech_position_layer(signals_5, ma_table),
            exit_signals=_mech_exit_signals(signals_5, ma_table),
            monitor_triggers=_mech_monitor_triggers(signals_5, ma_table),
            # 买卖点 (三买三卖)
            buy_sell_points=raw.get("buy_sell_points"),
            # Tushare 扩展数据 (幂等性: 从 dump 读，不在 renderer 实时拉)
            ts_weekly=((raw.get("tushare") or {}).get("weekly") or []),
            ts_monthly=((raw.get("tushare") or {}).get("monthly") or []),
            ts_north_flow=_dict_to_rows((raw.get("tushare") or {}).get("north_flow")),
            ts_margin=((raw.get("tushare") or {}).get("margin") or []),
            ts_top_list=((raw.get("tushare") or {}).get("top_list") or []),
            ts_dividend=((raw.get("tushare") or {}).get("dividend") or []),
            ts_fina_rows=((raw.get("tushare") or {}).get("fina_rows") or []),
        )

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
        line("fflow", self.fflow_status, f"{len(self.fflow_data)} 日")
        line("EPS", self.eps_status, f"{len(self.eps_table)} 条")

        report["缠论"] = ("✅" if self.chan_data else "❓", "OK" if self.chan_data else "未计算")
        report["四问"] = ("✅" if self.four_questions else "❓", "OK" if self.four_questions else "未计算")
        report["T框架"] = ("✅" if self.t_frame else "❓", "OK" if self.t_frame else "未计算")
        report["PEG"] = ("✅" if self.peg_detail else "❓",
                         f"={self.peg_detail.get('peg')}" if self.peg_detail else "未计算")
        report["DCF L"] = ("✅" if self.dcf_detail else "❓",
                           f"L={self.dcf_detail.get('L_r8')}" if self.dcf_detail else "未计算")
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
