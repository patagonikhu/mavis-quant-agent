"""
因子 × 3 周期 综合矩阵 (2026-07-25 重构, 2026-08-17 改名为 factor_matrix)

作为统一输出模块, 可被以下 skill 调用:
  - /t-analyze: 单只票分析 (render_report 渲染)
  - /t-watchlist: 批量扫描 (输出 buy/sell 建议价)
  - /t-sector: 板块分析 (按建议价选股)
  - /t-monitor: 监控 (触发后给建议)

输入: signals_5method 输出 + chan_data + buy_sell_points
输出: {
    "code", "name", "current_price",
    "scene", "scene_name", "resonance_count", "action",
    "matrix": {"weekly"/"daily"/"60min": {5方法 + 建议价}},
    "top_warning": {"weekly"/"daily"/"60min": "强/标准/弱/否"},
    "bottom_signal": {"weekly"/"daily"/"60min": "1买⭐/1买/2买/3买/否"},
}
"""
from typing import Dict, List, Optional, Tuple


# ============================================================
# 123 买卖点判定 (从 buy_sell_points 数据)
# ============================================================

def _bsp_to_action(bsp: dict) -> str:
    """买卖点 → 操作建议 (从 buy_sell_points 提取)"""
    if not bsp:
        return '—'
    # 优先级: 1买⭐ > 1买 > 2买 > 3买 > 0买 (底部)
    #          1卖⭐ > 1卖 > 2卖 > 3卖 (顶部)
    action = bsp.get('action', '—')
    if '1买⭐' in str(action) or '趋势1买' in str(action):
        return '⭐⭐⭐⭐⭐ 趋势1买建仓 (2中枢+分型, 最强)'
    if '1买' in str(action):
        return '⭐⭐⭐⭐ 1买建仓 (底背驰+分型)'
    if '2买' in str(action):
        return '⭐⭐⭐ 2买加仓 (中枢下沿)'
    if '0买' in str(action):
        return '⭐⭐ 0买试仓 (超跌反弹)'
    if '1卖⭐' in str(action) or '趋势1卖' in str(action):
        return '🔴🔴🔴🔴🔴 趋势1卖清仓 (2中枢+分型, 最强)'
    if '1卖' in str(action):
        return '🔴🔴🔴🔴 1卖减仓 (顶背驰+分型)'
    if '2卖' in str(action):
        return '🔴🔴🔴 2卖减仓 (中枢上沿)'
    if '3买' in str(action):
        return '⭐⭐ 3买突破加仓 (中枢上沿)'
    if '3卖' in str(action):
        return '🔴🔴 3卖减仓 (中枢下沿)'
    return str(action) if action else '—'


# ============================================================
# 价格计算: 1买价 / 1卖价 / 中枢上下沿
# ============================================================

def _calc_buy_sell_prices(
    chan_data: dict,
    bsp: dict,
    wyckoff: dict,
    current_price: float,
) -> dict:
    """
    计算 1买价 / 1卖价 / 中枢上下沿 (按价格位置动态调整)

    三种情况:
    A. 价格在中枢上方: 1买 = 中枢下沿 (回踩买入), 1卖 = 中枢上沿 (突破后止盈)
    B. 价格在中枢内部: 1买 = 中枢下沿, 1卖 = 中枢上沿
    C. 价格在中枢下方 (跌穿): 1买 = 结构低点 (不是中枢, 中枢在上面), 1卖 = 中枢下沿 (反弹第一目标)

    结构低点 = 当前周期最近 N 根 K 线的最低价 (从 chan_data 推, 找不到则用 hub_low * 0.95)
    """
    if not chan_data:
        return {
            'hub_low': None,
            'hub_high': None,
            'target_buy': None,
            'target_sell': None,
            'price_position': '?',
        }
    # chan_data 结构兼容: {low, high, valid} 或 {hub: {low, high, valid}}
    hub = chan_data.get('hub', chan_data) if 'low' not in chan_data else chan_data
    if not hub.get('valid'):
        return {
            'hub_low': None,
            'hub_high': None,
            'target_buy': None,
            'target_sell': None,
            'price_position': '无中枢',
        }
    hub_low = hub.get('low', 0)
    hub_high = hub.get('high', 0)
    if not hub_low or not hub_high or hub_low >= hub_high:
        return {
            'hub_low': hub_low,
            'hub_high': hub_high,
            'target_buy': None,
            'target_sell': None,
            'price_position': '中枢无效',
        }

    # 判定价格位置
    if current_price > hub_high:
        pos = 'A_上方'
        target_buy = hub_low
        target_sell = hub_high
    elif current_price >= hub_low:
        pos = 'B_内部'
        target_buy = hub_low
        target_sell = hub_high
    else:
        # 价格在中枢下方 → 1买应是结构低点 (而不是中枢, 中枢在上面)
        pos = 'C_下方'
        # 找结构低点: 从 chan_data 推 (若无, 用 hub_low * 0.95)
        structure_low = chan_data.get('structure_low') or chan_data.get('lowest_low')
        if not structure_low:
            structure_low = round(hub_low * 0.95, 2)
        target_buy = structure_low
        # 1卖 = 中枢下沿 (反弹第一目标)
        target_sell = hub_low

    return {
        'hub_low': hub_low,
        'hub_high': hub_high,
        'target_buy': target_buy,
        'target_sell': target_sell,
        'price_position': pos,
    }


