"""
detectors/scanner.py - 9 sub_event 扫描器 (跟 WyckoffTradingAgent _scan_sub_events 1:1)

输入: K 线 + open + pct_chg + market_cap_yi + period_label
输出: 9 sub_event 集合 (set 去重)

v5.7 (2026-07-29) 加: 按 period_label 自适应 detector 参数
  - 60分 SOS pct_min 从 6% 降到 2% (6% 几乎不触发, 2% = 日内 5% 合理)
  - 周线 SOS pct_min 从 6% 提到 8% (周线 6% 偏松)
  - 全部阈值在 config/project.yaml:wyckoff_detectors 节, 改 config 不用动代码
"""
import pathlib as _pl
import yaml as _yaml

from . import (
    detect_spring, detect_lps, detect_evr, detect_sos, detect_compression,
    detect_trend_pullback, detect_markup_entry, detect_distribution_start, detect_upthrust,
)

# 加载 config (跟其他模块一致, config 缺失 raise FileNotFoundError)
_CFG_PATH = _pl.Path(__file__).parent.parent.parent.parent.parent / "config" / "project.yaml"
try:
    with open(_CFG_PATH, encoding="utf-8") as _f:
        _CFG = _yaml.safe_load(_f) or {}
except FileNotFoundError:
    raise FileNotFoundError(
        f"❌ config/project.yaml 不存在 (路径: {_CFG_PATH})\n"
        f"   首次使用请: cp .env.example .env (拉代码根目录)"
    )

_DET = _CFG.get("wyckoff_detectors", {}) or {}
_CODE_OVERRIDES = _CFG.get("code_overrides", {}) or {}


def _lookup(cfg_section: dict, key: str, period_label: str, default, style: str = None):
    """从 config 节按 period_label 查值, 缺则 fallback default

    支持三种结构:
      1. 标量: {key: 6.0}              → 任何周期都返 6.0
      2. 按周期: {key: {daily: 6.0, weekly: 8.0}} → 按 period_label 选
      3. 按周期×风格: {key: {daily: {default: 25, star: 40}}}
         → 按 period_label + style (可空) 选
    """
    val = cfg_section.get(key, default)
    if isinstance(val, dict):
        # 3 层: period → style → value
        if style is not None:
            period_dict = val.get(period_label, {})
            if isinstance(period_dict, dict):
                return period_dict.get(style, period_dict.get("default", default))
            return period_dict  # period 下是标量, 忽略 style
        # 2 层: period → value
        return val.get(period_label, val.get("default", default))
    return val


def _det_cfg(detector: str, code: str | None = None) -> dict:
    """返回 detector 的配置，code_overrides 优先于全局 wyckoff_detectors。"""
    global_cfg = _DET.get(detector, {}) or {}
    if not code:
        return global_cfg
    override = (_CODE_OVERRIDES.get(code, {}) or {}).get("wyckoff_detectors", {}) or {}
    det_override = override.get(detector, {}) or {}
    if not det_override:
        return global_cfg
    # 深度合并: override 覆盖 global_cfg，未覆盖的 key 保留 global_cfg
    merged = dict(global_cfg)
    for k, v in det_override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = {**merged[k], **v}
        else:
            merged[k] = v
    return merged


