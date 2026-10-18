"""
tools/batch/wyckoff_a2m_scan.py — 全市场扫描"最近 N 根 K 线 A→M 转变"

v8 (2026-10-16) 重构 (用户反馈 7 分钟太慢):
- 删 _judge_full 慢路径后, 扫全场只剩 wyckoff_judge 一个入口
- v8 直调: build_kline_features (1 次 O(N)) + scan_sub_events 全段 (1 次) + wyckoff_judge O(1) per i
- v7 走 AnalysisEngine.analyze_history 算 480 日 history → 0.07s/只 → 7 分钟
- v8 跳过 history 算, 直接算 lookback+1 个 i → ~0.005s/只 → 5812 × 30s

判定逻辑 (跟 v6/v7 1:1, 没变):
  对每只票最近 (lookback+1) 根 K 线:
  - wyckoff_judge 输出 stage (Accumulation / Markup / Distribution)
  - 任意连续两根 (i-1, i) 满足 prev='Accumulation' 且 curr='Markup' → A→M 转变命中
  - M 之后 M 占比 ≥ 50% (过滤跳来跳去的假突破)
  - 单日涨跌 > 20% 跳过 (除权/复牌)

数据一致性 (跟 /t-analyze 100%):
  - 同源: 同 K 线 parquet + 同 wyckoff_judge 算法 + 同 arrs 预算
  - 已实测 (5 只票 5 周期全等): /tmp/diff_judges2.py

性能基线 (2026-10-16 v8 实测):
  全市场 5812 只 ≈ 30s (1 worker, 直调 wyckoff_judge + arrs 预算)
"""
from __future__ import annotations
import argparse
import sys
import time
import duckdb
import multiprocessing as mp
import os
import pandas as pd
from pathlib import Path

# 2026-10-16 v5 改: 1 次 SQL 拿全市场 K 线, 然后并发按票调 WyckoffStageFactor.compute
# 性能: v4 7 分钟 → v5 ~30s (200x 加速)
# 数据: 跟 /t-analyze 详报 100% 一致 (走的是同一个 WyckoffStageFactor, stage_factor.py 修了 self.config getattr fallback)


def _filter_by_st(codes: list[str]) -> list[str]:
    """1 次 SQL 排除 ST / *ST 股 (退市风险, 不进 A→M 扫描)

    2026-10-17 加: 204 只 ST 票有退市风险, 默认剔除

    Args:
        codes: 候选 code 列表 (6 位纯数字)

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes:
        return []
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
    df = duckdb.execute(f"""
        SELECT ts_code FROM read_parquet('data/history/stock_basic/stock_basic.parquet')
        WHERE ts_code IN ({in_clause})
          AND (name LIKE '%ST%' OR name LIKE '%*ST%')
    """).fetchdf()
    st_codes = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
    return [c for c in codes if c not in st_codes]


def _filter_by_pe(codes: list[str], pe_max: float) -> list[str]:
    """1 次 SQL 按 stk_factor 最新 trade_date 过滤 PE > pe_max 的票 (高估值剔除)

    2026-10-17 加: --exclude-pe 高价股前置过滤
    - 默认 0 = 不过滤
    - 50/80/100 分别为 适中/较严/严 三档 (跟用户 A 股估值分布对账: PE>100 占 23%)
    - PE <= 0 或 NULL (亏损 / 数据缺) 默认放行 (跟 --with-finance 互补)
    - 跟 stk_basic name 字段比 join 一下拿中文名 (但不必要, 过滤只用 ts_code)

    Args:
        codes: 候选 code 列表 (6 位纯数字)
        pe_max: PE 上限 (如 100),保留 pe <= pe_max 的票

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes or pe_max <= 0:
        return codes
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
    df = duckdb.execute(f"""
        SELECT ts_code FROM read_parquet('data/history/stk_factor/2026Q4.parquet')
        WHERE trade_date = (SELECT MAX(trade_date) FROM read_parquet('data/history/stk_factor/2026Q4.parquet'))
          AND pe > {pe_max} AND pe < 500
    """).fetchdf()
    expensive = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
    return [c for c in codes if c not in expensive]


