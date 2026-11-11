"""
tools/analysis/industry_analysis.py — 行业版分析 (2026-11-12 加, /t-analyze --sw)

给 SW2021 **二级行业指数**出跟个股同款的分析报告。

为什么能复用个股那一套 (已实测, 不是假设):
  行业指数 K 线 (OHLCV) → 拼 RawContext → **直接调** AnalysisEngine().analyze_history()
  → 跟个股**同一套** WyckoffStrategy, **零改动**。
  (industry_stage.py 早就验过这条路: 120 日 0.28s / 全历史 1696 日 0.24s)

架构 (规则下沉):
  本模块 = 生产侧, 算好 ctx / 行业间排名 / 龙头榜 塞进行数据
  渲染器只读不算 (tools/render/industry_renderer.py 不 import 本模块, 只读 row 里的字段)

数据访问: 全部走 DataStore (tools/storage/store.py) —— 业务层禁直读 parquet (守门员规则)

⚠️ 行业没有财报:
  「财务数据」段对行业不适用 → 行业版报告**不出**那一段, 换成「行业估值 (PE/PB/市值)」。
  「行业地位」段对行业自己不适用 → 换成「行业间地位」+「行业内龙头榜」。
"""
from __future__ import annotations


# ============================================================
# 行业清单
# ============================================================

def list_industry_codes(level: str = "L2", with_data_only: bool = True) -> list[str]:
    """可出报告的行业指数代码列表

    Args:
        level:        "L1" / "L2" —— 申万层级
        with_data_only: True = 只返回**有 K 线数据**的 (默认; 没数据的行业出不了报告)
                        False = 返回成分表里的全部该层级行业

    Returns:
        ['801012.SI', ...] (sorted)
    """
    from tools.storage.store import DataStore

    if level == "L1":
        l1_codes = {v["l1_code"] for v in DataStore.get_industry_level_map().values()
                    if v.get("l1_code")}
    else:
        l1_codes = set(DataStore.get_industry_level_map().keys())

    if not with_data_only:
        return sorted(l1_codes)

    with_data = set(DataStore.list_sw_index_codes())
    return sorted(l1_codes & with_data)


def industry_meta(ts_code: str) -> dict:
    """行业元信息 {code, name, l1_code, l1_name}

    行业名从**成分表**取 (SW2021 唯一真源); K 线 parquet 里也有 name 字段,
    但那是数据源自带的, 以成分表为准 (口径统一)。
    """
    from tools.storage.store import DataStore
    code = str(ts_code)
    rec = DataStore.get_industry_level_map().get(code, {})
    return {
        "code": code,
        "name": rec.get("l2_name") or "",
        "l1_code": rec.get("l1_code") or "",
        "l1_name": rec.get("l1_name") or "",
    }


# ============================================================
# 行业 ctx (RawContext)
# ============================================================

def build_industry_ctx(ts_code: str):
    """行业指数 → RawContext (跟个股 ctx 同结构)

    ⚠️ vol → volume 已在 DataStore.get_sw_index_bars 的 DAO 层做完 (跟个股同口径)。
       不转的话威科夫读到的成交量恒为 0, 量能类判定全废 (**静默出错**)。
    """
    from tools.storage.store import DataStore
    from tools.analysis.analysis_engine import RawContext
    from tools.storage.sources.eastmoney import _synthesize_weekly

    bars = DataStore.get_sw_index_bars(ts_code)
    if not bars:
        return None

    weekly = _synthesize_weekly(bars)
    last = bars[-1]
    meta = industry_meta(ts_code)

    # total_mv 单位是"万元" (申万指数口径同 Tushare daily_basic) → 转亿
    total_mv_yi = (last.get("total_mv") or 0.0) / 1e4
    float_mv_yi = (last.get("float_mv") or 0.0) / 1e4

    ctx = RawContext(
        kline=bars,
        weekly=weekly,
        current_price=float(last.get("close") or 0.0),
        market_cap_yi=total_mv_yi,
        industry=meta["l1_name"],     # 行业自己的"板块" = 所属一级行业
        code=ts_code,
        name=meta["name"] or str(last.get("name") or ts_code),
    )
    # 行业指数没有个股财务 → valuation_data 用指数自带口径 (PE/PB/市值)
    ctx.valuation_data = {
        "pe": last.get("pe"),
        "pb": last.get("pb"),
        "total_mv_yi": total_mv_yi,
        "float_mv_yi": float_mv_yi,
        "amount": last.get("amount"),
    }
    return ctx


# ============================================================
# 行业间地位 (该行业在全部二级行业里的排名)
# ============================================================

