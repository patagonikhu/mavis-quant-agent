## 📊 市场状态定量判断（必须在缠论上方输出）

**三指标打分 → 方法优先级矩阵**（代码见 t-analyze SKILL.md §2n）

| 总分 | 状态 | 缠论背驰 | 威科夫 | SMC-OB |
|------|------|---------|--------|--------|
| 7-9 | 🚀主升浪 | ❌禁用 | ✅主用(Markup) | ⚠️辅助 |
| 4-6 | 🔄过渡回调 | ✅主用 | ✅辅助(Accumulation) | ⚠️辅助 |
| 0-3 | ⬇️震荡下跌 | ⚠️谨慎 | ⚠️等Accumulation确认 | ✅主用 |

```python
# 读市场状态：从 RenderData.from_raw(dump).analysis 读（不用 exec /tmp/*.py）
from tools.analysis.render_data import RenderData
data = RenderData.from_raw(dump)
analysis = data.analysis or {}
wyckoff  = analysis.get('wyckoff', {}).get('stage')  # Accumulation/Markup/Distribution
score    = analysis.get('total_score', 0)
```

## 📊 缠论补充分析（SMC + 量价 + 多市场共振 + 威科夫）

> 缠论失效时的补充手段，数据来自 dump['kline']，无需新 API。

### 快速查表

| 场景 | 缠论问题 | 补充方法 | 触发条件 |
|------|---------|---------|---------|
| 震荡市 | 背驰面积比噪音 | SMC Order Block | 涨幅<20%无明显段结构 |
| 真假突破 | 段面积扩张 | 量价：放量确认 | vol_ratio>1.5 才算真突破 |
| 主升浪 | 背驰失效 | 威科夫 Markup | MA20偏离>20%替代 |
| 底部确认 | 底背驰后还跌 | 威科夫 Accumulation | 缩量假跌破=Spring测试 |
| 信号过滤 | 单股假信号 | 多市场共振 | 个股+板块+大盘三向同向 |

### 调用方式

```python
# 读补充分析结果：从 data.analysis 读（不用 exec /tmp/*.py）
chan      = analysis.get('chan', {})
daily_bc  = chan.get('daily', {}).get('beichi', '')
smc       = analysis.get('smc', {})
vp        = analysis.get('volume_price', {})
resonance = analysis.get('resonance', {})
# 威科夫三阶段: Accumulation / Markup / Distribution（无 A/B/C/D/E）
wyckoff_stage = analysis.get('wyckoff', {}).get('stage')
```

### 数据来源说明

所有计算基于 `dump['kline']` (日线 K 线)：
- `close / high / low` → SMC Order Block（纯价格结构）
- `close / vol` → OBV + vol_ratio（量价分析）
