# 报告格式 v3.0 完整规则 (2026-07-21 固化)

> 拆自 CLAUDE.md (2026-08-06)
> 任何 `/t-analyze` / `/t-trigger` / `/t-watchlist` / `/t-monitor` 输出的 md 报告必须遵守

## 22 section 必填 (缺数据不删标题)

```
## 📊 数据完整性           ← 头部表格, 实时扫描 section 状态
## EPS + 财务数据
## MA 均线
## 📊 技术指标 (8 种) ⭐   ← MACD/RSI/KDJ/BOLL/ATR/量比 (Wilder 公式)
## 🟢 主力分析 (fflow)
## 🚨 60 分钟级背驰信号
## 📐 缠论完整数据 (4 个级别)  ← 保留原报告 130 行缠论数据
## 🎯 投资四问
## ⏰ T 框架
## 💰 PEG 实算
## 📊 DCF L 实算
## 🚨 5 类 14 子信号
## 🤖 XGBoost 校准
## 📈 板块过热预警
## 🎯 止盈 3 层
## 🛑 止损 4 档
## 🟢 退场信号检查
## 📋 3 层仓位策略
## 🔍 缠论补充 (4 方法) ⭐  ← SMC + 量价 + 共振 + 威科夫
## 🎯 5 方法 × 3 周期 矩阵 (硬保证) ⭐⭐  ← 场景判定 + 共振数 + 行动建议, 缺则 Linter FAIL
## 📌 监控触发点
## 🔍 Linter 校验报告       ← 自动校验, 幂等替换
```

## 三阶段工作流 (强制)

- **Phase 1 (Python 工具):** `enhance_report.py` 拉数据 + 模板 → 4 项基本数据 + 占位符
- **Phase 2 (LLM):** 套框架计算 + Edit 填实数据 → 14 项分析
- **Phase 3 (Linter):** `report_linter.py` 校验 → 18 section 必填 + 11 关键数据点正则

## 幂等性 (铁律)

- **`enhance_report.py`**: 跑 N 次 = 22 section 唯一, 不重复
- **`report_linter.py`**: 检测已有 Linter 块, 替换而非追加
- **完整性表**: 实时扫描 section 实际内容, 跟实际填的数据 100% 同步

## 工具调用 (统一封装)

```bash
# 增强已有报告 (推荐, 非破坏性)
bash tools/with_venv.sh python3 tools/enhance_report.py docs/analyze-{code}-{name}.md {板块名}

# 校验报告
bash tools/with_venv.sh python3 tools/render/report_linter.py docs/analyze-{code}-{name}.md

# 批量校验
bash tools/with_venv.sh python3 tools/render/report_linter.py all
```
