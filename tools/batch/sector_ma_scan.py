"""
tools/batch/sector_ma_scan.py
板块均线顶底信号 (走 DataStore, 0 网络)

4 个核心信号:
  1. close vs MA20       → 短期顶/底
  2. close vs MA60       → 中期顶/底 (牛熊分界)
  3. MA20 金叉/死叉 MA60 → 趋势反转确认
  4. 偏离 MA20 幅度      → 超买/超卖 (顶: >+15%, 底: <-8%)

输入: 行业名 (e.g. "半导体") 或 "all" (全市场)
输出: 板块等权指数顶底信号 + 个股一览

用法:
  bash tools/with_venv.sh python -m tools.batch.sector_ma_scan 半导体
  bash tools/with_venv.sh python -m tools.batch.sector_ma_scan --side top
  bash tools/with_venv.sh python -m tools.batch.sector_ma_scan 半导体 --side bottom
  bash tools/with_venv.sh python -m tools.batch.sector_ma_scan --all --top-dev 10
"""
import argparse
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_kline_via_datastore(years: float = 1.5) -> pd.DataFrame:
    """走 DataStore.load_all_kline (1 次 SQL 拿全市场 K 线, 0 网络)"""
    from tools.storage.store import DataStore
    raw = DataStore.load_all_kline(years=years)
    rows = []
    for ts_code, recs in raw.items():
        for r in recs:
            rows.append({
                "ts_code": ts_code,
                "trade_date": r["trade_date"],
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "vol": r["vol"],
                "amount": r.get("amount", np.nan),
            })
    return pd.DataFrame(rows)


def load_stock_basic_via_datastore() -> pd.DataFrame:
    """走 DataStore.load_stock_basic (1 次 SQL 拿全市场 stock_basic, 0 网络)"""
    from tools.storage.store import DataStore
    df = DataStore.load_stock_basic()
    if df.empty:
        return df
    return df