# ============================================================
# 因子单周期详细 (含价格)
# ============================================================

def _period_detail(
    level: str,                 # 'weekly' / 'daily' / '60min'
    weight: str,                # '1.5x' / '1.0x' / '0.5x'
    chan_str: str,              # 缠论背驰字符串 (周/日/60分)
    chan_data: dict,            # 中枢数据 (含 hub: {low, high, pos, valid})
    bsp: dict,                  # 买卖点 (含 0买/1买/1买⭐/2买/3买/1卖/1卖⭐/2卖/3卖/action)
    wyckoff: dict,              # 威科夫 (含 stage, stage_name, confidence)
    smc: dict,                  # SMC (含 nearest_bull_ob, nearest_bear_ob, nearest_fvg_bull, nearest_fvg_bear, recent_sweeps)
    vp: dict,                   # 量价 (含 fflow_net_3d/5d/30d, verdict, trend_*)
    res: dict,                  # 多市场共振 (含 direction, stock_ret_5d, sector_ret_5d)
    current_price: float,
) -> dict:
    """
    因子 × 单周期详细 (含价格)
    返回: 1 个周期的所有信息
    """
    # === 缠论 (含中枢 + 买卖点 + 价格) ===
    prices = _calc_buy_sell_prices(chan_data, bsp, wyckoff, current_price)
    chan_detail = {
        'stage': _parse_chan_stage(chan_str, current_price, prices),
        'beichi': chan_str,
        'hub': {
            'low': prices['hub_low'],
            'high': prices['hub_high'],
            'pos': chan_data.get('pos', '—') if chan_data else '—',
            'valid': chan_data.get('valid', False) if chan_data else False,
        },
        'buy_sell_points': {
            '0buy': bsp.get('🟢0买', '—') if bsp else '—',
            '1buy': bsp.get('🟢1买', '—') if bsp else '—',
            '1buy_trend': bsp.get('🟢1买⭐', '—') if bsp else '—',
            '2buy': bsp.get('🟢2买', '—') if bsp else '—',
            '3buy': bsp.get('🟢3买', '—') if bsp else '—',
            '1sell': bsp.get('🔴1卖', '—') if bsp else '—',
            '1sell_trend': bsp.get('🔴1卖⭐', '—') if bsp else '—',
            '2sell': bsp.get('🔴2卖', '—') if bsp else '—',
            '3sell': bsp.get('🔴3卖', '—') if bsp else '—',
            'action': bsp.get('action', '—') if bsp else '—',
        },
        'target_buy_price': prices['target_buy'],
        'target_sell_price': prices['target_sell'],
    }

    # === 威科夫 (v3 对齐 WyckoffTradingAgent 3 大阶段) ===
    wy_detail = {
        'stage': wyckoff.get('stage', '?') if wyckoff else '?',
        'stage_name': wyckoff.get('stage_name', '—') if wyckoff else '—',
        'stage_detail': wyckoff.get('stage_detail', '') if wyckoff else '',
        'confidence': wyckoff.get('confidence', 0) if wyckoff else 0,
        'action': wyckoff.get('action', '—') if wyckoff else '—',
    }

    # === SMC (含 OB/FVG/Sweep 价格) ===
    smc_detail = {
        'summary': smc.get('summary', '—') if smc else '—',
        'total_obs': smc.get('total_obs', 0) if smc else 0,
        'total_fvgs': smc.get('total_fvgs', 0) if smc else 0,
        'total_sweeps': len(smc.get('recent_sweeps', [])) if smc else 0,
        'nearest_bull_ob': _format_ob(smc.get('nearest_bull_ob', {})) if smc else None,
        'nearest_bear_ob': _format_ob(smc.get('nearest_bear_ob', {})) if smc else None,
        'nearest_fvg_bull': _format_fvg(smc.get('nearest_fvg_bull', {})) if smc else None,
        'nearest_fvg_bear': _format_fvg(smc.get('nearest_fvg_bear', {})) if smc else None,
    }

    # === 量价 ===
    # 2026-08-17 fix: 之前只抽 fflow 字段, OBV 段背离信息没进 volume_price_detail, _md_volume_price 拿不到
    volume_price_detail = {
        'verdict': vp.get('verdict', '—') if vp else '—',
        'fflow_3d': vp.get('fflow_net_3d', 0) if vp else 0,
        'fflow_5d': vp.get('fflow_net_5d', 0) if vp else 0,
        'fflow_30d': vp.get('fflow_net_30d', 0) if vp else 0,
        'fflow_60d': vp.get('fflow_net_60d', 0) if vp else 0,
        'trend_3d': vp.get('trend_3d', '—') if vp else '—',
        'trend_30d': vp.get('trend_30d', '—') if vp else '—',
        # OBV 段背离 (2026-08-17 OBV/fflow 拆分后加入)
        'obv_verdict':      vp.get('obv_verdict', '—') if vp else '—',
        'obv_div_bot_60d':  vp.get('obv_div_bot_60d', 0) if vp else 0,
        'obv_div_top_60d':  vp.get('obv_div_top_60d', 0) if vp else 0,
    }

    # === 多市场共振 ===
    # v5.10.2 改: 各周期读对应 stock_ret 字段 (5d 有 stock_ret_5d, 1d/20d 用 stock_ret)
    # 之前只读 stock_ret_5d, 1d/20d 周期永远 fallback 到 0 (bug 修)
    res_detail = {
        'direction': res.get('direction', '—') if res else '—',
        'stock_ret_5d': res.get('stock_ret_5d', res.get('stock_ret', 0)) if res else 0,
        'sector_ret_5d': res.get('sector_ret_5d', res.get('sector_ret', 0)) if res else 0,
    }

    # === 综合判定 ===
    composite = _composite_verdict(chan_detail, wy_detail, smc_detail, volume_price_detail, res_detail, prices, current_price)

    return {
        'level': level,
        'weight': weight,
        'chan': chan_detail,
        'wyckoff': wy_detail,
        'smc': smc_detail,
        'volume_price': volume_price_detail,
        'resonance': res_detail,
        'composite': composite,
    }


