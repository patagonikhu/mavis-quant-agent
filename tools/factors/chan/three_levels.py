"""
chan/three_levels.py - 周/日 二级别缠论分析入口

主函数:
  build_chan_levels(code, name, get_day)
      — 完整二级别分析, 返回 (res_w, res_d, bc_w, bc_d)
  format_chan_table(res_w, res_d, bc_w, bc_d, stop_signal, p)
      — 渲染二级别中枢+背驰表格

向后兼容别名 (供旧代码过渡):
  analyze_three_levels = build_chan_levels
  format_three_hubs    = format_chan_table
"""
from .hub import analyze_hub_v2, find_all_hubs
from .beichi import beichi_from_segs, classify_beichi


def _date(b): return b['trade_date'] if isinstance(b, dict) else b[0]
def _open(b): return b['open'] if isinstance(b, dict) else b[1]
def _close(b): return b['close'] if isinstance(b, dict) else b[2]
def _high(b): return b['high'] if isinstance(b, dict) else b[3]
def _low(b): return b['low'] if isinstance(b, dict) else b[4]
def _vol(b): return b.get('volume', b.get('vol', 0)) if isinstance(b, dict) else b[5]


def build_chan_levels(code, name, get_day, get_60m=None):
    """
    周/日 二级别缠论分析

    get_day: callable, 返回 [{trade_date, open, close, high, low, volume}, ...]
    get_60m: 保留参数兼容旧调用, 不再使用

    返回: (res_w, res_d, bc_w, bc_d)
      res_*  — analyze_hub_v2 结果
      bc_*   — beichi_from_segs 结构体 (含 display / bc_type / direction / strength)
    """
    kd_day = get_day(code)
    if not kd_day or len(kd_day) < 30:
        return None

    # 周线：5 根日线合成 1 根
    week = []
    for i in range(0, len(kd_day) - 4, 5):
        chunk = kd_day[i:i + 5]
        if len(chunk) < 5:
            break
        week.append({
            'trade_date': _date(chunk[0]),
            'open':  _open(chunk[0]),
            'close': _close(chunk[-1]),
            'high':  max(_high(c) for c in chunk),
            'low':   min(_low(c) for c in chunk),
            'volume': sum(_vol(c) for c in chunk),
        })

    d_w = [_date(b) for b in week]
    c_w = [_close(b) for b in week]
    h_w = [_high(b) for b in week]
    l_w = [_low(b) for b in week]

    d_d = [_date(b) for b in kd_day]
    c_d = [_close(b) for b in kd_day]
    h_d = [_high(b) for b in kd_day]
    l_d = [_low(b) for b in kd_day]

    # 笔→段→中枢
    res_w = analyze_hub_v2(d_w, c_w, h_w, l_w, '周线')
    res_d = analyze_hub_v2(d_d, c_d, h_d, l_d, '日线')

    # 背驰：用已算好的正式段，不重跑 K 线扫描
    p_d = c_d[-1] if c_d else 0
    p_w = c_w[-1] if c_w else 0
    _bc_empty = {'display': '数据不足', 'direction': 'none', 'strength': 'none',
                 'bc_type': 'normal', 'ratio': 0, 'a1': 0, 'a2': 0, 's1_hub': -1, 's2_hub': -1}
    hubs_w = find_all_hubs(res_w.get('segs', []), p_w) if len(c_w) >= 30 else []
    hubs_d = find_all_hubs(res_d.get('segs', []), p_d)
    bc_w = beichi_from_segs(res_w.get('segs', []), c_w, d_w, hubs_w) \
           if len(c_w) >= 30 else _bc_empty
    bc_d = beichi_from_segs(res_d.get('segs', []), c_d, d_d, hubs_d)

    return res_w, res_d, bc_w, bc_d


