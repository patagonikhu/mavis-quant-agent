---
name: t-near-low
description: 每周监控"跌 70-80% 且距 5y 低 < 3%"的股票清单（含反弹次数、5y最大回撤、2025财报、并发拉tushare）。用户说"距 5y 低"、"近底"、"超跌清单"时触发。
user-invocable: true
allowed-tools:
  - Bash

## 用法

```bash
/t-near-low                            # 默认: 跌 70-80% + 距 5y 低 < 3% + 5y lookback
/t-near-low --gap 5                    # 距 5y 低 < 5% (宽松)
/t-near-low --gap 2                    # 距 5y 低 < 2% (严格)
/t-near-low --drop 80 --drop-max 90    # 跌 80-90%
/t-near-low --lookback-years 3         # 3y lookback (不含 2021 牛市)
/t-near-low --min-bounces 4            # 反弹次数 ≥ 4
/t-near-low --skip-tushare              # 跳过 tushare (只用 weekly 末根)
/t-near-low --write-md                 # 写 docs/oversold-watchlist.md
```

## 算法 (3 步)

1. **粗筛 (DataStore weekly, 0 网络)**: 全市场 5y weekly K 线
   - `max_drop`: high → low 最深回撤，默认 `70% ≤ max_drop < 80%`
   - `距 5y 低 (粗筛)`: `< 10%` (用 weekly 末根)
   - `反弹次数`: 5y weekly 内 30%+ 反弹事件，window=3 strict local min
2. **精筛 (本地 DataStore, 0 网络)**: 对粗筛候选
   - `DataStore.get_daily_basic(code)` 读本地 daily 最新价, 重算 `距 5y 低 < 3%`
   - `DataStore.get_income(code)` 读本地 EPS/净利 (2025A/2024A)
3. **输出清单 (按距低% 升序)**: 8-13 只

> 2026-08-30 修: 之前 SKILL.md 写"拉 tushare"是过时注释, 实际代码已改用本地 DataStore, 全程 0 网络, 跟 t-bb-obv / t-analyze 框架一致.

## 执行

```bash
# 检查本地数据（< 400 只需先跑 kline_history_backfill）
bash tools/with_venv.sh python3 -c "from tools.kline_store import DataStore; print(f'本地: {len(DataStore.list_codes())} 只')"

# 后台跑（8 worker, ~10s）
bash tools/with_venv.sh python3 tools/batch/find_near_low.py --write-md
```

## 输出示例

```
代码       名称         行业         现价   5y低    5y最大回撤  今gap  反弹  2025净利  今年
002531    天顺风能     电气设备     6.11   6.08   -72.3%    +0.49%  13次  -1.3亿   亏
601888    中国中免     旅游服务    53.10  52.73   -80.5%    +0.70%  12次  36.9亿  -24%
```

## 关键参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--gap` | 3 | 距 5y 低阈值 (daily 价, %) |
| `--weekly-gap` | 10 | 粗筛 weekly 末根距 5y 低 (放宽用) |
| `--drop` / `--drop-max` | 70 / 80 | max_drop 区间 (%)，排除 80%+ 异常 |
| `--lookback-years` | 5 | max_drop 窗口 (5 或 3) |
| `--min-bounces` | 0 | 反弹次数阈值 (历史弹性) |
| `--skip-tushare` | False | 跳过 tushare (只用 weekly) |

## 实战策略

距 5y 低 < 3% 的超跌股多为业绩下行/亏损：
- **谷底跌 70-80% 反弹期望最好**（中位 +45%）
- **90%+ 反弹差**（中位 +13%）
- **反弹策略**（不价值投资）：涨 10-20% 跑 / 跌破谷底 10% 砍 / 持有 1-3 个月 / 5-10 只分散

## 深挖（清单出来后）

挑 3-5 只用 `/t-analyze` 跑完整 22 section 报告。

**单只深挖命令** (用 `AnalysisEngine.analyze_history` 正确 API):
```bash
bash tools/with_venv.sh python3 << 'PYEOF'
import sys; sys.path.insert(0, '.')
from tools.kline_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
from tools.analysis.render_data import RenderData
from tools.render.report_renderer import render_report
from pathlib import Path
ctx = DataStore.get_ctx('002531')
all_dates = [k['trade_date'].replace('-','')[:8] for k in ctx.kline]
history = AnalysisEngine().analyze_history(ctx, all_dates[-120:])
data = RenderData.from_result(ctx, history[all_dates[-1]])
# 复用 history (render 接受 list[dict] 格式)
data.factor_history_rows = list(history.values())
md = render_report(data)
p = Path('docs') / f'analyze-002531-{ctx.name}.md'
p.write_text(md, encoding='utf-8'); print(p)
PYEOF
```

**批量入口** (深挖多只, 避免 N 次 sync):
```bash
# 1 次 sync + 4 worker 并发 analyze+render (2026-08-31 refresh_all.sh 已删, 用 t-analyze --all 替代)
T_ANALYZE_WORKERS=4 bash tools/with_venv.sh python3 tools/batch/t_analyze_all.py
```

## 每周自动跑 (cron)

```python
mavis(cron.create, {
  "cron_name": "weekly-near-low",
  "schedule": "0 9 * * 1",  # 每周一 09:00
  "prompt": "跑 t-near-low skill 监控超跌清单, 输出本周 8-10 只, 标记业绩/技术状态",
  "session": {"mode": "new", "agent_name": "mavis"}
})
```

## 相关

- `tools/batch/find_near_low.py` — 筛选脚本（8 worker, ~10s, 走 DataStore）
- `data/oversold-watchlist.md` — 输出清单（`--write-md`）
