---
name: t-rotation
description: 板块轮动扫描 — 用东方财富 K-line 真实数据，计算 sectors.json 各板块的 1周/1月/3月涨跌幅中位数，排序输出热度表，帮助判断资金在哪个板块流入/流出。用法 /t-rotation [--window 1w|1m|3m] [--top N]。任何时候用户说"板块轮动"、"资金去哪了"、"哪个板块最强"、"CPO不行了资金去哪"、"板块热度"、"轮动分析"触发。**数据 100% 来自东方财富官方 K-line API，不是 LLM 估算。**
user-invocable: true
allowed-tools:

> 🚨 **拉数据铁律 (2026-07-29 v3.4 固化)**
>
> **跑这个 skill 前, 必须先调 `t-pull` skill 拉数据** (走 `tools/dump_data.py`):
> /t-rotation                    /t-rotation --window 1w        /t-rotation --top 5            /t-rotation --sector CPO       ```
```python
import json
with open("data/sectors.json") as f:
    sdata = json.load(f)
sectors = {k: v["codes"] for k, v in sdata["sectors"].items()
           if k != "_example" and len(v.get("codes", [])) >= 2}
```

##
```bash
python3 tools/ensure_fresh.py --watchlist

python3 tools/ensure_fresh.py --sector "{sector_name}"
```
```python
def pct(closes, n):
    if len(closes) < n + 1:
        return None
    return (closes[-1] / closes[-n-1] - 1) * 100

windows = {"1w": 5, "1m": 21, "3m": 63, "6m": 126}
```

每个板块取各股涨跌幅的**中位数**（避免单只异常股拉偏整体）。

##
按 1周涨跌 从高到低排列，同时展示 1月/3月 便于判断趋势：

```
板块轮动热度表 | 2026-07-09
数据来源: 🟢 东方财富 K-line API (前复权日K, 中位数)

板块            1周      1月      3月    样本   趋势信号
────────────────────────────────────────────────────
半导体封测    +9.3%  +29.1% +124.8%   4只   🔥 本周加速
半导体设备    +7.1%  +51.6% +132.7%   5只   🔥 强势持续
先进封装      +7.1%  +52.2% +132.7%   5只   🔥 强势持续
AI 芯片       +6.1%  +24.1%  +65.2%   3只   ↗ 稳步上行
半导体设计    +4.4%  +27.6%  +91.1%   5只   ↗ 稳步上行
─── 分界线: 以下板块本周资金流出 ───
AI 服务器     -1.4%  -11.4%  +73.9%   5只   ❄️ 1月回落
人形机器人    -2.2%   -6.9%  +19.3%   5只   ❄️ 1月回落
消费电子代工  -3.2%  -11.6%  +26.1%   4只   ❄️ 1月回落
新能源        -3.9%  -15.4%   +1.0%   5只   ❄️ 持续弱势
半导体材料    -7.0%  +42.0% +102.8%   5只   ⚠️ 1月涨多回调
稀土永磁      -8.5%   +8.9%  +29.6%   4只   ⚠️ 短期回调
光学          -8.6%   -8.4%  +14.1%   4只   ❄️ 持续弱势

💡 轮动解读:
  资金流入: 半导体设备/封测 (上游补涨逻辑)
  资金流出: AI服务器/CPO (3月涨70%+已充分定价)
  注意: 半导体材料 1月+42% 但本周-7% → 短期获利了结，非趋势反转
```

##
```
🔥 本周加速: 1周 > +5% AND 1月 > +20%
↗ 稳步上行: 1周 > 0% AND 1月 > 0%
❄️ 资金流出: 1周 < -1% AND 1月 < -5%
⚠️ 短期回调: 1周 < -3% BUT 3月 > +50% (涨多喘息，非趋势反转)
→ 观察: 其他情况
```

##
在热度表之后，输出 3 行文字解读：
1. **本周资金主要流入**: 最强 2-3 个板块 + 一句话逻辑
2. **本周资金主要流出**: 最弱 2-3 个板块 + 一句话逻辑
3. **值得关注的信号**: 如"3月大涨后本周回调"="获利了结不是趋势反转"

#
- **sectors.json 覆盖范围决定分析质量**: 只分析已收录的板块，新板块先跑 `/t-chain` 加进来
- **中位数 vs 均值**: 用中位数防止单只涨停/暴跌拉偏整体，更准确
- **前复权**: fqt=1 已处理分红/拆股，不影响涨跌计算
- **样本数 < 3**: 标注"(样本少，参考)"，不强制排除
- **curl 超时**: 单只 10-15 秒超时，失败的跳过并标注
- **代理 (2026-07-21 修正)**: 全部 API 走直连，不加 proxy。只有 `git pull/fetch/clone` 走 proxy，`git push` proxy+非proxy 都试（见 CLAUDE.md 数据抓取规则）

#
```
数据来源: 🟢 读 data/dump/{code}.json kline_daily 字段 (走 dump_data 路径, 7-30 改)
计算方式: ⚪ Python 派生 (close[-N]到close[-1]的百分比变化，各板块取中位数)
可验证: 任意一只股票，同样代码运行应得到相同结果 (±0.1% 浮点误差)
```

##
在热度表之后，对每个板块输出主力出货风险评估：

```
主力出货三特征（同时满足2个即预警）:
  1. 3月涨幅 > 50%（筹码已被大幅推高）
  2. 本周涨跌 → 由正转负（趋势反转）
  3. 1月涨幅 < 0（主力已撤，散户套牢）

