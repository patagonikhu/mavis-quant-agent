"""
chan/__init__.py - 缠论算法库

7 个子模块:
  inclusion   — K 线包含关系 (Step 1)
  strokes     — 顶底分型 → 笔 (Step 2+3)
  segments    — 笔 → 段 (Step 4)
  hub         — 中枢 (Step 5)
  beichi      — 背驰 (正式段算法: find_beichi_signals / beichi_str_from_segs / classify_beichi)
  fenxing     — 分型 (底/顶 + 确认)
  three_levels — 三级别入口 (build_chan_levels / format_chan_table)

Factor 层:
  beichi_60m  — 60分背驰字符串解析
  buy_sell    — 缠论买卖点
  stop_signal — 止跌信号
  verdict     — 缠论综合判定
"""
from .inclusion import merge_inclusion
from .strokes import find_strokes_full
from .segments import find_segments_full
from .hub import (
    analyze_hub_v2, format_hub_v2, find_all_hubs,
)
from .beichi import (
    seg_red_area, seg_green_area,
    find_beichi_signals, beichi_from_segs, beichi_str_from_segs,
    classify_beichi,
)
from .fenxing import (
    is_bottom_fenxing, is_top_fenxing, fenxing_confirmed, has_recent_confirmed_fenxing,
)
from .three_levels import (
    build_chan_levels, format_chan_table,
    # 向后兼容别名
    analyze_three_levels, format_three_hubs,
)

__all__ = [
    # 核心算法
    'merge_inclusion', 'find_strokes_full', 'find_segments_full',
    'find_hub_from_segs_v2', 'analyze_hub_v2', 'format_hub_v2', 'find_all_hubs',
    'seg_red_area', 'seg_green_area',
    'find_beichi_signals', 'beichi_from_segs', 'beichi_str_from_segs', 'classify_beichi',
    'is_bottom_fenxing', 'is_top_fenxing', 'fenxing_confirmed', 'has_recent_confirmed_fenxing',
    # 三级别入口
    'build_chan_levels', 'format_chan_table',
    # 向后兼容别名
    'analyze_three_levels', 'format_three_hubs',
]
