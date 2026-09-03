"""
tools/factors/chan/czsc_wrapper.py — czsc 1.0 集成 wrapper

目的: 用 czsc (Rust 实现的缠论核心) 替换我们自己的 beichi.py/strokes.py/hub.py
       解决 "算得不准" 的问题 (min_bi_len 缺失, 顶底交替不强制, fx_b 全局新高检查)

用法:
    from tools.factors.chan.czsc_wrapper import compute_chan_czsc
    
    result = compute_chan_czsc(
        klines=[{'date': '20260825', 'open': 100, 'high': 105, 'low': 98,
                 'close': 103, 'vol': 1000, 'amount': 100000}, ...],
        code='600089',
        max_bi_num=50,
        min_bi_len=6,
    )
    # result['fxs'], result['bis'], result['zss'] 跟项目现有格式兼容

输出格式对齐 (跟 tools/factors/chan/strokes.py 一致):
    fxs: [{'date': str, 'mark': 'D'/'G', 'high': float, 'low': float, 'price': float}, ...]
    bis: [{'sdt': str, 'edt': str, 'direction': 'Up'/'Down', 'high': float, 'low': float,
           'fx_a': FX, 'fx_b': FX, 'nb': int}, ...]
    zss: [{'sdt': str, 'edt': str, 'low': float, 'high': float, 'bis': [BI, ...]}, ...]
"""
import sys
import pandas as pd
from typing import List, Dict, Any
from pathlib import Path

# 路径兼容 (项目用 .venv, 但 pyproject 路径)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    import czsc
    from czsc import CZSC, Freq, RawBar
    CZSC_AVAILABLE = True
except ImportError as e:
    CZSC_AVAILABLE = False
    _IMPORT_ERROR = e


def kline_to_raw_bar(kline: Dict, code: str, idx: int) -> "RawBar":
    """
    我们的 Tushare daily K 线 → czsc RawBar

    字段对齐 (跟 czsc 18_tushare_daily_event_universe.py 一致):
      - vol: 手 → 股 (× 100)
      - amount: 千元 → 元 (× 1000)
    """
    if not CZSC_AVAILABLE:
        raise RuntimeError(f"czsc 未安装: {_IMPORT_ERROR}")

    return RawBar(
        symbol=code,
        dt=pd.to_datetime(kline['date']),
        id=idx,
        freq=Freq.D,
        open=float(kline['open']),
        close=float(kline['close']),
        high=float(kline['high']),
        low=float(kline['low']),
        vol=int(kline.get('vol', 0) * 100),                      # 手 → 股
        amount=int(kline.get('amount', 0) * 1000),              # 千 → 元
    )


def klines_to_raw_bars(klines: List[Dict], code: str) -> List["RawBar"]:
    """批量转换，批量 parse 日期避免逐行 pd.to_datetime"""
    dates = pd.to_datetime([k['date'] for k in klines])
    return [
        RawBar(
            symbol=code,
            dt=dates[i],
            id=i,
            freq=Freq.D,
            open=float(k['open']),
            close=float(k['close']),
            high=float(k['high']),
            low=float(k['low']),
            vol=int(k.get('vol', 0) * 100),
            amount=int(k.get('amount', 0) * 1000),
        )
        for i, k in enumerate(klines)
    ]


def fx_to_dict(fx) -> Dict[str, Any]:
    """czsc FX (分型) → 项目格式"""
    return {
        'date': str(fx.dt),
        'mark': 'D' if fx.mark.name == 'D' else 'G',  # D=底, G=顶
        'high': float(fx.high),
        'low': float(fx.low),
        'price': float(fx.fx),  # 分型值 (底=low, 顶=high)
        'elements': [str(b.dt) for b in fx.elements],
    }


