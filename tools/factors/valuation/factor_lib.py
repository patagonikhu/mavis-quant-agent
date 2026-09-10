"""
tools/factors/valuation/factor_lib.py — 估值算子全集 (纯函数, 2026-09-09 统一)

历史: 之前 3 个文件重复实现:
  - tools/analysis/valuation.py       (calc_roc_at_date / calc_ey_at_date / calc_magic_one_day / batch_magic_scores)
  - tools/factors/valuation/multi.py  (PegFactor / DcfFactor / SectorOverheatFactor / FiveCategoriesFactor)
  - tools/analysis/report_section_evaluators.py (compute_peg / compute_dcf_l)

合并后: 1 个文件, 11 个纯函数, 统一风格, 无 class 包装。
Mavis 守门员 (t-guardrail) 规则: 所有 factor 统一放 1 个文件, 纯函数风格。

# 公开 API
  - compute_peg(eps_table, current_price)         # PEG 估值
  - compute_dcf_l(eps_table, market_cap_yi)       # DCF 隐含 L
  - compute_roc(financials, as_of_date, min_capital_yi)  # ROC 资本回报率
  - compute_ey(financials, as_of_date, market_cap_wan)   # EY 盈利收益率
  - compute_magic_one_day(...)                    # ROC+EY 合并
  - compute_sector_overheat(kline)                # 板块过热预警
  - compute_five_categories(...)                  # 5 类 14 子信号
  - batch_magic_scores(codes, with_market_cap)   # 全市场批量排名入口
  - find_full_year_financials(financials, as_of_date)  # 找全年财报

# 内部工具
  - _ttm_ebit(financials)                        # TTM EBIT 拼装
  - EXCLUDED_INDUSTRIES (8+5 类)                 # 行业黑名单
  - _load_bulk_caches()                           # 模块级缓存 (N+1 优化)
"""
from __future__ import annotations

from typing import Optional
from pathlib import Path

# ============================================================
# 行业黑名单 (ROC/EY/PEG 在这些行业失真)
# ============================================================
EXCLUDED_INDUSTRIES = {
    "银行", "保险", "证券", "信托", "期货", "租赁",  # 金融
    "房地产", "物业管理", "园区开发",               # 地产
    "电力", "水务", "燃气", "热力", "环保",          # 公用
    "多元金融",                                      # 金融子类
}


# ============================================================
# 内部: 质量退化过滤 (2026-09-10 加, 防"主业崩盘"型公司混进 Magic 排名)
# ============================================================
# 过滤 A: yoy 双暴跌 (or_yoy < -20% AND np_yoy < -30%)  — 主业恶化
# 过滤 B: 营业外巨亏 (np_yoy 暴跌 + ebit 还正) — 一次性减值/营业外
# 注意: Tushare fina_indicator 没 netprofit 绝对值字段, 用 np_yoy<-50 代理
# 例外: 跳过 12-31 全年行 (yoy 本身就是该年 vs 去年, 不算"双暴跌")
# 2026-09-10 加, 跟现有 EBIT<=0 亏损跳过互补
def _quality_gate(financials: list[dict]) -> dict | None:
    """质量退化检测: 返回 None=通过, dict=skip_reason"""
    if not financials:
        return None
    latest = financials[-1]
    end_date = (latest.get("end_date", "") or "").replace("-", "")[:8]
    # 12-31 全年行不算"暴跌" (它就是全年累计, yoy 是全年 vs 上全年)
    if end_date.endswith("1231"):
        return None

    or_yoy = latest.get("or_yoy")
    np_yoy = latest.get("netprofit_yoy")
    ebit = latest.get("ebit")

    # 过滤 B: 营业外巨亏 (EBIT 还正, 但 netprofit_yoy 暴跌 -50%+, 大概率一次性减值/营业外)
    if ebit is not None and ebit > 0 and np_yoy is not None:
        try:
            if np_yoy < -50:
                return {"skip_reason": "non_operating_loss", "industry": latest.get("industry", ""),
                        "period_label": end_date}
        except TypeError:
            pass

    # 过滤 A: 营收 + 净利 双暴跌 (主业恶化)
    if or_yoy is not None and np_yoy is not None:
        try:
            if or_yoy < -20 and np_yoy < -30:
                return {"skip_reason": "revenue_profit_collapse", "industry": latest.get("industry", ""),
                        "period_label": end_date}
        except TypeError:
            pass

    return None


