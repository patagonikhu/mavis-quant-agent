"""
chan/beichi.py - 背驰 (缠论, 段算法 + 趋势/盘整/普通分类)

输入: segs/hubs (由 czsc_adapter.analyze_hub_v2_czsc 算的)
算法: 用段的起止日期切 MACD 面积, 对比段选取规则:
       趋势背驰: 两个中枢各自的"离开段"（中枢结束后第一个同向段）
       盘整/普通背驰: 最近两个同向段（无法找到两中枢离开段时的降级）

主要函数:
  seg_red_area(seg, hist, dt2i)       — 单段 MACD 正柱面积
  seg_green_area(seg, hist, dt2i)     — 单段 MACD 负柱面积 (下跌段用)
  beichi_from_segs(segs, closes, dates, hubs) — 结构体背驰结果 (主入口)
  beichi_str_from_segs(...)           — 向后兼容, 返回 display 字符串
  classify_beichi(bc, ...)            — 分类: ⭐趋势/🟡盘整/🔵普通/无

注: find_all_hubs 已在 _deprecated/hub.py (v1 老的), 新版用 czsc_adapter.czsc_zss_to_hub_format
     这里不依赖, 只需 hubs 字段 (leaving_up/left)
"""


def _build_dt2i(dates):
    return {str(d): i for i, d in enumerate(dates)}


def _calc_macd_hist(closes):
    def ema(p, n):
        k = 2 / (n + 1)
        e = [p[0]]
        for x in p[1:]:
            e.append(x * k + e[-1] * (1 - k))
        return e
    e12 = ema(closes, 12)
    e26 = ema(closes, 26)
    dif = [e12[i] - e26[i] for i in range(len(closes))]
    dea = ema(dif, 9)
    return [2 * (dif[i] - dea[i]) for i in range(len(dif))]


def seg_red_area(seg, hist, dt2i):
    sdt, edt = str(seg['sdt']), str(seg['edt'])
    if sdt not in dt2i or edt not in dt2i:
        return 0.0
    i1, i2 = dt2i[sdt], dt2i[edt]
    return sum(x for x in hist[i1:i2 + 1] if x > 0)


def seg_green_area(seg, hist, dt2i):
    sdt, edt = str(seg['sdt']), str(seg['edt'])
    if sdt not in dt2i or edt not in dt2i:
        return 0.0
    i1, i2 = dt2i[sdt], dt2i[edt]
    return sum(abs(x) for x in hist[i1:i2 + 1] if x < 0)


def _seg_hub_index(seg, hubs):
    """seg 所在中枢索引，不在任何中枢内返回 -1"""
    for i, h in enumerate(hubs):
        if seg['sdt'] >= h['sdt'] and seg['edt'] <= h['edt']:
            return i
    return -1


def _hub_leaving_seg(hub_idx, hubs, segs, direction):
    """从中枢结构体直接读离开段（由 find_all_hubs 构建时同步记录）。"""
    h = hubs[hub_idx]
    if direction == 'B':
        s = h.get('leaving_up')
    else:
        s = h.get('leaving_down')
    if s is None:
        return None, None
    # 找该段在 segs 里的索引
    for i, seg in enumerate(segs):
        if seg['sdt'] == s['sdt'] and seg['edt'] == s['edt']:
            return i, seg
    return None, None


def _find_compare_segs(segs, hubs, direction):
    """
    找背驰对比的两段：
    1. 优先：找最近两个中枢各自的离开段 → 趋势背驰候选
    2. 降级：最近两个同向段 → 盘整/普通候选

    返回 (s1, s2, s1_hub_idx, s2_hub_idx, is_trend_candidate)
    """
    same = [(i, s) for i, s in enumerate(segs) if s['sst'] == direction]
    if len(same) < 2:
        return None, None, -1, -1, False

    # 尝试找最近两个中枢的离开段
    if len(hubs) >= 2:
        # 从最新中枢往前找，找到两个各有离开段的中枢
        for j in range(len(hubs) - 1, 0, -1):
            _, s2 = _hub_leaving_seg(j, hubs, segs, direction)
            _, s1 = _hub_leaving_seg(j - 1, hubs, segs, direction)
            if s1 is not None and s2 is not None and s1 is not s2:
                return s1, s2, j - 1, j, True

    # 降级：最近两个同向段
    _, s1 = same[-2]
    _, s2 = same[-1]
    h1 = _seg_hub_index(s1, hubs)
    h2 = _seg_hub_index(s2, hubs)
    return s1, s2, h1, h2, False


