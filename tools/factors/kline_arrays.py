"""
tools/factors/kline_arrays.py — 共享 K 线预计算数组

所有 strategy 共用的滑动窗口数组，一次计算，全局复用。
O(n) 计算，O(1) 查询。

使用方式:
    arrs = precompute(closes, highs, lows, vols)
    ma20_today = arrs['ma20'][i]
    rmin_60    = arrs['rmin_l_pos'][i]
"""
from collections import deque


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

def precompute(closes, highs, lows, vols, window=250, pos_lookback=60):
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
        'ma20':      sliding_ma(closes, 20),
        'ma50':      sliding_ma(closes, 50),
        'ma60':      ma60_arr,
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

        # 原始数据引用
        'closes': closes, 'highs': highs, 'lows': lows, 'vols': vols,
        'n': n, 'window': window, 'pos_lookback': pos_lookback,
    }
