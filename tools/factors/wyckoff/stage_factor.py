"""
wyckoff/stage_factor.py - 威科夫 3 大阶段 (Accumulation / Markup / Distribution)

v5.4 重写 (2026-07-28): 从 tools.wyckoff 拆过来, 跟 WyckoffTradingAgent 1:1
- 9 detector: tools.factors.wyckoff.detectors
- 13 helper: tools.factors.wyckoff.helpers
- 评分: 内联 (跟原 wyckoff_stage_v2 评分逻辑 1:1, 保证 confidence 数值一致)

输入: K 线 DataFrame (close/high/low/volume/open/pct_chg)
输出: dict { 阶段, 含义, 操作, 置信度, 趋势_30日_pct, sub_events, sub_event_count, 判定,
              stage, stage_name, action, confidence, stage_detail, phase_progress, scores,
              accum_detail, distribution_detail, markup_detail, signals }
"""
import statistics
from typing import List, Optional
import pandas as pd
from tools.factors.base import Factor
from tools.factors.wyckoff.helpers import _find_range
from tools.factors.wyckoff.detectors import scan_sub_events


def _mean(seq):
    """sum/len — 34x faster than statistics.mean for small lists."""
    return sum(seq) / len(seq)


def _sliding_ma(arr, n):
    """O(n) sliding-window MA of length n (warm-up uses actual count)."""
    result = []
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= n:
            s -= arr[i - n]
        result.append(s / min(i + 1, n))
    return result


