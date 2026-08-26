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


# cs.s key (czsc 1.0.1 实测) → (label, direction | None=从sig_val判断)
# direction=None 表示需要看 sig_val 内容来决定方向
CS_KEY_LABEL_MAP = {
    "日线_D1B_BUY1":               ("🟢1买",       "buy"),
    "日线_D1B_SELL1":              ("🔴1卖",       "sell"),
    "日线_D1_三买辅助V230228":      ("🟢3买",       "buy"),
    "日线_D1双中枢_BS1辅助V230311": ("🟢双中枢",    "buy"),
    "日线_D1MACD背驰_BS辅助V230804": None,   # 底背→buy, 顶背→sell, 看 sig_val
    "日线_D1DIF走平_BS辅助V230516":  None,   # 看多→buy, 看空→sell
    "日线_D1MACD开仓_BS辅助V230517": None,   # 看多→buy, 看空→sell
    "日线_D1_吞没形态":             None,    # 看涨→buy, 看跌→sell
    "日线_D1_孕线":                 None,    # 看涨→buy, 看跌→sell
    "日线_D1K_三法":                None,    # 看涨→buy, 看跌→sell
    "日线_D1_反击线":               None,    # 看涨→buy, 看跌→sell
    "日线_D1B_山川形态":            ("🟢山川",     "buy"),
    "日线_D1K_分手线":              None,    # 看涨→buy, 看跌→sell
    "日线_D1_三只乌鸦":             ("🔴三只乌鸦", "sell"),
    "日线_D1K_两只乌鸦":            ("🔴两只乌鸦", "sell"),
    "日线_D1K_塔形":                ("🔴塔形",     "sell"),
    "日线_快速突破_BE辅助V230815":   ("🟢笔结束",   "buy"),
    "日线_趋势跟随_BS辅助V240527":   None,   # 趋势跟随→buy, 其他→skip
}

# sig_val 方向判断: 含这些词→buy 或 sell
_VAL_BUY_TOKENS  = ("看多", "看涨", "底背", "买入", "趋势跟随")
_VAL_SELL_TOKENS = ("看空", "看跌", "顶背", "卖出")

# key→label (方向未知时) 用 sig_val 的词汇推断标签
_KEY_LABEL_FROM_VAL = {
    "日线_D1MACD背驰_BS辅助V230804":  {"多头": "🟢MACD底背", "空头": "🔴MACD顶背"},
    "日线_D1DIF走平_BS辅助V230516":   {"看多": "🟢DIF走平",  "看空": "🔴DIF走平"},
    "日线_D1MACD开仓_BS辅助V230517":  {"看多": "🟢MACD开仓", "看空": "🔴MACD开仓"},
    "日线_D1_吞没形态":               {"看涨": "🟢吞没",    "看跌": "🔴吞没"},
    "日线_D1_孕线":                   {"看涨": "🟢孕线",    "看跌": "🔴孕线"},
    "日线_D1K_三法":                  {"看涨": "🟢三法",    "看跌": "🔴三法"},
    "日线_D1_反击线":                 {"看涨": "🟢反击线",  "看跌": "🔴反击线"},
    "日线_D1K_分手线":                {"看涨": "🟢分手线",  "看跌": "🔴分手线"},
    "日线_趋势跟随_BS辅助V240527":    {"趋势跟随": "🟢趋势跟随"},
}

# 保留旧名兼容 (外部可能直接 import SIGNAL_VALUE_MAP)
SIGNAL_VALUE_MAP = {
    "买入_一买": ("🟢1买", "buy"),
    "买入_三买": ("🟢3买", "buy"),
}

_CS_META_KEYS = frozenset({"id", "dt", "symbol", "high", "low", "open", "close",
                            "vol", "amount", "freq", "cache"})


def precompute_signals(klines: List[Dict], code: str = "TEMP") -> list:
    """预计算全量信号序列，供 factor_history 复用（避免逐节点重算）。

    返回 generate_czsc_signals 的原始 list，调用方用 extract_points_from_sigs 按日期提取。
    """
    if not CZSC_AVAILABLE or len(klines) < 60:
        return []
    from .czsc_wrapper import klines_to_raw_bars
    bars = klines_to_raw_bars(klines, code)
    try:
        cfg = get_signals_config(list(SIGNAL_TEMPLATES.values()))
        return generate_czsc_signals(bars, cfg, sdt='20200101', df=False) or []
    except Exception:
        return []


