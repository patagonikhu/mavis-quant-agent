"""
因子 × 2 周期 矩阵 (威科夫对照)

2026-11-06 缠论整层下线 (用户指令) 后大幅简化。

原来 3 行:
    缠论 (中枢 + 买卖点 + 背驰)  ← czsc 产物
    威科夫 (3 大阶段)
    综合判定 (投票: chan 票 + wyckoff 票, 阈值 底>=2 / 顶>=2)

现在 1 行:
    威科夫 (3 大阶段) × 周/日 2 周期

删掉的东西及原因:
  - 缠论行: 中枢 / 买卖点 / 背驰 全是 czsc 产物 → 缠论整层下线
  - 综合判定行 (投票): 2026-11-06 前投票 = 2 类票 (chan + wyckoff), 阈值 底>=2 / 顶>=2。
    删掉 chan 后只剩 1 票 → 阈值恒不可达 (全部退化成「🟡 观察」)。
    按用户指令**整块删除投票逻辑**, 不是把阈值降到 1 ——
    降阈值会让「任何威科夫 Accumulation 都算建仓建议」, 属另一次决策, 不在本次范围。
    报告里的方向信号改由威科夫阶段转变 (A→M 买 / M→D 卖) + 子事件 + MA 偏离 提供
    (见 tools/analysis/analysis_result_signals.py: extract_signals)。
  - 实战建议 (建议买入价 / 建议卖出价 / 中枢区间): 全部基于缠论中枢上下沿 → 无中枢即无建议价

输出: {
    "code", "name", "current_price",
    "matrix": {"weekly": {...}, "daily": {...}},
}
"""
from typing import Dict


# ============================================================
# 因子单周期详细 (威科夫)
# ============================================================

def _period_detail(
    level: str,                 # 'weekly' / 'daily'
    weight: str,                # '1.5x' / '1.0x'
    wyckoff: dict,              # 威科夫 (含 stage, stage_name, confidence)
) -> dict:
    """
    因子 × 单周期详细 (威科夫)
    返回: 1 个周期的威科夫信息
    """
    wy_detail = {
        'stage': wyckoff.get('stage', '?') if wyckoff else '?',
        'stage_name': wyckoff.get('stage_name', '—') if wyckoff else '—',
        'stage_detail': wyckoff.get('stage_detail', '') if wyckoff else '',
        'confidence': wyckoff.get('confidence', 0) if wyckoff else 0,
        'action': wyckoff.get('action', '—') if wyckoff else '—',
    }

    return {
        'level': level,
        'weight': weight,
        'wyckoff': wy_detail,
    }


# ============================================================
# 主函数: build_factor_matrix (可被其他 skill 调用)
# ============================================================

def build_factor_matrix(
    code: str,
    name: str,
    current_price: float,
    signals_5method: dict,
) -> dict:
    """
    构建 因子 × 2 周期 矩阵 (威科夫对照)

    2026-11-06 缠论整层下线: 去掉 chan_data / buy_sell_points 两个入参
      (原来这两个参数喂中枢 + 买卖点 + 缠论投票)

    Args:
        code: 股票代码
        name: 股票名称
        current_price: 当前价
        signals_5method: analysis dict (AnalysisEngine 输出)

    Returns:
        完整 因子矩阵 dict (可渲染报告)
    """
    s5 = signals_5method or {}

    # 因子 × 2 周期
    matrix = {}
    period_configs = [
        ('weekly', '1.5x'),
        ('daily', '1.0x'),
    ]
    for level, weight in period_configs:
        # 威科夫: 按周期取**真值**
        # ⚠️ 2026-11-05 修 BUG (既有): 原来 weekly 取 s5.get("wyckoff_weekly"),
        #   但 analysis dict 里**从来没有** wyckoff_weekly 这个顶层 key
        #   (它只是 RawContext 上的字段, 没进 raw) → 永远 fallback 到
        #   s5["wyckoff"] = **日线**结果 → 矩阵的周线列显示日线阶段/置信度。
        #   真源: s5["wyckoff"]["3period"][level] (analysis_engine.py)。
        _wy_root = s5.get('wyckoff') or {}
        if level == 'daily':
            wyckoff = _wy_root
        else:
            wyckoff = (_wy_root.get('3period') or {}).get(level) or {}

        matrix[level] = _period_detail(
            level=level,
            weight=weight,
            wyckoff=wyckoff,
        )

    return {
        'code': code,
        'name': name,
        'current_price': current_price,
        'matrix': matrix,
    }


# ============================================================
# Markdown 渲染 (给 _section_factor_matrix 调用)
# ============================================================

def render_factor_matrix_md(matrix_result: dict) -> str:
    """
    渲染 因子 × 2 周期 矩阵为 Markdown (报告用)

    注意: 标题由 render_report 主模板输出, 这里只输出内容
    """
    m = matrix_result['matrix']
    code = matrix_result['code']
    name = matrix_result['name']
    price = matrix_result['current_price']

    md = []
    md.append(f"**股票**: {code} {name} ¥{price:.2f}\n")

    md.append("**🎯 因子 × 2 周期 (威科夫 3 大阶段):**\n")
    md.append("| 维度 | 周线 (1.5x) | 日线 (1.0x) |")
    md.append("|---|---|---|")
    md.append("| **威科夫 (3 大阶段)** | "
              f"{_md_wy(m['weekly']['wyckoff'])} | "
              f"{_md_wy(m['daily']['wyckoff'])} |")
    md.append("")

    return "\n".join(md)


def _md_wy(wy: dict) -> str:
    """威科夫行 markdown"""
    return f"{wy.get('stage', '?')}/{wy.get('stage_detail', '') or '—'} ({wy.get('confidence', 0)}%)"
