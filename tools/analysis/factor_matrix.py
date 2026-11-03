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
    "action",
    "matrix": {"weekly"/"daily"/"60min": {5方法 + 建议价}},
    "top_warning": {"weekly"/"daily"/"60min": "强/标准/弱/否"},
    "bottom_signal": {"weekly"/"daily"/"60min": "1买⭐/1买/2买/3买/否"},
}
(2026-08-29: 删 scene/scene_name/resonance_count, 硬编码 if-else 不准)
"""
from typing import Dict, List, Tuple


# ============================================================
# 123 买卖点判定 (从 buy_sell_points 数据)
# ============================================================

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
            'pos': (chan_data.get('hub', {}) or {}).get('pos', '—') if chan_data else '—',
            'valid': prices['hub_low'] is not None and prices['hub_high'] is not None,
        },
        'buy_sell_points': {
            # v3.0: bsp 直接 spread 保留所有 czsc_signals 出的 key (不再 strict 映射)
            **({} if not bsp else {k: bsp.get(k, '—') for k in bsp}),
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

    # === 综合判定 ===
    composite = _composite_verdict(chan_detail, wy_detail, prices, current_price)

    return {
        'level': level,
        'weight': weight,
        'chan': chan_detail,
        'wyckoff': wy_detail,
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


def _composite_verdict(chan, wy, prices, current_price) -> dict:
    """
    综合判定: 因子等权投票 (2026-09-09 改: 删 chan 隐式 3 票优势, 各 1 票)

    原设计: chan 给 1buy_trend/1buy/2buy 3 票, 其余各 1 票 (chan 3/8 隐式权重)
    改后: 各因子 1 票等权, 决策更分散

    ⚠️ 2026-10-27 修 chan 票 bug (实测 38 只核心票发现):
      旧代码读 chan.get('1buy_trend') / chan.get('1buy'),
      但 _period_detail 产出的 chan_detail 只有 'stage'/'beichi'/'hub'/'buy_sell_points',
      真正的买卖点在 buy_sell_points 嵌套里 (key 带 emoji 前缀, 如 '🟢1买' / '🔴1卖').
      结果: 缠论票从来没投过 (605189/300699/002475 都有真实 1买, chan_buy 仍为 False).
      修法: chan 票改从 buy_sell_points 读 (兼容 emoji 前缀), 修好后 chan 票才真正生效.

    ⚠️ 2026-10-28 删 vp (量价) 票:
      原 5 类投票含 fflow 票, 但 fflow parquet 落后 K 线 29 天时旧代码仍按"当前主力动向"计票.
      实测 14 只票 fflow 判出货, 其中 13 只被同时点 (K线派生, 数据新鲜) 的量价指标判为偏进货.
      方向完全相反 = 数据陈旧造成的系统性看空偏差, 不是市场信号.
      27 只核心票对照实验: 删 fflow 票后 3 只判定变化, 全部是 fflow 制造的假建仓/假减仓.
      阈值同步下调一档 (>=3 / >=2).

    ⚠️ 2026-11-04 删 OB/FVG 那一票:
      该票在 212 个周期行实测中只单独贡献过 2 行 (且都是 FVG 看涨), 12 个"底 2"全部来自
      缠论买 + 威科夫 Accumulation → 删除对任何 action 零影响.

    ⚠️ 2026-11-05 删 res (多市场共振) 那一票:
      resonance_3period 网络调用早已删除, res 恒为空 dict → 该票恒投 0, 对判定零影响.
      既然恒 0, 报告里再展示「多市场共振: 已移除」占位也只是噪音 → 连展示层一起删.
      现为 2 类票 (chan/wyckoff), 最高票数 = 2, 阈值不变 (底>=2 / 顶>=2).
      原「🥇 强建仓」(>=3) / 「🔴 强减仓」(>=3) 两档随第三票一起删除 (本就不可达).
      如将来恢复共振数据, 需重新加回 res 参数 + res 票 + >=3 分支.
    """
    # === chan 票 (2026-10-27 修: 从 buy_sell_points 读, 兼容 emoji 前缀) ===
    bsp = chan.get('buy_sell_points') or {}
    bsp_keys = [str(k) for k in bsp.keys() if k != 'action']

    def _has(*tokens):
        return any(any(t in k for t in tokens) for k in bsp_keys)

    chan_buy = (
        _has('1买', '2买', '3买', '1buy_trend', '趋势1买')
        or chan.get('1buy_trend', '—') != '—'
        or chan.get('1buy', '—') != '—'
        or chan.get('2buy', '—') != '—'
    )
    chan_sell = (
        _has('1卖', '2卖', '3卖', '1sell_trend', '趋势1卖')
        or chan.get('1sell_trend', '—') != '—'
        or chan.get('1sell', '—') != '—'
        or chan.get('2sell', '—') != '—'
    )

    # 底部信号计数 (2 类各 1 票)
    bottom_signals = sum([
        1 if chan_buy else 0,
        1 if wy.get('stage') == 'Accumulation' else 0,
    ])
    # 顶部信号计数 (2 类各 1 票)
    top_signals = sum([
        1 if chan_sell else 0,
        1 if wy.get('stage') == 'Distribution' else 0,
    ])

    if bottom_signals >= 2 and bottom_signals > top_signals:
        action = '🥈 标准建仓'
        direction = 'long'
    elif top_signals >= 2 and top_signals > bottom_signals:
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

    "5 方法" 历史命名已过时: 实际是 2 因子 (wyckoff/chan)
    (2026-11-05 删 res/多市场共振 — resonance_3period 网络调用早已删除, 该票恒为 0)
    现在统一叫 "因子矩阵", 跟 AnalysisEngine Strategy 命名对齐.

    Args:
        code: 股票代码
        name: 股票名称
        current_price: 当前价
        signals_5method: analysis dict (AnalysisEngine 输出, 跟 signals_5method 字段兼容)
        chan_data: 老 data 工具 中的 chan 字段 (含 weekly/daily/60min 各有 hub)
        buy_sell_points: 老 data 工具 中的 buy_sell_points 字段 (含 weekly/daily/60min 各有 0买/1买/...)

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

        # 威科夫: 按周期取**真值**
        # ⚠️ 2026-11-05 修 BUG (既有): 原来 weekly 取 s5.get("wyckoff_weekly"),
        #   但 analysis dict 里**从来没有** wyckoff_weekly 这个顶层 key
        #   (它只是 RawContext 上的字段, 没进 raw) → 永远 fallback 到
        #   s5["wyckoff"] = **日线**结果 → 矩阵的周线列显示日线阶段/置信度。
        #   真源: s5["wyckoff"]["3period"][level] (analysis_engine.py L236)。
        _wy_root = s5.get('wyckoff') or {}
        if level == 'daily':
            wyckoff = _wy_root
        else:
            wyckoff = (_wy_root.get('3period') or {}).get(level) or {}

        matrix[level] = _period_detail(
            level=level,
            weight=weight,
            chan_str=chan_str,
            chan_data=chan_period,
            bsp=bsp_period,
            wyckoff=wyckoff,
            current_price=current_price,
        )

    return {
        'code': code,
        'name': name,
        'current_price': current_price,
        'action': s5.get('action', '—'),
        'matrix': matrix,
    }


# ============================================================
# 便捷函数: 给 watchlist / sector 用
# ============================================================

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
    md.append(f"**行动**: {matrix_result['action']}\n")

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
    # 兼容老 key (1buy/2buy/0buy) 和新 emoji key (🟢1买/🟢3买/🟢双中枢/🟢笔结束)
    # 优先显示新 key (双中枢/笔结束/3买) 在前
    triggered = []
    for k in ['double_zs', 'bi_end', 'macd_bc_bot',  # 新 (czsc)
              '3buy', '1buy_trend', '1buy', '2buy', '0buy',
              '1sell_trend', '1sell', '2sell', '3sell',
              '🟢双中枢', '🟢笔结束', '🟢MACD底背', '🟢3买',
              '🟢1买', '🟢1买⭐', '🟢2买', '🟢0买',
              '🔴1卖', '🔴1卖⭐', '🔴2卖', '🔴3卖',
              # czsc_signals.py 出的 K线形态
              '🟢吞没', '🟢孕线', '🟢三法', '🟢反击线', '🟢山川', '🟢分手线',
              '🟢DIF走平', '🟢MACD开仓',
              '🔴三只乌鸦', '🔴两只乌鸦', '🔴塔形']:
        v = bsp.get(k, '—')
        if v != '—' and isinstance(v, str) and '¥' in v:
            triggered.append(f"{k}={v.split(' ')[0]}")
    bsp_str = ', '.join(triggered[:2]) if triggered else '无'
    return f"{chan.get('stage', '—')[:20]} / {hub_str} / 买卖点: {bsp_str}"


def _md_wy(wy: dict) -> str:
    """威科夫行 markdown"""
    return f"{wy.get('stage', '?')}/{wy.get('stage_detail', '') or '—'} ({wy.get('confidence', 0)}%)"


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