def format_chan_table(res_w, res_d, bc_w, bc_d, stop_signal, p,
                     res_60=None, bc_60=None):
    """
    渲染二级别中枢 + 背驰 + 止跌表格
    列：级别 / 下沿 / 上沿 / 位置 / 背驰信号(面积) / 趋势背驰 / 止损 / 加仓位 / 止跌
    res_60/bc_60: 保留参数兼容旧调用, 不再使用
    """
    def hub_info(res):
        h = res.get('hub', {})
        if h.get('valid'):
            return f"¥{h['low']:.2f}", f"¥{h['high']:.2f}", h['pos'], h
        return "未识别", "—", "延伸中", {}

    def bc_short(bc):
        disp = bc['display'] if isinstance(bc, dict) else bc
        for kw in ['面积不足', '顶背驰', '底背驰', '无背驰', '回调中', '数据不足', '波段不足', '弱背驰']:
            if kw in disp:
                part = disp.split('段1=')[0].rstrip(' (') if '段1=' in disp else disp[:16]
                return part.strip()
        return disp[:16]

    def bc_area(bc):
        a1 = bc.get('a1', 0) if isinstance(bc, dict) else 0
        a2 = bc.get('a2', 0) if isinstance(bc, dict) else 0
        if a1 > 0:
            return f"{a1:.1f}→{a2:.1f}"
        return "—"

    def op_stop(res, p):
        h = res.get('hub', {})
        return f"止损¥{h['low']:.2f}" if h.get('valid') else "等段结束"

    def op_add(res, p):
        h = res.get('hub', {})
        if not h.get('valid'):
            return "—"
        if p > h['high']:
            return f"回踩¥{h['high']:.2f}"
        if p < h['low']:
            return f"突破¥{h['low']:.2f}"
        return f"突破¥{h['high']:.2f}"

    wl, wu, wp, wh = hub_info(res_w)
    dl, du, dp, dh = hub_info(res_d)

    segs_w = res_w.get('segs', [])
    segs_d = res_d.get('segs', [])
    hubs_w = find_all_hubs(segs_w, p)
    hubs_d = find_all_hubs(segs_d, p)
    trend_w = classify_beichi(bc_w)
    trend_d = classify_beichi(bc_d)

    def row(label, lo, hi, pos, bc_str, trend, res, extra=""):
        area = bc_area(bc_str)
        bc_cell = f"{bc_short(bc_str)} {area}"
        return (f"  | {label:<4} | {lo:<6} | {hi:<6} | {pos:<6} | "
                f"{bc_cell:<26} | {trend:<9} | {op_stop(res, p):<8} | "
                f"{op_add(res, p):<8} |{extra}")

    lines = ["📐 二级中枢 + 背驰 + 止跌\n"]
    lines.append("  | 级别 | 下沿   | 上沿   | 位置   | 背驰信号(面积段1→段2)     | 趋势背驰  | 止损     | 加仓位   |")
    lines.append("  |------|--------|--------|--------|---------------------------|-----------|----------|----------|")
    lines.append(row('周线', wl, wu, wp, bc_w, trend_w, res_w))
    lines.append(row('日线', dl, du, dp, bc_d, trend_d, res_d, f" 止跌:{stop_signal[:6]} |"))
    lines.append("")

    w_ok = bool(wh.get('valid') and wh.get('low', 999) <= p)
    bot = any(
        (isinstance(x, dict) and x.get('direction') == 'bot' and x.get('strength') in ('strong', 'weak'))
        or (isinstance(x, str) and '底背驰' in x)
        for x in [bc_w, bc_d]
    )
    has_stop = stop_signal != "❌无"
    score = int(w_ok) + int(bot) + int(has_stop)
    stars = '★' * score + '○' * (3 - score)
    lines.append(f"  入场三要素: {stars}  中枢{'✅' if w_ok else '❌'}  背驰{'✅' if bot else '❌'}  止跌{'✅' if has_stop else '❌'}")
    lines.append("")
    lines.append("  背驰说明:")
    lines.append("    ⭐趋势背驰: 两个中枢+中枢外背驰 → 可直接减仓(大顶/大底信号)")
    lines.append("    🔵普通背驰: 局部动力衰竭 → 需配合止跌信号才操作")
    lines.append("    🟡盘整背驰: 中枢内震荡 → 仅供参考，假信号多")
    lines.append("    无背驰/面积扩张: 主升浪加速 → 用MA20偏离>30%补充")
    lines.append("  逃顶策略(两层):")
    lines.append("    ⭐趋势顶背驰→减中仓1/3  🔵普通顶背驰+止跌→减波动仓")
    lines.append("    MA20偏离>20%(板块非主升浪)→减波动仓  双重触发→减中仓1/2")
    lines.append("    注：CPO/AI链主升浪中MA20失效，面板弱势股失效")
    return '\n'.join(lines)


# 向后兼容别名
analyze_three_levels = build_chan_levels
format_three_hubs    = format_chan_table
