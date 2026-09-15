---
name: t-macd-r2g
description: MACD 红转绿信号 (红柱≥N天 + 最近2根翻绿). 0 网络. 触发词: "红转绿"、"MACD 底部"、"红柱翻绿".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.tech_bb_obv_scan              # 默认全市场
... --window 5            # 改 OBV 5 日窗口
... --limit 100           # 调试: 只扫前 N 只
... --write-md            # 写 docs/tech-bb-obv-watchlist.md
... --workers 8           # 线程数 (默认 4)
... --no-junk-filter      # 跳过垃圾股过滤
```

## 关键约束

- **0 网络**: `DataStore.get_ctx(kline_only=True)` 跳过 EPS/fflow 网络拉取
- **架构**: 走 `AnalysisEngine + [ObvStrategy]`, mode="bb_obv" 让 `build_kline_features` 只算 6 数组 (ma5/20/60/120 + boll_pct + boll_width), 跳过 17 个用不到的
- **3 维过滤**: BOLL 位置 + BBW 带宽 + OBV 5日/趋势 (全过 → 1-3 个月反弹)
- **性能**: 5256 只 ~1.5min (老版 30+min, 30x 提速)
- **OBV 适用性**: ✅ 光学/封测/HBM (主力控盘) | ⚠️ 白酒/医药/银行 (主力分散, 信号参考度低) | ❌ 题材小盘 (噪声大)
- **缺数据**: 报"请先 /t-sync-data"

## 输出

- `docs/tech-bb-obv-watchlist.md` — 命中列表 (含 OBV 适用性警告)
- 命中后用 `/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-near-low` / `/t-roc-ey` / `/t-earnings-blowout` / `/t-sector-ma` / `/t-backtest` / `/t-sync-data`
