"""
boll_bbw_distribution.py — 临时统计: 最近 N 天科技股 BBW / BOLL% 分布

不存, 只 print + 返 dict。一次性脚本。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# 改用项目根相对路径 (跟 bb_obv_scan 一致)
_PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT))
# 确保相对路径 (data/history/...) 在 cwd 也对得上
import os
os.chdir(_PROJECT)

from concurrent.futures import ThreadPoolExecutor, as_completed
from tools.kline_store import DataStore
from tools.analysis.analysis_engine import AnalysisEngine
import pandas as pd

# 1) 拿科技股池 (申万行业 ∈ 科技股)
print("加载 stock_basic ...")
df_basic = pd.read_parquet("data/history/stock_basic/stock_basic.parquet")
# 复用 bb_obv_scan 里的行业分类 (与它一致)
TECH_INDUSTRIES = {
    "半导体", "元器件", "光学光电子", "消费电子", "电子化学品", "其他电子",
    "通信设备", "通信服务", "计算机设备", "IT 服务", "软件开发", "互联网",
    "航空装备", "航天装备", "地面兵装", "船舶制造", "自动化设备", "机械设备",
    "通用机械", "专用机械", "工程机械", "金属制品", "电源设备", "电机",
    "电池", "光伏设备", "风电设备", "电力设备", "电气设备", "仪器仪表",
    "汽车零部件", "汽车服务", "乘用车", "商用车", "摩托车及其他",
    "化学制药", "中药", "生物制品", "医疗器械", "医药商业", "医疗服务",
    "橡胶", "塑料", "化学纤维", "化学制品", "农化制品",
    "钢铁", "钢铁板材", "钢铁特钢", "建筑材料", "水泥", "玻璃玻纤",
    "金属新材料", "工业金属", "贵金属", "小金属",
}
mask = df_basic["industry"].isin(TECH_INDUSTRIES)
codes = df_basic.loc[mask, "ts_code"].str[:6].tolist()
print(f"科技股池: {len(codes)} 只")

# 2) 同步全量 (确保 20260901 数据在)
from tools.kline_store import sync_incremental
sync_incremental(target_date="20260901")

# 3) 取最近 2 个交易日
all_dates = sorted(Path("data/history/daily").glob("*.parquet"))[-1:]
# 实际取 parquet 末 2 行对应 trade_date
import pyarrow.parquet as pq
sample = pq.read_table(all_dates[0]).to_pandas().sort_values("trade_date")
last_dates = sample["trade_date"].drop_duplicates().sort_values().tail(2).tolist()
print(f"最近 2 个交易日: {last_dates}")

# 4) 并发算每只 BBW / BOLL%
def calc_one(code: str) -> dict:
    try:
        kline = DataStore.get_kline(code, limit=30)
        if len(kline) < 25:
            return None
        ctx = DataStore.get_ctx(code)
        # 用 AnalysisEngine Wyckoff strategy (算 boll_pct/boll_width) — 传类, 不是字符串
        from tools.analysis.analysis_engine import WyckoffStrategy, ObvStrategy
        engine = AnalysisEngine(strategies=[WyckoffStrategy, ObvStrategy])
        # 找最后 2 个 trade_date 对应的 date
        all_dates_k = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
        target_dates = [d for d in all_dates_k if d in last_dates]
        if not target_dates:
            return None
        history = engine.analyze_history(ctx, target_dates)
        result = []
        # BOLL 字段直读 ctx.kline_arrs (wyckoff raw 不存)
        arrs = ctx.kline_arrs or {}
        bp_arr  = arrs.get("boll_pct")
        bw_arr  = arrs.get("boll_width")
        date_idx = {k["trade_date"].replace("-", "")[:8]: i for i, k in enumerate(ctx.kline)}
        for d in target_dates:
            r = history.get(d)
            if not r or not r.raw:
                continue
            idx = date_idx.get(d)
            bp = float(bp_arr[idx]) if (bp_arr is not None and idx is not None) else None
            bw = float(bw_arr[idx]) if (bw_arr is not None and idx is not None) else None
            result.append({
                "code": code, "date": d,
                "boll_pct": bp, "boll_width": bw,
            })
        return result
    except Exception as e:
        import traceback
        return [{"code": code, "error": str(e), "tb": traceback.format_exc()[:500]}]
    except Exception as e:
        return None

print(f"算 {len(codes)} 只 (4 workers)...")
t0 = datetime.now()
rows = []
with ThreadPoolExecutor(max_workers=4) as ex:
    futs = {ex.submit(calc_one, c): c for c in codes}
    for i, fut in enumerate(as_completed(futs), 1):
        r = fut.result()
        if r:
            rows.extend(r)
        if i % 500 == 0:
            print(f"  {i}/{len(codes)}  ({(datetime.now()-t0).total_seconds():.0f}s)")

print(f"总耗时 {(datetime.now()-t0).total_seconds():.0f}s, 有效 {len(rows)} 行")

# 5) 统计
df = pd.DataFrame(rows)
print(f"\nRow 样本 (前 5 行):")
if len(df):
    print(df.head().to_string())
    print(f"\nNaN 分布: boll_pct NaN={df['boll_pct'].isna().sum()}, boll_width NaN={df['boll_width'].isna().sum()}")
print()
print("=" * 60)
print(f"{'日期':<12}{'样本':<8}{'BBW<8%':<10}{'BBW<10%':<10}{'BBW<12%':<10}{'BOLL<15%':<10}{'BOLL<25%':<10}")
print("=" * 60)
for d in last_dates:
    sub = df[df["date"] == d]
    n = len(sub)
    n8  = (sub["boll_width"] < 8).sum() if n else 0
    n10 = (sub["boll_width"] < 10).sum() if n else 0
    n12 = (sub["boll_width"] < 12).sum() if n else 0
    b15 = (sub["boll_pct"] < 15).sum() if n else 0
    b25 = (sub["boll_pct"] < 25).sum() if n else 0
    print(f"{d:<12}{n:<8}{n8:<10}{n10:<10}{n12:<10}{b15:<10}{b25:<10}")
print()

# 双确认: BBW<10% AND BOLL<15%
print("双确认 (BBW<10% AND BOLL<15%):")
for d in last_dates:
    sub = df[df["date"] == d]
    n = ((sub["boll_width"] < 10) & (sub["boll_pct"] < 15)).sum()
    print(f"  {d}: {n} 只")
print("三确认 (+ OBV): 看 bb_obv_scan 结果 (0 命中)")