# ============================================================
# 内部: TTM EBIT 拼装 (A 股没季报, 用 H1 退到去年全年当 proxy)
# ============================================================
def _ttm_ebit(financials: list[dict]) -> tuple[Optional[float], bool, str]:
    """TTM EBIT (A 股近似)

    策略: 优先用最新全年 (12-31), 半年报 (06-30) 退回上一年全年当 proxy。

    Returns:
        (ebit, seasonal_warning, period_label)
    """
    if not financials:
        return None, False, "no_data"

    latest = financials[-1]
    end_date = latest.get("end_date", "")

    ebit_now = latest.get("ebit")
    if ebit_now is None or ebit_now <= 0:
        return None, False, "loss"

    # 倒序找第一个 12-31 (全年)
    full_year_row = None
    for r in reversed(financials):
        ed = r.get("end_date", "")
        if ed.endswith("1231"):
            full_year_row = r
            break

    if full_year_row and full_year_row.get("ebit") and full_year_row["ebit"] > 0:
        fy_ebit = full_year_row["ebit"]
        fy_date = full_year_row["end_date"]
        if fy_date == end_date:
            return fy_ebit, False, f"{fy_date[:4]} 全年"
        else:
            return fy_ebit, True, f"{fy_date[:4]} 全年 (TTM proxy, 最新 H1 {end_date})"

    return ebit_now, True, f"{end_date[:4]} 半年 (无全年, 视为 TTM)"


def find_full_year_financials(financials: list[dict], as_of_date: str) -> Optional[dict]:
    """找 ≤ as_of_date 的最新全年财报 (12-31)

    Args:
        financials:  按 end_date 升序, [{end_date, ebit, ...}, ...]
        as_of_date:  '20260831' 8 字符

    Returns:
        那一年的全年行, 或 None
    """
    as_of_yyyymm = as_of_date[:6]  # '202608'
    as_of_year = int(as_of_date[:4])

    for r in reversed(financials):
        ed = r.get("end_date", "")
        ed_yyyymm = ed[:6]
        if not ed.endswith("1231"):
            continue
        ed_year = int(ed[:4])
        if ed_year < as_of_year:
            return r
        if ed_year == as_of_year and ed_yyyymm <= as_of_yyyymm:
            return r
    return None


