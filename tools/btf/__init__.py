"""
tools/btf/__init__.py — BackTest Framework 统一入口

4 抽象类 + 5 Strategy + 4 Evaluator + 1 Portfolio + Runner:
  - DataLayer: AnalysisCacheData (analysis_cache.db 读)
  - Strategy: EY8 / ROC25 / ROC25EY8 / PEG15 / Market
  - Portfolio: EqualWeightPortfolio (等权)
  - Evaluator: Return / Risk / RiskAdjReturn / Trading (4 维)
  - BacktestRunner: 串联 4 抽象

设计: 4 抽象都是 Strategy pattern, 跑回测时自由组合.

用法示例:
    from tools.btf import (
        BacktestRunner, AnalysisCacheData,
        EY8Strategy, ROC25Strategy, ROC25EY8Strategy, PEG15Strategy, MarketStrategy,
        EqualWeightPortfolio,
        ReturnEvaluator, RiskEvaluator, RiskAdjReturnEvaluator, TradingEvaluator,
    )

    data = AnalysisCacheData()
    pf   = EqualWeightPortfolio(hold_days=20)
    evals = [ReturnEvaluator(), RiskEvaluator(), RiskAdjReturnEvaluator(), TradingEvaluator()]

    for StratCls in [EY8Strategy, ROC25Strategy, ROC25EY8Strategy, PEG15Strategy, MarketStrategy]:
        runner = BacktestRunner(data, StratCls(), pf, evals)
        metrics, _, _ = runner.run(start='20250825', end='20260901', top_n=20)
        print(StratCls.name, metrics)
"""
from tools.btf.framework import (
    # 数据结构
    Trade, NavPoint,
    # DataLayer
    DataLayer, AnalysisCacheData,
    # Strategy
    Strategy, EY8Strategy, ROC25Strategy, ROC25EY8Strategy, PEG15Strategy, MarketStrategy,
    # Portfolio
    Portfolio, EqualWeightPortfolio,
    # Evaluator
    Evaluator, ReturnEvaluator, RiskEvaluator, RiskAdjReturnEvaluator, TradingEvaluator,
)
from tools.btf.runner import BacktestRunner

__all__ = [
    "Trade", "NavPoint",
    "DataLayer", "AnalysisCacheData",
    "Strategy", "EY8Strategy", "ROC25Strategy", "ROC25EY8Strategy", "PEG15Strategy", "MarketStrategy",
    "Portfolio", "EqualWeightPortfolio",
    "Evaluator", "ReturnEvaluator", "RiskEvaluator", "RiskAdjReturnEvaluator", "TradingEvaluator",
    "BacktestRunner",
]
