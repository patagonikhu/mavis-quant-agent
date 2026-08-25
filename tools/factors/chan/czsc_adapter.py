"""
tools/factors/chan/czsc_adapter.py — 用 czsc 算缠论, 输出跟现有 hub.py 兼容的格式

设计目标:
  1. 保持 three_levels.py 和 ChanStrategy 不动
  2. 替换 analyze_hub_v2 为 czsc 版本 (返回相同 dict 结构)
  3. 用 czsc 算笔/中枢, 保留我们自己的 beichi (面积比)
  4. 中枢用 czsc.get_zs_seq 算

用法 (在 three_levels.py):
  from tools.factors.chan.czsc_adapter import analyze_hub_v2_czsc
  res = analyze_hub_v2_czsc(dates, closes, highs, lows, '日线')
  # 跟 analyze_hub_v2 输出格式完全一样
"""
import sys
import statistics
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    import czsc
    from czsc import CZSC, Freq, RawBar, get_zs_seq
    CZSC_AVAILABLE = True
except ImportError as e:
    CZSC_AVAILABLE = False
    _IMPORT_ERROR = e


def to_datetime(d):
    """字符串/数字 → datetime 对象 (czsc RawBar 需要)"""
    if isinstance(d, datetime):
        return d
    if isinstance(d, str):
        # '2026-08-25' 或 '20260825' 都支持
        s = d.replace('-', '')
        if len(s) == 8 and s.isdigit():
            return datetime.strptime(s, '%Y%m%d')
        # pandas Timestamp 也支持
        try:
            import pandas as pd
            return pd.to_datetime(d).to_pydatetime()
        except Exception:
            return datetime.strptime(d[:10], '%Y-%m-%d')
    if isinstance(d, (int, float)):
        return datetime.fromtimestamp(d)
    return d


def dates_closes_to_raw_bars(dates, closes, highs, lows, symbol="TEMP"):
    """date/close/high/low 数组 → czsc RawBar 列表"""
    if not CZSC_AVAILABLE:
        raise RuntimeError(f"czsc 未安装: {_IMPORT_ERROR}")

    bars = []
    for i, (d, c, h, l) in enumerate(zip(dates, closes, highs, lows)):
        bars.append(RawBar(
            symbol=symbol,
            dt=to_datetime(d),
            id=i,
            freq=Freq.D,
            open=float(c),  # 简化: open=close
            close=float(c),
            high=float(h),
            low=float(l),
            vol=0,  # 没用到 vol
            amount=0,
        ))
    return bars


def to_date_str(d):
    """datetime → '20260825' 格式 (跟项目 date 格式一致, 无分隔)"""
    if isinstance(d, str):
        return d.replace('-', '').replace('/', '')[:8]
    if hasattr(d, 'strftime'):
        return d.strftime('%Y%m%d')
    return str(d)[:8].replace('-', '')


def bis_to_segs_format(bis, dates):
    """czsc 笔 (BI) 列表 → 项目 segs 格式 (用单笔当段)

    我们的 segs 格式: {'sst', 'sdt', 'edt', 'sp', 'ep', 'lo', 'hi', 'nb', 'dir', 'est'}
    sdt/edt 格式: '20260825' (8 字符, 无分隔, 跟 beichi dt2i 对齐)
    """
    segs = []
    for bi in bis:
        sst = 'B' if bi.fx_a.mark.name == 'D' else 'T'
        est = 'B' if bi.fx_b.mark.name == 'D' else 'T'
        sdt = to_date_str(bi.fx_a.dt)
        edt = to_date_str(bi.fx_b.dt)

        sp = float(bi.fx_a.fx)
        ep = float(bi.fx_b.fx)
        segs.append({
            'sdt': sdt,
            'edt': edt,
            'sst': sst,
            'est': est,
            'sp': sp,
            'ep': ep,
            'lo': min(sp, ep),
            'hi': max(sp, ep),
            'nb': getattr(bi, 'length', 1),
            'dir': '↑' if sst == 'B' else '↓',
        })
    return segs



