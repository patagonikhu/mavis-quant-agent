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


def extract_points_from_sigs(sigs: list, as_of_date: str = "") -> Dict[str, Any]:
    """从预计算的 sigs 里，按截止日期提取最近 30 天内触发的买卖点。"""
    if not sigs:
        return {'points': {}}

    as_of_clean = as_of_date.replace("-", "")[:8] if as_of_date else ""

    # 确定截止基准：as_of_date 或最后一根 bar
    if as_of_clean:
        filtered = [s for s in sigs if str(s.get('dt', ''))[:10].replace('-', '') <= as_of_clean]
    else:
        filtered = sigs

    if not filtered:
        return {'points': {}}

    last_date_str = str(filtered[-1].get('dt', ''))[:10]
    try:
        from datetime import datetime, timedelta
        cutoff_dt = datetime.strptime(last_date_str, '%Y-%m-%d') - timedelta(days=30)
    except Exception:
        cutoff_dt = None

    points = {}
    for s in reversed(filtered):
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
            if not isinstance(sig_val, str):
                continue
            if not sig_val or sig_val == '其他_其他_任意_0':
                continue
            prefix = '_'.join(sig_val.split('_')[:2])
            for key, (label, _direction) in SIGNAL_VALUE_MAP.items():
                if key in sig_val or prefix in sig_val:
                    if label not in points:
                        points[label] = f"¥{close:.2f} @ {bar_date_str}"
                    break

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

    points = {}

    if not sigs:
        return {'points': {}}

    # 最新 K 线日期作截止基准
    last_bar = sigs[-1]
    last_date_str = str(last_bar.get('dt', ''))[:10]
    try:
        from datetime import datetime, timedelta
        cutoff_dt = datetime.strptime(last_date_str, '%Y-%m-%d') - timedelta(days=30)
    except Exception:
        cutoff_dt = None

    # 反向遍历 (最新 → 最旧), 同 label 保留最近触发, 超 30 天截止
    for s in reversed(sigs):
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
            if not isinstance(sig_val, str):
                continue
            if not sig_val or sig_val == '其他_其他_任意_0':
                continue
            prefix = '_'.join(sig_val.split('_')[:2])
            for key, (label, _direction) in SIGNAL_VALUE_MAP.items():
                if key in sig_val or prefix in sig_val:
                    if label not in points:
                        points[label] = f"¥{close:.2f} @ {bar_date_str}"
                    break

    return {'points': points, 'raw_signals': sigs}


