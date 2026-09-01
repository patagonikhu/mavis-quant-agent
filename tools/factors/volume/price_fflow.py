"""
volume/price_fflow.py - fflow + OBV 量价因子 (纯计算, 无网络请求)

**2026-08-17 拆分**: 之前 price_fflow_factor 一个函数混 fflow + OBV + 双判定, 容易让
调用方搞混. 现在拆成两个独立函数:

- fflow_factor(): 只算 Tushare money_flow 主力净流入, 5 档判定
- obv_factor(): 只算经典 Granville 1963 OBV, 含 5 类信号 + 60 日段背离

双判定同向/矛盾逻辑在 analysis_engine 聚合层 (FflowStrategy.analyze) 算, 不再混在
factor 函数里. 这样:

  1. 因子库职责单一, fflow 和 OBV 各自纯计算
  2. 聚合在 strategy 层做, 容易测试和调权
  3. 出报告时 render 层可独立读 fflow_result / obv_result

输入数据: moneyflow_list (dump['tushare']['money_flow']) / closes+vols (K线) —
          都由 sync_watchlist_fresh.py 预拉, factor 层只做计算。
"""
from pathlib import Path
import sys

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================
# fflow (主力资金流) 因子
# ============================================================

def fflow_factor(code: str, moneyflow_list=None, dates=None, asof=None) -> dict:
    """fflow 主力资金流因子 — 纯计算, 无网络

    输入: Tushare money_flow (dump 预拉)
    输出: 5 档判定 + 3/5/10/20/30/60 日主力累计 + 趋势标记

    Args:
        code: 股票代码 (签名占位, 不参与计算)
        moneyflow_list: dump['tushare']['money_flow'] 列表, 每条含 trade_date/date + main_yi/net_mf_amount
        dates: 跟 moneyflow 对齐的日期列表 (YYYYMMDD), 用于 asof 切片 (可不传, 跟 moneyflow 自带 trade_date 等价)
        asof: 'YYYY-MM-DD' / 'YYYYMMDD' — 算到该日期及之前的状态 (None = 最新)

    Returns:
        dict:
          - score: -2 ~ +2
          - verdict: "🟢主力进货" / "🟡偏进货" / "⬜中性" / "🟠偏出货" / "🔴主力出货" [+ 短线/中线矛盾标记]
          - signals: 列表, 各周期累计金额字符串
          - source: "Tushare.money_flow (dump 预拉)" / "无数据"
          - fflow_net_3d / 5d / 10d / 20d / 30d / 60d: 主力净流入累计 (亿元)
          - trend_3d / 10d / 30d: "↑进货" / "↓出货"
          - raw_fflow: 最近 10 天数据
          - asof: "YYYYMMDD" / "latest"
    """
    data = moneyflow_list or []

    # asof 切片 (走 factors/utils.normalize_asof 统一格式)
    if asof and data:
        from tools.factors.utils import normalize_asof
        asof_norm = normalize_asof(asof)
        if asof_norm:
            data = [d for d in data
                    if str(d.get("trade_date", d.get("date", ""))).replace("-", "")[:8] <= asof_norm]

    if not data or len(data) < 3:
        return {
            "score": 0, "verdict": "无数据", "signals": [], "source": "无数据",
            "fflow_net_3d": 0, "fflow_net_5d": 0, "fflow_net_10d": 0,
            "fflow_net_20d": 0, "fflow_net_30d": 0, "fflow_net_60d": 0,
            "trend_3d": "?", "trend_10d": "?", "trend_30d": "?", "raw_fflow": [],
            "asof": asof or "latest",
        }

    def _net(d):
        # tushare moneyflow: main_yi (亿) 或 net_mf_amount (万元)
        if "main_yi" in d:
            return float(d.get("main_yi", 0) or 0)
        return float(d.get("net_mf_amount", 0) or 0) / 10000

    net_3  = sum(_net(d) for d in data[-3:])
    net_5  = sum(_net(d) for d in data[-5:])
    net_10 = sum(_net(d) for d in data[-10:])
    net_20 = sum(_net(d) for d in data[-20:])
    net_30 = sum(_net(d) for d in data[-30:]) if len(data) >= 30 else net_20
    net_60 = sum(_net(d) for d in data[-60:]) if len(data) >= 60 else net_30

    trend_3d  = "↑进货" if net_3  > 0 else "↓出货"
    trend_10d = "↑进货" if net_10 > 0 else "↓出货"
    trend_30d = "↑进货" if net_30 > 0 else "↓出货"

    if net_3 >= 5 and net_10 > 0:
        verdict, score = "🟢主力进货", 2
    elif net_3 >= 1 and net_10 > 0:
        verdict, score = "🟡偏进货", 1
    elif net_3 > -1:
        verdict, score = "⬜中性", 0
    elif net_3 > -5:
        verdict, score = "🟠偏出货", -1
    else:
        verdict, score = "🔴主力出货", -2

    conflict = " ⚠️ 短线 vs 中线方向矛盾" if (net_3 > 0) != (net_30 > 0) else ""

    return {
        "score": score,
        "verdict": verdict + conflict,
        "signals": [
            f"3日主力: {net_3:+.2f}亿 ({trend_3d})",
            f"5日主力: {net_5:+.2f}亿",
            f"10日主力: {net_10:+.2f}亿 ({trend_10d})",
            f"20日主力: {net_20:+.2f}亿",
            f"30日主力: {net_30:+.2f}亿 ({trend_30d})",
            f"60日主力: {net_60:+.2f}亿",
            f"最近日: {data[-1].get('trade_date', data[-1].get('date', ''))} {_net(data[-1]):+.2f}亿",
        ],
        "source": "Tushare.money_flow (dump 预拉)",
        "fflow_net_3d": net_3, "fflow_net_5d": net_5,
        "fflow_net_10d": net_10, "fflow_net_20d": net_20,
        "fflow_net_30d": net_30, "fflow_net_60d": net_60,
        "trend_3d": trend_3d, "trend_10d": trend_10d, "trend_30d": trend_30d,
        "raw_fflow": data[-10:],
        "asof": asof or "latest",
    }