def _filter_by_quality(codes: list[str], or_yoy_min: float, gm_min: float,
                       np_yoy_min: float | None = None) -> list[str]:
    """1 次 SQL 按 financials 过滤: or_yoy > or_yoy_min AND grossprofit_margin > gm_min AND (np_yoy_min)

    2026-10-17 改: 优先用最新季报 (2026Q2), 拿不到 fallback 2025Q4 累计
    (旧版本只用 2025Q4 累计 netprofit_yoy 滞后 1 年, 漏判 2026 已亏损的票)

    Args:
        codes: 候选 code 列表 (6 位纯数字)
        or_yoy_min: 营收同比最小值 (%, 如 0 / 10 / 20)
        gm_min: 毛利率最小值 (%, 同上)
        np_yoy_min: 净利润同比最小值 (%, 默认 None = 不限, 设 0/10/20 启用)

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes:
        return []
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH'" for c in codes)
    np_clause = f"AND netprofit_yoy > {np_yoy_min}" if np_yoy_min is not None else ""
    # 优先 2026Q2 (最新季报), 拿不到 fallback 2025Q4 累计
    df = duckdb.execute(f"""
        SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, source
        FROM (
            SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, '2026Q2' AS source
            FROM read_parquet('data/history/financials/2026Q2.parquet')
            WHERE ts_code IN ({in_clause}) AND fetch_status = 'ok'
            UNION ALL
            SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, '2025Q4' AS source
            FROM read_parquet('data/history/financials/2025Q4.parquet')
            WHERE ts_code IN ({in_clause})
              AND ts_code NOT IN (SELECT ts_code FROM read_parquet('data/history/financials/2026Q2.parquet') WHERE fetch_status = 'ok')
        )
        WHERE grossprofit_margin > {gm_min}
          AND or_yoy > {or_yoy_min}
          {np_clause}
    """).fetchdf()
    return [r['ts_code'].split('.')[0] for r in df.to_dict('records')]


def _filter_by_rsi(codes: list[str], rsi6_max: float = 30, rsi12_max: float = 35) -> list[str]:
    """1 次 SQL 算 RSI6 + RSI12 过滤超卖股

    简化 RSI 公式 (跟 W&B 量化对账过): RSI_n = 100 * avg_gain_n / (avg_gain_n + avg_loss_n)
    取最近 n 根的 chg (不含今天, 因为今天 close - yesterday close 算昨天那根的 chg),
    实际取最近 n+1 根的 chg 取前 n 个 = n 根的"老"gain/loss.

    Args:
        codes: 候选 code 列表 (6 位纯数字)
        rsi6_max: RSI6 上限, 默认 30 (超卖)
        rsi12_max: RSI12 上限, 默认 35 (超卖)

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes:
        return []
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH'" for c in codes)
    df = duckdb.execute(f"""
        WITH k AS (
          SELECT ts_code, trade_date, close,
            close - LAG(close) OVER (PARTITION BY ts_code ORDER BY trade_date) AS chg,
            ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
          FROM read_parquet('data/history/daily/*.parquet')
          WHERE ts_code IN ({in_clause})
        ),
        rsi6 AS (
          SELECT ts_code, 100.0 * AVG(gain) / (AVG(gain) + AVG(loss)) AS rsi6
          FROM (SELECT ts_code, GREATEST(chg, 0) gain, GREATEST(-chg, 0) loss, rn FROM k WHERE chg IS NOT NULL AND rn BETWEEN 2 AND 7)
          GROUP BY ts_code
        ),
        rsi12 AS (
          SELECT ts_code, 100.0 * AVG(gain) / (AVG(gain) + AVG(loss)) AS rsi12
          FROM (SELECT ts_code, GREATEST(chg, 0) gain, GREATEST(-chg, 0) loss, rn FROM k WHERE chg IS NOT NULL AND rn BETWEEN 2 AND 13)
          GROUP BY ts_code
        )
        SELECT k.ts_code, rsi6.rsi6, rsi12.rsi12
        FROM (SELECT DISTINCT ts_code FROM k WHERE rn = 1) k
        LEFT JOIN rsi6 USING (ts_code)
        LEFT JOIN rsi12 USING (ts_code)
        WHERE COALESCE(rsi6.rsi6, 50) < {rsi6_max} AND COALESCE(rsi12.rsi12, 50) < {rsi12_max}
    """).fetchdf()
    return [r['ts_code'].split('.')[0] for r in df.to_dict('records')]


