# 板块启动信号 Agent 设计文档

## 1. 项目目标

构建一个 LLM Agent，**实时监控 A 股各行业板块**，识别"板块启动"信号，输出可解释的概率评分和操作建议。

### 1.1 什么是"板块启动"

板块启动指**某个行业板块在短期内（通常 5-30 天）出现持续性上涨**，特征：
- 板块指数突破前期高点
- 成分股普遍上涨（≥60% 个股上涨）
- 龙头股领涨，资金涌入
- 涨幅显著超过大盘

### 1.2 Agent 的核心价值

不是预测涨跌，而是**信号识别 + 可解释性**：
- 在板块"启动初期"（前 1-3 天）发出信号
- 给出多维度证据，让用户判断是否参与
- 历史回测验证信号有效性
- 持续学习优化阈值

---

## 2. 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户交互层 (Web/CLI)                    │
│   - 实时信号告警 / 查询特定板块 / 历史回测查看                │
└─────────────────┬───────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│                    Agent 调度层 (LangGraph)                  │
│   - Router: 分发用户意图（实时监控/历史查询/回测）           │
│   - Memory: 保存用户偏好、历史信号、用户反馈                 │
└─────────────────┬───────────────────────────────────────────┘
                  │
        ┌─────────┴──────────┬─────────────┬──────────────┐
        ▼                    ▼             ▼              ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ 信号生成 Agent│  │ 解释器 Agent │  │ 回测 Agent   │  │ 监控 Agent   │
│ (规则+模型)  │  │ (LLM 解释)   │  │ (历史验证)   │  │ (实时扫描)   │
└──────┬───────┘  └──────────────┘  └──────────────┘  └──────────────┘
       │
┌──────▼──────────────────────────────────────────────────────┐
│                     特征工程层                                │
│   ─ 量价特征 / 资金流特征 / 龙头特征 / 政策特征 / 情绪特征   │
└──────┬──────────────────────────────────────────────────────┘
       │
┌──────▼──────────────────────────────────────────────────────┐
│                     数据采集层                                │
│   ─ 行情数据(tushare/akshare) / 资金流向 / 新闻 / 研报      │
└─────────────────────────────────────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────────────────┐
│              数据存储层 (TimescaleDB + Redis)                │
│   ─ 行情时序数据 / 信号历史 / 用户反馈 / 缓存                │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 各模块职责

| 模块 | 职责 | 技术栈 |
|---|---|---|
| **数据采集** | 抓行情、资金流、新闻 | tushare、akshare、爬虫 |
| **特征工程** | 计算技术指标、统计特征 | pandas、numpy、ta-lib |
| **信号生成** | 规则+ML 计算原始信号 | 规则引擎 + XGBoost |
| **LLM Agent** | 综合多维信号、生成解释 | LangGraph + Qwen/DeepSeek |
| **回测引擎** | 历史验证信号有效性 | backtrader / vectorbt |
| **监控调度** | 定时扫描、告警 | APScheduler / Celery |
| **存储** | 时序数据 + 缓存 | TimescaleDB + Redis |

---

## 3. 数据采集

### 3.1 数据源

| 数据类型 | 来源 | 频率 | 字段 |
|---|---|---|---|
| **板块指数** | tushare/akshare 免费 | 日级 + 分钟级 | 开高低收、成交量、成交额 |
| **个股行情** | tushare/akshare | 日级 + 分钟级 | OHLCV、涨跌停、换手率 |
| **板块成分股** | 同花顺/东财 | 周级（变动小）| 板块名 → 股票列表 |
| **资金流向** | 东财 Choice、同花顺 iFinD | 日级 | 主力净流入、超大单、散户 |
| **北向资金** | tushare（陆股通）| 日级 | 北向净买入（按板块）|
| **龙虎榜** | 交易所官网 / akshare | 日级 | 买卖前 5 席位 |
| **板块热度** | 东财热度榜爬虫 | 小时级 | 板块排名变化 |
| **新闻** | 财联社、新浪财经 RSS | 实时 | 标题、正文、发布时间 |
| **研报** | 东方财富研报 / wind | 日级 | 标题、评级、目标价 |
| **政策事件** | 国务院/发改委爬虫 + LLM 提取 | 实时 | 政策文本 |

