"""
report_schema.py — 报告格式定义 (单一真源, 不可变)

设计原则:
  1. 报告所有 section 在 SCHEMA 里硬编码 — 改报告 = 改 SCHEMA
  2. 报告顺序在 SCHEMA 里硬编码 — 顺序不会变
  3. section 标题在 SCHEMA 里硬编码 — 不会拼写错
  4. section 内容从 JSON 渲染 — 数据不会丢
  5. linter 强校验 — 缺一个就报错

CLAUDE.md 红字硬约束 (2026-07-20 固化):
  1️⃣ 缠论三要素 (中枢+背驰+止跌) — 一等公民, 最前
  2️⃣ 4 个缠论补充策略 (SMC-OB+量价+威科夫+多市场共振) — 一等公民
  3️⃣ 市场状态定量 (三指标 0-9 分 / 板块过热) — 二等
  4️⃣ 大盘+美股背景 — 二等 (可跳过)
  5️⃣ PEG/DCF L (基本面对冲) — 二等, 必须在 1️⃣2️⃣ 之后
  6️⃣ 主力 fflow — 验证
  7️⃣ 三层仓位+买卖点 — 综合 (止盈/止损/退场/仓位/监控)

refresh 字段说明 (2026-07-24 加入, 方案 B 核心):
  "REGEN"    — 每次 enhance 强制重新生成 (数据驱动, 不保留旧内容)
               适用: 实时数据 (fflow/MA/技术指标), 或之前有单位 bug 的 section
  "PRESERVE" — 保留原报告内容, 只在缺失时补充占位符
               适用: LLM 填充的分析 (缠论三要素/四问/T框架)
"""

