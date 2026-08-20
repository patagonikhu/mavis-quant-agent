"""回测引擎

对历史日期逐日运行信号评估，记录信号触发和后续收益。
依赖：板块K线历史数据（需要足够长的历史，一般2年+）

使用方式：
    engine = BacktestEngine(start_date, end_date)
    records = await engine.run(["半导体", "AI", "新能源车"])
    metrics = calculate_metrics(records)
    print(metrics.report_text())
"""

from __future__ import annotations

import datetime
import logging
from typing import Optional

import numpy as np

from app.backtest.metrics import BacktestRecord, BacktestMetrics, calculate_metrics
from app.data import get_data_provider
from app.data.models import Period, SectorKlineBar

logger = logging.getLogger(__name__)

# 持有天数
HOLD_DAYS = 10
# 胜利阈值：10日内最大涨幅
WIN_THRESHOLD = 0.10


def _get_trading_dates(
    bars: list[SectorKlineBar],
    start: datetime.date,
    end: datetime.date,
) -> list[datetime.date]:
    """从K线数据中提取 [start, end] 范围内的交易日"""
    return [b.trade_date for b in bars if start <= b.trade_date <= end]


def _evaluate_outcome(
    bars: list[SectorKlineBar],
    signal_date: datetime.date,
    hold_days: int = HOLD_DAYS,
) -> tuple[float, float]:
    """计算信号触发后 hold_days 日的收益

    Returns:
        (max_return, close_return_10d)
    """
    future = [b for b in bars if b.trade_date > signal_date][:hold_days]
    if not future:
        return 0.0, 0.0

    signal_bar = next((b for b in bars if b.trade_date == signal_date), None)
    if signal_bar is None or signal_bar.close_price <= 0:
        return 0.0, 0.0

    base = signal_bar.close_price
    closes = [b.close_price for b in future]
    max_return = (max(closes) - base) / base
    close_return = (closes[-1] - base) / base
    return round(max_return, 4), round(close_return, 4)


class BacktestEngine:

    def __init__(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        min_score_to_record: float = 30.0,
    ):
        self.start_date = start_date
        self.end_date = end_date
        self.min_score = min_score_to_record

    async def run(
        self,
        sectors: Optional[list[str]] = None,
    ) -> list[BacktestRecord]:
        """运行回测，返回所有信号触发记录"""
        from app.data.models import SECTOR_LIST
        from app.signals import evaluate_sector
        from app.signals.leader import identify_leaders

        targets = sectors or SECTOR_LIST
        provider = get_data_provider()
        all_records: list[BacktestRecord] = []

        for sector_name in targets:
            logger.info("回测板块: %s", sector_name)
            try:
                # 拉取足够长的历史（回测区间 + HOLD_DAYS buffer）
                bars = await provider.get_sector_kline(sector_name, count=500)
                if len(bars) < 60:
                    logger.warning("%s K线不足，跳过", sector_name)
                    continue

                trading_dates = _get_trading_dates(bars, self.start_date, self.end_date)

                for date in trading_dates:
                    # 截取到当日的K线（模拟当时只有历史数据）
                    bars_to_date = [b for b in bars if b.trade_date <= date]
                    if len(bars_to_date) < 30:
                        continue

                    # 获取龙头K线（简化：只用板块数据代替个股）
                    constituents = []
                    leader_klines: dict = {}
                    try:
                        # 注意：get_sector_constituents 返回的是当前成分股，不是历史日期的成分股。
                        # 这会导致龙头识别略有偏差，是已知局限——完整修复需要历史成分股快照库。
                        constituents = await provider.get_sector_constituents(sector_name)
                        leaders = identify_leaders(constituents, top_n=2)
                        for ldr in leaders:
                            lbars = await provider.get_kline(ldr.symbol, Period.DAILY, 60)
                            lbars_to_date = [b for b in lbars if b.trade_date <= date]
                            if lbars_to_date:
                                leader_klines[ldr.symbol] = lbars_to_date
                    except Exception:
                        pass

                    report = await evaluate_sector(
                        sector_name=sector_name,
                        sector_bars=bars_to_date,
                        constituents=constituents,
                        leader_klines=leader_klines,
                        evaluate_date=date,
                    )

                    if report.total_score < self.min_score:
                        continue

                    max_ret, close_ret = _evaluate_outcome(bars, date, HOLD_DAYS)
                    record = BacktestRecord(
                        date=str(date),
                        sector=sector_name,
                        score=report.total_score,
                        rating=report.rating,
                        max_return=max_ret,
                        close_return_10d=close_ret,
                        is_winner=max_ret >= WIN_THRESHOLD,
                    )
                    all_records.append(record)
                    logger.debug(
                        "%s %s score=%.0f max_ret=%.1f%% win=%s",
                        date, sector_name, report.total_score,
                        max_ret * 100, record.is_winner,
                    )

            except Exception as e:
                logger.error("回测 %s 失败: %s", sector_name, e)
                continue

        logger.info("回测完成，共 %d 条信号记录", len(all_records))
        return all_records

    async def run_and_report(
        self,
        sectors: Optional[list[str]] = None,
    ) -> BacktestMetrics:
        """运行回测并返回汇总指标"""
        records = await self.run(sectors)
        return calculate_metrics(records)
