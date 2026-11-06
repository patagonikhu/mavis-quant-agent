"""
eastmoney.py — 技术指标计算 + 周线合成 (纯计算, 0 网络)

⚠️ 2026-11-02 大幅瘦身 (928 → 668 行):
  - 删 get_kline / fetch_all / status_emoji 三个 0 外部调用点的死函数
    (AST 可达性分析: 全仓只 import compute_indicators / _build_eps_table /
     _synthesize_weekly 三个)
  - 删未用 import (json / subprocess / Any / Optional)
  - 历史 curl helper (_get_with_retry / _get_session / _secid / _secucode 等)
    2026-10-30 已删 (0 调用, 且 push2 属 WAF 拒接域)

⚠️ 2026-11-04 删 _build_eps_table (datacenter EPS 一致预期):
  - EPS 预期是**预测值**, 11-02 删 PEG/DCF 后已无任何分析消费者
  - 估值口径固化: 只看 PE (静态) + 盈利质量, 全部用已审计 TTM 财报算
  - 本文件由此变成纯计算模块, 不再有任何联网函数

**现在只剩 2 个对外入口:**
  1. _synthesize_weekly(kline) → 日线合成周线 (纯计算)
  2. compute_indicators(kline) → 8 大技术指标 (纯计算, 无网络)

数据源矩阵:
  ┌──────────────┬─────────────────────────────────────────┐
  │ 数据         │  唯一源                                     │
  ├──────────────┼─────────────────────────────────────────┤
  │ K 线         │  Tushare.daily (见 tools/storage/sources/tushare.py) │
  │ 财务         │  Tushare.fina_indicator (financials parquet) │
  │ 总股本/市值  │  Tushare.stock_basic.total_share + daily_basic.total_mv │
  └──────────────┴─────────────────────────────────────────┘

调用方:
  - store.py → _synthesize_weekly
  - analysis_engine.py / render_data.py → compute_indicators
"""

from __future__ import annotations

# 2026-11-06 删: 加载 config/project.yaml 的整块死代码
#   理由: 本文件是**纯计算模块** (0 网络), 加载了 _PROJECT_CFG 但一次都没用
#         (唯一引用就是这行赋值本身), 却给纯计算模块加了个「project.yaml 必须存在」
#         的硬依赖 —— 而 project.yaml 里当时被引用的 data.kline_60m_count 是 60min
#         的残留配置 (60min 已无消费者)。
#   真正读 config 的是 store.py (data.kline_days) 和 tushare.py (data.weekly_limit)。

# ============================================================
# 1. 周线合成 (纯计算)
# ============================================================

def _synthesize_weekly(kline: list[dict]) -> list[dict]:
    """从日线合成周线（自然周，周一开盘~周五收盘）。"""
    from datetime import datetime
    if not kline:
        return []

    weeks: dict[str, list] = {}
    for bar in kline:
        try:
            d = datetime.strptime(str(bar["trade_date"])[:8], "%Y%m%d")
        except ValueError:
            try:
                d = datetime.strptime(str(bar["trade_date"])[:10], "%Y-%m-%d")
            except ValueError:
                continue
        # ISO 周键：年-周号
        week_key = d.strftime("%G-%V")
        weeks.setdefault(week_key, []).append(bar)

    result = []
    for week_key in sorted(weeks):
        bars = weeks[week_key]
        result.append({
            "trade_date": bars[-1]["trade_date"],  # 周五（最后一个交易日）
            "open":    bars[0]["open"],
            "close":   bars[-1]["close"],
            "high":    max(b["high"] for b in bars),
            "low":     min(b["low"]  for b in bars),
            "volume":  sum(b.get("volume", b.get("vol", 0)) for b in bars),
            "amount":  sum(b.get("amount", 0) for b in bars),
            "pct_chg": round(
                (bars[-1]["close"] / bars[0]["open"] - 1) * 100, 2
            ) if bars[0]["open"] else 0,
        })
    return result