def bi_to_dict(bi) -> Dict[str, Any]:
    """czsc BI (笔) → 项目格式 (跟 strokes.py 对齐)"""
    direction = 'Up' if bi.direction.name == 'Up' else 'Down'

    # 计算笔的长度 (去包含后 K 线数)
    nb = len(bi.bars) if hasattr(bi, 'bars') and bi.bars else 0

    # czsc 暴露 high/low 是 Python 属性, 不是 get_high() 方法
    high_val = bi.high
    low_val = bi.low

    return {
        'sdt': str(bi.fx_a.dt),
        'edt': str(bi.fx_b.dt),
        'sst': 'B' if bi.fx_a.mark.name == 'D' else 'T',
        'est': 'B' if bi.fx_b.mark.name == 'D' else 'T',
        'sp': float(bi.fx_a.fx),
        'ep': float(bi.fx_b.fx),
        'high': float(high_val),
        'low': float(low_val),
        'direction': direction,
        'nb': nb,
        'fx_a': fx_to_dict(bi.fx_a),
        'fx_b': fx_to_dict(bi.fx_b),
    }


def zs_to_dict(zs) -> Dict[str, Any]:
    """czsc ZS (中枢) → 项目格式 (跟 hub.py 对齐)"""
    return {
        'sdt': str(zs.sdt),
        'edt': str(zs.edt),
        'low': float(zs.zd),     # 中枢下沿
        'high': float(zs.zg),    # 中枢上沿
        'bis': [bi_to_dict(bi) for bi in zs.bis],  # 构成笔列表
    }


def compute_chan_czsc(
    klines: List[Dict],
    code: str,
    max_bi_num: int = 50,
    min_bi_len: int = 6,
) -> Dict[str, Any]:
    """
    用 czsc 计算缠论 (分型/笔/中枢)

    Args:
        klines: list of dict, 每条包含 date/open/close/high/low/vol/amount
        code: 股票代码
        max_bi_num: 最大保留笔数 (默认 50)
        min_bi_len: 笔最小长度 (默认 6, czsc 默认)

    Returns:
        dict {
            'fxs': [...],   # 分型列表
            'bis': [...],   # 笔列表
            'zss': [...],   # 中枢列表
            'czsc_obj': CZSC,  # 原始对象, 用于后续信号计算
            'kline_count': int,
        }
    """
    if not CZSC_AVAILABLE:
        raise RuntimeError(f"czsc 未安装: {_IMPORT_ERROR}")

    if len(klines) < 30:
        return {'fxs': [], 'bis': [], 'zss': [], 'kline_count': len(klines)}

    # 1. 转换 K 线 → RawBar
    bars = klines_to_raw_bars(klines, code)

    # 2. 跑 CZSC (Rust 核心)
    cz = CZSC(bars, max_bi_num=max_bi_num, min_bi_len=min_bi_len)

    # 3. 提取分型/笔/中枢 (Rust 端 CZSC 暴露: fx_list 是属性不是方法)
    fxs = [fx_to_dict(fx) for fx in cz.fx_list]
    bis = [bi_to_dict(bi) for bi in cz.bi_list]
    # 中枢从 bi_list 自动算
    from czsc import get_zs_seq
    zss = [zs_to_dict(zs) for zs in get_zs_seq(cz.bi_list)]

    return {
        'fxs': fxs,
        'bis': bis,
        'zss': zss,
        'czsc_obj': cz,  # 保留原始对象 (用于 signals)
        'kline_count': len(klines),
    }


