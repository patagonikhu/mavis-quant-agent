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

# 龙头判定阈值: 行业分位 (越小 = 越靠前)
LEADER_TOP_PCTILE = 10.0        # 前 10% 算龙头

# 行业口径层级: "L2" (申万二级) / "L3" (申万三级)
# 2026-11-12 改: **默认 L3** (用户要求「股票显示 L3 的板块」) —— 个股报告的
#   🏆行业地位 + /t-wyckoff-a2m 的 strict/loose 分档都用 L3 口径, 两处同口径
#   (CLAUDE.md「跟报告 🏆行业地位 同口径同窗口」这条原则不能破)。
#   L3 归属信息在 industry_member.parquet 里现成 (l3_code/l3_name) → **0 网络请求**。
#
# ⚠️ 门槛不能从 30% 收到 10% (2026-11-12 实测 12 只命中票):
#   L3 行业小 → 10% 门槛下 151/337 个 L3 (45%) 整档出局 (成分股 < 10 只的行业,
#   第 1 名分位都 > 10%, 一只都进不了 strict); 命中票 strict 数 5 → 1 只。
#   30% 门槛下只有 39 个 (12%) 出局, 且 L3 口径并不比 L2 松 (5 vs 6 只)。
INDUSTRY_LEVEL = "L3"

# A→M strict 档门槛的真源 = `tools/batch/wyckoff_a2m_scan.py::FILTER_TIERS`
#   (2026-11-13: 分位口径 → 名次口径, 门槛值 = 行业市值前 3 名)
# ⚠️ 这里**不要**再定义一份门槛常量 —— 2026-11-13 之前这里有个 `TIER_PCTILE = 30.0`,
#   跟 FILTER_TIERS 重复定义, 实际没人读它 (死代码), 改口径时差点漏改。
#   单一真源 = FILTER_TIERS, 本模块只负责**算**位置 (rank / total / percentile)。

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


def _level_keys(level: str) -> tuple[str, str]:
    """层级 → (code 字段名, name 字段名)"""
    return ("l3_code", "l3_name") if str(level).upper() == "L3" else ("l2_code", "l2_name")


def _lookup_industry(ts_code: str, level: str = INDUSTRY_LEVEL) -> tuple[str, str]:
    """查该股的 SW2021 行业 → (行业代码, 行业名), 查不到返回 ("", "")

    Args:
        level: "L3" (三级, 默认) / "L2" (二级)

    数据源: index_member_all 落的 industry_member.parquet (唯一真源)
    """
    try:
        from tools.storage.store import DataStore
        info = DataStore.get_industry_info(ts_code)
        code_key, name_key = _level_keys(level)
        return info.get(code_key, ""), info.get(name_key, "")
    except Exception:
        return "", ""


def _industry_members(code: str, name: str = "", level: str = INDUSTRY_LEVEL) -> list[str]:
    """该行业的全部成员 ts_code

    2026-11-11 改: 行业口径统一到 **SW2021** (申万新版 134 个二级行业)
      - 同行组来自 index_member_all 落的 industry_member.parquet (唯一真源)
      - 之前用 stock_basic.industry = 申万**旧版** 110 个 → 「电气设备」348 家
        把光伏/电机/电网设备混一锅, 涨幅排名被稀释 (光伏设备实为 75 家)

    2026-11-12 改: 删掉申万旧版回退 —— stock_basic.industry 已删 (口径已废),
      成分表没覆盖就是没覆盖, 不拿旧口径凑 (凑出来的组是错的)。
      同时加 level 参数: L3 (三级, 默认) / L2 (二级)。

    Args:
        code:  行业代码 (L3 = 850781.SI 机器人 / L2 = 801735.SI)
        name:  兼容旧调用 (不再使用)
        level: "L3" (默认) / "L2"
    """
    if not code:
        return []
    from tools.storage.store import DataStore
    if str(level).upper() == "L3":
        return DataStore.get_industry_members_by_l3(code)
    return DataStore.get_industry_members_by_l2(code)


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


