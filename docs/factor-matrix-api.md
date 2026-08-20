# 因子 × 3 周期 综合矩阵 API 文档

> **新增于 2026-07-25 v4**
> **2026-08-17 改名为 `factor_matrix` (改名前 `five_method_matrix`):** "5 方法" 历史命名已过时, 实际是 7 因子 (wyckoff/smc/chan/resonance/peg/dcf + fflow/obv, 2026-08-17 OBV/fflow 拆分后)
> **目的:** 把 N 个因子 (缠论/威科夫/SMC/量价/共振/PEG/DCF) × 3 周期 (周/日/60分) 合并成 1 个统一输出模块
> **可被以下 skill 调用:** `/t-analyze` / `/t-watchlist` / `/t-sector` / `/t-monitor`

---

## 为什么需要这个模块

### 问题 (重构前)
- `_section_factor_matrix` 在 `report_renderer.py` 里手写 ~130 行代码 (改名前 `_section_5method_matrix`)
- N 个方法 × 3 个周期 = 3N 个数据点要分别读 `s5.get('xxx')` 容易写错
- 缺乏**统一的价格输出** (中枢上下沿 / 123 买卖点价 / 建议买卖价)
- `/t-watchlist` / `/t-sector` 想用 因子结果**得自己再算一遍**

### 解决方案 (v4)
- 1 个 `build_factor_matrix()` 主函数, 1 次调用出 N×3 完整结构
- 1 个 `render_factor_matrix_md()` 渲染 markdown
- 2 个便捷函数 `get_buy_recommendation()` / `get_sell_recommendation()` 给 watchlist/sector 用
- **render 阶段 0 重复计算** (复用 `signals_5method` 已算好的数据)

---

## 主函数

### `build_factor_matrix(code, name, current_price, signals_5method, chan_data, buy_sell_points) -> dict`

**参数:**
| 参数 | 类型 | 说明 |
|---|---|---|
| `code` | str | 股票代码 (e.g. '300274') |
| `name` | str | 股票名称 (e.g. '阳光电源') |
| `current_price` | float | 当前价 (元) |
| `signals_5method` | dict | `analysis` dict (AnalysisEngine 输出) |
| `chan_data` | dict | dump.json 里的 `chan` 字段 (含 `weekly`/`daily`/`60min` 各有 `hub`) |
| `buy_sell_points` | dict | dump.json 里的 `buy_sell_points` 字段 (含 `weekly`/`daily`/`60min` 各有买卖点) |

**返回结构:**
```python
{
    "code": "300274",
    "name": "阳光电源",
    "current_price": 113.42,
    "scene": "D",
    "scene_name": "底部建仓",
    "resonance_count": 2,
    "action": "🟡 观察, 2 重共振信号弱",

    "matrix": {
        "weekly": {
            "level": "weekly", "weight": "1.5x",
            "chan": {stage, beichi, hub: {low, high, pos, valid},
                     buy_sell_points: {0buy, 1buy, 1buy_trend, 2buy, 3buy,
                                       1sell, 1sell_trend, 2sell, 3sell, action},
                     target_buy_price, target_sell_price},
            "wyckoff": {stage, stage_name, stage_detail, confidence, action},
            "smc": {summary, total_obs, total_fvgs, total_sweeps,
                    nearest_bull_ob, nearest_bear_ob, nearest_fvg_bull, nearest_fvg_bear},
            "volume_price": {verdict, fflow_3d, fflow_5d, fflow_30d, fflow_60d, trend_3d, trend_30d,
                             obv_verdict, obv_div_bot_60d, obv_div_top_60d},  # 2026-08-17 拆 fflow + obv
            "resonance": {direction, stock_ret_5d, sector_ret_5d},
            "composite": {action, direction, bottom_signals, top_signals,
                          buy_target, sell_target, hub_low, hub_high, price_position}
        },
        "daily":  {...},  # 同上结构
        "60min":  {...},  # 同上结构
    },

    "top_warning": {"weekly": "✅ 否", "daily": "✅ 否", "60min": "✅ 否"},
    "bottom_signal": {"weekly": "🟠 标准", "daily": "🟡 弱", "60min": "✅ 否"},
    "top_4in1_detail": {"weekly": "2/4 (MA=-28% Wyckoff=Accumulation)", ...},
    "bot_4in1_detail": {"weekly": "2/4 (MA=-28% Wyckoff=Accumulation)", ...},
}
```

**关键字段说明:**