def _parse_chan_stage(chan_str: str, current_price: float, prices: dict) -> str:
    """解析缠论阶段 (基于买卖点 action + 中枢位置)"""
    if '趋势1买' in str(chan_str) or '1买⭐' in str(chan_str):
        return '🟢 趋势1买 (强底部反转)'
    if '1买' in str(chan_str):
        return '🟢 1买 (底背驰+分型)'
    if '2买' in str(chan_str):
        return '🟡 2买 (中枢下沿)'
    if '0买' in str(chan_str):
        return '🟡 0买 (超跌反弹)'
    if '趋势1卖' in str(chan_str) or '1卖⭐' in str(chan_str):
        return '🔴 趋势1卖 (强顶部反转)'
    if '1卖' in str(chan_str):
        return '🔴 1卖 (顶背驰+分型)'
    if '2卖' in str(chan_str):
        return '🟠 2卖 (中枢上沿)'
    if '底背驰' in str(chan_str) and '顶' not in str(chan_str):
        return '🟢 底背驰 (观察1买)'
    if '顶背驰' in str(chan_str):
        return '🔴 顶背驰 (观察1卖)'
    return '观望'


def _format_ob(ob: dict) -> Optional[dict]:
    """格式化 OB (含价格区间)"""
    if not ob:
        return None
    return {
        'low': ob.get('top', ob.get('low', 0)),  # 新字段 top, 兜底 low
        'high': ob.get('top', ob.get('high', 0)),
        'displacement_atr': ob.get('displacement_atr', ob.get('strength', 0)),
        'date': ob.get('date', '—'),
    }


