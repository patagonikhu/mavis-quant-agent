"""
机械业务规则 (跟 strategy 物理隔离)

这里装的是 Mavis 团队定的"业务阈值/比例" — 不是技术信号, 不属于 strategy 层.
- 止盈/止损百分比 (5%/10%/20% / -3%/-5%/-8%/-10%)
- MA 均线 SMA
- 后续: 仓位比例 / 风险阈值 / 复盘规则 都可以放这里

调用方: render_data.py::_compute_phase2
"""

from typing import Optional


def compute_fundamental_4d(val: dict, industry: str = "未知") -> dict:
    """基本面 4 维评分: 估值 / 盈利 / 增长 / 安全

    业务规则 (Mavis 投资纪律):
    - 估值 (PEG): <1.0=75 / <1.5=50 / >=1.5=25
    - 盈利 (ROC): >25%=75 / >10%=50 / <=10%=25
    - 增长 (EY): >8%=75 / >5%=50 / <=5%=25
    - 安全 (行业): 50 占位, 等 LLM 细化

    Args:
        val: FinanceStrategy 输出, 含 PEG_真实/roc/ey 字段
        industry: 行业名, 写入 safety 维度

    Returns:
        dict 含 valuation/profitability/growth/safety (各 {score, comment}) + total_score + dims
    """
    dims = {}
    # 1) 估值 (PEG)
    peg = val.get("PEG_真实")
    if isinstance(peg, (int, float)) and peg < 1.0:
        dims["valuation"] = {"score": 75, "comment": f"✅ PEG {peg} (Lynch 买入区)"}
    elif isinstance(peg, (int, float)) and peg < 1.5:
        dims["valuation"] = {"score": 50, "comment": f"🟡 PEG {peg} (合理)"}
    else:
        dims["valuation"] = {"score": 25, "comment": f"🟠 PEG {peg or '—'} (偏贵)"}
    # 2) 盈利 (ROC)
    roc = val.get("roc")
    if isinstance(roc, (int, float)) and roc > 25:
        dims["profitability"] = {"score": 75, "comment": f"✅ ROC {roc}% (>25% 优秀)"}
    elif isinstance(roc, (int, float)) and roc > 10:
        dims["profitability"] = {"score": 50, "comment": f"🟡 ROC {roc}%"}
    else:
        dims["profitability"] = {"score": 25, "comment": f"⚠️ ROC {roc or '—'}"}
    # 3) 增长 (EY)
    ey = val.get("ey")
    if isinstance(ey, (int, float)) and ey > 8:
        dims["growth"] = {"score": 75, "comment": f"✅ EY {ey}% (>8% 便宜)"}
    elif isinstance(ey, (int, float)) and ey > 5:
        dims["growth"] = {"score": 50, "comment": f"🟡 EY {ey}%"}
    else:
        dims["growth"] = {"score": 25, "comment": f"⚠️ EY {ey or '—'}"}
    # 4) 安全 (行业)
    dims["safety"] = {"score": 50, "comment": f"⚠️ 行业 {industry} (待 LLM 细化)"}
    # 总分 (4 维平均)
    total = sum(d["score"] for d in dims.values()) // 4
    return {
        "valuation": dims["valuation"],
        "profitability": dims["profitability"],
        "growth": dims["growth"],
        "safety": dims["safety"],
        "total_score": total,
        "dims": dims,
    }


def compute_stop_pl(kline: list, current_price: Optional[float] = None) -> dict:
    """止盈 3 层 + 止损 4 档 (基于 MA20/MA60 + 当前价)

    业务规则 (Mavis 投资纪律):
    - 止盈: 5% / 10% / 20% (锁利 1/3 + 显著利润 1/3 + 清仓)
    - 止损: -3% / -5% / -8% / -10% (检查基本面 / 卖 1/3 / 减半 / 清仓)

    Args:
        kline: K 线 list[dict], 每条含 "close" 字段
        current_price: 当前价 (默认用 K 线末根收盘)

    Returns:
        dict 含 tp1/tp2/tp3/s1/s2/s3/s4 价位 + 止盈 3 层 + 止损 4 档 (含操作建议)
    """
    if not kline or len(kline) < 60:
        return {}
    closes = [k["close"] for k in kline if "close" in k]
    if not closes:
        return {}
    current = current_price if current_price else closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = sum(closes[-60:]) / 60

    # 止盈 3 层
    tp1 = round(current * 1.05, 2)
    tp2 = round(current * 1.10, 2)
    tp3 = round(current * 1.20, 2)
    # 止损 4 档
    sl1 = round(current * 0.97, 2)
    sl2 = round(current * 0.95, 2)
    sl3 = round(current * 0.92, 2)
    sl4 = round(current * 0.90, 2)

    return {
        "current_price": current,
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        # 止盈 (Renderer 旧字段名)
        "tp1_price": tp1, "tp2_price": tp2, "tp3_price": tp3,
        "止盈3层": [
            {"档位": "1档 (+5%)", "价位": tp1, "建议操作": "卖 1/3 锁利"},
            {"档位": "2档 (+10%)", "价位": tp2, "建议操作": "卖 1/3 显著利润"},
            {"档位": "3档 (+20%)", "价位": tp3, "建议操作": "清仓"},
        ],
        # 止损 (Renderer 旧字段名 s1_price/s2_price/...)
        "s1_price": sl1, "s2_price": sl2, "s3_price": sl3, "s4_price": sl4,
        "止损4档": [
            {"档位": "1档 (-3%)", "价位": sl1, "建议操作": "⚠️ 检查基本面"},
            {"档位": "2档 (-5%)", "价位": sl2, "建议操作": "卖 1/3"},
            {"档位": "3档 (-8%)", "价位": sl3, "建议操作": "减半仓"},
            {"档位": "4档 (-10%)", "价位": sl4, "建议操作": "🛑 清仓"},
        ],
    }