典型案例: 人形机器人 (3月+59%, 本周-11.9%, 1月-0.9%) → 主力出货完成
稳健信号: 半导体设备 (3月+88%, 本周+1.6%, 1月+40%) → 资金仍在
```

**输出格式（板块汇总后加一段）:**
```
主力进出货快评:
  🔴 出货完成: 人形机器人(3月+59%,本周-12%) / 稀土永磁(本周-11%)
  🟠 出货中:   面板(3月+91%,本周-9%) / 存储(本周-5%)
  🟢 资金在位: 半导体设备 / AI芯片
```


###
> **每次分析必须输出：背驰状态 + 中枢位置 + 九种组合判断 + 操作建议**
> **数据源：** 腾讯K线（日线+60分钟），同v7，无需额外请求

####
```python
# 7-30 改: 不要自行实现 run_beichi / calc_beichi 缠论计算，读 dump analysis 字段即可:
from tools.analysis.analysis_data import AnalysisData
import json as _json
with open(f'data/dump/{code}.json') as _f:
    _dump = _json.load(_f)
_data = AnalysisData.from_raw(_dump)
_chan = (_data.analysis or {}).get('chan', {})

# 背驰 (各级别)
daily_beichi  = _chan.get('daily',  {}).get('beichi', '')   # 日线背驰状态字符串
weekly_beichi = _chan.get('weekly', {}).get('beichi', '')   # 周线背驰状态字符串
m60_beichi    = _chan.get('m60',    {}).get('beichi', '')   # 60分背驰状态字符串

# 中枢 (各级别)  {low, high, pos, valid, stop, t1, t2}
daily_hub  = _chan.get('daily',  {}).get('hub', {})
weekly_hub = _chan.get('weekly', {}).get('hub', {})
m60_hub    = _chan.get('m60',    {}).get('hub', {})

# 60分 K线读 dump (0 curl)
def fetch_60min(code, n=100):
    """7-30 改: 走 dump['kline_60m'], 0 curl
    先跑: bash tools/with_venv.sh python -m tools.dump_data {code} --force
    """
    from pathlib import Path as _Path
    _dp = _Path(f'data/dump/{code}.json')
    if not _dp.exists():
        print(f'⚠️ dump 不存在, 先跑: bash tools/with_venv.sh python -m tools.dump_data {code} --force')
        return []
    with open(_dp, encoding='utf-8') as _f2:
        _d = _json.load(_f2)
    rows = _d.get('kline_60m', [])[-n:] if n else _d.get('kline_60m', [])
    return [(r.get('date', r.get('day', '')),
             float(r.get('close', 0)),
             float(r.get('high', 0)),
             float(r.get('low', 0))) for r in rows]
```

####
```
📐 背驰 + 中枢分析 (v15)

【背驰】
  日线: [⚠️顶背驰/🟡弱背驰/✅无背驰/📉回调中]  面积段1→段2(比例%)
  60分: [同上]
  共振: [🔴日线+60分同时背驰→减仓加倍 / 🟡单级别→观察 / ✅均无→正常持]

【三级别中枢】（上沿/下沿必须给出具体数字）
  周线中枢(底仓): 下沿¥XXX  上沿¥XXX  当前¥XXX → [上方✅/内部⬜/下方⚠️]
                  → [上方:止损¥XXX / 内部:止损¥XXX目标¥XXX / 下方:目标¥XXX→¥XXX]
  日线中枢(中仓): 下沿¥XXX  上沿¥XXX  当前¥XXX → [上方✅/内部⬜/下方⚠️]
                  → [上方:止损¥XXX / 内部:止损¥XXX目标¥XXX / 下方:目标¥XXX→¥XXX]
  60分中枢(波动): 下沿¥XXX  上沿¥XXX  当前¥XXX → [上方✅/内部⬜/下方⚠️]
                  → [上方:止损¥XXX / 内部:止损¥XXX目标¥XXX / 下方:目标¥XXX→¥XXX]

【三级别汇总表】
  | 级别 | 下沿 | 上沿 | 宽度 | 当前位置 | 对应仓位 | 止损/目标 |
  |------|------|------|------|---------|---------|---------|
  | 周线 | ¥XXX | ¥XXX | XXX | 上方✅  | 底仓    | 止损¥XXX |
  | 日线 | ¥XXX | ¥XXX | XXX | 下方⚠️  | 中仓    | 目标¥XXX |
  | 60分 | ¥XXX | ¥XXX | XXX | 内部⬜  | 波动仓  | 止损¥XXX |

【九种组合判断】
  | 周线 | 日线 | 60分 | 状态 | 底仓 | 中仓 | 波动仓 |
  |------|------|------|------|------|------|--------|
  | 上方 | 上方 | 上方 | 🟢加速 | 持有 | 持有 | 持/加  |
  | 上方 | 上方 | 内部 | 🟢整理 | 持有 | 持有 | 等突破 |
  | 上方 | 上方 | 下方 | 🟡回调 | 持有 | 持有 | 空仓   | ← 当前
  | 上方 | 内部 | 上方 | 🟡反弹 | 持有 | 持有 | 等确认 |
  | 上方 | 内部 | 内部 | 🟡震荡 | 持有 | 减1/3| 空仓   |
  | 上方 | 内部 | 下方 | 🟠偏弱 | 持有 | 减1/3| 空仓   |
  | 上方 | 下方 | 任意 | 🔴危险 | 持有 | 全减  | 空仓   |
  | 内部 | 任意 | 任意 | 🔴受损 | 止损  | 全减  | 空仓   |
  | 下方 | 任意 | 任意 | 🔴结束 | 全清  | 全清  | 空仓   |
  当前位置: 周线[X] / 日线[X] / 60分[X]

