# ROC + EY 联合排名 Top 3 — 2026-09-11

> **报告期:** 2026Q2  |  **股票池:** 1923 只科技股 (client-side 筛选)  |  **跳过:** 2611 只 (含行业 EXCLUDED + 无数据)
> **公式:** ROC = EBIT / (净营运资本 + 固定资产), EY = EBIT / EV (Greenblatt 2005)
> **排名:** ROC 降序 + EY 降序, 综合 = (ROC 排名 + EY 排名) / 2, 数字小的胜出

## 📊 统计概览

| 指标 | 平均 | 最高 |
|------|------|------|
| ROC (%) | 116.9 | 234.6 |
| EY  (%) | 24.80 | 32.60 |

## 🏆 Top 3

| # | 代码 | 名称 | 行业 | ROC (%) | EY (%) | ROC 排名 | EY 排名 | 综合 | 市值 (亿) | EV (亿) |
|---|------|------|------|---------|--------|----------|---------|------|-----------|---------|
| 1 | 600741 | 华域汽车 | 汽车配件 | 77.9 | 32.60 | 16 | 4 | 10.0 | 490 | 302 |
| 2 | 000719 | 中原传媒 | 出版业 | 234.6 | 20.10 | 8 | 24 | 16.0 | 123 | 63 |
| 3 | 601919 | 中远海控 | 水运 | 38.3 | 21.70 | 30 | 18 | 24.0 | 2,530 | 1,827 |

## 📖 怎么读

1. **ROC (Return on Capital)** = 资本回报率, 越高越好 — 公司赚钱效率
2. **EY  (Earnings Yield)** = 盈利收益率, 越高越好 — 股价相对盈利能力便宜
3. **综合排名** = ROC + EY 联合排名, 越小越靠前 (双优)
4. **行业过滤** = 银行/保险/地产/公用等不参与 (ROC/EY 在这些行业失真)

## 🔗 数据流

```
sync.py --financials 预拉 (Tushare 财务接口, 全市场 9255 行, 落 financials parquet)
   ↓ 客户端筛科技股 (industry != EXCLUDED_INDUSTRIES)
data/history/financials/{period}.parquet  (1923 只, status=ok)
   ↓ DataStore.get_financials(code)
ROC = EBIT / (NWC + FA),  EY = EBIT / EV
   ↓ 联合排名
Top 20 → docs/roc-ey-top20.md
```

## 💡 用法

```bash
# 跑全市场排名 (默认 1923 科技股, Top 20)
bash tools/with_venv.sh python -m tools.batch.roc_ey_top20

# 自定义 Top 数
bash tools/with_venv.sh python -m tools.batch.roc_ey_top20 --top 50

# 改报告期 (缺数据请先 /t-sync-data)
bash tools/with_venv.sh python -m tools.batch.roc_ey_top20 --period 2026Q1
```

---

📅 **生成时间:** 2026-09-11 15:22:12  |  🔧 **脚本:** `tools/batch/roc_ey_top20.py`  |  📊 **数据:** `data/history/financials/2026Q2.parquet`
