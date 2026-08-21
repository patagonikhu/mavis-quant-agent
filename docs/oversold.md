# 超跌股 watchlist (auto-generated, 2026-08-20)

> 来源: `tools/oversold/build_oversold_watchlist.py`  
> 跌幅阈值: ≥ 70% from 250 根 weekly high (20200101 至今)  
> 全 A 股扫描: 1351 只 → 筛出 0 只 (排除 ST/*ST/退市/0 数据不足)  
> 拉取: ✅0 / ❌5 (频控 0 / 权限 0 / 其它 5)  
> 耗时: 3 秒 (0.1 分钟)

## 📋 摘要

- **筛选结果**: 0 只超跌股 (跌 ≥ 70%)
- **平均跌幅**: 0.0%
- **平均反弹**: +0.0%
- **行业分布**: 0 个行业
- **输出 watchlist**: `data/watchlist_oversold.json` (416 只, 可直接 `bash tools/refresh_all.sh --watchlist data/watchlist_oversold.json` 跑完整 analysis)

## 🎯 Top 0 (按跌幅排序)

| 代码 | 名称 | 行业 | 当前价 | 250周高 | 跌幅 | 反弹% | as_of |
|------|------|------|--------|---------|------|-------|-------|
| - | - | - | - | - | - | - | - |

**未发现跌幅 ≥ 70% 的超跌股** (回看 250 根 weekly)

## 📁 输出文件

- `data/watchlist_oversold.json` — 新 watchlist, 跟 `data/watchlist.json` 同结构, 可走完整 analysis
- `data/dump_oversold/{code}.json` — lite dump 缓存 (weekly 250 根 + name/industry), 0 个
- `docs/oversold.md` — 本报告

## 🚀 下一步

```bash
# 1. 拉完整 dump (走 dump_data.py 路径, fflow/eps/daily/60m)
bash tools/refresh_all.sh --watchlist data/watchlist_oversold.json --workers 4

# 2. 看强信号汇总
cat docs/signal-watchlist.md
```
