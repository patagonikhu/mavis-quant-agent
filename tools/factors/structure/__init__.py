"""
structure/__init__.py - 结构分析算法库 (OB / FVG / Sweep, 按 factor 库组织)

5 个子模块:
  - atr: ATR (14 根 TR 平均, OB/FVG 过滤用)
  - order_blocks: Order Block (主力订单块)
  - fvg: Fair Value Gap (公允价值缺口, 含 mitigation 进度)
  - swings_sweeps: Swing 高低点 + Liquidity Sweep (假突破)
  - analysis: structure_analysis 主入口

Factor 抽象层 (output_type=dict):
  - ob: OB 5 档判定
"""
from .atr import calc_atr
from .order_blocks import find_order_blocks
from .fvg import find_fvg, _fvg_mitigation_pct
from .swings_sweeps import find_swings, find_liquidity_sweeps
from .analysis import structure_analysis

__all__ = [
    'calc_atr', 'find_order_blocks', 'find_fvg', '_fvg_mitigation_pct',
    'find_swings', 'find_liquidity_sweeps', 'structure_analysis',
]