def _format_fvg(fvg: dict) -> Optional[dict]:
    """格式化 FVG (含价格区间 + 回补进度)"""
    if not fvg:
        return None
    return {
        'low': fvg.get('bottom', fvg.get('low', 0)),
        'high': fvg.get('top', fvg.get('high', 0)),
        'mitigation_pct': fvg.get('mitigation_pct', 0),
        'size_atr': fvg.get('size_atr', 0),
        'date': fvg.get('date', '—'),
    }


def _composite_verdict(chan, wy, smc, vp, res, prices, current_price) -> dict:
    """
    综合判定: 因子投票, 输出最终建议
    """
    # 底部信号计数
    bottom_signals = sum([
        1 if chan.get('1buy_trend', '—') != '—' else 0,
        1 if chan.get('1buy', '—') != '—' else 0,
        1 if chan.get('2buy', '—') != '—' else 0,
        1 if wy.get('stage') == 'Accumulation' else 0,
        1 if smc.get('nearest_bull_ob') else 0,
        1 if smc.get('nearest_fvg_bull') else 0,
        1 if '进货' in str(vp.get('verdict', '')) else 0,
        1 if '跑赢' in str(res.get('direction', '')) or '正' in str(res.get('direction', '')) else 0,
    ])
    # 顶部信号计数
    top_signals = sum([
        1 if chan.get('1sell_trend', '—') != '—' else 0,
        1 if chan.get('1sell', '—') != '—' else 0,
        1 if chan.get('2sell', '—') != '—' else 0,
        1 if wy.get('stage') == 'Distribution' else 0,
        1 if smc.get('nearest_bear_ob') else 0,
        1 if smc.get('nearest_fvg_bear') else 0,
        1 if '出货' in str(vp.get('verdict', '')) else 0,
        1 if '跑输' in str(res.get('direction', '')) or '负' in str(res.get('direction', '')) else 0,
    ])

    if bottom_signals >= 4 and bottom_signals > top_signals:
        action = '🥇 强建仓'
        direction = 'long'
    elif bottom_signals >= 3 and bottom_signals > top_signals:
        action = '🥈 标准建仓'
        direction = 'long'
    elif top_signals >= 4 and top_signals > bottom_signals:
        action = '🔴 强减仓'
        direction = 'short'
    elif top_signals >= 3 and top_signals > bottom_signals:
        action = '🟠 标准减仓'
        direction = 'short'
    else:
        action = '🟡 观察'
        direction = 'neutral'

    return {
        'action': action,
        'direction': direction,
        'bottom_signals': bottom_signals,
        'top_signals': top_signals,
        'buy_target': prices.get('target_buy'),
        'sell_target': prices.get('target_sell'),
        'hub_low': prices.get('hub_low'),
        'hub_high': prices.get('hub_high'),
        'price_position': prices.get('price_position', '?'),
    }


# ============================================================
# 主函数: build_5method_matrix (可被其他 skill 调用)
# ============================================================

