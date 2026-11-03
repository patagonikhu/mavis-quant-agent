"""
tools/factors/__init__.py - 因子库入口 (2026-09-09 简化)

2026-09-09 改动:
  - 删了 Factor 基类 (Factor) + Registry (FactorRegistry/Runner/StandardFactorSets/print_registry)
  - 删了 FactorConfig (yaml 配置)
  - 删了 FactorMeta
  - 全部改成纯函数, 统一在 1 个文件:
    - valuation/factor_lib.py (估值: ROC/EY/Magic 等)
  - 2026-10-28 删: factor_volume.py (量能: fflow) — 主力资金流维度整体删除
  - 2026-11-03 删: factor_basic.py (3 层仓位策略整档已删后该文件无任何函数,
    其余因子早已迁走, 直接删除整个模块)
  - 2026-11-03 删: factor_risk.py (监控触发点整档已删后该文件只剩
    compute_monitor_triggers 一个函数, 直接删除整个模块)
  - 2026-11-05 删: structure/ (原 smc/, 结构分析 OB/FVG/Sweep) —
    整档从 analysis 层删除后无任何引用, 因子库整目录移除
  → 至此 tools/factors/ 顶层只剩 utils.py + kline_arrays.py
  - 保留框架: utils.py (K 线处理) + kline_arrays.py (滑动窗口)
  - 保留专用: chan/ wyckoff/ (各自保留 class Factor 风格)

Mavis 守门员 (t-guardrail::factor-guard) 规则:
  - ❌ 禁止 class XXX(Factor) 包装 (在 valuation/ 目录)
  - ❌ 禁止 tools/analysis/valuation.py 复活
  - ❌ 禁止 tools/factors/valuation/ 多文件 (只允许 factor_lib.py)
  - 2026-11-02: dcf_engine.py 已删 (DCF 整档删除后完全没人用)
"""
__version__ = '2.0.0'  # 2026-09-09 纯函数化 + 删框架