【结论】
  当前状态: [状态描述]
  操作建议: [具体操作]
  止损价:   周线止损¥XXX / 日线止损¥XXX / 60分止损¥XXX
  下一个触发点: [加仓/减仓触发条件]
```

####
```
半导体设备: 背驰失效 → 用单股MA20偏离>30%替代
AI芯片/服务器: 背驰失效 → 用板块MA20偏离>30%
CPO/光学/封测: 背驰有效 → 面积<50%触发
其他板块: 参考CLAUDE.md §5.2
```


###
> **原理:** 缠论背驰量化版。价格创新高但MACD面积（∫hist dt）比上段收缩>50%，说明动力衰竭。
> **数学本质:** hist≈f''(x)加速度，area=∫hist dt≈ΔDIF，背驰=后段ΔDIF<前段×50%
> **双级别:** 日线定方向，60分钟定时机，两者共振才是强信号
> **实证(CPO 3-5月):** 上涨段1面积267→段2面积72(27%)→段3面积18(25%)，两次背驰后见顶

####
```python
def ema(prices, n):
    k=2/(n+1); e=[prices[0]]
    for p in prices[1:]: e.append(p*k+e[-1]*(1-k))
    return e

def calc_macd(closes):
    e12=ema(closes,12); e26=ema(closes,26)
    dif=[e12[i]-e26[i] for i in range(len(closes))]
    dea=ema(dif,9)
    hist=[2*(dif[i]-dea[i]) for i in range(len(dif))]
    return dif,dea,hist

def calc_area(hist, start, end):
        return sum(h for h in hist[start:end+1] if h > 0)

def beichi(hist, closes, t0, p1, t1, p2):
        area1 = calc_area(hist, t0, p1)
    area2 = calc_area(hist, t1, p2)
    ratio = area2/area1 if area1>0 else 1
    new_hi = closes[p2] > closes[p1]
    if new_hi and ratio < 0.5:
        return {'signal':'⚠️顶背驰', 'area1':area1,'area2':area2,'ratio':ratio,
                'action':'减波动仓1/3，底仓继续持'}
    elif new_hi and ratio < 0.8:
        return {'signal':'🟡弱背驰', 'area1':area1,'area2':area2,'ratio':ratio,
                'action':'观察，暂不减仓'}
    else:
        return {'signal':'✅无背驰', 'area1':area1,'area2':area2,'ratio':ratio,
                'action':'正常持有'}

def find_waves(closes):
    """找最近两个上涨段的端点 (t0,p1,t1,p2)"""
    peaks   = [i for i in range(3,len(closes)-3)
               if closes[i]==max(closes[i-3:i+4])]
    troughs = [i for i in range(3,len(closes)-3)
               if closes[i]==min(closes[i-3:i+4])]
    if len(peaks)<2 or len(troughs)<1: return None
    p1,p2   = peaks[-2],peaks[-1]
    t1_list = [t for t in troughs if p1<t<p2]
    t0_list = [t for t in troughs if t<p1]
    if not t1_list or not t0_list: return None
    return t0_list[-1], p1, t1_list[0], p2
```

####
```python
closes_daily = [float(x[2]) for x in kd]

import subprocess, json
def fetch_60min(code, n=100):
    """7-30 改: 走 t-pull 读 dump['kline_60m'], 0 curl

    旧版: subprocess.run(curl money.finance.sina.com.cn, WAF 风险)
    新版: 先 bash tools/with_venv.sh python -m tools.dump_data {code} --force
          读 data/dump/{code}.json 的 kline_60m 字段 (200 根)
    """
    import json as _json
    from pathlib import Path as _Path
    dump_path = _Path(f'data/dump/{code}.json')
    if not dump_path.exists():
        print(f'⚠️ dump 不存在, 先跑: bash tools/with_venv.sh python -m tools.dump_data {code} --force')
        return [], []
    with open(dump_path, encoding='utf-8') as _f:
        _d = _json.load(_f)
    rows = _d.get('kline_60m', [])[-n:] if n else _d.get('kline_60m', [])
    closes = [float(r.get('close', 0)) for r in rows]
    dates = [r.get('date', r.get('day', '')) for r in rows]
    return closes, dates
```

####
```python
dif_d,dea_d,hist_d = calc_macd(closes_daily)
waves_d = find_waves(closes_daily)
if waves_d:
    t0,p1,t1,p2 = waves_d
    result_d = beichi(hist_d, closes_daily, t0, p1, t1, p2)

closes_60, dates_60 = fetch_60min(code)
dif_60,dea_60,hist_60 = calc_macd(closes_60)
waves_60 = find_waves(closes_60)
if waves_60:
    t0,p1,t1,p2 = waves_60
    result_60 = beichi(hist_60, closes_60, t0, p1, t1, p2)
```

####
```
📊 MACD背驰 (v14.1):
  日线:    上涨段1 MM-DD→MM-DD 面积=XXX | 上涨段2 MM-DD→MM-DD 面积=XXX(比=XX%)
           → [⚠️顶背驰 减波动仓1/3 / 🟡弱背驰 观察 / ✅无背驰 正常持]
  60分钟:  上涨段1 MM-DD HH:mm→MM-DD HH:mm 面积=XXX | 上涨段2 面积=XXX(比=XX%)
           → [⚠️顶背驰 / 🟡弱背驰 / ✅无背驰]
  共振:    [🔴日线+60分钟同时背驰→强减仓 / 🟡单级别背驰→观察 / ✅两级别均无背驰]