| 字段 | 含义 | 示例 |
|---|---|---|
| `composite.action` | 因子投票后的综合判定 | 🥇 强建仓 / 🥈 标准建仓 / 🟡 观察 / 🟠 标准减仓 / 🔴 强减仓 |
| `composite.direction` | 方向 (long/short/neutral) | long |
| `composite.bottom_signals` | 底部信号数 (0-8) | 3 |
| `composite.top_signals` | 顶部信号数 (0-8) | 0 |
| `composite.buy_target` | 建议买入价 (元) | 134.15 |
| `composite.sell_target` | 建议卖出价 (元) | 141.21 |
| `composite.price_position` | 价格位置 (A_上方 / B_内部 / C_下方) | C_下方 |
| `top_warning[周期]` | 4 合 1 顶部预警 | 🚨 强 (≥3) / 🟠 标准 (≥2) / 🟡 弱 (=1) / ✅ 否 |
| `bottom_signal[周期]` | 4 合 1 底部预警 | 同上 |

### 价格动态调整 (重要!)

`buy_target` / `sell_target` 随价格位置变化:

| 价格位置 | 1 买价 (target_buy) | 1 卖价 (target_sell) | 含义 |
|---|---|---|---|
| **A 上方** (健康持有) | 中枢下沿 | 中枢上沿 | 回踩下沿买入, 突破后止盈 |
| **B 内部** (横盘震荡) | 中枢下沿 | 中枢上沿 | 站上下沿买入, 跌破下沿止损 |
| **C 下方** (跌穿中枢) | **结构低点** (不是中枢, 中枢在上面) | **中枢下沿** (反弹第一目标) | 等止跌信号建仓, 反弹到下沿先卖一半 |

**结构低点来源:**
1. `chan_data['structure_low']` (如果有, 优先)
2. `chan_data['lowest_low']` (兜底)
3. `hub_low × 0.95` (最后兜底)

---

## 便捷函数 (给 watchlist / sector 用)

### `get_buy_recommendation(matrix_result) -> dict | None`

从 因子矩阵提取**建议买入价**, 优先级: 日线 > 周线 > 60分

```python
{
    'price': 134.15,    # 元
    'level': 'daily',   # 哪个周期
    'action': '🥈 标准建仓',
    'bottom_signals': 3,
}
```

**调用示例 (用于 watchlist 排序):**
```python
from tools.analysis.factor_matrix import build_factor_matrix, get_buy_recommendation

results = []
for code in watchlist:
    data = AnalysisData.from_raw(dump[code])
    matrix = build_factor_matrix(
        code=code, name=data.name, current_price=data.current_price,
        signals_5method=data.analysis,  # 2026-08-17 改: 之前是 data.signals_5method
        chan_data=data.chan_data, buy_sell_points=data.buy_sell_points,
    )
    rec = get_buy_recommendation(matrix)
    if rec:
        results.append({
            'code': code, 'name': data.name,
            'price_now': data.current_price,
            'buy_target': rec['price'],
            'action': rec['action'],
            'bottom_signals': rec['bottom_signals'],
        })

# 按 bottom_signals 降序排
results.sort(key=lambda x: x['bottom_signals'], reverse=True)
```

### `get_sell_recommendation(matrix_result) -> dict | None`

对称, 提取**建议卖出价**:
```python
{
    'price': 141.21,    # 元
    'level': 'daily',
    'action': '🔴 强减仓',
    'top_signals': 4,
}
```

---

## 渲染函数

### `render_factor_matrix_md(matrix_result) -> str`

把 因子 × 3 周期 矩阵渲染成 markdown (报告用, 2026-08-17 改名前 `render_5method_matrix_md`)。

**输出示例 (阳光电源 300274):**

```markdown
## 🎯 因子 × 3 周期 综合矩阵 (2026-07-25 合并: 整合原 5 合 1 顶部预警)
**股票**: 300274 阳光电源 ¥113.42
**场景**: D (底部建仓) | **共振数**: 2 重 | **行动**: 🟡 观察

**📊 4 合 1 顶部预警:**
| 周期 | 4 合 1 评分 | 详情 |
|---|---|---|
| 周线 1.5x | ✅ 否 | 0/4 (MA=-28% Wyckoff=Accumulation) |
...

**🟢 4 合 1 底部预警:**
| 周期 | 4 合 1 评分 | 详情 |
|---|---|---|
| 周线 1.5x | 🟠 标准 | 2/4 (MA=-28% Wyckoff=Accumulation) |
...

**🎯 因子 × 3 周期 (含中枢 + 123 买卖点 + 建议价格):**
| 维度 | 周线 (1.5x) | 日线 (1.0x) | 60分 (0.5x) |
|---|---|---|---|
| **缠论** | 观望 / 无中枢 / 买卖点: 无 | 🟢 底背驰 / 买卖点: 1buy_trend=¥113 | ... |
| **威科夫** | Accumulation (100%) | Accumulation (100%) | Accumulation (100%) |
| **SMC** | 扫流×15 | 扫流×8 | 扫流×7 |
| **量价 (fflow+OBV)** | 3d:+6.4亿 / 30d:-62.5亿 / 🟢进货 / OBV强底×2/4 | ... | ... |
| **多市场共振** | ⬜混合 -24.9%/+0.0% | ... | ... |
| **🎯 综合判定** | 🥈 标准建仓 / 底3/顶1 | 🥈 标准建仓 / 买¥134 / 卖¥141 | 🟡 观察 |

**💰 实战建议 (日线):**
- 行动: **🥈 标准建仓**
- 价格位置: 🟠 在中枢下方 (跌穿, 关注止跌)
- 建议买入价: **¥134.15** (结构低点, 价格已穿中枢)
- 建议卖出价: **¥141.21** (中枢下沿, 反弹第一目标)
- 中枢区间: ¥141.21 ~ ¥159.98
```