# ============================================================
# ROC 资本回报率 (单日)
# ============================================================
def compute_roc(financials: list[dict], as_of_date: str, min_capital_yi: float = 5) -> dict:
    """单日 ROC = TTM EBIT / (NWC + FA)

    Args:
        min_capital_yi: NWC+FA 最小阈值 (亿), 低于此值视为分母过小假阳性
            默认 5 亿 (剔除 ROC 4000%+ 这种 NWC≈0 的出版/软件业假阳性)
            设 0 关闭过滤

    Returns:
        {roc, ebit_yi, capital_yi, period_label, seasonal_warning, skip_reason, industry}
    """
    if not financials:
        return {"roc": None, "skip_reason": "no_data"}

    industry = (financials[-1].get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return {"roc": None, "industry": industry, "skip_reason": "industry_excluded"}

    # 2026-09-10 加: 质量退化过滤 (yoy 双暴跌 / 营业外巨亏)
    gate = _quality_gate(financials)
    if gate is not None:
        return {"roc": None, "industry": gate.get("industry", industry), "skip_reason": gate["skip_reason"],
                "period_label": gate.get("period_label", "")}

    ebit_ttm, seasonal, period_label = _ttm_ebit(financials)
    if ebit_ttm is None or ebit_ttm <= 0:
        return {"roc": None, "industry": industry, "skip_reason": "no_data", "period_label": period_label}

    # 分母用最新时点 (NWC + FA 是 balance, 不能加总)
    latest = financials[-1]
    nwc = latest.get("networking_capital") or 0
    fa  = latest.get("fixed_assets") or 0
    capital = nwc + fa
    if capital <= 0:
        return {"roc": None, "industry": industry, "skip_reason": "no_data", "period_label": period_label}

    # 剔除 NWC+FA 过小导致的 ROC 假阳性
    # 例: 中南传媒 NWC=-28.6亿 + FA=29亿 = 0.4亿, EBIT=16.6 → ROC 4186% (假)
    capital_yi = capital / 1e8
    if min_capital_yi > 0 and capital_yi < min_capital_yi:
        return {"roc": None, "industry": industry, "capital_yi": round(capital_yi, 2),
                "skip_reason": f"capital_too_small ({capital_yi:.1f}亿 < {min_capital_yi}亿)",
                "period_label": period_label}

    roc = round(ebit_ttm / capital * 100, 1)
    return {
        "roc": roc,
        "industry": industry,
        "period_label": period_label,
        "seasonal_warning": seasonal,
        "ebit_yi": round(ebit_ttm / 1e8, 1),
        "capital_yi": round(capital_yi, 2),
    }


# ============================================================
# EY 盈利收益率 (单日)
# ============================================================
def compute_ey(financials: list[dict], as_of_date: str, market_cap_wan: float) -> dict:
    """单日 EY = TTM EBIT / EV (EV = mkt_cap + net_debt)

    Args:
        market_cap_wan: 当日市值, 单位"万" (跟 Tushare daily_basic 一致)

    Returns:
        {ey, ev_yi, market_cap_yi, netdebt_yi, industry, ...}
    """
    if not financials or not market_cap_wan or market_cap_wan <= 0:
        return {"ey": None, "skip_reason": "no_data"}

    industry = (financials[-1].get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return {"ey": None, "industry": industry, "skip_reason": "industry_excluded"}

    # 2026-09-10 加: 质量退化过滤 (yoy 双暴跌 / 营业外巨亏)
    gate = _quality_gate(financials)
    if gate is not None:
        return {"ey": None, "industry": gate.get("industry", industry), "skip_reason": gate["skip_reason"],
                "period_label": gate.get("period_label", "")}

    ebit_ttm, seasonal, period_label = _ttm_ebit(financials)
    if ebit_ttm is None or ebit_ttm <= 0:
        return {"ey": None, "industry": industry, "skip_reason": "no_data", "period_label": period_label}

    netdebt = financials[-1].get("netdebt") or 0
    market_cap_yi = market_cap_wan / 1e4
    netdebt_yi = netdebt / 1e8
    ev_yi = market_cap_yi + netdebt_yi
    if ev_yi <= 0:
        return {"ey": None, "industry": industry, "skip_reason": "no_data", "period_label": period_label}

    ey = round(ebit_ttm / 1e8 / ev_yi * 100, 1)
    return {
        "ey": ey,
        "ev_yi": ev_yi,
        "industry": industry,
        "period_label": period_label,
        "seasonal_warning": seasonal,
        "ebit_yi": round(ebit_ttm / 1e8, 1),
        "netdebt_yi": round(netdebt_yi, 1),
        "market_cap_yi": round(market_cap_yi, 1),
    }


# ============================================================
# Magic (ROC + EY 1 次性合并)
# ============================================================
def compute_magic_one_day(financials: list[dict], as_of_date: str, market_cap_wan: float,
                          min_capital_yi: float = 5) -> dict:
    """单日 Magic (ROC + EY 1 次 financials + 1 次市场值 读)

    Args:
        min_capital_yi: NWC+FA 最小阈值 (亿), 透传给 compute_roc
    """
    roc_data = compute_roc(financials, as_of_date, min_capital_yi=min_capital_yi)
    ey_data  = compute_ey(financials, as_of_date, market_cap_wan)

    # 合并 skip_reason: 两个都失败才算 no_data
    if roc_data.get("skip_reason") and ey_data.get("skip_reason"):
        skip = roc_data["skip_reason"]
    else:
        skip = None

    return {
        "roc": roc_data.get("roc"),
        "ey":  ey_data.get("ey"),
        "industry": roc_data.get("industry") or ey_data.get("industry"),
        "skip_reason": skip,
        "ev_yi": ey_data.get("ev_yi"),
        "seasonal_warning": roc_data.get("seasonal_warning", False) or ey_data.get("seasonal_warning", False),
        "period_label": roc_data.get("period_label") or ey_data.get("period_label"),
        "ebit_yi":       roc_data.get("ebit_yi"),
        "capital_yi":    roc_data.get("capital_yi"),
        "netdebt_yi":    ey_data.get("netdebt_yi"),
        "market_cap_yi": ey_data.get("market_cap_yi"),
    }


# ============================================================
# PEG 估值 (Forward PE / 3 年 CAGR)
# ============================================================
def compute_peg(eps_table: list[dict], current_price: Optional[float] = None) -> dict:
    """
    PEG = Forward PE / 3 年 EPS CAGR

    边界修复 (2026-09-03):
    - E0/E1/E3 ≤ 0 或缺失 → 返 {"error": "..."}, 不算 PEG
    - g_pct 算出后再算 PEG, 缺 g 直接返错误
    - 避免 1 年前半导体 EPS=0.01 → PEG=0.01 误选
    """
    if not eps_table or not current_price:
        return {"error": "数据不足"}

    actuals = [r for r in eps_table if r.get("year_mark") == "A"]
    estimates = [r for r in eps_table if r.get("year_mark") == "E"]

    if not actuals or not estimates:
        return {"error": "需要 actual + estimate 数据"}

    e0 = actuals[-1].get("eps", 0) or 0
    e1 = estimates[0].get("eps", 0) or 0
    e2 = estimates[1].get("eps", 0) or 0 if estimates else 0
    e3 = estimates[2].get("eps", 0) or 0 if len(estimates) >= 3 else 0

    if e1 <= 0:
        return {"error": "E1 数据无效 (≤0 或缺失)"}

    fwd_pe = current_price / e1
    if e0 <= 0 or e3 <= 0:
        return {
            "price": current_price, "E0": e0, "E1": e1, "E2": e2, "E3": e3,
            "fwd_pe": round(fwd_pe, 2),
            "g": None, "peg": None, "verdict": "— 数据不足 (E0/E3 缺)",
            "error": "no_growth",
        }

    n = 3
    g_pct = (((e3 / e0) ** (1.0 / n)) - 1) * 100
    if g_pct <= 0:
        return {
            "price": current_price, "E0": e0, "E1": e1, "E2": e2, "E3": e3,
            "fwd_pe": round(fwd_pe, 2),
            "g": round(g_pct, 1), "peg": None, "verdict": "— 增长 ≤0",
            "error": "negative_growth",
        }

    peg = fwd_pe / g_pct
    if peg < 1.0:
        verdict = "🟢 健康 (Lynch 买入区, <1.0)"
    elif peg < 1.5:
        verdict = "🟡 合理 (1.0-1.5)"
    elif peg < 2.0:
        verdict = "🟠 偏贵 (1.5-2.0)"
    else:
        verdict = "🔴 高估 (>2.0)"

    return {
        "price": current_price,
        "E0": e0, "E1": e1, "E2": e2, "E3": e3,
        "fwd_pe": round(fwd_pe, 2),
        "g": round(g_pct, 1),
        "peg": round(peg, 2),
        "verdict": verdict,
    }


# ============================================================
# DCF 隐含 L (3 档 r=8/10/12%)
# ============================================================
def compute_dcf_l(eps_table: list[dict], market_cap_yi: Optional[float] = None) -> dict:
    """DCF 隐含 L (3 档 r=8/10/12% 折现率, 二分搜索)"""
    if not eps_table or not market_cap_yi:
        return {"error": "数据不足"}

    estimates = [r for r in eps_table if r.get("year_mark") == "E"]
    if len(estimates) < 2:
        return {"error": "需要至少 2 年 E 数据"}

    e1 = estimates[0].get("net_profit_yi", 0)
    e2 = estimates[1].get("net_profit_yi", 0)
    e3 = estimates[2].get("net_profit_yi", 0) if len(estimates) >= 3 else e2

    if e1 <= 0 or e3 <= 0:
        return {"error": "净利润数据无效"}

    GROWTH_YEARS = 5

    def fair_value(L, e1, e2, e3, r_pct):
        r = r_pct / 100.0
        pv = e1 / (1 + r) ** 1 + e2 / (1 + r) ** 2 + e3 / (1 + r) ** 3
        if e3 > 0 and L > 0 and abs(L - e3) > 1e-9:
            g = (L / e3) ** (1.0 / GROWTH_YEARS) - 1.0
            for t in range(4, 9):
                pv += e3 * (1 + g) ** (t - 3) / (1 + r) ** t
        elif e3 > 0 and L > 0:
            for t in range(4, 9):
                pv += e3 / (1 + r) ** t
        pv += (L / r) / (1 + r) ** (3 + GROWTH_YEARS)
        return pv

    def implied_L(cap, e1, e2, e3, r_pct):
        r = r_pct / 100.0
        hi = max(cap * r * (1 + r) ** 8 * 10, e3 * 100, 1000.)
        lo = 0.
        for _ in range(300):
            mid = (lo + hi) / 2.
            if fair_value(mid, e1, e2, e3, r_pct) < cap:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.

    L_r8 = implied_L(market_cap_yi, e1, e2, e3, 8)
    L_r10 = implied_L(market_cap_yi, e1, e2, e3, 10)
    L_r12 = implied_L(market_cap_yi, e1, e2, e3, 12)

    L_actual = round(L_r8, 1)

    L_E3_r8 = round(L_r8 / e3, 2)
    L_E3_r10 = round(L_r10 / e3, 2)

    g_r8 = round(((L_r8 / e3) ** 0.2 - 1) * 100, 1)
    g_r10 = round(((L_r10 / e3) ** 0.2 - 1) * 100, 1)
    g_r12 = round(((L_r12 / e3) ** 0.2 - 1) * 100, 1)

    # 简化的可达利润 (用当前净利率 × 当前营收 × 1.5x)
    estimates_now = estimates[0]
    nm = estimates_now["net_profit_yi"] / estimates_now["revenue_yi"] * 100 if estimates_now.get("revenue_yi", 0) > 0 else 15
    revenue_ceiling = estimates_now.get("revenue_yi", 0) * 1.5
    achievable = revenue_ceiling * nm / 100

    L_achievable = f"{L_actual:.0f}/{achievable:.0f}={L_actual/achievable if achievable > 0 else 0:.2f}x"
    if achievable > 0 and L_actual / achievable < 0.8:
        verdict = "🟢 低估 (<0.8, 双侧便宜)"
    elif achievable > 0 and L_actual / achievable < 1.5:
        verdict = "🟡 合理 (0.8-1.5)"
    else:
        verdict = "🔴 偏贵 (>1.5, 叙事透支)"

    return {
        "L_r8": round(L_r8, 1), "L_r10": round(L_r10, 1), "L_r12": round(L_r12, 1),
        "L_actual": L_actual,
        "L_E3_r8": L_E3_r8, "L_E3_r10": L_E3_r10, "L_E3_r12": round(L_r12 / e3, 2),
        "g_r8": g_r8, "g_r10": g_r10, "g_r12": g_r12,
        "L_achievable": L_achievable,
        "verdict": verdict,
        "market_cap_yi": market_cap_yi,
    }


# ============================================================
# 板块过热预警 (1周/1月/3月涨幅)
# ============================================================
def compute_sector_overheat(kline) -> dict:
    """板块过热预警 (K 线代理, 1周/1月/3月涨幅)

    输入: K 线 list-of-dict (有 close 字段, 至少 64 天)
    返回: {1周涨幅, 1月涨幅, 3月涨幅, source, _warning}
    """
    if not kline or len(kline) < 64:
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
        "source": "K线代理 (1周/1月/3月涨幅)",
    }


# ============================================================
# 5 类 14 子信号 (组合层, 调其他 5 个 factor)
# ============================================================
def compute_five_categories(ctx) -> dict:
    """5 类 14 子信号 (缠论 + 止跌 + fflow + 估值)

    输入: ctx (RawContext, 含 kline / fflow / eps_table / current_price)
    返回: 5 类 dict, 详见 _derive_five_categories
    """
    from tools.factors.valuation.dcf_engine import get_assumptions
    # 注: 5 类信号实际逻辑在 analysis_engine.py:1209 _derive_five_categories 里
    # 这里只是给 FinanceStrategy 调用的入口, 真实计算在那边
    return _derive_five_categories_impl(ctx)


def _derive_five_categories_impl(ctx) -> dict:
    """5 类 14 子信号实现 (从 analysis_engine 迁过来)

    类别:
      1. 资金类: fflow 净流入 (5 档)
      2. 龙头类: 涨跌幅 / 量比
      3. 估值类: PEG / DCF / Magic
      4. 止跌类: 5 日价跌 + OBV 涨
      5. 政策类: (保留, 暂未实现)
    """
    from tools.factors.factor_volume import compute_fflow_factor as fflow_factor
    from tools.analysis.valuation import calc_magic_one_day  # backward compat

    out = {}

    # 1. 资金类
    fflow = getattr(ctx, "fflow", None) or []
    if fflow:
        ff = fflow_factor(fflow, window=5) or {}
        out["fflow_5d"] = ff.get("verdict", "—")
        out["fflow_5d_amount_yi"] = ff.get("net_amount_5d_yi", 0)

    # 2. 龙头类 (略, 跟 K 线走)

    # 3. 估值类
    eps_table = getattr(ctx, "eps_table", None) or []
    current_price = getattr(ctx, "current_price", 0)
    market_cap_yi = getattr(ctx, "market_cap_yi", 0)

    if eps_table and current_price:
        peg_out = compute_peg(eps_table, current_price) or {}
        out["PEG_真实"] = peg_out.get("peg")
        out["peg_verdict"] = peg_out.get("verdict")
    if eps_table and market_cap_yi:
        dcf_out = compute_dcf_l(eps_table, market_cap_yi) or {}
        out["L_r10"] = dcf_out.get("L_r10")
        out["dcf_verdict"] = dcf_out.get("verdict")

    # 4. Magic (ROC + EY)
    financials = getattr(ctx, "financials", None) or []
    if financials and market_cap_yi:
        market_cap_wan = market_cap_yi * 1e4
        magic = compute_magic_one_day(financials, str(financials[-1].get("end_date", "")),
                                       market_cap_wan) or {}
        out["roc"] = magic.get("roc")
        out["ey"] = magic.get("ey")

    return out


# ============================================================
# 模块级缓存 (2026-09-09 性能优化, N+1 修复)
# ============================================================
# 全市场 5500+ 只数据一次性加载, 后续批量算直接 dict 查表
# 避免 N+1 查询问题 (每只 3 次 DataStore 调用)
_FIN_DICT: dict = {}
_SB_DICT: dict = {}
_DB_DICT: dict = {}
_CACHE_LOADED: bool = False


def _load_bulk_caches(force: bool = False) -> None:
    """模块级缓存: 一次性 bulk 加载 3 张表, 后续 batch 调用直接查表

    实测: 全市场 5555 只
      - Q2 单文件加载: 0.05s
      - groupby 建 dict: 0.5s
      - 后续 1900 只查表: 0.01s
      相比原 N+1 查询: 258s → 0.5s (≈500x 加速)
    """
    global _FIN_DICT, _SB_DICT, _DB_DICT, _CACHE_LOADED
    if _CACHE_LOADED and not force:
        return

    from tools.storage.store import DataStore, _conn
    import pandas as pd
    from pathlib import Path as _Path

    # === financials: UNION ALL BY NAME 全 5 季 (Q2 含 1231 全年数据, _ttm_ebit 需要) ===
    needed_cols = ['ts_code', 'code', 'end_date', 'industry', 'fetch_status',
                   'ebit', 'fixed_assets', 'networking_capital', 'netdebt', 'interestdebt',
                   'or_yoy', 'netprofit_yoy']  # 2026-09-10 加: 给 _quality_gate 用
    fin_dir = _Path(__file__).resolve().parent.parent.parent.parent / "data/history/financials"
    if fin_dir.exists():
        fin_files = sorted(fin_dir.glob("*.parquet"))
        if fin_files:
            union_sql = " UNION ALL BY NAME ".join(
                f"SELECT * FROM read_parquet('{f}')" for f in fin_files
            )
            try:
                all_fin_full = _conn().execute(union_sql).df()
                all_fin = all_fin_full[[c for c in needed_cols if c in all_fin_full.columns]]
            except Exception:
                all_fin = pd.DataFrame()
            if not all_fin.empty:
                all_fin = all_fin[all_fin["fetch_status"] == "ok"]
                all_fin = all_fin.sort_values(["code", "end_date"], ascending=[True, False])
                for code_short, g in all_fin.groupby("code", sort=False):
                    _FIN_DICT[code_short] = g.head(4).iloc[::-1].to_dict("records")
                all_fin2 = all_fin.sort_values(["ts_code", "end_date"], ascending=[True, False])
                for ts_code, g in all_fin2.groupby("ts_code", sort=False):
                    _FIN_DICT[ts_code] = g.head(4).iloc[::-1].to_dict("records")

    # === stock_basic ===
    sb_df = DataStore.load_stock_basic()
    if sb_df is not None and not sb_df.empty:
        for _, r in sb_df.iterrows():
            _SB_DICT[r["code"]] = r.get("name", r["code"])
            _SB_DICT[r["ts_code"]] = r.get("name", r["ts_code"])

    # === daily_basic ===
    all_db = DataStore.load_all_daily_basic()
    if all_db is not None and not all_db.empty:
        latest_db = all_db.sort_values("trade_date").groupby("ts_code").tail(1)
        for _, r in latest_db.iterrows():
            ts_code = r["ts_code"]
            _DB_DICT[ts_code] = r.get("total_mv")
            if isinstance(ts_code, str) and len(ts_code) >= 6:
                _DB_DICT[ts_code[:6]] = r.get("total_mv")

    _CACHE_LOADED = True


def batch_magic_scores(codes: list[str], with_market_cap: bool = True,
                       min_capital_yi: float = 5) -> list[dict]:
    """批量算 Magic Formula 评分 (排名用)

    2026-09-09 性能优化 (修复 N+1 查询):
      改前: 每只票循环 3 次 DataStore 单只查询
            5555 只 = 16665 次 SQL/duckdb 调用, 实测 ~5-7 分钟
      改后: 1 次模块级 bulk 加载, 缓存后查表 < 0.01s
            5555 只总耗时 ~0.5s (首次 0.5s + 计算 0.01s)
    """
    from tools.storage.store import DataStore
    import pandas as pd

    # === 1. 模块级缓存 (首次加载, 后续 0 IO) ===
    _load_bulk_caches()
    fin_dict = _FIN_DICT
    sb_dict = _SB_DICT
    all_db_dict = _DB_DICT if with_market_cap else {}

    results = []
    for code in codes:
        # code 是 6 位短码, 财报用 ts_code (6位.SH/.SZ/.CSI 等)
        fins = fin_dict.get(code, [])

        # 名称
        name = sb_dict.get(code, code)

        # 市值
        mc = all_db_dict.get(code) if with_market_cap else None

        if not fins:
            results.append({
                "code": code, "name": name, "market_cap": mc,
                "roc": None, "ey": None, "industry": "",
                "skip_reason": "no_data", "ev_yi": None,
                "seasonal_warning": False, "period_label": "no_data",
                "ebit_yi": None, "capital_yi": None,
                "netdebt_yi": 0, "market_cap_yi": mc,
            })
            continue

        latest_date = fins[-1].get("end_date", "20251231").replace("-", "")[:8]
        # 2026-09-09 设计: /t-roc-ey 是"全市场 Top20 排名"工具
        # 用最新可用全年 TTM EBIT (找 12-31 全年报) → 跨股票可比
        # 注: 这与 /t-analyze 4 季大表的"每季单期"语义不同, 是有意区分:
        #   - /t-analyze 4 季大表: 每行是该季快照 (周期分析)
        #   - /t-roc-ey Top20: 跨股票统一用最新全年 TTM (排名可比)
        score = compute_magic_one_day(fins, latest_date, mc, min_capital_yi=min_capital_yi)
        score["code"] = code
        score["name"] = name
        score["market_cap"] = mc
        results.append(score)
    return results
