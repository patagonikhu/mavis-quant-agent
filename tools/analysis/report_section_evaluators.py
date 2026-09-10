"""
analysis_evaluators.py — 评估器统一接入 (v1.0, 2026-07-21)

目的: 把 app/signals/ app/analysis/ app/strategy/ 里现成的评估代码
      接入到 tools/ 流程, 让报告 22 section 全部有实数据 (不是占位符)

包含 4 个评估器:
  1. compute_fundamental_score()  - 4 维基本面 (估值/盈利/成长/安全)
  2. compute_volume_price_signals() - 4 个量价信号
  3. compute_strategy_signals()   - 4 套交易策略 (MACD/KDJ/MA/Boll)
  4. compute_signal_5cat()        - 5 类 14 子信号 (量价+龙头+资金+政策+情绪)

数据需求:
  - 个股 K 线 (250 条) — 全部需要
  - EPS 一致预期 (6 年) — fundamental 需要
  - 实时价 + PE_TTM — fundamental 需要
  - fflow (主力资金) — signal_5cat 资金类需要 (但 fflow 公开 API 无备源)
  - 板块成分股 K 线 — signal_5cat 龙头类需要 (暂未接入)

降级策略:
  - 缺数据时, 该子项返回 0/中性分, 不让整个 section 挂掉
  - 报告里明确标 "部分数据缺失, X 项 0 分" 让用户知道
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Optional
import yaml

# === 项目级配置加载 (2026-07-27 集中管理, 无默认值) ===
def _load_config() -> dict:
    config_path = Path(__file__).parent.parent.parent / "config" / "project.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ 找不到 {config_path}\n"
            f"   首次使用请: 手动创建 config/project.yaml (不在 git 里, 参考 git history 或 docs/AGENT_MEMORY.md)"
        )
    with open(config_path) as f:
        return yaml.safe_load(f)

_PROJECT_CFG = _load_config()


# ============================================================
# 2026-09-09 删: compute_fundamental_score (4 维基本面评估, 死代码)
# 原因: 4 维 (估值/盈利/成长/安全) 走 PEG/ROC/EY/行业, 已被 ValuationStrategy (PEG+DCF+Magic ROC/EY v6.2.5) 取代
# 决策走 缠论 1买/2买/3买/1卖/2卖/3卖 + 估值双指标 (PEG/DCF L)
# 报告 section 整个删掉, 函数也删 (原 200 行)
# ============================================================

# ============================================================
# 2. 4 个量价信号 (volume_breakout / limit_up_surge / volume_price_uptrend / breakout_resistance)
# ============================================================

def compute_volume_price_signals(kline: list[dict]) -> dict:
    """
    量价类 4 个子信号 (v6.2.8 重构: 删 weight/raw_score 加权汇总, 2026-09-09 再清)
    决策走 缠论 1买/2买/3买/1卖/2卖/3卖, 量价只是参考子信号.

    Returns:
        {
            "signals": [
                {"name": "volume_breakout", "score": 0-10, "triggered": bool, "reason": str},
                ...
            ],
        }
    """
    if len(kline) < 20:
        return {"error": "K线不足 20 条"}

    closes = [b["close"] for b in kline]
    vols = [b["vol"] for b in kline]
    highs = [b["high"] for b in kline]
    lows = [b["low"] for b in kline]

    signals = []

    # 1. volume_breakout: 当日量 > 20日均量 × 1.5 + 价涨
    vol_ma20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else vols[-1]
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 > 0 else 0
    price_change = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] > 0 else 0

    if vol_ratio > _PROJECT_CFG["thresholds"]["volume_ratio"]["strong_surge"] and price_change > 0:
        vb_score = 9
        vb_reason = f"放量 {vol_ratio:.1f}x + 涨 {price_change:+.1f}%"
    elif vol_ratio > _PROJECT_CFG["thresholds"]["volume_ratio"]["moderate_surge"] and price_change > 0:
        vb_score = 7
        vb_reason = f"温和放量 {vol_ratio:.1f}x + 涨 {price_change:+.1f}%"
    elif vol_ratio < _PROJECT_CFG["thresholds"]["volume_ratio"]["dry"]:
        vb_score = 3
        vb_reason = f"缩量 {vol_ratio:.1f}x, 观望"
    else:
        vb_score = 5
        vb_reason = f"量 {vol_ratio:.1f}x 价 {price_change:+.1f}%, 中性"
    signals.append({
        "name": "volume_breakout", "category": "量价", "score": vb_score,
        "triggered": vb_score >= 7, "reason": vb_reason,
    })

    # 2. limit_up_surge: 最近 20 日涨停数
    limit_ups = 0
    for i in range(-min(20, len(closes)), -1):
        if closes[i] > 0 and (closes[i] / closes[i-1] - 1) >= 0.095:  # A 股涨停 ~9.5%
            limit_ups += 1
    if limit_ups >= 3:
        lu_score = 9
    elif limit_ups >= 1:
        lu_score = 7
    else:
        lu_score = 3
    signals.append({
        "name": "limit_up_surge", "category": "量价", "score": lu_score,
        "triggered": lu_score >= 7, "reason": f"20日内涨停 {limit_ups} 次",
    })

    # 3. volume_price_uptrend: 3 日 close 涨 + vol 涨
    if len(closes) >= 4:
        c_trend = closes[-1] > closes[-2] > closes[-3] > closes[-4]
        v_trend = vols[-1] > vols[-2] > vols[-3] > vols[-4]
        if c_trend and v_trend:
            vp_score = 8
            vp_reason = "3日量价齐升"
        elif c_trend:
            vp_score = 5
            vp_reason = "3日价升量未升"
        elif v_trend:
            vp_score = 4
            vp_reason = "3日量升价未升"
        else:
            vp_score = 3
            vp_reason = "3日量价未齐升"
    else:
        vp_score = 3
        vp_reason = "K线 < 4 条"
    signals.append({
        "name": "volume_price_uptrend", "category": "量价", "score": vp_score,
        "triggered": vp_score >= 7, "reason": vp_reason,
    })

    # 4. breakout_resistance: 突破 60 日新高
    if len(closes) >= 60:
        recent_high = max(closes[-60:-1])
        if closes[-1] >= recent_high:
            br_score = 9
            br_reason = f"突破 60 日新高 ¥{recent_high:.0f}"
        elif closes[-1] >= recent_high * 0.97:
            br_score = 6
            br_reason = f"接近 60 日新高 (距 ¥{recent_high:.0f} {((closes[-1]/recent_high-1)*100):+.1f}%)"
        else:
            br_score = 3
            br_reason = f"距 60 日新高 {((closes[-1]/recent_high-1)*100):+.1f}%"
    else:
        br_score = 3
        br_reason = "K线 < 60"
    signals.append({
        "name": "breakout_resistance", "category": "量价", "score": br_score,
        "triggered": br_score >= 7, "reason": br_reason,
    })

    # 2026-09-09 删: 加权求和 (raw_score) + rating (强势/中等/偏弱/弱势)
    # 决策走 缠论, 量价只是参考子信号, 不再汇总成 0-30 分
    return {
        "signals": signals,
    }


# 2026-09-09 删: compute_strategy_signals (4 套交易策略, 死代码)
# 原因: 4 套 (MACD/KDJ/MA/BOLL) 老的简易指标投票, 已被 TechnicalStrategy (8 个技术指标 v6.2.8) 取代
# RenderData.strategy / _section_strategy / status 行 "策略" 全部删除 (走 缠论, 不再单独跑 4 套指标)

# ============================================================
# 4. 5 类 14 子信号 (板块级别, 简化为个股可用版本)

def compute_signal_5cat(
    kline: list[dict],
    fflow: list[dict] | None = None,
) -> dict:
    """
    5 类 14 子信号 (满分 123 分, 简化为个股可用版)

    完整 14 子信号 (5 大类):
      - 量价 (30): volume_breakout / limit_up_surge / volume_price_uptrend / breakout_resistance
      - 龙头 (25): leader_launching / sector_diffusion  (需板块数据, 简化跳过)
      - 资金 (20): main_capital_inflow / north_capital / institutional / etf_subscription
      - 政策 (15): policy_keyword_hit / llm_policy_score  (需新闻, 简化跳过)
      - 情绪 (10): hot_rank_surge / research_surge / discussion_anomaly  (需热度榜, 简化跳过)

    Returns:
        {
            "signals": [{name, cat, score, triggered, reason}, ...],  # 2026-09-09 删 weight
            "missing": [str],  # 哪些类数据缺失
            # 2026-09-09 删: raw_score + rating 加权汇总
        }
    """
    all_signals = []

    # === 1. 量价 (4 子信号, 30 分) — 个股 K 线可算 ===
    vp_result = compute_volume_price_signals(kline)
    if "signals" in vp_result:
        for sig in vp_result["signals"]:
            all_signals.append(sig)
    raw_vp = 0  # 2026-09-09 删: 不再加权汇总

    # === 2. 龙头 (2 子信号, 25 分) — 需板块成分股, 标 "数据缺失" ===
    all_signals.append({
        "name": "leader_launching", "category": "龙头", "score": 5,
        "triggered": False, "reason": "需板块成分股数据 (暂未接入)",
    })
    all_signals.append({
        "name": "sector_diffusion", "category": "龙头", "score": 5,
        "triggered": False, "reason": "需板块成分股数据 (暂未接入)",
    })
    raw_leader = 0  # 2026-09-09 删: 不再加权汇总

    # === 3. 资金 (4 子信号, 20 分) ===
    # main_capital_inflow: 5 日 fflow 净额
    if fflow and len(fflow) >= 5:
        recent_5 = fflow[-5:]
        total_main = sum(d.get("main_net", 0) for d in recent_5) / 1e4  # 万元 → 亿
        if total_main > 10:
            mc_score, mc_reason = 9, f"5日主力净流入 +{total_main:.1f}亿"
            mc_trig = True
        elif total_main > 3:
            mc_score, mc_reason = 7, f"5日主力净流入 +{total_main:.1f}亿"
            mc_trig = True
        elif total_main < -10:
            mc_score, mc_reason = 2, f"5日主力净流出 {total_main:.1f}亿"
            mc_trig = False
        elif total_main < -3:
            mc_score, mc_reason = 4, f"5日主力净流出 {total_main:.1f}亿"
            mc_trig = False
        else:
            mc_score, mc_reason = 5, f"5日主力中性 {total_main:+.1f}亿"
            mc_trig = False
    else:
        mc_score, mc_reason, mc_trig = 5, "fflow 数据缺失", False
    all_signals.append({
        "name": "main_capital_inflow", "category": "资金", "score": mc_score,
        "triggered": mc_trig, "reason": mc_reason,
    })

    # north_capital / institutional / etf_subscription: 需专项 API, 暂标记
    all_signals.append({
        "name": "north_capital_anomaly", "category": "资金", "score": 5,
        "triggered": False, "reason": "北向资金 API 暂未接入 (Tushare 10000 积分档, 后续按需)",
    })
    all_signals.append({
        "name": "institutional_concentration", "category": "资金", "score": 5,
        "triggered": False, "reason": "机构持仓 API 未接入",
    })
    all_signals.append({
        "name": "etf_subscription_surge", "category": "资金", "score": 5,
        "triggered": False, "reason": "ETF 申赎 API 未接入",
    })
    raw_capital = 0  # 2026-09-09 删: 不再加权汇总

    # === 4. 政策 (2 子信号, 15 分) ===
    all_signals.append({
        "name": "policy_keyword_hit", "category": "政策", "score": 5,
        "triggered": False, "reason": "政策新闻 API 未接入 (需 WebSearch)",
    })
    all_signals.append({
        "name": "llm_policy_score", "category": "政策", "score": 5,
        "triggered": False, "reason": "LLM 政策评分需 mavis 算",
    })
    raw_policy = 0  # 2026-09-09 删: 不再加权汇总

    # === 5. 情绪 (3 子信号, 10 分) ===
    all_signals.append({
        "name": "hot_rank_surge", "category": "情绪", "score": 5,
        "triggered": False, "reason": "热度榜 API 未接入",
    })
    all_signals.append({
        "name": "research_surge", "category": "情绪", "score": 5,
        "triggered": False, "reason": "研报数量 API 未接入",
    })
    all_signals.append({
        "name": "discussion_anomaly", "category": "情绪", "score": 5,
        "triggered": False, "reason": "讨论量 API 未接入",
    })
    raw_sentiment = 0  # 2026-09-09 删: 不再加权汇总

    # 2026-09-09 删: 总分 (raw_total) + 评级 (rating)
    # 决策走 缠论 1买/2买/3买/1卖/2卖/3卖, 子信号只是参考
    return {
        "signals": all_signals,
        "missing": ["龙头类", "政策类", "情绪类", "部分资金类"],
    }


# ============================================================
# 5. 止盈止损计算
# ============================================================

def compute_take_profit_stop_loss(
    current_price: float,
    kline: list[dict],
    cost_price: Optional[float] = None,
) -> dict:
    """
    止盈 3 层 + 止损 4 档。
    cost_price: 持仓成本，缺省时用当前价（假设今日买入）。
    """
    cost = cost_price if cost_price and cost_price > 0 else current_price
    p = current_price

    # ATR(14) — 止损宽度基准
    atr = None
    if len(kline) >= 15:
        closes = [b["close"] for b in kline]
        highs  = [b["high"]  for b in kline]
        lows   = [b["low"]   for b in kline]
        tr_list = [
            max(highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i]  - closes[i - 1]))
            for i in range(1, len(closes))
        ]
        atr_raw = sum(tr_list[:14]) / 14
        for tr in tr_list[14:]:
            atr_raw = (atr_raw * 13 + tr) / 14
        atr = round(atr_raw, 2)

    # 止盈 3 层（相对成本）
    t1 = round(cost * 1.20, 2)
    t2 = round(cost * 1.50, 2)
    t3 = round(cost * 2.00, 2)

    # 止损 4 档（相对成本）
    s1 = round(cost * 0.90, 2)   # -10%
    s2 = round(cost * 0.85, 2)   # -15%
    s3 = round(cost * 0.75, 2)   # -25%
    s4 = round(cost * 0.65, 2)   # -35%

    # ATR 止损（1.5× ATR，仅当 atr 可算）
    atr_stop = round(p - 1.5 * atr, 2) if atr else None

    # 当前状态（已涨多少）
    unrealized_pct = round((p / cost - 1) * 100, 1) if cost > 0 else 0

    return {
        "cost": cost,
        "current_price": p,
        "unrealized_pct": unrealized_pct,
        "atr": atr,
        "atr_stop": atr_stop,
        # 止盈
        "t1_price": t1, "t1_label": "+20%",
        "t2_price": t2, "t2_label": "+50%",
        "t3_price": t3, "t3_label": "+100%",
        # 止损
        "s1_price": s1, "s1_label": "-10% 检查基本面",
        "s2_price": s2, "s2_label": "-15% 卖 1/3",
        "s3_price": s3, "s3_label": "-25% 减半仓",
        "s4_price": s4, "s4_label": "-35% 清仓",
    }


# ============================================================
# 6. 板块过热预警
# ============================================================

# 板块名 → ETF 腾讯代码（用于拉 K 线）
SECTOR_ETF_MAP: dict[str, str] = {
    "光伏-逆变器+储能": "sh515790",   # 光伏ETF
    "光伏": "sh515790",
    "新能源": "sz159934",             # 新能源ETF
    "储能": "sh515220",               # 储能ETF
    "半导体设备": "sh588200",         # 科创芯片ETF
    "半导体": "sz159995",             # 芯片ETF
    "AI芯片": "sz159605",             # 人工智能ETF
    "AI服务器": "sz159605",
    "AI": "sz159605",
    "CPO": "sz159605",
    "PCB": "sz159516",                # 电子ETF
    "消费电子": "sh515880",           # 消费电子ETF
    "机器人": "sh563080",             # 机器人ETF
    "稀土永磁": "sz159715",           # 稀土ETF
    "光学": "sh515880",
    "封测": "sz159995",
    "电力变压器": "sh515220",
    "默认": "sh000300",               # 沪深300兜底
}


def compute_sector_overheat(sector: str, kline_fetcher=None) -> dict:
    """
    板块过热预警。

    2026-09-03 v6.1.1 改: 不再直连 tushare_fetcher.get_daily (违反 sync_data 唯一入口)
    - 必须传 kline_fetcher, 否则返 error
    - 数据从本地 parquet 读, 走 /t-sync-data 补
    """
    import statistics

    def _fetch_closes(symbol: str) -> list[float]:
        if kline_fetcher:
            return kline_fetcher(symbol)
        # v6.1.1 改: 不再 fallback 到网络, 强制要求 kline_fetcher 注入
        # 主路径走 render_data._compute_sector_overheat (本地 K 线), 不会调到这里
        return []

    # 匹配 ETF
    etf = SECTOR_ETF_MAP.get(sector)
    if not etf:
        # 模糊匹配
        for key, val in SECTOR_ETF_MAP.items():
            if key in sector or sector in key:
                etf = val
                break
        etf = etf or SECTOR_ETF_MAP["默认"]

    closes = _fetch_closes(etf)
    if len(closes) < 21:
        return {"error": f"K线不足({len(closes)}条)", "etf": etf}

    p = closes[-1]

    def ma(n):
        return statistics.mean(closes[-n:]) if len(closes) >= n else p

    ma20  = ma(20)
    ma120 = ma(120) if len(closes) >= 120 else None

    w1 = round((p / closes[-5]  - 1) * 100, 1) if len(closes) >= 5  else None
    m1 = round((p / closes[-21] - 1) * 100, 1) if len(closes) >= 21 else None
    m3 = round((p / closes[-63] - 1) * 100, 1) if len(closes) >= 63 else None

    d20  = round((p / ma20  - 1) * 100, 1)
    d120 = round((p / ma120 - 1) * 100, 1) if ma120 else None

    # 综合判断
    overheat = False
    warn = False
    if m1 is not None and m1 > 30:   overheat = True
    if d20 > 30:                       overheat = True
    if m3 is not None and m3 > 100:   overheat = True
    if m1 is not None and m1 > 20:   warn = True
    if d20 > 20:                       warn = True
    if d120 is not None and d120 > 50: warn = True

    verdict = "🔴 板块过热 必减仓1/3" if overheat else "🟠 板块偏热 关注" if warn else "✅ 安全"

    return {
        "etf": etf,
        "sector": sector,
        "price": p,
        "1w": w1,
        "1m": m1,
        "3m": m3,
        "ma20_dev": d20,
        "ma120_dev": d120,
        "verdict": verdict,
        "overheat": overheat,
        "warn": warn,
    }


# ============================================================
# 7. 缠论补充 (SMC + 量价 + 威科夫)
# ============================================================

def compute_supplement(kline: list[dict]) -> dict:
    """
    缠论补充 4 方法：SMC-OB/BOS、量价综合、多市场共振(简化)、威科夫阶段。
    全部从个股 K 线派生，不需要额外 API。
    """
    import statistics

    if len(kline) < 30:
        return {"error": "K线不足30条"}

    closes = [b["close"] for b in kline]
    opens  = [b["open"]  for b in kline]
    highs  = [b["high"]  for b in kline]
    lows   = [b["low"]   for b in kline]
    vols   = [b["vol"]   for b in kline]
    p = closes[-1]

    # ── 1. SMC：BOS/CHoCH ──
    window = 5
    n = len(closes)
    swing_highs = [i for i in range(window, n - window)
                   if highs[i] == max(highs[i - window: i + window + 1])]
    swing_lows  = [i for i in range(window, n - window)
                   if lows[i]  == min(lows[i - window: i + window + 1])]

    smc_signals = []
    if len(swing_highs) >= 2:
        sh1, sh2 = swing_highs[-2], swing_highs[-1]
        if highs[sh2] > highs[sh1]:
            smc_signals.append(f"BOS↑ 新高¥{highs[sh2]:.2f}>前高¥{highs[sh1]:.2f}(趋势延续)")
        else:
            smc_signals.append(f"CHoCH↓ 高点降低¥{highs[sh2]:.2f}<¥{highs[sh1]:.2f}(趋势转弱)")
    if len(swing_lows) >= 2:
        sl1, sl2 = swing_lows[-2], swing_lows[-1]
        if lows[sl2] < lows[sl1]:
            smc_signals.append(f"BOS↓ 新低¥{lows[sl2]:.2f}<前低¥{lows[sl1]:.2f}(下跌延续)")
        else:
            smc_signals.append(f"CHoCH↑ 低点抬高¥{lows[sl2]:.2f}>¥{lows[sl1]:.2f}(趋势转强)")

    # 最近看涨 OB（下跌段最后阴线，之后突破）
    bull_ob = None
    lookback = min(50, n)
    for i in range(1, lookback - 2):
        if closes[i] < opens[i]:  # 阴线
            if any(highs[j] > highs[i] for j in range(i + 1, min(i + 10, lookback))):
                if lows[i] < p < highs[i] * 1.05:
                    bull_ob = (round(lows[i], 2), round(highs[i], 2))

    # ── 2. 量价综合 ──
    vol_ma20 = statistics.mean(vols[-20:]) if len(vols) >= 20 else vols[-1]
    vr = vols[-1] / vol_ma20 if vol_ma20 > 0 else 1.0
    pct5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0

    # OBV
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:   obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i - 1]: obv.append(obv[-1] - vols[i])
        else:                            obv.append(obv[-1])
    obv_ma5 = statistics.mean(obv[-5:])
    obv_trend = "↑" if obv[-1] > obv_ma5 * 1.01 else ("↓" if obv[-1] < obv_ma5 * 0.99 else "→")

    vp_signals = []
    if vr > 1.5 and pct5 > 2:    vp_signals.append(f"✅ 放量上涨({vr:.1f}x) 真实上涨")
    elif vr > 1.5 and pct5 < -2: vp_signals.append(f"⚠️ 放量下跌({vr:.1f}x) 恐慌出货")
    elif vr < 0.7 and pct5 > 3:  vp_signals.append(f"⚠️ 缩量拉高({vr:.1f}x) 出货嫌疑")
    elif vr < 0.7 and pct5 < -2: vp_signals.append(f"✅ 缩量回调({vr:.1f}x) 卖压轻")
    if pct5 > 5 and obv_trend == "↓":  vp_signals.append("🔴 OBV顶背离: 价涨OBV下行 主力出货")
    elif pct5 < -5 and obv_trend == "↑": vp_signals.append("🟢 OBV底背离: 价跌OBV上行 主力吸筹")

    # ── 3. 威科夫阶段 ──
    nb = min(60, n)
    hi60 = max(highs[-nb:])
    lo60 = min(lows[-nb:])
    pos_pct = round((p - lo60) / (hi60 - lo60) * 100, 0) if hi60 > lo60 else 50
    vr_recent = statistics.mean(vols[-5:]) / statistics.mean(vols[-20:]) if len(vols) >= 20 else 1
    slope = ((statistics.mean(closes[-20:]) / statistics.mean(closes[-40:-20])) - 1) * 100 if len(closes) >= 40 else 0

    if pos_pct > 70 and vr_recent > 1.2 and slope > 3:
        wyckoff = f"E主升浪 (位置{pos_pct:.0f}%↑ 放量{vr_recent:.1f}x 斜率+{slope:.1f}%)"
    elif pos_pct > 50 and slope > 2:
        wyckoff = f"D突破 (位置{pos_pct:.0f}% 斜率+{slope:.1f}%)"
    elif pos_pct < 20 and vr_recent < 0.7:
        wyckoff = f"C弹簧候选 (位置{pos_pct:.0f}%底部 缩量{vr_recent:.1f}x)"
    elif pos_pct < 30 and vr_recent > 1.5:
        wyckoff = f"A卖出高潮 (位置{pos_pct:.0f}% 放量{vr_recent:.1f}x 可能底部)"
    else:
        wyckoff = f"B横盘积累 (位置{pos_pct:.0f}% 量比{vr_recent:.1f}x)"

    return {
        "smc_signals": smc_signals,
        "bull_ob": bull_ob,
        "vol_ratio": round(vr, 2),
        "obv_trend": obv_trend,
        "vp_signals": vp_signals,
        "wyckoff": wyckoff,
        "pos_pct_60d": pos_pct,
    }

