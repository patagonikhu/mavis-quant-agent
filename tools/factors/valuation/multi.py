"""
valuation/multi.py - 估值 + 板块 + 5 类 14 子信号 因子 (Day D6, 2026-07-27)

把 原 dump_data 4 个子计算函数提炼成 4 个独立 factor:
- _calc_peg (line 592-610, 19 行) → PegFactor
- _calc_dcf (line 613-628, 16 行) → DcfFactor
- _calc_sector_overheat (line 631-643, 13 行) → SectorOverheatFactor
- _calc_five_categories (line 646-669, 24 行) → FiveCategoriesFactor
"""
import pandas as pd
from pathlib import Path
import yaml
from tools.factors.base import Factor

# === 项目级配置加载 (2026-07-27 集中管理) ===
def _load_config() -> dict:
    # v5.10.34 修: 之前是 .parent.parent.parent (→ tools/), 实际是 .parent.parent.parent.parent (→ 项目根/)
    # bug 导致 17 baseline peg/dcf 全是 "数据不足", 现在按 原 dump_data / report_renderer 一样走 with_venv.sh 兼容
    config_path = Path(__file__).parent.parent.parent.parent / "config" / "project.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ 找不到 {config_path}\n"
            f"   首次使用请: 手动创建 config/project.yaml (不在 git 里, 参考 git history 或 docs/AGENT_MEMORY.md)"
        )
    with open(config_path) as f:
        return yaml.safe_load(f)

_PROJECT_CFG = _load_config()


class PegFactor(Factor):
    """PEG 估值因子

    输出字段 (dict):
      - E0_本年/E1_NTM/E2/E3: 4 个 EPS
      - Forward PE: 前向 PE
      - g_CAGR: 3 年 CAGR (复苏扭曲时取 abs)
      - PEG_真实: PEG 数值
      - PEG_判定: ✅ Lynch 买入区 (<1.0) / 🟡 合理 (1-1.5) / 🟠 偏贵
    """

    name = "peg"
    category = "valuation"
    dependencies = []
    description = "PEG 估值 (Forward PE / 3年 CAGR)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        eps_table = kwargs.get("eps_table") or []
        current_price = kwargs.get("current_price", 0)

        if not eps_table or len(eps_table) < 4 or not current_price:
            return {"PEG_真实": "数据不足", "Forward PE": "—", "g": "—"}

        E0 = eps_table[0].get("eps", 0)
        E1 = eps_table[1].get("eps", 0)
        E2 = eps_table[2].get("eps", 0)
        E3 = eps_table[3].get("eps", 0)
        fwd_pe = current_price / E1 if E1 else 0
        g = (E3 / E0 - 1) / 3 if E0 and E0 > 0 else 0
        if g < 0 and E0 > 0:
            g = abs(g)
        peg = fwd_pe / (g * 100) if g > 0 else 0

        return {
            "E0_本年": E0, "E1_NTM": E1, "E2": E2, "E3": E3,
            "Forward PE": round(fwd_pe, 2),
            "g_CAGR": f"{g*100:.1f}%",
            "PEG_真实": round(peg, 2),
            "PEG_判定": "✅ Lynch 买入区" if peg < 1.0 else ("🟡 合理" if peg < 1.5 else "🟠 偏贵"),
        }


class DcfFactor(Factor):
    """DCF 隐含 L 因子 (3 档 r=8/10/12%)"""

    name = "dcf"
    category = "valuation"
    dependencies = []
    description = "DCF 隐含终局利润 L (3 档折现率 8/10/12%)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        eps_table = kwargs.get("eps_table") or []
        current_price = kwargs.get("current_price", 0)
        market_cap_yi = kwargs.get("market_cap_yi", 0)

        if not eps_table or len(eps_table) < 4 or not market_cap_yi:
            return {"r_8%_L": "数据不足"}

        E3 = eps_table[3].get("eps", 0)
        if not E3 or not current_price:
            return {"r_8%_L": "数据不足"}

        shares_yi = market_cap_yi / current_price
        results = {}
        for r in _PROJECT_CFG["thresholds"]["dcf"]["discount_rates"]:
            L_implied = market_cap_yi * r
            L_per_share = L_implied / shares_yi
            results[f"r_{int(r*100)}%"] = {
                "L_隐含(亿)": round(L_implied, 1),
                "L/E3(每share)": round(L_per_share / E3, 2) if E3 else 0,
            }
        return results


class SectorOverheatFactor(Factor):
    """板块过热预警因子 (1周/1月/3月涨幅)"""

    name = "sector_overheat"
    category = "valuation"
    dependencies = []
    description = "板块过热预警 (1周/1月/3月涨幅)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        p_1w = kwargs.get("price_change_1w", 0)
        p_1m = kwargs.get("price_change_1m", 0)
        p_3m = kwargs.get("price_change_3m", 0)

        return {
            "1周涨幅": f"{p_1w:+}%",
            "1月涨幅": f"{p_1m:+}%",
            "3月涨幅": f"{p_3m:+}%",
            "判定": (
                "🔴 过热" if p_1m > 30
                else "🟠 偏热" if p_1m > 20
                else "🟡 关注" if p_1m > 10
                else "🟦 超跌" if p_1m < -20
                else "✅ 安全"
            ),
        }


class FiveCategoriesFactor(Factor):
    """5 类 14 子信号因子 (量价+资金+龙头+政策+情绪 5 维)

    ⚠️ 当前只实算前 2 维 (量价+资金), 其他 3 维需 Tushare/WebSearch 补充
    """

    name = "five_categories"
    category = "valuation"
    dependencies = []
    description = "5 类 14 子信号 (量价+资金+龙头+政策+情绪)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        fflow = kwargs.get("fflow") or {}
        eps_table = kwargs.get("eps_table") or []
        current_price = kwargs.get("current_price", 0)

        score = 0
        signals = []
        fflow_today = ((fflow.get("data_columns") or {}).get("real") or [{}])[0]
        if fflow_today:
            main = fflow_today.get("main_yi", 0)
            if main > 3:
                signals.append(f"✅ 量价: 主力真实 +{main:.2f}亿 (强)")
                score += 2
            elif main > 0:
                signals.append(f"🟡 量价: 主力 +{main:.2f}亿 (弱)")
        score_main = fflow.get("score", 0)
        if score_main > 0:
            signals.append(f"✅ 资金: 5日分 {score_main} (进货)")
            score += 1
        signals.append("⚠️ 龙头: 需 Tushare 验证")
        signals.append("⚠️ 政策: 需 WebSearch")
        signals.append("⚠️ 情绪: 需 Tushare hot_rank")
        return {
            "score": score,
            "verdict": "🟢 强" if score >= 3 else ("🟡 中" if score >= 1 else "🔴 弱"),
            "signals": signals,
            "触发": f"{score}/14 触发 (其他需 Tushare/WebSearch)",
        }