def add_volume_signals(idx: pd.DataFrame) -> pd.DataFrame:
    """量能信号: vol_ma5, vol_ratio_5, vol_ma5_slope, 量能底判定"""
    g = idx.groupby("industry")
    idx["vol_ma5"] = g["idx_vol"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    idx["vol_ma20"] = g["idx_vol"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    # 当日量比 (vs 5 日均量)
    idx["vol_ratio_5"] = idx["idx_vol"] / idx["vol_ma5"]
    # 短期/中期量比
    idx["vol_ratio_5_20"] = idx["vol_ma5"] / idx["vol_ma20"]
    # 5 日量斜率 (量能加速/减速)
    idx["vol_ma5_5d_ago"] = g["vol_ma5"].shift(5)
    idx["vol_ma5_slope"] = (idx["vol_ma5"] / idx["vol_ma5_5d_ago"] - 1) * 100
    return idx


def compute_ma_morphology(idx: pd.DataFrame) -> pd.DataFrame:
    """MA 形态学: 5 年回测实证 (半导体历史顶点/低点平均)

    顶点特征 (2024-2026 共 2 个):
      MA20 5日斜率 ≈ +2.48%
      MA60 5日斜率 ≈ +2.02%
      M20-M60 间距 ≈ +6.89%
      M60-M120 间距 ≈ +12.86%

    低点特征 (2024-2026 共 3 个):
      MA20 5日斜率 ≈ -3.75%
      MA60 5日斜率 ≈ -1.54%
      M20-M60 间距 ≈ -7.04%
      M60-M120 间距 ≈ +0.65%

    距离典型顶/底 0-100% 评分
    """
    g = idx.groupby("industry")
    # MA 5 日斜率
    idx["ma20_5d_ago"] = g["ma20"].shift(5)
    idx["ma60_5d_ago"] = g["ma60"].shift(5)
    idx["ma20_slope"] = (idx["ma20"] / idx["ma20_5d_ago"] - 1) * 100
    idx["ma60_slope"] = (idx["ma60"] / idx["ma60_5d_ago"] - 1) * 100
    # MA 间距
    idx["ma20_ma60_gap"] = (idx["ma20"] - idx["ma60"]) / idx["ma60"] * 100
    idx["ma60_ma120_gap"] = (idx["ma60"] - idx["ma120"]) / idx["ma120"] * 100

    # === 距离典型顶/底 的接近度 (-100 = 远离, 0 = 完全一致) ===
    # 顶点 4 维典型值
    TOP_MA20_SLOPE = 2.48
    TOP_MA60_SLOPE = 2.02
    TOP_M20_M60_GAP = 6.89
    TOP_M60_M120_GAP = 12.86
    # 低点 4 维典型值
    BOT_MA20_SLOPE = -3.75
    BOT_MA60_SLOPE = -1.54
    BOT_M20_M60_GAP = -7.04
    BOT_M60_M120_GAP = 0.65

    # 每个维度的 0-1 相似度 (1 = 完全等于典型值)
    def similarity_to(series, target, scale=5.0):
        """线性相似度: 偏离 target 每 scale 个百分点, 相似度降 1"""
        return np.clip(1 - np.abs(series - target) / scale, 0, 1)

    # 4 维几何平均
    top_sim = (
        similarity_to(idx["ma20_slope"], TOP_MA20_SLOPE) *
        similarity_to(idx["ma60_slope"], TOP_MA60_SLOPE) *
        similarity_to(idx["ma20_ma60_gap"], TOP_M20_M60_GAP) *
        similarity_to(idx["ma60_ma120_gap"], TOP_M60_M120_GAP)
    ) ** 0.25 * 100  # 转 %

    bot_sim = (
        similarity_to(idx["ma20_slope"], BOT_MA20_SLOPE) *
        similarity_to(idx["ma60_slope"], BOT_MA60_SLOPE) *
        similarity_to(idx["ma20_ma60_gap"], BOT_M20_M60_GAP) *
        similarity_to(idx["ma60_ma120_gap"], BOT_M60_M120_GAP)
    ) ** 0.25 * 100

    idx["top_similarity"] = top_sim
    idx["bot_similarity"] = bot_sim
    return idx


def detect_volume_bottom(idx: pd.DataFrame) -> pd.DataFrame:
    """量能底判定: 历史低点平均 (量比 1.33, 量比 5/20 1.05, 5日量斜率 +12.85%)

    真底需要的量能信号:
      - 当日量比 > 1.33 (放量见底)
      - 量比 5/20 > 1.05 (短期量超过中期)
      - 5日量斜率 > +10% (量能加速)
    """
    g = idx.groupby("industry")
    # 量能底 3 维典型值
    VOL_BOT_RATIO = 1.33
    VOL_BOT_RATIO_5_20 = 1.05
    VOL_BOT_SLOPE = 12.85

    def vol_sim(series, target, scale=0.5):
        return np.clip(1 - np.abs(series - target) / scale, 0, 1)

    vol_bot_sim = (
        vol_sim(idx["vol_ratio_5"], VOL_BOT_RATIO) *
        vol_sim(idx["vol_ratio_5_20"], VOL_BOT_RATIO_5_20) *
        vol_sim(idx["vol_ma5_slope"], VOL_BOT_SLOPE, scale=20)
    ) ** (1/3) * 100

    idx["vol_bot_similarity"] = vol_bot_sim
    return idx


def classify_phase(row) -> str:
    """根据 MA 形态 + 量能 综合判定当前阶段

    返回 4 阶段之一:
      1️⃣ 见顶/杀跌 (top_sim > 60, MA 形态像历史顶部)
      2️⃣ 装死阴跌 (top_sim 30-60 + 量能萎缩, 空头发散但未止跌)
      3️⃣ 筑底 (bot_sim > 60, MA 形态像历史底部, 但量能未确认)
      4️⃣ 真底 (bot_sim > 60 + vol_bot_sim > 50, 价+量双确认)

    注: 相似度 < 30 表示离典型顶/底都远, 归为震荡
    """
    top_sim = row.get("top_similarity", 0)
    bot_sim = row.get("bot_similarity", 0)
    vol_bot = row.get("vol_bot_similarity", 0)
    vol_slope = row.get("vol_ma5_slope", 0)
    ma20_ma60_gap = row.get("ma20_ma60_gap", 0)

    # 4 阶段优先
    if bot_sim >= 60 and vol_bot >= 50:
        return "4️⃣ 真底 (价+量双确认)"
    if bot_sim >= 60:
        return "3️⃣ 筑底 (量能待确认)"
    if top_sim >= 60:
        return "1️⃣ 顶部/杀跌"
    if top_sim >= 30 and vol_slope < 0:
        return "2️⃣ 装死阴跌"
    # 兜底: 如果 ma20-ma60 间距很负 (<-7%) 但 top_sim < 30, 归 2️⃣
    if ma20_ma60_gap < -7 and vol_slope < 0:
        return "2️⃣ 装死阴跌"
    if top_sim >= 30:
        return "🟡 偏弱"
    if bot_sim >= 30:
        return "🟢 偏强"
    return "🟡 震荡"


def list_industries() -> list[str]:
    """列出 stock_basic 里所有 industry"""
    basic = load_stock_basic_via_datastore()
    if basic.empty:
        return []
    return sorted(basic["industry"].dropna().unique().tolist())


def build_industry_index(daily: pd.DataFrame, basic: pd.DataFrame,
                          industries: list[str] | None = None) -> pd.DataFrame:
    """按 industry 合成板块等权指数

    Args:
        industries: None = 全市场所有行业;  list = 只合指定行业
    """
    df = daily.merge(basic[["ts_code", "industry"]], on="ts_code", how="inner")
    df = df.dropna(subset=["industry", "close"])
    if industries is not None:
        df = df[df["industry"].isin(industries)]
    df = df.sort_values(["industry", "ts_code", "trade_date"])
    df["ret"] = df.groupby("ts_code")["close"].pct_change()
    df["norm"] = df.groupby("ts_code")["ret"].transform(
        lambda s: (1 + s.fillna(0)).cumprod()
    )
    idx = df.groupby(["industry", "trade_date"]).agg(
        idx_close=("norm", "mean"),
        idx_vol=("vol", "sum"),
        idx_amt=("amount", "sum"),
        n_stocks=("ts_code", "nunique"),
    ).reset_index()
    idx = idx.sort_values(["industry", "trade_date"])
    return idx


def add_ma_signals(idx: pd.DataFrame) -> pd.DataFrame:
    """4 均线体系: MA5 / MA20 / MA60 / MA120 + 金叉死叉 + 偏离度 + 多空排列"""
    g = idx.groupby("industry")
    # 4 条均线
    idx["ma5"] = g["idx_close"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    idx["ma20"] = g["idx_close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    idx["ma60"] = g["idx_close"].transform(lambda s: s.rolling(60, min_periods=30).mean())
    idx["ma120"] = g["idx_close"].transform(lambda s: s.rolling(120, min_periods=60).mean())

    # 昨收均线 (用于金叉死叉判定)
    idx["ma5_prev"] = g["ma5"].shift(1)
    idx["ma20_prev"] = g["ma20"].shift(1)
    idx["ma60_prev"] = g["ma60"].shift(1)
    idx["ma120_prev"] = g["ma120"].shift(1)

    # 金叉/死叉 (3 套: MA5/20, MA20/60, MA60/120)
    idx["golden_cross_5_20"] = (idx["ma5_prev"] < idx["ma20_prev"]) & (idx["ma5"] > idx["ma20"])
    idx["death_cross_5_20"] = (idx["ma5_prev"] > idx["ma20_prev"]) & (idx["ma5"] < idx["ma20"])
    idx["golden_cross_20_60"] = (idx["ma20_prev"] < idx["ma60_prev"]) & (idx["ma20"] > idx["ma60"])
    idx["death_cross_20_60"] = (idx["ma20_prev"] > idx["ma60_prev"]) & (idx["ma20"] < idx["ma60"])
    idx["golden_cross_60_120"] = (idx["ma60_prev"] < idx["ma120_prev"]) & (idx["ma60"] > idx["ma120"])
    idx["death_cross_60_120"] = (idx["ma60_prev"] > idx["ma120_prev"]) & (idx["ma60"] < idx["ma120"])

    # 偏离度 (vs MA20, 主参考)
    idx["dev_ma5"] = (idx["idx_close"] / idx["ma5"] - 1) * 100
    idx["dev_ma20"] = (idx["idx_close"] / idx["ma20"] - 1) * 100
    idx["dev_ma60"] = (idx["idx_close"] / idx["ma60"] - 1) * 100
    idx["dev_ma120"] = (idx["idx_close"] / idx["ma120"] - 1) * 100

    # 多空排列 (4 均线关系)
    # 多头: ma5 > ma20 > ma60 > ma120
    # 空头: ma5 < ma20 < ma60 < ma120
    # 多头排列得分 = 4 项对称满足的 1/0 累加
    idx["bull_alignment"] = (
        (idx["ma5"] > idx["ma20"]).astype(int) +
        (idx["ma20"] > idx["ma60"]).astype(int) +
        (idx["ma60"] > idx["ma120"]).astype(int)
    )  # 0~3, 3 = 完全多头排列
    idx["bear_alignment"] = (
        (idx["ma5"] < idx["ma20"]).astype(int) +
        (idx["ma20"] < idx["ma60"]).astype(int) +
        (idx["ma60"] < idx["ma120"]).astype(int)
    )  # 0~3, 3 = 完全空头排列

    # 量能均线
    idx["vol_ma5"] = g["idx_vol"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    return idx


def format_alignment(latest_row) -> str:
    """格式化多空排列: 3 字符图标 (🟢🟢🟢 = 完全多头, 🔴🔴🔴 = 完全空头)"""
    bull = int(latest_row["bull_alignment"])
    bear = int(latest_row["bear_alignment"])
    if bull == 3:
        return "🟢🟢🟢 多头"
    if bear == 3:
        return "🔴🔴🔴 空头"
    if bull >= 2:
        return f"🟢🟢⬜ 多偏{bull}-空{bear}"
    if bear >= 2:
        return f"🔴🔴⬜ 空偏{bull}-空{bear}"
    return f"🟡 震荡 {bull}-{bear}"


def main():
    parser = argparse.ArgumentParser(description="板块均线顶底信号 (走 DataStore, 0 网络)")
    parser.add_argument("industry", nargs="?", default="all",
                        help="行业名 (e.g. 半导体 / 电气设备), 默认 all 扫全市场")
    parser.add_argument("--side", choices=["all", "top", "bottom", "phase"], default="all",
                        help="side 模式: all/top/bottom/phase (phase 输出 4 阶段分类 + MA 形态 + 量能底)")
    parser.add_argument("--min-stocks", type=int, default=5)
    parser.add_argument("--top-dev", type=float, default=15.0)
    parser.add_argument("--bottom-dev", type=float, default=-8.0)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--list", action="store_true", help="列出所有 industry 名称后退出")
    args = parser.parse_args()

    if args.list:
        print("📋 所有行业 (来自 DataStore.load_stock_basic):")
        for i, ind in enumerate(list_industries(), 1):
            print(f"  {i:3d}. {ind}")
        return

    print("📥 通过 DataStore 加载数据 (0 网络) ...")
    daily = load_kline_via_datastore(years=1.5)
    basic = load_stock_basic_via_datastore()
    print(f"  K 线: {len(daily):,} 行,  {daily['ts_code'].nunique()} 只股")
    print(f"  stock_basic: {len(basic):,} 行,  {basic['industry'].nunique()} 个行业")

    if daily.empty or basic.empty:
        print("❌ 数据缺失, 请先 /t-sync-data")
        return

    # 决定行业范围
    if args.industry.lower() == "all":
        industries = None
        scope_desc = "全市场"
    else:
        all_ind = set(basic["industry"].dropna().unique().tolist())
        if args.industry not in all_ind:
            print(f"❌ 行业 '{args.industry}' 不存在")
            print(f"   可选: {sorted(list(all_ind))[:20]} ... (用 --list 看全)")
            return
        industries = [args.industry]
        scope_desc = f"行业: {args.industry}"

    print(f"🏭 合成板块等权指数 ({scope_desc}) ...")
    idx = build_industry_index(daily, basic, industries=industries)
    idx = add_ma_signals(idx)
    idx = add_volume_signals(idx)
    idx = compute_ma_morphology(idx)
    idx = detect_volume_bottom(idx)

    today = idx["trade_date"].max()
    print(f"📅 数据最新一天: {today}")

    latest = idx[idx["trade_date"] == today].dropna(subset=["ma20", "ma60", "ma120"]).copy()
    latest = latest[latest["n_stocks"] >= args.min_stocks]

    if len(latest) == 0:
        print(f"❌ 无满足 '板块股票数 ≥ {args.min_stocks}' 的板块 (MA120 需 60+ 天数据, 不够会 NaN)")
        return

    # 板块近 5 日表现
    pct_5d = []
    for ind, sub in idx.groupby("industry"):
        sub = sub.tail(6)
        if len(sub) >= 2:
            pct = (sub["idx_close"].iloc[-1] / sub["idx_close"].iloc[0] - 1) * 100
            pct_5d.append({"industry": ind, "pct_5d": pct})
    pct_5d_df = pd.DataFrame(pct_5d)
    latest = latest.merge(pct_5d_df, on="industry", how="left").fillna({"pct_5d": 0})

    # === 4 阶段分类 + 形态学相似度 ===
    latest["phase"] = latest.apply(classify_phase, axis=1)

    # === 顶信号 (4 均线加权) ===
    # 短顶: 跌破 MA5 (×1) + 跌破 MA20 (×1) + 偏离 MA20 顶阈值 (×1)
    # 中顶: 跌破 MA60 (×2) + MA20/60 死叉 (×3)
    # 长顶: 跌破 MA120 (×3) + MA60/120 死叉 (×4) + 空头排列=3 (×2)
    # 超买: 偏离 MA20 顶阈值 (×1, 已包含)
    latest["top_score"] = (
        # 短
        (latest["idx_close"] < latest["ma5"]).astype(int) * 1 +
        (latest["idx_close"] < latest["ma20"]).astype(int) * 1 +
        (latest["dev_ma20"] > args.top_dev).astype(int) * 1 +
        # 中
        (latest["idx_close"] < latest["ma60"]).astype(int) * 2 +
        latest["death_cross_20_60"].astype(int) * 3 +
        # 长
        (latest["idx_close"] < latest["ma120"]).astype(int) * 3 +
        latest["death_cross_60_120"].astype(int) * 4 +
        (latest["bear_alignment"] == 3).astype(int) * 2 +
        # 偏离连续加权
        np.maximum(latest["dev_ma20"] / args.top_dev, 0)
    )
    cond_top = latest["top_score"] >= 2  # 至少 2 分才算顶信号

    # === 底信号 (4 均线加权) ===
    # 短底: 站回 MA5 (×1) + 偏离 MA20 底阈值 (超卖, ×2)
    # 中底: MA20/60 金叉 (×3) + 站回 MA60 (×1)
    # 长底: 站回 MA120 (×2) + MA60/120 金叉 (×4) + 多头排列=3 (×2)
    latest["bottom_score"] = (
        # 短
        (latest["idx_close"] > latest["ma5"]).astype(int) * 1 +
        (latest["dev_ma20"] < args.bottom_dev).astype(int) * 2 +
        # 中
        (latest["idx_close"] > latest["ma60"]).astype(int) * 1 +
        latest["golden_cross_20_60"].astype(int) * 3 +
        # 长
        (latest["idx_close"] > latest["ma120"]).astype(int) * 2 +
        latest["golden_cross_60_120"].astype(int) * 4 +
        (latest["bull_alignment"] == 3).astype(int) * 2
    )
    cond_bottom = latest["bottom_score"] >= 3  # 至少 3 分才算底信号

    print()
    print("=" * 110)
    print(f"📊 板块均线顶底信号 [{scope_desc}]  板块≥{args.min_stocks}只 | 顶偏离阈 +{args.top_dev}% | 底偏离阈 {args.bottom_dev}%")
    print("=" * 110)

    # === phase 模式: 4 阶段分类 + MA 形态 + 量能底 相似度 ===
    if args.side == "phase":
        phase_sorted = latest.sort_values(
            ["bot_similarity", "top_similarity"], ascending=[False, False]
        ).head(args.top)
        print()
        print(f"📊 4 阶段分类 (基于 MA 形态 + 量能 5 年回测实证):")
        print(f"   1️⃣ 顶部/杀跌: MA 形态 top_similarity ≥ 60")
        print(f"   2️⃣ 装死阴跌: top_similarity 30-60 + 量能萎缩")
        print(f"   3️⃣ 筑底: bot_similarity ≥ 60 + 量能待确认")
        print(f"   4️⃣ 真底: bot_similarity ≥ 60 + vol_bot_similarity ≥ 50 (价+量双确认)")
        print()
        print(f"   形态相似度 0-100% (跟历史典型顶/底 对比):")
        print(f"     顶点典型: MA20斜率+2.48, MA60斜率+2.02, M20-M60间距+6.89, M60-M120间距+12.86")
        print(f"     低点典型: MA20斜率-3.75, MA60斜率-1.54, M20-M60间距-7.04, M60-M120间距+0.65")
        print(f"   量能底典型: 量比1.33, 5/20比1.05, 5日量斜率+12.85")
        print()
        print(f"  {'板块':<8} {'股数':<5} {'阶段':<24} {'顶sim':<6} {'底sim':<6} {'量底sim':<7} {'M20斜率':<8} {'M20-M60':<8} {'量比':<5} {'量斜率':<7}")
        print("  " + "-" * 120)
        for _, r in phase_sorted.iterrows():
            print(
                f"  {r['industry']:<8} {int(r['n_stocks']):<5} {r['phase']:<24} "
                f"{r['top_similarity']:<6.1f} {r['bot_similarity']:<6.1f} {r['vol_bot_similarity']:<7.1f} "
                f"{r['ma20_slope']:+.2f}%   {r['ma20_ma60_gap']:+.2f}%   {r['vol_ratio_5']:<5.2f} {r['vol_ma5_slope']:+.1f}%"
            )
        print()
        print("💡 距离真底最近:  阶段='3️⃣ 筑底' 或 '4️⃣ 真底' + 量能开始放大")
        print("   看底sim (跟历史底相似度) + 量底sim (量能底相似度), 都高 = 真底信号")
        return

    if args.side in ("all", "top"):
        tops = latest[cond_top].sort_values("top_score", ascending=False).head(args.top)
        print()
        print(f"🔴 顶信号板块 (共 {len(tops)} 个, top_score ≥ 2):")
        if len(tops) == 0:
            print("   (无)")
        else:
            print(f"  {'板块':<8} {'股数':<5} {'近5日%':<8} {'偏M5%':<7} {'偏M20%':<7} {'偏M60%':<7} {'偏M120%':<7} {'排列':<10} {'M20/60':<7} {'M60/120':<7} {'top':<5}")
            print("  " + "-" * 130)
            for _, r in tops.iterrows():
                cross_60 = "死叉💀" if r["death_cross_20_60"] else ("金叉✨" if r["golden_cross_20_60"] else "—")
                cross_120 = "死叉💀" if r["death_cross_60_120"] else ("金叉✨" if r["golden_cross_60_120"] else "—")
                align = format_alignment(r)
                print(
                    f"  {r['industry']:<8} {int(r['n_stocks']):<5} {r['pct_5d']:+.2f}%   "
                    f"{r['dev_ma5']:+.1f}  {r['dev_ma20']:+.1f}  {r['dev_ma60']:+.1f}  {r['dev_ma120']:+.1f}  "
                    f"{align:<10} {cross_60:<7} {cross_120:<7} {r['top_score']:.1f}"
                )

    if args.side in ("all", "bottom"):
        bottoms = latest[cond_bottom].sort_values("bottom_score", ascending=False).head(args.top)
        print()
        print(f"🟢 底信号板块 (共 {len(bottoms)} 个, bottom_score ≥ 3):")
        if len(bottoms) == 0:
            print("   (无)")
        else:
            print(f"  {'板块':<8} {'股数':<5} {'近5日%':<8} {'偏M5%':<7} {'偏M20%':<7} {'偏M60%':<7} {'偏M120%':<7} {'排列':<10} {'M20/60':<7} {'M60/120':<7} {'bot':<5}")
            print("  " + "-" * 130)
            for _, r in bottoms.iterrows():
                cross_60 = "金叉✨" if r["golden_cross_20_60"] else ("死叉💀" if r["death_cross_20_60"] else "—")
                cross_120 = "金叉✨" if r["golden_cross_60_120"] else ("死叉💀" if r["death_cross_60_120"] else "—")
                align = format_alignment(r)
                print(
                    f"  {r['industry']:<8} {int(r['n_stocks']):<5} {r['pct_5d']:+.2f}%   "
                    f"{r['dev_ma5']:+.1f}  {r['dev_ma20']:+.1f}  {r['dev_ma60']:+.1f}  {r['dev_ma120']:+.1f}  "
                    f"{align:<10} {cross_60:<7} {cross_120:<7} {r['bottom_score']:.1f}"
                )

    # === 指定行业: 还打印板块内个股一览 ===
    if args.industry.lower() != "all" and len(latest) > 0:
        ind_row = latest.iloc[0]
        ind_name = ind_row["industry"]
        print()
        print(f"📋 板块 '{ind_name}' 内个股 (今日, 涨跌幅排序):")
        daily_today = daily[daily["trade_date"] == today].copy()
        ind_stocks = basic[basic["industry"] == ind_name][["ts_code", "name"]]
        merged = daily_today.merge(ind_stocks, on="ts_code", how="inner")
        if not merged.empty:
            # 自己算 pct_chg (DataStore.load_all_kline 不含此列)
            daily_sorted = daily.sort_values(["ts_code", "trade_date"])
            daily_sorted["prev_close"] = daily_sorted.groupby("ts_code")["close"].shift(1)
            today_with_prev = daily_sorted[daily_sorted["trade_date"] == today][
                ["ts_code", "prev_close"]
            ]
            merged = merged.merge(today_with_prev, on="ts_code", how="left")
            merged["pct_chg"] = (merged["close"] / merged["prev_close"] - 1) * 100
            merged = merged.sort_values("pct_chg", ascending=False)
            print(
                f"  {merged[['ts_code', 'name', 'close', 'pct_chg']].head(20).to_string(index=False)}"
            )

    print()
    print("=" * 110)
    print("💡 4 均线信号解读 (短/中/长 × 顶/底):")
    print("  短 (MA5):  1 周趋势")
    print("  中 (MA20): 1 月趋势, 主偏离参考 (±15% / ±8%)")
    print("  季 (MA60): 牛熊分界, 中期信号")
    print("  长 (MA120): 半年线, 长期信号")
    print()
    print("  顶 (top_score):")
    print("    跌破MA5×1 + 跌破MA20×1 + 偏离顶阈×1 + 跌破MA60×2 + M20/60死叉×3")
    print("    + 跌破MA120×3 + M60/120死叉×4 + 空头排列×2 + 偏离连续加权")
    print("    ≥ 6 强顶  |  4-6 中顶  |  2-3 弱顶")
    print()
    print("  底 (bottom_score):")
    print("    站回MA5×1 + 偏离底阈×2 + 站回MA60×1 + M20/60金叉×3")
    print("    + 站回MA120×2 + M60/120金叉×4 + 多头排列×2")
    print("    ≥ 6 强底  |  4-5 中底  |  3 弱底")


if __name__ == "__main__":
    main()
