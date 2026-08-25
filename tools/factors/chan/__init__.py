"""
chan/__init__.py - 缠论算法库 (v4.1, 100% czsc)

所有缠论算法 (分型/笔/中枢/段/背驰/买卖点) 都通过 czsc 计算。
我们的 Python 实现已废弃, 移到 _deprecated/ 目录。

主入口:
  from tools.factors.chan.czsc_adapter import analyze_hub_v2_czsc
  from tools.factors.chan.czsc_wrapper import (
      compute_chan_czsc,           # 主: 算分型/笔/中枢
      has_recent_confirmed_fenxing, # 兼容老 API: 接受 klines
      recent_confirmed_fenxing_from_czsc,
      klines_to_raw_bars, kline_to_raw_bar,
  )
  from tools.factors.chan.czsc_signals import compute_buy_sell_signals  # 1买/3买 等
"""
from .czsc_adapter import (
    analyze_hub_v2_czsc,
    beichi_from_czsc_bis,
    classify_beichi,
)
from .czsc_wrapper import (
    compute_chan_czsc,
    has_recent_confirmed_fenxing,
    recent_confirmed_fenxing_from_czsc,
    klines_to_raw_bars, kline_to_raw_bar,
)
from .czsc_signals import compute_buy_sell_signals

__all__ = [
    # czsc 集成
    'analyze_hub_v2_czsc',
    'beichi_from_czsc_bis',
    'classify_beichi',
    'compute_chan_czsc',
    'has_recent_confirmed_fenxing',
    'recent_confirmed_fenxing_from_czsc',
    'klines_to_raw_bars', 'kline_to_raw_bar',
    'compute_buy_sell_signals',
]
