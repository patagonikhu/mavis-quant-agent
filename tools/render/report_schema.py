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
  2️⃣ 2 个缠论补充策略 (威科夫+多市场共振) — 一等公民
  6️⃣ 买卖点 (1买/2买/3买/1卖/2卖/3卖) — 综合 (买点/卖点)

2026-11-03 删: 🎯 T 框架事件 一档 (t_events)
  → 本档正文只剩一句占位符: "⚠️ {code} T 框架事件待 LLM 补 (年报/新闻/公告)"
    —— LLM 从未填充过, 常年零有效信息
  → 数据源 events.json 早在 v6.2 就已删除 (README.md:121 有说明),
    本档在生产中从来只会渲染出那一句 ⚠️ 占位
  → 事件清单类信息改由 LLM 在报告生成时从外部查 (年报/新闻/公告) 后
    直接写入报告正文, 不再走固定 section
  → 本档无独立数据管线: render_data / analysis_engine 均无 t_events 字段,
    只需删除 renderer 的 _section_t_events + 调用块 + schema 条目

2026-11-03 删: 📌 监控触发点 一档 (monitor_triggers)
  → 本档是 2026-11-03 这轮风控四档 (止盈/止损/退场信号/3层仓位) 删完后的
    最后两档之一, 与 "📋 3 层仓位策略" 一起删除
  → 实际有效信息只有 3 个缠论触发 (60分底背 / 止跌信号 / 60分顶背):
    - "中枢_突破上沿 / 跌破下沿" 的「已触发」是硬编码 False, 从不变化
    - "时间触发" 生产调用传 events=[] + code="" → 永远为空
  → 操作建议里的仓位比例 (建底仓 25-30% / 加中仓 20-25% / 减仓 1/3) 与
    止损价 (¥price×0.97) 同样是机械外推, 无标的特异性判断
  → 缠论三要素 (中枢位置 + 背驰 + 止跌) 仍在报告最前面, 买卖点
    (1买/2买/3买/1卖/2卖/3卖) 仍由 缠论买卖点 section (_fmt_bs) +
    因子矩阵独立提供 → 删除本档不丢缠论信号
  → 数据管线一并删除: factor_risk.compute_monitor_triggers (该文件删空后
    整个模块移除) + analysis_engine._derive_monitor_triggers + render_data
    的 monitor_triggers property
  → ⚠️ 删除后报告不再有任何独立风控 section (止盈/止损/退场/3层仓位/监控
    触发点 五档全删); 仓位与止损决策需自行从 缠论三要素 + 买卖点 推导

2026-11-03 删: 📋 3 层仓位策略 一档 (three_layer_position)
  → 输出中枢位三档仓位 (逆势/底/中/波动) + 止损阶梯 + 目标阶梯,
    其中价位全是"当前价 × 固定百分比"的机械外推 (止损 ×0.85/0.92/0.97,
    目标 +5%/+10%/+20%), 不含标的特异性判断
  → 与 "📌 监控触发点" 重叠; 两档同批删除
  → 卖点 (1卖/2卖/3卖) 仍由 缠论买卖点 section (_fmt_bs) + 因子矩阵
    独立提供, 删除本档不丢卖点信息
  → 数据管线一并删除: factor_basic.compute_three_layer_position (该文件删空后
    整个模块移除) + analysis_engine._derive_three_layer_position + render_data
    的 three_layer_position / position_layer 两个 property

2026-11-03 删: 🟢 退场信号检查 一档 (exit_signals)
  → 纯缠论退出判定 (60分底背/60分顶背/止跌信号 → 一句话 verdict),
    信息量与 "📌 监控触发点"
    (缠论 4 触发 + 中枢 2 触发 + 时间触发) 完全重叠
  → 卖点 (1卖/2卖/3卖) 仍由 缠论买卖点 section (_fmt_bs) + 因子矩阵
    独立提供, 删除本档不丢卖点信息
  → 数据管线一并删除: factor_risk.compute_exit_signals +
    analysis_engine._derive_exit_signals + render_data.exit_signals property

