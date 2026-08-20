"""
chan/wyckoff_combo.py - 缠论背驰 × 威科夫阶段 4 组合判定因子 (v5.7, 2026-07-29)

4 组合判定 (跟背驰方向 + 威科夫阶段交叉):
  底背驰 + Accumulation/Spring  → 🟢🟢 最强抄底 (双确认)
  底背驰 + Markup               → 🟢   次强 (主升回调中继, 加仓)
  底背驰 + ?                    → 🟡   观望 (只有空间, 时间没确认)
  底背驰 + Distribution         → 🔴   危险 (矛盾, 下降中继背驰, 不抄)
  顶背驰 + Distribution/UTAD    → 🟢🟢 最强逃顶 (双确认)
  顶背驰 + Markup               → 🟡   警惕 (主升中段, 顶背驰可能诱空, 看量能)
  顶背驰 + ?                    → 🟡   减仓 1/3 (只有空间, 时间没确认)
  顶背驰 + Accumulation         → 🟠   蓄势反转? (看量能, 不轻易抄)
  无背驰 + Distribution         → 🟠   减仓 (时间已经派发, 空间没创新高)
  无背驰 + Accumulation/Spring  → 🟢   强买入 (时间蓄势, 等空间突破中枢)
  无背驰 + Markup               → ⚪   持有 (主升中, 暂无信号)
  无背驰 + ?                    → ⚪   观望 (数据不足)

输入: beichi_str (str) + wy_stage (str)
输出: dict {
  4 组合判定 / verdict: str
  颜色 / color: str
  行动 / action: str
  原因 / reason: str
  风险 / risk: str
  实战建议 / advice: str
}

跟 project_memory 里"背驰+威科夫=4种组合" 表完全对应。
"""
import pandas as pd
from tools.factors.base import Factor


# 4 组合判定表 (key=(beichi_type, wy_stage), value=判定+行动+原因+风险+建议)
# beichi_type: "底" / "顶" / "无"
# wy_stage:    "Accumulation" / "Markup" / "Distribution" / "?"
COMBOS = {
    # ============ 底背驰 (4 种) ============
    ("底", "Accumulation"): {
        "color": "🟢🟢",
        "verdict": "最强抄底",
        "action": "分批建仓",
        "reason": "空间 (中枢下沿) + 时间 (Spring/累积) 双确认, 主力吸筹+背驰共振",
        "risk": "如果 Spring 已触发 5d+, 可能是最后一波假突破, 仓位要控",
        "advice": "建议 1-2 周分 2 次建仓, 首次 30%, 回调到中枢下沿再 70%",
    },
    ("底", "Markup"): {
        "color": "🟢",
        "verdict": "次强加仓",
        "action": "回调中继加仓",
        "reason": "已经在主升浪, 背驰是回调中继 (1买/2买 机会)",
        "risk": "如果主升浪已涨幅 >50%, 可能是顶部中继背驰, 警惕",
        "advice": "如果累计涨幅 <30%, 底背驰是买点; >50% 谨慎, 看量能",
    },
    ("底", "?"): {
        "color": "🟡",
        "verdict": "观望",
        "action": "等待时间确认",
        "reason": "只有空间 (底背驰) 没时间 (威科夫阶段不明), 胜率 60%",
        "risk": "震荡市背驰反复, 假信号多",
        "advice": "等威科夫阶段明确 (3 大阶段任一), 或等 60分 Spring 触发",
    },
    ("底", "Distribution"): {
        "color": "🔴",
        "verdict": "危险 (矛盾)",
        "action": "不抄底",
        "reason": "空间说底 (背驰), 时间说派发 (Distribution), 矛盾 → 下降中继背驰",
        "risk": "背驰后还会再跌, 抄底必被套",
        "advice": "等派发结束 + Spring 触发 + 60分底背驰三重确认再建仓",
    },
    # ============ 顶背驰 (4 种) ============
    ("顶", "Distribution"): {
        "color": "🟢🟢",
        "verdict": "最强逃顶",
        "action": "减仓 1/3 或清仓",
        "reason": "空间 (顶背驰) + 时间 (派发 UTAD) 双确认, 主力在高位卖给散户",
        "risk": "如果 UTAD 已触发 5d+, 主力可能已经走完, 立即清仓",
        "advice": "立即减仓 1/3, 跌破 MA60 再减 1/3, 触发 4 合 1 顶部预警全清",
    },
    ("顶", "Markup"): {
        "color": "🟡",
        "verdict": "警惕 (主升中段)",
        "action": "不卖, 观察量能",
        "reason": "主升浪中段顶背驰可能是诱空 (洗盘), 真正顶部要看量能是否缩",
        "risk": "如果量能持续放大 + 突破前高, 顶背驰失效, 继续持有",
        "advice": "如果缩量 + 跌破 MA5, 减仓 1/3; 如果放量 + 突破, 继续持有",
    },
    ("顶", "?"): {
        "color": "🟡",
        "verdict": "减仓 1/3",
        "action": "减仓 1/3 (预防)",
        "reason": "只有空间 (顶背驰) 没时间 (威科夫阶段不明), 预防性减仓",
        "risk": "可能错杀, 但仓位管控优先",
        "advice": "减仓 1/3, 等威科夫派发明确或 60分顶背驰确认再减 1/3",
    },
    ("顶", "Accumulation"): {
        "color": "🟠",
        "verdict": "蓄势反转?",
        "action": "观察量能",
        "reason": "时间说蓄势 (主力在低位吸筹), 空间说顶 (顶背驰), 矛盾 → 可能是下降→反转",
        "risk": "如果 Accumulation 已经走了 30d+, 可能是诱多",
        "advice": "如果量能持续缩 + 60分不创新低, 等待 Spring 触发买; 否则观望",
    },
    # ============ 无背驰 (4 种) ============
    ("无", "Distribution"): {
        "color": "🟠",
        "verdict": "减仓",
        "action": "减仓 1/3",
        "reason": "时间已经派发, 空间没背驰 (不创新高), 典型'钝刀割肉'行情",
        "risk": "可能横盘很久才下跌, 但下行趋势已定",
        "advice": "减仓 1/3, 不等 4 合 1 顶部预警 (那种要等顶背驰才触发)",
    },
    ("无", "Accumulation"): {
        "color": "🟢",
        "verdict": "强买入 (等中枢突破)",
        "action": "突破中枢上沿就买",
        "reason": "时间蓄势 + 空间没背驰 = 大级别底, 等中枢上沿突破就是 2买",
        "risk": "Accumulation 可能失败 (再跌), 需要 Spring 触发确认",
        "advice": "等中枢上沿突破 + Spring 触发, 分批建仓",
    },
    ("无", "Markup"): {
        "color": "⚪",
        "verdict": "持有 (无信号)",
        "action": "持有, 看 MA 偏离",
        "reason": "主升浪中, 暂无背驰也无派发信号",
        "risk": "主升浪中段可能突然见顶, 看 MA20 偏离 >20% 触发减仓",
        "advice": "MA20 偏离 >20% 减仓 1/3, >30% 减半仓, 触发 4 合 1 顶部预警全清",
    },
    ("无", "?"): {
        "color": "⚪",
        "verdict": "观望",
        "action": "等待",
        "reason": "空间+时间都没信号, 数据可能不足 (新票/复牌/低流动性)",
        "risk": "等下一个背驰或威科夫阶段明确",
        "advice": "等中枢形成 + 段背驰, 或等威科夫 3 大阶段任一确认",
    },
}


