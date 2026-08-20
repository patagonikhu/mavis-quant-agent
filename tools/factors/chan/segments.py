"""
chan/segments.py - 笔→段 (缠论 Step 4, 3 笔合 1 段可延伸)

从 tools/chan_analysis.py find_segments_full 搬过来 (跟原版 1:1)
"""
def find_segments_full(strokes, mh, ml, md):
    """
    Step4: 笔→段（3笔合1段，可延伸）
    后处理：检查连续同向段之间是否有被跳过的大振幅单笔
    如果有（振幅>相邻段振幅的30%），作为简化段插入
    """
    segs = []
    i = 0
    while i <= len(strokes) - 3:
        s1si, s1st, s1ei, s1et = strokes[i]
        s3si, s3st, s3ei, s3et = strokes[i + 2]
        ep1 = ml[s1ei] if s1et == 'B' else mh[s1ei]
        ep3 = ml[s3ei] if s3et == 'B' else mh[s3ei]
        up = (s1st == 'B' and ep3 > ep1)
        down = (s1st == 'T' and ep3 < ep1)
        if up or down:
            end = i + 2
            while end + 2 < len(strokes):
                _, _, nei, net = strokes[end + 2]
                nep = ml[nei] if net == 'B' else mh[nei]
                if (up and net == 'T' and nep > ep3) or (down and net == 'B' and nep < ep3):
                    ep3 = nep
                    end += 2
                else:
                    break
            fsi = strokes[i][0]
            fst = strokes[i][1]
            fei = strokes[end][2]
            fet = strokes[end][3]
            sp_f = mh[fsi] if fst == 'T' else ml[fsi]
            ep_f = ml[fei] if fet == 'B' else mh[fei]
            segs.append({
                'sdt': md[fsi], 'sst': fst, 'edt': md[fei], 'est': fet,
                'sp': sp_f, 'ep': ep_f, 'lo': min(sp_f, ep_f), 'hi': max(sp_f, ep_f),
                'nb': end - i + 1, 'dir': '↑' if fst == 'B' else '↓'
            })
            i = end + 1
        else:
            i += 1

    # 后处理：插入被跳过的大振幅单笔（简化段）
    segs_fixed = []
    for j, s in enumerate(segs):
        if j > 0 and segs_fixed and segs_fixed[-1]['sst'] == s['sst']:
            prev = segs_fixed[-1]
            gap_strokes = [(si, st, ei, et) for si, st, ei, et in strokes
                           if md[si] >= prev['edt'] and md[ei] <= s['sdt']]
            for gsi, gst, gei, get_ in gap_strokes:
                gsp = mh[gsi] if gst == 'T' else ml[gsi]
                gep = ml[gei] if get_ == 'B' else mh[gei]
                g_chg = abs(gep - gsp) / gsp
                prev_chg = abs(prev['ep'] - prev['sp']) / prev['sp']
                if g_chg > max(0.15, prev_chg * 0.3):
                    segs_fixed.append({
                        'sdt': md[gsi], 'sst': gst, 'edt': md[gei], 'est': get_,
                        'sp': gsp, 'ep': gep, 'lo': min(gsp, gep), 'hi': max(gsp, gep),
                        'nb': 1, 'dir': '↑' if gst == 'B' else '↓(简化段)'
                    })
        segs_fixed.append(s)
    return segs_fixed
