"""
chan/buy_sell.py - 缠论 4 级别买卖点因子 (Day G, 2026-07-27)

把 dump_data.calc_buy_sell_points (原 line 382-528, 128 行) 提炼成独立 factor

4 级别: weekly / daily / 60min / 30min
9 个买卖点: 🟢0买/🟢1买/🟢1买⭐/🟢2买/🟢3买/🔴1卖/🔴1卖⭐/🔴2卖/🔴3卖
+ 1 个 action 字符串 (按优先级选)

输入: res (dict, 含 hub/segs/p/seg_status), beichi_str (str), klines (list, 该级别 K 线)
输出: dict {points: {9个买卖点: 字符串或"—"}, action: 字符串}

⚠️ 重要: 完全保留 dump_data 原算法 (分型确认 + 趋势/盘整 1买1卖 区分), regression test 验证
"""
import pandas as pd
from tools.factors.base import Factor


class BuySellPointsFactor(Factor):
    """缠论 4 级别买卖点因子

    对应 dump_data.calc_buy_sell_points 单级别 (weekly/daily/60min/30min) 循环体

    输出字段 (dict):
      - points: dict {9个买卖点: 字符串或"—"}
      - action: 字符串 (按优先级选最大信号)
    """

    name = "buy_sell_points"
    category = "chan"
    dependencies = []  # 用 res + beichi_str + klines
    description = "缠论 4 级别买卖点 (0买/1买/2买/3买/1卖/2卖/3卖) + action"
    version = "1.0"
    output_type = "dict"

    def compute(self, df=None, **kwargs) -> dict:
        res = kwargs.get("res")
        klines = kwargs.get("klines") or []
        level_key = kwargs.get("level_key", "unknown")

        # 兜底: res 空, 返回空 dict (跟原 dump_data 行为一致)
        if not res:
            return {
                "points": {
                    "🟢0买": "—",
                    "🟢1买": "—",
                    "🟢1买⭐": "—",
                    "🟢2买": "—",
                    "🟢3买": "—",
                    "🔴1卖": "—",
                    "🔴1卖⭐": "—",
                    "🔴2卖": "—",
                    "🔴3卖": "—",
                    "action": "—",
                },
                "action": "—",
                "level": level_key,
            }

        hub = res.get("hub", {})
        segs = res.get("segs", [])
        current_price = res.get("p", 0)
        seg_status = str(res.get("seg_status", ""))
        hub_pos = str(hub.get("pos", ""))

        points = {
            "🟢0买": "—", "🟢1买": "—", "🟢1买⭐": "—",
            "🟢2买": "—", "🟢3买": "—",
            "🔴1卖": "—", "🔴1卖⭐": "—",
            "🔴2卖": "—", "🔴3卖": "—",
        }

        # === 1. bc_class: 背驰分类 (⭐趋势 / 🟡盘整 / 🔵普通 / 无) ===
        bc_class = "无"
        beichi_obj = kwargs.get("beichi_str", "")  # 结构体（新）或字符串（旧兼容）
        if hub.get("valid") and segs and current_price:
            from tools.factors.chan import classify_beichi
            try:
                if isinstance(beichi_obj, dict) and "direction" in beichi_obj:
                    bc_class = classify_beichi(beichi_obj)
                # 旧字符串路径：无法重算 MACD（无 closes/dates），保持"无"
            except Exception:
                bc_class = "无"
        is_trend_bc = "⭐" in bc_class
        is_bottom_bc = "底背" in bc_class
        is_top_bc = "顶背" in bc_class

        # === 2. 分型确认 ===
        from tools.factors.chan import has_recent_confirmed_fenxing  # v5.5 改走 factor 库
        bottom_fx_confirmed = False
        top_fx_confirmed = False
        if klines and len(klines) >= 5:
            bottom_fx_confirmed, _, _ = has_recent_confirmed_fenxing(
                klines, lookback=5, kind="bottom"
            )
            top_fx_confirmed, _, _ = has_recent_confirmed_fenxing(
                klines, lookback=5, kind="top"
            )

        # === 3. 9 个买卖点判定 ===
        if hub.get("valid") and segs:
            low = hub.get("low", 0)
            high = hub.get("high", 0)

            # 🟢0买: 中枢下方 + 底背驰
            if current_price < low and is_bottom_bc:
                fx_note = " +分型确认" if bottom_fx_confirmed else " (待分型确认)"
                points["🟢0买"] = f"¥{current_price:.2f} 底背驰{fx_note}"

            # 🟢1买: 中枢下沿 + 底背驰 + 底分型确认
            if current_price < low and ("上涨" in seg_status or "震荡" in seg_status) and is_bottom_bc and bottom_fx_confirmed:
                if is_trend_bc:
                    points["🟢1买⭐"] = f"¥{current_price:.2f} 趋势1买 (2中枢+分型)"
                else:
                    points["🟢1买"] = f"¥{current_price:.2f} 盘整1买 (1中枢+分型)"

            # 🟢2买: 中枢下沿附近 + 上涨/震荡 + 底分型
            if low and abs(current_price - low) / low < 0.05 and ("上涨" in seg_status or "震荡" in seg_status) and bottom_fx_confirmed:
                points["🟢2买"] = f"¥{low:.2f} 接近 (分型确认)"

            # 🟢3买: 中枢上方 + 上涨 + 底分型
            if current_price > high and "上涨" in seg_status and bottom_fx_confirmed:
                points["🟢3买"] = f"¥{high:.2f} 突破上沿 (分型确认)"

            # 🔴1卖: 中枢上沿 + 顶背驰 + 顶分型
            if current_price > high and is_top_bc and top_fx_confirmed:
                if is_trend_bc:
                    points["🔴1卖⭐"] = f"¥{current_price:.2f} 趋势1卖 (2中枢+分型)"
                else:
                    points["🔴1卖"] = f"¥{current_price:.2f} 盘整1卖 (1中枢+分型)"

            # 🔴2卖: 中枢上沿附近 + 震荡 + 顶分型
            if high and abs(current_price - high) / high < 0.05 and "震荡" in seg_status and top_fx_confirmed:
                points["🔴2卖"] = f"¥{high:.2f} 接近 (分型确认)"

            # 🔴3卖: 中枢下方 + 下跌延伸 + 顶分型
            if current_price < low and "下跌延伸" in seg_status and top_fx_confirmed:
                points["🔴3卖"] = f"¥{low:.2f} 跌穿下沿 (分型确认)"


        # === 4. action 优先级 (跟原 dump_data 完全一致) ===
        if points["🟢1买⭐"] != "—":
            action = "⭐ 趋势1买建仓 (2中枢, 最强)"
        elif points["🔴1卖⭐"] != "—":
            action = "⭐ 趋势1卖减仓 (2中枢, 最强)"
        elif points["🟢1买"] != "—":
            action = "🟢 盘整1买建仓 (1中枢)"
        elif points["🔴1卖"] != "—":
            action = "🔴 盘整1卖减仓 (1中枢)"
        elif points["🟢0买"] != "—":
            action = "🟢 0买建仓 (超跌)"
        elif points["🟢2买"] != "—":
            action = "🟢 2买点建仓"
        elif points["🟢3买"] != "—":
            action = "🟢 3买点持有"
        elif points["🔴2卖"] != "—":
            action = "🔴 2卖点减仓"
        elif points["🔴3卖"] != "—":
            action = "🔴 3卖点清仓"
        elif "上涨" in seg_status:
            action = "🟢 持有"
        elif "下跌" in seg_status:
            action = "🟡 观察"
        else:
            action = "🟡 震荡"

        points["action"] = action

        return {
            "points": points,
            "action": action,
            "level": level_key,
        }