# ============================================================
# 2. 技术指标计算 (Wilder 标准公式)
# ============================================================

def compute_indicators(kline: list[dict]) -> dict:
    """
    基于 K 线计算 8 个技术指标 (Wilder 1978 标准公式)

    Args:
        kline: [{"date", "open", "close", "high", "low", "vol"}, ...]

    Returns:
        {
            "macd": {"DIF": float, "DEA": float, "BAR": float, "verdict": str},
            "rsi": {"rsi6": float, "rsi12": float, "rsi24": float, "verdict": str},
            "kdj": {"K": float, "D": float, "J": float, "verdict": str},
            "boll": {"mid": float, "upper": float, "lower": float, "verdict": str},
            "atr": {"atr14": float, "verdict": str},
            "vol_ma": {"vol_ma5": float, "vol_ma10": float, "vol_ratio": float, "verdict": str},
            "summary": str,  # 综合判定
        }

    参考: 东方财富 / 同花顺 / 通达信 标准实现
    """
    if not kline or len(kline) < 20:
        return {"error": "K线不足 (需 >= 20 条)"}

    # 2026-08-06 修复: dump 顶层 kline 用 'volume' 字段, 老代码用 'vol', 兼容两种
    def _v(bar):
        return bar.get("volume") if "volume" in bar else bar.get("vol", 0)

    closes = [bar["close"] for bar in kline]
    highs = [bar["high"] for bar in kline]
    lows = [bar["low"] for bar in kline]
    vols = [_v(bar) for bar in kline]
    n = len(closes)
    current = closes[-1]

    result = {}
    # v6.2.8 改: TechnicalStrategy 需要 series 时序, 这里预算完整 series 一并存入 result
    # 系列长度对齐到 K 线, 前面不足的位填 None

    def _align_to_kline(series: list, series_start_idx: int) -> list:
        """把 series 对齐到 K 线, 前面 series_start_idx 位填 None"""
        out = [None] * series_start_idx + list(series)
        if len(out) < n:
            out = out + [None] * (n - len(out))
        return out[:n]

    # ========== 1. MACD (12, 26, 9) ==========
    # 算完整 EMA12/EMA26 序列, 然后 DIF = EMA12 - EMA26, DEA = EMA9(DIF)
    ema12_series = _ema_series(closes, 12)
    ema26_series = _ema_series(closes, 26)
    # 对齐: EMA12 从第 12 个开始, EMA26 从第 26 个开始
    # 取后 min(len12, len26) 部分
    min_len = min(len(ema12_series), len(ema26_series))
    dif_series = [ema12_series[-min_len + i] - ema26_series[-min_len + i] for i in range(min_len)]
    if len(dif_series) >= 9:
        dea_series = _ema_series(dif_series, 9)
    else:
        dea_series = [sum(dif_series) / len(dif_series)] * len(dif_series) if dif_series else [0]
    dif = dif_series[-1] if dif_series else 0
    dea = dea_series[-1] if dea_series else 0
    bar = (dif - dea) * 2
    if dif > dea and dif > 0:
        macd_verdict = "🟢 金叉多头 (DIF>DEA>0)"
    elif dif > dea and dif < 0:
        macd_verdict = "🟡 金叉弱势 (DIF>DEA 但<0)"
    elif dif < dea and dif < 0:
        macd_verdict = "🔴 死叉空头 (DIF<DEA<0)"
    else:
        macd_verdict = "🟠 死叉强势 (DIF<DEA 但>0)"
    result["macd"] = {
        "DIF": round(dif, 4),
        "DEA": round(dea, 4),
        "BAR": round(bar, 4),
        "verdict": macd_verdict,
        # v6.2.8 加: series 字段, 供 TechnicalStrategy 用 (前面对齐 None)
        "dif_series": _align_to_kline(dif_series, n - len(dif_series)),
        "dea_series": _align_to_kline(dea_series, n - len(dea_series)),
    }

    # ========== 2. RSI (6, 12, 24) ==========
    rsi6 = _rsi(closes, 6)
    rsi12 = _rsi(closes, 12)
    rsi24 = _rsi(closes, 24)
    rsi_verdict = _rsi_verdict(rsi6, rsi12, rsi24)
    # v6.2.8 加: RSI series (Wilder 递归)
    rsi6_series = _rsi_series(closes, 6)
    rsi12_series = _rsi_series(closes, 12)
    rsi24_series = _rsi_series(closes, 24)
    result["rsi"] = {
        "rsi6": round(rsi6, 2),
        "rsi12": round(rsi12, 2),
        "rsi24": round(rsi24, 2),
        "verdict": rsi_verdict,
        "rsi6_series": rsi6_series,
        "rsi12_series": rsi12_series,
        "rsi24_series": rsi24_series,
    }

    # ========== 3. KDJ (9, 3, 3) ==========
    k, d, j = _kdj(highs, lows, closes, n=9, m1=3, m2=3)
    kdj_verdict = _kdj_verdict(k, d, j)
    # v6.2.8 加: KDJ series (sliding window)
    kdj_k_series, kdj_d_series, kdj_j_series = _kdj_series(highs, lows, closes, n=9, m1=3, m2=3)
    result["kdj"] = {
        "K": round(k, 2),
        "D": round(d, 2),
        "J": round(j, 2),
        "verdict": kdj_verdict,
        "k_series": kdj_k_series,
        "d_series": kdj_d_series,
        "j_series": kdj_j_series,
    }

    # ========== 4. BOLL (20, 2) ==========
    boll_series_mid, boll_series_upper, boll_series_lower, boll_pct_series = _boll_series(closes, 20, 2)
    if n >= 20:
        mid20 = sum(closes[-20:]) / 20
        # 总体标准差
        variance = sum((c - mid20) ** 2 for c in closes[-20:]) / 20
        std20 = variance ** 0.5
        upper = mid20 + 2 * std20
        lower = mid20 - 2 * std20
        if current > upper:
            boll_verdict = "🟠 突破上轨 (强势但超买)"
        elif current < lower:
            boll_verdict = "🟢 跌破下轨 (弱势可能反弹)"
        elif current > mid20:
            boll_verdict = "🟡 中轨上方 (偏多)"
        else:
            boll_verdict = "⚪ 中轨下方 (偏空)"
        result["boll"] = {
            "mid": round(mid20, 2),
            "upper": round(upper, 2),
            "lower": round(lower, 2),
            "width": round((upper - lower) / mid20 * 100, 2),  # 带宽 %
            "verdict": boll_verdict,
            # v6.2.8 加: BOLL series
            "mid_series": boll_series_mid,
            "upper_series": boll_series_upper,
            "lower_series": boll_series_lower,
            "pct_series": boll_pct_series,  # (close-lower)/(upper-lower), 0=下轨, 1=上轨
        }
    else:
        result["boll"] = {"error": "K线 < 20"}

    # ========== 5. ATR (14) ==========
    atr14_series = _atr_series(highs, lows, closes, 14)
    if n >= 15:
        atr14 = _atr(highs, lows, closes, 14)
        # ATR 占价格比 (波动率)
        atr_pct = atr14 / current * 100
        if atr_pct > 5:
            atr_verdict = f"🔴 高波动 ({atr_pct:.1f}%)"
        elif atr_pct > 3:
            atr_verdict = f"🟠 中波动 ({atr_pct:.1f}%)"
        else:
            atr_verdict = f"✅ 低波动 ({atr_pct:.1f}%)"
        result["atr"] = {
            "atr14": round(atr14, 2),
            "atr_pct": round(atr_pct, 2),
            "verdict": atr_verdict,
            "atr14_series": atr14_series,
            "atr_pct_series": [round(a / c * 100, 2) if (a and c) else None
                               for a, c in zip(atr14_series, closes)],
        }
    else:
        result["atr"] = {"error": "K线 < 15"}

    # ========== 6. 量比 (vol_ratio) ==========
    if n >= 6:
        vol_ma5 = sum(vols[-6:-1]) / 5 if len(vols) >= 6 else 0
        vol_ratio = vols[-1] / vol_ma5 if vol_ma5 > 0 else 0
        if vol_ratio > 2:
            vol_verdict = f"🟠 放量 ({vol_ratio:.2f}x) — 关注"
        elif vol_ratio > 1.2:
            vol_verdict = f"🟡 温和放量 ({vol_ratio:.2f}x)"
        elif vol_ratio < 0.7:
            vol_verdict = f"🟢 缩量 ({vol_ratio:.2f}x)"
        else:
            vol_verdict = f"⚪ 正常 ({vol_ratio:.2f}x)"
        # v6.2.8 加: vol_ratio series (sliding)
        vol_ratio_series = _vol_ratio_series(vols, window=5)
        result["vol_ma"] = {
            "vol_ma5": round(vol_ma5, 0),
            "vol_today": round(vols[-1], 0),
            "vol_ratio": round(vol_ratio, 2),
            "verdict": vol_verdict,
            "vol_ratio_series": vol_ratio_series,
            "vol_ma5_series": _vol_ma_series(vols, 5),
        }
    else:
        result["vol_ma"] = {"error": "K线 < 6"}

    # ========== 7. 综合判定 ==========
    signals = []
    if "macd" in result and "verdict" in result["macd"]:
        signals.append(result["macd"]["verdict"])
    if "rsi" in result and "verdict" in result["rsi"]:
        signals.append(result["rsi"]["verdict"])
    if "kdj" in result and "verdict" in result["kdj"]:
        signals.append(result["kdj"]["verdict"])
    if "boll" in result and "verdict" in result["boll"]:
        signals.append(result["boll"]["verdict"])

    bullish = sum(1 for s in signals if any(x in s for x in ["🟢", "🟡金叉多头", "买入", "偏低", "缩量"]))
    bearish = sum(1 for s in signals if any(x in s for x in ["🔴", "死叉空头", "卖出", "超买", "放量"]))
    if bullish >= 3:
        summary = f"🟢 综合偏多 ({bullish}/{len(signals)} 看多)"
    elif bearish >= 3:
        summary = f"🔴 综合偏空 ({bearish}/{len(signals)} 看空)"
    else:
        summary = f"🟡 震荡 ({bullish}多 / {bearish}空)"

    result["summary"] = summary
    return result


