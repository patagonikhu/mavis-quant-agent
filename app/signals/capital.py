"""资金流信号模块

实现设计文档 4.2 节的四类资金流信号：
- 主力资金连续净流入（main_capital_consecutive_inflow）
- 北向资金加仓异常（north_capital_anomaly）
- 龙虎榜机构席位密集（institutional_concentration）
- 行业ETF申购激增（etf_subscription_surge）—— Phase 2 仅桩实现
"""

from __future__ import annotations

import numpy as np

from app.data.models import LhbRecord, NorthFlowRecord, SectorConstituentStock, SectorFundFlow
from app.signals.base import SignalResult


def main_capital_consecutive_inflow(
    fund_flow_history: list[SectorFundFlow],
    days: int = 3,
) -> SignalResult:
    """主力资金连续净流入信号

    连续 N 天主力净流入均为正，且总量显著超过近20日均值3倍。

    Args:
        fund_flow_history: 板块资金流列表（按日期升序，最新在末尾）
        days: 要求连续天数
    """
    if len(fund_flow_history) < max(days, 5):
        return SignalResult(reason="资金流数据不足")

    recent = fund_flow_history[-days:]
    historical = fund_flow_history[:-days]

    consecutive = all(f.main_net_inflow > 0 for f in recent)
    total_recent = sum(f.main_net_inflow for f in recent)

    if historical:
        avg_abs = float(np.mean([abs(f.main_net_inflow) for f in historical]))
        is_significant = avg_abs > 0 and total_recent > avg_abs * 3
    else:
        is_significant = total_recent > 0

    triggered = consecutive and is_significant
    score = 0.0
    if triggered:
        # 评分基于连续天数和净流入强度
        score = min(10.0, days * 2.0 + (total_recent / max(avg_abs, 1) - 3) * 1.0)

    inflow_wan = round(total_recent / 10000, 1) if total_recent != 0 else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "consecutive_days": days,
            "total_inflow_wan": inflow_wan,
            "is_significant": is_significant,
            "history_avg_abs_wan": round(avg_abs / 10000, 1) if historical else 0,
        },
        reason=f"主力连续{days}日净流入共{inflow_wan}万元" if triggered
               else f"未满足连续{days}日净流入条件",
    )


def north_capital_anomaly(
    north_flow_history: list[NorthFlowRecord],
    lookback: int = 20,
) -> SignalResult:
    """北向资金加仓异常信号（Z-score 检测）

    今日净买入显著异常（Z>2）且为正值。
    """
    if len(north_flow_history) < 5:
        return SignalResult(reason="北向资金数据不足")

    values = [r.net_inflow for r in north_flow_history]
    today = values[-1]
    history = values[:-1]

    mean = float(np.mean(history))
    std = float(np.std(history))
    z_score = (today - mean) / std if std > 0 else 0.0

    triggered = z_score > 2.0 and today > 0
    score = min(10.0, z_score * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_inflow": round(today, 1),
            "mean": round(mean, 1),
            "z_score": round(z_score, 2),
        },
        reason=f"北向今日净买入{today:.0f}万（Z={z_score:.1f}）" if triggered
               else f"北向资金无异常（Z={z_score:.1f}）",
    )


def institutional_concentration(
    lhb_records: list[LhbRecord],
    sector_symbols: set[str],
    window: int = 3,
) -> SignalResult:
    """龙虎榜机构席位密集信号

    近 window 日内，板块成分股出现在龙虎榜且机构席位买入的记录数量。

    Args:
        lhb_records: 近 window 日的龙虎榜数据
        sector_symbols: 板块成分股代码集合
    """
    if not lhb_records:
        return SignalResult(reason="无龙虎榜数据")

    institutional_buys = [
        r for r in lhb_records
        if r.symbol in sector_symbols and r.institutional_buy_count > 0
    ]
    count = len(institutional_buys)
    triggered = count >= 3
    score = min(10.0, count * 2.0) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "institutional_buy_count": count,
            "stocks": list({r.symbol for r in institutional_buys})[:5],
        },
        reason=f"近{window}日{count}只板块股上龙虎榜且机构买入" if triggered
               else f"近{window}日仅{count}只板块股有机构龙虎榜买入",
    )


def etf_subscription_surge(
    etf_shares_history: list[float],
) -> SignalResult:
    """行业 ETF 申购激增信号（Phase 2 简化实现）

    Args:
        etf_shares_history: ETF 份额历史（万份，按日升序，最新在末尾）
    """
    if len(etf_shares_history) < 3:
        return SignalResult(reason="ETF份额数据不足")

    today_change = etf_shares_history[-1] - etf_shares_history[-2]
    past_changes = [
        abs(etf_shares_history[i] - etf_shares_history[i - 1])
        for i in range(1, len(etf_shares_history) - 1)
    ]
    avg_change = float(np.mean(past_changes)) if past_changes else 0.0

    change_ratio = today_change / avg_change if avg_change > 0 else 0.0
    triggered = change_ratio > 3 and today_change > 0
    score = min(10.0, change_ratio * 2) if triggered else 0.0

    return SignalResult(
        triggered=triggered,
        score=score,
        detail={
            "today_change_wan": round(today_change, 1),
            "avg_change_wan": round(avg_change, 1),
            "change_ratio": round(change_ratio, 2),
        },
        reason=f"ETF申购激增 {change_ratio:.1f}倍" if triggered
               else "ETF申购无异常",
    )
