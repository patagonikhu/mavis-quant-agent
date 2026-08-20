"""
macro/resonance.py - 多市场共振因子 (纯计算，无网络请求)

resonance_3period 由 dump_data.py 调用（拉网络数据层），结果存入 dump['resonance']。
ResonanceStrategy 从 ctx.resonance 读，不重拉。

输入: stock_code (供 dump_data 调用时拉网络)
输出: {period_days: {stock_ret, sector_ret, direction, strength, score, summary}}
"""
from typing import Dict, List, Optional


def _ret_pct(closes: List[float], days: int) -> float:
    """最近 N 日涨跌幅 %"""
    if not closes or len(closes) < days + 1:
        return 0.0
    return (closes[-1] / closes[-days-1] - 1) * 100


def _classify_resonance(s_ret: float, sec_ret: float, cn_ret: float, sh_ret: float) -> dict:
    """共振方向判定 (4 源: 个股/板块/创业板/沪深300)"""
    pos_count = sum(1 for v in [s_ret, sec_ret, cn_ret, sh_ret] if v > 0)
    neg_count = sum(1 for v in [s_ret, sec_ret, cn_ret, sh_ret] if v < 0)

    if pos_count == 4:
        direction, strength = "🟢四向↑", 4
    elif pos_count == 3 and neg_count == 1:
        direction, strength = "🟡三向↑ (1 分歧)", 3
    elif pos_count == 2 and neg_count == 2:
        direction, strength = "⬜分歧", 0
    elif pos_count == 1 and neg_count == 3:
        direction, strength = "🟠三向↓ (1 分歧)", -3
    elif neg_count == 4:
        direction, strength = "🔴四向↓", -4
    else:
        direction, strength = "⬜混合", 0

    return {
        "stock_ret":    s_ret,
        "sector_ret":   sec_ret,
        "chinext_ret":  cn_ret,
        "shanghai_ret": sh_ret,
        "rel_to_sector": s_ret - sec_ret,
        "direction": direction,
        "strength":  strength,
        "score":     strength,
    }


def resonance_3period(stock_code: str, sector_code: str = 'sz399808',
                      periods: tuple = (1, 5, 20)) -> Dict[str, dict]:
    """1 次拉指数 K 线，算 3 周期共振。

    由 dump_data.py 调用（属于数据拉取层，有网络请求）。
    结果存入 dump['resonance']，AnalysisEngine 通过 ctx.resonance 读取，不重算。
    """
    def _fetch(code: str, limit: int = 60) -> Optional[List[float]]:
        code_clean = code.replace('sh', '').replace('sz', '')
        try:
            from tools.fetch.tushare_fetcher import get_daily as _gd, get_index_daily as _gid
            is_index = code_clean in {
                "000300", "000688", "000001", "399006", "399808", "399001", "000016", "000905"
            }
            rows, _ = _gid(code_clean, limit=limit) if is_index else _gd(code_clean, limit=limit)
            return [float(r['close'] or 0) for r in rows] if rows else None
        except Exception:
            return None

    stock   = _fetch(stock_code, 60)
    sector  = _fetch(sector_code, 60) if sector_code else []
    chinext = _fetch('sz399006', 60)
    shanghai= _fetch('sh000300', 60)

    if not stock:
        return {p: {"error": f"无法获取 {stock_code} K 线", "score": 0, "direction": "未知"}
                for p in periods}

    result = {}
    for p in periods:
        s_ret   = _ret_pct(stock,    p)
        sec_ret = _ret_pct(sector,   p) if sector   else 0.0
        cn_ret  = _ret_pct(chinext,  p) if chinext  else 0.0
        sh_ret  = _ret_pct(shanghai, p) if shanghai else 0.0

        cls = _classify_resonance(s_ret, sec_ret, cn_ret, sh_ret)
        cls["summary"] = (
            f"个股{s_ret:+.1f}% / 板块{sec_ret:+.1f}% / "
            f"创业板{cn_ret:+.1f}% / 沪深300{sh_ret:+.1f}% / {cls['direction']} (p={p}d)"
        )
        if p == 5:
            cls["stock_ret_5d"]   = s_ret
            cls["sector_ret_5d"]  = sec_ret
            cls["chinext_ret_5d"] = cn_ret
            cls["shanghai_ret_5d"]= sh_ret
        result[p] = cls

    return result