def wyckoff_judge(i, arrs, sub_events, window, period_label,
                  ma_short=20, ma_long=60, pos_lookback=60):
    """威科夫阶段判定 — O(1) per call，使用 kline_arrays.precompute() 返回的预算数组。

    参数:
        i:           绝对 K 线索引（0-based）
        arrs:        kline_arrays.precompute() 返回的 dict
        sub_events:  该日期之前的 sub_events 名称集合（set[str]）
        window:      Wyckoff 回看窗口（与 arrs['window'] 一致即可）

    与 _judge_full 等价，但所有滑动窗口操作变为 O(1) 数组查询。
    """
    closes = arrs['closes']
    lows   = arrs['lows']
    n_bars = len(closes)
    if i < 1 or i >= n_bars:
        return None

    p     = closes[i]
    ma20  = arrs['ma20'][i]
    ma50  = arrs['ma50'][i]
    ma60  = arrs['ma60'][i]
    # ma200 = window 均线（_judge_full 里 _mean(c) where c=closes[-window:]）
    # 当 i < 199 时退化为 ma60（与原代码 else ma60 一致）
    ma200 = arrs['ma_window'][i] if i >= 199 else arrs['ma60'][i]

    dev_ma20  = (p / ma20  - 1) * 100 if ma20  > 0 else 0.0
    dev_ma60  = (p / ma60  - 1) * 100 if ma60  > 0 else 0.0
    dev_ma200 = (p / ma200 - 1) * 100 if ma200 > 0 else 0.0

    slope_60 = arrs['slope_60'][i]
    trend_30 = arrs['trend_30'][i]

    recent_low  = arrs['rmin_l_pos'][i]
    recent_high = arrs['rmax_h_pos'][i]
    pos_pct = (
        (p - recent_low) / (recent_high - recent_low) * 100
        if recent_high > recent_low > 0 else 50.0
    )

    # MA 金叉 / 持续上方
    ma50_prev  = arrs['ma50'][i - 1]      if i >= 1   else ma50
    ma200_prev = arrs['ma_window'][i - 1] if i >= 200 else ma200
    golden_cross_recent = (ma50_prev <= ma200_prev and ma50 > ma200)

    confirm_days = 5
    above_ma200 = p > ma200
    if i >= ma_long + confirm_days:
        above_ma200_sustained = all(
            closes[j] > arrs['ma_long'][j] * 0.98
            for j in range(i - confirm_days + 1, i + 1)
        )
    else:
        above_ma200_sustained = above_ma200

    # 量能
    vol_5  = arrs['vol5'][i]
    vol_60 = arrs['vol60'][i]
    vol_shrink = vol_5 < vol_60 * 0.5 if vol_60 > 0 else False

    vol_3  = arrs['vol3'][i]
    dist_confirm_days = 3
    vol_dry_3d_consecutive = (
        vol_3 < vol_60 * 0.5
        if vol_60 > 0 and i >= 60 + dist_confirm_days
        else vol_shrink
    )

    # 评分初始化
    score_Accumulation = score_Markup = score_Distribution = 0
    stage_detail = ""
    accum_base_low_ok = accum_ma_gap_ok = accum_volume_ok = False
    accum_b_test_count_n = 0
    accum_c_ok = accum_abc_pass = False

    # Accumulation 累积
    if i >= 30:
        period_low = arrs['rmin_l_250'][i]
        accum_base_low_ok = period_low > 0 and p <= period_low * 1.45

        if ma200 > 0:
            accum_ma_gap_ok = abs(ma50 - ma200) / ma200 * 100 <= 8

        if i >= 120:
            vol_recent = arrs['vol20'][i]
            vol_ref    = arrs['vol_ref_120_20'][i]
        elif i >= 40:
            vol_recent = arrs['vol10'][i]
            vol_ref    = arrs['vol_ref_30_10'][i]
        else:
            vol_recent = vol_ref = 0.0

        if vol_ref > 0 and vol_recent / vol_ref < 0.75:
            accum_volume_ok = True

        # B 阶段底部测试次数 — O(60) 有界常数
        if accum_base_low_ok:
            start_j = max(0, i - 59)
            accum_b_test_count_n = sum(
                1 for j in range(start_j, i + 1)
                if period_low > 0 and abs(lows[j] - period_low) / period_low <= 0.05
            )

        if accum_base_low_ok and i >= 20:
            recent_20_low = arrs['rmin_l_20'][i]
            if recent_20_low >= period_low * 0.97 and vol_ref > 0 and vol_recent / vol_ref < 0.75:
                accum_c_ok = True

        if accum_base_low_ok and accum_ma_gap_ok and accum_volume_ok:
            accum_abc_pass = True

    if accum_abc_pass:
        score_Accumulation += 3
        if any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
            score_Accumulation += 2
        if dev_ma20 < 5 and dev_ma60 < 0:
            score_Accumulation += 1
    elif abs(slope_60) < 8 and 25 < pos_pct < 75 and any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
        score_Accumulation += 1
    elif pos_pct < 30 and dev_ma200 < -5 and any(e in sub_events for e in ('Spring', 'LPS', 'EVR')):
        score_Accumulation += 1
    elif pos_pct < 30 and dev_ma200 < -5 and len(sub_events) >= 1:
        score_Accumulation += 1

    if accum_abc_pass:
        if accum_b_test_count_n >= 3:
            stage_detail = 'Accum_C' if accum_c_ok else 'Accum_B'
        else:
            stage_detail = 'Accum_A'

    # Markup 主升浪
    ma_gap_pct_above = (p - ma200) / ma200 * 100 if ma200 > 0 else 0.0
    ma50_angle_5d = 0.0
    if i >= 54:
        ma50_5d_ago = sum(closes[i - 54:i - 49]) / 5
        if ma50_5d_ago > 0:
            ma50_angle_5d = (ma50 / ma50_5d_ago - 1) * 100

    is_uptrend = slope_60 > 0 and dev_ma200 > -10 and trend_30 > -15

    if golden_cross_recent and above_ma200_sustained and ma_gap_pct_above > 0.5 and is_uptrend:
        score_Markup += 3
        if 'SOS' in sub_events:
            score_Markup += 1
        if ma50_angle_5d >= 2.0:
            score_Markup += 1
    elif above_ma200 and ma_gap_pct_above > 0.5 and is_uptrend:
        score_Markup += 1
    if dev_ma20 > 5 and pos_pct > 60 and is_uptrend:
        score_Markup += 1

    # Distribution 派发
    distribution_start = dev_ma200 > 30 and vol_dry_3d_consecutive
    distribution_weak  = (
        dev_ma200 > 15 and vol_shrink
        and any(e in sub_events for e in ('DistributionStart', 'UTAD', 'EVR'))
    )
    if distribution_start:
        score_Distribution += 3
        if any(e in sub_events for e in ('DistributionStart', 'UTAD')):
            score_Distribution += 1
        if 'EVR' in sub_events:
            score_Distribution += 1
    elif distribution_weak:
        score_Distribution += 2
        if any(e in sub_events for e in ('DistributionStart', 'UTAD')):
            score_Distribution += 1
    if distribution_start and 'UTAD' in sub_events:
        stage_detail = 'UTAD'

    # 取 max stage
    scores = {
        'Accumulation': score_Accumulation,
        'Markup': score_Markup,
        'Distribution': score_Distribution,
    }
    if max(scores.values()) > 0:
        stage = max(scores, key=scores.get)
    elif slope_60 > 0 and dev_ma200 > -15 and ma50 >= ma200 * 0.95 and trend_30 > -10:
        stage = 'Markup'; scores['Markup'] = 1
    elif len(sub_events) >= 1 and (slope_60 > 5 or slope_60 < -5):
        if trend_30 > 5 and slope_60 > -20:
            stage = 'Markup'; scores['Markup'] = 1
        elif trend_30 < -5:
            stage = 'Accumulation'; scores['Accumulation'] = 1
        else:
            stage = '?'
    else:
        stage = '?'

    max_score = scores.get(stage, 0)
    confidence = int(max_score / (sum(scores.values()) or 1) * 100)

    if not stage_detail and (stage == 'Accumulation' or score_Accumulation > 0):
        sub_names = {e if isinstance(e, str) else e.get('name', '') for e in sub_events}
        if 'Spring' in sub_names:
            stage_detail = 'Accum_Spring'
        elif accum_b_test_count_n >= 3:
            stage_detail = 'Accum_B'
        elif any(n in sub_names for n in ('LPS', 'EVR')):
            stage_detail = 'Accum_A'
        else:
            stage_detail = 'Accum_Fallback'

    if stage == 'Distribution' and stage_detail == 'UTAD':
        stage_name = '派发/UTAD'
    elif stage == 'Distribution':
        stage_name = '派发'
    else:
        stage_name = {'Accumulation': '累积', 'Markup': '主升浪', '?': '未知'}.get(stage, '未知')

    actions = {
        'Accumulation': '等待 Spring 触发 (sub-event) 确认建仓',
        'Markup': '持有, 主升浪中',
        'Distribution': '减仓, 警惕 UTAD',
        '?': '数据不足',
    }
    sub_list = sorted(sub_events) if isinstance(sub_events, set) else list(sub_events)

    return {
        "stage": stage, "stage_name": stage_name, "stage_detail": stage_detail,
        "action": actions.get(stage, '—'),
        "confidence": confidence,
        "sub_events": sub_list, "sub_event_count": len(sub_list),
        "phase_progress": _phase_progress_map(stage, stage_detail, sub_list, accum_abc_pass, distribution_start),
        "scores": scores,
        "accum_detail": {
            "base_low_ok": accum_base_low_ok, "ma_gap_ok": accum_ma_gap_ok,
            "volume_dry_ok": accum_volume_ok, "b_test_count": accum_b_test_count_n,
            "c_stage_ok": accum_c_ok, "pass_all": accum_abc_pass,
        },
        "distribution_detail": {
            "bias_200": dev_ma200, "bias_200_ok": dev_ma200 > 30,
            "vol_dry_3d": vol_dry_3d_consecutive, "distribution_start": distribution_start,
        },
        "markup_detail": {
            "golden_cross_recent": golden_cross_recent,
            "above_ma200_sustained": above_ma200_sustained,
            "ma_gap_pct_above": ma_gap_pct_above, "ma50_angle_5d": ma50_angle_5d,
        },
        "signals": [
            f"{window}根斜率({period_label}): {slope_60:+.1f}%",
            f"MA20偏({period_label}): {dev_ma20:+.1f}%",
            f"MA60偏({period_label}): {dev_ma60:+.1f}%",
            f"MA200偏({period_label}): {dev_ma200:+.1f}%",
            f"{window//2}根位置({period_label}): {pos_pct:.0f}%",
            f"子事件: {','.join(sub_list) if sub_list else '无'}",
        ],
        "verdict": f"{stage_name} (置信度 {confidence}%)",
        "趋势_30日_pct": trend_30,
    }