def _parse_beichi(beichi_str: str) -> str:
    """从背驰字符串解析方向 (跟 beichi_60m 因子逻辑一致)

    底背驰/弱背驰 → 底
    顶背驰/弱背驰 → 顶
    其他 → 无
    """
    if not beichi_str:
        return "无"
    s = str(beichi_str)
    if "底背驰" in s or "底弱背驰" in s:
        return "底"
    if "顶背驰" in s or "顶弱背驰" in s:
        return "顶"
    return "无"


def _parse_wy_stage(wy_stage: str) -> str:
    """威科夫 stage 标准化: Accumulation / Markup / Distribution / ?

    输入: stage 字符串 (可能含置信度/含义/操作, 或纯 stage 名字)
    输出: 4 种之一
    """
    if not wy_stage or wy_stage == "?":
        return "?"
    s = str(wy_stage).strip()
    # 大小写归一
    s_lower = s.lower()
    if "accum" in s_lower:
        return "Accumulation"
    if "markup" in s_lower or "mark" in s_lower:
        return "Markup"
    if "distribution" in s_lower or "dist" in s_lower or "派发" in s:
        return "Distribution"
    return "?"


class WyckoffChanComboFactor(Factor):
    """缠论背驰 × 威科夫阶段 4 组合判定因子

    输入 (kwargs):
      - beichi_str: 背驰字符串 ("底背驰"/"顶背驰"/"无")
      - wy_stage:  威科夫 stage ("Accumulation"/"Markup"/"Distribution"/"?")

    输出 (dict, 跟 COMBOS 表一致):
      - beichi_type: "底"/"顶"/"无"
      - wy_stage_norm: "Accumulation"/"Markup"/"Distribution"/"?"
      - color: "🟢🟢"/"🟢"/"🟡"/"🟠"/"🔴"/"⚪"
      - verdict: 一句话判定
      - action: 具体行动
      - reason: 判定原因
      - risk: 风险提示
      - advice: 实战建议
    """
    name = "wyckoff_chan_combo"
    category = "chan"
    dependencies = []
    description = "缠论背驰 × 威科夫阶段 4 组合判定 (12 种场景表)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df: pd.DataFrame = None, **kwargs) -> dict:
        beichi_str = kwargs.get("beichi_str", "")
        wy_stage = kwargs.get("wy_stage", "?")

        beichi_type = _parse_beichi(beichi_str)
        wy_stage_norm = _parse_wy_stage(wy_stage)

        key = (beichi_type, wy_stage_norm)
        result = COMBOS.get(key, {
            "color": "⚪",
            "verdict": "未知组合",
            "action": "观望",
            "reason": f"背驰={beichi_type}, 威科夫={wy_stage_norm} 组合不在表中",
            "risk": "可能数据异常",
            "advice": "检查 beichi_str / wy_stage 输入",
        })

        return {
            "beichi_type": beichi_type,
            "wy_stage_norm": wy_stage_norm,
            "color": result["color"],
            "verdict": result["verdict"],
            "action": result["action"],
            "reason": result["reason"],
            "risk": result["risk"],
            "advice": result["advice"],
        }