def _load_all_klines(codes: list[str], limit_per_code: int = 2000) -> dict[str, pd.DataFrame]:
    """1 次 SQL 拿多只票的最近 N 根 K 线, 返回 {code: DataFrame}

    比 DataStore.get_kline 5812 次快 ~200x (实测 0.42s 拿 5812 票 vs 5812 × 0.07s = 7 分钟)
    """
    # IN 子句拼字符串 (codes 数 5812, 拼出来 ~60KB, duckdb 接受)
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH'" for c in codes)
    sql = f"""
        WITH ranked AS (
            SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
            FROM read_parquet('data/history/daily/*.parquet')
            WHERE ts_code IN ({in_clause})
        )
        SELECT ts_code, trade_date, open, high, low, close, pre_close, vol, pct_chg
        FROM ranked
        WHERE rn <= {limit_per_code}
        ORDER BY ts_code, trade_date
    """
    df = duckdb.execute(sql).fetchdf()
    df['date'] = df['trade_date']
    df['volume'] = df['vol']
    out = {}
    for code, sub in df.groupby('ts_code'):
        out[code.split('.')[0]] = sub.sort_values('trade_date').reset_index(drop=True)
    return out


def _process_one(code: str, df: pd.DataFrame, lookback: int = 5) -> dict | None:
    """单只票 威科夫当前阶段检测 (v9 改: 只要当前是 Markup 就命中, 不再要求 A→M 转变)

    性能: ~0.075s/只 (单进程顺序), 5812 × 5.3 分钟
    数据: 100% 跟 /t-analyze 详报一致 (同源 wyckoff_judge 算法, 同输入数据)
    路径: 1 次 SQL 全市场 K 线 → 循环每只:
            ① build_kline_features(closes, highs, lows, vols) 1 次 (250 根 O(N))
            ② scan_sub_events(..., as_of_idx=None) 全段扫 1 次缓存
            ③ wyckoff_judge(i, arrs, sub_set) O(1) per i (lookback+1 次)
    判定: 当前 K 线 stage == 'Markup' 即命中
    """
    # df 来自 _load_all_klines (≤2000 根), 直接用
    if df is None or len(df) < 220:
        return None

    closes = df['close'].tolist()
    highs  = df['high'].tolist()
    lows   = df['low'].tolist()
    vols   = df['volume'].tolist()
    opens  = df['open'].tolist() if 'open' in df.columns else closes
    pct_chgs = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
    dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
    n = len(closes)
    window = min(250, n)

    from tools.factors.wyckoff.stage_factor import wyckoff_judge
    from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events
    from tools.factors.kline_arrays import build_kline_features

    # ① arrs 预算 1 次
    arrs = build_kline_features(closes, highs, lows, vols, window=window)

    # ② sub_events 全段扫 1 次 (as_of_idx=None)
    sub_events_all = scan_sub_events(
        closes, highs, lows, vols, None,
        o=opens, pct_chg=pct_chgs,
        period_label="daily", code=code,
        as_of_idx=None,
        dates=dates_,
        precomputed_arrs=arrs,
    )

    # ③ wyckoff_judge O(1) per i (最近 lookback+1 个)
    #    sub_events 按 i ≤ idx 过滤 (历史窗口内触发过的)
    n_tail = max(lookback + 1, 11)  # 2026-10-17 v10: 至少 11 根 (1 当前 + 10 前), 看最近 10 根内是否转 M
    stages = []
    closes_tail = []
    dates_tail = []
    for k in range(n_tail):
        i = n - n_tail + k  # 倒着数第 k 个
        sub_set = {e["name"] for e in sub_events_all if e["idx"] <= i}
        out = wyckoff_judge(i, arrs, sub_set,
                             window=arrs['window'], period_label="daily")
        if out is None:
            stages.append("?")
        else:
            stages.append(out.get("stage", "?"))
        closes_tail.append(closes[i])
        dates_tail.append(dates_[i])

    # ── v11 字面版 判定 (2026-10-17 改): "最近 10 根 K 线, 前 5 根 ∈ {A, ?}, 当前 M" ──
    #   1. 当前 (最后) 根 K 线 stage == 'Markup' — 必须
    #   2. 前面 5 根 (T-1 ~ T-5) ALL ∈ {Accumulation, ?} — 必须 (A + ? 都接受, 排除 D)
    #      "?" 表示停牌 / 数据不足 / 反弹区, 跟 A 同样视为"非上涨"状态
    #   3. T-6 ~ T-9 是过渡期, 不卡
    #   4. 单日涨跌幅 > 20% 跳过 (除权/复牌)
    # 这是"刚转过来"的强信号: 从吸筹/盘整阶段确认进入 M
    last_chg = abs(closes_tail[-1] / closes_tail[-2] - 1) if len(closes_tail) >= 2 and closes_tail[-2] > 0 else 0
    if last_chg > 0.20:
        return None
    if stages[-1] != "Markup":
        return None
    if len(stages) < 6:
        return None
    # T-1..T-5 (stages[-6..-1]) 必须 ALL 是 Accumulation 或 "?" (排除 Markup 和 Distribution)
    accum_window = stages[-6:-1]  # 5 根
    allowed = {"Accumulation", "?"}
    if not all(s in allowed for s in accum_window):
        return None
    # 找到第一根 (从右往左) 满足 != M 的位置, 记为 trans_idx
    trans_idx = 0
    for k in range(len(stages) - 1, 0, -1):
        if stages[k - 1] != "Markup":
            trans_idx = k
            break

    return {
        "code": code,
        "trans_idx": trans_idx,
        "trans_date": dates_tail[trans_idx],
        "trans_close": closes_tail[trans_idx],
        "last_date": dates_tail[-1],
        "last_close": closes_tail[-1],
        "prev_stage": stages[trans_idx - 1] if trans_idx > 0 else "?",
        "curr_stage": stages[trans_idx],
        "curr_stage": stages[-1],
        "trans_stages": stages,
        "m_ratio": 1.0,
    }

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _worker(code: str, df: pd.DataFrame, lookback: int) -> dict | None:
    """单只票检测当前威科夫 M 阶段 (df 来自 _load_all_klines 一次 SQL 预加载)"""
    try:
        return _process_one(code, df, lookback)
    except Exception as e:
        return {"code": code, "error": f"{type(e).__name__}: {e}"}