# ============================================================
# OBV (经典 Granville 1963) 因子
# ============================================================

def obv_factor(closes, vols, dates=None, asof=None) -> dict:
    """OBV 因子 — 经典 Granville 1963 累计 + 5 类信号 + 60 日段背离

    纯 K 线计算, 无网络。累计规则:
      close[i] > close[i-1] → obv += vol[i]
      close[i] < close[i-1] → obv -= vol[i]
      close[i] = close[i-1] → obv 不变

    Args:
        closes: 收盘价列表
        vols:   成交量列表 (与 closes 等长)
        dates:  日期列表 (YYYYMMDD 字符串, 与 closes 等长) — 用于 asof 切片
        asof:   'YYYY-MM-DD' / 'YYYYMMDD' — 算到该日期及之前的状态 (None = 最新)
                注意: asof 只在 dates 完整时生效

    Returns:
        dict:
          - score: -3 ~ +5 (5 类信号累加)
          - verdict: 5 档 (进货/偏进货/中性/偏出货/出货)
          - signals: 命中的具体信号
          - source: "OBV 派生 (K线)" (因为 fflow 走的是 Tushare, OBV 走的是 K线)
          - asof: "YYYYMMDD" / "latest"
    """
    # asof 切片 (跟 fflow_factor 同样的处理)
    if asof and dates and len(dates) == len(closes):
        from tools.factors.utils import normalize_asof
        asof_norm = normalize_asof(asof)
        if asof_norm:
            cut = [i for i, d in enumerate(dates) if str(d).replace("-", "")[:8] <= asof_norm]
            if cut:
                last = cut[-1] + 1
                closes = closes[:last]
                vols   = vols[:last]
                dates  = dates[:last]

    # 数据不足: 至少要 2 根 K 线算 OBV, 20 根算 OBV MA20
    if not closes or len(closes) < 2:
        return {
            "score": 0, "verdict": "无数据", "signals": [],
            "source": "OBV 派生 (K线, 数据不足)", "asof": asof or "latest",
        }

    p = closes[-1]
    def ma(n): return sum(closes[-n:]) / n if len(closes) >= n else None
    m5, m20, m60, m120 = ma(5), ma(20), ma(60), ma(120)
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:   obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i-1]: obv.append(obv[-1] - vols[i])
        else:                          obv.append(obv[-1])
    obv_ma20  = sum(obv[-20:]) / 20 if len(obv) >= 20 else obv[-1]
    obv_trend = (obv[-1] - obv_ma20) / max(abs(obv_ma20), 1) if obv_ma20 else 0
    pct5  = (closes[-1] / closes[-6]  - 1) * 100 if len(closes) >= 6  else 0
    pct20 = (closes[-1] / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
    vr = vols[-1] / (sum(vols[-20:]) / 20) if len(vols) >= 20 else 1.0
    d120 = (p / m120 - 1) * 100 if m120 else 0

    signals = []; score = 0
    if d120 > 5:   signals.append(f"MA120偏{d120:.0f}%高位"); score -= 1
    elif d120 < -5: signals.append(f"MA120偏{d120:.0f}%低位蓄势"); score += 1
    if pct20 > 10 and obv_trend < -0.05:  signals.append("OBV背离:价涨OBV降→出货"); score -= 2
    elif pct20 < -5 and obv_trend > 0.05: signals.append("OBV底背离:价跌OBV升→吸筹"); score += 2
    if vr > 1.5 and pct5 > 2:   signals.append(f"放量上涨vol={vr:.2f}"); score += 1
    elif vr > 1.5 and pct5 < -2: signals.append(f"放量下跌vol={vr:.2f}"); score -= 1
    elif vr < 0.5 and pct5 > 3:  signals.append(f"缩量拉高vol={vr:.2f}出货嫌疑"); score -= 1
    elif vr < 0.7 and pct5 < -2: signals.append("缩量回调卖压轻"); score += 1
    if m60 and m120 and m60 > m120 and m5 and p < m5:
        signals.append("拉高出货型"); score -= 1
    elif m5 and m20 and m60 and m120 and p > m5 > m20 > m60 > m120:
        signals.append("多头排列"); score += 1

    if score >= 3:    verdict = "🟢主力进货"
    elif score >= 1:  verdict = "🟡偏进货"
    elif score == 0:  verdict = "⬜中性"
    elif score >= -2: verdict = "🟠偏出货"
    else:             verdict = "🔴主力出货"

    return {
        "score": score, "verdict": verdict, "signals": signals,
        "source": "OBV 派生 (K线)",
        "asof": asof or "latest",
    }

