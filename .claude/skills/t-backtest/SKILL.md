---
name: t-backtest
description: 信号回测 — 扫描 N 年历史，统计指定信号触发后未来 N 天的最大涨幅命中率。任何时候用户说"回测"、"信号胜率"、"历史统计"、"信号效果"、"backtest"时触发。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
# 单信号回测
/t-backtest --signal Spring
/t-backtest --signal 1买
/t-backtest --signal fflow:强进货
/t-backtest --signal Accumulation

# 组合信号 (AND)
/t-backtest --signal Spring --signal fflow:强进货

# 参数
/t-backtest --signal Spring --days 30 --threshold 10
/t-backtest --lookback 3y   # 回看3年 (默认5y)
/t-backtest --codes 300274  # 只跑这只
/t-backtest --all           # 全 watchlist (默认只跑持仓)
/t-backtest --portfolio     # 只跑持仓 (7只)
/t-backtest --write-md      # 输出到 docs/backtest-*.md
```

## 信号列表

**威科夫子事件:**
```
Spring / LPS / EVR / SOS / Compression / TrendPullback / MarkupEntry / DistributionStart / UTAD
```

**缠论买卖点:**
```
1买 / 1买⭐ / 2买 / 3买 / 双中枢 / 笔结束 / 吞没
```

**威科夫阶段:**
```
Accumulation / Markup / Distribution / Markdown
```

**主力资金:**
```
fflow:强进货 / fflow:偏进货 / fflow:中性 / fflow:偏出货 / fflow:强出货
```

**场景:**
```
scene:A / scene:B / scene:C / scene:D / scene:E
```

**示例组合:**
```
--signal Spring --signal fflow:强进货     # Spring + 主力进货
--signal Accumulation --signal 1买        # 吸筹末段 + 缠论1买
--signal Markup --signal fflow:偏进货     # 主升浪 + 主力偏进货
```

## 算法 (3 步)

### Step 1: 批量加载数据 (0 网络)

从 parquet 读全量数据，内存拼接 RawContext:

```python
# 全市场 5y daily 一次读完 (DuckDB SQL)
# 内存: 400只 × 1250天 × 5字段 ≈ 50万行 ≈ 100MB
```

### Step 2: 扫描信号 (step=1, 精筛不漏)

对每只股票:
- `engine.analyze_history(ctx, dates)` — 5年逐日分析 (~30秒/只)
- 命中判定: `rows[i]` 满足 `--signal` 条件 → 记录 `(date, price)`
- 并发: 持仓 7 只串行约 3.5 分钟; 全 watchlist 61 只可后台

### Step 3: 算未来涨幅

对每个命中日:
- 找到后续 `N` 个交易日的价格 (最高点)
- `max_return = max_future_price / price_at_signal - 1`
- `hit = max_return > threshold%`

## 输出格式

```
📊 回测报告: [Spring] | 5年 (2021-08 ~ 2026-08) | 持仓期30天 | 阈值10%

命中 12 次 | 命中率 67% (8/12) | 均涨幅 +18.3% | 中位 +14.2% | 最大 +42%
  失败 4 次 | 均跌幅 -5.1%

月度分布:
  2024-01 Spring @¥42.1 → 30日最大 +23% ✅
  2024-06 Spring @¥38.7 → 30日最大 +8%  ❌
  ...

总计: 持仓7只, 命中42次
```

## Step 1: 写回测脚本

```bash
cat > /tmp/backtest_run.py << 'PYEOF'
import sys, time, json
sys.path.insert(0, '.')
from pathlib import Path

# ===== 参数 =====
SIGNALS = ["Spring"]
DAYS = 30
THRESHOLD = 10.0  # 涨幅阈值%
LOOKBACK_YEARS = 5
CODES = None  # None=持仓, or list
WRITE_MD = True

# ===== 1. 加载数据 =====
t0 = time.time()
from tools.kline_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine

ds = DataStore()
engine = AnalysisEngine()

if CODES is None:
    # 只跑持仓
    wl = json.load(open('data/watchlist.json'))['stocks']
    CODES = [s['code'] for s in wl if s.get('list_type') == '持仓']
    print(f"持仓: {CODES}")

print(f"加载数据: {time.time()-t0:.1f}s")

# ===== 2. 回测函数 =====
def match_signal(result, signals):
    """检查一个 AnalysisResult 是否匹配信号列表 (AND)"""
    raw = result.raw
    wy = raw.get('wyckoff') or {}
    chan = raw.get('chan') or {}
    bsp = chan.get('buy_sell_points', {}) if isinstance(chan, dict) else {}
    fflow_v = raw.get('fflow', {}).get('verdict', '')
    scene = result.scene or ''

    for sig in signals:
        sig = sig.strip()
        hit = False
        # 威科夫子事件
        if sig in ('Spring','LPS','EVR','SOS','Compression','TrendPullback','MarkupEntry','DistributionStart','UTAD'):
            se = wy.get('sub_events', [])
            if isinstance(se, list):
                hit = any(isinstance(e,dict) and e.get('name') == sig for e in se)
        # 缠论买卖点
        elif sig in ('1买','1买⭐','2买','3买','双中枢','笔结束','吞没'):
            daily_bsp = bsp.get('daily', {}) if isinstance(bsp, dict) else bsp
            if isinstance(daily_bsp, dict):
                hit = sig in daily_bsp
        # 威科夫阶段
        elif sig in ('Accumulation','Markup','Distribution','Markdown'):
            hit = wy.get('stage') == sig
        # fflow
        elif sig.startswith('fflow:'):
            label = sig.split(':',1)[1]
            hit = label in fflow_v
        # scene
        elif sig.startswith('scene:'):
            label = sig.split(':',1)[1]
            hit = scene == label
        if not hit:
            return False
    return True