# 报告 section 顺序 (硬编码, 不可改)
# 严格按 CLAUDE.md 1️⃣-7️⃣ 顺序
REPORT_SECTIONS = [
    # === 头部 (数据驱动, 每次重算) ===
    {
        "id": "data_completeness",
        "title": "📊 数据完整性 (开篇即知, 一目了然)",
        "category": "📋",
        "render": "render_data_completeness",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "eps_finance",
        "title": "EPS + 财务数据",
        "category": "📋",
        "render": "render_eps_finance",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ma_indicators",
        "title": "MA 均线",
        "category": "📋",
        "render": "render_ma",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "tech_indicators",
        "title": "📊 技术指标 (8 种) ⭐",
        "category": "📋",
        "render": "render_tech_indicators",
        "required": True,
        "refresh": "REGEN",
    },
    # 2026-07-25: 5 方法 × 3 周期 矩阵 (独立 section, 整合原 5 合 1 顶部预警)
    # 注: chan_signals / chan_supplement 已于 7-29 合并入 factor_history + method_matrix, 废弃删除
    {
        "id": "method_matrix",
        "title": "🎯 5 方法 × 3 周期 综合矩阵 (2026-07-25 合并: 整合原 5 合 1 顶部预警)",
        "category": "2️⃣",
        "render": "render_method_matrix",
        "required": True,
        "refresh": "REGEN",
    },
    # === 3️⃣ 市场状态定量 / 板块 ===
    {
        "id": "sector_overheat",
        "title": "📈 板块过热预警",
        "category": "3️⃣",
        "render": "render_sector_overheat",
        "required": True,
        "refresh": "REGEN",
    },
    # === 4️⃣ 大盘+美股背景 (可跳过) ===
    {
        "id": "market_context",
        "title": "🌍 大盘 + 美股背景",
        "category": "4️⃣",
        "render": "render_market_context",
        "required": False,
        "refresh": "REGEN",
    },
    # === 5️⃣ PEG/DCF L (必须在 1️⃣2️⃣ 之后) ===
    {
        "id": "peg",
        "title": "💰 PEG 实算",
        "category": "5️⃣",
        "render": "render_peg",
        "required": True,
        "refresh": "PRESERVE",  # LLM 填充, 保留
    },
    {
        "id": "dcf",
        "title": "📊 DCF L 实算",
        "category": "5️⃣",
        "render": "render_dcf",
        "required": True,
        "refresh": "PRESERVE",  # LLM 填充, 保留
    },
    # === 基本面 + 策略 (数据驱动) ===
    {
        "id": "fundamental",
        "title": "💎 基本面 (4 维) — 自动评估",
        "category": "📋",
        "render": "render_fundamental",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "strategy",
        "title": "🎯 4 套交易策略 — 自动评估",
        "category": "📋",
        "render": "render_strategy",
        "required": True,
        "refresh": "REGEN",
    },
    # === 投资四问 + T 框架 (LLM 填充, 保留) ===
    {
        "id": "four_questions",
        "title": "🎯 投资四问",
        "category": "📋",
        "render": "render_four_questions",
        "required": True,
        "refresh": "PRESERVE",
    },
    {
        "id": "t_frame",
        "title": "⏰ T 框架",
        "category": "📋",
        "render": "render_t_frame",
        "required": True,
        "refresh": "PRESERVE",
    },
    {
        "id": "five_categories",
        "title": "🚨 5 类 14 子信号",
        "category": "📋",
        "render": "render_five_categories",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "xgboost",
        "title": "🤖 XGBoost 校准",
        "category": "📋",
        "render": "render_xgboost",
        "required": True,
        "refresh": "REGEN",
    },
    # === 7️⃣ 三层仓位 + 买卖点 (数据驱动) ===
    {
        "id": "stop_profit",
        "title": "🎯 止盈 3 层",
        "category": "7️⃣",
        "render": "render_stop_profit",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "stop_loss",
        "title": "🛑 止损 4 档",
        "category": "7️⃣",
        "render": "render_stop_loss",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "exit_signals",
        "title": "🟢 退场信号检查",
        "category": "7️⃣",
        "render": "render_exit_signals",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "three_layer_position",
        "title": "📋 3 层仓位策略",
        "category": "7️⃣",
        "render": "render_three_layer_position",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "monitor_triggers",
        "title": "📌 监控触发点",
        "category": "7️⃣",
        "render": "render_monitor_triggers",
        "required": True,
        "refresh": "REGEN",
    },
    # === Tushare 补充数据 (数据驱动) ===
    {
        "id": "data_sources",
        "title": "📡 数据源矩阵 (类型/主源/备源/状态) — 固化不丢",
        "category": "📋",
        "render": "render_data_sources_matrix",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_basic",
        "title": "📊 基础信息 (Tushare)",
        "category": "📋",
        "render": "render_ts_basic",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_pe_pb",
        "title": "💹 PE / PB / 市值 (Tushare daily_basic)",
        "category": "📋",
        "render": "render_ts_pe_pb",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_weekly_monthly",
        "title": "📈 周线 / 月线 K (Tushare)",
        "category": "📋",
        "render": "render_ts_weekly_monthly",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_north_flow",
        "title": "🌐 北向资金 (沪深股通)",
        "category": "📋",
        "render": "render_ts_north_flow",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_margin",
        "title": "💳 融资融券",
        "category": "📋",
        "render": "render_ts_margin",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_top_list",
        "title": "🐉 龙虎榜 (近 1 日, Tushare)",
        "category": "📋",
        "render": "render_ts_top_list",
        "required": False,
        "refresh": "REGEN",
    },
    {
        "id": "ts_finance",
        "title": "💵 财务指标 (ROE/毛利率/净利率/资产负债率)",
        "category": "📋",
        "render": "render_ts_finance",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_dividend",
        "title": "💰 分红送转 (近 10 年)",
        "category": "📋",
        "render": "render_ts_dividend",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_forecast",
        "title": "📊 业绩预告 (Tushare forecast, 限量接口) ⭐",
        "category": "📋",
        "render": "render_ts_forecast",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "t_events",
        "title": "🎯 T 框架事件 (业绩自动 + 非业绩手维护, 2026-07-23 升级)",
        "category": "📋",
        "render": "render_t_events",
        "required": True,
        "refresh": "REGEN",
    },
    {
        "id": "ts_money_flow",
        "title": "💹 个股资金流向 (Tushare moneyflow, 真正的 fflow)",
        "category": "📋",
        "render": "render_ts_money_flow",
        "required": True,
        "refresh": "REGEN",
    },
    # === Linter (末尾, 每次重算) ===
    {
        "id": "linter",
        "title": "🔍 Linter 校验报告 (增强模式自动追加)",
        "category": "🔧",
        "render": "render_linter",
        "required": True,
        "refresh": "REGEN",
    },
]