def build_factor_matrix(
    code: str,
    name: str,
    current_price: float,
    signals_5method: dict,
    chan_data: dict,
    buy_sell_points: dict,
) -> dict:
    """
    构建 因子 × 3 周期 综合矩阵 (2026-08-17 改名, 之前叫 build_5method_matrix)

    "5 方法" 历史命名已过时: 实际是 7 因子 (wyckoff/smc/chan/resonance/peg/dcf/fflow/obv)
    现在统一叫 "因子矩阵", 跟 AnalysisEngine Strategy 命名对齐.

    Args:
        code: 股票代码
        name: 股票名称
        current_price: 当前价
        signals_5method: analysis dict (AnalysisEngine 输出, 跟 signals_5method 字段兼容)
        chan_data: dump_data 中的 chan 字段 (含 weekly/daily/60min 各有 hub)
        buy_sell_points: dump_data 中的 buy_sell_points 字段 (含 weekly/daily/60min 各有 0买/1买/...)

    Returns:
        完整 因子矩阵 dict (可渲染报告, 也可被 watchlist/sector 调用)
    """
    s5 = signals_5method or {}
    chan_raw = chan_data or {}
    bsp_raw = buy_sell_points or {}

    # 因子 × 3 周期
    matrix = {}
    period_configs = [
        ('weekly', '1.5x'),
        ('daily', '1.0x'),
    ]
    for level, weight in period_configs:
        chan_period = chan_raw.get(level, {})
        bsp_period = bsp_raw.get(level, {})
        # 缠论背驰字符串 (在 s5['chan'] 里按 weekly/daily/60min 排)
        chan_str = (s5.get('chan') or {}).get(level, '—')

        # 威科夫: 周期对应键 (日线无后缀, 周/60分有)
        wy_key = {'weekly': 'wyckoff_weekly', 'daily': 'wyckoff'}[level]
        wyckoff = s5.get(wy_key) or s5.get('wyckoff') or {}

        # SMC: 同样
        smc_key = {'weekly': 'smc_weekly', 'daily': 'smc'}[level]
        smc = s5.get(smc_key) or s5.get('smc') or {}

        # 共振: weekly 有独立键
        res_key = {'weekly': 'resonance_weekly', 'daily': 'resonance'}[level]
        res = s5.get(res_key) or s5.get('resonance') or {}

        # 量价: 2026-08-17 拆分 fflow + obv 独立 strategy, 这里合成回 vp dict 给下游用
        # 老字段 s5.get('volume_price') 已废弃, 优先读 fflow, obv 信息合并到同 dict
        fflow_dict = s5.get('fflow') or {}
        obv_dict   = s5.get('obv')   or {}
        # 2026-08-17 fix: 之前 OBV 的 verdict/score 直接展开, 覆盖了 fflow 的 verdict/score (同 key 冲突)
        # OBV 段背离字段 (obv_div_bot_60d / obv_div_top_60d) 不冲突, 直接展开
        # OBV 的 verdict/score 加 obv_ 前缀避免冲突, _obv 子 dict 保留完整 OBV 信息
        vp = {
            **fflow_dict,                                  # 主力净流入字段 (fflow_net_3d/5d/10d/20d/30d/60d, verdict, trend_*)
            **{k: v for k, v in obv_dict.items() if k in ('obv_div_bot_60d', 'obv_div_top_60d')},  # 不冲突
            'obv_verdict': obv_dict.get('verdict', '—'),  # 避免覆盖 fflow.verdict
            'obv_score':   obv_dict.get('score', 0),      # 避免覆盖 fflow.score
            # 兼容老 s5.get('volume_price') 调用方
            '_fflow': fflow_dict,
            '_obv':   obv_dict,
        }

        matrix[level] = _period_detail(
            level=level,
            weight=weight,
            chan_str=chan_str,
            chan_data=chan_period,
            bsp=bsp_period,
            wyckoff=wyckoff,
            smc=smc,
            vp=vp,
            res=res,
            current_price=current_price,
        )

    return {
        'code': code,
        'name': name,
        'current_price': current_price,
        'scene': s5.get('scene', '?'),
        'scene_name': s5.get('scene_name', '未知'),
        'resonance_count': s5.get('resonance_count', 0),
        'action': s5.get('action', '—'),
        'matrix': matrix,
    }


# ============================================================
# 便捷函数: 给 watchlist / sector 用
# ============================================================

def get_buy_recommendation(matrix_result: dict) -> Optional[dict]:
    """
    从 因子矩阵提取"建议买入价"
    优先级: 日线 composite.buy_target > 周线 > 60分
    """
    for level in ['daily', 'weekly']:
        comp = matrix_result['matrix'][level].get('composite', {})
        if comp.get('direction') == 'long' and comp.get('buy_target'):
            return {
                'price': comp['buy_target'],
                'level': level,
                'action': comp['action'],
                'bottom_signals': comp.get('bottom_signals', 0),
            }
    return None