### 3.2 数据采集实现

```python
# data_collector.py
import tushare as ts
import akshare as ak
from datetime import datetime, timedelta

class DataCollector:
    def __init__(self, ts_token: str):
        self.pro = ts.pro_api(ts_token)
    
    def get_sector_daily(self, sector_code: str, start: str, end: str):
        """获取板块日 K"""
        df = ak.stock_board_industry_hist_em(
            symbol=sector_code, start_date=start, end_date=end
        )
        return df
    
    def get_sector_constituents(self, sector_code: str):
        """获取板块成分股"""
        return ak.stock_board_industry_cons_em(symbol=sector_code)
    
    def get_capital_flow(self, sector_code: str):
        """获取板块资金流向"""
        return ak.stock_sector_fund_flow_rank()
    
    def get_north_flow_by_sector(self, date: str):
        """获取北向资金按板块"""
        return ak.stock_hsgt_board_rank_em()
    
    def get_news_realtime(self):
        """获取实时财经新闻"""
        return ak.stock_news_em()
```

---

## 4. 信号设计与算法

核心是**多维度信号融合**，分 5 大类，每类多个具体信号。

### 4.1 量价信号 (Volume-Price Signals)

#### 4.1.1 板块放量上涨

**逻辑**：板块指数突然放量大涨，是启动最直接的信号。

**公式**：
```
volume_ratio = today_volume / MA(volume, 20)
price_change = (today_close - yesterday_close) / yesterday_close

放量上涨 = (volume_ratio > 1.5) AND (price_change > 0.02)
```

**强度评分**：
```python
def volume_breakout_score(volume_ratio, price_change):
    if volume_ratio > 3 and price_change > 0.05:
        return 10  # 强信号
    elif volume_ratio > 2 and price_change > 0.03:
        return 7
    elif volume_ratio > 1.5 and price_change > 0.02:
        return 5
    else:
        return 0
```

#### 4.1.2 涨停家数突变（核心信号）

**逻辑**：板块内涨停家数突然增多，比单股放量更强烈。

**公式（Z-score 异常检测）**：
```python
def is_limit_up_surge(sector, date):
    history = get_limit_up_history(sector, date, lookback=20)
    today_count = history[-1]
    
    mean = np.mean(history[:-1])
    std = np.std(history[:-1])
    
    z_score = (today_count - mean) / std if std > 0 else 0
    
    return {
        'triggered': (
            today_count >= 3                  # 绝对数量门槛
            and z_score >= 2                  # 统计异常
            and today_count >= mean * 3       # 至少 3 倍均值
        ),
        'z_score': z_score,
        'today_count': today_count,
        'mean_20d': mean,
    }
```

**强度评分**：
```
score = min(10, z_score * 2)
```

#### 4.1.3 量价齐升（持续性信号）

**逻辑**：连续多日成交量和价格同步上升，说明买盘持续。

**公式**：
```python
def volume_price_uptrend(sector, date, days=3):
    """连续 N 日量价齐升"""
    data = get_recent_data(sector, date, days)
    
    price_up = all(data[i].close > data[i-1].close for i in range(1, days))
    volume_up = all(data[i].volume > data[i-1].volume for i in range(1, days))
    
    return price_up and volume_up
```

#### 4.1.4 突破压力位

**逻辑**：板块指数突破近期重要压力位（前高、20 日均线、60 日均线）。

**公式**：
```python
def breakout_resistance(sector, date):
    today_close = get_close(sector, date)
    
    # 多重压力位
    high_60d = max(get_closes(sector, date, 60))
    ma20 = MA(get_closes(sector, date, 20))
    ma60 = MA(get_closes(sector, date, 60))
    
    breaks = []
    if today_close > high_60d:
        breaks.append('60日新高')
    if today_close > ma20 and yesterday_close <= ma20:
        breaks.append('突破MA20')
    if today_close > ma60 and yesterday_close <= ma60:
        breaks.append('突破MA60')
    
    return breaks
```

