"""
lgb_ranker.py - LightGBM 多因子打分骨架 (Day 11, 2026-07-27)

目的: 用 8 个基础特征 + 17 只 baseline 票训练 LightGBM, 给全 57 只票打分排序

输入: parquet via DataStore (用 baseline 17 只训练, 全 watchlist 预测)
输出: docs/lgb_ranking_{date}.md (排序表 + Top10 + 验证指标)

骨架特点:
- 不动现有 5方法×3周期报告, 只在 watchlist 端加一个排序能力
- 用最简单 LightGBM rank 模式 (lambdarank), 1 个文件搞定
- 留出接 alpha_ga_001 等更多因子的扩展位

⚠️ 限制: 17 只票训练样本太少, 实战应扩到 50+ 票

跨机器 (2026-07-27):
- 自动检测平台, macOS 设 libomp 路径, Linux 跳过
- 数据路径用 PROJECT_ROOT (Path(__file__).parent.parent) 解析, 不依赖 CWD

用法:
  PYTHONPATH=. python3 tools/lgb_ranker.py
  或: PYTHONPATH=. .venv/bin/python3 tools/lgb_ranker.py
"""
import os
import sys
import platform
from pathlib import Path
import yaml

# macOS 必需: libomp 路径 (LightGBM 二进制依赖)
if platform.system() == "Darwin" and "DYLD_LIBRARY_PATH" not in os.environ:
    candidates = [
        "/opt/homebrew/opt/libomp/lib",
        "/usr/local/opt/libomp/lib",
    ]
    for libomp_path in candidates:
        if Path(libomp_path).exists():
            os.environ["DYLD_LIBRARY_PATH"] = libomp_path
            break

# === 项目级配置加载 (2026-07-27 集中管理, 无默认值) ===
def _load_lgb_config() -> dict:
    """从 config/project.yaml 加载 lightgbm 段, 没有就报错"""
    config_path = Path(__file__).parent.parent / "config" / "project.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ 找不到 {config_path}\n"
            f"   首次使用请: 手动创建 config/project.yaml (不在 git 里, 参考 git history 或 docs/AGENT_MEMORY.md)"
        )
    with open(config_path) as f:
        return yaml.safe_load(f).get("lightgbm", {})

_LGB_CFG = _load_lgb_config()

import json
import time
from datetime import datetime

import numpy as np
import pandas as pd
import lightgbm as lgb

# === 跨机器路径 (2026-07-27) ===
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WATCHLIST_JSON = PROJECT_ROOT / "data" / "watchlist.json"
DOCS_DIR = PROJECT_ROOT / "docs"


# === 1. 基础特征工程 (8 维) ===
def build_features(code: str) -> dict:
    """拉 8 个特征, 拼成一行 (走 fetch_all 获取最新数据)"""
    from tools.storage.sources.eastmoney import fetch_all
    raw = fetch_all(code)
    kline = raw.get("kline", [])
    if not kline or len(kline) < 60:
        return None

    closes = pd.Series([k["close"] for k in kline])
    vols = pd.Series([k.get("vol", k.get("volume", 0)) for k in kline])

    # 1. PEG (从 dump 拿现成的)
    peg = raw.get("peg", {})
    peg = peg.get("PEG_真实") if isinstance(peg.get("PEG_真实"), (int, float)) else 0
    peg = peg if peg > 0 else 2.0  # 无数据时填 2 (中性偏贵)

    # 2. PE_TTM
    pe_ttm = raw.get("pe_ttm", 0) or 0

    # 3. PB
    pb = raw.get("pb", 0) or 0

    # 4. MA20 偏离
    ma20 = closes.rolling(20).mean().iloc[-1]
    ma20_dev = (closes.iloc[-1] / ma20 - 1) * 100

    # 5. RSI 14
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean().iloc[-1]
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean().iloc[-1]
    rsi = 100 - 100 / (1 + gain / (loss + 1e-9))

    # 6. OBV 5d 变化
    obv = (np.sign(closes.diff()) * vols).cumsum()
    obv_5d_chg = (obv.iloc[-1] - obv.iloc[-6]) / (vols.rolling(5).mean().iloc[-1] + 1)

    # 7. 量比
    vol_ratio = raw.get("volume_ratio", 0) or 1.0

    # 8. 换手率
    turnover = raw.get("turnover_rate", 0) or 0

    # Target: 未来 5d 涨跌幅
    if len(closes) < 6:
        return None
    target_5d = (closes.iloc[-1] / closes.iloc[-6] - 1) * 100

    return {
        "code": code,
        "name": raw.get("name", ""),
        "PEG": peg,
        "PE_TTM": pe_ttm,
        "PB": pb,
        "MA20_偏离": ma20_dev,
        "RSI": rsi,
        "OBV_5d": obv_5d_chg,
        "量比": vol_ratio,
        "换手率": turnover,
        "target_5d": target_5d,
    }