def max_forward_return(kline, signal_date, days):
    """从 signal_date 起 N 个交易日的最大涨幅"""
    dates = [k['trade_date'] for k in kline]
    if signal_date not in dates:
        return None, None
    idx = dates.index(signal_date)
    buy_price = kline[idx]['close']
    future = kline[idx+1:idx+1+days]
    if not future:
        return None, None
    max_price = max(k['high'] for k in future)
    return (max_price / buy_price - 1) * 100, buy_price

# ===== 3. 跑回测 =====
results_all = []

for code in CODES:
    t1 = time.time()
    ctx = ds.get_ctx(code)
    if not ctx.kline:
        print(f"  ⚠️ {code} 无数据")
        continue

    kline = ctx.kline
    # 截取 lookback 年
    cutoff = kline[-1]['trade_date']
    import datetime
    cutoff_dt = datetime.datetime.strptime(cutoff[:8], '%Y%m%d')
    from datetime import timedelta
    lookback_dt = cutoff_dt - timedelta(days=LOOKBACK_YEARS * 365)
    lookback_str = lookback_dt.strftime('%Y%m%d')
    kline = [k for k in kline if k['trade_date'] >= lookback_str]
    ctx = ds.get_ctx(code)
    ctx.kline = kline

    dates = [k['trade_date'].replace('-','')[:8] for k in kline]
    print(f"  {code}: {len(kline)}根K线, {len(dates)}步 (step=1)...", end='', flush=True)

    rows = engine.analyze_history(ctx, dates)
    print(f" {time.time()-t1:.0f}s")

    hits = []
    for d, r in rows.items():
        if match_signal(r, SIGNALS):
            ret, price = max_forward_return(kline, d, DAYS)
            hits.append({'date': d, 'price': price, 'return': ret, 'code': code})

    results_all.extend(hits)
    if hits:
        print(f"    命中 {len(hits)} 次")

# ===== 4. 统计 =====
print(f"\n{'='*60}")
print(f"信号: {SIGNALS} | 回看{LOOKBACK_YEARS}年 | 持仓期{DAYS}天 | 阈值{THRESHOLD}%")
print(f"总计命中: {len(results_all)} 次")

valid = [h for h in results_all if h['return'] is not None]
if valid:
    hits_only = [h for h in valid if h['return'] >= THRESHOLD]
    returns = [h['return'] for h in valid]
    print(f"命中率: {len(hits_only)}/{len(valid)} = {len(hits_only)/len(valid)*100:.0f}%")
    print(f"均涨幅: {sum(returns)/len(returns):+.1f}%")
    print(f"中位涨幅: {sorted(returns)[len(returns)//2]:+.1f}%")
    print(f"最大涨幅: {max(returns):+.1f}%")
    print(f"最大跌幅: {min(returns):+.1f}%")

    # 逐条列出
    print(f"\n明细:")
    for h in sorted(valid, key=lambda x: x['date']):
        flag = '✅' if h['return'] >= THRESHOLD else '❌'
        print(f"  {h['date']} {h['code']} @{h['price']:.2f} → {h['return']:+.1f}% {flag}")

    if WRITE_MD:
        md_lines = [f"# 回测报告\n\n"]
        md_lines.append(f"**信号:** `{' + '.join(SIGNALS)}` | 回看{LOOKBACK_YEARS}年 | 持仓{DAYS}天 | 阈值{THRESHOLD}%\n\n")
        if valid:
            md_lines.append(f"| 次数 | 命中率 | 均涨幅 | 中位 | 最大 | 最小 |\n")
            md_lines.append(f"|---|---|---|---|---|---|\n")
            md_lines.append(f"| {len(valid)} | {len(hits_only)}/{len(valid)} ({len(hits_only)/len(valid)*100:.0f}%) | {sum(returns)/len(returns):+.1f}% | {sorted(returns)[len(returns)//2]:+.1f}% | {max(returns):+.1f}% | {min(returns):+.1f}% |\n\n")
            md_lines.append(f"| 日期 | 代码 | 买入价 | 30日最大涨幅 | 结果 |\n")
            md_lines.append(f"|---|---|---|---|---|\n")
            for h in sorted(valid, key=lambda x: x['date']):
                flag = '✅' if h['return'] >= THRESHOLD else '❌'
                md_lines.append(f"| {h['date']} | {h['code']} | ¥{h['price']:.2f} | {h['return']:+.1f}% | {flag} |\n")
        sig_str = '_'.join(s.replace(' ', '_') for s in SIGNALS)
        out_path = Path(f'docs/backtest-{sig_str}.md')
        out_path.write_text(''.join(md_lines), encoding='utf-8')
        print(f"\n📄 {out_path}")

print(f"\n总耗时: {time.time()-t0:.0f}s")
PYEOF
```

## Step 2: 运行

```bash
# 先跑持仓 (7只, 约3.5分钟)
/t-backtest --signal Spring --days 30 --threshold 10 --portfolio

# 跑全 watchlist (61只, 约30分钟, 后台)
/t-backtest --signal Spring --days 30 --threshold 10 --all

# 组合信号
/t-backtest --signal Spring --signal fflow:强进货 --days 30 --threshold 10 --portfolio

# 缠论1买
/t-backtest --signal 1买 --days 30 --threshold 10 --portfolio
```
