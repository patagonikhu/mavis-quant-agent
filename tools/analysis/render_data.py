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
# 2026-11-03 删: _mech_four_questions / _mech_t_frame
#   → 投资四问 + T 框架两档从 md 报告里删除, 这两个机械占位函数一并清理
#   → handbook/analysis-framework.md 的方法论正文保留 (仍是 LLM 手动分析时的参考)


# ============================================================
# 工具函数 (2026-07-25 加)
# ============================================================

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

    kline: list[KLineBar] = field(default_factory=list)
    kline_status: Optional[DataStatus] = None

    # 2026-11-05 加: 周线 K 线 (由 ctx.weekly 合成, 纯计算不联网)
    #   用途: 报告「周线分析」段的威科夫判定要按**周线口径**取 bars —— 之前该段
    #   的公式代入值用的是日线 bars (data.kline), 跟同段的周线因子值口径错配。
    weekly_kline: list[KLineBar] = field(default_factory=list)

    ma_table: list[MaRow] = field(default_factory=list)
    ma_status: Optional[DataStatus] = None

    # ===== 技术指标 (8 个, 从 K线自动算) =====
    technical: Optional[dict] = None  # {macd, rsi, kdj, boll, atr, vol_ma, summary}
    technical_status: Optional[DataStatus] = None

    # ===== 自动评估器 (Phase 1 自动算, 不再是占位符) =====
    # 2026-09-09 改: 删 total_score 加权字段, 4 维独立 dict
    # 2026-09-09 删: fundamental 字段 (4 维基本面, 走 Magic)
    # fundamental: Optional[dict] = None
    # fundamental_status: Optional[DataStatus] = None


    # ===== 5 方法 × 3 周期 矩阵 (2026-07-24 固化) =====
    analysis: Optional[dict] = None  # v5.10.26+: 替代 signals_5method, AnalysisEngine 输出 dict

    # ===== 季度财务 (最近 2 季, 营收/净利/毛利率/ROE/EBIT) — render 用 =====
    quarterly_finance: list[dict] = field(default_factory=list)  # [{quarter, or_yoy, np_yoy, gm, roe, ebit_yi, revenue_yi, netprofit_yi}, ...]

    # 因子历史缓存 — 计算一次后由 render/_section_factor_history 复用，避免重复 analyze_history
    factor_history_rows: Optional[list] = field(default=None, repr=False)

    # v5.10.35: 派生字段兼容层 — render 读 data.<字段> 现在自动从 analysis 拿
    # 之前 v5.10.34 前: 这些字段从 dump 顶层读; 现在挪到 analysis 层
    # render 改读 data.analysis.<字段> 太散, 加 property 兼容老代码
    # 2026-11-02 删: peg / dcf 两个 property (PEG/DCF 整档已删, 0 调用)
    # 2026-11-03 删: five_categories property (5 类 14 子信号整档已删, 0 调用)
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

    # 2026-11-03 删: monitor_triggers property (监控触发点整档已删)

    # 2026-11-02 删: _merge_peg_detail / _merge_dcf_detail
    #   (2026-09-02 就没人调了 — peg_detail/dcf_detail 已合并成 valuation_data; PEG/DCF 整档也已删)

    # 2026-09-09 删: strategy 字段 (5 strategy 投票, 走缠论)
    # strategy: Optional[dict] = None
    # strategy_status: Optional[DataStatus] = None

    # ===== 扩展 (mavis LLM 算) =====
    chan_data: Optional[dict] = None
    chan_status: Optional[DataStatus] = None
    # 2026-11-03 删: four_questions / t_frame / signal_5cat 三个字段
    #   (投资四问 / T 框架 / 5 类 14 子信号三档整档已删)
    # 2026-09-02 合并: peg_detail / dcf_detail / magic_formula_detail → valuation_data 1 个
    # 2026-11-02: PEG/DCF 已删, valuation_data 现在只剩 Magic (ROC/EY)
    valuation_data: Optional[dict] = None  # FinanceStrategy 输出 (Magic)
    xgboost_prob: Optional[float] = None
    supplement: Optional[dict] = None
    # 2026-11-03 删: take_profit / stop_loss 两个字段 (止盈 3 层 + 止损 4 档整档已删)
    # 2026-11-03 删: exit_signals property (退场信号检查整档已删)
    # 2026-10-30 修: 删掉 position_layer / monitor_triggers 两个字段声明
    #   原因: 它们在 class body 里声明在 @property 之后 (356 行 vs 279/291 行),
    #   class body 自上而下执行 → 字段声明把 property 覆盖成 None, 导致
    #   renderer 永远读到 None → 报告出 "监控触发点 未生成" 占位符.
    #   实测: analysis dict 里 monitor_triggers 有真实数据, 只是被字段遮蔽了.
    #   现在这个值统一由上面对应的 @property 从 analysis dict 派生.
    #   2026-11-03: monitor_triggers property 已随「📌 监控触发点」整档删除
    # 2026-11-03 删: three_layer_position / position_layer 两个 property
    #   (3 层仓位策略整档已删)

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
            # 2026-11-05 加: 周线 bars 传给渲染层, 让「周线分析」段的威科夫
            #   公式代入值用周线口径 (跟生产侧 ctx.weekly 同源)。
            weekly_kline=[KLineBar(**{k: bar.get(k, 0) for k in KLineBar.__dataclass_fields__})
                          for bar in (getattr(ctx, "weekly", None) or [])],
            ma_table=ma_table,
            ma_status=DataStatus(name="ma", status="OK" if ma_table else "EMPTY"),
            # 季度财务 (最近 4 季) — 直接从 FinanceStrategy 结果取
            quarterly_finance=(result.raw.get("finance") or {}).get("quarterly") or [],
            technical=technical,
            technical_status=DataStatus(name="technical",
                                        status="OK" if technical and "error" not in technical else "EMPTY"),
            analysis=signals_5,
            chan_data=chan_data,
            ctx=ctx,
            # 2026-10-30 修: 删掉 position_layer / monitor_triggers 两个传参
            #   它们走 _mech_* stub (永远返回 None), 且会把上面删掉的字段重新加回来,
            #   导致 property 再次被遮蔽. 现在统一由 property 从 analysis dict 派生.
            #   (exit_signals 同样在 2026-11-03 整档删除, 不再传参)
            # 2026-11-03 删: four_questions / t_frame 两个传参 (两档整档已删)
            market_cap_yi=ctx.market_cap_yi,
            # Magic Formula: 在 from_result 里直接算, 不留 N/A
            # market_cap 入参单位"万" (跟 Tushare daily_basic 一致), 内部 / 1e4 转亿
            # 2026-09-02 改: 删 magic_formula_detail, 用 valuation_data 1 个字段
            valuation_data=cls._compute_valuation(ctx, result),
            # 2026-09-03 修 12 处 Phase2 降级: 7 个 schema 字段填值
            # _compute_phase2 返 dict 包含: sector_overheat / supplement
            #   2026-11-03 删: fundamental / signal_5cat / strategy 三个 key
            #   2026-11-03 删: take_profit key (止盈/止损两档整档已删)
            **cls._compute_phase2(ctx, result, signals_5, ma_table),
        )

    @classmethod
    def _compute_magic(cls, ctx: "RawContext") -> Optional[dict]:
        """旧 _compute_magic: 2026-09-02 保留兼容, 实际数据从 valuation_data 读。

        报告渲染层已经从 data.valuation_data 读 (Magic)。
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
        # 走 缠论 + Magic (ROC/EY), 报告 section 整个删掉
        # 2026-11-03 删: 5 类信号 (整档已删, 只剩"暂未接入"占位, 零有效信息)
        # 2026-09-09 删: 策略 (5 strategy 投票, 走缠论)
        # 4) 缠论补充 — 从 chan 取 (bsp + hub 已经算)
        out["supplement"] = cls._extract_chan_supplement(raw)
        # 2026-11-03 删: 止盈 3 层 + 止损 4 档 (整档已删)
        #   原: out["take_profit"] = compute_stop_pl(ctx.kline or [])
        #   价位是"当前价 × 固定百分比"的机械外推, 无标的特异性判断;
        #   (原注释说"真实风控由 monitor_triggers 承担" — 该档本轮也已删, 见下)
        # 2026-11-03 删: 退场信号检查 (exit_signals) — 与上面两档重叠
        # 2026-11-03 删: 3 层仓位策略 + 监控触发点 (整档已删)
        #   → 报告已无独立风控 section; 仓位/止损需自行从 缠论买卖点 推导

        return out

    # 2026-09-09 删: _agg_strategy (5 strategy 投票, 67 行)
    # 原因: 决策走 缠论 1买/2买/3买/1卖/2卖/3卖, 不再单独跑 5 strategy 投票
    # 渲染端: 4 套交易策略 section 整个删掉 (走缠论, 走 TechnicalStrategy 8 个技术指标)

    # 2026-11-03 删: _agg_5_categories (5 类 14 子信号整档已删)
    #   演进: 2026-10-28 删量价+资金 → 2026-11-02 删 PEG → 只剩 龙头/政策/情绪
    #         3 项"暂未接入"占位 (score=5, triggered=False) → 零有效子信号

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
        # 2026-09-09 删: 基本面 status 行 (4 维基本面, 走 Magic)
        # 2026-11-03 删: 5类信号 status 行 (5 类 14 子信号整档已删)
        # 2026-09-09 删: 策略 status 行 (走缠论, 不再单独展示 5 strategy 投票)
        # 2026-08-31: 资金流 section 已停用 (CLAUDE.md 板块适用性限制), 不展示
        # 2026-11-04 删: EPS status 行 (EPS 机构一致预期整档已删)

        report["缠论"] = ("✅" if self.chan_data else "❓", "OK" if self.chan_data else "未计算")
        # 2026-11-03 删: 四问 / T框架 两行 (两档整档已删)
        # 2026-09-02 改: 合并 valuation_data 1 个
        # 2026-11-02 删: PEG / DCF L 两行 (整档已删)
        _val = self.valuation_data or {}
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
        # 2026-11-02 修: 删重复的 report["5类信号"] 赋值
        #   (上面 line("5类信号", ...) 已经赋过, 这里再赋会覆盖掉子信号数详情)
        report["缠论补充"] = ("✅" if self.supplement else "❓", "OK" if self.supplement else "未计算")
        # 2026-11-03 删: report["止盈止损"] 行 (止盈 3 层 + 止损 4 档整档已删)

        return report

    def completeness_pct(self) -> int:
        report = self.completeness_report()
        ok = sum(1 for emoji, _ in report.values() if emoji == "✅")
        return int(ok / len(report) * 100)

    # 2026-11-02 删: can_calc_peg / can_calc_dcf (0 调用; PEG/DCF 整档已删)

    def can_calc_supplement(self) -> bool:
        return len(self.kline) >= 30
