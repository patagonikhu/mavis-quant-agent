"""
chan/strokes.py - 顶底分型 → 笔 (缠论 Step 2+3)

从 tools/chan_analysis.py find_strokes_full 搬过来 (跟原版 1:1)
"""
from .inclusion import merge_inclusion


def find_strokes_full(dates, closes, highs, lows):
    """Step2+3: 顶底分型 → 笔

    Returns: (strokes, mh, ml, md)
      strokes: list of (start_idx, start_type, end_idx, end_type) - T=顶/B=底
      mh/ml/md: merge_inclusion 后的 high/low/date list
    """
    m = merge_inclusion(dates, closes, highs, lows)
    mh = [x[2] for x in m]
    ml = [x[3] for x in m]
    md = [x[0] for x in m]
    tops = [i for i in range(1, len(m) - 1) if mh[i] > mh[i - 1] and mh[i] > mh[i + 1]]
    bots = [i for i in range(1, len(m) - 1) if ml[i] < ml[i - 1] and ml[i] < ml[i + 1]]
    af = sorted([(i, 'T') for i in tops] + [(i, 'B') for i in bots])
    strokes = []
    prev = None
    for idx, typ in af:
        if prev is None:
            prev = (idx, typ)
            continue
        pi, pt = prev
        if typ != pt and idx - pi >= 2:
            strokes.append((pi, pt, idx, typ))
            prev = (idx, typ)
    return strokes, mh, ml, md