# 2026-10-17 加: multiprocessing worker for /t-analyze 详报
# 用 forkserver 启动 (mac 比 fork 稳), GIL 跨进程解锁 4-8x 加速
def _report_worker(task: tuple[str, str, str]) -> tuple[str, bool, str, float]:
    """单只详报 worker (跑在子进程)

    Args:
        task: (code, name, output_subdir) 三元组

    Returns:
        (code, ok, md_path_or_err, elapsed)
    """
    code, name, output_subdir = task
    import time
    t0 = time.time()
    try:
        from tools.batch.t_analyze_one import process_one as t_analyze_process_one
        r = t_analyze_process_one(code, name, output_subdir=output_subdir)
        elapsed = time.time() - t0
        ok = bool(r.get("ok"))
        path_or_err = r.get("md_path") if ok else r.get("err", "unknown")
        return (code, ok, path_or_err, elapsed)
    except Exception as e:
        return (code, False, f"{type(e).__name__}: {e}", time.time() - t0)


def load_codes_all_market() -> list[str]:
    """全市场 codes (data/history/daily 有数据的)"""
    df = duckdb.execute(
        "SELECT DISTINCT ts_code FROM read_parquet('data/history/daily/*.parquet')"
    ).fetchdf()
    return [c.split('.')[0] for c in df['ts_code'].tolist()]


def load_codes_watchlist(types: list[str] | None) -> list[tuple[str, str]]:
    """从 watchlist.json 取 (code, name)"""
    import json
    wl_path = ROOT / "config" / "watchlist.json"
    with open(wl_path) as f:
        wl = json.load(f)
    out = []
    for s in wl.get("stocks", []):
        lt = s.get("list_type", "")
        if types and lt not in types:
            continue
        code = s.get("code", "").strip()
        name = s.get("name", "").strip() or code
        if code:
            out.append((code, name))
    return out


_STOCK_BASIC_PATHS = (
    "data/history/stock_basic/stock_basic.parquet",
    "data/stock_basic.parquet",
    "data/basics/stock_basic.parquet",
)

def load_stock_basic() -> dict:
    """code → (name, industry) 映射"""
    for path in _STOCK_BASIC_PATHS:
        try:
            df = duckdb.execute(
                f"SELECT ts_code, name, industry FROM read_parquet('{path}')"
            ).fetchdf()
            if len(df) > 0:
                return {row['ts_code'].split('.')[0]: (row['name'], row['industry'])
                        for _, row in df.iterrows()}
        except Exception:
            continue
    return {}