---

### 4.2 资金流信号 (Capital Flow Signals)

#### 4.2.1 主力资金连续净流入

**逻辑**：连续 N 天主力净流入，说明大资金持续布局。

**公式**：
```python
def main_capital_consecutive_inflow(sector, date, days=3):
    flows = get_main_capital_flow(sector, date, days)
    
    consecutive = all(f > 0 for f in flows)
    total = sum(flows)
    avg_inflow_20d = MA(get_main_capital_flow(sector, date, 20), 20)
    
    return {
        'consecutive': consecutive,
        'total': total,
        'is_significant': total > abs(avg_inflow_20d) * 3,
    }
```

#### 4.2.2 北向资金加仓异常

**逻辑**：陆股通是聪明钱，板块内北向净买入突变是先行信号。

**公式**：
```python
def north_capital_anomaly(sector, date):
    history = get_north_flow_by_sector(sector, date, lookback=20)
    today = history[-1]
    
    mean = np.mean(history[:-1])
    std = np.std(history[:-1])
    
    z_score = (today - mean) / std if std > 0 else 0
    
    return {
        'triggered': z_score > 2 and today > 0,
        'z_score': z_score,
        'today_inflow': today,
    }
```

#### 4.2.3 龙虎榜机构席位密集

**逻辑**：板块内多只票同时上龙虎榜且机构席位买入，是机构调仓信号。

**公式**：
```python
def institutional_concentration(sector, date, window=3):
    """3 日内龙虎榜机构席位买入板块内股票数"""
    sector_stocks = get_sector_constituents(sector)
    
    institutional_buys = []
    for d in range(window):
        date_d = date - timedelta(days=d)
        lhb = get_lhb_data(date_d)
        
        for record in lhb:
            if record['stock'] in sector_stocks:
                if record['top5_buy'].contains_institutional():
                    institutional_buys.append(record)
    
    return {
        'count': len(institutional_buys),
        'is_significant': len(institutional_buys) >= 3,
    }
```

#### 4.2.4 行业 ETF 申购激增

**逻辑**：行业 ETF 份额暴增说明资金通过 ETF 涌入板块。

**公式**：
```python
def etf_subscription_surge(sector_etf_code, date):
    history = get_etf_shares(sector_etf_code, date, lookback=20)
    today_change = history[-1] - history[-2]
    avg_change = np.mean(np.abs(np.diff(history[:-1])))
    
    return {
        'triggered': today_change / avg_change > 3 and today_change > 0,
        'change_ratio': today_change / avg_change,
    }
```

---

### 4.3 龙头启动信号 (Leader Signals)

#### 4.3.1 识别龙头股

**逻辑**：每个板块有 1-3 只龙头，龙头先动是板块启动的领先信号。

**公式**：
```python
def identify_leaders(sector, date):
    """龙头判定：流通市值前 3 + 年涨幅前 3 + 板块涨停先驱"""
    stocks = get_sector_constituents(sector)
    
    # 流通市值前 3
    top_by_mcap = sorted(stocks, key=lambda s: s.float_mcap, reverse=True)[:3]
    
    # 年度涨幅前 3
    top_by_return = sorted(stocks, key=lambda s: s.yearly_return, reverse=True)[:3]
    
    # 历史涨停领涨次数前 3
    top_by_limitup_history = sorted(
        stocks, key=lambda s: s.lead_limitup_count_yearly, reverse=True
    )[:3]
    
    leaders = set(top_by_mcap) | set(top_by_return) | set(top_by_limitup_history)
    return list(leaders)
```

#### 4.3.2 龙头启动