2026-11-03 删: 🎯 止盈 3 层 + 🛑 止损 4 档 两档
  → 止盈/止损价位是"当前价 × 固定百分比"的机械外推 (止盈 +5/+10/+20%,
    止损 -3/-5/-8/-10%), 不含任何标的特异性判断
  → 与 监控触发点 属重复表达, 两档同批删除; 退出/止损依据改由 缠论三要素 +
    缠论买卖点 承担
  → 数据管线一并删除: mech.compute_stop_pl (MA20/MA60 外推) +
    factor_risk.compute_stop_profit_loss (中枢位外推) + analysis_engine
    ._derive_stop_profit_loss + render_data.take_profit/stop_loss 字段

2026-11-03 删: 🎯 投资四问 + ⏰ T 框架 + 🚨 5 类 14 子信号 三档
  → 四问/T框架 只输出机械占位 (LLM 从未填充), 5 类子信号 2026-11-02 起只剩
    "龙头/政策/情绪" 3 个"暂未接入"占位 → 三档常年零有效信息
  → 数据管线 (_mech_four_questions / _mech_t_frame / _agg_5_categories /
    _derive_five_categories) 一并删除; 报告头的"评级/龙头 X/14"复用一并移除
  → 注意: 🎯 T 框架事件 (t_events) 原为独立 section (事件清单), 同日一并删除
    (见本文件顶部 2026-11-03 删说明)

2026-11-03 删: 4️⃣ 大盘+美股背景整档 (本地指数数据常年缺失/滞后, 美股永远是占位符)

2026-11-02 删: 5️⃣ PEG/DCF L 整档 (PEG 是假指标, DCF 依赖同一套 EPS 预测)
  → 估值只看 4 季财务大表里的 PE + 盈利质量 (ROC/EY/毛利率/杠杆)

