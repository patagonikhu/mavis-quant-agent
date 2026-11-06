"""
tools/factors/valuation/factor_lib.py — 估值算子全集 (纯函数, 2026-09-09 统一)

历史: 之前 3 个文件重复实现:
  - tools/analysis/valuation.py       (calc_roc_at_date / calc_ey_at_date / calc_magic_one_day)
  - tools/factors/valuation/multi.py  (PegFactor / DcfFactor / SectorOverheatFactor)
                                      [2026-11-02 PEG/DCF 已删; 2026-11-03 FiveCategoriesFactor 已删]
  - (2026-10-27: tools/analysis/report_section_evaluators.py 已删, 是死代码副本)

合并后: 1 个文件, 纯函数, 统一风格, 无 class 包装。
Mavis 守门员 (t-guardrail) 规则: 所有 factor 统一放 1 个文件, 纯函数风格。

2026-11-02 删: compute_peg / compute_dcf_l
  → PEG 是假指标 (依赖 EPS 预测, 预测本身不准), DCF 依赖同一套 EPS 预测.
  → 估值只看 PE (静态) + 盈利质量 (ROC/EY/毛利率/杠杆).

# 公开 API
  - compute_roc(financials, min_capital_yi)            # ROC 资本回报率
  - compute_ey(financials, market_cap_wan)              # EY 盈利收益率
  - compute_magic_one_day(...)                    # ROC+EY 合并
  - find_full_year_financials(financials, as_of_date)  # 找全年财报

# 内部工具
  - _ttm_ebit(financials)                        # TTM EBIT 拼装
  - EXCLUDED_INDUSTRIES (8+5 类)                 # 行业黑名单
"""
from __future__ import annotations

from typing import Optional
from pathlib import Path

# ============================================================
# 行业黑名单 (ROC/EY 在这些行业失真)
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
def compute_roc(financials: list[dict], min_capital_yi: float = 5) -> dict:
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
def compute_ey(financials: list[dict], market_cap_wan: float) -> dict:
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
def compute_magic_one_day(financials: list[dict], market_cap_wan: float,
                          min_capital_yi: float = 5) -> dict:
    """单日 Magic (ROC + EY 1 次 financials + 1 次市场值 读)

    Args:
        min_capital_yi: NWC+FA 最小阈值 (亿), 透传给 compute_roc
    """
    roc_data = compute_roc(financials, min_capital_yi=min_capital_yi)
    ey_data  = compute_ey(financials, market_cap_wan)

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