---

## 报告渲染集成

### `_section_factor_matrix(data: AnalysisData) -> str`

`tools/render/report_renderer.py` 里的 wrapper, 被 `render_report` 主模板调用 (2026-08-17 改名前 `_section_5method_matrix`):

```python
def _section_factor_matrix(data: AnalysisData) -> str:
    if not data.analysis:
        return "> **❌ 数据缺失:** 因子矩阵未生成\n"
    try:
        from tools.analysis.factor_matrix import (
            build_factor_matrix,
            render_factor_matrix_md,
        )
        matrix = build_factor_matrix(
            code=data.code, name=data.name,
            current_price=data.current_price or 0,
            signals_5method=data.analysis,
            chan_data=data.chan_data or {},
            buy_sell_points=data.buy_sell_points or {},
        )
        return render_factor_matrix_md(matrix)
    except Exception as e:
        return f"> **❌ factor_matrix 调用失败:** {e}\n"
```

**关键点:**
- 1 个 section 输出 因子 × 3 周期 全部内容 (含中枢 + 买卖点 + 建议价)
- 替代了原来的 5 合 1 顶部预警独立 section
- 跨 4 批 38 只 388 样本验证 (2026-07-24, 4合1 联合 81.3% 5d 胜率)

---

## Linter 验证

**18 只票 (持仓 6 + 半导体 12) 全部 100% (35/36) Linter 通过:**

| 板块 | 票 | 完整度 |
|---|---|---|
| 持仓 6 | 300274 / 600089 / 601958 / 600362 / 002475 / 000725 | 100% (35/36) |
| 半导体设备 6 | 002371 / 688012 / 688120 / 688072 / 688082 / 300604 | 100% (35/36) |
| CPO/封测 6 | 300308 / 002463 / 300476 / 002156 / 688008 / 600584 | 100% (35/36) |

**缺失的 1 个 section** 是 ETF/非标准报告, 不影响 18 只票验证。

---

## 设计决策 (为什么这样做)

### 1. 为什么不直接让 watchlist/sector 调 `analysis` dict?
- `analysis` dict 输出**平铺** 7 个因子, 跨周期没对齐
- 没聚合 `composite` (因子投票)
- 没计算 `target_buy` / `target_sell` (这是用户要的)
- `build_factor_matrix` 填补这块空白

### 2. 为什么不存到 dump.json?
- 因子矩阵依赖实时价格 + analysis dict
- 这些在 `AnalysisData.from_raw()` 时内存里算
- 报告渲染时直接调 build, 不需要序列化

### 3. 为什么 3 周期不强制输出 hub?
- 阳光电源周线没形成 hub (`valid=False`) → 显示 "无中枢"
- 真实反映市场状态, 比硬填一个假 hub 强
- "无中枢"本身是有用的信号 (趋势初期, 没盘整)

### 4. 为什么"价格位置"判定要分 A/B/C?
- A 上方: 健康, 用中枢下沿作为回踩买入价
- B 内部: 横盘, 用中枢下沿作为支撑买入
- **C 下方: 跌穿, 中枢变成目标而非止损** → 1 买要换成结构低点

**用户 2026-07-25 原话:**
> "因子 × 3 周期 综合矩阵 需要加上中枢， 123买卖点， 这样我就知道大致价格吧，
> 因子 × 3 周期 综合矩阵 需要作为其他skill 的输出， 这样分析板块或者watchlist 我就知道哪些票可以用什么价格买入或者卖出"

→ 这个模块就是为这个需求做的。

---

## 后续 TODO

- [ ] 跑 watchlist 全 57 只 批量 `scan-3in1` 验证 v4 二级规则
- [ ] `/t-watchlist` 调用 `get_buy_recommendation` 输出 buy 价排序
- [ ] `/t-sector` 调用 `get_buy_recommendation` 按板块聚合 buy 价分布
- [ ] (可选) 修 fflow 风险权重: 6 只都判"🥇 强建仓" 跟持仓 fflow 矛盾
- [ ] (可选) 因子 vs CZSC 对比白皮书 (`docs/chan-czsc-comparison.md`): 等 CZSC 装上