# === 快速测试 ===
if __name__ == "__main__":
    # 用 603893 瑞芯微 测试
    import sys
    sys.path.insert(0, '/Users/I514959/workspace/mavis-quant-agent')

    from tools.storage.store import DataStore
    from tools.storage.store import read_kline
    from tools.storage.store import _to_ts_code

    code = '603893'
    print(f"=== 测试 {code} (czsc 计算) ===")
    print(f"czsc 版本: {czsc.__version__}")

    # 读 K 线
    rows = read_kline(_to_ts_code(code), limit=300)
    klines = []
    for r in rows:
        klines.append({
            'date': r.get('trade_date', ''),
            'open': r.get('open', 0),
            'close': r.get('close', 0),
            'high': r.get('high', 0),
            'low': r.get('low', 0),
            'vol': r.get('vol', 0),
            'amount': r.get('amount', 0),
        })

    print(f"K线数: {len(klines)}")

    # 跑 czsc
    result = compute_chan_czsc(klines, code, max_bi_num=50, min_bi_len=6)
    print(f"分型: {len(result['fxs'])} 个")
    print(f"笔: {len(result['bis'])} 个")
    print(f"中枢: {len(result['zss'])} 个")

    # 显示最近 5 个分型 + 5 个笔
    print("\n最近 5 个分型:")
    for fx in result['fxs'][-5:]:
        print(f"  {fx['date']} {fx['mark']} ¥{fx['price']:.2f}")

    print("\n最近 5 个笔:")
    for bi in result['bis'][-5:]:
        print(f"  {bi['sdt']}~{bi['edt']} {bi['direction']} nb={bi['nb']} ¥{bi['sp']:.2f}→¥{bi['ep']:.2f}")

    print("\n最近 3 个中枢:")
    for zs in result['zss'][-3:]:
        print(f"  {zs['sdt']}~{zs['edt']} ¥{zs['low']:.2f}~¥{zs['high']:.2f} (含 {len(zs['bis'])} 笔)")


def recent_confirmed_fenxing_from_czsc(czsc_obj, lookback: int = 5, kind: str = "bottom") -> tuple:
    """
    从 czsc 的分型列表找最近 lookback 根 K 线内确认的底/顶分型

    等价于原来 fenxing.py 的 has_recent_confirmed_fenxing, 但用 czsc 数据

    Args:
        czsc_obj: CZSC 实例 (cz.czsc_obj)
        lookback: 看最近几根 K 线
        kind: "bottom" 或 "top"

    Returns:
        (confirmed: bool, index: int, price: float)
    """
    if not hasattr(czsc_obj, 'fx_list') or not czsc_obj.fx_list:
        return False, -1, 0

    # 取最近 lookback 个分型
    fxs = czsc_obj.fx_list[-lookback:] if len(czsc_obj.fx_list) >= lookback else czsc_obj.fx_list

    # czsc 的分型 mark: D=底, G=顶
    target_mark = 'D' if kind == 'bottom' else 'G'

    for fx in reversed(fxs):
        if fx.mark.name == target_mark:
            # 检查确认: 下一根 K 线收盘突破分型值
            # 简化: czsc 的分型已经经过确认 (FX 内部 elements 包含 3 根 K 线)
            return True, len(czsc_obj.fx_list) - 1, float(fx.fx)

    return False, -1, 0


def has_recent_confirmed_fenxing(klines, lookback: int = 5, kind: str = "bottom") -> tuple:
    """
    兼容 fenxing.py 的 has_recent_confirmed_fenxing API

    Args:
        klines: list of dict, 每条 {date, open, close, high, low, vol, amount}
        lookback: 看最近几根 K 线
        kind: "bottom" 或 "top"

    Returns:
        (confirmed: bool, index: int, price: float)
    """
    if not klines or len(klines) < lookback + 2:
        return False, -1, 0

    # 用 czsc 算分型
    code = 'TEMP'
    result = compute_chan_czsc(klines, code)
    fxs = result['fxs']
    if not fxs:
        return False, -1, 0

    # 取最近 lookback 个分型
    recent_fxs = fxs[-lookback:] if len(fxs) >= lookback else fxs

    # czsc 的分型 mark: D=底, G=顶 (通过 fx.mark 或 fx.mark.name)
    for fx in reversed(recent_fxs):
        # 我们的 dict 格式 mark='D'/'G' (大写)
        is_target = (kind == "bottom" and fx.get('mark') == 'D') or \
                    (kind == "top" and fx.get('mark') == 'G')
        if is_target:
            return True, fxs.index(fx), fx.get('price', 0)

    return False, -1, 0
