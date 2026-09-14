"""
tools/batch/earnings_blowout_scan.py — Earnings Blowout 财季炸裂扫描 (v6.2.7, 2026-09-09 改自 quality_growth_scan.py)

原名 quality_growth_scan.py, 改名理由: "财季炸裂 (Earnings Blowout)" 更贴切 R3 反转信号语义,
跟 /t-roc-ey 形成 "质量" 主题兄弟 skill, 用户更容易理解"营收+净利+毛利率同向爆量"是什么

R3 v6.2.7 4 基础 (AND):
  1. 营收 yoy >= 25%  (主业高增长)
  2. 净利 yoy >= 50%  (盈利高增长, Tushare VIP 无扣非 yoy, 用净利润代理)
  3. 毛利率 gap ≤ 2pp (环比)  — v6.2.7 改: 允许 ±2pp 波动, 抓科技股龙头
  4. 毛利率 gap ≤ 2pp (同比)  — v6.2.7 改: 允许 ±2pp 波动, 抓科技股龙头

R3 v6.2.7 1 触发 (OR, --jump-mode 选 1):
  a. 反转 (c_jump):   np_yoy_t - np_yoy_t-1 >= 50pp  (业务反转核心信号)
  b. 龙头 (c_leader): or_yoy >= 80% AND np_yoy >= 80% AND gm 环比升  (持续高增龙头, 抓中际旭创/寒武纪/中微)

位置过滤 (默认开启, --no-position-filter 关闭):
  - 距 1 年低点 <= 200%  (避免追 7 倍以上的高位票)
  - 距 1 年高点 >= -30%  (至少回调 30%, 不追顶)

周期股默认排除 (SW 周期类 + 电气设备, --include-cycle 可开)

v6.2.7 关键改动:
  - 毛利率双升 → 毛利率 gap ≤ 2pp (允许 ±2pp 波动)
  - 新进寒武纪/中微公司 (毛利率长期稳定型科技龙头)

性能: 全市场 13 季 5555 只 0.01s 跑完 (Python pandas 内存计算, 0 网络)

输出:
  - docs/earnings-blowout-watchlist.md (按季分 section, 18 列全中文)
  - stdout 速览 (最新 1 季 Top 30)
  - --top-np-jump N: 追加按 np_jump pp 差降序的 Top N 表 (默认 0=不输出)

用法:
  bash tools/with_venv.sh python -m tools.batch.earnings_blowout_scan
  bash tools/with_venv.sh python -m tools.batch.earnings_blowout_scan --rev-yoy 30 --np-yoy 80
  bash tools/with_venv.sh python -m tools.batch.earnings_blowout_scan --jump-mode leader
  bash tools/with_venv.sh python -m tools.batch.earnings_blowout_scan --top-np-jump 200
"""
import argparse
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
        {code: {"name": ..., "industry": ...}, ...}
    """
    try:
        from tools.storage.store import DataStore
        df = DataStore.load_stock_basic()
        if df.empty:
            return {}
        return {
            row["code"]: {"name": row.get("name", "") or "",
                          "industry": row.get("industry", "") or ""}
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
    输出:  df 新增 col_prev, col_prev2, col_prev3 列 (lag 1/2/3)
    """
    df = df.sort_values(["ts_code", "end_date"]).reset_index(drop=True)
    for col in cols:
        for n in range(1, n_lags + 1):
            df[f"{col}_prev{n if n > 1 else ''}"] = (
                df.groupby("ts_code")[col].shift(n)
            )
    return df


# ============================================================
# 4 条件判断 (纯函数, 每条一行)
# ============================================================

def check_4_conditions(row, rev_yoy_th, np_yoy_th) -> dict:
    """单季 4 条件判断 (返回 dict 6 个 bool)

    c1: 营收 yoy >= 阈值
    c2: 净利 yoy >= 阈值
    c3a: 毛利率 环比升 (本季 > 上季)
    c3b: 毛利率 同比升 (本季 > 去年同期)
    """
    return {
        "c1":  row["or_yoy"]             >= rev_yoy_th,
        "c2":  row["netprofit_yoy"]      >= np_yoy_th,
        "c3a": row["grossprofit_margin"] >  row["grossprofit_margin_prev"],   # 环比
        "c3b": row["grossprofit_margin"] >  row["grossprofit_margin_prev4"],  # 同比
    }