def build_incremental_signals(klines: List[Dict], code: str = "TEMP",
                              warmup_to: int = -1,
                              weekly_bars=None):
    """建 CzscSignals 对象并预热到指定 bar 索引。

    Args:
        klines:      全量日线 K 线
        code:        股票代码
        warmup_to:   预热到第几根 bar（不含），-1 表示全量预热
        weekly_bars: 可选，周线 RawBar 列表；传入后 kas 同时包含周线

    Returns:
        (cs, bars, cfg, [])
    """
    if not CZSC_AVAILABLE or len(klines) < 60:
        return None, [], None, []
    import czsc as _czsc
    from .czsc_wrapper import klines_to_raw_bars
    bars = klines_to_raw_bars(klines, code)
    cfg = get_signals_config(list(SIGNAL_TEMPLATES.values()))
    n = warmup_to if warmup_to >= 0 else len(bars)

    extra_freqs = [_czsc.Freq.W] if weekly_bars else []
    bg = _czsc.BarGenerator(bars[0].freq, extra_freqs, 1000)
    if weekly_bars:
        bg.init_freq_bars(_czsc.Freq.W, weekly_bars)
    cs = _czsc.CzscSignals(bg, cfg)
    for bar in bars[:n]:
        cs.update_signals(bar)
    return cs, bars, cfg, []


def extract_points_from_sigs(sigs: list, as_of_date: str = "",
                              days: int = 30) -> Dict[str, Any]:
    """从预计算的 sigs 里，按截止日期提取买卖点。

    Args:
        days: 回看天数。30=最近30天（主报告）; 1=仅当天（factor_history 每行）
    """
    if not sigs:
        return {'points': {}}

    as_of_clean = as_of_date.replace("-", "")[:8] if as_of_date else ""

    if as_of_clean:
        filtered = [s for s in sigs if str(s.get('dt', ''))[:10].replace('-', '') <= as_of_clean]
    else:
        filtered = sigs

    if not filtered:
        return {'points': {}}

    last_date_str = str(filtered[-1].get('dt', ''))[:10]

    if days == 1:
        # 只取当天（as_of_clean 那一天）
        exact = as_of_clean or last_date_str.replace('-', '')
        day_sigs = [s for s in filtered
                    if str(s.get('dt', ''))[:10].replace('-', '')[:8] == exact[:8]]
        cutoff_dt = None
        scan_sigs = day_sigs
    else:
        try:
            from datetime import datetime, timedelta
            cutoff_dt = datetime.strptime(last_date_str, '%Y-%m-%d') - timedelta(days=days)
        except Exception:
            cutoff_dt = None
        scan_sigs = list(reversed(filtered))

    points = {}
    for s in (scan_sigs if days == 1 else scan_sigs):
        if not isinstance(s, dict):
            continue
        bar_date_str = str(s.get('dt', ''))[:10]
        if cutoff_dt:
            try:
                from datetime import datetime as _dt
                if _dt.strptime(bar_date_str, '%Y-%m-%d') < cutoff_dt:
                    break
            except Exception:
                pass
        close = 0
        try:
            close = float(s.get('close', 0))
        except (TypeError, ValueError):
            pass
        for sig_name, sig_val in s.items():
            if sig_name in _CS_META_KEYS:
                continue
            if not isinstance(sig_val, str) or not sig_val:
                continue
            if sig_val.startswith('其他'):
                continue
            if sig_name not in CS_KEY_LABEL_MAP:
                continue
            entry = CS_KEY_LABEL_MAP[sig_name]
            if entry is not None:
                label, _dir = entry
            else:
                val_map = _KEY_LABEL_FROM_VAL.get(sig_name, {})
                label = next((lbl for tok, lbl in val_map.items() if tok in sig_val), None)
                if label is None:
                    continue
            if label not in points:
                points[label] = f"¥{close:.2f}"

    return {'points': points}


def compute_buy_sell_signals(klines: List[Dict], code: str = "TEMP") -> Dict[str, Any]:
    """用 czsc 算抄底/逃顶/买卖点 等信号"""
    if not CZSC_AVAILABLE:
        raise RuntimeError(f"czsc 未安装: {_IMPORT_ERROR}")

    if len(klines) < 60:
        return {'points': {}}

    from .czsc_wrapper import klines_to_raw_bars
    bars = klines_to_raw_bars(klines, code)

    signals_seq = list(SIGNAL_TEMPLATES.values())

    try:
        cfg = get_signals_config(signals_seq)
        sigs = generate_czsc_signals(bars, cfg, sdt='20200101', df=False)
    except Exception as e:
        return {'points': {}, 'error': f'czsc signals 失败: {e}'}

    if not sigs:
        return {'points': {}}

    result = extract_points_from_sigs(sigs, days=1)
    return {**result, 'raw_signals': sigs}