```

####
```
顶背驰（减仓）:  后段面积 < 前段×50%  且  价格新高  → 减波动仓1/3
二次背驰:        连续两段背驰                        → 再减1/3
底背驰（加仓）:  回调段2面积 < 段1×50%  且  未创新低  → 可加波动仓
无背驰(扩张):    后段面积 > 前段                     → 主升浪，拿住

级别有效期:
  60分钟背驰 → 有效期6小时~2天   用于精确进场/出场时机
  日线背驰   → 有效期1周~3周     用于判断波段方向

共振规则:
  日线背驰 + 60分钟背驰同时触发  → 强信号，减仓力度加倍
  只有日线背驰                   → 标准信号，减波动仓1/3
  只有60分钟背驰                 → 弱信号，观察为主
  两级别均无背驰                 → 正常持有/可加仓
```


####
```
✅ 背驰有效场景：
   主升浪晚期，面积逐段递减（CPO案例：267→72→18）
   至少连续2段，后段/前段 < 50%

❌ 背驰失效场景（改用MA20偏离）：
   半导体设备：6/6只龙头背驰全部未触发，面积反而扩张（85%-258%）
   AI芯片/AI服务器：板块强势，每段都在加速

🟡 规则：
   背驰触发 → 减波动仓1/3
   背驰未触发 但 单股MA20偏离>30% → 同样减波动仓1/3
   MA20偏离是更通用的信号，背驰是补充确认
   优先级：单股MA20偏离>30% > 背驰
```

####
```
1. EMA天然延迟10-17天，背驰在段结束后才能确认
2. 波段识别依赖参数（前后3日），短小波段可能漏识别
3. 需要至少40根K线（日线40天，60分钟约5个交易日）
4. 背驰后价格仍可能继续涨（CPO案例：背驰后仍+30%）
   → 背驰=减波动仓信号，不是清仓信号，底仓不动
5. 60分钟在A股T+1限制下：信号触发后当天不能卖当天买的仓位
```
###
> **来源:** 北方华创 2年数据（2024-2026）+ 半导体设备板块验证
> **核心结论:** 日线背驰判断波段，周线背驰判断主升浪是否结束，两者缺一不可

####
```
60分钟背驰  →  有效期 6小时~2天    →  波段内短线进出
日线背驰    →  有效期 1周~3周      →  子浪顶底
周线背驰    →  有效期 1月~3月      →  主升浪是否结束

必须从高级别到低级别看：
  周线定主浪方向 → 日线定子浪位置 → 60分钟定进场时机
```

####
```
主升浪5浪结构（经典艾略特）：

  第1浪：试探性上涨，MACD面积小，成交量一般
  第2浪：回调深（可达第1浪的50-61.8%），洗盘
  第3浪：最强最长，MACD面积最大，成交量暴增  ← 不该卖
  第4浪：回调浅（第3浪的30-38.2%），不破第1浪顶
  第5浪：再创新高但MACD面积 < 第3浪  ← 这里才出现背驰

关键识别规则：
  第3浪 MACD面积 > 第1浪面积  → 扩张，加仓
  第5浪 MACD面积 < 第3浪面积×50%  → 周线顶背驰，主升浪结束
  当前在第N浪内部  → 日线背驰只是子浪信号，不是大顶
```

####
```
起点: 2024年 ¥216

第1浪: 2024下半年  216→298  +38%   MACD面积小
第2浪: 2025上半年  298→303  横盘震荡
第3浪: 2025下半年  303→523  +73%   MACD面积扩张（最强）
第4浪: 2025末     523→426  -19%   正常回调
第5浪: 2026年     426→935  +120%  面积132%>第3浪面积 → 周线无背驰

→ 周线背驰未触发 = 主升浪未结束
→ 2026-07月回调(-21%) = 第5浪内部的4浪调整
→ 大概率还有一段上涨
```

####
```
三种情况导致背驰失效：

① 市场狂热（新增量不断涌入）：
   每段由新入场资金推动，面积持续扩张
   信号：换手率持续新高，成交量每段放大

② 基本面持续升级（每段有新催化剂）：
   每段背后都有新订单/政策/业绩，不是惯性
   信号：季报超预期、新订单公告

③ 主升浪早中期（第1-4浪）：
   运动员还没累，每段都在加速
   信号：周线无背驰，面积在扩张

半导体设备2026年 = ②③同时满足，背驰完全失效
改用：单股MA20偏离>30% 替代
```

####
```
看周线：判断在第几浪
  周线无背驰（面积扩张） → 主升浪进行中，日线回调是买点
  周线弱背驰（面积<80%） → 主升浪晚期，开始控制仓位
  周线顶背驰（面积<50%） → 主升浪结束，底仓也要减

看日线：判断子浪位置
  日线无背驰 + 价格在MA20上方 → 子浪上涨中，持有
  日线顶背驰 + 周线无背驰     → 子浪顶部，减波动仓1/3
  日线底背驰 + 缩量长下影     → 子浪底部，加波动仓

看60分钟：精确进场
  日线确认底部区域后
  等60分钟底背驰触发 → 这一刻买入
  比日线信号精确1-2天

优先级：周线 > 日线 > 60分钟
单独看任何一个级别都可能误判
```

####
```
周线：第5浪，面积132%，无背驰 → 主升浪未结束
日线：从935回调到743，-21%   → 第5浪内部4浪调整
60分钟：还在下跌中，无底背驰  → 未止跌