# ============================================================
# 兼容层 (v5.4): 老代码用 wyckoff_stage(closes, highs, lows, vols) 函数式调用
# 跟 WyckoffStageFactor().compute() 行为一致
# ============================================================
# v5.10.75 真实 phase_progress 映射: 经典 Wyckoff 子阶段 → 进度
# 比 max_score*20 靠谱 (那个 0/1 fallback 让所有票都是 20%)
PHASE_PROGRESS_MAP = {
    # Accumulation 子阶段 (经典 Wyckoff 4 阶段 A/B/C/D)
    ('Accumulation', 'Accum_A'):        20,   # 停止下跌
    ('Accumulation', 'Accum_B'):        50,   # 吸筹区间 (≥3 次测试底部)
    ('Accumulation', 'Accum_C'):        80,   # 测试成功 (准备 Spring)
    ('Accumulation', 'Accum_Spring'):   90,   # Spring 触发: 假跌破快速收回 → 即将主升
    ('Accumulation', 'Accum_Fallback'): 35,   # 阶段确认但无强信号 (放宽 fallback)
    # Markup 主升浪
    ('Markup', ''):                     100,  # 主升进行中
    # Distribution 派发子阶段
    ('Distribution', 'UTAD'):           30,   # 派发后上探前期阻力 (顶部信号)
}