**公式**：
```python
def leader_launching(leader_stock, date):
    """龙头启动：连续涨停 OR 5 日大幅放量上涨"""
    consecutive_limit_up = count_consecutive_limit_up(leader_stock, date)
    
    last_5d = get_recent_data(leader_stock, date, 5)
    return_5d = (last_5d[-1].close - last_5d[0].close) / last_5d[0].close
    volume_ratio_5d = last_5d.mean_volume / get_volume_ma(leader_stock, date, 20)
    
    return {
        'triggered': (
            consecutive_limit_up >= 2 or
            (return_5d > 0.15 and volume_ratio_5d > 2)
        ),
        'consecutive_limit_up': consecutive_limit_up,
        'return_5d': return_5d,
    }
```

#### 4.3.3 板块跟随扩散

**逻辑**：龙头启动后 1-3 天内，板块内其他股票跟随启动，扩散信号最强。

**公式**：
```python
def sector_diffusion(sector, leader_launch_date, date):
    """龙头启动后 N 日内板块涨停家数变化"""
    days_since_leader = (date - leader_launch_date).days
    
    if days_since_leader < 1 or days_since_leader > 5:
        return {'triggered': False}
    
    today_limitup = count_limit_up(sector, date)
    pre_launch_avg = mean_limit_up_count(sector, leader_launch_date, lookback=5)
    
    return {
        'triggered': today_limitup > pre_launch_avg * 2 and today_limitup >= 3,
        'today_limitup': today_limitup,
        'pre_launch_avg': pre_launch_avg,
        'days_since_leader': days_since_leader,
    }
```

---

### 4.4 政策/事件信号 (Policy & Event Signals)

#### 4.4.1 政策关键词命中（规则）

**公式**：
```python
SECTOR_POLICY_KEYWORDS = {
    "新能源车": ["新能源车", "电动车", "动力电池", "充电桩", "补贴", "碳中和"],
    "半导体": ["芯片", "半导体", "光刻", "国产替代", "大基金"],
    "军工": ["国防", "军费", "装备", "演习", "国防预算"],
    "AI": ["人工智能", "大模型", "算力", "GPU", "AI 算力"],
    "光伏": ["光伏", "太阳能", "硅料", "组件", "新能源"],
}

OFFICIAL_SOURCES = ["国务院", "发改委", "工信部", "央行", "证监会", "财政部"]

def policy_keyword_hit(news_today, sector):
    keywords = SECTOR_POLICY_KEYWORDS.get(sector, [])
    
    score = 0
    hits = []
    for news in news_today:
        for kw in keywords:
            if kw in news['title'] or kw in news['content']:
                weight = 3 if any(s in news['source'] for s in OFFICIAL_SOURCES) else 1
                score += weight
                hits.append({'kw': kw, 'news': news['title']})
    
    return {
        'triggered': score >= 3,
        'score': score,
        'hits': hits,
    }
```

#### 4.4.2 LLM 政策影响评估

**逻辑**：用 LLM 综合判断政策对板块的影响（规则太死板）。

**Prompt**：
```python
POLICY_EVAL_PROMPT = """你是金融分析师。评估今日政策/事件对 {sector} 板块的影响。

今日相关新闻：
{news_list}

请输出 JSON：
{{
  "impact_direction": "利好|利空|中性",
  "impact_strength": 1-10,
  "impact_timeframe": "短期|中期|长期",
  "key_drivers": ["核心驱动因素1", "驱动因素2"],
  "risks": ["风险点1", "风险点2"],
  "reasoning": "详细推理过程"
}}
"""

def llm_policy_evaluation(sector, news_list):
    response = llm.invoke(POLICY_EVAL_PROMPT.format(
        sector=sector,
        news_list=format_news(news_list)
    ))
    return json.loads(response)
```

---

### 4.5 情绪信号 (Sentiment Signals)

#### 4.5.1 板块热度排名上升

**公式**：
```python
def hot_rank_surge(sector, date):
    history = get_hot_rank_history(sector, date, lookback=5)
    today_rank = history[-1]
    avg_rank = np.mean(history[:-1])
    
    return {
        'triggered': (
            today_rank <= 5 or                    # 进入前 5
            avg_rank - today_rank >= 10           # 排名上升 10 位以上
        ),
        'rank_change': avg_rank - today_rank,
    }
```

