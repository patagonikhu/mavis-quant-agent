"""
tools/factors/kline_arrays.py — 共享 K 线预计算数组

所有 strategy 共用的滑动窗口数组，一次计算，全局复用。
O(n) 计算，O(1) 查询。

使用方式:
    arrs = build_kline_features(closes, highs, lows, vols)
    ma20_today = arrs['ma20'][i]
    rmin_60    = arrs['rmin_l_pos'][i]
"""
from collections import deque
import math


# ── 基础工具 ───────────────────────────────────────────────────────────────

def sliding_ma(arr, n):
    """O(n) 滑动均线，warm-up 阶段用实际条数做分母。"""
    result = []
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= n:
            s -= arr[i - n]
        result.append(s / min(i + 1, n))
    return result


def rolling_min(arr, n):
    """O(n) 单调双端队列滚动最小值。"""
    dq, result = deque(), []
    for i, v in enumerate(arr):
        while dq and arr[dq[-1]] >= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
        result.append(arr[dq[0]])
    return result


def rolling_max(arr, n):
    """O(n) 单调双端队列滚动最大值。"""
    dq, result = deque(), []
    for i, v in enumerate(arr):
        while dq and arr[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - n:
            dq.popleft()
        result.append(arr[dq[0]])
    return result


# ── 核心预计算 ─────────────────────────────────────────────────────────────

def _swing_low_arr(lows, window: int = 3) -> list:
    """O(n) 预算 swing low 布尔数组。
    swing_low_arr[j] = True 当且仅当 lows[j] 是局部极小值（左右各 window 根邻居都 >= lows[j]）。
    边界 window 根内无法判断，标 False。
    """
    n = len(lows)
    result = [False] * n
    for j in range(window, n - window):
        v = lows[j]
        ok = True
        for k in range(j - window, j + window + 1):
            if k != j and lows[k] < v:
                ok = False
                break
        result[j] = ok
    return result

def _boll_arr(closes, n, k, field):
    """O(n) Bollinger Band 数组。field: 'upper'|'lower'|'mid'|'pct'
    用滑动前缀和维护 Σx 和 Σx²，std² = Σx²/cnt - (Σx/cnt)²，数学等价原 O(n²) 实现。
    """
    result = []
    sx = 0.0   # Σx
    sx2 = 0.0  # Σx²
    win = deque()
    for c in closes:
        sx += c
        sx2 += c * c
        win.append(c)
        if len(win) > n:
            old = win.popleft()
            sx -= old
            sx2 -= old * old
        cnt = len(win)
        mid = sx / cnt
        variance = sx2 / cnt - mid * mid
        std = math.sqrt(max(variance, 0.0))
        upper = mid + k * std
        lower = mid - k * std
        if field == 'upper':   result.append(round(upper, 2))
        elif field == 'lower': result.append(round(lower, 2))
        elif field == 'mid':   result.append(round(mid, 2))
        elif field == 'pct':
            result.append(round((c - lower) / (upper - lower) * 100, 1) if upper > lower else 50.0)
        else:  # width
            result.append(round((upper - lower) / mid * 100, 1) if mid > 0 else 0.0)
    return result


def build_kline_features(closes, highs, lows, vols, window=250, pos_lookback=60):
    """K 线公共数组预计算，O(n) 一次，供所有 strategy 共用。

    Args:
        closes / highs / lows / vols: list[float]，等长
        window:       Wyckoff 用的窗口长度（默认 250）
        pos_lookback: 价格位置计算回看天数（默认 60）

    Returns:
        dict — 所有数组均与输入等长，arrs[key][i] 对应第 i 根 K 线。

    数组说明:
        ma20 / ma50 / ma60 / ma200  — 收盘价滑动均线
        ma_window                   — window 日均线（Wyckoff 用作"长期均线锚"）
        ma_long                     — alias for ma60（Wyckoff ma_long 默认 60）
        vol3 / vol5 / vol10 / vol20 / vol60 — 量能滑动均线
        rmin_l_pos                  — pos_lookback 日最低价滚动最小
        rmax_h_pos                  — pos_lookback 日最高价滚动最大
        rmin_l_250                  — 250 日最低价滚动最小
        rmin_l_20                   — 20 日最低价滚动最小
        slope_60                    — 60 日斜率 %
        trend_30                    — 30 日涨跌幅 %
        vol_ref_120_20              — mean(vols[i-120 : i-20])，用于吸筹量比
        vol_ref_30_10               — mean(vols[i-30  : i-10])，短周期量比
        closes / highs / lows / vols — 原始数据引用（方便 strategy 统一入口）
    """
    n = len(closes)

    # 前缀和，O(1) 范围求均
    vp = [0.0] * (n + 1)
    for j in range(n):
        vp[j + 1] = vp[j] + vols[j]

    def _range_mean(i, back_start, back_end):
        """mean(vols[i-back_start : i-back_end])，back_start > back_end >= 0"""
        s = max(0, i - back_start)
        e = max(0, i - back_end)
        cnt = e - s
        return (vp[e] - vp[s]) / cnt if cnt > 0 else 0.0

    slope_60_arr = [
        (closes[i] / closes[i - 60] - 1) * 100 if i >= 60 else 0.0
        for i in range(n)
    ]
    trend_30_arr = [
        (closes[i] / closes[i - 31] - 1) * 100 if i > 30 else slope_60_arr[i]
        for i in range(n)
    ]

    ma60_arr = sliding_ma(closes, 60)

    return {
        # 收盘价均线
        'ma5':       sliding_ma(closes, 5),
        'ma20':      sliding_ma(closes, 20),
        'ma50':      sliding_ma(closes, 50),
        'ma60':      ma60_arr,
        'ma120':     sliding_ma(closes, 120),
        'ma200':     sliding_ma(closes, 200),
        'ma_window': sliding_ma(closes, window),   # Wyckoff "ma200" = window 日均
        'ma_long':   ma60_arr,                     # ma_long 默认 60，alias

        # 量能均线
        'vol3':  sliding_ma(vols, 3),
        'vol5':  sliding_ma(vols, 5),
        'vol10': sliding_ma(vols, 10),
        'vol20': sliding_ma(vols, 20),
        'vol60': sliding_ma(vols, 60),

        # 滚动极值
        'rmin_l_pos': rolling_min(lows,  pos_lookback),
        'rmax_h_pos': rolling_max(highs, pos_lookback),
        'rmin_l_250': rolling_min(lows,  250),
        'rmin_l_20':  rolling_min(lows,  20),

        # 斜率 / 趋势
        'slope_60': slope_60_arr,
        'trend_30': trend_30_arr,

        # 量比参考（前缀和派生，O(1) 查询）
        'vol_ref_120_20': [_range_mean(i, 120, 20) for i in range(n)],
        'vol_ref_30_10':  [_range_mean(i, 30,  10) for i in range(n)],

        # Bollinger Bands (20日, 2σ) — per bar
        'boll_upper': _boll_arr(closes, 20, 2.0, 'upper'),
        'boll_lower': _boll_arr(closes, 20, 2.0, 'lower'),
        'boll_mid':   _boll_arr(closes, 20, 2.0, 'mid'),
        'boll_pct':   _boll_arr(closes, 20, 2.0, 'pct'),
        'boll_width': _boll_arr(closes, 20, 2.0, 'width'),

        # 原始数据引用
        'closes': closes, 'highs': highs, 'lows': lows, 'vols': vols,
        'n': n, 'window': window, 'pos_lookback': pos_lookback,

        # Spring 支撑位预算：swing_low_3[j] = lows[j] 是 window=3 的 swing low
        # 左右各 3 根邻居都 >= lows[j]，边界 3 根内标 False（无法判断）
        'swing_low_3': _swing_low_arr(lows, window=3),
    }