def beichi_from_segs(segs, closes, dates, hubs=None):
    """
    主入口：返回结构体背驰结果。

    返回 dict:
      direction  — 'top' / 'bot' / 'none'
      strength   — 'strong' / 'weak' / 'none'  (ratio<0.5 / 0.5-0.8 / >=0.8)
      bc_type    — 'trend' / 'consolidation' / 'normal'
                   trend:         s1/s2 是两个不同中枢的离开段
                   consolidation: s1/s2 在同一中枢内
                   normal:        其他
      ratio      — a2/a1 (float)
      a1, a2     — MACD 面积
      s1_hub     — 前段所在中枢索引 (-1 = 不在任何中枢)
      s2_hub     — 后段所在中枢索引
      display    — 渲染用字符串，格式兼容旧版
    """
    _NONE = {
        'direction': 'none', 'strength': 'none', 'bc_type': 'normal',
        'ratio': 0.0, 'a1': 0.0, 'a2': 0.0,
        's1_hub': -1, 's2_hub': -1, 'display': '波段不足',
    }
    if not segs or len(closes) < 30:
        return {**_NONE, 'display': '数据不足'}

    hist  = _calc_macd_hist(closes)
    dt2i  = _build_dt2i(dates)
    if hubs is None:
        hubs = find_all_hubs(segs, closes[-1])

    results = []
    for direction, area_fn, new_extreme, label_top, label_bot in [
        ('top', seg_red_area,   lambda s1, s2: s2['ep'] > s1['ep'], '⚠️顶背驰', '✅底背驰'),
        ('bot', seg_green_area, lambda s1, s2: s2['ep'] < s1['ep'], '⚠️顶背驰', '✅底背驰'),
    ]:
        seg_dir = 'B' if direction == 'top' else 'T'
        s1, s2, h1_idx, h2_idx, is_trend_cand = _find_compare_segs(segs, hubs, seg_dir)
        if s1 is None or s2 is None:
            continue
        if not new_extreme(s1, s2):
            continue
        a1 = area_fn(s1, hist, dt2i)
        a2 = area_fn(s2, hist, dt2i)
        if a1 <= 0:
            continue

        ratio = a2 / a1

        # bc_type
        if is_trend_cand and h1_idx >= 0 and h2_idx >= 0 and h1_idx != h2_idx:
            bc_type = 'trend'
        elif h1_idx >= 0 and h2_idx >= 0 and h1_idx == h2_idx:
            bc_type = 'consolidation'
        else:
            bc_type = 'normal'

        # strength
        if ratio < 0.5:
            strength = 'strong'
        elif ratio < 0.8:
            strength = 'weak'
        else:
            strength = 'none'

        # display 字符串
        if direction == 'top':
            if strength == 'strong':
                disp = f"⚠️顶背驰({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"
            elif strength == 'weak':
                disp = f"🟡弱背驰({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"
            else:
                disp = f"✅无背驰({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"
        else:
            if strength == 'strong':
                disp = f"✅底背驰({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"
            elif strength == 'weak':
                disp = f"📉回调中({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"
            else:
                disp = f"✅无背驰({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"

        trigger_date = str(s2['edt'])[:10].replace('/', '-')
        disp_with_date = f"{disp} @{trigger_date}"
        results.append({
            'direction': direction,
            'strength': strength,
            'bc_type': bc_type,
            'ratio': ratio,
            'a1': a1,
            'a2': a2,
            's1_hub': h1_idx,
            's2_hub': h2_idx,
            'trigger_date': trigger_date,
            'display': disp_with_date,
            '_edt': s2['edt'],  # 用于选最新的
        })

    if not results:
        # 无法比较时返回最近上涨段面积比作参考
        up = [s for s in segs if s['sst'] == 'B']
        if len(up) >= 2:
            a1 = seg_red_area(up[-2], hist, dt2i)
            a2 = seg_red_area(up[-1], hist, dt2i)
            ratio = a2 / a1 if a1 > 0 else 0
            return {**_NONE, 'ratio': ratio, 'a1': a1, 'a2': a2,
                    'display': f"✅无背驰({ratio:.0%}) 段1={a1:.1f} 段2={a2:.1f}"}
        return _NONE

    # 取最新段结束日期更晚的结果
    best = max(results, key=lambda r: r['_edt'])
    best.pop('_edt')
    return best


def beichi_str_from_segs(segs, closes, dates, label=''):
    """向后兼容：返回 display 字符串"""
    return beichi_from_segs(segs, closes, dates)['display']


def classify_beichi(bc, hubs=None, p=None, segs=None):
    """
    分类背驰类型。

    bc 可以是:
      - dict (beichi_from_segs 返回的结构体, 推荐)
      - str  (旧版字符串, 兼容)

    返回: ⭐趋势顶背/底背 / 🟡盘整顶背/底背 / 🔵普通顶背/底背 / 无
    """
    # 旧版字符串兼容
    if isinstance(bc, str):
        if not segs or not hubs:
            return "无"
        bc = beichi_from_segs(segs, closes=[], dates=[], hubs=hubs)
        # closes/dates 空时 _calc_macd_hist 会失败，需要完整重算
        # 此路径只做兜底，调用方应迁移到传结构体
        has = any(x in bc if isinstance(bc, str) else '' for x in ['顶背驰', '底背驰', '弱背驰'])
        if not has:
            return "无"

    if bc.get('direction') == 'none' or bc.get('strength') == 'none':
        return "无"

    suffix = "顶背" if bc['direction'] == 'top' else "底背"
    bc_type = bc.get('bc_type', 'normal')
    if bc_type == 'trend':
        return f"⭐趋势{suffix}"
    if bc_type == 'consolidation':
        return f"🟡盘整{suffix}"
    return f"🔵普通{suffix}"
