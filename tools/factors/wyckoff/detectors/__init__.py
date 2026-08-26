"""
detectors/__init__.py - 9 sub_event detector + scanner

公开 API:
  detect_spring / detect_lps / detect_evr / detect_sos / detect_compression
  detect_trend_pullback / detect_markup_entry / detect_distribution_start / detect_upthrust
  scan_sub_events (扫整段 K 线, 返 9 sub_event 集合)
  SUB_EVENT_GLOSSARY: 9 sub_event 中文名/含义/阶段 (报告渲染用)
"""
from .spring import detect_spring
from .lps import detect_lps
from .evr import detect_evr
from .sos import detect_sos
from .compression import detect_compression
from .trend_pullback import detect_trend_pullback
from .markup_entry import detect_markup_entry
from .distribution_start import detect_distribution_start
from .upthrust import detect_upthrust
from .sub_event_scanner import scan_sub_events


# 9 sub_event 中文名 + 含义 + 阶段 (从 wyckoff.py 155 行搬过来, 跟 WyckoffTradingAgent 1:1)
SUB_EVENT_GLOSSARY = {
    # ── Accumulation 累积阶段 sub-event (3) ──
    "Spring": ("弹簧", "假跌破区间下沿后快速收回, 最强吸筹信号", "Accumulation 末段 → Markup 起点"),
    "LPS":    ("最后供给点", "回踩 MA20 + 缩量 + MA20 上升, 派发后/跌势反弹结束", "派发后/跌势"),
    "EVR":    ("巨量+滞涨", "Effort vs Result, 巨量但价格不动, 主力意图最直接信号", "Distribution 关键 / Accumulation 末段"),
    # ── Markup 主升浪 sub-event (4) ──
    "SOS":              ("强势信号", "放量突破区间上沿 (vol>3× + 单日≥6%), 主升浪启动", "Markup 起点"),
    "Compression":      ("压缩蓄势", "连续 N 日 ATR 收窄+缩量, 爆发前夜形态", "Markup 中段整理"),
    "TrendPullback":    ("趋势回踩", "上升趋势中回踩 MA20, 入场机会", "Markup 中段"),
    "MarkupEntry":      ("主升浪入场", "突破 + 量能确认 + 趋势确认, 入场点", "Markup 起点"),
    # ── Distribution 派发阶段 sub-event (2) ──
    "DistributionStart": ("派发起点", "顶部放量 + 趋势转弱, 派发开始", "Distribution 起点"),
    "UTAD":              ("派发后上探", "派发后再次上探前期阻力 (bias_200>15%), 强顶部信号", "Distribution 末段"),
}


__all__ = [
    'detect_spring', 'detect_lps', 'detect_evr', 'detect_sos', 'detect_compression',
    'detect_trend_pullback', 'detect_markup_entry', 'detect_distribution_start',
    'detect_upthrust', 'scan_sub_events', 'SUB_EVENT_GLOSSARY',
]