#### 4.5.2 研报数量突增

**公式**：
```python
def research_report_surge(sector, date):
    history = get_research_count(sector, date, lookback=30)
    today = history[-1]
    avg = np.mean(history[:-1])
    
    return {
        'triggered': today / avg > 3 if avg > 0 else False,
        'ratio': today / avg if avg > 0 else 0,
    }
```

#### 4.5.3 股吧讨论量异常

**公式**：
```python
def discussion_anomaly(sector, date):
    history = get_discussion_count(sector, date, lookback=20)
    today = history[-1]
    
    z_score = (today - np.mean(history[:-1])) / np.std(history[:-1])
    
    return {
        'triggered': z_score > 2,
        'z_score': z_score,
    }
```

---

## 5. 信号融合与评分

### 5.1 加权打分模型

```python
SIGNAL_WEIGHTS = {
    # 量价（权重 30）
    'volume_breakout': 10,
    'limit_up_surge': 15,
    'consecutive_uptrend': 5,
    
    # 龙头（权重 25）
    'leader_launching': 15,
    'sector_diffusion': 10,
    
    # 资金（权重 20）
    'main_capital_inflow': 10,
    'north_capital_anomaly': 5,
    'institutional_concentration': 5,
    
    # 政策（权重 15）
    'policy_keyword_hit': 5,
    'llm_policy_score': 10,  # LLM 给的 1-10 分
    
    # 情绪（权重 10）
    'hot_rank_surge': 4,
    'research_surge': 3,
    'discussion_anomaly': 3,
}

def calculate_total_score(sector, date):
    score = 0
    triggered = []
    
    for signal_name, weight in SIGNAL_WEIGHTS.items():
        signal_func = SIGNAL_FUNCS[signal_name]
        result = signal_func(sector, date)
        
        if result.get('triggered'):
            actual_weight = weight * result.get('strength_factor', 1.0)
            score += actual_weight
            triggered.append({
                'signal': signal_name,
                'weight': actual_weight,
                'detail': result,
            })
    
    return {
        'total_score': score,
        'triggered_signals': triggered,
        'rating': classify_rating(score),
    }


def classify_rating(score):
    if score >= 70: return '强信号 ⭐⭐⭐⭐⭐'
    elif score >= 50: return '中等信号 ⭐⭐⭐⭐'
    elif score >= 30: return '弱信号 ⭐⭐⭐'
    elif score >= 15: return '观察 ⭐⭐'
    else: return '无信号'
```

### 5.2 ML 增强：XGBoost 二次校准

规则评分给一个基础分，再用 XGBoost 训练一个分类模型校准：

```python
import xgboost as xgb

# 训练数据：过去 5 年每天每个板块的特征 + 标签（未来 5 日是否上涨 ≥10%）
features = [
    'volume_ratio', 'price_change', 'limit_up_z_score',
    'main_capital_3d', 'north_capital_z', 'leader_consec_limit_up',
    'policy_keyword_score', 'llm_policy_score',
    'hot_rank_change', 'rule_total_score',
]

model = xgb.XGBClassifier(
    n_estimators=200,
    max_depth=5,
    learning_rate=0.05,
)
model.fit(X_train, y_train)

# 推理时用模型校准
ml_probability = model.predict_proba(features)[1]  # 启动概率
final_score = rule_score * 0.6 + ml_probability * 100 * 0.4
```

---

## 6. LLM Agent 整合

### 6.1 Agent 架构（LangGraph）

```python
from langgraph.graph import StateGraph, END

class SectorSignalState(TypedDict):
    user_query: str
    sectors_to_analyze: List[str]
    raw_signals: Dict
    llm_analysis: Dict
    final_report: str
    user_feedback: Optional[str]


def build_signal_agent():
    graph = StateGraph(SectorSignalState)
    
    graph.add_node('intent_router', intent_router)
    graph.add_node('signal_collector', collect_all_signals)
    graph.add_node('llm_analyzer', llm_analyze_signals)
    graph.add_node('explanation_generator', generate_explanation)
    graph.add_node('alert_dispatcher', dispatch_alerts)
    
    graph.set_entry_point('intent_router')
    graph.add_edge('intent_router', 'signal_collector')
    graph.add_edge('signal_collector', 'llm_analyzer')
    graph.add_edge('llm_analyzer', 'explanation_generator')
    graph.add_edge('explanation_generator', 'alert_dispatcher')
    graph.add_edge('alert_dispatcher', END)
    
    return graph.compile()
```