def compute_industry_peer_ranks(codes: list[str], trading_days: int = 120) -> dict[str, dict]:
    """一批行业指数的**行业间**排名 (市值 + 区间涨幅)

    个股版是「行业内排名」; 行业版是「行业之间排名」—— 同一套口径换比较对象。

    Args:
        codes:       行业指数代码列表
        trading_days: 区间交易日数 (默认 120 = 跟个股 /t-analyze 因子历史窗口一致)

    Returns:
        {code: {market_value_rank, market_value_rank_total, market_value_percentile,
                gain_pct, gain_rank, gain_rank_total, gain_percentile,
                start_date, end_date, trading_days}}
        没有 K 线数据的 code 不在返回里 (调用方当"判定不了")
    """
    import pandas as pd

    from tools.storage.store import DataStore

    codes = [c for c in codes if c]
    if not codes:
        return {}

    # 1. 一次 SQL 拿所有行业的末日 + 期初快照
    frames = []
    for c in codes:
        bars = DataStore.get_sw_index_bars(c)
        if len(bars) >= 2:
            frames.append(pd.DataFrame(bars))
    if not frames:
        return {}
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)

    all_dates = sorted(df["trade_date"].unique())
    if len(all_dates) < 2:
        return {}
    end_date = all_dates[-1]
    # 期初: 每个行业**自己**往前数 trading_days 根 (有的行业停更过, 不能用全局日期)
    start_map: dict[str, str] = {}
    for c, g in df.groupby("ts_code"):
        d = sorted(g["trade_date"].unique())
        if len(d) >= 2:
            start_map[c] = d[max(0, len(d) - trading_days - 1)]

    end_df = df[df["trade_date"] == end_date].copy()
    end_df = end_df.sort_values("total_mv", ascending=False)
    end_df["mv_rank"] = range(1, len(end_df) + 1)
    mv_total = len(end_df)

    # 区间涨幅 (指数收盘价比值; 行业指数无送转失真问题, 直接用 close)
    gains: list[tuple[str, float]] = []
    for c, g in df.groupby("ts_code"):
        sd = start_map.get(c)
        if not sd:
            continue
        sub = g[g["trade_date"].isin([sd, end_date])]
        if len(sub) < 2:
            continue
        s_close = float(sub[sub["trade_date"] == sd].iloc[0]["close"])
        e_close = float(sub[sub["trade_date"] == end_date].iloc[0]["close"])
        if not s_close:
            continue
        gains.append((c, (e_close / s_close - 1) * 100))
    gains.sort(key=lambda x: x[1], reverse=True)
    gain_rank_map = {c: i for i, (c, _) in enumerate(gains, 1)}
    gain_total = len(gains)

    out: dict[str, dict] = {}
    for _, r in end_df.iterrows():
        c = str(r["ts_code"])
        g = next((gv for gc, gv in gains if gc == c), None)
        mv_rank = int(r["mv_rank"])
        gr = gain_rank_map.get(c)
        out[c] = {
            "market_value_yi": float(r.get("total_mv") or 0.0) / 1e4,
            "float_mv_yi": float(r.get("float_mv") or 0.0) / 1e4,
            "market_value_rank": mv_rank,
            "market_value_rank_total": mv_total,
            "market_value_percentile": mv_rank / mv_total * 100 if mv_total else None,
            "pe": r.get("pe"),
            "pb": r.get("pb"),
            "gain_pct": g,
            "gain_rank": gr,
            "gain_rank_total": gain_total,
            "gain_percentile": (gr / gain_total * 100) if (gr and gain_total) else None,
            "start_date": start_map.get(c, ""),
            "end_date": end_date,
            "trading_days": trading_days,
        }
    return out


def judge_industry_peer(peer: dict | None) -> tuple[str, str]:
    """行业间地位判定 (市值分位 + 涨幅分位 → 4 档)

    跟个股的 `_judge_leader` 同一套逻辑 (同一份真源: industry_position._judge_leader)。
    """
    if not peer:
        return ("— 数据不足, 无法判定", "unknown")
    from tools.analysis.industry_position import _judge_leader
    return _judge_leader(peer.get("market_value_percentile"), peer.get("gain_percentile"))


# ============================================================
# 行业内龙头榜 (成分股: 市值前 N + 涨幅前 N)
# ============================================================

def compute_industry_leader_board(l2_code: str, trading_days: int = 120,
                                  top_n: int = 10) -> dict:
    """某个二级行业的成分股龙头榜

    两张榜 (用户定调 2026-11-12):
      1. 市值前 N  —— 谁是大票 (跟 /t-wyckoff-a2m strict 档同口径)
      2. 涨幅前 N  —— 谁在涨 (区间涨幅用 total_mv 比值 = 股东实际收益, 送转不失真,
                     跟 industry_position.compute_industry_position 同口径)

    Args:
        l2_code:    二级行业代码 (801012.SI)
        trading_days: 区间交易日数 (默认 120)
        top_n:      每张榜取前 N (默认 10)

    Returns:
        {l2_code, l2_name, peer_total, start_date, end_date,
         mv_top: [{code, name, market_value_yi, market_value_percentile, gain_pct}],
         gain_top: [...]}
    """
    from tools.analysis.industry_position import compute_industry_leader_board as _board
    return _board(l2_code, trading_days=trading_days, top_n=top_n)