def _phase_progress_map(stage, stage_detail, sub_events, accum_abc_pass, distribution_start):
    """经典 Wyckoff 子阶段 → phase_progress 0-100"""
    # 1. 优先用 stage_detail 精确映射
    if (stage, stage_detail) in PHASE_PROGRESS_MAP:
        base = PHASE_PROGRESS_MAP[(stage, stage_detail)]
    # 2. fallback: 阶段确认但 detail 空
    elif stage == 'Accumulation':
        base = 35  # fallback 中位
    elif stage == 'Markup':
        base = 100
    elif stage == 'Distribution':
        base = 60  # fallback 中位
    else:
        return 0   # stage = '?' 数据不足

    # 3. sub_event 加成 (Spring/SOS +10, LPS/UTAD +5, 其他 +2)
    bonus = 0
    for e in sub_events:
        if e in ('Spring', 'SOS'):
            bonus += 10
        elif e in ('LPS', 'UTAD', 'MarkupEntry', 'DistributionStart'):
            bonus += 5
        else:  # EVR, TrendPullback, Compression
            bonus += 2
    bonus = min(bonus, 20)  # 最多 +20

    # 4. 子条件加成
    if stage == 'Accumulation' and accum_abc_pass:
        bonus += 10
    if stage == 'Distribution' and distribution_start:
        bonus += 10

    return min(base + bonus, 100)


def wyckoff_stage(closes, highs, lows, vols, window=120, **kwargs):
    """兼容层: 老 signals_5method 用函数式调用 (backtest_5methods 已删, signals_5method 仍调用)"""
    df = pd.DataFrame({
        "close": closes,
        "high": highs,
        "low": lows,
        "volume": vols,
    })
    if 'open' in kwargs and kwargs['open']:
        df['open'] = kwargs['open']
    if 'pct_chg' in kwargs and kwargs['pct_chg']:
        df['pct_chg'] = kwargs['pct_chg']
    factor = WyckoffStageFactor()
    return factor.compute(df, window=window, **kwargs)


