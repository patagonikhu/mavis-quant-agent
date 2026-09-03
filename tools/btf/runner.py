"""
tools/btf/runner.py — 回测运行器, 串联 Strategy + Portfolio + DataLayer + Evaluator

用法:
    from tools.btf import (
        BacktestRunner, AnalysisCacheData,
        EY8Strategy, EqualWeightPortfolio,
        ReturnEvaluator, RiskEvaluator, RiskAdjReturnEvaluator, TradingEvaluator,
    )

    data = AnalysisCacheData()
    pf   = EqualWeightPortfolio(hold_days=20)
    strat = EY8Strategy()
    evals = [ReturnEvaluator(), RiskAdjReturnEvaluator(), TradingEvaluator()]

    runner = BacktestRunner(data, strat, pf, evals)
    metrics, nav_history, trades = runner.run(start='20250825', end='20260901', top_n=20)

    print(metrics)  # 4 个 evaluator 的 13 个指标
"""
from __future__ import annotations

import time
from typing import Optional

import pandas as pd

from tools.btf.framework import (
    AnalysisCacheData, Strategy, Portfolio, Evaluator, NavPoint, Trade,
)


class BacktestRunner:
    """1 策略 1 年回测, 串联 4 抽象"""

    def __init__(
        self,
        data: AnalysisCacheData,
        strategy: Strategy,
        portfolio: Portfolio,
        evaluators: list[Evaluator],
    ):
        self.data = data
        self.strategy = strategy
        self.portfolio = portfolio
        self.evaluators = evaluators

    def run(self, start: str = "20250825", end: str = "20260901", top_n: int = 20) -> tuple[dict, list, list]:
        """跑回测, 返 (metrics, nav_history, trades)"""
        t0 = time.time()
        # 1) 加载数据
        df = self.data.load_window(start, end)
        if df.empty:
            print(f"❌ 数据空: {start} → {end}")
            return {}, [], []
        n_dates = df["date"].nunique()
        n_codes = df["code"].nunique()
        print(f"📊 数据: {n_dates} 天 × {n_codes} 票 = {len(df):,} 行")

        # 2) 按日期循环
        rebalance = getattr(self.portfolio, "rebalance", True)
        first_signal_done = False
        for i, date in enumerate(sorted(df["date"].unique())):
            today_df = df[df["date"] == date]
            prices = dict(zip(today_df["code"], today_df["close"]))

            # 2a) 调仓: 长持模式只第 1 天选股, 之后不调
            if rebalance or not first_signal_done:
                codes = self.strategy.select(date, today_df, top_n=top_n)
                self.portfolio.on_signal(date, codes, prices)
                first_signal_done = True
            # 2b) 更新 NAV (卖到期)
            self.portfolio.on_eod(date, prices)

            if (i + 1) % 20 == 0 or i == n_dates - 1:
                elapsed = time.time() - t0
                nav = self.portfolio.nav_history[-1].nav if self.portfolio.nav_history else 1.0
                print(f"   [{i+1}/{n_dates}] {date} | NAV={nav:.4f} | "
                      f"持仓 {self.portfolio.holdings and len(self.portfolio.holdings) or 0} 只 | "
                      f"{elapsed:.1f}s")

        # 3) 跑所有 evaluator
        all_metrics: dict[str, dict] = {}
        for ev in self.evaluators:
            try:
                m = ev.compute(self.portfolio.nav_history, self.portfolio.trades)
                all_metrics[ev.name] = m
            except Exception as e:
                print(f"   ⚠️ {ev.name} 失败: {e}")
                all_metrics[ev.name] = {}

        print(f"\n✅ 完成: {n_dates} 天, {len(self.portfolio.trades)} 笔交易, 总耗时 {time.time()-t0:.1f}s")
        return all_metrics, self.portfolio.nav_history, self.portfolio.trades
