"""
magic_formula.py — Joel Greenblatt Magic Formula (ROC + EY)

公式 (来自 The Little Book That Beats the Market):
  ROC = EBIT / (净营运资本 + 固定资产)         ← 高 = 好公司
  EY  = EBIT / EV                              ← 高 = 便宜股
        EV = 市值 + 带息债务 - 现金
        (现金: A股简化为"货币资金", 这里用 netdebt 近似)

数据源 (data/history/financials/{YYYYQN}.parquet):
  ebit, fixed_assets, networking_capital, interestdebt, netdebt, eps_period

市值:  从 DataStore.get_daily_basic 拿 total_mv (亿)  ← 单只实时
行业过滤 (算 ROC/EY 时跳过):
  - 银行 / 保险 / 券商 / 房地产 / 公用事业 → N/A
  - 净利润负数 / 关键字段 NaN → N/A
  - 上市不足 1 年 → N/A (资本数据不稳)

用法:
    from tools.factors.valuation.magic_formula import calc_roc, calc_ey, calc_magic_score

    roc = calc_roc(code)                          # 单只, 返 ROC% 或 None
    ey  = calc_ey(code, market_cap=1.6e8)          # 单只, 返 EY% 或 None, market_cap 单位"万"
    score = calc_magic_score(code, market_cap=1.6e8)  # 综合

参考:
  Greenblatt, J. (2005). The Little Book That Beats the Market.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional


# 行业过滤: 这些行业的 ROC / EY 失真, 不算
EXCLUDED_INDUSTRIES = {
    "银行", "保险", "证券", "信托", "期货", "租赁",  # 金融
    "房地产", "物业管理", "园区开发",               # 地产
    "电力", "水务", "燃气", "热力", "环保",          # 公用
    "多元金融",
}


# ============================================================
# 单指标计算
# ============================================================

def calc_roc(code: str, financials: list[dict] | None = None) -> Optional[float]:
    """单只 ROC (Return on Capital, %)

    ROC = EBIT / (净营运资本 + 固定资产) × 100

    Args:
        code:        6 位股票代码
        financials:  可选, 传了就不查 DataStore (用于测试/批量)
                     格式跟 DataStore.get_financials 返回一致

    Returns:
        ROC 百分比 (e.g. 73.5 表示 73.5%), 行业失真 / 数据缺返 None
    """
    if financials is None:
        from tools.kline_store import DataStore
        financials = DataStore.get_financials(code, lookback_quarters=4)

    if not financials:
        return None

    # 用最新 1 季 (财务是定值, 不需要 TTM 算, 最新季代表当前)
    latest = financials[-1]

    # 行业过滤
    industry = (latest.get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return None

    # 拿关键字段
    ebit = latest.get("ebit")
    nwc  = latest.get("networking_capital")
    fa   = latest.get("fixed_assets")

    # NaN / 0 / 负数检查
    if ebit is None or nwc is None or fa is None:
        return None
    if ebit <= 0:        # 亏损公司 ROC 无意义
        return None
    nwc_plus_fa = nwc + fa
    if nwc_plus_fa <= 0:  # 分母 ≤ 0 (财务异常)
        return None

    return round(ebit / nwc_plus_fa * 100, 1)


def calc_ey(code: str, market_cap: float,
            financials: list[dict] | None = None) -> Optional[float]:
    """单只 EY (Earnings Yield, %)

    EY = EBIT / EV × 100
    EV = 市值 + 带息债务 - 净债务
        (净债务 ≈ 货币资金, Tushare netdebt 字段直接给, 单位元)

    单位换算 (Tushare daily_basic.total_mv 是"万元", fina_indicator 字段是"元"):
      total_mv (万元) / 1e4 → 亿元
      ebit    (元)     / 1e8 → 亿元
      netdebt (元)     / 1e8 → 亿元

    Args:
        code:        6 位股票代码
        market_cap:  总市值 (万元), 调方传 DataStore.get_daily_basic(code)['total_mv']
                    内部自动 / 1e4 转亿
        financials:  可选, 传了就不查 DataStore

    Returns:
        EY 百分比, 数据缺 / 行业失真返 None
    """
    if financials is None:
        from tools.kline_store import DataStore
        financials = DataStore.get_financials(code, lookback_quarters=4)

    if not financials:
        return None

    latest = financials[-1]
    industry = (latest.get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return None

    ebit    = latest.get("ebit")
    netdebt = latest.get("netdebt")  # 元

    if ebit is None or netdebt is None or market_cap is None or market_cap <= 0:
        return None
    if ebit <= 0:
        return None

    # EV (亿) = 市值 (万 → 亿) + 净债务 (元 → 亿)
    market_cap_yi = market_cap / 1e4
    netdebt_yi    = netdebt / 1e8
    ev_yi = market_cap_yi + netdebt_yi
    if ev_yi <= 0:
        return None

    return round(ebit / 1e8 / ev_yi * 100, 1)


def calc_magic_score(code: str, market_cap: float) -> dict:
    """单只 Magic Formula 综合评分 (dict)

    返回字段:
      - roc:        ROC % 或 None
      - ey:         EY % 或 None
      - industry:   行业名
      - skip_reason: 跳过原因 (None / "industry_excluded" / "no_data")
      - ev_yi:      企业价值 (亿) 或 None (EY 分母)

    排名 / 综合分 留给 batch 调用方 (sort by roc, sort by ey)
    """
    from tools.kline_store import DataStore
    financials = DataStore.get_financials(code, lookback_quarters=4)
    if not financials:
        return {
            "roc": None, "ey": None, "industry": "",
            "skip_reason": "no_data", "ev_yi": None,
        }

    industry = (financials[-1].get("industry") or "").strip()
    if industry in EXCLUDED_INDUSTRIES:
        return {
            "roc": None, "ey": None, "industry": industry,
            "skip_reason": "industry_excluded", "ev_yi": None,
        }

    roc = calc_roc(code, financials)
    ey  = calc_ey(code, market_cap, financials)

    # 算 EV 给上层 (后续算 L/E3 ratio 用), 单位统一到"亿"
    netdebt = financials[-1].get("netdebt") or 0
    if market_cap and market_cap > 0:
        ev_yi = market_cap / 1e4 + netdebt / 1e8
    else:
        ev_yi = None

    return {
        "roc": roc,
        "ey": ey,
        "industry": industry,
        "skip_reason": None if (roc is not None or ey is not None) else "no_data",
        "ev_yi": ev_yi,
    }


# ============================================================
# 批量 (watchlist / 板块)
# ============================================================

def batch_magic_scores(codes: list[str], with_market_cap: bool = True) -> list[dict]:
    """批量算 Magic Formula 评分

    Args:
        codes: 票列表
        with_market_cap: True (从 DataStore.get_daily_basic 拿市值),
                          False (只算 ROC, 跳过 EY)

    Returns:
        list[dict], 每只一个结果:
          {code, name, roc, ey, industry, skip_reason, ev_yi, market_cap_yi}
    """
    from tools.kline_store import DataStore
    results = []
    for code in codes:
        # 拿 name
        sb = DataStore.get_stock_basic(code)
        name = sb.get("name", code) if sb else code

        # 拿市值 (optional, 单位"万" 跟 Tushare daily_basic 一致)
        mc = None
        if with_market_cap:
            db = DataStore.get_daily_basic(code)
            mc = db.get("total_mv") if db else None

        score = calc_magic_score(code, mc)
        results.append({
            "code": code,
            "name": name,
            "market_cap": mc,  # 万元
            **score,
        })
    return results


# ============================================================
# 自测
# ============================================================

if __name__ == "__main__":
    # 5 只测试票 (跟之前 ROC 验证那批一致)
    test_codes = ["600519", "300274", "688981", "688041", "002475", "002747"]

    print("=== Magic Formula (ROC + EY) 自测 ===\n")
    results = batch_magic_scores(test_codes)

    # 排名: ROC 降序
    results_sorted = sorted(
        [r for r in results if r["roc"] is not None],
        key=lambda x: x["roc"],
        reverse=True,
    )

    print(f"{'代码':<8} {'名称':<14} {'行业':<10} {'ROC%':>7} {'EY%':>7} {'EV(亿)':>9}  备注")
    print("-" * 80)
    for r in results:
        roc_s = f"{r['roc']:.1f}" if r["roc"] is not None else "—"
        ey_s  = f"{r['ey']:.1f}" if r["ey"] is not None else "—"
        ev_s  = f"{r['ev_yi']:.1f}" if r["ev_yi"] is not None else "—"
        note  = r.get("skip_reason") or ""
        print(f"{r['code']:<8} {r['name']:<14} {r['industry']:<10} "
              f"{roc_s:>7} {ey_s:>7} {ev_s:>9}  {note}")

    print(f"\n按 ROC 排名 (前 {min(3, len(results_sorted))}):")
    for i, r in enumerate(results_sorted[:3], 1):
        print(f"  #{i} {r['code']} {r['name']} ROC={r['roc']}%")