操作：
  等日线底背驰 + 缩量长下影  → 加波动仓
  等周线MA20偏离再次>40%    → 认真考虑离场
  周线顶背驰触发时           → 底仓也减
```

###
> **核心原则：三个级别中枢永远都要算，各管一层仓位，含义随价格位置变化**

####
```
周线中枢（每段1-3个月）  → 管底仓
日线中枢（每段1-4周）    → 管中仓
60分中枢（每段数小时）   → 管波动仓
```

####
```
情况A：价格在中枢上方（健康持有）
  下沿 = 支撑 / 止损线        ← 跌破就减仓
  上沿 = 已突破，向上顺势
  目标 = 上沿 + 中枢宽度（等幅）

情况B：价格在中枢内部（横盘）
  下沿 = 支撑 / 止损           ← 跌破看空
  上沿 = 压力 / 突破目标       ← 站上看多
  方向未定，等突破

情况C：价格在中枢下方（已跌出）
  下沿 = 第一阻力 / 第一目标   ← 反弹到这里是第一关
  上沿 = 第二阻力 / 第二目标
  止损 = 用结构低点（不用中枢，中枢在上面无法做止损）
```

####
| 层级 | 仓位 | 止损基准 | 目标 | 离场 |
|------|------|---------|------|------|
| **逆势仓** | 10-15% | 结构低点 - 1×ATR | 60分中枢下沿（第一目标） | 跌破止损立割 |
| **底仓** | 25-30% | 底背驰确认低点 | 日线中枢下沿 | 周线背驰 |
| **中仓** | 20-25% | 日线中枢下沿 | 日线中枢上沿 | 日线顶背驰 |
| **波动仓** | 20-25% | 60分中枢下沿 | MA20偏离>30% | 日线顶背驰 |

####
```
价格在中枢下方时：
  逆势仓：PEG<1.0 + RSI<25 + 止跌信号（不等中枢）
  底仓：  日线底背驰 + 3天不创新低
  中仓：  价格突破日线中枢下沿并站稳
  波动仓：60分底背驰 + 价格在60分中枢内或上方

价格在中枢内部/上方时：
  底仓：  周线中枢回踩（中枢上沿支撑）
  中仓：  日线中枢回踩（中枢上沿支撑）
  波动仓：60分底背驰
```

####
```
跌破结构低点-1×ATR  → 逆势仓全减（阶段一止损）
跌破60分中枢下沿     → 波动仓全减
跌破日线中枢下沿     → 中仓全减
跌破周线中枢下沿     → 底仓全减（主升浪结束）
```

####
```
📋 三层仓位策略 | {code} {name} | {date}

【三级别中枢汇总】
  周线中枢: ¥XXX~¥XXX  当前[上方✅/内部⬜/下方⚠️]  → [止损¥XXX / 等方向 / 目标¥XXX]
  日线中枢: ¥XXX~¥XXX  当前[上方✅/内部⬜/下方⚠️]  → [止损¥XXX / 等方向 / 目标¥XXX]
  60分中枢: ¥XXX~¥XXX  当前[上方✅/内部⬜/下方⚠️]  → [止损¥XXX / 等方向 / 目标¥XXX]

【逆势仓 10-15%】（仅当价格在日线中枢下方时）
  进场：PEG<1.0 + RSI<25 + 止跌信号
  止损：¥XXX（结构低点-1×ATR）
  目标：¥XXX（60分/日线中枢下沿）

【底仓 25-30%】（日线底背驰后建 / 或周线中枢上方直接建）
  进场：底背驰确认 / 周线中枢上方
  止损：¥XXX（底背驰低点 / 周线中枢下沿）
  目标：¥XXX（日线中枢下沿 / 周线中枢上沿）

【中仓 20-25%】（日线中枢上方建）
  进场：价格站上日线中枢下沿
  止损：¥XXX（日线中枢下沿跌破减）
  目标：¥XXX（日线中枢上沿）

【波动仓 20-25%】
  进场：60分底背驰
  止损：¥XXX（60分中枢下沿）
  减仓：MA20偏离>30%（¥XXX）

【止损阶梯】
  ¥XXX → 逆势仓止损
  ¥XXX → 波动仓全减（60分中枢下沿）
  ¥XXX → 中仓全减（日线中枢下沿）
  ¥XXX → 底仓全减（周线中枢下沿）

【目标价】
  ¥XXX（60分中枢下沿）→ ¥XXX（日线中枢下沿）→ ¥XXX（日线中枢上沿）→ ¥XXX（周线中枢上沿）
```

####
```
三级别全在下方：
  周线中枢: ¥140~¥181  下方-33⚠️  → 目标¥140(第一)→¥181(第二)
  日线中枢: ¥141~¥167  下方-34⚠️  → 目标¥141(第一)→¥167(第二)
  60分中枢: ¥126~¥130  下方-19⚠️  → 目标¥126(最近)→¥130

操作路径：
  等止跌信号 → 逆势仓¥108，止损¥90，目标¥126（60分中枢下沿）
  60分中枢突破 → 底仓，止损¥101，目标¥141
  日线中枢突破 → 中仓，止损¥141，目标¥167
  周线中枢内部 → 波动仓，止损¥140，目标¥181
