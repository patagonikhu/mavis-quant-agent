---
name: t-rsi6-tech
description: RSI 双指标超卖 + 放量 spike 双信号共振买点 (RSI6<25 且 RSI12<30 且 最近 10 根 K 线任一量/MA10≥2.5). 0 网络, 从 watchlist.json 选股. 触发词: "RSI 超卖"、"放量启动"、"技术面抄底".
user-invocable: true
allowed-tools:
  - Bash

> 📐 代码组织: [`architecture.md`](../../architecture.md)
> 📐 决策框架: [`.claude/skills/_shared/analysis_framework.md`](../_shared/analysis_framework.md)
> 📐 新 Strategy: [`strategy_recipe.md`](../../strategy_recipe.md)

## 🕐 跑节奏 (技术面 — 日级)

- 业绩过滤由上游 `/t-finance-earnings-blowout` 完成 (blowout 段已是业绩过关票)
- 此 skill 只判断技术信号 (RSI + 放量 AND), 不再做业绩过滤

## 用法

```bash
bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan
    # 默认 watchlist + RSI + 放量 AND + lookback 2

... --volume-spike 2.0           # 放量倍数 (默认 2.5)
... --volume-window 10           # 放量 MA 窗口 (默认 10)
... --volume-lookback 10         # 放量看最近 N 根 (默认 10)
... --lookback 2                 # RSI 看最近 N 根 (默认 2)
... --threshold-rsi6 20          # RSI6 阈值 (默认 25)
... --no-rsi12                   # 关 RSI12 (只用 RSI6)
... --all-market                 # 全市场扫 (默认 watchlist)
... --watchlist-types 持仓       # 只跑某类
... --tech                       # 仅科技板块
... --write-md                   # 写 docs/rsi6-tech-watchlist.md
... --check-news                 # 命中后 MCP 查近 30 天负面新闻 (有则标 ⚠️)
... --news-days 30               # 新闻回溯天数 (默认 30)
```

## 跑流程 (2 步)

1. **step 1** — 跑 RSI + 放量 AND 扫描 (DataStore 0 网络)
2. **step 2 (可选, --check-news 时启用)** — 命中后逐只查 MCP 负面新闻
   - 走 `connector__hengsheng__call_api` (StockNewslist, emotionDirectionCode=负面)
   - 近 N 天 (默认 30) 任一负面新闻 → 命中行加 ⚠️
   - MCP 不可用时静默跳过,不影响主流程

## 触发规则 (AND)

最近 N 根 K 线任一:

1. **RSI 超卖**: `RSI6 < 25 AND RSI12 < 30`
2. **放量 spike**: `最近 10 根 K 线任一 量 >= MA{volume-window} × volume_spike` (默认 10 根 + 2.5x)

**两者都过才算命中** (`trigger` 字段标记 `"RSI+放量"`)

## 关键约束

- **0 网络**, 走 `DataStore` (K线 parquet)
- **默认 watchlist** 选股 (持仓+blowout+自选), `--all-market` 全市场
- **不做业绩过滤** — 由 `/t-finance-earnings-blowout` 把关 (blowout 段已是业绩过关票)
- 默认扫描 355 只 (当前 watchlist 长度)
- `--lookback 2` (RSI) + `--volume-lookback 10` (放量) — 都是"最近 N 根任一"语义

## 输出

```
代码    名称    行业    RSI6   RSI12  触发        放量比   触发日   价格    质量
300972  万辰集团 食品     15.70  26.0   RSI+放量    2.6x    20260922  163.80
...
```

排序 RSI6 升序 (越低越强), 限 50 行。**质量**:
- ⭐⭐ RSI6 < 5
- ⭐  RSI6 < 10
- ·   RSI6 < 15

## v4.1 → v4.2 变更 (2026-09-23)

1. **去掉 Wyckoff LPSY → JAC 买点旁路** (`--wyckoff-pullback`)
   - 原因: 4 步形态判定 (放量+缩量回调+不破位+突破前高) 无趋势 / 位置 / RSI 约束,
     在下跌中继 + 一字板都能命中, 实战不是真买点
   - 删除: `rsi6_tech_scan.py:308-381` 整块判定 + `wyckoff_info` 输出 + `--wyckoff-*` 两个 CLI flag
2. **RSI + 放量 改 AND** — 两者都过才算买点 (`trigger` 统一标 `RSI+放量`)
   - 之前 OR 容易把"涨停后 RSI 顶背离"和"放量突破前高"都当买点捞进,
     实战跟超卖抄底初心完全相反
3. **放量固定看最近 10 根 K 线任一** (新加 `--volume-lookback`,默认 10)
   - 10 根 ≈ 2 周交易日, 跨日捕捉异动, 又不至于拉到 1 个月前失去时效

## 参数速查

| CLI | 默认 | 含义 |
|---|---|---|
| `--all-market` | False | 全市场 (默认 watchlist) |
| `--from-watchlist` | (True) | watchlist 选股 (兼容旧 CLI) |
| `--watchlist-types` | 持仓,blowout,自选 | list_type 过滤 |
| `--threshold-rsi6` | 25 | RSI6 阈值 |
| `--threshold-rsi12` | 30 | RSI12 阈值 |
| `--no-rsi12` | False | 关 RSI12 |
| `--volume-spike` | 2.5 | 放量倍数门槛 (与 RSI AND) |
| `--volume-window` | 10 | 放量 MA 窗口 |
| `--volume-lookback` | 10 | 放量看最近 N 根任一放量 |
| `--lookback` | 2 | RSI 看最近 N 根任一跌破 |
| `--tech` | False | 科技板块限定 |
| `--write-md` | False | 写 docs/rsi6-tech-watchlist.md |
| `--check-news` | False | 命中后 MCP 查近 30 天负面新闻 |
| `--news-days` | 30 | 新闻回溯天数 (需 --check-news) |

## 命中后

`/t-analyze <code>` 看 22 section 详报

## 相关

- `/t-analyze` / `/t-finance-earnings-blowout` (上游业绩过滤)
- `/t-near-low` (5y 回撤超跌)
- `/t-finance-roc-ey` (财务多维)
- `/t-concept-macd` (概念 MACD)
- `/t-backtest` (信号回测)
- `/t-sync-data --kline` (跑前必跑)

## 命名沿革

v1 `/t-tech-bb-obv` → v2 `/t-macd-r2g` → v3 `/t-rsi6-oversold` → **v4 `/t-rsi6-tech` (当前)**
v4.1 (2026-09-23) 默认 watchlist + RSI/放量 OR + lookback 2, 去掉 6 重业绩过滤
v4.2 (2026-09-23) 去掉 Wyckoff 旁路 + RSI / 放量 改 AND (两者都过才算买点) + 放量固定看最近 10 根 K 线
v4.3 (2026-09-23) 加 MCP 负面新闻检查 (--check-news) + 命中行加 ⚠️ 告警 (step 2)