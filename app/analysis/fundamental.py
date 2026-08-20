"""基本面分析

基于财务数据进行估值评估、盈利能力分析和综合打分。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.data.models import FinancialData

logger = logging.getLogger(__name__)


@dataclass
class FundamentalScore:
    """基本面评分结果"""

    symbol: str
    name: str

    # 各维度评分 (0-100)
    valuation_score: float = 0.0    # 估值评分
    profitability_score: float = 0.0  # 盈利能力评分
    growth_score: float = 0.0       # 成长性评分
    safety_score: float = 0.0       # 安全性评分

    # 综合评分
    total_score: float = 0.0

    # 评价文本
    valuation_comment: str = ""
    profitability_comment: str = ""
    growth_comment: str = ""
    safety_comment: str = ""
    summary: str = ""


def _score_range(value: float, low: float, high: float, reverse: bool = False) -> float:
    """将数值映射到 0-100 评分

    Args:
        value: 原始值
        low: 差阈值
        high: 优阈值
        reverse: True 表示值越小越好 (如 PE)
    """
    if value <= 0:
        return 0.0 if not reverse else 100.0

    if reverse:
        if value <= low:
            return 100.0
        if value >= high:
            return 0.0
        return (high - value) / (high - low) * 100
    else:
        if value >= high:
            return 100.0
        if value <= low:
            return 0.0
        return (value - low) / (high - low) * 100


def score_valuation(data: FinancialData) -> tuple[float, str]:
    """估值评分

    PE < 15: 低估, 15-25: 合理, 25-40: 偏高, > 40: 高估
    PB < 1.5: 低估, 1.5-3: 合理, 3-5: 偏高, > 5: 高估
    """
    pe_score = _score_range(data.pe_ttm, 15, 50, reverse=True) if data.pe_ttm > 0 else 50
    pb_score = _score_range(data.pb, 1.5, 6, reverse=True) if data.pb > 0 else 50

    score = pe_score * 0.6 + pb_score * 0.4

    if data.pe_ttm > 0:
        if data.pe_ttm < 15:
            comment = f"PE(TTM)={data.pe_ttm:.1f}，估值偏低"
        elif data.pe_ttm < 25:
            comment = f"PE(TTM)={data.pe_ttm:.1f}，估值合理"
        elif data.pe_ttm < 40:
            comment = f"PE(TTM)={data.pe_ttm:.1f}，估值偏高"
        else:
            comment = f"PE(TTM)={data.pe_ttm:.1f}，估值较高"
    else:
        comment = "PE 数据缺失"

    return round(score, 1), comment


def score_profitability(data: FinancialData) -> tuple[float, str]:
    """盈利能力评分

    ROE > 15%: 优秀, 10-15%: 良好, 5-10%: 一般, < 5%: 较差
    """
    roe_score = _score_range(data.roe, 5, 20)
    gm_score = _score_range(data.gross_margin, 20, 60)
    nm_score = _score_range(data.net_margin, 5, 25)

    score = roe_score * 0.45 + gm_score * 0.3 + nm_score * 0.25

    if data.roe >= 15:
        comment = f"ROE={data.roe:.1f}%，盈利能力优秀"
    elif data.roe >= 10:
        comment = f"ROE={data.roe:.1f}%，盈利能力良好"
    elif data.roe >= 5:
        comment = f"ROE={data.roe:.1f}%，盈利能力一般"
    else:
        comment = f"ROE={data.roe:.1f}%，盈利能力较弱"

    return round(score, 1), comment


def score_growth(data: FinancialData) -> tuple[float, str]:
    """成长性评分

    营收/净利润增长率 > 20%: 高增长, 10-20%: 稳健, 0-10%: 放缓, < 0: 负增长
    """
    rev_score = _score_range(data.revenue_yoy, 0, 30)
    profit_score = _score_range(data.net_profit_yoy, 0, 30)

    score = rev_score * 0.45 + profit_score * 0.55

    avg_growth = (data.revenue_yoy + data.net_profit_yoy) / 2
    if avg_growth >= 20:
        comment = f"营收增长{data.revenue_yoy:.1f}%，净利增长{data.net_profit_yoy:.1f}%，高增长"
    elif avg_growth >= 10:
        comment = f"营收增长{data.revenue_yoy:.1f}%，净利增长{data.net_profit_yoy:.1f}%，稳健增长"
    elif avg_growth >= 0:
        comment = f"营收增长{data.revenue_yoy:.1f}%，净利增长{data.net_profit_yoy:.1f}%，增速放缓"
    else:
        comment = f"营收增长{data.revenue_yoy:.1f}%，净利增长{data.net_profit_yoy:.1f}%，负增长"

    return round(score, 1), comment


def score_safety(data: FinancialData) -> tuple[float, str]:
    """安全性评分 (偿债能力)

    资产负债率 < 40%: 安全, 40-60%: 合理, 60-70%: 偏高, > 70%: 高风险
    """
    score = _score_range(data.debt_ratio, 30, 70, reverse=True)

    if data.debt_ratio < 40:
        comment = f"资产负债率{data.debt_ratio:.1f}%，财务安全"
    elif data.debt_ratio < 60:
        comment = f"资产负债率{data.debt_ratio:.1f}%，负债合理"
    elif data.debt_ratio < 70:
        comment = f"资产负债率{data.debt_ratio:.1f}%，负债偏高"
    else:
        comment = f"资产负债率{data.debt_ratio:.1f}%，负债较高"

    return round(score, 1), comment


def analyze_fundamentals(data: FinancialData) -> FundamentalScore:
    """综合基本面分析

    Args:
        data: 财务数据

    Returns:
        FundamentalScore 综合评分结果
    """
    val_score, val_comment = score_valuation(data)
    prof_score, prof_comment = score_profitability(data)
    grow_score, grow_comment = score_growth(data)
    safe_score, safe_comment = score_safety(data)

    # 加权综合分 (估值 30%, 盈利 30%, 成长 25%, 安全 15%)
    total = val_score * 0.30 + prof_score * 0.30 + grow_score * 0.25 + safe_score * 0.15

    # 生成总结
    if total >= 75:
        summary = f"综合评分 {total:.1f} 分，基本面优秀"
    elif total >= 60:
        summary = f"综合评分 {total:.1f} 分，基本面良好"
    elif total >= 45:
        summary = f"综合评分 {total:.1f} 分，基本面一般"
    else:
        summary = f"综合评分 {total:.1f} 分，基本面偏弱"

    return FundamentalScore(
        symbol=data.symbol,
        name=data.name,
        valuation_score=val_score,
        profitability_score=prof_score,
        growth_score=grow_score,
        safety_score=safe_score,
        total_score=round(total, 1),
        valuation_comment=val_comment,
        profitability_comment=prof_comment,
        growth_comment=grow_comment,
        safety_comment=safe_comment,
        summary=summary,
    )
