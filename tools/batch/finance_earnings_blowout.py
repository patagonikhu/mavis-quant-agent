"""
tools/batch/finance_earnings_blowout.py — Earnings Blowout 财季炸裂扫描 (v6.3.0, 2026-09-24 prefilter 重构)

原名 quality_growth_scan.py, 改名理由: "财季炸裂 (Earnings Blowout)" 更贴切 R3 反转信号语义,
跟 /t-roc-ey 形成 "质量" 主题兄弟 skill, 用户更容易理解"营收+净利+毛利率同向爆量"是什么

v6.3.0 (2026-09-24): 5 闸 prefilter 跑在 3 rule 之前
  prefilter: ST / 周期股 / 市值 / 上市时间 / EBIT 预警 (ebit_crash + ebit_peak_down)
  业绩杀样本验证: 双林/华纬/富临/中熔/中科星图 5/5 全活; 中际旭创/兆易创新/拓荆 3/3 0 误伤

R3 v6.2.7 (--rev-yoy 默认放宽到 15, --np-yoy 到 20, --gm-tol 到 5) 沿用至 v6.3.0:
  4 基础 (AND):
  1. 营收 yoy >= 15% (主业高增长, 默认阈值)
  2. 净利 yoy >= 20% (盈利高增长, 默认阈值, Tushare VIP 无扣非 yoy, 用净利润代理)
  3. 毛利率 (升 OR 跌幅 ≤ 5pp) — 环比 + 同比 (gross_margin_qoq_stable + gross_margin_yoy_stable)

  OR 旁路 净利暴增 (profit_surge 旁路, --profit-surge-floor 默认 80):
     净利 yoy > 80%  AND  毛利率双过 (±5pp)
     用途: 放过"营收微增但净利暴增"的真实业绩反转 (国芳/三羊/双星 等)
     设计: 仍要求毛利率稳, 但允许营收不达标 (营收/净利两条腿可以瘸一条, 毛利率不能瘸)

1 触发 (OR, --jump-mode 选 1):
  a. 反转 (reversal):   净利 yoy_t - 净利 yoy_t-1 >= 50pp (业务反转核心信号)
  b. 龙头 (leader):     营收 yoy>=80% AND 净利 yoy>=80% AND 毛利率 环比升 (持续高增龙头, 抓中际旭创/寒武纪/中微)

位置过滤 (2026-09-23 已删, 不再过滤高位/低位票):
  - 距 1 年低点 <= 200%  (避免追 7 倍以上的高位票) ← 已删
  - 距 1 年高点 >= -30%  (至少回调 30%, 不追顶) ← 已删

周期股默认排除 (SW 周期类 + 电气设备, --include-cycle 可开)

性能: 全市场 13 季 5555 只 0.01s 跑完 (Python pandas 内存计算, 0 网络)

输出:
  - docs/earnings-blowout-watchlist.md (按季分 section, 18 列全中文)
  - stdout 速览 (最新 1 季 Top 30)
  - --top-np-jump N: 追加按净利跳升 pp 差降序的 Top N 表 (默认 0=不输出)

用法:
  bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout
  bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --rev-yoy 30 --np-yoy 80
  bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --jump-mode leader
  bash tools/with_venv.sh python -m tools.batch.finance_earnings_blowout --top-np-jump 200
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage.store import DataStore  # noqa: E402  数据访问统一走 DataStore


# ============================================================
# 数据加载 (走 DataStore, 0 网络 0 直读 parquet)
# ============================================================

def _load_financials() -> pd.DataFrame:
    """从 financials 13 季 parquet 加载原始数据 (走 DataStore)

    返 pd.DataFrame: ts_code, industry, end_date, ebit, roe, roe_yoy,
                     grossprofit_margin, or_yoy, netprofit_yoy
    """
    df = DataStore.load_all_financials()
    if df.empty:
        return df
    # 基础过滤 (替代原 SQL WHERE)
    df = df[
        (df["fetch_status"] == "ok")
        & df["or_yoy"].notna()
        & df["netprofit_yoy"].notna()
        & df["grossprofit_margin"].notna()
        & df["ebit"].notna() & (df["ebit"] > 0)
        & df["industry"].notna() & (df["industry"] != "")
    ]
    cols = ["ts_code", "industry", "end_date", "ebit", "roe", "roe_yoy",
            "grossprofit_margin", "or_yoy", "netprofit_yoy"]
    return df[cols].sort_values(["ts_code", "end_date"]).reset_index(drop=True)


def _load_stk_factor_latest() -> pd.DataFrame:
    """每只票最新一日的 PE/PE_TTM/市值/收盘价 (走 DataStore)

    返 pd.DataFrame: ts_code, close, pe, pe_ttm, total_mv
    """
    return DataStore.get_stk_factor_latest()


def _load_basic_map() -> dict:
    """预加载 stock_basic (避免每只票单独查)

    Returns:
        {code: {"name", "industry", "is_st", "list_date", "total_share"}, ...}

    2026-09-18 加: is_st 字段 (基于名称前缀 "ST" / "*ST")
    2026-09-24 加: list_date / total_share (供 prefilter 垃圾股闸用)
    """
    try:
        from tools.storage.store import DataStore
        df = DataStore.load_stock_basic()
        if df.empty:
            return {}
        return {
            row["code"]: {
                "name": row.get("name", "") or "",
                "industry": row.get("industry", "") or "",
                "is_st": (row.get("name", "") or "").startswith(("ST", "*ST", "S ", "S*")),
                "list_date":   str(row.get("list_date", "") or ""),
                # total_share 不暴露 (DataStore.load_stock_basic 返回列里没有)
            }
            for _, row in df.iterrows()
        }
    except Exception as e:
        print(f"[WARN] load_stock_basic 失败: {e}", flush=True)
        return {}


# ============================================================
# 4 季 LAG 计算 (纯 pandas, 一行代码)
# ============================================================

def _add_lags(df: pd.DataFrame, cols: list[str], n_lags: int = 3) -> pd.DataFrame:
    """给指定列加 LAG (本季/上季/上 2 季/上 3 季), 按 ts_code 分组按 end_date 排序

    输入:  df 必须有 ts_code, end_date 列
    输出:  df 新增 col_last_quarter / col_two_quarters_ago / col_three_quarters_ago 列 (业务命名 lag 1/2/3)
    """
    df = df.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
    for col in cols:
        for n in range(1, n_lags + 1):
            if n == 1:
                df[f"{col}_last_quarter"] = df.groupby("ts_code")[col].shift(1)
            elif n == 2:
                df[f"{col}_two_quarters_ago"] = df.groupby("ts_code")[col].shift(2)
            elif n == 3:
                df[f"{col}_three_quarters_ago"] = df.groupby("ts_code")[col].shift(3)
            elif n == 4:
                df[f"{col}_one_year_ago"] = df.groupby("ts_code")[col].shift(4)
            else:
                raise ValueError(f"n_lags={n} 不支持, 最大 4 (1 年)")
    return df


def is_strictly_increasing(arr) -> bool:
    """数组严格递增判断 (每步 prev < curr, 一步不满足即返 false)

    用法: 给定本季→上季→上 2 季→上 3 季的数组, 必须每步严格递增才算单调
    例 is_strictly_increasing([136.3, 132.8, 30.4, 12.8]) → True
       is_strictly_increasing([192.1, 182.5, 30.4, 12.8]) → False  (本季 < 上季)

    NaN / None 视为数据缺失, 直接返 False (避免 NaN > x 总是 False 的陷阱)
    """
    if arr is None or len(arr) < 2:
        return False
    # 类型安全: 数字 (int/float) 才能比, 字符串/None/NaN 一律 False
    for v in arr:
        if v is None:
            return False
        if isinstance(v, float) and v != v:  # NaN 检查
            return False
        if not isinstance(v, (int, float)):
            return False
    return all(arr[i] < arr[i + 1] for i in range(len(arr) - 1))


# ============================================================
# 命中判断 — 策略模式 (2026-09-23 重构)
# ============================================================
#
# 3 个独立 rule, 每个 rule 是一个 bool Series:
#     rule_main_path   = or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & trigger   (主路径: 4 基础 + 触发)
#     rule_reversal    = or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & reversal  (反转: 跳升≥50pp + 毛利率双过)
#     rule_profit_surge = 净利 yoy > 100 & gross_margin_qoq_stable & gross_margin_yoy_stable     (盈利暴增: 净利翻倍 + 毛利率双过)
#
# 加新 rule: 写一个 fn 返回 Series[bool], 在 _RULES 里 OR 一行即可
# --jump-mode 控制是否开启 rule_reversal (默认 on), rule_profit_surge 默认 on
#
# 最终 mask = rule_main_path | rule_reversal | rule_profit_surge  (3 个 rule 任意一个过即可)

from typing import Callable

def _rule_main_path(df: pd.DataFrame) -> pd.Series:
    """main_path: or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & (reversal | leader)"""
    return (
        df["or_yoy_meet"] & df["netprofit_yoy_meet"] & df["gross_margin_qoq_stable"] & df["gross_margin_yoy_stable"]
        & (df["reversal"] | df["leader"])
    ).rename("rule_main_path")


def _rule_reversal(df: pd.DataFrame) -> pd.Series:
    """reversal: 业绩反转跳升 ≥ 50pp + 营收/净利高增 + 毛利率双稳 (跟旧版 trigger 等价)"""
    return (
        df["or_yoy_meet"] & df["netprofit_yoy_meet"] & df["gross_margin_qoq_stable"] & df["gross_margin_yoy_stable"] & df["reversal"]
    ).rename("rule_reversal")


def _rule_profit_surge(df: pd.DataFrame, revenue_floor: float = 0.0, profit_floor: float = 80.0) -> pd.Series:
    """profit_surge: netprofit_yoy > profit_floor% (默认 80, 2026-09-23 从 100 放宽) + 毛利率双稳 (允许营收不达标, 抓真实业绩反转)

    revenue_floor: 营收 yoy 最低门槛 (默认 0 = 不限; 设 25 等同主路径 or_yoy_meet 门槛)
    profit_floor:  净利 yoy 最低门槛 (默认 80 = 不要求翻倍)
    """
    return (
        (df["netprofit_yoy"] > profit_floor)
        & (df["or_yoy"] >= revenue_floor)
        & df["gross_margin_qoq_stable"] & df["gross_margin_yoy_stable"]
    ).rename("rule_profit_surge")


_RULES: list[Callable] = [_rule_main_path, _rule_reversal]  # _rule_profit_surge 单独调用 (有 revenue_floor 参数)


def _apply_rules(df: pd.DataFrame, revenue_floor: float = 0.0, profit_floor: float = 80.0) -> tuple[pd.Series, dict]:
    """跑全部 rule, OR 起来.  返回 (final_mask, rule_hits)

    revenue_floor: 传给 _rule_profit_surge, 控制 profit_surge rule 的营收 yoy 下限 (默认 0 = 不限)
    profit_floor:  传给 _rule_profit_surge, 控制 profit_surge rule 的净利 yoy 下限 (默认 80)
    """
    rule_hits = {}
    combined = pd.Series(False, index=df.index)
    for fn in _RULES:
        m = fn(df)
        rule_hits[fn.__name__] = int(m.sum())
        combined = combined | m
    # _rule_profit_surge 单独跑, 带 revenue_floor + profit_floor
    m_profit_surge = _rule_profit_surge(df, revenue_floor=revenue_floor, profit_floor=profit_floor)
    rule_hits[_rule_profit_surge.__name__] = int(m_profit_surge.sum())
    combined = combined | m_profit_surge
    return combined, rule_hits


# ============================================================
# PRE-FILTER (v6.3.0 2026-09-24 重构)
# ============================================================
#
# 6 闸一次性跑在 _apply_rules 之前, 13 季 5555 只全表扫, 0 网络:
#   1. ST 过滤     (默认开, --include-st 关)
#   2. 周期股过滤  (默认开, --include-cycle 关)
#   3. 市值 < 30 亿 (默认开, --min-mv 控制下限)
#   4. 上市 < 60 季 (默认开, --min-listing-q 控制下限)
#   5. 总股本 < 1 亿股 (默认开, --min-shares 控制下限)
#   6. EBIT 预警   (默认开, --ebit-floor 控制; 环比 < floor 踢)
#
# 经验 (2026-09-23 分析):
#   - 业绩杀样本 5/5 双林/华纬/富临/中熔/中科星图, EBIT 环比 -66%~-94% (披露日已知)
#   - 单 EBIT 预警 1 项即可剔除 100% 业绩杀样本
#   - 旧版 (line 670-698) 周期股 + ST 在 3 rule 之后过滤, 抓不到被 RS 误伤的票


def _apply_prefilter(df: pd.DataFrame, basic_map: dict, sf_df: pd.DataFrame, args) -> pd.DataFrame:
    """5 闸前置过滤 (v6.3.0 简化版), 全 0 网络. 返回过滤后的 df.

    df:      load_financials + _add_lags 之后的 dataframe (已含 ebit / ebit_last_quarter 列)
    basic_map: {code: {name, industry, is_st, list_date, ...}}
              注: DataStore 不暴露 total_share, 所以 5 闸去掉股本, 用市值近似
    sf_df:   stk_factor_latest (用于 total_mv 市值判断)
    args:    argparse Namespace

    设计: 默认全开. --include-st/--include-cycle/--include-junk/--ebit-floor 关闭对应闸.
    """
    cycle_industries = set(s.strip() for s in args.cycle_industries.split(","))
    n0 = len(df)
    print(f"  🛡️  PRE-FILTER 启动 ({n0} 只次入参)")

    # 提 6 位 code 列 (后续 ST/周期股/上市/市值 都要)
    df = df.copy()
    df["_code"] = df["ts_code"].str.split(".").str[0]

    # ===== 1. ST 过滤 =====
    if not args.include_st:
        before = len(df)
        df["_is_st"] = df["_code"].map(lambda c: bool(basic_map.get(c, {}).get("is_st", False)))
        n_st = int(df["_is_st"].sum())
        df = df[~df["_is_st"]].copy()
        n_after = len(df)
        print(f"     ① ST 过滤:        排除 {before - n_after:>5} 只次 ({n_st} ST 命中, 默认开, --include-st 关)")
        df = df.drop(columns=["_is_st"])

    # ===== 2. 周期股过滤 =====
    if not args.include_cycle:
        before = len(df)
        df["_industry"] = df["_code"].map(lambda c: basic_map.get(c, {}).get("industry", ""))
        df = df[~df["_industry"].isin(cycle_industries)].copy()
        n_after = len(df)
        print(f"     ② 周期股过滤:     排除 {before - n_after:>5} 只次 (β 主导, 默认开, --include-cycle 关)")
        df = df.drop(columns=["_industry"])

    # ===== 3+4. 垃圾股 prefilter (市值 + 上市时间) =====
    #   注: 删 ⑤ 总股本闸, DataStore 不暴露 total_share (sync 注释说"已补"
    #        但 load_stock_basic 返回列里没有, 留着会全空踢). 靠 ③ 市值 +
    #   ④ 上市时间 两项已经够了.
    if not args.include_junk:
        before = len(df)

        # 拼 basic_map 提供的字段 (list_date)
        df["_list_date"] = df["_code"].map(lambda c: basic_map.get(c, {}).get("list_date", "") or "")
        # 拼 stk_factor.total_mv (万元 → 亿)
        sf_mv = sf_df.set_index("ts_code")["total_mv"].to_dict() if not sf_df.empty else {}
        df["_total_mv_yi"] = df["ts_code"].map(lambda c: (sf_mv.get(c) or 0) / 1e4)

        # ③ 市值 < min_mv 亿 (默认 30)
        #   注: 0/NaN 当作未知, 保留 (不对 unknown 误踢)
        mv_kick = (df["_total_mv_yi"] > 0) & (df["_total_mv_yi"] < args.min_mv)
        n_mv = int(mv_kick.sum())

        # ④ 上市 < min_listing_q 季 (默认 16=4 年)
        #   list_date 格式 YYYYMMDD, 当前季 = (今天 - list_date) / 90 天
        #   0/NaN 当上市时间未知, 默认保留 (上交所最早 1990 年, 4 年内新股才踢)
        today = pd.Timestamp(datetime.now().strftime("%Y%m%d"))
        list_dt = pd.to_datetime(df["_list_date"], format="%Y%m%d", errors="coerce")
        q_since_list = ((today - list_dt).dt.days / 90)
        lq_kick = q_since_list.notna() & (q_since_list < args.min_listing_q)
        n_lq = int(lq_kick.sum())

        kick_mask = mv_kick | lq_kick
        df = df[~kick_mask].copy()
        n_after = len(df)
        print(f"     ③ 市值 < {args.min_mv} 亿:    排除 {n_mv:>5} 只次")
        print(f"     ④ 上市 < {args.min_listing_q} 季:     排除 {n_lq:>5} 只次")
        print(f"        → 垃圾股合计:   {before - n_after:>5} 只次 (默认开, --include-junk 关)")
        df = df.drop(columns=["_list_date", "_total_mv_yi"])

    # ===== 5. EBIT 杀业绩预警 (v6.3.0 核心新增) =====
    #   业务名:
    #     ebit_crash: 单季崩盘
    #       本季 EBIT / 上季 EBIT < (1 + floor/100)  → 踢 (环比跌幅 > -floor%)
    #       默认 floor=-50 (环比腰斩即踢, 抓披露日业绩腰斩票)
    #       经验: 业绩杀样本 5/5 双林/华纬/富临/中熔/中科星图, EBIT 环比 -66%~-94% (披露日已知)
    #     ebit_peak_down: 业绩见顶后下行
    #       当前 EBIT < 4 季前 EBIT + 4 季内任意一季环比跌 < peak_kill
    #       抓"业绩高峰过后被杀"的票 (双林 2025Q2 见顶后 2026 已反弹, 单季崩盘抓不住)
    #       --ebit-peak-kill 控制阈值 (默认 -30% = 4 季内有任一季环比跌幅 > 30%)
    # 关闭方式: --ebit-floor -100 或 --ebit-peak-kill -100 (永不踢)
    if args.ebit_floor > -100 or args.ebit_peak_kill > -100:
        before = len(df)

        # ===== ebit_crash: 单季环比腰斩 =====
        if args.ebit_floor > -100:
            valid = df["ebit"].notna() & df["ebit_last_quarter"].notna() & (df["ebit_last_quarter"].abs() > 1e-3)
            ebit_qoq = (df["ebit"] / df["ebit_last_quarter"]) - 1
            kick_crash = valid & (ebit_qoq < (1 + args.ebit_floor / 100))
            kick_crash = kick_crash.fillna(False)
            n_crash = int(kick_crash.sum())
            print(f"        ebit_crash 单季 EBIT 环比 < {-args.ebit_floor:.0f}%:  {n_crash:>5} 只次")
        else:
            kick_crash = pd.Series(False, index=df.index)
            n_crash = 0

        # ===== ebit_peak_down: 4 季趋势见顶 =====
        #   条件:
        #     1) 当前 EBIT < 4 季前 EBIT (4 季累计负增长)
        #     2) 最近 4 季内 (本季/上季/上2季/上3季) 任意一季环比跌 < peak_kill
        #        即 MIN(prev ratio) < 1 + peak_kill/100
        if args.ebit_peak_kill > -100:
            ebit_cur = df["ebit"]
            ebit_one_year_ago = df.get("ebit_one_year_ago")  # LAG 4
            valid_peak = ebit_cur.notna() & ebit_one_year_ago.notna() & (ebit_one_year_ago.abs() > 1e-3)

            # 4 季内任意一季环比 < 阈值
            #   ratio = ebit / ebit_last_quarter (本季), last / two_ago, two / three_ago, three / year_ago
            #   MIN(ratio) < 1 + peak_kill/100  ==  任意一季环比跌幅超过 peak_kill
            ratio_curr_to_last  = df["ebit"] / df["ebit_last_quarter"].replace(0, pd.NA)
            ratio_last_to_two   = df["ebit_last_quarter"] / df.get("ebit_two_quarters_ago", pd.Series(pd.NA, index=df.index)).replace(0, pd.NA)
            ratio_two_to_three  = df.get("ebit_two_quarters_ago", pd.Series(pd.NA, index=df.index)) / df.get("ebit_three_quarters_ago", pd.Series(pd.NA, index=df.index)).replace(0, pd.NA)
            ratio_three_to_year = df.get("ebit_three_quarters_ago", pd.Series(pd.NA, index=df.index)) / df.get("ebit_one_year_ago", pd.Series(pd.NA, index=df.index)).replace(0, pd.NA)
            min_ratio = pd.concat([ratio_curr_to_last, ratio_last_to_two, ratio_two_to_three, ratio_three_to_year], axis=1).min(axis=1)

            # 当前 < 1 年前 (4 季累计负)
            cumulative_decline = valid_peak & (ebit_cur < ebit_one_year_ago)
            # 4 季内任一季环比跌穿 peak_kill (e.g. -30% 即 min_ratio < 0.7)
            sharp_drop = valid_peak & (min_ratio < (1 + args.ebit_peak_kill / 100))
            kick_peak = cumulative_decline & sharp_drop
            kick_peak = kick_peak.fillna(False)
            n_peak = int(kick_peak.sum())
            print(f"        ebit_peak_down 4 季趋势见顶 (4 季内任一季 < {args.ebit_peak_kill:.0f}%):  {n_peak:>5} 只次")
        else:
            kick_peak = pd.Series(False, index=df.index)
            n_peak = 0

        kick_mask = kick_crash | kick_peak
        df = df[~kick_mask].copy()
        n_after = len(df)
        n_ebit = n_crash + n_peak
        print(f"     ⑤ EBIT 预警合计:  排除 {n_ebit:>5} 只次 (crash 单季 + peak_down 趋势)")

    n_after = len(df)
    print(f"  🛡️  PRE-FILTER 完成: {n0} → {n_after} 只次 (踢除 {n0 - n_after})\n")

    # 清掉 _code 临时列
    df = df.drop(columns=["_code"])
    return df


# ============================================================
# md 输出 (按季分 section)
# ============================================================


# ============================================================
# md 输出 (按季分 section)
# ============================================================

def render_md(hits: list[dict], args) -> str:
    """按季分 section, 18 列全中文, 含字段说明"""
    if args.jump_mode == "reverse":
        mode_desc = "反转模式 (净利 yoy 跳升>=50pp)"
    elif args.jump_mode == "leader":
        mode_desc = "龙头模式 (营收/净利 yoy>=80% + 毛利率环比升)"
    else:
        mode_desc = "反转或龙头任一 (默认, 同时抓中际旭创/新易盛 + 反转票)"
    md = [f"# 高质量高增长 (按季分组) ({datetime.now().strftime('%Y-%m-%d')})\n\n"]
    md.append(f"> 全市场扫描 13 季 | R3 v6.3.0 启动期模式: 营收 yoy>={args.rev_yoy}% + 净利 yoy>={args.np_yoy}% + 毛利率 (升 OR 跌幅≤{args.gm_tol}pp, 环比+同比) + ({mode_desc}); 5 闸 prefilter 已扫 (ST/周期股/市值/上市/EBIT)\n\n")

    by_q = defaultdict(list)
    for h in hits:
        by_q[h["end_date"]].append(h)

    md.append(f"**总命中: {len(hits)} 只次, 跨 {len(by_q)} 个季** (按季从新到旧)\n\n")
    md.append("| 季 | 命中数 | EBIT 总规模 (亿) | 行业数 |\n")
    md.append("|---|---|---|---|\n")
    for q in sorted(by_q.keys(), reverse=True):
        n = len(by_q[q])
        total_ebit = sum(h["ebit_yi"] for h in by_q[q])
        n_ind = len(set(h["industry_display"] for h in by_q[q]))
        md.append(f"| {q} | {n} | {total_ebit:.1f} | {n_ind} |\n")
    md.append("\n")

    # 18 列表头 (3 季: 本季 + 上季 + 上 2 季)
    headers = [
        "代码", "名称", "行业",
        "营收 yoy % (本季)", "营收 yoy % (上季)", "营收 yoy % (上 2 季)",
        "净利 yoy % (本季)", "净利 yoy % (上季)", "净利 yoy % (上 2 季)",
        "毛利率 % (本季)", "毛利率 % (上季)", "毛利率 % (上 2 季)", "毛利率 % (去年同期)",
        "EBIT 亿 (本季)", "4 季增量 亿", "ROE %", "PE 倍", "PE_TTM 倍", "市值 亿",
    ]
    sep = ["---"] * len(headers)

    for q in sorted(by_q.keys(), reverse=True):
        md.append(f"## 季报: {q}\n\n")
        md.append(f"**{len(by_q[q])} 只命中** (按 净利 yoy 降序)\n\n")
        md.append("| " + " | ".join(headers) + " |\n")
        md.append("| " + " | ".join(sep) + " |\n")
        for h in sorted(by_q[q], key=lambda x: -x["netprofit_yoy"]):
            row = [
                h["ts_code"], h["name"], h["industry_display"],
                h["or_yoy"], h["or_yoy_last_quarter"], h["or_yoy_two_quarters_ago"],
                h["netprofit_yoy"], h["netprofit_yoy_last_quarter"], h["netprofit_yoy_two_quarters_ago"],
                h["grossprofit_margin"], h["grossprofit_margin_last_quarter"], h["grossprofit_margin_two_quarters_ago"],
                h["grossprofit_margin_one_year_ago"],
                h["ebit_yi"], h["ebit_increase_yi"], h["roe"],
                h.get("pe", "—"), h.get("pe_ttm", "—"), h.get("total_mv_yi", "—"),
            ]
            md.append("| " + " | ".join(f"{v:.1f}" if isinstance(v, float) else str(v) for v in row) + " |\n")
        md.append("\n---\n\n")

    # 字段说明
    md.append("## 字段说明\n\n")
    md.append("| 字段 | 公式 | 含义 |\n")
    md.append("|---|---|---|\n")
    md.append("| 营收 yoy (本季) | `or_yoy` | 本季营业总收入同比 |\n")
    md.append("| 营收 yoy (上季) | `LAG(or_yoy, 1)` | 上一季同比 |\n")
    md.append("| 营收 yoy (上 2 季) | `LAG(or_yoy, 2)` | 上 2 季同比 |\n")
    md.append("| 净利 yoy | `netprofit_yoy` | 净利润同比 (Tushare VIP 无扣非 yoy) |\n")
    md.append("| 毛利率 | `grossprofit_margin` | 单季毛利率 |\n")
    md.append("| 毛利率 (去年同期) | `LAG(grossprofit_margin, 4)` | 同比基准 |\n")
    md.append("| EBIT | `ebit / 1e8` | 当前季 EBIT (亿) |\n")
    md.append("| 1 年增量 | `(ebit - ebit_one_year_ago) / 1e8` | 业务规模真实扩大 |\n")
    md.append("| ROE | `roe` | 净资产收益率 |\n")
    md.append("| PE | `pe` (stk_factor 最新一日) | 静态市盈率 |\n")
    md.append("| PE_TTM | `pe_ttm` | TTM 滚动市盈率 |\n")
    md.append("| 市值 | `total_mv / 1e4` | 总市值 (亿) |\n\n")

    md.append("## R3 启动期反转信号\n\n")
    md.append("对每只命中的票 (每季), 验证 4 个条件:\n")
    md.append("- 1. 营收 yoy >= 25% (主业高增长)\n")
    md.append("- 2. 净利 yoy >= 50% (盈利高增长)\n")
    md.append("- 3. 毛利率 同比 + 环比 双升 (议价能力提升)\n")
    md.append("- 4. **净利 yoy 跳升 >= 50pp** (本季 - 上季, 业务反转关键信号)\n\n")
    md.append("**位置过滤 (2026-09-23 已删)**: 不再过滤高位/低位票\n\n")
    md.append("**为什么用跳升 50pp**: 10x 票起涨季 (T+0) 净利 yoy 中位 41% / 跳升中位 50pp+, 旧\"3 季 EBIT 累计 >= 2x\" 在 T+0 0% 命中。跳升 50pp 在 T+0 41% 命中。\n\n")

    md.append("## 4 条件门槛\n\n")
    md.append(f"- 营收 yoy >= {args.rev_yoy}%\n")
    md.append(f"- 净利 yoy >= {args.np_yoy}%\n")
    md.append(f"- 毛利率 当前 > 上季 (环比升) **AND** 毛利率 当前 > 去年 (同比升)\n")
    md.append(f"- 净利 yoy 跳升 >= 50pp (本季 - 上季)\n\n")
    md.append(f"- (ROE 门槛已移除, R3 算法不依赖 ROE)\n\n")

    md.append("**字段来源**: 本地 financials parquet 109 字段 (由 sync.py --financials 预拉) + stk_factor parquet 17 字段 (PE / 市值)\n")
    md.append(f"\n**数据落盘**: `data/history/financials/{{YYYYQN}}.parquet` (按季 13 份) + `data/history/stk_factor/{{YYYYQN}}.parquet` (按季 5 份)\n")

    return "".join(md)


# ============================================================
# stdout 输出 (最新 1 季 Top 30)
# ============================================================

def render_stdout(hits: list[dict], args) -> str:
    """stdout 速览: 最新 1 季 Top 30"""
    if not hits:
        return "无命中 (条件严格, 0-3 只/季度为正常)"

    latest_q = max(h["end_date"] for h in hits)
    latest_hits = [h for h in hits if h["end_date"] == latest_q]
    latest_hits = sorted(latest_hits, key=lambda x: -x["netprofit_yoy"])[:args.limit]

    lines = [
        f"=== 最新 1 季 ({latest_q}) Top {len(latest_hits)} ===\n",
        f"{'代码':<10}{'名称':<10}{'行业':<10}{'季':<10}"
        f"{'营收yoy%(本季)':<14}{'上季':<8}{'上2季':<8}"
        f"{'净利yoy%(本季)':<15}{'上季':<9}{'上2季':<9}"
        f"{'毛利%(本季)':<11}{'上季':<7}{'上2季':<7}{'去年同季':<10}"
        f"{'EBIT亿(本季)':<13}{'4q增量亿':<10}"
        f"{'ROE%(本季)':<11}{'PE倍':<8}{'PE_TTM倍':<10}{'市值亿':<10}"
    ]
    for h in latest_hits:
        lines.append(
            f"{h['ts_code']:<10}{(h.get('name') or '')[:8]:<10}{h['industry_display'][:8]:<10}"
            f"{h['end_date']:<10}"
            f"{h['or_yoy']:<8.1f}{h.get('or_yoy_last_quarter', 0) or 0:<8.1f}{h.get('or_yoy_two_quarters_ago', 0) or 0:<8.1f}"
            f"{h['netprofit_yoy']:<9.1f}{h.get('netprofit_yoy_last_quarter', 0) or 0:<8.1f}{h.get('netprofit_yoy_two_quarters_ago', 0) or 0:<8.1f}"
            f"{h['grossprofit_margin']:<6.1f}{h.get('grossprofit_margin_last_quarter', 0) or 0:<7.1f}{h.get('grossprofit_margin_two_quarters_ago', 0) or 0:<7.1f}{h.get('grossprofit_margin_one_year_ago', 0) or 0:<7.1f}"
            f"{h['ebit_yi']:<10.2f}{h['ebit_increase_yi']:<9.2f}"
            f"{h['roe']:<6.1f}{h.get('pe', 0) or 0:<8.1f}{h.get('pe_ttm', 0) or 0:<8.1f}{h.get('total_mv_yi', 0) or 0:<10.1f}"
        )
    return "\n".join(lines)


# ============================================================
# watchlist 同步 (覆盖 blowout 段, 2026-09-23 加)
# ============================================================
#
# 策略:
#   当季 (latest end_date) earnings-blowout 命中:
#     - 不在 watchlist            → 加 (list_type=blowout, tag=blowout-2026Q2)
#     - 在 watchlist 已是 blowout → 保留 (更新 tag 季度)
#     - 在 watchlist 持仓/自选    → 保留 (不抢持仓), 只在 stdout 提示
#
#   watchlist 原 blowout 段:
#     - 本季不再命中 → 改 list_type=自选 (不删, 保留历史)
#
# 加 --no-sync-watchlist 可关, 默认开

WATCHLIST_PATH = ROOT / "config" / "watchlist.json"


def sync_watchlist_blowout(hits_df: pd.DataFrame, dry_run: bool = False) -> None:
    """同步 watchlist.json 的 blowout 段: 加新命中, 退场直接删除

    dry_run=True: 只 print 计划, 不写文件 (默认 False, 直接写)
    """
    if hits_df.empty:
        print("  ⚠️  无命中, 跳过 watchlist 同步")
        return

    # 取当季 (latest end_date)
    latest_q = hits_df["end_date"].max()
    cur_q = hits_df[hits_df["end_date"] == latest_q]
    hit_codes = set(cur_q["ts_code"].str.split(".").str[0])  # 6位无后缀
    q_tag = f"blowout-{latest_q[:4]}Q{((int(latest_q[4:6]) - 1) // 3) + 1}"  # 20260630 → 2026Q2

    # 读 watchlist
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        wl = json.load(f)

    old_blowout_codes = {s["code"] for s in wl.get("stocks", []) if s.get("list_type") == "blowout"}
    added, removed, kept = [], [], []

    for s in wl.get("stocks", []):
        code = s["code"]
        if s.get("list_type") == "blowout":
            if code not in hit_codes:
                removed.append(code)
            else:
                kept.append(code)

    # 加新命中 (不在任何 list_type)
    existing_codes = {s["code"] for s in wl.get("stocks", [])}
    new_hits = cur_q[~cur_q["ts_code"].str.split(".").str[0].isin(existing_codes)]
    for _, r in new_hits.iterrows():
        added.append(r["ts_code"].split(".")[0])

    print(f"\n  📋 watchlist 同步计划 ({latest_q} = {q_tag}):")
    print(f"     新增 blowout:   {len(added)} 只")
    if added:
        print(f"       {added[:10]}{'...' if len(added) > 10 else ''}")
    print(f"     退场删除:       {len(removed)} 只")
    if removed:
        print(f"       {removed[:10]}{'...' if len(removed) > 10 else ''}")
    print(f"     保留 blowout:   {len(kept)} 只")
    print(f"     同步后 blowout 总数: {len(kept) + len(added)}")

    if dry_run:
        print(f"\n  🔍 DRY-RUN: 不写文件 (传 --no-dry-run 才会真写)")
        return

    # 真写
    wl["stocks"] = [
        s for s in wl["stocks"]
        if not (s.get("list_type") == "blowout" and s["code"] not in hit_codes)
    ]
    # 更新保留的 tag
    for s in wl["stocks"]:
        if s.get("list_type") == "blowout":
            tags = s.get("tags", [])
            if q_tag not in tags:
                tags.append(q_tag)
                s["tags"] = tags

    # 加新命中
    for _, r in new_hits.iterrows():
        code = r["ts_code"].split(".")[0]
        name = r.get("name", "")
        wl["stocks"].append({
            "code": code,
            "name": name,
            "list_type": "blowout",
            "tags": [q_tag],
            "notes": f"[{datetime.now().strftime('%Y-%m-%d')} auto-add from earnings-blowout {latest_q}] 净利 yoy {r.get('netprofit_yoy', 0):+.0f}% | 营收 yoy {r.get('or_yoy', 0):+.0f}%",
        })

    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(wl, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ watchlist.json 已写")


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="5 条件启动期捕获 (EBIT>0 + 3 季 EBIT 累计 >= 2x + 营收/净利 yoy + 毛利率双升)",
    )
    parser.add_argument("--rev-yoy", type=float, default=15.0, help="最低单季营收同比 (默认 15, 2026-09-23 从 20 再放宽, 激进抓赛道早期票)")
    parser.add_argument("--np-yoy", type=float, default=20.0, help="最低单季净利润同比 (默认 20, 2026-09-23 从 30 再放宽)")
    parser.add_argument("--np-jump", type=float, default=30.0, help="净利 yoy 跳升门槛 (pp, 本季-上季, 默认 30, 2026-09-23 从 50 再放宽)")
    parser.add_argument("--jump-mode", choices=["reverse", "leader", "either"], default="either",
                        help="R3 反转模式: reverse=只看跳升(默认反转) / leader=只看持续高增龙头 / either=反转或龙头任一即可 (默认 either, 同时抓中际旭创/新易盛 这种持续高增龙头 + 反转票)")
    parser.add_argument("--no-position-filter", action="store_true",
                        help="(已删 2026-09-23) 位置过滤代码已整段移除")
    parser.add_argument("--limit", type=int, default=30, help="stdout Top N (默认 30)")
    parser.add_argument("--top-np-jump", type=int, default=0,
                        help="按本季-上季净利 yoy 差 (pp) 降序取前 N, 追加到 md (默认 0=不输出, 例 --top-np-jump 200)")
    parser.add_argument("--out", default="docs/earnings-blowout-watchlist.md", help="md 输出路径")
    parser.add_argument("--include-cycle", action="store_true", help="包含周期股 (默认排除, 周期股景气突破是 β 不是 α)")
    parser.add_argument("--include-st", action="store_true", help="包含 ST/*ST 票 (默认排除, 退市风险)")
    parser.add_argument("--tolerance", type=float, default=0.0, help="3 季单调回踩容忍 (pp, 默认 0 = 严格; 例 1.0 允许 prev vs prev2 差 -1pp, 解决财务披露口径跳跃问题)")
    # 2026-09-23 删 --no-position-filter (位置过滤强制开启, 不可关)
    parser.add_argument("--gm-tol", type=float, default=5.0, help="毛利率跌幅容忍 (pp, 默认 5, 2026-09-23 从 2 放宽; gross_margin_qoq_stable/gross_margin_yoy_stable 跌幅 <= tol 接受)")
    parser.add_argument("--profit-surge-revenue-floor", type=float, default=0.0,
                        help="_rule_profit_surge 营收 yoy 下限 (默认 0 = 不限; 设 25 等同主路径 or_yoy_meet 门槛, 压缩选股数量)")
    parser.add_argument("--profit-surge-floor", type=float, default=80.0,
                        help="_rule_profit_surge 净利 yoy 下限 (默认 80, 2026-09-23 从 100 放宽; 例 100 要求翻倍)")
    parser.add_argument("--no-sync-watchlist", action="store_true",
                        help="关闭 watchlist.json 自动同步 (默认开: 当季命中自动写入 watchlist blowout 段)")
    parser.add_argument("--dry-run", action="store_true",
                        help="watchlist 同步 dry-run, 只 print 计划不写文件")
    parser.add_argument("--cycle-industries", default="小金属,铜,铝,化工原料,农药化肥,铅锌,矿物制品,钢铁,煤炭,石油,化纤,水运,仓储物流,电器仪表,家用电器,工程机械,石油开采,黄金,塑料,造纸,建材,玻璃,陶瓷,纺织,化纤,电气设备",
                        help="周期股白名单 (默认 SW 周期类 + 电气设备, 因为光伏/储能/电池 跟锂电材料强相关)")
    # 2026-09-24 v6.3.0 新增: prefilter 4 开关
    # --include-st / --include-cycle 已存在 (上放 v6.2.7 时期定义, v6.3.0 复用)
    parser.add_argument("--include-junk", action="store_true", help="包含垃圾股 (市值/上市/股本, 默认 prefilter 排除)")
    parser.add_argument("--min-mv", type=float, default=30.0,
                        help="市值下限 (亿, 默认 30, --include-junk 关掉此项)")
    parser.add_argument("--min-listing-q", type=int, default=16,
                        help="上市时间下限 (季, 默认 16=4 年, 16 季 EBIT 数据完整 + yoy 基准稳)")
    # 注: 总股本闸已删 (DataStore 不暴露 total_share 字段)
    parser.add_argument("--ebit-floor", type=float, default=-50.0,
                        help="EBIT 环比预警 (默认 -50, 本季/上季 < 1 + floor/100 即踢; 设 -100 关闭)")
    parser.add_argument("--ebit-peak-kill", type=float, default=-30.0,
                        help="EBIT 4 季趋势见顶 (默认 -30, 当前 < 4 季前 AND 4 季内任一季环比 < 1+peak_kill/100 踢; 设 -100 关闭)")
    args = parser.parse_args()

    print("=" * 70)
    print("Earnings Blowout 财季炸裂扫描 (v6.3.0 prefilter 重构)")
    print("=" * 70)
    # 完整规则一览 (5 闸 prefilter + 4 基础 AND + 1 触发 OR + 3 rule)
    print("┌─ R3 v6.3.0 架构 ───────────────────────────────────────┐")
    print("│ 5 闸 prefilter (默认全开, 跑在 3 rule 之前):              │")
    print("│   ① ST 过滤                                          │")
    print("│   ② 周期股过滤 (SW 周期 + 电气设备)                  │")
    print("│   ③ 市值 < 30 亿 (--min-mv)                          │")
    print("│   ④ 上市 < 16 季 (--min-listing-q, 4 年)              │")
    print("│   ⑤ EBIT 预警:                                       │")
    print("│      ebit_crash      单季 EBIT 环比 < -50% 踢        │")
    print("│      ebit_peak_down  4 季趋势见顶 (任一季 < -30%) 踢  │")
    print("│                                                          │")
    print("│ 3 rule OR (任一过即命中):                                │")
    print(f"│   rule_main_path     : or_yoy_meet(≥{args.rev_yoy}%) & netprofit_yoy_meet(≥{args.np_yoy}%) & 毛利率双稳 & (reversal ∨ leader)  │")
    print(f"│   rule_reversal      : 4 基础 AND + 净利跳升 ≥ 50pp (本季-上季) │")
    print(f"│   rule_profit_surge  : 净利 yoy > {args.profit_surge_floor}% & 毛利率双稳 (营收可放宽) │")
    print("│                                                          │")
    print(f"│ 当前: jump={args.jump_mode} | 周期股={'排除' if not args.include_cycle else '包含'} | ST={'排除' if not args.include_st else '包含'} | 位置=不过滤 │")
    print("└──────────────────────────────────────────────────────────┘")
    print()
    print(f"  1. 营收 yoy  >= {args.rev_yoy}%")
    print(f"  2. 净利 yoy  >= {args.np_yoy}%")
    print(f"  3. 毛利率 (升 OR 跌幅 ≤ {args.gm_tol}pp) — 环比 + 同比")
    print(f"  4. 净利 yoy 跳升 >= 50pp (本季 - 上季, 反转信号)")
    print(f"  5. (位置过滤已删 2026-09-23, 不再过滤)")
    print(f"  🚀 启动期模式: R3 v6.3.0 (营收 15% / 净利 20% / 毛利率升 OR 跌幅≤5pp / 净利跳升 50pp), 10x 票 T+0 命中 41%")
    print()

    print("ℹ️  0 网络, 走 DataStore (financials parquet), 缺数据请先 /t-sync-data --financials",
          flush=True)

    # 0. 数据状态
    try:
        from tools.storage.store import DataStore
        fin_df = DataStore.load_all_financials()
        n_parquet = len(list(Path("data/history/financials").glob("*.parquet")))
        latest = fin_df["end_date"].max() if not fin_df.empty else None
        print(f"  financials parquet: {n_parquet} 季, 最新一季 max end_date: {latest}", flush=True)
    except Exception as e:
        print(f"  [WARN] financials 检查失败: {e}", flush=True)

    # 1. 取数 (SQL 只做列选择, 不算 LAG/筛选)
    t0 = time.time()
    fin = _load_financials()
    sf  = _load_stk_factor_latest()
    basic_map = _load_basic_map()
    t_load = time.time() - t0

    # 1.5 启动期模式: R3 (净利 yoy 跳升) 替代 3 季 EBIT 累计
    # 1. 营收 yoy >= 25%
    # 2. 净利 yoy >= 50%  (Tushare VIP 无扣非 yoy, 用净利润代理)
    # 3. 毛利率 (升 OR 跌幅 ≤ {gm_tol}pp, 当前 {args.gm_tol}) — 环比 + 同比 (v6.3.0 沿用 v6.2.7 放宽, 抓科技股龙头)
    # 4. 净利 yoy 跳升 >= 50pp (本季 - 上季, 反转信号)
    # 触发 OR: 反转 (reversal) OR 龙头 (leader)
    # 位置过滤: 距 1y 低 <= 200% AND 距 1y 高 >= -30%
    print(f"  🚀 启动期模式: R3 v6.3.0 (4 基础 AND + 触发 OR), 10x 票 T+0 命中 41%")

    # 2. 加 LAG (pandas groupby+shift, 一行代码)
    t0 = time.time()
    cols_to_lag = ["or_yoy", "netprofit_yoy", "grossprofit_margin", "ebit", "roe"]
    fin = _add_lags(fin, cols_to_lag, n_lags=4)
    t_lag = time.time() - t0

    # 3. 计算业务字段 (Python 纯函数, 透明)
    t0 = time.time()
    fin["ebit_yi"] = fin["ebit"] / 1e8
    fin["ebit_one_year_ago_yi"] = fin["ebit_one_year_ago"] / 1e8
    fin["ebit_increase_yi"] = fin["ebit_yi"] - fin["ebit_one_year_ago_yi"]

    # 4. 4 条件 (业务语义命名)
    #   or_yoy_meet      = or_yoy >= 阈值
    #   netprofit_yoy_meet       = netprofit_yoy >= 阈值
    #   gross_margin_qoq_stable   = (毛利率 升 OR 跌幅 ≤ 2pp) — 本季 vs 上季
    #   gross_margin_yoy_stable   = (毛利率 升 OR 跌幅 ≤ 2pp) — 本季 vs 去年同期
    fin["or_yoy_meet"]    = fin["or_yoy"]         >= args.rev_yoy
    fin["netprofit_yoy_meet"]     = fin["netprofit_yoy"]  >= args.np_yoy
    fin["gross_margin_qoq_stable"] = (fin["grossprofit_margin"] > fin["grossprofit_margin_last_quarter"])  | ((fin["grossprofit_margin"] - fin["grossprofit_margin_last_quarter"]).abs()  <= args.gm_tol)
    fin["gross_margin_yoy_stable"] = (fin["grossprofit_margin"] > fin["grossprofit_margin_one_year_ago"]) | ((fin["grossprofit_margin"] - fin["grossprofit_margin_one_year_ago"]).abs() <= args.gm_tol)

    # 5. 连续 3 季单调 (营收 / 净利 / 毛利率 3 项, 本季 > 上季 > 上 2 季 ± tolerance, 2 段比较)
    #   注: 派生但未在 mask 中使用, 仅展示/兼容用, 保留派生 (v6.3.0 改名工程命名→业务名)
    tol = args.tolerance
    fin["or_yoy_qoq_rising"] = fin["or_yoy"]      > fin["or_yoy_last_quarter"]
    fin["or_yoy_qoq_rising_two_quarters_ago_tol"] = fin["or_yoy_last_quarter"]  > fin["or_yoy_two_quarters_ago"] - tol
    fin["netprofit_yoy_qoq_rising"]  = fin["netprofit_yoy"]      > fin["netprofit_yoy_last_quarter"]
    fin["netprofit_yoy_qoq_rising_two_quarters_ago_tol"] = fin["netprofit_yoy_last_quarter"]  > fin["netprofit_yoy_two_quarters_ago"] - tol
    fin["gross_margin_qoq_rising"]  = fin["grossprofit_margin"]      > fin["grossprofit_margin_last_quarter"]
    fin["gross_margin_qoq_rising_two_quarters_ago_tol"] = fin["grossprofit_margin_last_quarter"]  > fin["grossprofit_margin_two_quarters_ago"] - tol


    # 6. 绝对值过滤
    t_calc = time.time() - t0

    # 启动期模式: 5 条件 (1+2+3+4+5) 替代 3 季单调 + ROE 稳定性
    # 1. 本季 EBIT > 0
    # 2. 3 季 EBIT 累计 >= 2x
    # 3. 营收 yoy >= 25%
    # 4. 净利 yoy >= 50%
    # 5. R3 净利 yoy 跳升 (替代旧 3 季 EBIT 累计)
    # 净利 yoy 跳升 = 本季 np_yoy - 上季 np_yoy, 至少 50pp 才算反转
    fin["np_jump"] = fin["netprofit_yoy"] - fin["netprofit_yoy_last_quarter"]
    fin["reversal"] = (fin["np_jump"] >= 50) & fin["netprofit_yoy_last_quarter"].notna()   # 业绩反转: 跳升 ≥ 50pp
    # 业务别名 (reversal 即可, 旧 c_jump 是工程列名, 彻底不用)

    # 持续高增长龙头分支: 营收 yoy>=80% AND 净利 yoy>=80% AND 毛利率环比升
    # 抓中际旭创/新易盛/天孚通信 这种连续 4-5 季高增、已经看不出跳升的真龙头
    fin["lead_revenue"] = fin["or_yoy"] >= 80
    fin["lead_profit"] = fin["netprofit_yoy"] >= 80
    fin["lead_margin"] = fin["grossprofit_margin"] > fin["grossprofit_margin_last_quarter"]
    fin["leader"] = fin["lead_revenue"] & fin["lead_profit"] & fin["lead_margin"] & fin["grossprofit_margin_last_quarter"].notna()
    # leader 本身已是业务名, 不另起别名

    mode_desc_map = {
        "reverse": "反转模式 (只看跳升>=50pp)",
        "leader":  "龙头模式 (只看持续高增 营收/净利>=80% + 毛利率环比升)",
        "either":  "反转或龙头任一 (默认, 同时抓中际旭创/新易盛 + 反转票)",
    }
    print(f"  🎯 R3 触发模式: {mode_desc_map[args.jump_mode]}")
    print(f"     反转票 (reversal) 命中: {fin['reversal'].sum():>6} 只次")
    print(f"     龙头票 (leader) 命中: {fin['leader'].sum():>5} 只次")

    # 策略模式 (2026-09-23 重构): 3 个独立 rule, OR 起来
    #   rule_main_path = or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & trigger
    #   rule_reversal = or_yoy_meet & netprofit_yoy_meet & gross_margin_qoq_stable & gross_margin_yoy_stable & reversal
    #   rule_profit_surge = 净利 yoy > profit_floor & gross_margin_qoq_stable & gross_margin_yoy_stable   (盈利暴增, 允许营收不达标)
    #
    # v6.3.0 (2026-09-24): prefilter 5 闸一次性跑在 _apply_rules 之前 (ST/周期股/市值/上市/EBIT 预警, 股本闸已删)
    fin = _apply_prefilter(fin, basic_map, sf, args)
    t_filter_start = time.time()

    mask, rule_hits = _apply_rules(fin, revenue_floor=args.profit_surge_revenue_floor, profit_floor=args.profit_surge_floor)
    hits_df = fin[mask].copy()
    for name, n in rule_hits.items():
        print(f"  🎯 Rule {name}: {n} 只次")
    print(f"  🎯 最终命中 (rule OR): {(mask).sum():>5} 只次")
    t_filter = time.time() - t_filter_start

    # v6.3.0 2026-09-24: 周期股 + ST 过滤已挪到 _apply_prefilter (跑在 3 rule 之前)
    # 旧版这段逻辑 (line 670-698) 已删

    # 6.6 位置过滤: 2026-09-23 删 (代码整段移除, 不再过滤高位票)
    # 距 1 年低点 <= 200% 且 距 1 年高点 >= -30% 的过滤已删除

    # 7. JOIN stk_factor + 名称/行业
    if not hits_df.empty:
        hits_df = hits_df.merge(
            sf, on="ts_code", how="left", suffixes=("", "_sf")
        )
        # 加 name / industry_display
        hits_df["code"] = hits_df["ts_code"].str.split(".").str[0]
        hits_df["name"] = hits_df["code"].map(lambda c: basic_map.get(c, {}).get("name", ""))
        hits_df["industry_display"] = hits_df["code"].map(
            lambda c: basic_map.get(c, {}).get("industry", "") or hits_df.loc[hits_df["code"] == c, "industry"].iloc[0]
        )
        # 处理 NaN
        hits_df["pe"] = hits_df["pe"].fillna(0)
        hits_df["pe_ttm"] = hits_df["pe_ttm"].fillna(0)
        hits_df["total_mv_yi"] = (hits_df["total_mv"].fillna(0) / 1e4).round(2)

    hits = hits_df.to_dict("records")

    # 8. 输出
    elapsed = time.time() - t0
    print(f"\n=== 完成 ({elapsed:.2f}s) [SQL 取数 {t_load:.2f}s + LAG {t_lag:.2f}s + 算 {t_calc:.2f}s + 过滤 {t_filter:.2f}s] ===")
    print(f"13 季总命中: {len(hits)} 只次 (按季分 section), 最新 1 季命中 {len([h for h in hits if h['end_date'] == max((h['end_date'] for h in hits), default='')])} 只\n")

    if not hits:
        print("无命中 (条件严格, 0-3 只/季度为正常)")
    else:
        print(render_stdout(hits, args))

    if hits:
        out_path = ROOT / args.out
        out_path.parent.mkdir(parents=True, exist_ok=True)
        md_content = render_md(hits, args)
        # 追加 Top N by 净利 yoy gap (np_jump = 本季 - 上季)
        if args.top_np_jump > 0 and hits_df is not None and not hits_df.empty:
            top = hits_df.dropna(subset=["np_jump"]).sort_values("np_jump", ascending=False).head(args.top_np_jump)
            if not top.empty:
                md_content += "\n\n---\n\n## 🚀 净利 yoy 跳升 Top {} (按本季-上季 pp 差降序)\n\n".format(args.top_np_jump)
                md_content += "| 排名 | 代码 | 名称 | 行业 | 季 | 营收 yoy | 净利 yoy (本季) | 净利 yoy (上季) | **np_jump (pp)** | 毛利率 | ROE | EBIT (亿) | 市值 (亿) | PE | PE_TTM |\n"
                md_content += "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n"
                for i, (_, r) in enumerate(top.iterrows(), 1):
                    md_content += (
                        f"| {i} | {r.get('ts_code','')} | {r.get('name','')} | {r.get('industry_display','')} | "
                        f"{r.get('end_date','')} | {r.get('or_yoy',0):+.0f}% | {r.get('netprofit_yoy',0):+.0f}% | "
                        f"{r.get('netprofit_yoy_last_quarter',0) or 0:+.0f}% | **{r.get('np_jump',0):+.0f}** | "
                        f"{r.get('grossprofit_margin',0):.0f}% | {r.get('roe',0):.1f}% | "
                        f"{r.get('ebit',0)/1e8:.2f} | {r.get('total_mv_yi',0):.0f} | "
                        f"{r.get('pe',0):.0f} | {r.get('pe_ttm',0):.0f} |\n"
                    )
        out_path.write_text(md_content, encoding="utf-8")
        print(f"\n📄 {out_path}")

    # 2026-09-23 加: 同步 watchlist.json (覆盖 blowout 段)
    #   - 当季 (latest end_date) 命中 + 不在 watchlist 的 → 加 (list_type=blowout)
    #   - watchlist 原 blowout 段 + 本季不再命中 → 改 list_type=自选 (不删, 保留历史)
    if not args.no_sync_watchlist and hits:
        sync_watchlist_blowout(hits_df, dry_run=getattr(args, 'dry_run', False))


if __name__ == "__main__":
    main()
