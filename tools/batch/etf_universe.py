"""
tools/batch/etf_universe.py — ETF 候选池 单一真源 (2026-11-06)

**为什么有这个文件:** ETF 扫描器曾各写各的候选池口径 ——
  - `etf_wyckoff_a2m_scan.py`: 自己定义 EXCLUDE_FUND_TYPES + 自己过滤
  - `etf_macd_scan.py`:       读 `config/etf_whitelist.json` (18 只手工维护名单, 已删)
两个扫描器都说"扫全市场 ETF", 口径却对不上号 → 候选池口径必须有单一真源。

> 2026-11-06: `etf_macd_scan.py` 整族下线, 本模块现只剩 `etf_wyckoff_a2m_scan.py` 一个消费者。

ETF 唯一有效的过滤 = **fund_type** (ETF 没有 PE / ROE / 毛利率 / 退市标记):
  - 债券型 — 震荡上行, 没有主升浪
  - 货币型 — 收益单调, 无形态
  - REITs  — 租金现金流驱动, 跟股票形态逻辑不同
  - 其他   — 分类不明, 不碰

用法:
    from tools.batch.etf_universe import (
        load_etf_basic, filter_by_fund_type, load_etf_klines,
        group_by_tracked_index,
    )
"""
from __future__ import annotations

import itertools
import re

# 排除的 fund_type (不适用"吸筹→主升" / 技术形态语义)
EXCLUDE_FUND_TYPES = frozenset({"债券型", "货币型", "REITs", "其他"})

# 每只取最近 N 根 K 线 (MA200 warmup 199 + 40 判定窗口 + 余量)
KLINE_LIMIT = 300

# 60 日收益率相关性 ≥ 此值 → 判定为"同一指数的不同基金", 合并
# 2026-11-05 实测 (11 只命中全 55 对): ≥0.95 命中 4 对 (全是真重复),
#   第 5 名掉到 0.885 (中证主要消费 ~ 中证酒, 不同指数), 阈值 0.95 分离干净。
DEDUP_CORR_THRESHOLD = 0.95
DEDUP_CORR_WINDOW = 60        # 相关性计算窗口 (根)


def load_etf_basic() -> list[dict]:
    """ETF 基础信息全量 (name / fund_type / benchmark 等) — 走 DataStore

    Returns: list[dict] (etf_basic 原序), 读不到返回 []
    """
    from tools.storage.store import DataStore
    rows = DataStore.get_etf_basic()
    return rows if isinstance(rows, list) else []


def filter_by_fund_type(basic: list[dict],
                       allow_types: set[str] | None = None) -> list[str]:
    """按 fund_type 过滤 → 保留的 ts_code 列表 (保持原始顺序)

    Args:
        basic:       load_etf_basic() 的结果
        allow_types: 只保留这些 fund_type; None = 排除 EXCLUDE_FUND_TYPES 里的
    """
    if allow_types:
        return [str(r["ts_code"]) for r in basic
                if str(r.get("fund_type")) in allow_types]
    return [str(r["ts_code"]) for r in basic
            if str(r.get("fund_type")) not in EXCLUDE_FUND_TYPES]


def load_etf_klines(codes: list[str],
                    limit: int = KLINE_LIMIT) -> dict[str, list[dict]]:
    """批量读 ETF K 线 — 走 DataStore (1 次 SQL)

    比逐只 get_etf_kline 快 ~30x (1992 只: 32s → 1.1s)

    Returns: {ts_code: rows} (升序); 没有数据的 code 不出现
    """
    from tools.storage.store import DataStore
    return DataStore.get_etf_klines(codes, limit=limit)


# ============================================================
# 同指数合并 (两个扫描器共用口径)
# ============================================================

def clean_index_name(seg: str) -> str:
    """把一个 benchmark 成分段清成纯指数名"""
    s = re.sub(r"×\d+(?:\.\d+)?%", "", seg)       # 去权重 ×95%
    s = s.replace("收益率", "")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)        # 去括号内容 (税后) / (使用估值汇率折算)
    s = re.sub(r"^经.{0,12}调整后的", "", s)        # 去前缀 "经估值汇率调整后的"
    s = re.sub(r"同期$", "", s)                    # 去尾部 "同期"
    return s.strip()


def normalize_benchmark(benchmark: str) -> str:
    """benchmark 归一化 → 核心跟踪指数名 (展示名 + 去重 key 的一部分)

    复合 benchmark 形如 '中证酒指数收益率×95%+商业银行活期存款利率(税后)×5%',
    要拆出权重最大的**指数**成分, 否则同一指数的 ETF / LOF / 联接会被当成
    不同指数漏合并 —— 实测 512690 鹏华中证酒ETF 的 benchmark 是
    '中证酒指数收益率×100%', 而 160632 鹏华中证酒指数(LOF) 是
    '中证酒指数收益率×95%+商业银行活期存款利率(税后)×5%', 同一指数两种写法。
    """
    s = re.sub(r"\s+", "", str(benchmark or ""))
    if not s:
        return ""
    best, best_w = "", -1.0
    for seg in s.split("+"):
        m = re.search(r"×(\d+(?:\.\d+)?)%", seg)
        w = float(m.group(1)) if m else 0.0
        name = clean_index_name(seg)
        if "指数" not in name:
            continue
        if w > best_w:
            best, best_w = name, w
    return best or clean_index_name(s)