def check_4q_monotonic(row, kpis: list[str]) -> dict:
    """连续 3 季单调递增判断 (kpis 是要检查的字段名列表)

    例 kpis=['or_yoy_prev','or_yoy_prev2','or_yoy_prev3'] 验证
       rev_yoy_prev > prev2 > prev3
    """
    return {
        f"c4_{kpi}": (
            row[kpi] > row[f"{kpi}2"] > row[f"{kpi}3"]
            if kpi.endswith("prev")
            else row[kpi] > row.get(f"{kpi}_prev", -1e9) > row.get(f"{kpi}_prev2", -1e9)
        )
        for kpi in kpis
    }


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
# 命中判断 (主逻辑)
# ============================================================

def _filter_hits(df: pd.DataFrame, rev_yoy_th: float, np_yoy_th: float,
                 min_ebit_yi: float, min_increase_yi: float, min_roe: float,
                 tolerance: float = 0.0) -> pd.DataFrame:
    """从 financials df (含 3 季 LAG) 过滤出所有 4 条件 + 绝对值都满足的行

    逻辑透明 (每条一行):
      1. 3 季必须单调 (前置条件, 没有 3 季 LAG 值的行淘汰)
      2. 4 条件 c1 + c2 + c3a + c3b 都满足
      3. 3 季单调 (or_yoy / np_yoy / gm 3 项, 本季 > 上季 > 上 2 季 ± tolerance)
         - curr > prev  (严格, 不过容忍)
         - prev > prev2 - tolerance  (允许微小回踩, 解决 Q1/H1/全年口径跳跃)
      4. 绝对值 EBIT + ROE 都过门槛
    """
    # 1. 必须有完整 3 季历史 (本季 + 上季 + 上 2 季, 不依赖 NULL LAG)
    df = df.dropna(subset=[
        "or_yoy_prev", "or_yoy_prev2",
        "netprofit_yoy_prev", "netprofit_yoy_prev2",
        "grossprofit_margin_prev", "grossprofit_margin_prev2", "grossprofit_margin_prev4",
        "roe_prev", "roe_prev2",
    ]).copy()

    # 2. 4 条件 (营收 / 净利门槛 + 毛利率双升)
    df["c1"]  = df["or_yoy"]         >= rev_yoy_th
    df["c2"]  = df["netprofit_yoy"]  >= np_yoy_th
    df["c3a"] = df["grossprofit_margin"] > df["grossprofit_margin_prev"]    # 环比升
    df["c3b"] = df["grossprofit_margin"] > df["grossprofit_margin_prev4"]   # 同比升

    # 3. 3 季单调 (营收 yoy / 净利 yoy / 毛利率 3 项, 本季 > 上季 > 上 2 季 ± tolerance)
    df["c3r"]  = df["or_yoy"]      > df["or_yoy_prev"]                            # 严格: 本季 > 上季
    df["c3r2"] = df["or_yoy_prev"]  > df["or_yoy_prev2"] - tolerance               # 容忍: 上季 > 上 2 季 - tol
    df["c3n"]  = df["netprofit_yoy"]      > df["netprofit_yoy_prev"]
    df["c3n2"] = df["netprofit_yoy_prev"]  > df["netprofit_yoy_prev2"] - tolerance
    df["c3g"]  = df["grossprofit_margin"]      > df["grossprofit_margin_prev"]
    df["c3g2"] = df["grossprofit_margin_prev"]  > df["grossprofit_margin_prev2"] - tolerance

    # 4. 绝对值过滤
    df["ebit_yi"] = df["ebit"] / 1e8

    return df[
        (df["c1"] & df["c2"] & df["c3a"] & df["c3b"])          # 4 条件 (营收/净利门槛 + 毛利率双升)
        & (df["c3r"] & df["c3r2"] & df["c3n"] & df["c3n2"] & df["c3g"] & df["c3g2"])  # 3 季单调 (本季>上季>上 2 季 ± tol)
        & (df["ebit_yi"] >= min_ebit_yi)                       # EBIT 规模
        & (df["roe"] > min_roe)                                # ROE 门槛
    ]


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
    md.append(f"> 全市场扫描 13 季 | R3 v6.2.7 启动期模式: 营收 yoy>={args.rev_yoy}% + 净利 yoy>={args.np_yoy}% + 毛利率 (升 OR 跌幅≤2pp, 环比+同比) + ({mode_desc}) + 位置过滤 (距 1 年低<=200%, 距 1 年高>=-30%)\n\n")

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
                h["or_yoy"], h["or_yoy_prev"], h["or_yoy_prev2"],
                h["netprofit_yoy"], h["netprofit_yoy_prev"], h["netprofit_yoy_prev2"],
                h["grossprofit_margin"], h["grossprofit_margin_prev"], h["grossprofit_margin_prev2"],
                h["grossprofit_margin_prev4"],
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
    md.append("| 4 季增量 | `(ebit - ebit_4q_ago) / 1e8` | 业务规模真实扩大 |\n")
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
    md.append("**位置过滤 (默认开启)**: 距 1 年低点 <= 200% 且 距 1 年高点 >= -30% (排除追顶 + 严选启动期)\n\n")
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
            f"{h['or_yoy']:<8.1f}{h.get('or_yoy_prev', 0) or 0:<8.1f}{h.get('or_yoy_prev2', 0) or 0:<8.1f}"
            f"{h['netprofit_yoy']:<9.1f}{h.get('netprofit_yoy_prev', 0) or 0:<8.1f}{h.get('netprofit_yoy_prev2', 0) or 0:<8.1f}"
            f"{h['grossprofit_margin']:<6.1f}{h.get('grossprofit_margin_prev', 0) or 0:<7.1f}{h.get('grossprofit_margin_prev2', 0) or 0:<7.1f}{h.get('grossprofit_margin_prev4', 0) or 0:<7.1f}"
            f"{h['ebit_yi']:<10.2f}{h['ebit_increase_yi']:<9.2f}"
            f"{h['roe']:<6.1f}{h.get('pe', 0) or 0:<8.1f}{h.get('pe_ttm', 0) or 0:<8.1f}{h.get('total_mv_yi', 0) or 0:<10.1f}"
        )
    return "\n".join(lines)


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="5 条件启动期捕获 (EBIT>0 + 3 季 EBIT 累计 >= 2x + 营收/净利 yoy + 毛利率双升) + 位置过滤",
    )
    parser.add_argument("--rev-yoy", type=float, default=25.0, help="最低单季营收同比 (默认 25)")
    parser.add_argument("--np-yoy", type=float, default=50.0, help="最低单季净利润同比 (默认 50)")
    parser.add_argument("--np-jump", type=float, default=50.0, help="净利 yoy 跳升门槛 (pp, 本季-上季, 默认 50, 反转信号)")
    parser.add_argument("--jump-mode", choices=["reverse", "leader", "either"], default="either",
                        help="R3 反转模式: reverse=只看跳升(默认反转) / leader=只看持续高增龙头 / either=反转或龙头任一即可 (默认 either, 同时抓中际旭创/新易盛 这种持续高增龙头 + 反转票)")
    parser.add_argument("--no-position-filter", action="store_true",
                        help="关闭位置过滤 (默认开启, 距 1 年低 <= 200pct 且 距 1 年高 >= -30pct)")
    parser.add_argument("--limit", type=int, default=30, help="stdout Top N (默认 30)")
    parser.add_argument("--top-np-jump", type=int, default=0,
                        help="按本季-上季净利 yoy 差 (pp) 降序取前 N, 追加到 md (默认 0=不输出, 例 --top-np-jump 200)")
    parser.add_argument("--out", default="docs/earnings-blowout-watchlist.md", help="md 输出路径")
    parser.add_argument("--include-cycle", action="store_true", help="包含周期股 (默认排除, 周期股景气突破是 β 不是 α)")
    parser.add_argument("--tolerance", type=float, default=0.0, help="3 季单调回踩容忍 (pp, 默认 0 = 严格; 例 1.0 允许 prev vs prev2 差 -1pp, 解决财务披露口径跳跃问题)")
    parser.add_argument("--cycle-industries", default="小金属,铜,铝,化工原料,农药化肥,铅锌,矿物制品,钢铁,煤炭,石油,化纤,水运,仓储物流,电器仪表,家用电器,工程机械,石油开采,黄金,塑料,造纸,建材,玻璃,陶瓷,纺织,化纤,电气设备",
                        help="周期股白名单 (默认 SW 周期类 + 电气设备, 因为光伏/储能/电池 跟锂电材料强相关)")
    args = parser.parse_args()

    print("=" * 70)
    print("高质量高增长扫描 (SQL 取数 + Python 算, v6.2.7)")
    print("=" * 70)
    # 完整规则一览 (4 基础 AND + 1 触发 OR + 2 过滤)
    print("┌─ R3 v6.2.7 规则 ───────────────────────────────────────┐")
    print("│ 4 基础 (AND, 必须全过):                                  │")
    print(f"│   1. 营收 yoy >= {args.rev_yoy}%                              │")
    print(f"│   2. 净利 yoy >= {args.np_yoy}%                              │")
    print("│   3. 毛利率 (升 OR 跌幅≤2pp) — 环比 + 同比                │")
    print("│   4. 触发 (--jump-mode 选 1):                             │")
    print("│      reverse: 净利 yoy 跳升 >= 50pp (本季-上季)            │")
    print("│      leader : or_yoy>=80% AND np_yoy>=80% AND gm 环比升    │")
    print("│      either : reverse OR leader (默认)                     │")
    print("│                                                          │")
    print("│ 2 过滤 (默认开):                                          │")
    print("│   - 周期股: 排除 SW 周期类 + 电气设备 (--include-cycle)   │")
    print("│   - 位置  : 距 1y 低<=200% AND 距 1y 高>=-30%               │")
    print("│             (--no-position-filter 可关)                     │")
    print("│                                                          │")
    print(f"│ 当前: jump={args.jump_mode} | 周期股={'排除' if not args.include_cycle else '包含'} | 位置={'过滤' if not args.no_position_filter else '不过滤'} │")
    print("└──────────────────────────────────────────────────────────┘")
    print()
    print(f"  1. 营收 yoy  >= {args.rev_yoy}%")
    print(f"  2. 净利 yoy  >= {args.np_yoy}%")
    print(f"  3. 毛利率 (升 OR 跌幅 ≤ 2pp) — 环比 + 同比")
    print(f"  4. 净利 yoy 跳升 >= 50pp (本季 - 上季, 反转信号)")
    print(f"  5. (位置过滤) 距 1 年低点 <= 200% 且 距 1 年高点 >= -30%")
    print(f"  🚀 启动期模式: R3 v6.2.7 (营收 25% / 净利 50% / 毛利率升 OR 跌幅≤2pp / 净利跳升 50pp), 10x 票 T+0 命中 41%")
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
    # 3. 毛利率 (升 OR 跌幅 ≤ 2pp) — 环比 + 同比 (v6.2.7 改: 允许小幅下滑, 抓科技股龙头)
    # 4. 净利 yoy 跳升 >= 50pp (本季 - 上季, 反转信号)
    # 触发 OR: 反转 (c_jump) OR 龙头 (c_leader)
    # 位置过滤: 距 1y 低 <= 200% AND 距 1y 高 >= -30%
    print(f"  🚀 启动期模式: R3 v6.2.7 (4 基础 AND + 触发 OR + 位置过滤), 10x 票 T+0 命中 41%")

    # 2. 加 LAG (pandas groupby+shift, 一行代码)
    t0 = time.time()
    cols_to_lag = ["or_yoy", "netprofit_yoy", "grossprofit_margin", "ebit", "roe"]
    fin = _add_lags(fin, cols_to_lag, n_lags=4)
    t_lag = time.time() - t0

    # 3. 计算业务字段 (Python 纯函数, 透明)
    t0 = time.time()
    fin["ebit_yi"] = fin["ebit"] / 1e8
    fin["ebit_4q_ago_yi"] = fin["ebit_prev4"] / 1e8
    fin["ebit_increase_yi"] = fin["ebit_yi"] - fin["ebit_4q_ago_yi"]

    # 4. 4 条件 (透明命名 + 一行)
    fin["c1"]  = fin["or_yoy"]         >= args.rev_yoy
    fin["c2"]  = fin["netprofit_yoy"]  >= args.np_yoy
    # c3a/c3b 改: 毛利率 (升 OR 跌幅 ≤ 2pp) — 允许小幅下滑, 也允许大幅升
    # 比 v6.2.6 严格升更宽松: 抓放量型科技股 (毛利率稳定/小幅下滑) + 不漏掉毛利率大幅升的票
    fin["c3a"] = (fin["grossprofit_margin"] > fin["grossprofit_margin_prev"]) | ((fin["grossprofit_margin"] - fin["grossprofit_margin_prev"]).abs() <= 2)
    fin["c3b"] = (fin["grossprofit_margin"] > fin["grossprofit_margin_prev4"]) | ((fin["grossprofit_margin"] - fin["grossprofit_margin_prev4"]).abs() <= 2)

    # 5. 连续 3 季单调 (营收 / 净利 / 毛利率 3 项, 本季 > 上季 > 上 2 季 ± tolerance, 2 段比较)
    tol = args.tolerance
    fin["c3r"]  = fin["or_yoy"]      > fin["or_yoy_prev"]
    fin["c3r2"] = fin["or_yoy_prev"]  > fin["or_yoy_prev2"] - tol
    fin["c3n"]  = fin["netprofit_yoy"]      > fin["netprofit_yoy_prev"]
    fin["c3n2"] = fin["netprofit_yoy_prev"]  > fin["netprofit_yoy_prev2"] - tol
    fin["c3g"]  = fin["grossprofit_margin"]      > fin["grossprofit_margin_prev"]
    fin["c3g2"] = fin["grossprofit_margin_prev"]  > fin["grossprofit_margin_prev2"] - tol


    # 6. 绝对值过滤
    t_calc = time.time() - t0

    # 启动期模式: 5 条件 (1+2+3+4+5) 替代 3 季单调 + ROE 稳定性
    # 1. 本季 EBIT > 0
    # 2. 3 季 EBIT 累计 >= 2x
    # 3. 营收 yoy >= 25%
    # 4. 净利 yoy >= 50%
    # 5. R3 净利 yoy 跳升 (替代旧 3 季 EBIT 累计)
    # 净利 yoy 跳升 = 本季 np_yoy - 上季 np_yoy, 至少 50pp 才算反转
    fin["np_jump"] = fin["netprofit_yoy"] - fin["netprofit_yoy_prev"]
    fin["c_jump"] = (fin["np_jump"] >= 50) & fin["netprofit_yoy_prev"].notna()

    # 持续高增长龙头分支: 营收 yoy>=80% AND 净利 yoy>=80% AND 毛利率环比升
    # 抓中际旭创/新易盛/天孚通信 这种连续 4-5 季高增、已经看不出跳升的真龙头
    fin["c_lead_rev"] = fin["or_yoy"] >= 80
    fin["c_lead_np"] = fin["netprofit_yoy"] >= 80
    fin["c_lead_gm"] = fin["grossprofit_margin"] > fin["grossprofit_margin_prev"]
    fin["c_leader"] = fin["c_lead_rev"] & fin["c_lead_np"] & fin["c_lead_gm"] & fin["grossprofit_margin_prev"].notna()

    if args.jump_mode == "reverse":
        trigger = fin["c_jump"]
        mode_desc = "反转模式 (只看跳升>=50pp)"
    elif args.jump_mode == "leader":
        trigger = fin["c_leader"]
        mode_desc = "龙头模式 (只看持续高增 营收/净利>=80% + 毛利率环比升)"
    else:  # either
        trigger = fin["c_jump"] | fin["c_leader"]
        mode_desc = "反转或龙头任一 (默认, 同时抓中际旭创/新易盛 + 反转票)"

    print(f"  🎯 R3 触发模式: {mode_desc}")
    print(f"     反转票 (c_jump) 命中: {fin['c_jump'].sum():>6} 只次")
    print(f"     龙头票 (c_leader) 命中: {fin['c_leader'].sum():>5} 只次")
    print(f"     任一命中:        {trigger.sum():>6} 只次")

    mask = (
        # 4 基础条件 (R3 启动期 + 毛利率双升)
        fin["c1"]                                            # 营收 yoy >= 25% (保留, 高质量)
        & fin["c2"]                                          # 净利 yoy >= 50% (保留, 高质量)
        & fin["c3a"] & fin["c3b"]                            # 毛利率 同比 + 环比双升
        & trigger                                            # 反转 OR 龙头 二选一
    )
    hits_df = fin[mask].copy()
    t_filter = time.time() - t0

    # 6.5 周期股过滤 (默认排除, 周期股景气突破是 β 不是 α)
    if not args.include_cycle and not hits_df.empty:
        cycle_industries = set(s.strip() for s in args.cycle_industries.split(","))
        before = len(hits_df)
        hits_df["_industry"] = hits_df["ts_code"].str.split(".").str[0].map(
            lambda c: basic_map.get(c, {}).get("industry", "")
        )
        hits_df = hits_df[~hits_df["_industry"].isin(cycle_industries)].copy()
        hits_df = hits_df.drop(columns=["_industry"])
        n_excluded = before - len(hits_df)
        if n_excluded > 0:
            print(f"🚫 周期股过滤: 排除 {n_excluded} 只次 ({before} → {len(hits_df)}) [周期股景气突破是 β 不是 α, --include-cycle 可关]")

    # 6.6 位置过滤: startup 模式默认开启 (其他模式 --position-filter 显式开启)
    # 距 1 年低点 <= 200% 且 距 1 年高点 >= -30%, 排除"已涨 7 倍以上 + 距高点 < 30%" 的高位票
    pos_filter_on = (not args.no_position_filter)
    if pos_filter_on and not hits_df.empty:
        before = len(hits_df)
        hits_df["_sig_date"] = pd.to_datetime(hits_df["end_date"])
        pos_ok = []
        for _, r in hits_df.iterrows():
            try:
                k = DataStore.get_kline(r["ts_code"], limit=400)
                if not k:
                    pos_ok.append(False); continue
                kdf = pd.DataFrame(k)
                kdf["trade_date"] = pd.to_datetime(kdf["trade_date"].astype(str))
                kdf = kdf.sort_values("trade_date")
                # 距信号日 +/- 60 天找最近收盘价作为信号日价
                nearby = kdf[(kdf["trade_date"] >= r["_sig_date"] - pd.Timedelta(days=60)) &
                             (kdf["trade_date"] <= r["_sig_date"] + pd.Timedelta(days=60))]
                if len(nearby) == 0:
                    pos_ok.append(False); continue
                sig_p = nearby.iloc[len(nearby)//2]["close"]
                # 1 年低点 (250 交易日内) 和 1 年高点
                past_1y = kdf[kdf["trade_date"] < r["_sig_date"]].tail(250)
                if len(past_1y) < 30:
                    pos_ok.append(False); continue
                p_low_1y = past_1y["close"].min()
                p_high_1y = past_1y["close"].max()
                up_from_low = (sig_p - p_low_1y) / p_low_1y * 100
                down_from_high = (sig_p - p_high_1y) / p_high_1y * 100
                # 距 1 年低点 <= 200% (不超 3 倍, 启动期)
                # 距 1 年高点 >= -30% (至少回调 30%, 不追顶)
                pos_ok.append((up_from_low <= 200) and (down_from_high >= -30))
            except Exception:
                pos_ok.append(False)
        hits_df = hits_df.assign(_pos=pos_ok).query("_pos == True").drop(columns=["_pos", "_sig_date"])
        n_excluded = before - len(hits_df)
        if n_excluded > 0:
            print(f"📍 位置过滤: 排除 {n_excluded} 只次 ({before} → {len(hits_df)}) [距 1 年低 >200% 或 距 1 年高 <30% 的高位票, --no-position-filter 可关]")

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
                        f"{r.get('netprofit_yoy_prev',0) or 0:+.0f}% | **{r.get('np_jump',0):+.0f}** | "
                        f"{r.get('grossprofit_margin',0):.0f}% | {r.get('roe',0):.1f}% | "
                        f"{r.get('ebit',0)/1e8:.2f} | {r.get('total_mv_yi',0):.0f} | "
                        f"{r.get('pe',0):.0f} | {r.get('pe_ttm',0):.0f} |\n"
                    )
        out_path.write_text(md_content, encoding="utf-8")
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