def czsc_zss_to_hub_format(zs_list, all_segs=None):
    """czsc 中枢 (ZS) 列表 → 项目 hubs 格式 (跟 find_all_hubs 一致)

    关键: 设置 leaving_up / leaving_down (找中枢之后的离开段),
          beichi 算趋势背驰靠这两个字段
    """
    hubs = []
    for i, zs in enumerate(zs_list):
        bis_in_zs = zs.bis if hasattr(zs, 'bis') else []
        # 中枢区间
        zd, zg = float(zs.zd), float(zs.zg)
        sdt = to_date_str(zs.sdt) if hasattr(zs, 'sdt') else None
        edt = to_date_str(zs.edt) if hasattr(zs, 'edt') else None
        next_edt = to_date_str(zs_list[i + 1].edt) if i + 1 < len(zs_list) else None

        # 找离开段 (跟原版 find_all_hubs 逻辑一致)
        leaving_up = None
        leaving_down = None
        if all_segs:
            for s in all_segs:
                if s['sdt'] < edt:
                    continue
                if next_edt and s['sdt'] >= next_edt:
                    break
                if s['sst'] == 'B' and s['hi'] > zg:
                    leaving_up = s
                    break
                if s['sst'] == 'T' and s['lo'] < zd:
                    leaving_down = s
                    break

        hubs.append({
            'low': zd,
            'high': zg,
            'bis': bis_in_zs,
            'seg_idx': [],
            'valid': True,
            'sdt': sdt,
            'edt': edt,
            'leaving_up': leaving_up,
            'leaving_down': leaving_down,
        })
    return hubs


def analyze_hub_v2_czsc(dates, closes, highs, lows, label='日线', code='TEMP', min_bi_len=6):
    """
    跟 analyze_hub_v2 输出一致, 但用 czsc 算笔和中枢

    Returns: dict {
        'hub': 当前中枢, 'segs': 笔列表 (兼容), 'n_strokes': int,
        'n_segs': int, 'seg_status': str, 'supports': [...], 'p': float, 'label': str,
        'hubs': 中枢列表 (兼容), 'bis': czsc 笔 (额外, 给 beichi 用),
    }
    """
    if not CZSC_AVAILABLE:
        raise RuntimeError(f"czsc 未安装: {_IMPORT_ERROR}")

    if len(closes) < 30:
        return {'error': '数据不足', 'label': label}

    p = closes[-1]

    # 1. 转 K 线
    bars = dates_closes_to_raw_bars(dates, closes, highs, lows, symbol=code)

    # 2. 跑 CZSC
    cz = CZSC(bars, max_bi_num=50, min_bi_len=min_bi_len)

    # 3. 笔 → segs 格式
    bis = cz.bi_list
    segs = bis_to_segs_format(bis, dates)

    # 4. 中枢 (设 leaving_up/left 让 beichi 找趋势背驰)
    zss = get_zs_seq(bis)
    hubs = czsc_zss_to_hub_format(zss, all_segs=segs)

    # 5. 当前中枢 (跟 analyze_hub_v2 一样)
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
        }
    else:
        hub = {'valid': False}

    # 6. 支撑
    supports = sorted([s['lo'] for s in segs[-8:] if s['lo'] < p], reverse=True)[:3]

    # 7. 段状态
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
        'hub': hub,
        'segs': segs,
        'n_strokes': len(bis),
        'n_segs': len(segs),
        'seg_status': seg_status,
        'supports': supports,
        'p': p,
        'label': label,
        'hubs': hubs,
        'bis': bis,
    }


# === 快速测试 ===
if __name__ == "__main__":
    import sys
    sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

    from tools.data_store import DataStore
    from tools.history_sync import read_kline
    from tools.data_store import _to_ts_code
    from tools.factors.chan.hub import analyze_hub_v2  # 现有
    from tools.factors.chan.czsc_adapter import analyze_hub_v2_czsc

    code = '603893'
    print(f"=== 对比 {code}: 现有 hub.py vs czsc adapter ===")

    rows = read_kline(_to_ts_code(code), limit=300)
    dates = [r.get('trade_date', '') for r in rows]
    closes = [r.get('close', 0) for r in rows]
    highs = [r.get('high', 0) for r in rows]
    lows = [r.get('low', 0) for r in rows]

    # 现有
    res_old = analyze_hub_v2(dates, closes, highs, lows, '日线')
    print(f"\n【现有 hub.py】")
    print(f"  段数: {len(res_old.get('segs', []))}, 中枢: {len(res_old.get('hubs', []))}")
    print(f"  笔数 (n_strokes): {res_old.get('n_strokes', 0)}")

    # czsc
    res_new = analyze_hub_v2_czsc(dates, closes, highs, lows, '日线', code=code)
    print(f"\n【czsc adapter】")
    print(f"  段数: {len(res_new.get('segs', []))}, 中枢: {len(res_new.get('hubs', []))}")
    print(f"  笔数: {res_new.get('n_strokes', 0)}")
    if res_new.get('hub', {}).get('valid'):
        h = res_new['hub']
        print(f"  当前中枢: ¥{h['low']:.2f}~¥{h['high']:.2f} ({h['pos']})")
    print(f"  最后段: {res_new['segs'][-1] if res_new['segs'] else '无'}")