def compute_industry_market_cap_ranks(codes: list[str],
                                      trading_days: int = 120,
                                      level: str = INDUSTRY_LEVEL) -> dict[str, dict]:
    """一批票的行业内**市值排名** (批量版, 供 /t-wyckoff-a2m 分档用)

    跟 compute_industry_position **同口径** (同窗口 / 同市值源 / 同分位算法 / 同层级),
    区别只在效率: **按行业只算一次** —— 同行业的票共用一次市值快照, 不重复算。
    (per-stock 版每只票都重算一遍全行业排名, 30 只命中跨 20 个行业 = 20 倍浪费)

    Args:
        codes:       6 位股票代码列表
        trading_days: 窗口交易日数 (默认 120 = 跟报告 🏆行业地位 同窗口 → 同口径)
        level:       行业层级 "L3" (默认) / "L2"

    Returns:
        {code: {industry, market_value_rank, market_value_rank_total,
                market_value_percentile}}
        不在成分表 / 窗口末日没市值数据 → 该 code **不在返回里** (调用方当"判定不了")
    """
    if not codes:
        return {}

    import pandas as pd

    # 1. code → 行业代码 (SW2021 成分表唯一真源)
    by_ind: dict[str, tuple[str, list[str]]] = {}
    for c in codes:
        ind_code, ind_name = _lookup_industry(c, level)
        if ind_code:
            by_ind.setdefault(ind_code, (ind_name, []))[1].append(c)
    if not by_ind:
        return {}

    # 2. 窗口首末日 (全局一次)
    start_date, end_date = _complete_window(trading_days)
    if not end_date:
        return {}

    # 3. 所有涉及行业的成员 → 一次 SQL 拿市值快照
    all_members: set[str] = set()
    for ind_code in by_ind:
        all_members.update(_industry_members(ind_code, level=level))
    if not all_members:
        return {}
    rows = _fetch_member_snapshot(sorted(all_members), start_date, end_date)
    if not rows:
        return {}
    df = pd.DataFrame(rows)

    # 4. 每个行业按窗口末日市值排序 → rank (停牌/退市自然出局)
    out: dict[str, dict] = {}
    end_all = df[df["trade_date"] == end_date]
    for ind_code, (ind_name, group) in by_ind.items():
        members = set(_industry_members(ind_code, level=level))
        sub = end_all[end_all["ts_code"].isin(members)].sort_values(
            "total_mv", ascending=False)
        if sub.empty:
            continue
        peer_total = len(sub)
        rank_map = {ts: i for i, ts in enumerate(sub["ts_code"].tolist(), 1)}
        for c in group:
            ts = _to_ts_code(c)
            rank = rank_map.get(ts)
            if not rank:
                continue
            out[c] = {
                "industry": ind_name,
                "industry_level": str(level).upper(),
                "market_value_rank": rank,
                "market_value_rank_total": peer_total,
                "market_value_percentile": rank / peer_total * 100,
            }
    return out