```

###
> **完整版：涵盖价格在中枢任何位置的操作建议，包含建仓和持仓两种状态**

####
| 周线 | 日线 | 60分 | 状态 | 有仓位操作 | 无仓位操作 |
|------|------|------|------|-----------|-----------|
| 上方 | 上方 | 上方 | 🟢主升浪加速 | 持有/加波动仓 | 底仓+中仓可建 |
| 上方 | 上方 | 内部 | 🟢健康整理 | 持有 | 等60分突破上沿再加 |
| 上方 | 上方 | 下方 | 🟡正常回调 | 底/中仓持有，波动仓空 | 等60分止跌信号 |
| 上方 | 内部 | 上方 | 🟡短线反弹 | 底仓持有，等日线确认 | 等日线中枢突破 |
| 上方 | 内部 | 内部 | 🟡中线震荡 | 持有，减波动仓 | 等方向确认 |
| 上方 | 内部 | 下方 | 🟠中线偏弱 | 减中仓1/3 | 不建 |
| 上方 | 下方 | 任意 | 🔴中线危险 | 减中仓全部 | 不建 |
| **内部** | **任意** | **任意** | 🟠**主升浪受损** | **减底仓至逆势仓比例** | **止跌信号触发可建逆势仓** |
| **下方** | **下方** | **下方** | 🔴**三级全跌出** | **底仓减至10%，等反转** | **等止跌信号建逆势仓** |
| **下方** | **下方** | **内部** | 🟡**60分开始修复** | **持逆势仓，等日线信号** | **60分中枢内可小建** |
| **下方** | **下方** | **上方** | 🟡**60分突破** | **加底仓** | **可建底仓** |
| **下方** | **内部** | **任意** | 🟡**日线修复中** | **加中仓** | **可建中仓** |
| **下方** | **上方** | **任意** | 🟢**日线修复完成** | **三仓框架恢复正常** | **可建全仓** |

**规则：看哪个级别，就管哪层仓位**
```
60分钟位置 → 管波动仓
日线位置   → 管中仓
周线位置   → 管底仓
```

####
```
核心：价格远低于中枢时，中枢变成目标而非止损
     止损改用结构低点（近期最低价 - 1×ATR）

建仓条件（无仓位时）：
  逆势仓（10-15%）：PEG<1.0 + RSI<25 + 止跌信号（缩量+长下影+次日不创新低）
  底仓（25-30%）：  日线底背驰 + 60分中枢站上 → 信号触发次日建
  中仓（20-25%）：  价格站回日线中枢下沿并维持
  波动仓（20-25%）：60分底背驰 + 在60分中枢内或上方

目标路径（从近到远）：
  60分中枢下沿 → 60分中枢上沿 → 日线中枢下沿 → 日线中枢上沿 → 周线中枢下沿 → 周线中枢上沿
```

####
```
价格在中枢上方时（正常持有）：
  跌破60分中枢下沿  → 波动仓全减
  跌破日线中枢下沿  → 中仓全减
  跌破周线中枢下沿  → 底仓全减

价格在中枢下方时（逆势仓/底仓阶段）：
  跌破结构低点-1×ATR → 逆势仓全减（唯一止损线）
  底背驰低点跌破      → 底仓减半
  60分中枢跌破        → 波动仓全减
```

####
```
三级别中枢：
  周线: ¥140~¥181  下方⚠️  → 目标第一关¥140，止损¥90
  日线: ¥141~¥167  下方⚠️  → 目标第一关¥141
  60分: ¥126~¥130  下方⚠️  → 目标第一关¥126（最近）

对应：「下方/下方/下方」→ 🔴三级全跌出

无仓位操作：
  等止跌信号（缩量+长下影+次日不创新低）
  → 触发后建逆势仓10-15%，止损¥90，第一目标¥126

有仓位操作：
  底仓减至10%以内（主升浪受损）
  等¥126站稳 → 加底仓
  等¥141站稳 → 加中仓
  等¥181附近 → 三仓框架恢复正常

中枢重心趋势：🔴下移（近期中心价持续下降）
修复条件：价格站回60分中枢下沿¥126并维持
```

###
> **核心结论：中枢给位置，背驰给时机，止跌信号给确认**
> **三个缺一不可：只有位置没有背驰→接飞刀；只有背驰没有位置→没有目标；只有止跌没有背驰→可能是假反弹**

####
**要素一：中枢位置（在哪里进）**
```
中枢下沿在上方 → 第一目标/阻力（不在这里进，在下面等信号）
中枢内部       → 支撑已知，可以建仓
中枢上方       → 回踩上沿是加仓点

三级对应：
  周线中枢 → 底仓位置参考
  日线中枢 → 中仓位置参考
  60分中枢 → 波动仓位置参考
```

**要素二：背驰（什么时候进）**
```
底背驰条件：
  回调段2的MACD面积 < 回调段1面积×50%
  且价格未创新低
  → 说明下跌动力衰竭，趋势可能反转

三级对应：
  60分底背驰 → 波动仓进场时机
  日线底背驰 → 中仓/底仓进场时机
  周线底背驰 → 大底信号（罕见，出现必加仓）

背驰分板块有效性（见§5.2）：
  CPO/光学/封测：有效
  半导体设备/AI链：失效，用MA20偏离>30%替代
```

**要素三：止跌信号（确认进场）**
```
三个条件同时满足：
  ① 缩量：当天成交量 < 20日均量×0.85
  ② 长下影：（收盘-最低）/（最高-最低）> 40%
  ③ 次日不创新低：次日最低价 ≥ 今天最低价

含义：
  ① 抛压消失（没人继续割肉）
  ② 低位有人接（盘中砸下去被买回来）
  ③ 低点成立（不是偶然接盘）

实证（CPO 3-5月）：命中率91%（10/11个底部）
```

####
```
三个都满足 → 最强信号，满足条件的仓位全建
两个满足   → 轻仓试探，减半建仓
一个满足   → 等，不进

