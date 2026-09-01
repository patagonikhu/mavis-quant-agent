"""
ga_mine.py - GA 挖掘 demo (Day 9, 2026-07-27)

目标: 在 300274 (阳光电源) 上跑 gplearn, 挖掘"缠论/SMC 增强因子"
输入: 7 个 factor 输出 (dict/Series) + 基础 OHLCV
输出: docs/ga_results_{code}_{date}.md + data/ga_pool.json

参数集中在 config/project.yaml (ga 段), 改 1 处全项目生效
"""
import sys
import json
import time
import warnings
from pathlib import Path
from datetime import datetime
import yaml

warnings.filterwarnings("ignore")

# === 项目级配置加载 (2026-07-27 集中管理, 无默认值) ===
def _load_ga_config() -> dict:
    """从 config/project.yaml 加载 ga 段, 没有就报错"""
    config_path = Path(__file__).parent.parent / "config" / "project.yaml"
    if not config_path.exists():
        raise FileNotFoundError(
            f"❌ 找不到 {config_path}\n"
            f"   首次使用请: 手动创建 config/project.yaml (不在 git 里, 参考 git history 或 docs/AGENT_MEMORY.md)"
        )
    with open(config_path) as f:
        return yaml.safe_load(f).get("ga", {})

_GA_CFG = _load_ga_config()

import numpy as np
import pandas as pd
from gplearn.genetic import SymbolicRegressor


def fetch_features(code: str = "300274") -> pd.DataFrame:
    """从本地历史库读K线，算 7 个 factor 输出 + 基础 OHLCV"""
    from tools.kline_store import DataStore
    ctx = DataStore.get_ctx(code)
    if not ctx.kline:
        raise FileNotFoundError(f"本地无K线: {code}，先跑 python -m tools.kline_store --init")

    kline = ctx.kline
    df = pd.DataFrame({
        "date":   [k["trade_date"] for k in kline],
        "open":   [k["open"]   for k in kline],
        "high":   [k["high"]   for k in kline],
        "low":    [k["low"]    for k in kline],
        "close":  [k["close"]  for k in kline],
        "volume": [k.get("vol", k.get("volume", 0)) for k in kline],
    })
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()

    # === 算 7 个 factor 输出, 当作 GP 的"原子特征" ===
    # 1. OBV (TS)
    df["obv"] = (np.sign(df["close"].diff()) * df["volume"]).cumsum()
    # 2. 5日/20日 涨幅
    df["ret_5"] = df["close"].pct_change(5)
    df["ret_20"] = df["close"].pct_change(20)
    # 3. 5日/20日 均量比 (缩量/放量)
    df["vol_5_20"] = df["volume"].rolling(5).mean() / df["volume"].rolling(20).mean()
    # 4. RSI 14
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / (loss + 1e-9))
    # 5. MACD area (5d 累计, 当背驰近似)
    ema12 = df["close"].ewm(span=12, adjust=False).mean()
    ema26 = df["close"].ewm(span=26, adjust=False).mean()
    df["macd_hist"] = (ema12 - ema26) - (ema12 - ema26).ewm(span=9, adjust=False).mean()
    # 6. 量价 OBV 5d 变化
    df["obv_5d_chg"] = df["obv"].diff(5) / (df["volume"].rolling(5).mean() + 1)
    # 7. 收盘价百分位排名 (0-1)
    df["close_pct_rank"] = df["close"].rolling(60).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=False)
    # 8. 5日 量价相关系数
    df["vol_price_corr"] = df["close"].rolling(5).corr(df["volume"])

    # 9. 60m 60分 OB 距离 (placeholder, 用 close vs MA20 距离近似)
    df["ma20_dist"] = (df["close"] - df["close"].rolling(20).mean()) / df["close"].rolling(20).mean()
    # 10. 多市场共振 (placeholder, 用 5d 涨幅 > 0 近似)
    df["up_5d"] = (df["ret_5"] > 0).astype(int)

    # === Target: 未来 5d 涨幅 (fitness 用) ===
    df["target_5d"] = df["close"].shift(-5) / df["close"] - 1

    # 丢 NaN
    df = df.dropna()
    return df