# ============================================================
# 3. 指标计算子函数 (Wilder 标准)
# ============================================================

def _ema_series(data: list[float], period: int) -> list[float]:
    """EMA 序列"""
    if not data:
        return []
    if len(data) < period:
        return [sum(data) / len(data)] * len(data)
    k = 2 / (period + 1)
    ema = sum(data[:period]) / period
    series = [ema]
    for price in data[period:]:
        ema = price * k + ema * (1 - k)
        series.append(ema)
    return series


def _rsi(closes: list[float], period: int = 14) -> float:
    """Wilder RSI"""
    if len(closes) < period + 1:
        return 50.0  # 数据不足时返回中性
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    # 初始均值
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # Wilder 平滑
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _kdj(highs: list[float], lows: list[float], closes: list[float],
         n: int = 9, m1: int = 3, m2: int = 3) -> tuple[float, float, float]:
    """KDJ 指标 (n=9, m1=3, m2=3 标准)"""
    if len(closes) < n:
        return 50.0, 50.0, 50.0
    # 最近 n 日的 H/L/C
    h_n = max(highs[-n:])
    l_n = min(lows[-n:])
    rsv = (closes[-1] - l_n) / (h_n - l_n) * 100 if h_n > l_n else 50.0

    # K = (m1-1)/m1 * K_prev + 1/m1 * RSV (K 初始 50)
    # D = (m2-1)/m2 * D_prev + 1/m2 * K
    # 简化为: 直接从 RSV 算
    K = rsv  # 简化: 第一个 K = RSV
    D = rsv
    for i in range(len(closes) - n - 1, 0, -1):
        if i < 0:
            break
        h_n = max(highs[max(0, i - n + 1):i + 1])
        l_n = min(lows[max(0, i - n + 1):i + 1])
        if h_n > l_n:
            rsv_i = (closes[i] - l_n) / (h_n - l_n) * 100
        else:
            rsv_i = 50
        K = (m1 - 1) / m1 * K + 1 / m1 * rsv_i
        D = (m2 - 1) / m2 * D + 1 / m2 * K

    J = 3 * K - 2 * D
    return K, D, J


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Wilder ATR"""
    if len(closes) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # 初始 ATR
    atr = sum(trs[:period]) / period
    # Wilder 平滑
    for i in range(period, len(trs)):
        atr = (atr * (period - 1) + trs[i]) / period
    return atr


def _rsi_verdict(rsi6: float, rsi12: float, rsi24: float) -> str:
    """RSI 综合判定"""
    avg = (rsi6 + rsi12 + rsi24) / 3
    if rsi6 > 80 and rsi12 > 75:
        return f"🔴 严重超买 (RSI6={rsi6:.0f})"
    if rsi6 > 70:
        return f"🟠 超买 (RSI6={rsi6:.0f})"
    if rsi6 < 20 and rsi12 < 25:
        return f"🟢 严重超卖 (RSI6={rsi6:.0f}) — 反弹机会"
    if rsi6 < 30:
        return f"🟢 超卖 (RSI6={rsi6:.0f})"
    if 40 < avg < 60:
        return f"⚪ 中性 (RSI均值={avg:.0f})"
    return f"🟡 正常 (RSI6={rsi6:.0f}, 12={rsi12:.0f})"


def _kdj_verdict(k: float, d: float, j: float) -> str:
    """KDJ 综合判定"""
    if j < 0:
        return f"🟢 J<0 超卖 (K={k:.0f}, D={d:.0f}, J={j:.0f})"
    if j > 100:
        return f"🔴 J>100 超买 (K={k:.0f}, D={d:.0f}, J={j:.0f})"
    if k > d and k < 30:
        return f"🟢 低位金叉 (K={k:.0f} > D={d:.0f})"
    if k < d and k > 70:
        return f"🔴 高位死叉 (K={k:.0f} < D={d:.0f})"
    if k > d:
        return f"🟡 金叉 (K={k:.0f} > D={d:.0f})"
    return f"🟠 死叉 (K={k:.0f} < D={d:.0f})"


# ============================================================
# v6.2.8 加: 5 个 series 辅助函数 (TechnicalStrategy 用)
# 返回长度 == K线数, 不足期填 None
# ============================================================

def _rsi_series(closes: list[float], period: int = 14) -> list:
    """Wilder RSI 序列 (返回长度=len(closes), 前面 period 位填 None)"""
    n = len(closes)
    if n < period + 1:
        return [None] * n
    # gain/loss 序列
    gains = [0.0]
    losses = [0.0]
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    # Wilder 初始均值
    avg_gain = sum(gains[1:period + 1]) / period
    avg_loss = sum(losses[1:period + 1]) / period
    series = [None] * (period)  # 前 period 位无值
    # period 位 RSI
    if avg_loss == 0:
        series.append(100.0)
    else:
        rs = avg_gain / avg_loss
        series.append(100 - 100 / (1 + rs))
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            series.append(100.0)
        else:
            rs = avg_gain / avg_loss
            series.append(100 - 100 / (1 + rs))
    return series


def _kdj_series(highs: list[float], lows: list[float], closes: list[float],
                n: int = 9, m1: int = 3, m2: int = 3) -> tuple[list, list, list]:
    """KDJ 序列 (返回 3 个 list, 长度=len(closes), 前面 n 位填 None)"""
    N = len(closes)
    if N < n:
        return [None] * N, [None] * N, [None] * N
    K_prev = 50.0
    D_prev = 50.0
    K_series = [None] * (n - 1)  # 前 n-1 位无值
    D_series = [None] * (n - 1)
    J_series = [None] * (n - 1)
    for i in range(n - 1, N):
        h_n = max(highs[max(0, i - n + 1):i + 1])
        l_n = min(lows[max(0, i - n + 1):i + 1])
        rsv = (closes[i] - l_n) / (h_n - l_n) * 100 if h_n > l_n else 50.0
        K = (m1 - 1) / m1 * K_prev + 1 / m1 * rsv
        D = (m2 - 1) / m2 * D_prev + 1 / m2 * K
        J = 3 * K - 2 * D
        K_series.append(K)
        D_series.append(D)
        J_series.append(J)
        K_prev, D_prev = K, D
    return K_series, D_series, J_series


def _boll_series(closes: list[float], n: int = 20, k: float = 2) -> tuple[list, list, list, list]:
    """BOLL 序列 (mid/upper/lower/pct), 返回 4 个 list, 长度=len(closes), 前面 n-1 位填 None

    pct = (close - lower) / (upper - lower), 0=下轨, 1=上轨
    """
    N = len(closes)
    if N < n:
        return [None] * N, [None] * N, [None] * N, [None] * N
    mid_series = [None] * (n - 1)
    upper_series = [None] * (n - 1)
    lower_series = [None] * (n - 1)
    pct_series = [None] * (n - 1)
    for i in range(n - 1, N):
        window = closes[i - n + 1:i + 1]
        mid = sum(window) / n
        variance = sum((c - mid) ** 2 for c in window) / n
        std = variance ** 0.5
        upper = mid + k * std
        lower = mid - k * std
        pct = (closes[i] - lower) / (upper - lower) if (upper > lower) else 0.5
        mid_series.append(mid)
        upper_series.append(upper)
        lower_series.append(lower)
        pct_series.append(pct)
    return mid_series, upper_series, lower_series, pct_series


def _atr_series(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list:
    """Wilder ATR 序列, 长度=len(closes), 前面 period 位填 None"""
    N = len(closes)
    if N < period + 1:
        return [None] * N
    trs = [None]  # i=0 无 TR
    for i in range(1, N):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # 初始 ATR
    atr = sum(trs[1:period + 1]) / period
    series = [None] * period + [atr]
    for i in range(period + 1, N):
        atr = (atr * (period - 1) + trs[i]) / period
        series.append(atr)
    return series


def _vol_ratio_series(vols: list[float], window: int = 5) -> list:
    """量比序列 (vol[i] / MA(vols[i-window:i], window))

    长度=len(vols), 前面 window 位填 None
    """
    N = len(vols)
    if N < window + 1:
        return [None] * N
    series = [None] * window
    for i in range(window, N):
        ma = sum(vols[i - window:i]) / window
        series.append(vols[i] / ma if ma > 0 else 1.0)
    return series


def _vol_ma_series(vols: list[float], window: int = 5) -> list:
    """成交量 MA 序列, 长度=len(vols), 前面 window-1 位填 None"""
    N = len(vols)
    series = [None] * (window - 1)
    for i in range(window - 1, N):
        series.append(sum(vols[i - window + 1:i + 1]) / window)
    return series


# ============================================================
# CLI
# ============================================================

if __name__ == "__main__":
    import sys

    from tools.storage.store import DataStore

    test_code = sys.argv[1] if len(sys.argv) > 1 else "002371"
    kline = DataStore.get_kline(test_code)
    print(f"\n=== eastmoney 纯计算模块自检: {test_code} ===\n")

    weekly = _synthesize_weekly(kline)
    print(f"周线合成: {len(kline)} 日 → {len(weekly)} 周")

    ind = compute_indicators(kline)
    if ind.get("error"):
        print(f"技术指标: {ind['error']}")
    else:
        print(f"技术指标: RSI6={ind.get('rsi6')} / MA20={ind.get('ma20')} / KDJ-K={ind.get('k', ind.get('kdj_k'))}")
