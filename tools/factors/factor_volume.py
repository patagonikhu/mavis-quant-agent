"""
tools/factors/factor_volume.py — 量能 factor 库 (纯函数, 2026-09-09 统一)

公开 API (1 个):
  - compute_fflow_factor  fflow 主力资金流因子 (Tushare money_flow)

2026-10-27: OBV 因子已删除.
  实测 12437 个采样点 (120 只票, 30 日前瞻): OBV 信号普遍负超额
  (多头排列 -2.38%, 底背离 -1.60%, 多头排列胜率仅 40.9%).
  剥掉藏在 OBV 里的"离 MA120 距离"成分后, OBV 信号 = 基准 3.02% vs 3.09%, 胜率 48.5% vs 50.1%,
  即比随机还差. 详见 docs/fflow-removal-assessment.md.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def compute_fflow_factor(code: str, moneyflow_list=None, dates=None, asof=None,
                         stale_threshold_days: int = 7) -> dict:
    """fflow 主力资金流因子 — 纯计算, 无网络

    输入: Tushare money_flow (dump 预拉)
    输出: 5 档判定 + 3/5/10/20/30/60 日主力累计 + 趋势标记 + 数据新鲜度状态

    ⚠️ 2026-10-27 加 (数据新鲜度):
      fflow parquet 靠 /t-sync-data 落盘, 落后 K 线是常态 (实测曾落后 28 天).
      旧数据算出的"主力出货"会被当成当前信号用 — 实测 38 只核心票里
      14 只 fflow 判出货, 其中 13 只被同时点 (K线派生, 数据新鲜) 的量价指标判为偏进货,
      方向完全相反. 这是数据陈旧造成的系统性看空偏差, 不是市场信号.

      现在额外返回:
        - data_date:  fflow 数据最新交易日 (YYYYMMDD)
        - stale_days: 相对 asof (或今天) 落后多少天
        - status:     "OK" / "STALE" / "NO_DATA" / "NOT_APPLICABLE_INDEX"
      STALE 的数据 score 仍照算 (保留原值供追溯), 但 verdict 加 "⚠️数据陈旧" 前缀,
      避免被误读成当前主力动向.

    Args:
        code: 股票代码 (签名占位, 不参与计算)
        moneyflow_list: dump['tushare']['money_flow'] 列表, 每条含 trade_date/date + main_yi/net_mf_amount
        dates: 跟 moneyflow 对齐的日期列表 (YYYYMMDD), 用于 asof 切片 (可不传, 跟 moneyflow 自带 trade_date 等价)
        asof: 'YYYY-MM-DD' / 'YYYYMMDD' — 算到该日期及之前的状态 (None = 最新)
        stale_threshold_days: 落后多少天算 STALE (默认 7 天)

    Returns:
        dict:
          - score: -2 ~ +2
          - verdict: "🟢主力进货" / "🟡偏进货" / "⬜中性" / "🟠偏出货" / "🔴主力出货" [+ 短线/中线矛盾标记] [+ 数据陈旧标记]
          - signals: 列表, 各周期累计金额字符串
          - source: "Tushare.money_flow (dump 预拉)" / "无数据"
          - fflow_net_3d / 5d / 10d / 20d / 30d / 60d: 主力净流入累计 (亿元)
          - trend_3d / 10d / 30d: "↑进货" / "↓出货"
          - raw_fflow: 最近 10 天数据
          - asof: "YYYYMMDD" / "latest"
          - data_date / stale_days / status: 数据新鲜度
    """
    data = moneyflow_list or []

    # 指数: Tushare.moneyflow 只覆盖个股, 指数没有主力资金流 (NOT_APPLICABLE, 不是 NO_DATA)
    from ..storage.store import INDEX_CODES
    bare = str(code or "").split(".")[0].strip()
    if bare and any(str(i).split(".")[0] == bare for i in INDEX_CODES):
        return {
            "score": 0, "verdict": "N/A 指数无主力资金流", "signals": [], "source": "无数据 (指数)",
            "fflow_net_3d": 0, "fflow_net_5d": 0, "fflow_net_10d": 0,
            "fflow_net_20d": 0, "fflow_net_30d": 0, "fflow_net_60d": 0,
            "trend_3d": "?", "trend_10d": "?", "trend_30d": "?", "raw_fflow": [],
            "asof": asof or "latest",
            "data_date": None, "stale_days": None, "status": "NOT_APPLICABLE_INDEX",
        }

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
            "data_date": None, "stale_days": None, "status": "NO_DATA",
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

    # === 数据新鲜度 (2026-10-27 加) ===
    data_date = str(data[-1].get("trade_date", data[-1].get("date", ""))).replace("-", "")[:8]
    ref_date = (asof or "").replace("-", "")[:8] if asof else ""
    if not ref_date:
        # 无 asof: 跟今天比, 衡量 fflow parquet 落后 K 线多少天
        from datetime import datetime
        ref_date = datetime.now().strftime("%Y%m%d")
    stale_days = None
    try:
        from datetime import datetime
        _gap = (datetime.strptime(ref_date, "%Y%m%d")
                - datetime.strptime(data_date, "%Y%m%d")).days
        stale_days = _gap
    except Exception:
        stale_days = None

    is_stale = stale_days is not None and stale_days > stale_threshold_days
    status = "STALE" if is_stale else "OK"
    stale_mark = "" if not is_stale else f" \u26a0\ufe0f\u6570\u636e\u9648\u65e7({stale_days}\u5929)"

    return {
        "score": score,
        "verdict": verdict + conflict + stale_mark,
        "signals": [
            f"3\u65e5\u4e3b\u529b: {net_3:+.2f}\u4ebf ({trend_3d})",
            f"5\u65e5\u4e3b\u529b: {net_5:+.2f}\u4ebf",
            f"10\u65e5\u4e3b\u529b: {net_10:+.2f}\u4ebf ({trend_10d})",
            f"20\u65e5\u4e3b\u529b: {net_20:+.2f}\u4ebf",
            f"30\u65e5\u4e3b\u529b: {net_30:+.2f}\u4ebf",
            f"60\u65e5\u4e3b\u529b: {net_60:+.2f}\u4ebf",
            f"\u6700\u8fd1\u65e5: {data[-1].get('trade_date', data[-1].get('date', ''))} {_net(data[-1]):+.2f}\u4ebf",
            f"\u6570\u636e\u65e5\u671f: {data_date}" + (f" (\u843d\u540e {stale_days} \u5929)" if stale_days is not None else ""),
        ],
        "source": "Tushare.money_flow (dump \u9884\u62c9)",
        "fflow_net_3d": net_3, "fflow_net_5d": net_5,
        "fflow_net_10d": net_10, "fflow_net_20d": net_20,
        "fflow_net_30d": net_30, "fflow_net_60d": net_60,
        "trend_3d": trend_3d, "trend_10d": trend_10d, "trend_30d": trend_30d,
        "raw_fflow": data[-10:],
        "asof": asof or "latest",
        "data_date": data_date, "stale_days": stale_days, "status": status,
    }
