"""
chan/hub.py - 中枢 (缠论 Step 5)

find_all_hubs   — 找所有有效中枢（延伸+合并+离开段）
analyze_hub_v2  — 完整分析入口 (笔→段→中枢)
format_hub_v2   — 格式化单级别输出
"""
from .strokes import find_strokes_full
from .segments import find_segments_full


def analyze_hub_v2(dates, closes, highs, lows, label='日线'):
    """主入口: 完整分析 (笔→段→中枢)"""
    if len(closes) < 30:
        return {'error': '数据不足', 'label': label}
    p = closes[-1]
    strokes, mh, ml, md = find_strokes_full(dates, closes, highs, lows)
    segs = find_segments_full(strokes, mh, ml, md)

    # 当前中枢：取 find_all_hubs 最新的一个，加 pos/stop/t1/t2
    hubs = find_all_hubs(segs, p)
    if hubs:
        h = hubs[-1]
        hl, hh = h['low'], h['high']
        if p > hh:
            pos = "上方✅"
        elif p < hl * 0.95:
            pos = "跌穿🔴"
        elif p < hl:
            pos = "下方⚠️"
        else:
            pos = "内部⬜"
        hub = {
            'low': hl, 'high': hh, 'pos': pos, 'valid': True,
            'stop': hl if p > hh else None,
            't1': hl if p < hl else None,
            't2': hh if p < hl else (hh + (hh - hl) if p > hh else None),
            'leaving_up': h.get('leaving_up'),
            'leaving_down': h.get('leaving_down'),
        }
    else:
        hub = {'valid': False}

    supports = sorted([s['lo'] for s in segs[-8:] if s['lo'] < p], reverse=True)[:3]
    seg_status = '未知'
    if segs:
        last = segs[-1]
        if last['sst'] == 'T' and p < last['ep']:
            seg_status = f"下跌段延伸中（¥{last['sp']:.0f}→¥{p:.0f}，未结束）"
        elif last['sst'] == 'B' and p > last['ep']:
            seg_status = f"上涨段延伸中（¥{last['sp']:.0f}→¥{p:.0f}，未结束）"
        else:
            seg_status = f"最后段结束¥{last['ep']:.0f}，当前¥{p:.0f}震荡"
    return {
        'hub': hub, 'segs': segs, 'n_strokes': len(strokes), 'n_segs': len(segs),
        'seg_status': seg_status, 'supports': supports, 'p': p, 'label': label
    }


def format_hub_v2(res):
    """格式化单级别中枢输出"""
    if 'error' in res:
        return f"【{res['label']}】{res['error']}"
    p = res['p']
    hub = res['hub']
    label = res['label']
    lines = [f"【{label}中枢 (完整算法)】  笔{res['n_strokes']}  段{res['n_segs']}"]
    lines.append(f"  段状态: {res['seg_status']}")
    if hub.get('valid'):
        lines.append(f"  中枢: 下沿¥{hub['low']:.0f}  上沿¥{hub['high']:.0f}  {hub['pos']}")
        if hub['stop']:
            lines.append(f"    止损¥{hub['stop']:.0f}")
        if hub['t1']:
            lines.append(f"    第一目标¥{hub['t1']:.0f}  第二目标¥{hub['t2']:.0f}")
        if hub['t2'] and not hub['t1']:
            lines.append(f"    目标¥{hub['t2']:.0f}")
    else:
        lines.append(f"  中枢: 最近段无有效重叠（趋势延伸中）")
    if res['supports']:
        lines.append(f"  关键支撑: {' / '.join(f'¥{v:.0f}' for v in res['supports'])}")
    return '\n'.join(lines)


def find_all_hubs(segs, p):
    """找所有有效中枢，返回时间顺序列表。

    第一步（从后往前）：找所有候选中枢，保证连接段约束。
    第二步：合并价格区间重叠的相邻中枢（重叠 = 实际是同一个更大的中枢）。
    第三步（从前往后）：对每个中枢找离开段。

    每个中枢包含:
      low, high, center, sdt, edt
      leaving_up   — 向上离开段 (sst='B', hi>high)，dict 或 None
      leaving_down — 向下离开段 (sst='T', lo<low)，dict 或 None
    """
    # 第一步：从后往前扫，找所有候选中枢
    raw = []
    for i in range(len(segs) - 1, 1, -1):
        s1, s2, s3 = segs[i - 2], segs[i - 1], segs[i]
        alt = (s1['sst'] == s3['sst'] and s1['sst'] != s2['sst'])
        hl = max(s1['lo'], s2['lo'], s3['lo'])
        hh = min(s1['hi'], s2['hi'], s3['hi'])
        center = (hl + hh) / 2
        rw = (hh - hl) / center * 100 if center > 0 else 0
        if not (hh > hl and rw > 3 and alt):
            continue
        if raw and i + 1 >= raw[-1]['_seg_start']:
            continue

        seg_end_idx = i - 2
        for k in (i - 2, i - 1, i):
            if segs[k]['lo'] >= hl and segs[k]['hi'] <= hh:
                seg_end_idx = k
        for j in range(i + 1, len(segs)):
            sj = segs[j]
            if sj['lo'] >= hl and sj['hi'] <= hh:
                seg_end_idx = j
            else:
                break

        raw.append({
            'low': hl, 'high': hh, 'center': center,
            'sdt': s1['sdt'], 'edt': segs[seg_end_idx]['edt'],
            '_seg_start': i - 2,
            '_seg_end_idx': seg_end_idx,
        })

    raw.reverse()

    # 第二步：合并价格区间重叠的相邻中枢
    # 重叠定义：h2.low < h1.high（两中枢价格区间有交集）
    # 合并规则：新区间 = [min(low), max(high)]，时间跨度合并，向后滚动直到无重叠
    merged = []
    for h in raw:
        if merged and h['low'] < merged[-1]['high']:
            # 与前一个中枢重叠，合并
            prev = merged[-1]
            new_low  = min(prev['low'],  h['low'])
            new_high = max(prev['high'], h['high'])
            new_center = (new_low + new_high) / 2
            merged[-1] = {
                'low': new_low, 'high': new_high, 'center': new_center,
                'sdt': prev['sdt'], 'edt': h['edt'],
            }
        else:
            merged.append({
                'low': h['low'], 'high': h['high'], 'center': h['center'],
                'sdt': h['sdt'], 'edt': h['edt'],
            })

    # 第三步：对每个中枢找离开段
    for idx, h in enumerate(merged):
        next_sdt = merged[idx + 1]['sdt'] if idx + 1 < len(merged) else None
        hl, hh = h['low'], h['high']
        leaving_up = leaving_down = None

        for s in segs:
            if s['sdt'] < h['edt']:
                continue
            if next_sdt and s['sdt'] >= next_sdt:
                break
            if s['sst'] == 'B' and s['hi'] > hh:
                leaving_up = s
                break
            if s['sst'] == 'T' and s['lo'] < hl:
                leaving_down = s
                break

        h['leaving_up'] = leaving_up
        h['leaving_down'] = leaving_down

    return merged