def get_sell_recommendation(matrix_result: dict) -> Optional[dict]:
    """
    从 因子矩阵提取"建议卖出价"
    优先级: 日线 composite.sell_target > 周线
    """
    for level in ['daily', 'weekly']:
        comp = matrix_result['matrix'][level].get('composite', {})
        if comp.get('direction') == 'short' and comp.get('sell_target'):
            return {
                'price': comp['sell_target'],
                'level': level,
                'action': comp['action'],
                'top_signals': comp.get('top_signals', 0),
            }
    return None


# ============================================================
# Markdown 渲染 (给 _section_factor_matrix 调用)
# ============================================================

def render_factor_matrix_md(matrix_result: dict) -> str:
    """
    渲染 因子 × 3 周期 矩阵为 Markdown (报告用, 2026-08-17 改名 render_5method_matrix_md)

    注意: 标题由 render_report 主模板输出, 这里只输出内容
    """
    m = matrix_result['matrix']
    code = matrix_result['code']
    name = matrix_result['name']
    price = matrix_result['current_price']

    md = []
    md.append(f"**股票**: {code} {name} ¥{price:.2f}")
    md.append(f"**场景**: {matrix_result['scene']} ({matrix_result['scene_name']}) | "
              f"**共振数**: {matrix_result['resonance_count']} 重 | "
              f"**行动**: {matrix_result['action']}\n")

    # 因子 × 3 周期 (含价格)
    md.append("**🎯 因子 × 2 周期 (含中枢 + 123 买卖点 + 建议价格):**\n")
    md.append("| 维度 | 周线 (1.5x) | 日线 (1.0x) |")
    md.append("|---|---|---|")

    md.append("| **缠论 (中枢+买卖点)** | "
              f"{_md_chan(m['weekly']['chan'])} | "
              f"{_md_chan(m['daily']['chan'])} |")
    md.append("| **威科夫 (3 大阶段)** | "
              f"{_md_wy(m['weekly']['wyckoff'])} | "
              f"{_md_wy(m['daily']['wyckoff'])} |")
    md.append("| **SMC (OB/FVG/Sweep)** | "
              f"{_md_smc(m['weekly']['smc'])} | "
              f"{_md_smc(m['daily']['smc'])} |")
    md.append("| **量价 (fflow+OBV)** | "
              f"{_md_volume_price(m['weekly']['volume_price'])} | "
              f"{_md_volume_price(m['daily']['volume_price'])} |")
    md.append("| **多市场共振** | "
              f"{_md_res(m['weekly']['resonance'])} | "
              f"{_md_res(m['daily']['resonance'])} |")
    md.append("| **🎯 综合判定** | "
              f"{_md_composite(m['weekly']['composite'])} | "
              f"{_md_composite(m['daily']['composite'])} |")
    md.append("")

    # 实战建议 (取日线 composite)
    daily_comp = m['daily']['composite']
    pos = daily_comp.get('price_position', '?')
    pos_label = {
        'A_上方': '🟢 在中枢上方 (健康, 持有)',
        'B_内部': '🟡 在中枢内部 (震荡, 等方向)',
        'C_下方': '🟠 在中枢下方 (跌穿, 关注止跌)',
        '无中枢': '⚪ 中枢未形成',
        '中枢无效': '⚪ 中枢无效',
    }.get(pos, pos)
    md.append(f"**💰 实战建议 (日线):**")
    md.append(f"- 行动: **{daily_comp['action']}**")
    md.append(f"- 价格位置: {pos_label}")
    if daily_comp.get('buy_target'):
        if pos == 'C_下方':
            md.append(f"- 建议买入价: **¥{daily_comp['buy_target']:.2f}** (结构低点, 价格已穿中枢)")
        else:
            md.append(f"- 建议买入价: **¥{daily_comp['buy_target']:.2f}** (中枢下沿, 底背驰/分型确认)")
    if daily_comp.get('sell_target'):
        if pos == 'C_下方':
            md.append(f"- 建议卖出价: **¥{daily_comp['sell_target']:.2f}** (中枢下沿, 反弹第一目标)")
        else:
            md.append(f"- 建议卖出价: **¥{daily_comp['sell_target']:.2f}** (中枢上沿, 顶背驰/分型确认)")
    md.append(f"- 中枢区间: ¥{daily_comp['hub_low']:.2f} ~ ¥{daily_comp['hub_high']:.2f}" if daily_comp.get('hub_low') and daily_comp.get('hub_high') else "- 中枢未形成")

    return "\n".join(md)


