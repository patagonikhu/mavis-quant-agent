"""industry_position.py — 行业内地位 (龙头判定)

回答两个问题:
  1. 这只票在板块里算不算龙头? → 市值排名 (存量地位) + 区间涨幅排名 (动能地位)
  2. 龙头是"市值大"还是"涨得快"?  → 两者几乎不相关, 必须分开看

**为什么市值排名不能单独当龙头依据** (实测 205 只近两年十倍股, **SW2021 口径**):
  - 起涨日市值分位中位 67.6% (高点日才降到 12.5%) → 十倍股起涨时基本都不是市值龙头
  - 但区间涨幅分位中位 2.3%, 92.2% 在行业涨幅前 10%
  - 市值分位 vs 涨幅分位 Spearman = -0.126 (无关, 旧版口径 -0.095)
  → 结论: 龙头 = **动能领先** (涨幅排名), 市值只是存量地位参考

数据 (0 网络, 全本地 parquet):
  - stk_factor:    total_mv / circ_mv (万元) / close / total_share, 6,021 只 × 1,174 日
  - 行业归属/同行分组: industry_member.parquet (index_member_all, **SW2021 134 个二级行业**)
    ⚠️ 2026-11-11 换口径: 之前用 stock_basic.industry = 申万**旧版** 110 个 →
       「电气设备」348 家把光伏/电机/电网混一锅, 涨幅排名被稀释 (光伏设备实为 75 家)

**区间收益口径 = total_mv 变化** (不是不复权 close):
  送转股日 close 会从 104 跳到 66 (假跌 37%), 但 total_mv 不变 (股东财富没变)。
  total_mv 比值 = 股东实际收益率, 且 99.9% 的股票无股本变动时它 ≡ 股价涨幅。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
STK_FACTOR_DIR = ROOT / "data" / "history" / "stk_factor"
STOCK_BASIC_PARQUET = ROOT / "data" / "history" / "stock_basic" / "stock_basic.parquet"

# 龙头判定阈值: 行业分位 (越小 = 越靠前)
LEADER_TOP_PCTILE = 10.0        # 前 10% 算龙头

# 窗口缓存 (进程内): {交易日数: (start_date, end_date)}
_WINDOW_CACHE: dict[int, tuple[int, int]] = {}


def _to_ts_code(code: str) -> str:
    """6 位代码 → ts_code (600089 / 600089.SH / 600089.SH → 600089.SH)"""
    c = str(code).strip().upper()
    if "." in c:
        return c
    if c.startswith("920"):        # 北交所新号段 (920xxx)
        return f"{c}.BJ"
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith("9"):          # 900xxx B 股 (沪)
        return f"{c}.SH"
    if c.startswith(("4", "8")):    # 430/830/87x 老号段北交所
        return f"{c}.BJ"
    return f"{c}.SZ"


def _industry_members(l2_code: str, l2_name: str = "") -> list[str]:
    """该行业的全部成员 ts_code

    2026-11-11 改: 行业口径统一到 **SW2021** (申万新版 134 个二级行业)
      - 同行组来自 index_member_all 落的 industry_member.parquet (唯一真源)
      - 之前用 stock_basic.industry = 申万**旧版** 110 个 → 「电气设备」348 家
        把光伏/电机/电网设备混一锅, 涨幅排名被稀释 (光伏设备实为 75 家)

    Args:
        l2_code: SW2021 二级行业代码 (801735.SI); 空则用 l2_name 回退查旧版
        l2_name: SW2021 二级行业名 (回退用)
    """
    import duckdb

    if l2_code:
        from tools.storage.store import DataStore
        members = DataStore.get_industry_members_by_l2(l2_code)
        if members:
            return members

    # 回退: 申万旧版 (成分表没覆盖时)
    if not l2_name or not STOCK_BASIC_PARQUET.exists():
        return []
    df = duckdb.connect().execute(
        f"""SELECT DISTINCT ts_code FROM read_parquet('{STOCK_BASIC_PARQUET}')
            WHERE industry = '{l2_name.replace("'", "''")}'"""
    ).df()
    return df["ts_code"].tolist() if not df.empty else []