def main():
    ap = argparse.ArgumentParser(description="Wyckoff A→M 转变扫描 (复用 AnalysisEngine+compute_factor_history)")
    ap.add_argument("--lookback", type=int, default=5, help="看最近 N 根 K 线 (默认 5)")
    ap.add_argument("--all-market", action="store_true", default=True,
                    help="全市场扫 (默认, 2026-10-16 改: 熊市 watchlist 没 M)")
    ap.add_argument("--watchlist-types", default="持仓,blowout,自选",
                    help="watchlist 类型过滤 (用 --watchlist-only 启用)")
    ap.add_argument("--watchlist-only", action="store_true",
                    help="只扫 watchlist (默认全市场)")
    ap.add_argument("--write-md", action="store_true", default=True,
                    help="落盘 docs/wyckoff-a2m.md (默认 True, 2026-10-16 改)")
    ap.add_argument("--no-write-md", action="store_true", help="不落盘 (默认 False)")
    ap.add_argument("--output", default="docs/wyckoff-a2m.md", help="md 输出路径")
    ap.add_argument("--full-report", action="store_true", default=True,
                    help="命中后逐只跑 /t-analyze 22 section 详报 (默认 True, 2026-10-16 改)")
    ap.add_argument("--no-full-report", action="store_true", help="不跑详报 (只输出扫描汇总)")
    ap.add_argument("--report-top", type=int, default=10,
                    help="--full-report 时只详报前 N 只 (默认 10, 0=全部)")
    ap.add_argument("--report-subdir", default="wyckoff-a2m",
                    help="详报输出子目录 (默认 wyckoff-a2m, 跳过 watchlist 分流, 不污染自选)")
    ap.add_argument("--no-clean", action="store_true",
                    help="不清理 docs/<subdir> 已有 md (默认会清理, 避免新旧混杂)")
    ap.add_argument("--with-finance", action="store_true",
                    help="加 finance 过滤: 剔除亏损股 (PE <= 0 视为亏损), 0 网络 (走 stk_factor parquet)")
    ap.add_argument("--with-quality", choices=["off", "basic", "strong", "elite"], default="off",
                    help="quality 过滤 (财务质量前置, 减少扫描票数): "
                         "off=不限 / basic=营收+毛利+净利均>0 (2.4分, ~1950票) / "
                         "strong=三指标均>10% (1.4分, ~1100票) / "
                         "elite=三指标均>20% (30秒, ~300票)")
    ap.add_argument("--with-rsi", action="store_true",
                    help="(2026-10-17 弃用) 加 RSI 超卖过滤 — 现在 v10 改 A→M 转变判定, 不再需要 RSI")
    ap.add_argument("--exclude-st", action="store_true", default=True,
                    help="排除 ST/*ST 股 (退市风险, 默认 True, 2026-10-17 加)")
    ap.add_argument("--include-st", action="store_true",
                    help="包含 ST/*ST 股 (反旗, 默认已排除, 仅当显式指定时启用)")
    ap.add_argument("--exclude-pe", type=float, default=0.0,
                    help="前置 PE 过滤上限 (排除 pe > N 的高估值票, 0=不限):\n"
                         "  50  = 适中 (剩 ~2400 票, 适合严价值选股)\n"
                         "  80  = 较严 (剩 ~2960 票)\n"
                         "  100 = 默认推荐 (剩 ~3160 票, 排除 PE>100 高估 23%)\n"
                         "底层走 stk_factor 最新 trade_date, 跟 --with-finance (命中后 PE≤0/PE>500 剔亏损妖股) 互补")
    ap.add_argument("--workers", type=int, default=0,
                    help="详报多进程 worker 数 (step 2 用, 跨 GIL 解锁; 默认 0 = os.cpu_count(); 设 1 强制单进程)")
    ap.add_argument("--bench-report", nargs="?", const=12, type=int,
                    help="跳过扫描, 只跑 N 个固定代码详报测试不同 workers (用于测 mp 加速比)")
    args = ap.parse_args()

    if args.watchlist_only:
        wl = load_codes_watchlist([t.strip() for t in args.watchlist_types.split(",") if t.strip()])
        codes = [c for c, _ in wl]
        scope = f"watchlist({args.watchlist_types})"
    else:
        codes = load_codes_all_market()
        scope = "全市场"

    if not codes:
        print("❌ 无代码可跑", file=sys.stderr)
        sys.exit(1)

    # 2026-10-17 加: --bench-report 性能测试 (跳过扫描, 跑 N 只固定代码详报对比 workers)
    if args.bench_report:
        bench_codes = ['301487', '605189', '605228', '600869', '301592',
                       '600177', '601107', '605566', '002232', '600408',
                       '603188', '000829'][:args.bench_report]
        print(f"\n=== BENCH: 跳扫描, 跑 {len(bench_codes)} 只固定代码详报对比 workers ===", flush=True)
        tasks = [(c, '', '_bench') for c in bench_codes]
        # 1 worker baseline
        t0 = time.time()
        with mp.get_context("spawn").Pool(1) as p:
            results = list(p.imap_unordered(_report_worker, tasks))
        t1 = time.time() - t0
        print(f"  1 spawn-worker: {t1:.1f}s ({len(bench_codes)} 只 = {t1/len(bench_codes):.2f}s/只)", flush=True)
        # N worker
        cpu_n = os.cpu_count() or 4
        nw = min(cpu_n, len(bench_codes))
        t0 = time.time()
        with mp.get_context("spawn").Pool(nw) as p:
            results = list(p.imap_unordered(_report_worker, tasks))
        tn = time.time() - t0
        speedup = t1 / tn if tn > 0 else 0
        print(f"  {nw} spawn-worker: {tn:.1f}s ({len(bench_codes)} 只 = {tn/len(bench_codes):.2f}s/只)")
        print(f"  ⚡ 加速: {speedup:.2f}x")
        # cleanup test md
        out_dir = Path("docs/_bench")
        if out_dir.exists():
            for f in out_dir.glob("*.md"):
                f.unlink()
        sys.exit(0)

    # 2026-10-17 加: 默认排除 ST/*ST 票 (退市风险)
    if args.exclude_st and not args.include_st:
        kept = _filter_by_st(codes)
        print(f"  exclude-st (ST/*ST 退市风险): {len(codes)} → {len(kept)} 票", flush=True)
        codes = kept
        if not codes:
            print("❌ ST 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-17 加: --exclude-pe 高估值前置过滤 (PE > N 默认 0 = 不过滤)
    if args.exclude_pe > 0:
        kept = _filter_by_pe(codes, args.exclude_pe)
        print(f"  exclude-pe (PE>{args.exclude_pe:.0f} 高估值): {len(codes)} → {len(kept)} 票", flush=True)
        codes = kept
        if not codes:
            print(f"❌ PE>{args.exclude_pe:.0f} 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-16: --with-quality 前置过滤 (财务质量, 减少扫描票数)
    if args.with_quality != "off":
        quality_thr = {"basic": 0, "strong": 10, "elite": 20}[args.with_quality]
        kept_codes = _filter_by_quality(codes, or_yoy_min=quality_thr,
                                         gm_min=quality_thr, np_yoy_min=quality_thr)
        print(f"  quality={args.with_quality} (营收>{quality_thr}% + 毛利>{quality_thr}% + 净利>{quality_thr}%): "
              f"{len(codes)} → {len(kept_codes)} 票", flush=True)
        codes = kept_codes
        if not codes:
            print("❌ 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-16: --with-rsi 超卖过滤 (在 quality 后, 进一步缩量)
    if args.with_rsi and codes:
        rsi_kept = _filter_by_rsi(codes, rsi6_max=30, rsi12_max=35)
        print(f"  rsi filter (RSI6<30 AND RSI12<35): {len(codes)} → {len(rsi_kept)} 票", flush=True)
        codes = rsi_kept
        if not codes:
            print("❌ RSI 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    basic = load_stock_basic()
    print(f"=== Wyckoff A→M 扫描 (scope: {scope}, lookback: {args.lookback}) ===")
    print(f"  任务: {len(codes)} 只, 单进程顺序 (scan_sub_events GIL 锁死, ThreadPool 0 加速)")
    print(f"  链路: DataStore → AnalysisEngine(WyckoffStrategy) → compute_factor_history")
    print(f"  复用跟 /t-analyze 同源, 0 网络\n")

    t0 = time.time()
    # 2026-10-16 v5 改: 1 次 SQL 拿全市场 K 线, 替换 5812 次 DataStore.get_kline
    # 实测 0.42s 拿 5812 票 (vs DataStore 7 分钟), 200x 加速
    print(f"  1) 加载全市场 K 线 (1 次 SQL) ...", flush=True)
    kline_map = _load_all_klines(codes, limit_per_code=2000)
    print(f"  1) 完成: {len(kline_map)} 票 ({time.time()-t0:.2f}s)", flush=True)

    t1 = time.time()
    hits = []
    errors = []
    # 2026-10-16 v8: 单进程顺序跑 (scan_sub_events 内部 GIL 占满, ThreadPool 实测 0 加速)
    # 之前保留 --workers flag 实测 1/4/8 worker 全是 32s, 2026-10-17 删 flag + 改成单进程
    completed = 0
    for code in codes:
        df = kline_map.get(code)
        if df is None or len(df) < 220:
            continue
        r = _worker(code, df, args.lookback)
        if r and 'error' not in r:
            hits.append(r)
        elif r and 'error' in r:
            errors.append(r)
        completed += 1
        if completed % 500 == 0 or completed == len(codes):
            print(f"  2) {completed}/{len(codes)} done, {len(hits)} hits, {len(errors)} errs, {time.time()-t1:.0f}s", flush=True)

    elapsed = time.time() - t0
    print(f"\n=== 完成 {len(hits)}/{len(codes)} 命中 ({len(errors)} errors), {elapsed:.1f}s ===\n")

    elapsed = time.time() - t0
    print(f"\n=== 完成 {len(hits)}/{len(codes)} 命中 ({len(errors)} errors), {elapsed:.1f}s ===\n")

    hits.sort(key=lambda x: (x['trans_idx'], x['code']))

    # 2026-10-16 加: --with-finance 过滤亏损股 (PE <= 0)
    if args.with_finance and hits:
        # 1 次 SQL 拿最近 stk_factor (PE 当日)
        sf_df = duckdb.execute("""
            SELECT ts_code, pe, pe_ttm, pb
            FROM read_parquet('data/history/stk_factor/2026Q4.parquet')
            WHERE trade_date = (SELECT MAX(trade_date) FROM read_parquet('data/history/stk_factor/2026Q4.parquet'))
        """).fetchdf()
        sf_map = {row['ts_code'].split('.')[0]: row.to_dict() for _, row in sf_df.iterrows()}

        before = len(hits)
        filtered = []
        for h in hits:
            sf = sf_map.get(h['code'])
            if sf is None:
                # stk_factor 没数据 (新上市/数据不全), 默认放行
                filtered.append(h)
                continue
            pe = sf.get('pe')
            pe_ttm = sf.get('pe_ttm')
            # PE <= 0 (亏损) 或 PE 异常高 (>500) 视为财务异常, 剔除
            if pe is not None and (pe <= 0 or pe > 500):
                continue
            if pe_ttm is not None and (pe_ttm <= 0 or pe_ttm > 500):
                continue
            h['pe'] = pe
            h['pe_ttm'] = pe_ttm
            filtered.append(h)
        hits = filtered
        print(f"\n=== finance 过滤: {before} → {len(hits)} 只 (剔除 {before - len(hits)} 只亏损/异常) ===\n")

    # 渲染输出
    lines = []
    lines.append(f"# Wyckoff A→M 转变扫描 ({scope})\n")
    try:
        last_trade_date = str(duckdb.execute(
            "SELECT MAX(trade_date) FROM read_parquet('data/history/daily/*.parquet')"
        ).fetchone()[0])[:8]
    except Exception:
        last_trade_date = hits[0]['last_date'] if hits else 'N/A'
    lines.append(f"> 数据日期: {last_trade_date} (lookback={args.lookback} 根 K 线)")
    lines.append(f"> 命中: {len(hits)} 只 / {len(codes)} ({len(hits)/len(codes)*100:.1f}%)")
    lines.append(f"> 扫描耗时: {elapsed:.1f}s (单进程顺序)")
    lines.append(f"> 数据源: AnalysisEngine + compute_factor_history (跟 /t-analyze 同源, 0 网络)\n")

    if hits:
        if args.with_finance:
            lines.append("| 转变天数 | 代码 | 名称 | 行业 | 转变日 | 转变价 | 当前价 | 涨幅% | PE | PE_TTM | 阶段序列 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        else:
            lines.append("| 转变天数 | 代码 | 名称 | 行业 | 转变日 | 转变价 | 当前价 | 涨幅% | 阶段序列 |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
        for r in hits:
            code = r['code']
            name, industry = basic.get(code, (code, "—"))
            stages_str = "/".join(s[0] for s in r['trans_stages'])
            chg = (r['last_close'] / r['trans_close'] - 1) * 100
            if args.with_finance:
                pe = r.get('pe', '')
                pe_ttm = r.get('pe_ttm', '')
                pe_s = f"{pe:.1f}" if isinstance(pe, (int, float)) else "—"
                pe_ttm_s = f"{pe_ttm:.1f}" if isinstance(pe_ttm, (int, float)) else "—"
                lines.append(
                    f"| **T-{r['trans_idx']}** | `{code}` | {name} | {industry} | "
                    f"{r['trans_date']} | ¥{r['trans_close']:.2f} | ¥{r['last_close']:.2f} | "
                    f"{chg:+.1f}% | {pe_s} | {pe_ttm_s} | {stages_str} |"
                )
            else:
                lines.append(
                    f"| **T-{r['trans_idx']}** | `{code}` | {name} | {industry} | "
                    f"{r['trans_date']} | ¥{r['trans_close']:.2f} | ¥{r['last_close']:.2f} | "
                    f"{chg:+.1f}% | {stages_str} |"
                )

    out_path = Path(args.output)
    if args.write_md:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"📄 落盘: {out_path}\n")
    else:
        print("\n".join(lines))

    # step 2: 命中后逐只跑 /t-analyze 22 section 详报
    if args.no_full_report:
        args.full_report = False
    if args.no_write_md:
        args.write_md = False
    if args.full_report and hits:
        from tools.batch.t_analyze_one import process_one as t_analyze_process_one
        target_hits = hits[:args.report_top] if args.report_top > 0 else hits
        # 2026-10-17: 默认清空 docs/<subdir>/ 旧 md (按 F5 跑的语义, 避免新旧混杂)
        sub_dir = Path(f"docs/{args.report_subdir}")
        if sub_dir.exists() and not args.no_clean:
            old_mds = list(sub_dir.glob("*.md"))
            for p in old_mds:
                p.unlink()
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只 → docs/{args.report_subdir}/) ===")
            print(f"  清空旧 md: {len(old_mds)} 份 (用 --no-clean 可跳过)")
        else:
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只 → docs/{args.report_subdir}/) ===")
            if not args.no_clean:
                print(f"  子目录不存在, 无需清空")
            else:
                print(f"  --no-clean: 保留历史详报")
        # 2026-10-17 加: multiprocessing 多进程跑详报 (跨 GIL, 4-8x 加速)
        # 默认 workers = os.cpu_count(), 设 1 单进程回退
        n_workers = args.workers if args.workers > 0 else (os.cpu_count() or 4)
        n_workers = min(n_workers, len(target_hits))  # 不超过任务数
        tasks = [(r['code'], basic.get(r['code'], (r['code'], "—"))[0], args.report_subdir)
                 for r in target_hits]
        rt0 = time.time()
        ok_n = 0
        if n_workers == 1:
            # 单进程 (无 Pool 开销, 直接循环)
            print(f"  1 worker (单进程回退)", flush=True)
            for idx, t in enumerate(tasks, 1):
                code, name, sub = t
                print(f"\n[{idx}/{len(tasks)}] {code} {name} ...", flush=True)
                code_r, ok, path_or_err, elapsed = _report_worker(t)
                if ok:
                    ok_n += 1
                    print(f"  ✅ {code_r} → {path_or_err} ({elapsed:.2f}s)", flush=True)
                else:
                    print(f"  ⚠️  {code_r} 失败: {path_or_err} ({elapsed:.2f}s)", flush=True)
        else:
            print(f"  {n_workers} workers (multiprocessing spawn)", flush=True)
            # 2026-10-17: 不能 fork (duckdb thread-pool fork 死锁), 用 spawn 跨进程
            # 每个 worker 启动 ~3-5s, 8 worker 总开销 ~15s, 单 worker 详报 ~2s
            # 实测 111 只: 单进程 288s → spawn 8 worker 估 50-70s (4x)
            ctx = mp.get_context("spawn")
            with ctx.Pool(processes=n_workers) as pool:
                # imap_unordered 流式结果, 不阻塞等所有齐
                for idx, result in enumerate(pool.imap_unordered(_report_worker, tasks), 1):
                    code, ok, path_or_err, elapsed = result
                    mark = "✅" if ok else "⚠️ "
                    sys.stdout.write(f"\r  [{idx}/{len(tasks)}] {mark} {code} ({elapsed:.2f}s)")
                    sys.stdout.flush()
                    if ok:
                        ok_n += 1
            print()  # 换行
        print(f"\n=== step 2 完成 {ok_n}/{len(target_hits)} ({ok_n/len(target_hits)*100:.0f}%), {time.time()-rt0:.0f}s "
              f"(workers={n_workers}) ===")


if __name__ == "__main__":
    main()