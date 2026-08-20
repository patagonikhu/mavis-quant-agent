"""
smc/analysis.py - SMC 综合分析主入口

从 tools/smc.py smc_analysis 搬过来 (跟原版 1:1)
"""
from typing import List, Dict
from .atr import calc_atr
from .order_blocks import find_order_blocks
from .fvg import find_fvg
from .swings_sweeps import find_liquidity_sweeps


def smc_analysis(opens: List[float], highs: List[float], lows: List[float],
                 closes: List[float], dates: List[str], vols: List[float],
                 current_price: float, lookback: int = 120,
                 displacement_atr_mult: float = None,
                 max_ob_age_bars: int = 80) -> Dict:
    """
    SMC 综合分析 (主入口)

    修复 (2026-08-15 v2):
      1. lookback 默认 50 → 120 (日线级)。50 根在 250 根 dump 里只覆盖 2-3 个月,
         找到的 OB 往往离现价 < 10% 或 > 30%, 实战无意义。
         推荐配置:
           - 日线: 120 根 (~半年)
           - 60分: 200 根 (~1 个月)
           - 周线: 200 根 (3 年)
      2. displacement_atr_mult 改为**自适应**: 取前 50 根 displacement 累计值的 70% 分位
         作为本股阈值。理由: 0.8x ATR 在高波动股(寒武纪日 ATR~¥80)几乎总满足,
         等于无过滤; 1.5x ATR 在低波动股(银行)又过严触发不了。
         仍然可以传固定值覆盖 (例如回测用固定 1.0)。
      3. max_ob_age_bars 60 → 80 (跟 lookback 120 配套)

    历史修复 (2026-08-03):
      - FVG 过滤回补 100% 的（原来 100% 填满的缺口仍然显示）

    4 个核心要素:
      1. Order Block: 主力最后反向 K + 自适应 displacement 阈值 × ATR
      2. FVG: 3 根 K 线 + 0.2 × ATR 过滤 + mitigation_pct < 100%
      3. Liquidity Sweep: swing 穿越 + 15 根 lookback
      4. ATR: 14 根 TR 平均
    """
    # === 自适应 displacement 阈值 (2026-08-15) ===
    # 思路: 统计最近 50 根 K 线的 displacement 幅度分布, 取 70% 分位
    # 这样高波动股自然有更高阈值(避免假 OB), 低波动股自动放宽(不漏 OB)
    if displacement_atr_mult is None:
        from .atr import calc_atr
        atr = calc_atr(highs, lows, closes) or 0
        n_pre = min(50, len(closes) - 1)
        if atr > 0 and n_pre >= 5:
            # 计算最近 n_pre 根每根的 abs(close - open) / ATR, 反映本股典型单根振幅
            bar_amp = [abs(closes[i] - opens[i]) / atr for i in range(-n_pre, 0)
                       if atr > 0]
            if bar_amp:
                # 70% 分位: 比典型单根振幅略大, 抓"主力主动买入/卖出"的异动
                bar_amp.sort()
                idx = int(len(bar_amp) * 0.7)
                displacement_atr_mult = max(0.8, min(2.5, bar_amp[idx]))
            else:
                displacement_atr_mult = 1.0
        else:
            displacement_atr_mult = 1.0

    obs = find_order_blocks(opens, highs, lows, closes, dates, lookback,
                            displacement_atr_mult=displacement_atr_mult)
    fvgs = find_fvg(opens, highs, lows, closes, dates, lookback)
    sweeps = find_liquidity_sweeps(opens, highs, lows, closes, dates, lookback=30)

    # 过滤太老的 OB
    bull_obs = [ob for ob in obs['bull'] if ob.get('age_bars', 0) <= max_ob_age_bars]
    bear_obs = [ob for ob in obs['bear'] if ob.get('age_bars', 0) <= max_ob_age_bars]

    # 找最近的看涨 OB (当前价下方, 距当前价最近)
    bull_obs_below = [ob for ob in bull_obs if ob['bottom'] < current_price]
    nearest_bull_ob = max(bull_obs_below, key=lambda ob: ob['bottom']) if bull_obs_below else None

    # 找最近的看跌 OB (当前价上方)
    bear_obs_above = [ob for ob in bear_obs if ob['top'] > current_price]
    nearest_bear_ob = min(bear_obs_above, key=lambda ob: ob['top']) if bear_obs_above else None

    # 最近看涨 FVG — 过滤回补 100% 的（完全填满已失效）
    fvgs_bull = [f for f in fvgs
                 if f['type'] == 'bull'
                 and f['top'] < current_price
                 and f.get('mitigation_pct', 0) < 100]
    nearest_fvg_bull = max(fvgs_bull, key=lambda f: f['top']) if fvgs_bull else None

    fvgs_bear = [f for f in fvgs
                 if f['type'] == 'bear'
                 and f['bottom'] > current_price
                 and f.get('mitigation_pct', 0) < 100]
    nearest_fvg_bear = min(fvgs_bear, key=lambda f: f['bottom']) if fvgs_bear else None

    # 摘要
    parts = []
    if nearest_bull_ob:
        ob = nearest_bull_ob
        age = ob.get('age_bars', 0)
        parts.append(f"OB支¥{ob['bottom']:.2f}~¥{ob['top']:.2f} {ob['displacement_atr']:.1f}×ATR {age}天前")
    if nearest_bear_ob:
        ob = nearest_bear_ob
        age = ob.get('age_bars', 0)
        parts.append(f"OB压¥{ob['bottom']:.2f}~¥{ob['top']:.2f} {ob['displacement_atr']:.1f}×ATR {age}天前")
    if nearest_fvg_bull:
        fvg = nearest_fvg_bull
        mit = fvg.get('mitigation_pct', 0)
        parts.append(f"FVG支¥{fvg['bottom']:.2f}~¥{fvg['top']:.2f} 回补{mit:.0f}%")
    if nearest_fvg_bear:
        fvg = nearest_fvg_bear
        mit = fvg.get('mitigation_pct', 0)
        parts.append(f"FVG压¥{fvg['bottom']:.2f}~¥{fvg['top']:.2f} 回补{mit:.0f}%")
    if sweeps:
        last_sweep = sweeps[0]
        type_zh = "买侧扫" if last_sweep['type'] == "buy_side_sweep" else "卖侧扫"
        wick = last_sweep.get('wick_size', 0)
        parts.append(f"{type_zh} {last_sweep['date']} ¥{last_sweep['swept_level']:.2f} 影线¥{wick:.2f}")
    summary = ' / '.join(parts) if parts else '无明显 SMC 信号'

    return {
        "nearest_bull_ob": nearest_bull_ob,
        "nearest_bear_ob": nearest_bear_ob,
        "nearest_fvg_bull": nearest_fvg_bull,
        "nearest_fvg_bear": nearest_fvg_bear,
        "recent_sweeps": sweeps,
        "summary": summary,
        "total_obs": len(bull_obs) + len(bear_obs),
        "total_fvgs": len(fvgs),
    }