### 6.2 LLM 综合分析 Prompt

```python
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
{rule_score}

请输出 JSON 格式分析：
{{
  "is_launching": true|false,
  "probability": 0-100,
  "stage": "启动前期|启动中期|爆发期|尾声",
  "key_drivers": ["核心驱动因素 1", "驱动因素 2", "驱动因素 3"],
  "risks": ["风险 1", "风险 2"],
  "leader_stocks_recommendation": ["龙头股 1", "龙头股 2"],
  "expected_duration": "预计持续 N 天",
  "expected_max_return": "预计最大涨幅 X%",
  "operation_advice": "操作建议（注意：仅供参考，不构成投资建议）",
  "confidence": "高|中|低",
  "reasoning": "详细推理逻辑"
}}
"""
```

---

## 7. 回测验证

### 7.1 回测框架

```python
class SectorSignalBacktest:
    def __init__(self, agent, start_date, end_date):
        self.agent = agent
        self.start = start_date
        self.end = end_date
        self.signals = []
        self.outcomes = []
    
    def run(self):
        for date in pd.date_range(self.start, self.end, freq='B'):
            for sector in ALL_SECTORS:
                signal = self.agent.evaluate(sector, date)
                
                if signal['rating'] in ['强信号', '中等信号']:
                    outcome = self._evaluate_outcome(sector, date, hold_days=10)
                    
                    self.signals.append({
                        'date': date,
                        'sector': sector,
                        'score': signal['total_score'],
                        'outcome': outcome,
                    })
        
        return self._calculate_metrics()
    
    def _evaluate_outcome(self, sector, signal_date, hold_days):
        future_data = get_sector_data(
            sector, signal_date, signal_date + timedelta(days=hold_days)
        )
        max_return = future_data['close'].max() / future_data['close'][0] - 1
        return {
            'max_return': max_return,
            'is_winner': max_return > 0.10,
        }
    
    def _calculate_metrics(self):
        total = len(self.signals)
        winners = sum(1 for s in self.signals if s['outcome']['is_winner'])
        avg_return = np.mean([s['outcome']['max_return'] for s in self.signals])
        
        return {
            'total_signals': total,
            'win_rate': winners / total if total > 0 else 0,
            'avg_max_return': avg_return,
            'sharpe': self._calc_sharpe(),
            'max_drawdown': self._calc_max_drawdown(),
        }
```

### 7.2 关键指标

| 指标 | 目标值 | 含义 |
|---|---|---|
| **胜率** | > 55% | 信号触发后 10 日内涨幅 > 10% 的比例 |
| **平均涨幅** | > 8% | 触发后 10 日内最大涨幅平均值 |
| **假阳性率** | < 30% | 信号触发但板块下跌的比例 |
| **夏普比率** | > 1.5 | 风险调整后收益 |
| **最大回撤** | < 15% | 历史最差表现 |

### 7.3 Walk-Forward 验证

```python
def walk_forward_validation(start, end, train_window=730, test_window=90):
    """滚动窗口验证，模拟真实使用"""
    results = []
    current = start
    
    while current + train_window + test_window <= end:
        train_start = current
        train_end = current + train_window
        test_start = train_end
        test_end = test_start + test_window
        
        # 用训练集调参
        best_params = optimize_params(train_start, train_end)
        
        # 用测试集验证
        test_metrics = backtest(test_start, test_end, params=best_params)
        results.append({
            'period': (test_start, test_end),
            'metrics': test_metrics,
        })
        
        current += test_window
    
    return results
```

---

## 8. 工程实现

### 8.1 技术栈

