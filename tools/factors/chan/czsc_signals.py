"""
tools/factors/chan/czsc_signals.py — 用 czsc 算抄底/逃顶/买卖点信号

czsc 内置 246 个信号, 这次选出最实战可用的 7 类:

  抄底 (Buy Signal):
    - 🟢吞没形态: jcc_ten_mo_V221028 (看涨吞没)
    - 🟢孕线: jcc_yun_xian_V221118 (看涨孕线)
    - 🟢三法: jcc_san_fa_V20221115 (看涨三法)
    - 🟢反击线: jcc_fan_ji_xian_V221121
    - 🟢山川: jcc_shan_chun_V221121

  逃顶 (Sell Signal):
    - 🔴三只乌鸦: jcc_three_crow_V221108 (强烈顶部信号)
    - 🔴两只乌鸦: jcc_two_crow_V221108
    - 🔴塔形: jcc_ta_xing_V221124

  缠论 (Chan Theory):
    - 🟢1买 / 🔴1卖: cxt_first_buy / cxt_first_sell
    - 🟢3买: cxt_third_buy_V230228
    - 🟢双中枢: cxt_double_zs_V230311

  MACD (Tech Indicator):
    - 🟢MACD底背: tas_macd_bc_V230804
    - 🟢DIF走平: zdy_macd_dif_V230516
    - 🟢MACD开仓: zdy_macd_dif_V230517
"""
import sys
from pathlib import Path
from typing import List, Dict, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

try:
    import czsc
    from czsc import generate_czsc_signals, get_signals_config
    CZSC_AVAILABLE = True
except ImportError as e:
    CZSC_AVAILABLE = False
    _IMPORT_ERROR = e


# 信号模板 (完整格式, 跟 18 案例一致)
SIGNAL_TEMPLATES = {
    # 缠论 1买/3买
    "1买": "日线_D1B_BUY1V221126_一买_任意_任意_0",
    "1卖": "日线_D1B_SELL1V221126_一卖_任意_任意_0",
    "3买": "日线_D1_三买辅助V230228_三买_任意_任意_0",
    "双中枢": "日线_D1双中枢_BS1辅助V230311_双中枢_任意_任意_0",

    # MACD 背驰
    "MACD底背": "日线_D1MACD背驰_BS辅助V230804_底背_任意_任意_0",
    "MACD顶背": "日线_D1MACD背驰_BS辅助V230804_顶背_任意_任意_0",
    "DIF走平": "日线_D1DIF走平_BS辅助V230516_走平_任意_任意_0",
    "MACD开仓": "日线_D1MACD开仓_BS辅助V230517_开仓_任意_任意_0",

    # K线形态 - 抄底
    "吞没": "日线_D1K_吞没形态V221028_吞没_任意_任意_0",
    "孕线": "日线_D1K_孕线V221118_孕线_任意_任意_0",
    "三法": "日线_D1K_三法V20221115_三法_任意_任意_0",
    "反击线": "日线_D1K_反击线V221121_反击线_任意_任意_0",
    "山川": "日线_D1B_山川形态V221121_山川_任意_任意_0",
    "分手线": "日线_D1K_分手线V20221113_分手线_任意_任意_0",

    # K线形态 - 逃顶
    "三只乌鸦": "日线_D1_三只乌鸦V221108_三只乌鸦_任意_任意_0",
    "两只乌鸦": "日线_D1K_两只乌鸦V221108_两只乌鸦_任意_任意_0",
    "塔形": "日线_D1K_塔形V221124_塔形_任意_任意_0",

    # 笔结束 / 决策
    "笔结束": "日线_快速突破_BE辅助V230815_快速突破_任意_任意_0",
    "趋势跟随": "日线_趋势跟随_BS辅助V240527_趋势跟随_任意_任意_0",
}


