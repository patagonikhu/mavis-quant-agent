"""
chan/beichi_60m.py - 60分底背/顶背 因子

把 原 dump_data._factor_chan_signals 顶部 line 193-197 的 60分 底背/顶背解析 提炼成独立因子

⚠️ 注意: 这个 factor 不是"计算" 60分背驰, 而是"解析" chan_analysis 返回的 bc_60_str
   真正的 60分 背驰计算在 chan_analysis.classify_beichi 里 (太重, 不拆)

输入: bc_60_str (str) — chan_analysis 的 60m 判定字符串
输出: dict {bot_60, top_60} — 跟原 dump_data 局部变量完全一致
"""
import pandas as pd
from tools.factors.base import Factor


class Beichi60mParseFactor(Factor):
    """60分背驰字符串解析因子

    输出字段 (dict):
      - bot_60: 60分底背驰 (True/False)
      - top_60: 60分顶背驰 (True/False)
    """

    name = "beichi_60m_parse"
    category = "chan"
    dependencies = []
    description = "60分背驰字符串解析 (从 bc_60_str 读底背/顶背)"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        bc_60_str = kwargs.get("bc_60_str", "") or ""
        bot_60 = "底背驰" in bc_60_str
        top_60 = "顶背驰" in bc_60_str
        return {"bot_60": bot_60, "top_60": top_60}