def get_section_ids() -> list:
    """返回所有 section ID (用于 linter 校验)"""
    return [s["id"] for s in REPORT_SECTIONS]


def get_section_titles() -> list:
    """返回所有 section 标题 (用于 linter 校验)"""
    return [s["title"] for s in REPORT_SECTIONS]


def get_required_section_ids() -> list:
    """返回必填 section ID"""
    return [s["id"] for s in REPORT_SECTIONS if s.get("required", True)]


def get_regen_titles() -> list[str]:
    """返回 refresh=REGEN 的 section 标题列表 (enhance 时强制重新生成)"""
    return [s["title"] for s in REPORT_SECTIONS if s.get("refresh") == "REGEN"]


def get_preserve_titles() -> list[str]:
    """返回 refresh=PRESERVE 的 section 标题列表 (enhance 时保留原内容)"""
    return [s["title"] for s in REPORT_SECTIONS if s.get("refresh") == "PRESERVE"]


def get_ordered_titles() -> list[str]:
    """按 CLAUDE.md 1️⃣-7️⃣ 顺序返回所有 section 标题 (linter 顺序检查用)"""
    return [s["title"] for s in REPORT_SECTIONS]


def validate_schema() -> dict:
    """校验 schema 自身: section 数量, ID 唯一, 顺序符合 CLAUDE.md 1️⃣-7️⃣"""
    errors = []

    # 1. ID 唯一
    ids = [s["id"] for s in REPORT_SECTIONS]
    if len(set(ids)) != len(ids):
        errors.append(f"❌ 重复 section ID: {[i for i in ids if ids.count(i) > 1]}")

    # 2. 顺序: 1️⃣ 必须在 2️⃣ 之前, 5️⃣ 必须在 1️⃣2️⃣ 之后, 6️⃣ 在 5️⃣ 之后, 7️⃣ 在 6️⃣ 之后
    cat_order = {"1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "5️⃣": 5, "6️⃣": 6, "7️⃣": 7}
    last_cat = 0
    for s in REPORT_SECTIONS:
        cat = s.get("category", "📋")
        if cat in cat_order:
            cur = cat_order[cat]
            if cur < last_cat:
                errors.append(f"❌ 顺序错误: {s['id']} ({cat}) 在 {last_cat} 之后")
            last_cat = max(last_cat, cur)

    # 3. 关键 section 必填 (chan_supplement 已于 7-29 废弃, 用 method_matrix 替代)
    # 2026-08-31: fflow section 已停用 (OBV 噪声大, CLAUDE.md 板块适用性限制), 不再 required
    required = {"method_matrix", "peg", "dcf"}
    missing = required - set(ids)
    if missing:
        errors.append(f"❌ 必填 section 缺失: {missing}")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "total_sections": len(REPORT_SECTIONS),
        "categories": sorted(set(s.get("category", "📋") for s in REPORT_SECTIONS)),
    }


if __name__ == "__main__":
    result = validate_schema()
    print(f"Schema 校验: {'✅ 通过' if result['valid'] else '❌ 失败'}")
    print(f"总 section 数: {result['total_sections']}")
    print(f"分类: {result['categories']}")
    if result["errors"]:
        for e in result["errors"]:
            print(f"  {e}")
