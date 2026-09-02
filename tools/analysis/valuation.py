"""
tools/analysis/valuation.py — 估值因子统一入口 (PEG + DCF + Magic 合并)

历史: 之前分 3 处:
  - tools/factors/valuation/multi.py:        PegFactor / DcfFactor (跟 FactorRegistry 配套, 保留)
  - tools/analysis/report_section_evaluators.py: compute_peg / compute_dcf_l (其他模块可能用, 保留)
  - tools/factors/valuation/magic_formula.py: calc_roc / calc_ey / calc_magic_score (全删, 合并到这里)

本文件 = ValuationStrategy 内部用的单日算子 + batch 排名入口。
ValuationStrategy 跑 4 个指标 (PEG + DCF + ROC + EY) 共享 1 次 daily_basic 读。
"""
from __future__ import annotations

from typing import Optional

# 行业过滤 (8 类, Magic Formula 失真)
EXCLUDED_INDUSTRIES = {
    "银行", "保险", "证券", "信托", "期货", "租赁",  # 金融
    "房地产", "物业管理", "园区开发",               # 地产
    "电力", "水务", "燃气", "热力", "环保",          # 公用
    "多元金融",
}


# ============================================================
# 单日算子 (1 个 date 算 1 个 ROC / EY)
# ============================================================

def _ttm_ebit(financials: list[dict]) -> tuple[Optional[float], bool, str]:
    """TTM EBIT (A 股近似, 因为没有季报)

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

    # 倒序找
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


def calc_roc_at_date(financials: list[dict], as_of_date: str) -> dict:
    """单日 ROC (跟 _ttm_ebit 一样, 抹平季节性)

    Returns: {roc, ebit_yi, capital_yi, period_label, seasonal_warning, skip_reason}
    """
    if not financials:
        return {"roc": None, "skip_reason": "no_data"}

    industry = (financials[-1].get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return {"roc": None, "industry": industry, "skip_reason": "industry_excluded"}

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

    roc = round(ebit_ttm / capital * 100, 1)
    return {
        "roc": roc,
        "industry": industry,
        "period_label": period_label,
        "seasonal_warning": seasonal,
        "ebit_yi": round(ebit_ttm / 1e8, 1),
        "capital_yi": round(capital / 1e8, 2),
    }


def calc_ey_at_date(financials: list[dict], as_of_date: str, market_cap_wan: float) -> dict:
    """单日 EY (跟 calc_roc 同一份 financials, 多用 daily_basic.market_cap)

    Args:
        market_cap_wan: 当日市值, 单位"万" (跟 Tushare daily_basic 一致)

    Returns: {ey, ev_yi, market_cap_yi, netdebt_yi, ...}
    """
    if not financials or not market_cap_wan or market_cap_wan <= 0:
        return {"ey": None, "skip_reason": "no_data"}

    industry = (financials[-1].get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return {"ey": None, "industry": industry, "skip_reason": "industry_excluded"}

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
# 合并算子 (ValuationStrategy 用, 4 指标 1 次 financials 读)
# ============================================================

def calc_magic_one_day(financials: list[dict], as_of_date: str, market_cap_wan: float) -> dict:
    """单日 Magic (ROC + EY 1 次 financials + 1 次市场值 读)

    跟 calc_magic_score 区别: 不用取 daily_basic 拿市值 (ValuationStrategy 已经传进来)
    """
    roc_data = calc_roc_at_date(financials, as_of_date)
    ey_data  = calc_ey_at_date(financials, as_of_date, market_cap_wan)

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
# batch 排名入口 (magic_top20.py 用)
# ============================================================

def calc_magic_score(code: str, market_cap: float) -> dict:
    """单只 Magic Formula 综合评分 (给 batch_magic_scores 排名用)

    Returns:
        dict 含 roc, ey, industry, skip_reason, ev_yi, seasonal_warning, period_label,
        ebit_yi, capital_yi, netdebt_yi, market_cap_yi
    """
    from tools.kline_store import DataStore
    financials = DataStore.get_financials(code, lookback_quarters=4)
    if not financials:
        return {
            "roc": None, "ey": None, "industry": "",
            "skip_reason": "no_data", "ev_yi": None,
            "seasonal_warning": False, "period_label": "no_data",
            "ebit_yi": None, "capital_yi": None,
            "netdebt_yi": 0, "market_cap_yi": None,
        }
    # 用最新财务日期 (没 as_of_date, 默认"今天")
    latest_date = financials[-1].get("end_date", "20251231").replace("-", "")[:8]
    return calc_magic_one_day(financials, latest_date, market_cap)


def batch_magic_scores(codes: list[str], with_market_cap: bool = True) -> list[dict]:
    """批量算 Magic Formula 评分 (排名用)

    Args:
        codes: 票列表
        with_market_cap: True (从 DataStore.get_daily_basic 拿市值), False (只算 ROC)
    """
    from tools.kline_store import DataStore
    results = []
    for code in codes:
        sb = DataStore.get_stock_basic(code)
        name = sb.get("name", code) if sb else code

        mc = None
        if with_market_cap:
            db = DataStore.get_daily_basic(code)
            mc = db.get("total_mv") if db else None

        score = calc_magic_score(code, mc)
        results.append({
            "code": code,
            "name": name,
            "market_cap": mc,
            **score,
        })
    return results
