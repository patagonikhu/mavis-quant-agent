"""
tools/factors/__init__.py - 因子库入口 (Day 1 重构)

Day 1 目标: 建空架子, 不动现有代码
  - 因子抽象类 (Factor)
  - 注册表 (FactorRegistry)
  - 注册几个示例因子 (价格衍生, 时序, 横截面)

Day 2+: 把 dump_data._factor_chan_signals 等逻辑抽进来
"""
from tools.factors.base import (
    Factor,
    FactorConfig,
    FactorMeta,
    safe_div,
    rank_pct,
    ts_rank,
    ts_mean,
    ts_std,
    zscore,
)
from tools.factors.registry import (
    FactorRegistry,
    FactorRunner,
    StandardFactorSets,
    print_registry,
)

__all__ = [
    'Factor',
    'FactorConfig',
    'FactorMeta',
    'FactorRegistry',
    'FactorRunner',
    'StandardFactorSets',
    'print_registry',
    'safe_div',
    'rank_pct',
    'ts_rank',
    'ts_mean',
    'ts_std',
    'zscore',
]

__version__ = '0.1.0'  # Day 1 版本
