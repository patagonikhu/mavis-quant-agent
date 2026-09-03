# Magic Formula 排名 Top 5 — 2026-09-03

> **报告期:** 2026Q2  |  **股票池:** 1923 只科技股 (client-side 筛选)  |  **跳过:** 1847 只 (含行业 EXCLUDED + 无数据)
> **公式:** ROC = EBIT / (净营运资本 + 固定资产), EY = EBIT / EV (Greenblatt 2005)
> **排名:** ROC 降序 + EY 降序, 综合 = (ROC 排名 + EY 排名) / 2, 数字小的胜出

## 📊 统计概览

| 指标 | 平均 | 最高 |
|------|------|------|
| ROC (%) | 1407.0 | 4186.3 |
| EY  (%) | 36.36 | 58.50 |

## 🏆 Top 5

| # | 代码 | 名称 | 行业 | ROC (%) | EY (%) | ROC 排名 | EY 排名 | 综合 | 市值 (亿) | EV (亿) |
|---|------|------|------|---------|--------|----------|---------|------|-----------|---------|
| 1 | 601098 | 中南传媒 | 出版业 | 4186.3 | 45.70 | 5 | 4 | 4.5 | 178 | 36 |
| 2 | 002668 | TCL智家 | 家用电器 | 983.6 | 33.30 | 16 | 6 | 11.0 | 104 | 75 |
| 3 | 000752 | *ST西发 | 啤酒 | 1338.4 | 23.70 | 9 | 22 | 15.5 | 22 | 12 |
| 4 | 000913 | 钱江摩托 | 摩托车 | 292.3 | 58.50 | 29 | 2 | 15.5 | 58 | 18 |
| 5 | 000719 | 中原传媒 | 出版业 | 234.6 | 20.60 | 41 | 29 | 35.0 | 121 | 61 |

## 📖 怎么读

1. **ROC (Return on Capital)** = 资本回报率, 越高越好 — 公司赚钱效率
2. **EY  (Earnings Yield)** = 盈利收益率, 越高越好 — 股价相对盈利能力便宜
3. **综合排名** = ROC + EY 联合排名, 越小越靠前 (双优)
4. **行业过滤** = 银行/保险/地产/公用等不参与 (ROC/EY 在这些行业失真)

## 🔗 数据流

```
Tushare fina_indicator_vip (1次API, 全市场 9255 行)
   ↓ 客户端筛科技股 (industry != EXCLUDED_INDUSTRIES)
data/history/financials/{period}.parquet  (1923 只, status=ok)
   ↓ DataStore.get_financials(code)
ROC = EBIT / (NWC + FA),  EY = EBIT / EV
   ↓ 联合排名
Top 20 → docs/magic-top20.md
```

## 💡 用法

```bash
# 跑全市场排名 (默认 1923 科技股, Top 20)
bash tools/with_venv.sh python -m tools.batch.magic_top20

# 自定义 Top 数
bash tools/with_venv.sh python -m tools.batch.magic_top20 --top 50

# 改报告期 (需先跑 sync_financials 拉对应季)
bash tools/with_venv.sh python -m tools.batch.magic_top20 --period 2026Q1
```

---

📅 **生成时间:** 2026-09-03 09:02:50  |  🔧 **脚本:** `tools/batch/magic_top20.py`  |  📊 **数据:** `data/history/financials/2026Q2.parquet`