def _md_chan(chan: dict) -> str:
    """缠论行 markdown"""
    bsp = chan.get('buy_sell_points', {})
    action = bsp.get('action', '—')
    hub = chan.get('hub', {})
    hub_str = f"中枢¥{hub.get('low', 0):.0f}-¥{hub.get('high', 0):.0f}" if hub.get('valid') else "无中枢"
    # 简化: 找 123 买卖点中第一个触发的
    triggered = []
    for k in ['1buy_trend', '1buy', '2buy', '0buy', '1sell_trend', '1sell', '2sell']:
        v = bsp.get(k, '—')
        if v != '—' and isinstance(v, str) and '¥' in v:
            triggered.append(f"{k}={v.split(' ')[0]}")
    bsp_str = ', '.join(triggered[:2]) if triggered else '无'
    return f"{chan.get('stage', '—')[:20]} / {hub_str} / 买卖点: {bsp_str}"


def _md_wy(wy: dict) -> str:
    """威科夫行 markdown"""
    return f"{wy.get('stage', '?')}/{wy.get('stage_detail', '') or '—'} ({wy.get('confidence', 0)}%)"


def _md_smc(smc: dict) -> str:
    """SMC 行 markdown (带价格)"""
    parts = []
    bull_ob = smc.get('nearest_bull_ob')
    if bull_ob:
        parts.append(f"多OB¥{bull_ob['low']:.0f}-¥{bull_ob['high']:.0f}")
    bear_ob = smc.get('nearest_bear_ob')
    if bear_ob:
        parts.append(f"空OB¥{bear_ob['low']:.0f}-¥{bear_ob['high']:.0f}")
    if smc.get('total_sweeps', 0) > 0:
        parts.append(f"扫流×{smc['total_sweeps']}")
    return ' / '.join(parts) if parts else '无'


def _md_volume_price(vp: dict) -> str:
    """量价行 markdown (2026-08-17 拆分后: fflow 主力净流入 + OBV 段背离)

    显示: fflow 3d/30d 主力净流入 + fflow verdict, OBV 段背离 (底/顶/无)
    """
    v3 = vp.get('fflow_3d', 0) or 0
    v30 = vp.get('fflow_30d', 0) or 0
    verdict = vp.get('verdict', '—')[:16]  # fflow verdict
    # OBV 段背离 (2026-08-17 新加)
    div_bot = vp.get('obv_div_bot_60d', 0) or 0
    div_top = vp.get('obv_div_top_60d', 0) or 0
    obv_part = ""
    if div_bot >= 2:   obv_part = f" OBV强底×{div_bot}/4"
    elif div_top >= 2: obv_part = f" OBV强顶×{div_top}/4"
    elif div_bot == 1: obv_part = " OBV底×1"
    elif div_top == 1: obv_part = " OBV顶×1"
    return f"3d:{v3:+.1f}亿 / 30d:{v30:+.1f}亿 / {verdict}{obv_part}"


def _md_res(res: dict) -> str:
    """共振行 markdown"""
    return f"{res.get('direction', '—')} 个股{res.get('stock_ret_5d', 0):+.1f}%/板块{res.get('sector_ret_5d', 0):+.1f}%"


def _md_composite(comp: dict) -> str:
    """综合判定行 markdown"""
    action = comp.get('action', '—')
    bot = comp.get('bottom_signals', 0)
    top = comp.get('top_signals', 0)
    buy = comp.get('buy_target')
    sell = comp.get('sell_target')
    parts = [action]
    if buy:
        parts.append(f"买¥{buy:.0f}")
    if sell:
        parts.append(f"卖¥{sell:.0f}")
    parts.append(f"底{bot}/顶{top}")
    return ' / '.join(parts)