# 信号 value 模式 → 项目命名 + 方向 (抄底/逃顶)
SIGNAL_VALUE_MAP = {
    # 抄底 (绿)
    "看涨_吞没": ("🟢吞没", "buy"),
    "看涨_孕线": ("🟢孕线", "buy"),
    "看涨_三法": ("🟢三法", "buy"),
    "看涨_反击线": ("🟢反击线", "buy"),
    "看涨_山川": ("🟢山川", "buy"),
    "看涨_分手线": ("🟢分手线", "buy"),
    "买入_一买": ("🟢1买", "buy"),
    "买入_三买": ("🟢3买", "buy"),
    "买入_双中枢": ("🟢双中枢", "buy"),
    "买入_底背": ("🟢MACD底背", "buy"),
    "买入_走平": ("🟢DIF走平", "buy"),
    "买入_开仓": ("🟢MACD开仓", "buy"),
    "买入_快速突破": ("🟢笔结束", "buy"),

    # 逃顶 (红)
    "看跌_三只乌鸦": ("🔴三只乌鸦", "sell"),
    "看跌_两只乌鸦": ("🔴两只乌鸦", "sell"),
    "看跌_塔形": ("🔴塔形", "sell"),
    "卖出_一卖": ("🔴1卖", "sell"),
    "卖出_顶背": ("🔴MACD顶背", "sell"),
}


def compute_buy_sell_signals(klines: List[Dict], code: str = "TEMP") -> Dict[str, Any]:
    """用 czsc 算抄底/逃顶/买卖点 等信号"""
    if not CZSC_AVAILABLE:
        raise RuntimeError(f"czsc 未安装: {_IMPORT_ERROR}")

    if len(klines) < 60:
        return {'points': {}, 'beichi': _bc_empty()}

    from .czsc_wrapper import klines_to_raw_bars
    bars = klines_to_raw_bars(klines, code)

    signals_seq = list(SIGNAL_TEMPLATES.values())

    try:
        cfg = get_signals_config(signals_seq)
        sigs = generate_czsc_signals(bars, cfg, sdt='20200101', df=False)
    except Exception as e:
        return {'points': {}, 'beichi': _bc_empty(),
                'error': f'czsc signals 失败: {e}'}

    points = {}
    beichi = _bc_empty()
    last_date = ''
    last_close = 0

    # 找最近的触发信号
    for s in sigs:
        if not isinstance(s, dict):
            continue
        last_date = str(s.get('dt', ''))[:10]
        try:
            last_close = float(s.get('close', 0))
        except (TypeError, ValueError):
            last_close = 0

        for sig_name, sig_val in s.items():
            if not isinstance(sig_val, str):
                continue
            if not sig_val or sig_val == '其他_其他_任意_0':
                continue
            # 按 value 头 4 个字匹配 (e.g. "看涨_吞没_任意_任意_0")
            prefix = '_'.join(sig_val.split('_')[:2])
            # 看是否在映射里
            for key, (label, _direction) in SIGNAL_VALUE_MAP.items():
                if key in sig_val or prefix in sig_val:
                    # 如果同 key 已有 (说明今天有多个信号), 保留
                    if label not in points:
                        points[label] = f"¥{last_close:.2f} @ {last_date}"
                    break

            # 背驰特殊处理
            if '底背' in sig_val and 'MACD' in sig_name:
                beichi = {'display': f'✅底背驰 @ {last_date}', 'direction': 'bot',
                          'strength': 'normal', 'ratio': 0, 'a1': 0, 'a2': 0,
                          's1_hub': -1, 's2_hub': -1}
            elif '顶背' in sig_val and 'MACD' in sig_name:
                beichi = {'display': f'⚠️顶背驰 @ {last_date}', 'direction': 'top',
                          'strength': 'normal', 'ratio': 0, 'a1': 0, 'a2': 0,
                          's1_hub': -1, 's2_hub': -1}

    return {'points': points, 'beichi': beichi, 'raw_signals': sigs}


def _bc_empty():
    return {'display': '数据不足', 'direction': 'none', 'strength': 'none',
            'bc_type': 'normal', 'ratio': 0, 'a1': 0, 'a2': 0,
            's1_hub': -1, 's2_hub': -1}
