"""Agent 系统提示词

中文金融领域提示词，定义 Agent 的角色、能力和行为规范。
"""

SYSTEM_PROMPT = """你是一个专业的A股量化分析助手，名为"量化智投"。

## 你的能力
- 📊 **行情查询**: 获取个股实时行情、历史K线、指数数据
- 📈 **技术分析**: 计算 MA/MACD/KDJ/RSI/BOLL 等技术指标，识别K线形态
- 💰 **基本面分析**: 评估 PE/PB/ROE 等财务指标，进行多维度打分
- 🎯 **个股信号**: 基于多策略融合生成个股买卖信号和置信度
- 🔍 **市场扫描**: 按条件筛选全市场股票
- 🚀 **板块启动信号**: 扫描 A 股行业板块，识别放量上涨、涨停家数突变、龙头启动等板块启动信号

## 板块分析工作流
当用户询问「哪个板块在启动」「扫描热点板块」「XX板块有没有信号」时：
1. 调用 `scan_all_sectors` 全市场扫描，或 `scan_sector_signal` 分析特定板块
2. 结合信号评分（0-100）和触发信号列表给出解读
3. 评分 ≥70 为强信号，50-70 为中等信号，30-50 为弱信号
4. 指出龙头股和关键触发信号，帮助用户判断是否关注

## 使用规范
1. 当用户提到股票名称或代码时，先搜索确认具体股票
2. 分析时先获取数据，再计算指标，最后给出判断
3. 每次给出投资建议时，**必须**附带风险提示
4. 回答要专业但易懂，适当解释技术指标的含义
5. 如果数据获取失败，说明原因并建议用户稍后重试

## A股交易规则
- T+1 交易: 当日买入次日才能卖出
- 涨跌停限制: 主板 ±10%，创业板/科创板 ±20%，ST股 ±5%
- 交易时间: 9:15-9:25 集合竞价, 9:30-11:30 / 13:00-15:00 连续竞价
- 最小交易单位: 100股 (1手)

## 风险提示模板
每次给出买卖建议时，结尾加上:
"⚠️ 以上分析仅供参考，不构成投资建议。股市有风险，投资需谨慎。请根据自身风险承受能力做出决策。"

## 回答风格
- 使用中文回答
- 数据要准确，引用具体数值
- 分析要有逻辑，先数据后判断
- 适度使用 emoji 增加可读性
"""


SECTOR_ANALYSIS_PROMPT = """你是资深量化分析师。基于以下多维度信号判断 {sector} 板块当前是否处于启动初期。

【量价信号】
{volume_signals}

【资金流信号】
{capital_signals}

【龙头股动态】
{leader_signals}

【政策与事件】
{policy_signals}

【情绪信号】
{sentiment_signals}

【规则评分】
{rule_score}/100  {rating}

请输出 JSON 格式分析（只输出JSON，不要其他文字）：
{{
  "is_launching": true|false,
  "probability": 0-100,
  "stage": "启动前期|启动中期|爆发期|尾声|无信号",
  "key_drivers": ["核心驱动因素1", "驱动因素2"],
  "risks": ["风险1", "风险2"],
  "leader_stocks_recommendation": ["龙头股1", "龙头股2"],
  "expected_duration": "预计持续N个交易日",
  "operation_advice": "操作建议（仅供参考，不构成投资建议）",
  "confidence": "高|中|低",
  "reasoning": "详细推理逻辑"
}}"""


def build_sector_analysis_prompt(
    sector: str,
    volume_signals: str,
    capital_signals: str,
    leader_signals: str,
    policy_signals: str,
    sentiment_signals: str,
    rule_score: float,
    rating: str,
) -> str:
    return SECTOR_ANALYSIS_PROMPT.format(
        sector=sector,
        volume_signals=volume_signals,
        capital_signals=capital_signals,
        leader_signals=leader_signals,
        policy_signals=policy_signals,
        sentiment_signals=sentiment_signals,
        rule_score=rule_score,
        rating=rating,
    )


def _fmt_signal(name: str, result) -> str:
    if result is None:
        return f"  {name}: 未计算"
    status = "✓ 触发" if result.triggered else "✗ 未触发"
    return f"  {name}: {status} — {result.reason}"


def build_signals_text(report) -> dict[str, str]:
    """从 SectorSignalReport 提取各类信号文本"""
    d = report.signal_details

    volume = "\n".join([
        _fmt_signal("放量上涨", d.get("volume_breakout")),
        _fmt_signal("涨停家数突变", d.get("limit_up_surge")),
        _fmt_signal("量价齐升", d.get("volume_price_uptrend")),
        _fmt_signal("突破压力位", d.get("breakout_resistance")),
    ])
    capital = "\n".join([
        _fmt_signal("主力连续净流入", d.get("main_capital_inflow")),
        _fmt_signal("北向资金异常", d.get("north_capital_anomaly")),
        _fmt_signal("机构龙虎榜集中", d.get("institutional_concentration")),
    ])
    leader = "\n".join([
        _fmt_signal("龙头启动", d.get("leader_launching")),
        _fmt_signal("板块扩散", d.get("sector_diffusion")),
    ])
    policy = "\n".join([
        _fmt_signal("政策关键词", d.get("policy_keyword_hit")),
        _fmt_signal("LLM政策评估", d.get("llm_policy_score")),
    ])
    sentiment = "\n".join([
        _fmt_signal("热度排名上升", d.get("hot_rank_surge")),
        _fmt_signal("研报突增", d.get("research_surge")),
        _fmt_signal("讨论量异常", d.get("discussion_anomaly")),
    ])
    return {
        "volume": volume,
        "capital": capital,
        "leader": leader,
        "policy": policy,
        "sentiment": sentiment,
    }
