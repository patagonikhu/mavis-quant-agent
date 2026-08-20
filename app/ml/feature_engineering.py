"""ML 特征工程

从 SectorSignalReport 和原始数据中提取标准化特征向量，
供 XGBoost 校准模型使用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from app.data.models import SectorKlineBar
from app.signals.scorer import SectorSignalReport


@dataclass
class FeatureVector:
    """单个板块单日的特征向量"""
    sector_name: str = ""
    # 量价
    volume_ratio: float = 0.0       # 今日量比（相对20日均量）
    price_change: float = 0.0       # 今日涨跌幅
    limit_up_z_score: float = 0.0   # 涨停家数 Z-score
    # 资金
    main_capital_3d: float = 0.0    # 近3日主力净流入之和（万元）
    north_capital_z: float = 0.0    # 北向资金 Z-score
    # 龙头
    leader_consec_limit_up: float = 0.0  # 龙头连续涨停天数
    # 政策
    policy_keyword_score: float = 0.0    # 政策关键词得分
    llm_policy_score: float = 0.0        # LLM 政策评估强度
    # 情绪
    hot_rank_change: float = 0.0         # 热度排名变化（正=上升）
    # 规则综合
    rule_total_score: float = 0.0        # 规则评分（0-100）
    # 技术
    ma20_breakout: float = 0.0           # 是否突破MA20（0/1）
    ma60_breakout: float = 0.0           # 是否突破MA60（0/1）
    high_60d_breakout: float = 0.0       # 是否创60日新高（0/1）
    vol_price_uptrend: float = 0.0       # 连续量价齐升（0/1）

    def to_array(self) -> list[float]:
        return [
            self.volume_ratio,
            self.price_change,
            self.limit_up_z_score,
            self.main_capital_3d,
            self.north_capital_z,
            self.leader_consec_limit_up,
            self.policy_keyword_score,
            self.llm_policy_score,
            self.hot_rank_change,
            self.rule_total_score,
            self.ma20_breakout,
            self.ma60_breakout,
            self.high_60d_breakout,
            self.vol_price_uptrend,
        ]

    @classmethod
    def feature_names(cls) -> list[str]:
        return [
            "volume_ratio", "price_change", "limit_up_z_score",
            "main_capital_3d", "north_capital_z", "leader_consec_limit_up",
            "policy_keyword_score", "llm_policy_score", "hot_rank_change",
            "rule_total_score",
            "ma20_breakout", "ma60_breakout", "high_60d_breakout", "vol_price_uptrend",
        ]


def extract_features(
    report: SectorSignalReport,
    sector_bars: list[SectorKlineBar],
) -> FeatureVector:
    """从信号报告和K线数据中提取特征向量"""
    d = report.signal_details
    fv = FeatureVector(sector_name=report.sector_name)
    fv.rule_total_score = report.total_score

    # ---- 量价特征 ----
    vb = d.get("volume_breakout")
    if vb and vb.detail:
        fv.volume_ratio = float(vb.detail.get("volume_ratio", 0))
        fv.price_change = float(vb.detail.get("price_change_pct", 0))

    lu = d.get("limit_up_surge")
    if lu and lu.detail:
        fv.limit_up_z_score = float(lu.detail.get("z_score", 0))

    br = d.get("breakout_resistance")
    if br and br.detail:
        breaks = br.detail.get("breaks", [])
        fv.ma20_breakout = 1.0 if "突破MA20" in breaks else 0.0
        fv.ma60_breakout = 1.0 if "突破MA60" in breaks else 0.0
        fv.high_60d_breakout = 1.0 if "60日新高" in breaks else 0.0

    vpu = d.get("volume_price_uptrend")
    fv.vol_price_uptrend = 1.0 if (vpu and vpu.triggered) else 0.0

    # ---- 资金特征 ----
    nca = d.get("north_capital_anomaly")
    if nca and nca.detail:
        fv.north_capital_z = float(nca.detail.get("z_score", 0))

    mci = d.get("main_capital_inflow")
    if mci and mci.detail:
        fv.main_capital_3d = float(mci.detail.get("total_inflow_wan", 0))

    # ---- 龙头特征 ----
    ll = d.get("leader_launching")
    if ll and ll.detail:
        fv.leader_consec_limit_up = float(ll.detail.get("consecutive_limit_up", 0))

    # ---- 政策特征 ----
    pk = d.get("policy_keyword_hit")
    if pk and pk.detail:
        fv.policy_keyword_score = float(pk.detail.get("raw_score", 0))

    lp = d.get("llm_policy_score")
    if lp and lp.detail:
        fv.llm_policy_score = float(lp.detail.get("impact_strength", 0))

    # ---- 情绪特征 ----
    hr = d.get("hot_rank_surge")
    if hr and hr.detail:
        fv.hot_rank_change = float(hr.detail.get("rank_improvement", 0))

    return fv
