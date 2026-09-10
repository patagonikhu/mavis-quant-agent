"""
tools/factors/__init__.py - 因子库入口 (2026-09-09 简化)

2026-09-09 改动:
  - 删了 Factor 基类 (Factor) + Registry (FactorRegistry/Runner/StandardFactorSets/print_registry)
  - 删了 FactorConfig (yaml 配置)
  - 删了 FactorMeta
  - 全部改成纯函数, 统一在 3 个文件:
    - factor_basic.py  (基础通用: ma / returns / position / 3 层仓位 / alpha)
    - factor_risk.py   (风控: 止盈止损 / 退出信号 / 监控触发)
    - factor_volume.py (量能: fflow / OBV)
    - valuation/factor_lib.py (估值: ROC/EY/PEG/DCF/Magic 等 11 个)
  - 保留框架: utils.py (K 线处理) + kline_arrays.py (滑动窗口)
  - 保留专用: chan/ smc/ wyckoff/ (各自保留 class Factor 风格)
  - 保留: tools/factors/valuation/dcf_engine.py (DCF 假设工具)

Mavis 守门员 (t-guardrail::factor-guard) 规则:
  - ❌ 禁止 class XXX(Factor) 包装 (在 valuation/ 目录)
  - ❌ 禁止 tools/analysis/valuation.py 复活
  - ❌ 禁止 tools/factors/valuation/ 多文件 (除 factor_lib.py + dcf_engine.py)
"""
__version__ = '2.0.0'  # 2026-09-09 纯函数化 + 删框架