def scan_sub_events(c, h, l, v, rng, o=None, pct_chg=None, max_bias=25.0,
                    market_cap_yi=0.0, period_label="daily",
                    as_of_idx: int | None = None,
                    dates: list | None = None,
                    code: str | None = None) -> list:
    """扫整段 K 线, 返回触发的 sub_events (带时间戳 list)

    9 sub_event (跟 WyckoffTradingAgent L4 1:1):
      Accumulation: Spring / LPS / EVR
      Markup:       SOS / Compression / TrendPullback / MarkupEntry
      Distribution: DistributionStart / UTAD

    v5.10.42 改:
    - 输出格式: list[{name, date, idx, price, vol}] (替代 set[str], 加时间戳)
    - 加 as_of_idx 参数: 扫到 i <= as_of_idx 截止 (用于历史回测, 不算未来 K 线)
    - 加 dates 参数: 触发时存日期 (回测时能看到具体哪天)

    Args:
        c, h, l, v: 收盘/最高/最低/成交量 list
        rng: 区间 (Stage 用, 内部不用)
        o, pct_chg: open + 涨跌幅 (v5.1 真字段)
        max_bias: bias_200 上限 (科创 40 / 主板 25 / 趋势 35, 也可按风格传)
        market_cap_yi: 总市值 (亿) — TrendPullback 量阈值缩放
        period_label: "daily" / "weekly" — 决定子事件阈值 (默认走日线参数)
        as_of_idx: 截至索引 (None=扫整段, int=扫到 i <= as_of_idx, 用于回测)
        dates: 日期 list (跟 c 等长, 触发时记 date, dump kline['date'])

    Returns:
        list[dict]: [{"name", "date", "idx", "price", "vol"}, ...]
        不去重 (同一 sub_event 可多次触发, 按时间排序)
    """
    if o is None:
        o = [c[i-1] if i > 0 else c[0] for i in range(len(c))]

    # ===== 周期自适应参数 (2026-07-29 v5.7 加, 从 config 读) =====
    sos_cfg = _det_cfg("sos", code)
    spring_cfg = _det_cfg("spring", code)
    evr_cfg = _det_cfg("evr", code)
    tp_cfg = _det_cfg("trend_pullback", code)
    lps_cfg = _det_cfg("lps", code)
    cmp_cfg = _det_cfg("compression", code)
    utad_cfg = _det_cfg("utad", code)
    ds_cfg = _det_cfg("distribution_start", code)

    # 外层 max_bias (40=科创/35=趋势/25=默认) 翻译成 style key
    _style = "star" if max_bias >= 40 else ("trending" if max_bias >= 35 else "default")

    sos_pct_min        = _lookup(sos_cfg, "pct_min", period_label, 6.0)
    sos_breakout_win   = _lookup(sos_cfg, "breakout_window", period_label, 60)
    sos_vol_ratio      = sos_cfg.get("vol_ratio", 3.0)
    sos_vol_quantile_w = sos_cfg.get("vol_quantile_window", 60)
    sos_vol_quantile   = _lookup(sos_cfg, "vol_quantile", period_label, 0.95)
    sos_breakout_tol   = _lookup(sos_cfg, "breakout_tolerance", period_label, 0.01)
    sos_max_bias       = _lookup(sos_cfg, "max_bias", period_label, max_bias, _style)

    spring_vol_ratio   = _lookup(spring_cfg, "vol_ratio", period_label, 1.3)
    spring_max_bias    = _lookup(spring_cfg, "max_bias", period_label, 15.0)

    evr_vol_ratio      = _lookup(evr_cfg, "vol_ratio", period_label, 1.8)
    evr_vol_window     = evr_cfg.get("vol_window", 20)
    evr_max_bias       = _lookup(evr_cfg, "max_bias", period_label, 25.0)
    evr_max_drop       = _lookup(evr_cfg, "max_drop", period_label, 2.0)
    evr_max_rise       = _lookup(evr_cfg, "max_rise", period_label, 2.0)

    tp_max_bias        = _lookup(tp_cfg, "max_bias", period_label, 35.0)
    # P8.A (2026-07-31): min/max_pullback 周期分层, 60m 0.5/8 (p50 1.65%)
    tp_min_pullback    = _lookup(tp_cfg, "min_pullback", period_label, 5.0)
    tp_max_pullback    = _lookup(tp_cfg, "max_pullback", period_label, 20.0)
    lps_max_bias       = _lookup(lps_cfg, "max_bias", period_label, max_bias, _style)
    lps_vol_dry_ratio  = _lookup(lps_cfg, "vol_dry_ratio", period_label, 0.50)
    cmp_max_bias       = _lookup(cmp_cfg, "max_bias", period_label, 25.0)

    # DistributionStart (顶部信号) 2 阈值
    ds_confirm_days  = _lookup(ds_cfg, "confirm_days", period_label, 3)
    ds_vol_dry_ratio = _lookup(ds_cfg, "vol_dry_ratio", period_label, 0.5)

    # UTAD (顶部信号) 4 阈值
    utad_breakout_pct     = _lookup(utad_cfg, "breakout_pct", period_label, 1.0)
    utad_close_back_pct   = _lookup(utad_cfg, "close_back_pct", period_label, 0.3)
    utad_upper_shadow_thr = _lookup(utad_cfg, "upper_shadow_thr", period_label, 0.35)
    utad_vol_ratio_thr    = _lookup(utad_cfg, "vol_ratio_thr", period_label, 1.5)

    # 周期自适应 MA 长窗口 + bias 门槛 + lookback（DistributionStart / UTAD 用）
    # daily:  MA200 ≈ 1年，bias_min=15%，lookback=60交易日
    # weekly: MA50 ≈ 1年，bias_min=30%，lookback=60周
    _period_cfg = {
        "daily":  (200, 15.0, 60),
        "weekly": (50,  30.0, 60),
    }
    ma_long_w, dist_min_bias, upthrust_lookback = _period_cfg.get(period_label, (200, 15.0, 60))

    # 预算 ATR 数组，O(n) 一次，避免每根 bar 内 _is_trading_range_context 重算 O(n)
    # atr_arr[i] = 以第 i 根 bar 结尾的 20 日 ATR
    _ATR_WIN = 20
    atr_arr = [0.0] * len(c)
    if len(c) > _ATR_WIN:
        trs = []
        for j in range(len(c)):
            tr1 = h[j] - l[j]
            tr2 = abs(h[j] - (c[j-1] if j > 0 else c[j]))
            tr3 = abs(l[j] - (c[j-1] if j > 0 else c[j]))
            trs.append(max(tr1, tr2, tr3))
        for j in range(_ATR_WIN - 1, len(c)):
            atr_arr[j] = sum(trs[j - _ATR_WIN + 1:j + 1]) / _ATR_WIN

    events = []
    n = len(c)
    # v5.10.42: as_of_idx 限制扫到 i <= as_of_idx (None=扫整段)
    end_idx = (as_of_idx + 1) if as_of_idx is not None else n
    end_idx = min(end_idx, n)  # 不超过实际长度
    for i in range(1, end_idx):
        d_str = dates[i] if dates and i < len(dates) else None
        v_at_i = v[i] if i < len(v) else 0
        c_at_i = c[i] if i < len(c) else 0
        if detect_spring(c, h, l, v, o, i, max_bias=spring_max_bias, vol_ratio=spring_vol_ratio,
                         precomputed_atr=atr_arr[i]):
            events.append({"name": "Spring", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_lps(c, h, l, v, o, i, max_bias=lps_max_bias, pct_chg=pct_chg,
                      vol_dry_ratio=lps_vol_dry_ratio, precomputed_atr=atr_arr[i]):
            events.append({"name": "LPS", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_evr(c, h, l, v, o, i, vol_window=evr_vol_window, vol_ratio=evr_vol_ratio,
                      max_bias=evr_max_bias, max_drop=evr_max_drop, max_rise=evr_max_rise,
                      min_bias=dist_min_bias, pct_chg=pct_chg):
            events.append({"name": "EVR", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_sos(c, h, l, v, o, i, vol_window=20, vol_ratio=sos_vol_ratio,
                      vol_quantile_window=sos_vol_quantile_w, vol_quantile=sos_vol_quantile,
                      breakout_window=sos_breakout_win, breakout_tolerance=sos_breakout_tol,
                      pct_min=sos_pct_min,
                      max_bias=sos_max_bias, pct_chg=pct_chg):
            events.append({"name": "SOS", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_compression(c, h, l, v, o, i, max_bias=cmp_max_bias, pct_chg=pct_chg):
            events.append({"name": "Compression", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_trend_pullback(c, h, l, v, o, i, max_bias=tp_max_bias, market_cap_yi=market_cap_yi,
                                  min_pullback=tp_min_pullback, max_pullback=tp_max_pullback):
            events.append({"name": "TrendPullback", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_markup_entry(c, h, l, v, o, i):
            events.append({"name": "MarkupEntry", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_distribution_start(c, h, l, v, o, i, ma_long_w=ma_long_w,
                                     high_thr=dist_min_bias,
                                     confirm_days=ds_confirm_days,
                                     vol_dry_ratio=ds_vol_dry_ratio):
            events.append({"name": "DistributionStart", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
        if detect_upthrust(c, h, l, v, o, i, ma_long_w=ma_long_w, min_bias=dist_min_bias,
                           lookback=upthrust_lookback,
                           breakout_pct=utad_breakout_pct,
                           close_back_pct=utad_close_back_pct,
                           upper_shadow_thr=utad_upper_shadow_thr,
                           vol_ratio_thr=utad_vol_ratio_thr):
            events.append({"name": "UTAD", "date": d_str, "idx": i, "price": c_at_i, "vol": v_at_i})
    return events