def index_display(benchmark: str) -> str:
    """展示用: 主指数名 (复合基准带权重)

    纯指数     → '中证酒指数'
    复合基准   → '沪深300指数×80%'  (别把 80/20 混合说成纯沪深300)
    """
    s = re.sub(r"\s+", "", str(benchmark or ""))
    if not s:
        return "—"
    segs: list[tuple[float, str]] = []
    for seg in s.split("+"):
        m = re.search(r"×(\d+(?:\.\d+)?)%", seg)
        w = float(m.group(1)) if m else 100.0
        name = clean_index_name(seg)
        if "指数" in name:
            segs.append((w, name))
    if not segs:
        return clean_index_name(s) or "—"
    segs.sort(key=lambda x: -x[0])
    w, name = segs[0]
    return f"{name}×{w:g}%" if len(segs) > 1 else name


def benchmark_dedup_key(row: dict) -> str:
    """某只 ETF 的 benchmark 去重键

    benchmark 有效 → 用归一化 benchmark (同指数同组)
    benchmark 空 / '本基金暂不设立业绩比较基准' → 用 ts_code (不合并, 避免误伤)
    """
    key = normalize_benchmark(row.get("benchmark"))
    if not key or "业绩比较基准" in key or "暂不设立" in key:
        return f"__solo__{row['ts_code']}"
    return key


def liquidity_score(rows: list[dict], window: int = 20) -> float:
    """最近 N 根日均成交额 (Tushare amount 单位: 千元) — 代表 ETF 的流动性"""
    tail = rows[-window:] if rows else []
    amts = [float(r.get("amount") or 0.0) for r in tail]
    return sum(amts) / len(amts) if amts else 0.0


def daily_returns(rows: list[dict], window: int = DEDUP_CORR_WINDOW) -> list[float]:
    """最近 N 根日收益率 (用于同指数判定)"""
    tail = rows[-(window + 1):] if rows else []
    closes = [float(r.get("close") or 0.0) for r in tail]
    return [closes[i] / closes[i - 1] - 1
            for i in range(1, len(closes)) if closes[i - 1] > 0]


def return_correlation(a: list[float], b: list[float], min_n: int = 20) -> float:
    """两组收益率的皮尔逊相关系数"""
    n = min(len(a), len(b))
    if n < min_n:
        return 0.0
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = sum((x - ma) ** 2 for x in a) ** 0.5
    db = sum((y - mb) ** 2 for y in b) ** 0.5
    return num / (da * db) if da and db else 0.0


def group_by_tracked_index(codes: list[str], klines: dict[str, list[dict]],
                            basic_by_ts: dict[str, dict]
                            ) -> tuple[dict[str, list[str]],
                                       dict[tuple[str, str], float]]:
    """按跟踪指数分组 (两级并查集) → ({组根: [成员 ts_code]}, {对: 相关系数})

    级 1: **benchmark 归一化 key 相同** → 同指数, 直接合并
    级 2: **60 日收益率相关性 ≥ DEDUP_CORR_THRESHOLD** → 同一指数的不同写法
           (源数据 benchmark 写法不统一: '中证酒指数' vs '中证白酒指数',
            纯字符串匹配抓不到 —— 实测 160632 / 161725 / 512690 三只相关度
            0.970~0.982, 是同一指数的三只基金)

    组根 = 组内第一个成员 (原始顺序); 代表由调用方按流动性挑。
    """
    parent: dict[str, str] = {c: c for c in codes}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    # ① benchmark 归一化 key 相同 → 合并
    by_key: dict[str, str] = {}
    for c in codes:
        key = benchmark_dedup_key(basic_by_ts.get(c, {}))
        if key in by_key:
            union(c, by_key[key])
        else:
            by_key[key] = c

    # ② 收益率相关性 ≥ 阈值 → 合并 (抓 benchmark 写法不一致的情况)
    #    注意: 相关度**全对都算** (包括已被 ① 合并的), 否则汇总表里的
    #    "合并依据" 对 benchmark 相同的组会显示 0.000, 看不出凭什么合并的
    rets = {c: daily_returns(klines.get(c, [])) for c in codes}
    pair_corr: dict[tuple[str, str], float] = {}
    for a, b in itertools.combinations(codes, 2):
        c = return_correlation(rets[a], rets[b])
        pair_corr[(a, b)] = c
        if find(a) == find(b):
            continue
        if c >= DEDUP_CORR_THRESHOLD:
            union(a, b)

    groups: dict[str, list[str]] = {}
    for c in codes:
        groups.setdefault(find(c), []).append(c)
    return groups, pair_corr
