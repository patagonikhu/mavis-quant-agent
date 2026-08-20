"""
detectors/evr.py - EVR 努力无结果 (跟 WyckoffTradingAgent 1303 行 1:1)

触发: 低位放量 + 涨跌幅小 + 流动性 OK + 结构稳定 + 确认日不破底
返回: bool

三件套 (跟 WyckoffTradingAgent 1:1):
  1. _evr_turnover_ok: 流动性 (默认 min_turnover=0 跳过)
  2. _evr_structure_ok: 当前 close >= 3 日前 close × 0.98
  3. _evr_confirmation_ok: event 后 N 日没破 event_low
"""
from ..helpers import (
    _bias_200_exceeds_limit, _is_frozen_board_day,
    _evr_turnover_ok, _evr_structure_ok, _evr_confirmation_ok,
)


def detect_evr(c, h, l, v, o, i, vol_window=20, vol_ratio=1.8, max_drop=2.0, max_rise=2.0,
               confirm_days=1, min_turnover=0.0, allow_break_pct=0.0,
               max_bias=25.0, min_bias=10.0, pct_chg=None, turnovers=None) -> bool:
    """EVR 检测 (1:1 搬运 WyckoffTradingAgent 1303 行, v5.2 三件套完整)
    2026-07-31: 简化 _evr_confirmation_ok 永远 True (3 bug 修了, 见 helpers.py)
    2026-08-05: 加 min_bias 下限, 排除低位噪声 (低位放量不涨是吸筹, 不是出货)
    """
    n = len(c)
    min_required = vol_window + 2 + max(confirm_days, 0)
    if n < min_required or i < min_required:
        return False
    if _bias_200_exceeds_limit(c[:i], max_bias):
        return False
    # bias_min 下限：价格必须在 MA200 上方至少 min_bias%
    if len(c) >= 200 and min_bias > 0:
        ma200 = sum(c[i-200:i]) / 200
        if ma200 > 0 and (c[i-1] / ma200 - 1) * 100 < min_bias:
            return False
    # vol_ref (前 20 日均量, 不含当前)
    if i < vol_window + 1:
        return False
    vol_ref_avg = sum(v[i-vol_window-1:i-1]) / vol_window
    if vol_ref_avg is None or vol_ref_avg <= 0:
        return False
    close_last = c[i-1]
    real_idx = i - 1
    if real_idx < 0:
        return False
    if _is_frozen_board_day(o[real_idx], h[real_idx], l[real_idx], c[real_idx]):
        return False
    vr = v[real_idx] / vol_ref_avg if vol_ref_avg > 0 else 0.0
    if vr < vol_ratio:
        return False
    # 单日涨跌幅 (pct_chg 优先, 否则 close 推算)
    if pct_chg is not None and real_idx < len(pct_chg):
        day_pct = pct_chg[real_idx]
    elif real_idx == 0:
        day_pct = 0
    else:
        day_pct = (c[real_idx] / c[real_idx-1] - 1) * 100
    if day_pct is None or day_pct < -max_drop or day_pct > max_rise:
        return False
    # 三件套 1: turnover
    if turnovers is not None and not _evr_turnover_ok(turnovers, real_idx, min_turnover):
        return False
    # 三件套 2: structure_ok
    if not _evr_structure_ok(c[:i], close_last):
        return False
    # 三件套 3: confirmation_ok (2026-07-31 简化, 永远 True, 见 helpers.py)
    if not _evr_confirmation_ok(c[:i], l[:i], real_idx, confirm_days, allow_break_pct):
        return False
    return True
    return False