```yaml
后端框架: FastAPI
Agent 框架: LangGraph
LLM: 
  主: Qwen3-Coder-Plus / DeepSeek-V3
  备: GLM-4.6
数据存储:
  时序: TimescaleDB (PostgreSQL 扩展)
  缓存: Redis
  向量: Milvus（用于新闻语义检索）
任务调度: APScheduler
ML: XGBoost + scikit-learn
回测: vectorbt
前端: Next.js + TradingView 图表
告警: Webhook + 钉钉/飞书机器人
部署: Docker Compose
```

### 8.2 项目结构

```
sector-signal-agent/
├── app/
│   ├── agents/
│   │   ├── signal_agent.py        # 信号生成 agent
│   │   ├── analysis_agent.py      # LLM 分析 agent
│   │   └── monitor_agent.py       # 监控调度 agent
│   ├── signals/
│   │   ├── volume_signals.py
│   │   ├── capital_signals.py
│   │   ├── leader_signals.py
│   │   ├── policy_signals.py
│   │   └── sentiment_signals.py
│   ├── data/
│   │   ├── collectors/
│   │   ├── storage.py
│   │   └── cache.py
│   ├── backtest/
│   │   ├── engine.py
│   │   └── metrics.py
│   ├── ml/
│   │   ├── feature_engineering.py
│   │   └── xgboost_calibrator.py
│   ├── api/
│   │   ├── routes.py
│   │   └── websocket.py
│   └── main.py
├── tests/
├── notebooks/                      # 探索分析
├── docker-compose.yml
└── requirements.txt
```

### 8.3 实时监控调度

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

# 盘中每 5 分钟扫描一次
@scheduler.scheduled_job('cron', hour='9-11,13-15', minute='*/5')
async def intraday_scan():
    for sector in ALL_SECTORS:
        signal = await agent.evaluate_realtime(sector)
        if signal['rating'] in ['强信号', '中等信号']:
            await send_alert(signal)

# 收盘后全量分析
@scheduler.scheduled_job('cron', hour=15, minute=30)
async def daily_full_analysis():
    report = await agent.generate_daily_report()
    await save_to_db(report)
    await send_to_subscribers(report)

# 每周回测验证
@scheduler.scheduled_job('cron', day_of_week='sun', hour=20)
async def weekly_backtest():
    metrics = await backtest_engine.run(
        start=datetime.now() - timedelta(days=30),
        end=datetime.now()
    )
    await update_dashboard(metrics)
```

---

## 9. 输出示例

### 9.1 实时告警示例

```
🚨 板块启动信号告警
板块: 半导体（申万二级）
评分: 76 / 100 ⭐⭐⭐⭐⭐ 强信号
时间: 2026-06-13 10:30

【触发信号】
✓ 涨停家数突变: 12 家（5 日均值 1.2，z-score = 4.5）
✓ 龙头启动: 中芯国际连续 2 日涨停
✓ 板块扩散: 龙头启动后 1 日，板块涨停 12 家
✓ 北向资金异常: 今日净买入 23 亿（z-score = 2.8）
✓ 政策信号: 工信部公布"国家集成电路大基金三期"
✓ 研报突增: 今日 18 篇研报（30 日均值 4.2）

【LLM 分析】
当前处于"启动初期 - 中期"过渡阶段。
核心驱动: ①国家政策利好 ②龙头股技术突破 ③北向资金积极布局
预计持续 5-15 个交易日，最大涨幅预期 15-25%
风险点: 国际地缘风险、估值已偏高

【建议关注龙头】
1. 中芯国际 (688981) - 板块龙头
2. 北方华创 (002371) - 设备龙头
3. 韦尔股份 (603501) - 设计龙头

⚠️ 仅供研究参考，不构成投资建议
```

### 9.2 历史回测报告

```
2024-01-01 至 2026-06-12 回测结果
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
触发信号总数:     342
强信号数:         87
胜率（强信号）:   68.9%
胜率（中等）:     54.2%
平均最大涨幅:     12.7%
最大单次涨幅:     43.5%（AI 板块 2025-03）
夏普比率:         1.84
最大回撤:         -11.2%