各笔入场条件：

第1笔（5%）：周线中枢内部 + 止跌信号（无需背驰，信号弱）
第2笔（10%）：60分底背驰 + 止跌信号（核心进场点）
第3笔（15%）：60分中枢站稳 + 日线底背驰（双重确认）
第4笔（10%）：日线中枢站稳 + 日线无顶背驰（趋势确认）
```

####
```python
def check_stop_signal(closes, highs, lows, vols, i):
    """
    检查第i天是否出现止跌信号
    返回：(信号1缩量, 信号2长下影, 信号3次日不创新低, 三者同时满足)
    """
    if i < 20 or i >= len(closes)-1: return False,False,False,False
    vol_ma20 = statistics.mean(vols[i-20:i])
    total = highs[i]-lows[i] if highs[i]>lows[i] else 0.001
    lower_shadow = (min(opens[i],closes[i])-lows[i])/total

    sig1 = vols[i] < vol_ma20*0.85              sig2 = lower_shadow > 0.40                  sig3 = lows[i+1] >= lows[i]*0.995      
    return sig1, sig2, sig3, (sig1 and sig2 and sig3)
```

####
| 股票 | 回调次数 | 止跌信号命中 | 次日平均涨幅 |
|------|---------|------------|------------|
| 中际旭创 | 4次 | 4/4 | +5.0% |
| 沪电股份 | 3次 | 3/3 | +4.3% |
| 胜宏科技 | 4次 | 3/4 | +4.8% |
| **合计** | **11次** | **10/11=91%** | **+4.7%** |
##
> **原理：A股个股受大盘影响约60-70%，先看大盘再看个股是基本纪律。**
> **数据源：** 腾讯 K-line（同个股 K-line，字段相同，指数代码前缀 sh/sz）

###
```bash
```

###
```
大盘信号 → 个股仓位上限
───────────────────────────────
三指数全多头排列           → 满仓（80%+）
创业板+科创多头，沪深混乱  → 偏成长仓位（70%，多配科技）
沪深多头，创业板空头       → 偏价值仓位（70%，多配蓝筹）
三指数全空头排列           → 防御（30%以下）
三指数RSI全<35（超卖）     → 可轻仓抄底（逆向信号）
```

###
```python
rel_strength = 创业板近20日涨跌幅 - 沪深300近20日涨跌幅

rel > +3%  → 资金偏向成长/科技，利好 AI/半导体/机器人等持仓
rel < -3%  → 资金偏向价值/周期，利好银行/能源/消费等
中性区间   → 无明显风格偏好
```

###
```
📊 大盘背景 (今日必查)
  创业板指: 3851  MA20偏-5.9%  RSI=37  ⚠️跌破MA60  EMA死叉
  科创50:   2010  MA20偏+0.0%  RSI=51  →混乱       EMA金叉  近60日+43%
  沪深300:  4797  MA20偏-1.7%  RSI=43  ⚠️跌破MA20   EMA死叉

  风格：创业板 vs 沪深300 近20日 = -2.6%（价值偏强）
  仓位建议：⚠️ 大盘偏弱，个股仓位控制在50%以下
  ATR止损：创业板ATR=169(4.4%)，持成长股止损宽度参考此值
```

###
```
创业板跌破MA60 + RSI<40 + 成交量萎缩（vol<0.9）:
  → 通常是短暂超卖，3-5个交易日内反弹概率>70%
  → 但趋势未变（空头排列），反弹后可能继续跌

创业板多头排列 + RSI 50-70 + 北向资金净流入:
  → 科技成长股最强进攻窗口
  → 持仓可以扩张至满仓

创业板 vs 沪深300 差值连续2周 > +5%:
  → 成长股主升浪，不要买价值股
  → 优先 AI/半导体/机器人等主线

当前状态（2026-07-14）:
  创业板 跌破MA60，RSI=37，缩量（0.85）
  → 技术面弱势整理，但未到恐慌
  科创50 MA20附近，RSI=51，相对强（近60日+43%）
  → 科创主线依然健康，只是短期回调
  结论: 个股优先选科创50成分股，创业板整体偏弱
```



###
```python
python3 -c "
import subprocess,json,statistics
indices=[(sz399006,创业板),(sh000688,科创50),(sh000300,沪深300)]
results={}
for idx,name in indices:
    try:
        kd=json.loads(r.stdout.decode('utf-8','ignore'))[\data'][idx][\day']
        closes=[float(x[2]) for x in kd];vols=[float(x[5]) for x in kd];p=closes[-1]
        def ma(n):return statistics.mean(closes[-n:]) if len(closes)>=n else None
        m20=ma(20);m60=ma(60)
        gains=[max(closes[i]-closes[i-1],0) for i in range(1,len(closes))]
        losses=[max(closes[i-1]-closes[i],0) for i in range(1,len(closes))]
        ag=statistics.mean(gains[-14:]);al=statistics.mean(losses[-14:])
        rsi=100-100/(1+ag/al) if al>0 else 100
        pct20=(closes[-1]/closes[-21]-1)*100 if len(closes)>=21 else 0
        if m20 and p>m20 and m60 and p>m60:arr='✅均上'
        elif m20 and p<m20:arr='⚠️跌破MA20'
        elif m60 and p<m60:arr='⚠️跌破MA60'
        else:arr='→混乱'
        results[name]=dict(p=p,rsi=rsi,arr=arr,pct20=pct20,d20=(p/m20-1)*100 if m20 else 0)
    except:pass