def compute_industry_position(code: str, industry: str | None = None,
                              trading_days: int = 120,
                              level: str = INDUSTRY_LEVEL) -> dict:
    """算单只票的行业内地位 (龙头判定)

    Args:
        code:        6 位股票代码
        industry:    行业名 (兼容旧调用, **不再用作回退** —— 行业一律以 SW2021 成分表为准);
                     None → 自动查
        trading_days: 区间交易日数 (默认 120 = 跟 /t-analyze 因子历史窗口一致)
        level:       行业层级 "L3" (三级, 默认) / "L2" (二级)

    Returns:
        dict, 字段见下方; 无行业/无数据时返回 {} (调用方渲染成 "—")

    2026-11-11: 行业口径统一到 SW2021 (申万新版 134 个二级行业)
      - 行业归属 + 同行分组都走 index_member_all 落的 industry_member.parquet
    2026-11-12: industry 参数的回退**也删了** —— stock_basic.industry 已删,
      成分表没覆盖 → 返 {} (不出行业排名), 不拿旧口径凑
    2026-11-12: 默认层级改 **L3** (用户要求「股票显示 L3 的板块」),
      同时把父级链 (L2 名 / L1 名) 带出来给渲染层显示。
    """
    import pandas as pd

    ts_code = _to_ts_code(code)

    # SW2021 行业归属 (唯一真源)
    ind_code, ind_name = _lookup_industry(ts_code, level)
    if not ind_code:
        return {}

    members = _industry_members(ind_code, ind_name, level=level)
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

    # ---- 父级链 (给渲染层显示 "机器人 · 自动化设备 · 机械设备") ----
    l2_name = l1_name = ""
    try:
        from tools.storage.store import DataStore
        info = DataStore.get_industry_info(ts_code)
        l2_name = str(info.get("l2_name") or "")
        l1_name = str(info.get("l1_name") or "")
    except Exception:
        pass

    return {
        "industry": ind_name or industry or "",
        "industry_level": str(level).upper(),
        "parent_industry": l2_name,
        "top_industry": l1_name,
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


def compute_industry_leader_board(l2_code: str, trading_days: int = 120,
                                  top_n: int = 10) -> dict:
    """某个二级行业的**成分股龙头榜** (市值前 N + 涨幅前 N)

    2026-11-12 加: 给 /t-analyze --sw 的行业版报告用 (个股版只有"我这只票在行业里排第几",
    行业版要看"这个行业里谁是大票、谁在涨")。

    ⚠️ 口径跟 compute_industry_position **完全一致**:
       - 市值排名: 窗口**末日** total_mv 降序 (停牌/退市自然出局)
       - 区间涨幅: total_mv 比值 (股东实际收益, 送转不失真)
       - 窗口: _complete_window(trading_days) 挑覆盖完整的交易日 (别用 max(trade_date),
         半拉日会让分母缩水 → 排名虚高)

    Args:
        l2_code:     二级行业代码 (801012.SI)
        trading_days: 区间交易日数 (默认 120)
        top_n:       每张榜取前 N (默认 10)

    Returns:
        {l2_code, l2_name, peer_total, start_date, end_date, trading_days,
         mv_top:  [{ts_code, code, name, market_value_yi, market_value_rank,
                    market_value_percentile, gain_pct, gain_rank, gain_percentile}],
         gain_top: [同上, 按涨幅排]}
    """
    import pandas as pd

    if not l2_code:
        return {}

    l2_name = ""
    try:
        from tools.storage.store import DataStore
        l2_name = DataStore.get_industry_level_map().get(l2_code, {}).get("l2_name", "")
    except Exception:
        pass
    members = _industry_members(l2_code, l2_name, level="L2")
    if not members:
        return {}

    start_date, end_date = _complete_window(trading_days)
    if not end_date:
        return {}
    rows = _fetch_member_snapshot(members, start_date, end_date)
    if not rows:
        return {}

    df = pd.DataFrame(rows)
    end_df = df[df["trade_date"] == end_date].sort_values("total_mv", ascending=False).copy()
    if end_df.empty:
        return {}
    end_df["mv_rank"] = range(1, len(end_df) + 1)
    peer_total = len(end_df)

    start_df = df[df["trade_date"] == start_date].set_index("ts_code")
    end_idx = end_df.set_index("ts_code")

    # 区间涨幅 (total_mv 比值)
    gains: dict[str, float] = {}
    for c in end_idx.index:
        if c in start_df.index:
            s_mv = float(start_df.loc[c, "total_mv"])
            e_mv = float(end_idx.loc[c, "total_mv"])
            if s_mv:
                gains[c] = (e_mv / s_mv - 1) * 100
    gain_sorted = sorted(gains.items(), key=lambda x: x[1], reverse=True)
    gain_rank_map = {c: i for i, (c, _) in enumerate(gain_sorted, 1)}

    # 成分股名字 (一次性取全表, 别逐只查)
    name_map: dict[str, str] = {}
    try:
        from tools.storage.store import DataStore
        for rec in DataStore.load_stock_basic().to_dict("records"):
            ts = str(rec.get("ts_code") or "")
            if ts:
                name_map[ts] = str(rec.get("name") or "")
    except Exception:
        pass

    def _row(ts: str) -> dict:
        code6 = ts.split(".")[0]
        mv_rank = int(end_idx.loc[ts, "mv_rank"])
        gr = gain_rank_map.get(ts)
        return {
            "ts_code": ts,
            "code": code6,
            "name": name_map.get(ts) or code6,
            "market_value_yi": float(end_idx.loc[ts, "total_mv"]) / 10000.0,
            "market_value_rank": mv_rank,
            "market_value_rank_total": peer_total,
            "market_value_percentile": mv_rank / peer_total * 100,
            "gain_pct": gains.get(ts),
            "gain_rank": gr,
            "gain_rank_total": len(gains),
            "gain_percentile": (gr / len(gains) * 100) if (gr and gains) else None,
        }

    mv_top = [_row(ts) for ts in end_df["ts_code"].tolist()[:top_n]]
    gain_top = [_row(ts) for ts, _ in gain_sorted[:top_n]]

    return {
        "l2_code": l2_code,
        "l2_name": l2_name,
        "peer_total": peer_total,
        "start_date": start_date,
        "end_date": end_date,
        "trading_days": trading_days,
        "mv_top": mv_top,
        "gain_top": gain_top,
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