【按板块拆解】
新能源车:  胜率 72%, 平均涨幅 15.3%
半导体:    胜率 68%, 平均涨幅 13.8%
军工:      胜率 61%, 平均涨幅 11.2%
医药:      胜率 52%, 平均涨幅 8.4%
房地产:    胜率 48%, 平均涨幅 7.1%（弱）
```

---

## 10. 风险与注意事项

### 10.1 技术风险

1. **数据质量**：免费数据源（akshare）偶尔延迟或缺失，需要多源校验
2. **过拟合**：参数在历史数据表现好，未来可能失效——必须 walk-forward 验证
3. **市场风格切换**：牛市/熊市信号有效性差异大，需分别建模
4. **黑天鹅事件**：政策突变、外部冲击会让所有信号失效

### 10.2 投资风险（非常重要）

⚠️ **本系统输出的是"信号"，不是"建议"**：

1. 历史回测胜率 ≠ 未来胜率
2. LLM 的判断有概率性错误
3. 板块启动 ≠ 个股一定涨（个股有自己的逻辑）
4. **任何实盘前必须用模拟盘验证 3-6 个月**
5. 仓位控制比信号准确度更重要

### 10.3 合规风险

- 不能宣称"AI 预测股市"
- 不能销售给用户作为"投资建议"
- 数据爬取要遵守 robots.txt 和服务条款
- 个人使用 OK，商用需要金融牌照

---

## 11. 开发路线图

### Phase 1: MVP (2-4 周)

- [x] 数据采集（板块行情、涨停数、资金流）
- [x] 基础信号（量价 + 龙头 + 简单融合）
- [x] 单板块单日测试
- [ ] CLI 输出

### Phase 2: 完整信号体系 (4-6 周)

- [ ] 完整 5 大类信号
- [ ] LLM 整合（Qwen/DeepSeek）
- [ ] 回测引擎
- [ ] 历史数据准备（5 年）

### Phase 3: ML 增强 (4 周)

- [ ] 特征工程
- [ ] XGBoost 校准模型
- [ ] Walk-forward 验证
- [ ] 参数自动优化

### Phase 4: 工程化 (4 周)

- [ ] 实时监控调度
- [ ] WebSocket 推送
- [ ] 告警系统
- [ ] Web 界面

### Phase 5: 持续优化

- [ ] 用户反馈收集
- [ ] 模型在线更新
- [ ] 多策略组合
- [ ] A/B 测试不同信号权重

---

## 12. 参考资源

### 数据源
- tushare: https://tushare.pro
- akshare: https://akshare.akfamily.xyz
- 同花顺 iFinD: https://www.51ifind.com
- 东方财富 Choice: https://choice.eastmoney.com

### 学术参考
- "Trend-following strategies and the cross-section of stock returns" - Moskowitz
- "Limit-up Limit-down Mechanism" 涨停板研究系列论文
- 《量化投资策略：如何实现超额收益》- 吴冬喜

### 开源项目
- vectorbt: 高性能回测框架
- backtrader: 经典回测框架
- ta-lib: 技术指标库
- LangGraph: agent 编排

---

## 13. 总结

这个 Agent 不是"预测股市"，而是**把分析师的工作流程自动化 + 规模化**：

1. **以前分析师手动**：每天看几十个板块的量价、资金、新闻
2. **现在 Agent 自动**：实时扫描所有板块，给出量化评分

**核心优势**：
- 不会疲劳、不会情绪化
- 多维度同时考量（人类容易忽略某些维度）
- 可解释（每个信号都有明确公式）
- 可回测（参数有效性可验证）

**核心限制**：
- 不能预测黑天鹅
- 不能完全替代人类判断
- 历史规律不一定持续有效

**最重要的一句**：信号系统是**辅助工具**，不是**决策机器**。最终决策永远在人，仓位和风控比信号精度更重要。

---

*本文档版本：v1.0*
*最后更新：2026-06-13*