def _fetch_member_snapshot(members: list[str], start_date: int,
                           end_date: int) -> list:
    """取行业内所有成员在窗口首末日 的市值快照

    Returns:
        rows = [{ts_code, trade_date, total_mv, circ_mv}, ...]
    """
    import duckdb
    if not members or not STK_FACTOR_DIR.exists():
        return []
    codes = ",".join(f"'{c}'" for c in members)
    df = duckdb.connect().execute(f"""
        SELECT s.ts_code, CAST(s.trade_date AS INTEGER) AS trade_date,
               s.total_mv, s.circ_mv
        FROM read_parquet('{STK_FACTOR_DIR}/*.parquet') s
        WHERE s.ts_code IN ({codes})
          AND s.trade_date IN ({int(start_date)}, {int(end_date)})
          AND s.total_mv IS NOT NULL AND s.total_mv > 0
    """).df()
    return [] if df.empty else df.to_dict("records")


def _complete_window(trading_days: int) -> tuple[int, int]:
    """选窗口首末日 —— 必须挑"覆盖完整"的交易日

    ⚠️ 踩过的坑: stk_factor 的最后一个交易日常常是**部分同步** (例 20261110 只有
    1591 只, 前一天 20261109 有 5573 只)。直接取 max(trade_date) 会让
    一半同行"消失" → 行业排名分母缩水 → 排名虚高。
    → 做法: 按日统计覆盖只数, 取覆盖 ≥ 峰值 90% 的日期里最近的 N 个交易日。

    Returns:
        (start_date, end_date)  8 位整数日期
    """
    global _WINDOW_CACHE
    if trading_days in _WINDOW_CACHE:
        return _WINDOW_CACHE[trading_days]

    import duckdb
    if not STK_FACTOR_DIR.exists():
        return 0, 0
    df = duckdb.connect().execute(f"""
        WITH cov AS (
            SELECT trade_date, count(DISTINCT ts_code) AS code_count
            FROM read_parquet('{STK_FACTOR_DIR}/*.parquet')
            GROUP BY 1
        ),
        good AS (
            SELECT trade_date FROM cov
            WHERE code_count >= 0.9 * (SELECT max(code_count) FROM cov)
        )
        SELECT trade_date FROM good ORDER BY trade_date DESC LIMIT {int(trading_days)}
    """).df()
    if df.empty:
        return 0, 0
    dates = sorted(int(x) for x in df["trade_date"].tolist())
    _WINDOW_CACHE[trading_days] = (dates[0], dates[-1])
    return _WINDOW_CACHE[trading_days]