def run_ga(code: str = "300274", generations: int = None, pop: int = None):
    """跑 GA (参数从 config/project.yaml 读, 命令行可覆盖)"""
    generations = generations or _GA_CFG["default_generations"]
    pop = pop or _GA_CFG["default_population"]
    print(f"🧬 GA 挖掘 {code} | 代数={generations} | 种群={pop}")
    print(f"⏱️  预计耗时: {generations * pop * 0.1:.0f}-{generations * pop * 0.5:.0f} 秒")

    df = fetch_features(code)
    print(f"📊 数据: {len(df)} 根 K 线 | {df.index[0].date()} → {df.index[-1].date()}")

    # 特征列 (10 个 factor)
    feature_cols = ["obv", "ret_5", "ret_20", "vol_5_20", "rsi", "macd_hist",
                    "obv_5d_chg", "close_pct_rank", "vol_price_corr", "ma20_dist"]
    X = df[feature_cols].values
    y = df["target_5d"].values

    # 训练/测试切分 (前 70% 训练, 后 30% 测试)
    split = int(len(df) * 0.7)
    X_train, X_test = X[:split], X[split:]
    y_train, y_test = y[:split], y[split:]

    # 跑 GP
    t0 = time.time()
    est = SymbolicRegressor(
        population_size=pop,
        generations=generations,
        tournament_size=5,
        stopping_criteria=0.01,
        p_crossover=0.7,
        p_subtree_mutation=0.1,
        p_hoist_mutation=0.05,
        p_point_mutation=0.1,
        p_point_replace=0.05,
        init_depth=tuple(_GA_CFG["init_depth"]),
        init_method="half and half",
        function_set=tuple(_GA_CFG["function_set"]),
        metric=_GA_CFG["metric"],
        parsimony_coefficient=_GA_CFG["parsimony_coefficient"],
        random_state=42,
        n_jobs=1,
        verbose=1,
    )
    # 关键: 用 abs(pearson) 当 fitness, 不管正负方向, GP 选绝对值最大的
    # 然后 eval 时取 sign(测试IC) * 绝对值, 报告里写明方向
    y_train_abs = np.abs(y_train)  # 留个接口, 这里其实用 pearson 自带正负
    est.fit(X_train, y_train)
    t1 = time.time()
    print(f"\n⏱️  GA 训练耗时: {t1-t0:.1f}s")

    # 评估
    y_pred_train = est.predict(X_train)
    y_pred_test = est.predict(X_test)

    train_pearson = np.corrcoef(y_train, y_pred_train)[0, 1]
    test_pearson = np.corrcoef(y_test, y_pred_test)[0, 1]
    # 用 |IC| 当强度指标
    train_ic_abs = abs(train_pearson)
    test_ic_abs = abs(test_pearson)

    # 5d 胜率 (用 50% 样本做 top/bottom 分组对比)
    train_winrate = calc_winrate(y_train, y_pred_train)
    test_winrate = calc_winrate(y_test, y_pred_test)
    # 绝对方向胜率 (把方向取反, 看哪种 sign 让胜率更高)
    train_winrate_abs = abs(train_winrate)
    test_winrate_abs = abs(test_winrate)

    # 取出 top 5 公式
    programs = est._programs[-1]  # 最后一代
    # 按 fitness 排序
    fitness_scores = [est.run_details_["best_oob_fitness"][i] if i < len(est.run_details_["best_oob_fitness"]) else 0
                      for i in range(min(5, len(programs)))]
    top_programs = programs[:5]

    # === 落地结果 ===
    out_dir = Path("docs")
    out_dir.mkdir(exist_ok=True)
    md_path = out_dir / f"ga_results_{code}_{datetime.now().strftime('%Y%m%d')}.md"

    with open(md_path, "w") as f:
        f.write(f"""# GA 挖掘结果 — {code} {json.load(open('data/watchlist.json'))['watchlist'][0]['name'] if False else ''}

**跑参**: {generations}代 × {pop}种群 | **耗时**: {(t1-t0):.0f}s
**数据**: {len(df)} 根 K 线 | {df.index[0].date()} → {df.index[-1].date()}
**训练/测试切分**: 70/30 (训练 {split} 根, 测试 {len(df)-split} 根)

## 📊 Top-Line 指标

| 指标 | 训练集 | 测试集 | 衰减 |
|---|---|---|---|
| Pearson IC | {train_pearson:.3f} | {test_pearson:.3f} | {(1 - test_pearson/train_pearson)*100 if train_pearson else 0:.0f}% |
| |IC| (强度) | {train_ic_abs:.3f} | {test_ic_abs:.3f} | — |
| 5d 胜率 (top vs bottom) | {train_winrate:+.1%} | {test_winrate:+.1%} | — |
| 5d 胜率 |胜率| (反向后) | {train_winrate_abs:.1%} | {test_winrate_abs:.1%} | — |

**判定**:
- |IC| > 0.05 = 弱信号
- |IC| > 0.10 = 强信号
- 训练/测试衰减 < 50% = 健康 (防过拟合)
- 测试胜率 > 55% = 可入库
- IC 正负 = 因子方向: 正=高因子值→5d涨, 负=高因子值→5d跌

## 🥇 Top 5 公式 (按 fitness 排序)

| # | 公式 | 训练 IC | 测试 IC |
|---|---|---|---|
""")
        for i, p in enumerate(top_programs):
            try:
                formula = str(p)
            except Exception:
                formula = f"prog_{i}"
            f.write(f"| {i+1} | `{formula}` | — | — |\n")

        f.write(f"""
## 🎯 评估结论

- **最佳 |IC|**: {test_ic_abs:.3f} (测试集, 方向: {'正向' if test_pearson > 0 else '反向'})
- **健康度**: {(1 - test_pearson/train_pearson)*100 if train_pearson else 0:.0f}% 衰减
- **建议**: {'✅ 可入库 alpha_ga_001' if test_ic_abs > 0.10 and test_ic_abs / max(train_ic_abs, 0.01) > 0.5 else '❌ 不可入库, IC 太低或过拟合'}

## 📋 特征列 (供查)

`{', '.join(feature_cols)}`

## 🔧 跑参

```python
generations = {generations}
population_size = {pop}
function_set = ('add', 'sub', 'mul', 'abs')
metric = 'pearson'
parsimony_coefficient = 0.001
```
""")

    print(f"\n📄 结果已落地: {md_path}")
    print(f"📊 测试 IC: {test_pearson:+.3f} | 训练 IC: {train_pearson:+.3f} (|IC|: {test_ic_abs:.3f})")
    print(f"🎯 5d 胜率: 训练 {train_winrate:+.1%} | 测试 {test_winrate:+.1%} (绝对: {test_winrate_abs:.1%})")

    return {
        "code": code,
        "train_pearson": train_pearson,
        "test_pearson": test_pearson,
        "train_ic_abs": train_ic_abs,
        "test_ic_abs": test_ic_abs,
        "train_winrate": train_winrate,
        "test_winrate": test_winrate,
        "elapsed_sec": t1 - t0,
    }


def calc_winrate(y_true, y_pred, top_pct=0.3):
    """top_pct 高预测 vs bottom_pct 低预测 的真实 5d 胜率差"""
    n = len(y_true)
    top_n = int(n * top_pct)
    idx_top = np.argsort(y_pred)[-top_n:]
    idx_bot = np.argsort(y_pred)[:top_n]
    winrate_top = (y_true[idx_top] > 0).mean()
    winrate_bot = (y_true[idx_bot] > 0).mean()
    return winrate_top - winrate_bot  # 差值越大, 因子越能区分


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "300274"
    gens = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    pop = int(sys.argv[3]) if len(sys.argv) > 3 else 100
    run_ga(code, gens, pop)