refresh 字段说明 (2026-07-24 加入, 方案 B 核心):
  "REGEN"    — 每次 enhance 强制重新生成 (数据驱动, 不保留旧内容)
               适用: 实时数据 (MA/技术指标), 或之前有单位 bug 的 section
  "PRESERVE" — 保留原报告内容, 只在缺失时补充占位符
               适用: LLM 填充的分析 (缠论三要素)
               注: 2026-11-03 起 PRESERVE 无消费方 (全仓仅定义处出现)
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
        "title": "财务数据",
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
        "required": False,  # 2026-09-18 砍: 跟 📈 因子历史走势重复 (含当日 MACD/RSI/KDJ/ATR)
        "refresh": "REGEN",
        "_deprecated": True,  # 标记废弃, renderer 不输出
        # 2026-10-30: 之前 _deprecated 只是"声明"没人消费, 导致 linter 仍把它算进必填
        #   → 下面 get_section_titles 现在会过滤掉
    },
    # 2026-07-25: 5 方法 × 3 周期 矩阵 (独立 section, 整合原 5 合 1 顶部预警)
    # 注: chan_signals / chan_supplement 已于 7-29 合并入 factor_history + method_matrix, 废弃删除
    {
        "id": "method_matrix",
        "title": "🎯 因子 × 3 周期 综合矩阵 (2026-07-25 合并: 整合原 5 合 1 顶部预警, 2026-09-09 改)",
        "category": "2️⃣",
        "render": "render_method_matrix",
        "required": True,
        "refresh": "REGEN",
    },
    # === 3️⃣ 大盘+美股背景 ===
    # 2026-11-03: 整档从 md 报告里删除 (用户指令)
    #   原因: A 股指数本地 parquet 常年滞后/缺失, 美股永远是 "需 /t-sync-data" 占位,
    #         这段常年输出无信息量的表格, 却占着 4️⃣ 整档位置
    {
        "id": "market_context",
        "title": "🌍 大盘 + 美股背景",
        "category": "4️⃣",
        "render": "render_market_context",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_market_context 一并删除)
        "_deprecated": True,
    },
    # === 基本面 + 策略 (数据驱动) ===
    # 2026-09-09 删: 基本面 (4 维) section (走 缠论 + ValuationStrategy PEG+DCF+Magic)
    # {
    #     "id": "fundamental",
    #     "title": "💎 基本面 (4 维) — 自动评估",
    #     "category": "📋",
    #     "render": "render_fundamental",
    #     "required": True,
    #     "refresh": "REGEN",
    # },
    # 2026-09-09 删: 4 套交易策略 section (走 缠论, 不再单独跑 4 套指标)
    # {
    #     "id": "strategy",
    #     "title": "🎯 4 套交易策略 — 自动评估",
    #     "category": "📋",
    #     "render": "render_strategy",
    #     "required": True,
    #     "refresh": "REGEN",
    # },
    # === 投资四问 + T 框架 + 5 类 14 子信号 ===
    # 2026-11-03: 三档整档从 md 报告里删除 (用户指令)
    #   原因: 四问/T 框架只输出机械占位 (LLM 从未真正填充过), 5 类子信号自
    #         2026-11-02 删 PEG 起只剩"龙头/政策/情绪"3 个"暂未接入"占位
    #         → 三档常年零有效信息, 却占着报告头部三段位置
    {
        "id": "four_questions",
        "title": "🎯 投资四问",
        "category": "📋",
        "render": "render_four_questions",
        "required": False,
        "refresh": "PRESERVE",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_four_questions 一并删除)
        "_deprecated": True,
    },
    {
        "id": "t_frame",
        "title": "⏰ T 框架",
        "category": "📋",
        "render": "render_t_frame",
        "required": False,
        "refresh": "PRESERVE",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_t_frame 一并删除)
        "_deprecated": True,
    },
    {
        "id": "five_categories",
        "title": "🚨 5 类 14 子信号",
        "category": "📋",
        "render": "render_five_categories",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_signal_5cat 一并删除)
        "_deprecated": True,
    },
    {
        "id": "xgboost",
        "title": "🤖 XGBoost 校准",
        "category": "📋",
        "render": "render_xgboost",
        "required": True,
        "refresh": "REGEN",
    },
    # === 7️⃣ 买卖点 (数据驱动) ===
    # 2026-11-03: 止盈 3 层 + 止损 4 档 + 退场信号 + 3 层仓位 + 监控触发点
    #   五档风控全部从 md 报告里删除 (用户指令)
    #   原因: 价位/仓位比例 = 当前价 × 固定百分比的机械外推, 无标的特异性判断;
    #         且五档彼此重复表达
    #   缠论三要素 (中枢+背驰+止跌) 仍在报告最前, 买卖点仍由缠论买卖点 section
    #   + 因子矩阵提供 → 报告不再有独立风控 section
    {
        "id": "stop_profit",
        "title": "🎯 止盈 3 层",
        "category": "7️⃣",
        "render": "render_stop_profit",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_take_profit 一并删除)
        "_deprecated": True,
    },
    {
        "id": "stop_loss",
        "title": "🛑 止损 4 档",
        "category": "7️⃣",
        "render": "render_stop_loss",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_stop_loss 一并删除)
        "_deprecated": True,
    },
    {
        "id": "exit_signals",
        "title": "🟢 退场信号检查",
        "category": "7️⃣",
        "render": "render_exit_signals",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_exit_signals 一并删除)
        #   纯缠论退出判定与 监控触发点 重叠 (两档同批删除);
        #   卖点仍由缠论买卖点 section 提供
        "_deprecated": True,
    },
    {
        "id": "three_layer_position",
        "title": "📋 3 层仓位策略",
        "category": "7️⃣",
        "render": "render_three_layer_position",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_position_layer 一并删除)
        #   中枢位三档仓位 + 止损/目标阶梯 (机械百分比外推) 与 监控触发点 重叠
        #   (两档同批删除)
        "_deprecated": True,
    },
    {
        "id": "monitor_triggers",
        "title": "📌 监控触发点",
        "category": "7️⃣",
        "render": "render_monitor_triggers",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: renderer 已不再输出该 section (函数 _section_monitor 一并删除)
        #   实际有效信息只有 3 个缠论触发 (中枢 2 触发硬编码 False, 时间触发永远为空)
        "_deprecated": True,
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
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_weekly_monthly",
        "title": "📈 周线 / 月线 K (Tushare)",
        "category": "📋",
        "render": "render_ts_weekly_monthly",
        "required": True,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_north_flow",
        "title": "🌐 北向资金 (沪深股通)",
        "category": "📋",
        "render": "render_ts_north_flow",
        "required": True,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_margin",
        "title": "💳 融资融券",
        "category": "📋",
        "render": "render_ts_margin",
        "required": True,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_top_list",
        "title": "🐉 龙虎榜 (近 1 日, Tushare)",
        "category": "📋",
        "render": "render_ts_top_list",
        "required": False,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_finance",
        "title": "💵 财务指标 (ROE/毛利率/净利率/资产负债率)",
        "category": "📋",
        "render": "render_ts_finance",
        "required": True,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_dividend",
        "title": "💰 分红送转 (近 10 年)",
        "category": "📋",
        "render": "render_ts_dividend",
        "required": True,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "ts_forecast",
        "title": "📊 业绩预告 (Tushare forecast, 限量接口) ⭐",
        "category": "📋",
        "render": "render_ts_forecast",
        "required": True,
        "refresh": "REGEN",
        # 2026-10-30: 标记废弃 — 从未实现 (render 函数不存在, 数据源本地也没有:
        #   north_flow/margin/top_list/dividend 无 DataStore 方法, data/history 下无 parquet)
        #   /t-analyze 是 0 网络 skill, 不能拉这些数据 → 本 section 永远出不来
        "_deprecated": True,
    },
    {
        "id": "t_events",
        "title": "🎯 T 框架事件 (业绩自动 + 非业绩手维护, 2026-07-23 升级)",
        "category": "📋",
        "render": "render_t_events",
        "required": False,
        "refresh": "REGEN",
        # 2026-11-03: 标记废弃 — 正文只剩一句 "⚠️ 待 LLM 补" 占位符,
        #   LLM 从未填充; 事件清单改由 LLM 在生成报告时从外部查后直接写入正文
        "_deprecated": True,
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
    """返回所有 section ID (用于 linter 校验)

    2026-10-30: 过滤 _deprecated=True 的 section
      (这些 section 的 render 函数从未实现, 数据源本地也没有, 永远出不来)
    """
    return [s["id"] for s in REPORT_SECTIONS if not s.get("_deprecated")]


def get_section_titles() -> list:
    """返回所有 section 标题 (用于 linter 校验)

    2026-10-30: 过滤 _deprecated=True 的 section
      之前 tech_indicators + 8 个 ts_* 被算进必填清单, 但 renderer 从不输出,
      linter 却从不为它们报警 (missing 只统计不 warn) → 完整度虚低, 问题被掩盖.
    """
    return [s["title"] for s in REPORT_SECTIONS if not s.get("_deprecated")]


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

    # 2. 顺序: 1️⃣ 必须在 2️⃣ 之前, 6️⃣ 在 2️⃣ 之后, 7️⃣ 在 6️⃣ 之后
    # 2026-11-02: 5️⃣ PEG/DCF 整档已删
    cat_order = {"1️⃣": 1, "2️⃣": 2, "3️⃣": 3, "4️⃣": 4, "6️⃣": 6, "7️⃣": 7}
    last_cat = 0
    for s in REPORT_SECTIONS:
        cat = s.get("category", "📋")
        if cat in cat_order:
            cur = cat_order[cat]
            if cur < last_cat:
                errors.append(f"❌ 顺序错误: {s['id']} ({cat}) 在 {last_cat} 之后")
            last_cat = max(last_cat, cur)

    # 3. 关键 section 必填 (chan_supplement 已于 7-29 废弃, 用 method_matrix 替代)
    # 2026-08-31: fflow section 已停用; 2026-10-28: 主力资金流维度整体删除
    # 2026-11-02: peg / dcf 已删 (PEG 是假指标)
    required = {"method_matrix"}
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