def compute_industry_position(code: str, industry: str | None = None,
                              trading_days: int = 120) -> dict:
    """算单只票的行业内地位 (龙头判定)

    Args:
        code:        6 位股票代码
        industry:    行业名 (兼容旧调用, 实际以 SW2021 成分表为准);
                     None → 自动查
        trading_days: 区间交易日数 (默认 120 = 跟 /t-analyze 因子历史窗口一致)

    Returns:
        dict, 字段见下方; 无行业/无数据时返回 {} (调用方渲染成 "—")

    2026-11-11: 行业口径统一到 SW2021 (申万新版 134 个二级行业)
      - 行业归属 + 同行分组都走 index_member_all 落的 industry_member.parquet
      - industry 参数只当回退 (成分表没覆盖时用旧版 stock_basic.industry)
    """
    import pandas as pd

    ts_code = _to_ts_code(code)

    # SW2021 行业归属 (唯一真源)
    l2_code, l2_name = _lookup_industry(ts_code)
    if not l2_code:
        # 成分表没覆盖 → 回退旧版 (行业名由调用方传)
        l2_name = industry or ""
        if not l2_name:
            return {}

    members = _industry_members(l2_code, l2_name)
    if ts_code not in members:
        # 成分表里没有这只 (ETF / 次新未建档) → 不出行业排名
        return {}

    start_date, end_date = _complete_window(trading_days)
    if not start_date:
        return {}
    rows = _fetch_member_snapshot(members, start_date, end_date)
    if not rows:
        return {}

    df = pd.DataFrame(rows)
    start_df = df[df["trade_date"] == start_date].set_index("ts_code")
    end_df = df[df["trade_date"] == end_date].set_index("ts_code")

    if ts_code not in end_df.index:
        return {}

    # 排名 (只算窗口末日有数据的成员 → 停牌/退市的自然出局)
    end_df = end_df.sort_values("total_mv", ascending=False)
    end_df["market_value_rank"] = range(1, len(end_df) + 1)
    end_df["circulating_value_rank"] = end_df["circ_mv"].rank(ascending=False, method="min").astype(int)
    peer_total = len(end_df)

    if ts_code in start_df.index:
        start_df = start_df.sort_values("total_mv", ascending=False)
        start_df["market_value_rank"] = range(1, len(start_df) + 1)
        start_rank = int(start_df["market_value_rank"].get(ts_code, 0)) or None
    else:
        start_rank = None

    own = end_df.loc[ts_code]
    market_value_rank = int(own["market_value_rank"])
    circulating_value_rank = int(own["circulating_value_rank"])
    market_value_yi = float(own["total_mv"]) / 10000.0
    circulating_value_yi = float(own["circ_mv"] or 0) / 10000.0

    # ---- 区间涨幅 (total_mv 比值 = 股东实际收益, 送转不失真) ----
    gain_pct = None
    gain_rank = None
    gain_peer_total = 0
    industry_gain_pct = None
    excess_pct = None
    if ts_code in start_df.index:
        start_mv = float(start_df.loc[ts_code, "total_mv"])
        common = [c for c in end_df.index if c in start_df.index]
        gains = []
        for c in common:
            g = float(end_df.loc[c, "total_mv"]) / float(start_df.loc[c, "total_mv"]) - 1
            gains.append((c, g))
        gains.sort(key=lambda x: x[1], reverse=True)
        gain_peer_total = len(gains)
        for i, (c, g) in enumerate(gains, 1):
            if c == ts_code:
                gain_rank = i
                gain_pct = g * 100
                break
        if gains:
            industry_gain_pct = sum(g for _, g in gains) / len(gains) * 100
        if gain_pct is not None and industry_gain_pct is not None:
            excess_pct = gain_pct - industry_gain_pct

    # ---- 分位 (越小越靠前) ----
    market_value_percentile = market_value_rank / peer_total * 100 if peer_total else None
    gain_percentile = gain_rank / gain_peer_total * 100 if (gain_rank and gain_peer_total) else None

    # ---- 龙头判定 (规则在本层, 渲染器只读) ----
    verdict, verdict_level = _judge_leader(market_value_percentile, gain_percentile)

    return {
        "industry": l2_name or industry or "",
        "peer_total": peer_total,
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": trading_days,
        "market_value_yi": market_value_yi,
        "circulating_value_yi": circulating_value_yi,
        "market_value_rank": market_value_rank,
        "market_value_rank_total": peer_total,
        "market_value_percentile": market_value_percentile,
        "market_value_rank_start": start_rank,
        "circulating_value_rank": circulating_value_rank,
        "circulating_value_rank_total": peer_total,
        "gain_pct": gain_pct,
        "gain_rank": gain_rank,
        "gain_rank_total": gain_peer_total,
        "gain_percentile": gain_percentile,
        "industry_gain_pct": industry_gain_pct,
        "excess_pct": excess_pct,
        "verdict": verdict,
        "verdict_level": verdict_level,
    }


def _judge_leader(market_value_percentile: float | None,
                  gain_percentile: float | None) -> tuple[str, str]:
    """龙头判定: 市值分位 + 涨幅分位 → 4 档

    Returns:
        (verdict 文本, level 标签)
    """
    size_top = market_value_percentile is not None and market_value_percentile <= LEADER_TOP_PCTILE
    gain_top = gain_percentile is not None and gain_percentile <= LEADER_TOP_PCTILE

    if size_top and gain_top:
        return ("🏆 双料龙头 — 市值 + 涨幅都在行业前 10%, 板块核心", "double")
    if gain_top:
        return ("⚡ 动能龙头 — 涨幅进行业前 10%, 但市值体量不在前 10% (成长型龙头)", "momentum")
    if size_top:
        return ("🏛 存量龙头 — 市值行业前 10%, 但涨幅未进前 10% (滞涨风险)", "size")
    if market_value_percentile is None or gain_percentile is None:
        return ("— 数据不足, 无法判定", "unknown")
    return ("🌱 跟随者 — 市值 + 涨幅都不在行业前 10%", "follower")


def _lookup_industry(ts_code: str) -> tuple[str, str]:
    """查该股的 SW2021 二级行业 → (l2_code, l2_name), 查不到返回 ("", "")

    数据源: index_member_all 落的 industry_member.parquet (唯一真源)
    """
    try:
        from tools.storage.store import DataStore
        info = DataStore.get_industry_info(ts_code)
        return info.get("l2_code", ""), info.get("l2_name", "")
    except Exception:
        return "", ""