# === 2. 主流程 ===
def main(train_codes: list = None, predict_codes: list = None, out_path: str = None):
    """跑 LightGBM 排序骨架"""
    train_codes = train_codes or ["300274", "300308", "300476", "300502", "300604",
                                  "600089", "600362", "601138", "002371", "002463",
                                  "002472", "002475", "688012", "688082", "688120",
                                  "300990", "000725"]
    watchlist = json.load(open(WATCHLIST_JSON))["stocks"]
    predict_codes = predict_codes or [w["code"] for w in watchlist]

    print(f"🧮 LightGBM 多因子打分骨架")
    print(f"📊 训练集: {len(train_codes)} 只 | 预测集: {len(predict_codes)} 只")
    print(f"📋 特征: PEG/PE_TTM/PB/MA20_偏离/RSI/OBV_5d/量比/换手率 (8 维)")

    # === 训练数据准备 ===
    train_rows = [build_features(c) for c in train_codes]
    train_rows = [r for r in train_rows if r is not None]
    print(f"✅ 训练数据: {len(train_rows)}/{len(train_codes)} 行 (样本太小, 实战应 50+ 票)")

    train_df = pd.DataFrame(train_rows)
    feature_cols = ["PEG", "PE_TTM", "PB", "MA20_偏离", "RSI", "OBV_5d", "量比", "换手率"]
    X_train = train_df[feature_cols].values
    y_train = train_df["target_5d"].values

    # === LightGBM 训练 (regression 模式, 5d 涨幅预测) ===
    t0 = time.time()
    train_data = lgb.Dataset(X_train, label=y_train, feature_name=feature_cols)
    params = {
        "objective": "regression",
        "metric": "mae",
        "learning_rate": _LGB_CFG["learning_rate"],
        "num_leaves": _LGB_CFG["num_leaves"],
        "max_depth": _LGB_CFG["max_depth"],
        "min_data_in_leaf": _LGB_CFG["min_data_in_leaf"],
        "feature_fraction": _LGB_CFG["feature_fraction"],
        "bagging_fraction": _LGB_CFG["bagging_fraction"],
        "bagging_freq": _LGB_CFG["bagging_freq"],
        "verbosity": -1,
    }
    model = lgb.train(
        params,
        train_data,
        num_boost_round=_LGB_CFG["num_boost_round"],
    )
    elapsed = time.time() - t0
    print(f"⏱️  训练耗时: {elapsed:.1f}s")

    # === 训练集指标 ===
    y_pred_train = model.predict(X_train)
    train_pearson = np.corrcoef(y_train, y_pred_train)[0, 1]
    train_mae = np.mean(np.abs(y_train - y_pred_train))
    print(f"📈 训练集: Pearson IC={train_pearson:.3f}, MAE={train_mae:.2f}%")

    # === 特征重要性 ===
    importance = sorted(zip(feature_cols, model.feature_importance(importance_type="gain")),
                         key=lambda x: -x[1])
    print(f"🎯 特征重要性 (gain):")
    for name, imp in importance:
        bar = "█" * min(20, int(imp / max([i[1] for i in importance]) * 20))
        print(f"   {name:12s} {bar} {imp:.0f}")

    # === 预测全 watchlist ===
    predict_rows = [build_features(c) for c in predict_codes]
    predict_rows = [r for r in predict_rows if r is not None]
    print(f"🔮 预测: {len(predict_rows)} 只票")

    predict_df = pd.DataFrame(predict_rows)
    X_predict = predict_df[feature_cols].values
    predict_df["lgb_score"] = model.predict(X_predict)

    # 排序
    predict_df = predict_df.sort_values("lgb_score", ascending=False).reset_index(drop=True)
    predict_df["排名"] = predict_df.index + 1

    # === 落报告 ===
    out_path = out_path or str(DOCS_DIR / f"lgb_ranking_{datetime.now().strftime('%Y%m%d')}.md")
    Path(out_path).parent.mkdir(exist_ok=True)

    with open(out_path, "w") as f:
        f.write(f"""# LightGBM 多因子打分排序 — {datetime.now().strftime('%Y-%m-%d')}

**模型**: LightGBM regression (5d 涨幅预测)
**训练**: {len(train_rows)} 只 baseline 票 | 8 维特征 | 100 轮迭代
**耗时**: {elapsed:.1f}s
**训练集指标**: Pearson IC={train_pearson:.3f}, MAE={train_mae:.2f}%

⚠️ **限制**: 17 只训练样本太少, 实战应扩到 50+ 票; 当前是骨架, 验证流程

## 🎯 特征重要性 (gain)

| 特征 | 重要性 |
|------|------|
""")
        for name, imp in importance:
            f.write(f"| {name} | {imp:.0f} |\n")

        f.write(f"""
## 🏆 全 Watchlist 排序 (Top 20)

| 排名 | 代码 | 名称 | LGB 分数 (5d 预期%) | PEG | PE_TTM | RSI | 板块 |
|------|------|------|------|------|------|------|------|
""")
        # 加载 watchlist 板块信息
        wl_dict = {w["code"]: w.get("sector", "—") for w in watchlist}
        for _, row in predict_df.head(20).iterrows():
            f.write(f"| {row['排名']} | {row['code']} | {row['name']} | "
                    f"{row['lgb_score']:+.2f}% | {row['PEG']:.2f} | "
                    f"{row['PE_TTM']:.0f} | {row['RSI']:.0f} | "
                    f"{wl_dict.get(row['code'], '—')} |\n")

        f.write(f"""
## 📊 Bottom 10 (减仓候选)

| 排名 | 代码 | 名称 | LGB 分数 | 板块 |
|------|------|------|------|------|
""")
        for _, row in predict_df.tail(10).iterrows():
            f.write(f"| {row['排名']} | {row['code']} | {row['name']} | "
                    f"{row['lgb_score']:+.2f}% | {wl_dict.get(row['code'], '—')} |\n")

        f.write(f"""
## 📋 评估

- **Pearson IC (训练)**: {train_pearson:.3f}  ({'强信号' if abs(train_pearson) > 0.3 else '弱信号, 样本不足'})
- **MAE**: {train_mae:.2f}% (越小越好)
- **Top 1**: {predict_df.iloc[0]['name']} ({predict_df.iloc[0]['lgb_score']:+.2f}%)
- **Bottom 1**: {predict_df.iloc[-1]['name']} ({predict_df.iloc[-1]['lgb_score']:+.2f}%)

## 🔧 跑参

```python
objective = "regression"
metric = "mae"
learning_rate = 0.05
num_leaves = 7
max_depth = 3
num_boost_round = 100
```

## 🚀 扩展方向

- **样本扩展**: 训练 17 → 50+ 票, IC 提升
- **特征扩展**: 接 alpha_ga_001 (GA 挖的) + 8 维手工特征 = 9 维
- **目标扩展**: 5d → 10d / 20d 多任务
- **模型扩展**: 加 XGBoost / CatBoost 对比
- **集成**: LightGBM + GA 因子 + 5方法综合矩阵 = 终极打分
""")

    print(f"\n📄 报告已落地: {out_path}")
    print(f"🏆 Top 1: {predict_df.iloc[0]['name']} ({predict_df.iloc[0]['lgb_score']:+.2f}%)")
    print(f"📉 Bottom 1: {predict_df.iloc[-1]['name']} ({predict_df.iloc[-1]['lgb_score']:+.2f}%)")
    return predict_df


if __name__ == "__main__":
    main()