print('📊 大盘背景:')
for name,r in results.items():print(f'  {name}: {r[\"p\"]:.0f}  MA20偏{r[\"d20\"]:+.1f}%  RSI={r[\"rsi\"]:.0f}  {r[\"arr\"]}'  )
if '创业板' in results and '沪深300' in results:
    rel=results['创业板']['pct20']-results['沪深300']['pct20']
    print(f'  风格: 创业板vsA50={rel:+.1f}% ({\"成长强\" if rel>3 else \"价值强\" if rel<-3 else \"中性\"})' )
rsi_avg=statistics.mean([r['rsi'] for r in results.values()])
n_warn=sum('⚠️' in r['arr'] or '❄️' in r['arr'] for r in results.values())
if rsi_avg<38:print('  🟡 三指数偏超卖，关注反弹机会')
elif n_warn>=2:print('  ⚠️ 大盘偏弱，仓位控制50%以下')
else:print('  🟢 大盘尚可，正常持仓')
"
```



###
```python
python3 -c "
import subprocess, json
stocks=[('usNVDA','英伟达'),('usSMH','费城半导体ETF'),('usQQQ','纳斯达克QQQ')]
print('🌏 美股前夜:')
for code,name in stocks:
    try:
        p=r.stdout.decode('utf-8','ignore').split('~')
        price=float(p[3]) if len(p)>3 else 0
        chg=float(p[32]) if len(p)>32 else 0
        flag='🟢' if chg>0 else '🔴' if chg<0 else '⬜'
        alert=' ⚡高开预警' if (code=='usNVDA' and chg>3) else ''
        print(f'  {flag}{name}: \${price:.1f}  {chg:+.2f}%{alert}')
    except: print(f'  {name}: 获取失败')
try:
    nvda_chg=float(nvda_r.stdout.decode('utf-8','ignore').split('~')[32])
    smh_chg=float(smh_r.stdout.decode('utf-8','ignore').split('~')[32])
    if nvda_chg>3 and smh_chg>2:print('  → 算力/半导体情绪强，A股科创板今日大概率高开')
    elif nvda_chg<-3 or smh_chg<-2:print('  → 美股半导体大跌，科创50今日注意低开风险')
    else:print('  → 美股平稳，无特殊信号')
except:pass
"
```

**输出示例：**
```
🌏 美股前夜:
  🟢英伟达: $211.8  +4.06% ⚡高开预警
  🟢费城半导体ETF: $600.3  +2.51%
  🟢纳斯达克QQQ: $719.7  +1.12%
  → 算力/半导体情绪强，A股科创板今日大概率高开
```

**注意：** 美股数据为前一交易日收盘价（A股开盘前参考），非实时。英伟达>+3%为高开预警阈值。



#
**三指标打分 → 方法优先级矩阵**（代码见 t-analyze SKILL.md §2n）

| 总分 | 状态 | 缠论背驰 | 威科夫(Accumulation) | SMC-OB |
|------|------|---------|-----------|--------|
| 7-9 | 🚀主升浪 | ❌禁用 | ✅主用 | ⚠️辅助 |
| 4-6 | 🔄过渡回调 | ✅主用 | ✅辅助 | ⚠️辅助 |
| 0-3 | ⬇️震荡下跌 | ⚠️谨慎 | ⚠️等Accumulation确认 | ✅主用 |

```python
# 7-30 改: 不用 exec('/tmp/market_state.py'), 读 data.analysis 字段:
# from tools.analysis.analysis_data import AnalysisData
# _data = AnalysisData.from_raw(dump)
# market = (_data.analysis or {}).get('market_state', {})
# score = market.get('score', 0)
# state = market.get('state', '未知')
# print(format_market_state(code, name, market, closes[-1]))
```

#
> 缠论失效时的补充手段，全部基于同一K线数据源（腾讯OHLCV），无需新API。

##
| 场景 | 缠论问题 | 补充方法 | 触发条件 |
|------|---------|---------|---------|
| 震荡市 | 背驰面积比噪音 | SMC Order Block | 涨幅<20%无明显段结构 |
| 真假突破 | 段面积扩张 | 量价：放量确认 | vol_ratio>1.5 才算真突破 |
| 主升浪 | 背驰失效 | 威科夫 Markup 阶段 | MA20偏离>20%替代 |
| 底部确认 | 底背驰后还跌 | 威科夫Accumulation | 缩量假跌破=弹簧测试 |
| 信号过滤 | 单股假信号 | 多市场共振 | 个股+板块+大盘三向同向 |

##
```python
# 7-30 改: 不用 exec('/tmp/supplement_analysis.py'), 读 data.analysis 字段:
# from tools.analysis.analysis_data import AnalysisData
# _data = AnalysisData.from_raw(dump)
# wyckoff_stage = (_data.analysis or {}).get('wyckoff', {}).get('stage')
# # 只有三个合法阶段: Accumulation / Markup / Distribution
# vp = (_data.analysis or {}).get('volume_price', {})
# smc = (_data.analysis or {}).get('smc', {})
# resonance = (_data.analysis or {}).get('resonance', {})
```

##
所有计算基于 dump['kline_daily'] / dump['kline_60m'] 字段 (7-30 改, 原腾讯OHLCV字段映射相同)：
- opens/closes/highs/lows → SMC（纯价格结构）
- closes + vols → OBV + vol_ratio（量价分析）
- closes（个股+大盘指数） → 多市场共振方向对比
- 全部字段 → 威科夫阶段识别 (只有 Accumulation/Markup/Distribution 三大阶段)

详细算法说明见 README §9。

