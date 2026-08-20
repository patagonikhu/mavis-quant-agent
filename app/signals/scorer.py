"""信号融合与评分

加权合并 5 大类信号（量价 + 龙头 + 资金 + 政策 + 情绪），
输出 SectorSignalReport。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from app.signals.base import SignalResult


# ---- 权重配置（满分100）----

SIGNAL_WEIGHTS: dict[str, float] = {
    # 量价（30分）
    "volume_breakout": 10.0,
    "limit_up_surge": 15.0,
    "volume_price_uptrend": 5.0,
    "breakout_resistance": 8.0,
    # 龙头（25分）
    "leader_launching": 15.0,
    "sector_diffusion": 10.0,
    # 资金（20分）
    "main_capital_inflow": 10.0,
    "north_capital_anomaly": 5.0,
    "institutional_concentration": 5.0,
    # 政策（15分）
    "policy_keyword_hit": 5.0,
    "llm_policy_score": 10.0,
    # 情绪（10分）
    "hot_rank_surge": 4.0,
    "research_surge": 3.0,
    "discussion_anomaly": 3.0,
}

_MAX_SCORE = sum(SIGNAL_WEIGHTS.values())  # 123分（满分）


def _try_load_optimized_weights() -> None:
    """启动时尝试加载参数优化保存的最优权重"""
    try:
        from app.backtest.optimizer import load_best_weights
        best = load_best_weights()
        if best:
            SIGNAL_WEIGHTS.clear()
            SIGNAL_WEIGHTS.update(best)
            global _MAX_SCORE
            _MAX_SCORE = sum(SIGNAL_WEIGHTS.values()) or 1.0
    except Exception:
        pass  # 加载失败静默降级到默认权重


_try_load_optimized_weights()


def _normalize(raw: float) -> float:
    """把原始分标准化到 0-100"""
    return round(min(100.0, raw / _MAX_SCORE * 100), 1)


def classify_rating(score: float) -> str:
    if score >= 70:
        return "强信号 ⭐⭐⭐⭐⭐"
    elif score >= 50:
        return "中等信号 ⭐⭐⭐⭐"
    elif score >= 30:
        return "弱信号 ⭐⭐⭐"
    elif score >= 15:
        return "观察 ⭐⭐"
    return "无信号"


@dataclass
class SectorSignalReport:
    """板块信号综合报告"""

    sector_name: str
    evaluate_date: datetime.date = field(default_factory=datetime.date.today)

    # 评分
    raw_score: float = 0.0       # 加权原始分
    total_score: float = 0.0     # 标准化到0-100
    rating: str = "无信号"

    # 各信号明细
    signal_details: dict[str, SignalResult] = field(default_factory=dict)

    # 触发的信号列表
    triggered_signals: list[dict[str, Any]] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            f"板块: {self.sector_name}",
            f"评分: {self.total_score:.0f}/100  {self.rating}",
            f"日期: {self.evaluate_date}",
            "",
            "【触发信号】",
        ]
        if not self.triggered_signals:
            lines.append("  无触发信号")
        for ts in self.triggered_signals:
            lines.append(f"  ✓ {ts['name']}: {ts['reason']}")
        return "\n".join(lines)


def calculate_score(
    sector_name: str,
    signal_results: dict[str, SignalResult],
    evaluate_date: datetime.date | None = None,
    sector_bars: "list | None" = None,
) -> SectorSignalReport:
    """融合各信号，输出 SectorSignalReport

    Args:
        sector_name: 板块名称
        signal_results: {signal_key: SignalResult} 各信号检测结果
        evaluate_date: 评估日期（默认今日）
        sector_bars: 板块K线（可选，用于 ML 特征提取）
    """
    raw_score = 0.0
    triggered = []

    for signal_name, weight in SIGNAL_WEIGHTS.items():
        result = signal_results.get(signal_name)
        if result is None:
            continue
        if result.triggered:
            actual = (result.score / 10.0) * weight
            raw_score += actual
            triggered.append({
                "name": signal_name,
                "weight": weight,
                "actual_score": round(actual, 1),
                "reason": result.reason,
                "detail": result.detail,
            })

    rule_total = _normalize(raw_score)

    # XGBoost 二次校准（有模型且有K线时启用）
    total = rule_total
    if sector_bars:
        try:
            from app.ml.feature_engineering import extract_features
            from app.ml.xgboost_calibrator import get_calibrator

            tmp_report = SectorSignalReport(
                sector_name=sector_name,
                signal_details=signal_results,
                total_score=rule_total,
            )
            fv = extract_features(tmp_report, sector_bars)
            calibrator = get_calibrator()
            total = calibrator.calibrate_score(rule_total, fv)
        except Exception:
            pass  # 退化为规则分

    return SectorSignalReport(
        sector_name=sector_name,
        evaluate_date=evaluate_date or datetime.date.today(),
        raw_score=round(raw_score, 1),
        total_score=total,
        rating=classify_rating(total),
        signal_details=signal_results,
        triggered_signals=triggered,
    )