class WyckoffStageFactor(Factor):
    """威科夫 3 大阶段 (Accumulation / Markup / Distribution)

    输出字段 (dict) - 跟原 wyckoff_stage_v2 1:1:
      - 阶段 / stage: "Accumulation" / "Markup" / "Distribution" / "?"
      - 含义 / stage_name: 阶段中文名 (累积/主升浪/派发/未分类)
      - 操作 / action: 建议操作
      - 置信度 / confidence: 0-100
      - 趋势_30日_pct: 最近 30 日涨跌幅
      - sub_events: 9 种 sub_event 列表
      - sub_event_count: sub_event 数量
      - stage_detail: Accum_A/B/C / UTAD
      - phase_progress: 0-100
      - scores: {Accumulation, Markup, Distribution}
      - accum_detail: {base_low_ok, ma_gap_ok, volume_dry_ok, b_test_count, c_stage_ok, pass_all}
      - distribution_detail: {bias_200, bias_200_ok, vol_dry_3d, distribution_start}
      - markup_detail: {golden_cross_recent, above_ma200_sustained, ma_gap_pct_above, ma50_angle_5d}
      - signals: 7 条调试信号
      - 判定: "阶段 (含义, 置信度%)" 完整字符串
    """

    name = "wyckoff_stage"
    category = "wyckoff"
    dependencies = ["close", "high", "low", "volume"]
    description = "威科夫 3 大阶段: Accumulation / Markup / Distribution"
    version = "5.4"
    output_type = "dict"

    def compute(self, df: pd.DataFrame, **kwargs) -> dict:
        if df is None or len(df) < 30:
            return {
                "stage": "?", "stage_name": "数据不足",
                "action": "数据不足",
                "confidence": 0,
                "verdict": "数据不足",
                "趋势_30日_pct": None, "sub_events": [], "sub_event_count": 0,
                "stage_detail": "", "phase_progress": 0,
                "scores": {}, "signals": [],
            }

        try:
            # 1. 字段提取
            closes = df['close'].tolist()
            highs = df['high'].tolist()
            lows = df['low'].tolist()
            vols = df['volume'].tolist()
            opens = df['open'].tolist() if 'open' in df.columns else None
            pct_chgs = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None

            # 2. 参数
            window = kwargs.get('window', min(self.config.get('wyckoff.window', 250), len(closes)))
            range_lookback = kwargs.get('range_lookback', self.config.get('wyckoff.range_lookback', 30))
            ma_short = kwargs.get('ma_short', self.config.get('wyckoff.ma_short', 20))
            ma_long = kwargs.get('ma_long', self.config.get('wyckoff.ma_long', 60))
            pos_lookback = kwargs.get('pos_lookback', self.config.get('wyckoff.pos_lookback', 60))
            period_label = kwargs.get('period_label', 'daily')
            market_cap_yi = kwargs.get('market_cap_yi', 0.0)

            if len(closes) < window:
                return {
                    "stage": "?", "stage_name": "数据不足",
                    "action": "等待",
                    "confidence": 0,
                    "verdict": f"数据<{window}日",
                    "趋势_30日_pct": None, "sub_events": [], "sub_event_count": 0,
                    "stage_detail": "", "phase_progress": 0,
                    "scores": {}, "signals": [],
                }

            c = closes[-window:]
            h = highs[-window:]
            l = lows[-window:]
            v = vols[-window:]
            o = opens[-window:] if opens else None
            pc = pct_chgs[-window:] if pct_chgs else None
            # v5.10.42: 透传 dates 给 sub_event (带时间戳)
            dt = kwargs.get('dates') or (df['date'].tolist() if 'date' in df.columns else None)
            dt = dt[-window:] if dt else None
            # v5.10.42: as_of_idx 限制扫到 i <= as_of_idx (None=扫整段)
            as_of_idx = kwargs.get('as_of_idx', None)

            # 3. 找 Range
            rng = _find_range(c, h, l, v, lookback=range_lookback)

            # 4. 扫 9 sub_event (v5.10.42: 输出 list[dict] 带时间戳, 不去重)
            # 2026-08-26 改: sub_events 扫整段 (c/h/l/v/o), 不被 window 限制
            # 否则 250 根外的历史触发 (LPS 2023, EVR 2024) 看不到
            _pre = kwargs.get('precomputed_sub_events_raw')
            if _pre is not None:
                sub_events_raw = _pre
            else:
                sub_events_raw = scan_sub_events(closes, highs, lows, vols, rng, o=opens, pct_chg=pct_chgs,
                                                  market_cap_yi=market_cap_yi,
                                                  period_label=period_label,
                                                  as_of_idx=as_of_idx,
                                                  dates=kwargs.get('dates') or (df['date'].tolist() if 'date' in df.columns else None),
                                                  code=kwargs.get('code'))
            # v5.10.42: judge 用名字 set, sub_events 字段保留完整 list[dict]
            sub_events = sorted({e["name"] for e in sub_events_raw})

            # 5. 阶段判定 (跟原 wyckoff_stage_v2 评分逻辑 1:1)
            result = self._judge_full(c, h, l, v, sub_events, window, period_label,
                                       ma_short, ma_long, pos_lookback)

            # 6. 趋势 30 日涨跌幅
            trend_30_pct = round((c[-1] / c[-31] - 1) * 100, 1) if len(c) > 30 else None
            result["趋势_30日_pct"] = trend_30_pct
            # v5.10.40 改: 删中文 判定 字段 (v5.10.33 加了 verdict 兼容, 现在统一到 verdict)
            result["verdict"] = f"{result['stage']} ({result['stage_name']}, {result['confidence']}%)"
            # v5.10.42: sub_events 字段改用 list[dict] (带时间戳), 兼容老 list[str]
            result["sub_events"] = sub_events_raw
            return result

        except Exception as e:
            return {
                "stage": "?", "stage_name": "计算失败",
                "action": "—",
                "confidence": 0,
                "verdict": f"计算失败: {e}",
                "趋势_30日_pct": None, "sub_events": [], "sub_event_count": 0,
                "stage_detail": "", "phase_progress": 0,
                "scores": {}, "signals": [],
                "error": str(e),
            }

    def _judge_full(self, c, h, l, v, sub_events, window, period_label,
                    ma_short, ma_long, pos_lookback):
        """3 大阶段完整判定 (跟原 wyckoff_stage_v2._judge 1:1)"""
        p = c[-1]

        # ---- 基础指标 ----
        ma20 = _mean(c[-20:]) if len(c) >= 20 else 0
        ma50 = _mean(c[-50:]) if len(c) >= 50 else ma20
        ma60 = _mean(c[-60:]) if len(c) >= 60 else ma20
        ma200 = _mean(c) if len(c) >= 200 else ma60
        dev_ma20 = (p / ma20 - 1) * 100 if ma20 > 0 else 0
        dev_ma60 = (p / ma60 - 1) * 100 if ma60 > 0 else 0
        dev_ma200 = (p / ma200 - 1) * 100 if ma200 > 0 else 0
        # 斜率
        if len(c) >= 60:
            slope_60 = (c[-1] / c[-60] - 1) * 100
        else:
            slope_60 = 0
        # 价格位置
        if len(c) >= pos_lookback:
            recent_low = min(l[-pos_lookback:])
            recent_high = max(h[-pos_lookback:])
            if recent_high > recent_low > 0:
                pos_pct = (p - recent_low) / (recent_high - recent_low) * 100
            else:
                pos_pct = 50
        else:
            pos_pct = 50

        # ---- MA 金叉 / 持续上方 ----
        ma50_prev = _mean(c[-51:-1]) if len(c) >= 51 else ma50
        ma200_prev = _mean(c[:-1]) if len(c) >= 201 else ma200
        golden_cross_recent = (ma50_prev <= ma200_prev and ma50 > ma200)
        confirm_days = 5
        above_ma200 = p > ma200
        if len(c) >= ma_long + confirm_days:
            ma200_series = _sliding_ma(c, ma_long)
            recent_above_count = sum(1 for i in range(len(c)-confirm_days, len(c))
                                     if c[i] > ma200_series[i] * 0.98)
            above_ma200_sustained = recent_above_count >= confirm_days
        else:
            above_ma200_sustained = above_ma200

        # ---- 量能 ----
        vol_5 = _mean(v[-5:]) if len(v) >= 5 else 0
        vol_60 = _mean(v[-60:]) if len(v) >= 60 else 0
        vol_shrink = vol_5 < vol_60 * 0.5 if vol_60 > 0 else False
        dist_confirm_days = 3
        if len(v) >= 60 + dist_confirm_days:
            recent_vol_3d = _mean(v[-dist_confirm_days:])
            vol_dry_3d_consecutive = recent_vol_3d < vol_60 * 0.5
        else:
            vol_dry_3d_consecutive = vol_shrink

        # ---- 评分 ----
        score_Accumulation = score_Markup = score_Distribution = 0
        stage_detail = ""

        # ---- Accumulation 累积 ----
        accum_base_low_ok = accum_ma_gap_ok = accum_volume_ok = False
        accum_b_test_count_n = 0
        accum_c_ok = accum_abc_pass = False

        if len(c) >= 30:
            lookback_low = min(250, len(c))
            period_low = min(l[-lookback_low:])
            if period_low > 0 and p <= period_low * 1.45:
                accum_base_low_ok = True
            if ma200 > 0:
                ma_gap_pct = abs(ma50 - ma200) / ma200 * 100
                if ma_gap_pct <= 8:
                    accum_ma_gap_ok = True
            if len(v) >= 120:
                vol_recent = _mean(v[-20:])
                vol_ref = _mean(v[-120:-20])
            elif len(v) >= 40:
                vol_recent = _mean(v[-10:])
                vol_ref = _mean(v[-30:-10])
            else:
                vol_recent = vol_ref = 0
            if vol_ref > 0 and vol_recent / vol_ref < 0.75:
                accum_volume_ok = True
            if accum_base_low_ok:
                zone_window = min(60, len(c))
                zone_lows = l[-zone_window:]
                accum_b_test_count_n = sum(
                    1 for low_v in zone_lows
                    if abs(low_v - period_low) / period_low <= 0.05
                )
            if accum_base_low_ok and len(c) >= 20:
                recent_20_low = min(l[-20:])
                c_stage_ok = recent_20_low >= period_low * 0.97
                if c_stage_ok and vol_ref > 0 and vol_recent / vol_ref < 0.75:
                    accum_c_ok = True
            if accum_base_low_ok and accum_ma_gap_ok and accum_volume_ok:
                accum_abc_pass = True

        if accum_abc_pass:
            score_Accumulation += 3
            if any(e in sub_events for e in ['Spring', 'LPS', 'EVR']):
                score_Accumulation += 2
            if dev_ma20 < 5 and dev_ma60 < 0:
                score_Accumulation += 1
        elif (abs(slope_60) < 8 and 25 < pos_pct < 75
              and any(e in sub_events for e in ['Spring', 'LPS', 'EVR'])):
            score_Accumulation += 1
        elif (pos_pct < 30 and dev_ma200 < -5
              and any(e in sub_events for e in ['Spring', 'LPS', 'EVR'])):
            score_Accumulation += 1
        elif (pos_pct < 30 and dev_ma200 < -5 and len(sub_events) >= 1):
            score_Accumulation += 1

        if accum_abc_pass:
            if accum_b_test_count_n >= 3:
                stage_detail = 'Accum_B'
                if accum_c_ok:
                    stage_detail = 'Accum_C'
            else:
                stage_detail = 'Accum_A'

        # ---- Markup 主升浪 ----
        ma_gap_pct_above = (p - ma200) / ma200 * 100 if ma200 > 0 else 0
        ma50_angle_5d = 0
        if len(c) >= 55:
            ma50_5d_ago = _mean(c[-55:-50])
            ma50_now = _mean(c[-50:])
            if ma50_5d_ago > 0:
                ma50_angle_5d = (ma50_now / ma50_5d_ago - 1) * 100
        # 30日跌幅守卫：近30日跌超-15%说明处于下跌趋势，不能判主升浪
        trend_30 = (c[-1] / c[-31] - 1) * 100 if len(c) > 30 else slope_60
        is_uptrend = slope_60 > 0 and dev_ma200 > -10 and trend_30 > -15

        if golden_cross_recent and above_ma200_sustained and ma_gap_pct_above > 0.5 and is_uptrend:
            score_Markup += 3
            if 'SOS' in sub_events:
                score_Markup += 1
            if ma50_angle_5d >= 2.0:
                score_Markup += 1
        elif above_ma200 and ma_gap_pct_above > 0.5 and is_uptrend:
            score_Markup += 1
        if dev_ma20 > 5 and pos_pct > 60 and is_uptrend:
            score_Markup += 1

        # ---- Distribution 派发 ----
        distribution_start = dev_ma200 > 30 and vol_dry_3d_consecutive
        distribution_weak = (
            dev_ma200 > 15 and vol_shrink
            and any(e in sub_events for e in ['DistributionStart', 'UTAD', 'EVR'])
        )
        if distribution_start:
            score_Distribution += 3
            if any(e in sub_events for e in ['DistributionStart', 'UTAD']):
                score_Distribution += 1
            if 'EVR' in sub_events:
                score_Distribution += 1
        elif distribution_weak:
            score_Distribution += 2
            if any(e in sub_events for e in ['DistributionStart', 'UTAD']):
                score_Distribution += 1
        if distribution_start and 'UTAD' in sub_events:
            stage_detail = 'UTAD'

        # ---- 取 max stage ----
        scores = {
            'Accumulation': score_Accumulation,
            'Markup': score_Markup,
            'Distribution': score_Distribution,
        }
        if max(scores.values()) > 0:
            stage = max(scores, key=scores.get)
        elif slope_60 > 0 and dev_ma200 > -15 and ma50 >= ma200 * 0.95 and trend_30 > -10:
            # fallback Markup：30日跌幅 > -10% 不算主升
            stage = 'Markup'
            scores['Markup'] = 1
        elif len(sub_events) >= 1 and (slope_60 > 5 or slope_60 < -5):
            slope_30 = trend_30  # 已算好
            if slope_30 > 5 and slope_60 > -20:
                # 60日整体跌 > 20%，哪怕近30日反弹也不是主升
                stage = 'Markup'
                scores['Markup'] = 1
            elif slope_30 < -5:
                stage = 'Accumulation'
                scores['Accumulation'] = 1
            else:
                stage = '?'
        else:
            stage = '?'
        max_score = scores.get(stage, 0)
        total = sum(scores.values()) or 1
        confidence = int(max_score / total * 100)

        # v5.10.75 放宽 stage_detail 判定: 即使 3/3 子条件不满足, 也按 sub_event + b_test 判 Accumulation detail
        if not stage_detail and (stage == 'Accumulation' or score_Accumulation > 0):
            sub_event_names = {e.get('name', '') if isinstance(e, dict) else str(e) for e in sub_events}
            if 'Spring' in sub_event_names:
                stage_detail = 'Accum_Spring'  # D 阶段: 假跌破快速收回
            elif accum_b_test_count_n >= 3:
                stage_detail = 'Accum_B'  # 吸筹区间
            elif any(n in sub_event_names for n in ('LPS', 'EVR')):
                stage_detail = 'Accum_A'  # 停止下跌 + 巨量滞涨
            else:
                stage_detail = 'Accum_Fallback'  # 阶段确认但无强信号

        # ---- 阶段名 (v5.10.75: 只放中文, detail 由 render 拼) ----
        if stage == 'Distribution' and stage_detail == 'UTAD':
            stage_name = '派发/UTAD'
        elif stage == 'Distribution':
            stage_name = '派发'
        else:
            stage_names = {
                'Accumulation': '累积',
                'Markup': '主升浪',
                'Distribution': '派发',
                '?': '未知'
            }
            stage_name = stage_names.get(stage, '未知')

        actions = {
            'Accumulation': '等待 Spring 触发 (sub-event) 确认建仓',
            'Markup': '持有, 主升浪中',
            'Distribution': '减仓, 警惕 UTAD',
            '?': '数据不足'
        }

        return {
            "stage": stage,
            "stage_name": stage_name,
            "stage_detail": stage_detail,
            "action": actions.get(stage, '—'),
            "confidence": confidence,
            "sub_events": sub_events,
            "sub_event_count": len(sub_events),
            "phase_progress": _phase_progress_map(stage, stage_detail, sub_events, accum_abc_pass, distribution_start),
            "scores": scores,
            "accum_detail": {
                "base_low_ok": accum_base_low_ok,
                "ma_gap_ok": accum_ma_gap_ok,
                "volume_dry_ok": accum_volume_ok,
                "b_test_count": accum_b_test_count_n,
                "c_stage_ok": accum_c_ok,
                "pass_all": accum_abc_pass,
            },
            "distribution_detail": {
                "bias_200": dev_ma200,
                "bias_200_ok": dev_ma200 > 30,
                "vol_dry_3d": vol_dry_3d_consecutive,
                "distribution_start": distribution_start,
            },
            "markup_detail": {
                "golden_cross_recent": golden_cross_recent,
                "above_ma200_sustained": above_ma200_sustained,
                "ma_gap_pct_above": ma_gap_pct_above,
                "ma50_angle_5d": ma50_angle_5d,
            },
            "signals": [
                f"{window}根斜率({period_label}): {slope_60:+.1f}%",
                f"MA20偏({period_label}): {dev_ma20:+.1f}%",
                f"MA60偏({period_label}): {dev_ma60:+.1f}%",
                f"MA200偏({period_label}): {dev_ma200:+.1f}%",
                f"{window//2}根位置({period_label}): {pos_pct:.0f}%",
                f"子事件: {','.join(sub_events) if sub_events else '无'}",
                f"DEBUG_Accum: abc_pass={accum_abc_pass} score={score_Accumulation} base={accum_base_low_ok} ma_gap={accum_ma_gap_ok} vol={accum_volume_ok} dev200={dev_ma200:+.1f} pos={pos_pct:.0f}",
            ],
        }
