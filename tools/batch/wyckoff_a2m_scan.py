"""
tools/batch/wyckoff_a2m_scan.py — 全市场扫描"最近 N 根 K 线 A→M 转变"

v8 (2026-10-16) 重构 (用户反馈 7 分钟太慢):
- 删 _judge_full 慢路径后, 扫全场只剩 wyckoff_judge 一个入口
- v8 直调: build_kline_features (1 次 O(N)) + scan_sub_events 全段 (1 次) + wyckoff_judge O(1) per i
- v7 走 AnalysisEngine.analyze_history 算 480 日 history → 0.07s/只 → 7 分钟
- v8 跳过 history 算, 直接算 lookback+1 个 i → ~0.005s/只 → 5812 × 30s

判定逻辑 (V13, 2026-10-29 改):
  对每只票最近 40 根 K 线 (判定窗口 n_tail=40):
  - wyckoff_judge 输出 stage (Accumulation / Markup / Distribution)
  - ① 当前 (最后) 根 stage == 'Markup'                    (必须)
  - ② 最近 40 根里 Markup 占比 < 10% (允许 ≤ 3 根)       (排除"反复 M"假突破)
  - ③ 单日涨跌 > 20% 跳过                                 (除权/复牌)
  - ④ K 线长度 ≥ 11 根
  命中后定位 trans_idx: 从右往左第一根 != M 的位置

  版本沿革:
    v11 (2026-10-17) — T-0=M + T-1..T-5 ∈ {A, ?}
    v12 (2026-10-20) — 窗口 30 根, M 占比 < 20% (允许 ≤5 根)
    v13 (2026-10-29) — 窗口 40 根, M 占比 < 10% (允许 ≤3 根)  ← 当前

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
import shutil
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


def _stk_factor_glob() -> str:
    """stk_factor parquet 的 glob 表达式 (取最近 2 个季度文件, 不硬编码季度)。

    2026-11-03 修: 原来硬编码 'data/history/stk_factor/2026Q4.parquet'
    → 季度切换 (2027Q1) 时会静默失效 (读不到最新数据)。
    改成动态取最近 2 个季度文件。
    """
    from tools.storage.store import STK_FACTOR_DIR
    files = sorted(STK_FACTOR_DIR.glob("*.parquet"))
    if not files:
        return str(STK_FACTOR_DIR / "*.parquet")
    recent = [str(f) for f in files[-2:]]
    return "[" + ", ".join(f"'{f}'" for f in recent) + "]"


def _filter_by_pe(codes: list[str], pe_max: float) -> list[str]:
    """1 次 SQL 按 stk_factor 里**每只票自己的最新 PE** 过滤 PE > pe_max 的票 (高估值剔除)

    2026-10-17 加: --exclude-pe 高价股前置过滤
    - 默认 0 = 不过滤
    - 50/80/100 分别为 适中/较严/严 三档 (跟用户 A 股估值分布对账: PE>100 占 23%)
    - PE <= 0 或 NULL (亏损 / 数据缺) 默认放行 (跟 --with-finance 互补)
    - 跟 stk_basic name 字段比 join 一下拿中文名 (但不必要, 过滤只用 ts_code)

    ⚠️ 2026-11-03 修 BUG (静默漏过滤):
      原来用 `WHERE trade_date = (SELECT MAX(trade_date) ...)`, 只查**全局最新那一天**。
      但 Tushare 的估值/股本数据是**当天陆续发布**的 —— 实测 20261103:
        sync 时 1777 只 → 2 小时后 2044 只 → 再 sync 2115 只 (基准 5571)
      → 缺失的 3794 只**根本没被检查**, 其中 **959 只 PE>60 直接漏过过滤**。
      实测后果: 恒工精密 (301261) 静态 PE=76.1, 本该被 --exclude-pe 60 排除,
      却因 20261103 没有它的数据而漏进来, 并且是本次 A→M 命中之一。
      → 扫描结果会**随上游发布进度漂移** (同一份数据, 早上跑和晚上跑命中数不同)。

      修法: 用 ROW_NUMBER() 取**每只票自己最新**的那天 (per-code latest),
      这样无论上游发到哪, 每只票都用自己最新可得的数据判 PE。

    Args:
        codes: 候选 code 列表 (6 位纯数字)
        pe_max: PE 上限 (如 100),保留 pe <= pe_max 的票

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes or pe_max <= 0:
        return codes
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH', '{c}.BJ'" for c in codes)
    glob_expr = _stk_factor_glob()
    df = duckdb.execute(f"""
        WITH latest AS (
            SELECT ts_code, pe,
                   ROW_NUMBER() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) rn
            FROM read_parquet({glob_expr})
            WHERE ts_code IN ({in_clause})
        )
        SELECT ts_code FROM latest
        WHERE rn = 1 AND pe > {pe_max} AND pe > 0
    """).fetchdf()
    expensive = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
    return [c for c in codes if c not in expensive]


def _filter_by_quality(codes: list[str], or_yoy_min: float, gm_min: float,
                       np_yoy_min: float | None = None,
                       roe_min: float | None = None,
                       gm_abs_min: float | None = None) -> list[str]:
    """1 次 SQL 按 financials 过滤:
    or_yoy > or_yoy_min AND grossprofit_margin > gm_min AND (np_yoy_min)
    AND (roe > roe_min) AND (grossprofit_margin > gm_abs_min)

    2026-10-17 改: 优先用最新季报 (2026Q2), 拿不到 fallback 2025Q4 累计
    (旧版本只用 2025Q4 累计 netprofit_yoy 滞后 1 年, 漏判 2026 已亏损的票)

    2026-10-28 加 roe_min / gm_abs_min (绝对值下限, 跟 yoy 门槛互补):
    之前的 quality 只看 yoy (同比增速), 漏掉两类票 ——
      1. 低基数假增长: 半年净利 0.5 亿 → yoy +202%, 但 ROE 只有 2.5%
      2. 微利高增速: ROE < 1% 的票, yoy 再高也没意义
    实测 (22 只 A→M 命中票): roe>=5 砍掉芯联集成/创维数字/中海油服/中国中车等 9 只;
    gm_abs>=20 砍掉中创物流(6.7%)/立中集团(10.5%)/芯联集成(12.7%)等 7 只。

    ⚠️ 语义澄清 (2026-10-28): `gm_min` 作用在 `grossprofit_margin` 上, 而这个字段是
    **毛利率水平值 (绝对 %)**, 不是毛利率同比增速。旧代码把 quality 档位门槛 (0/10/20)
    直接传给它, 导致 "strong=10" 实际含义是 "毛利率 > 10%" (而非文档写的 "毛利率增速 > 10%")。
    financials 表里没有毛利率 yoy 字段, 所以 yoy 维度只由 or_yoy / netprofit_yoy 承担。
    绝对值门槛现在由 gm_abs_min 统一管, 两者都作用于同一列, 取更严者生效。

    Args:
        codes: 候选 code 列表 (6 位纯数字)
        or_yoy_min: 营收同比最小值 (%, 如 0 / 10 / 20)
        gm_min: 毛利率水平下限 (%, 同上; 0 = 不限)
        np_yoy_min: 净利润同比最小值 (%, 默认 None = 不限, 设 0/10/20 启用)
        roe_min: ROE 绝对值下限 (%, 默认 None = 不限)
        gm_abs_min: 毛利率水平下限 (%, 默认 None = 不限; 与 gm_min 取更严者)

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes:
        return []
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH'" for c in codes)
    np_clause = f"AND netprofit_yoy > {np_yoy_min}" if np_yoy_min is not None else ""
    roe_clause = f"AND roe > {roe_min}" if roe_min is not None else ""
    # gm_min 与 gm_abs_min 都作用在 grossprofit_margin 上, 取更严的
    gm_eff = max(gm_min, gm_abs_min) if gm_abs_min is not None else gm_min
    gm_abs_clause = f"AND grossprofit_margin > {gm_eff}" if gm_eff > 0 else ""
    # 优先 2026Q2 (最新季报), 拿不到 fallback 2025Q4 累计
    df = duckdb.execute(f"""
        SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, source
        FROM (
            SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, roe, '2026Q2' AS source
            FROM read_parquet('data/history/financials/2026Q2.parquet')
            WHERE ts_code IN ({in_clause}) AND fetch_status = 'ok'
            UNION ALL
            SELECT ts_code, grossprofit_margin, or_yoy, netprofit_yoy, roe, '2025Q4' AS source
            FROM read_parquet('data/history/financials/2025Q4.parquet')
            WHERE ts_code IN ({in_clause})
              AND ts_code NOT IN (SELECT ts_code FROM read_parquet('data/history/financials/2026Q2.parquet') WHERE fetch_status = 'ok')
        )
        WHERE grossprofit_margin > {gm_min}
          AND or_yoy > {or_yoy_min}
          {np_clause}
          {roe_clause}
          {gm_abs_clause}
    """).fetchdf()
    return [r['ts_code'].split('.')[0] for r in df.to_dict('records')]


def _filter_by_leverage(codes: list[str], max_nd_ratio: float) -> list[str]:
    """1 次 SQL 按 financials 过滤杠杆: 净负债 / 总市值 > max_nd_ratio 的票剔除

    2026-10-28 加: 前面的 quality 门槛 (yoy/毛利) 完全不看负债, 导致重债票能进扫描池。
    实测 22 只命中票里, 瀚蓝环境净负债 320 亿 > 市值 224 亿 (杠杆 1.26),
    立中集团净负债 101 亿 / 市值 125 亿 (0.74) —— 这类票 PE 再低也不能算便宜。

    口径: 净负债/总市值 (不是资产负债率)。净现金票 (netdebt < 0) 的比值是负数, 一律放行。
    财务数据缺失的票也放行 (跟其他 filter 一致, 不因缺数据误杀)。

    Args:
        codes: 候选 code 列表 (6 位纯数字)
        max_nd_ratio: 净负债/市值 上限 (如 0.5 = 净负债不能超过市值的一半)

    Returns:
        过滤后 code 列表 (保留原顺序)
    """
    if not codes or max_nd_ratio <= 0:
        return codes
    in_clause = ",".join(f"'{c}.SZ', '{c}.SH'" for c in codes)
    # 净负债/市值 = (netdebt/1e8) / (total_mv/1e4), total_mv 来自 stk_factor
    df = duckdb.execute(f"""
        WITH sf AS (
            SELECT ts_code, total_mv FROM read_parquet('data/history/stk_factor/2026Q4.parquet')
            WHERE trade_date = (SELECT MAX(trade_date) FROM read_parquet('data/history/stk_factor/2026Q4.parquet'))
        ),
        fin AS (
            SELECT ts_code, netdebt FROM read_parquet('data/history/financials/2026Q2.parquet', union_by_name=true)
            WHERE ts_code IN ({in_clause}) AND fetch_status = 'ok'
            UNION ALL
            SELECT ts_code, netdebt FROM read_parquet('data/history/financials/2025Q4.parquet', union_by_name=true)
            WHERE ts_code IN ({in_clause})
              AND ts_code NOT IN (SELECT ts_code FROM read_parquet('data/history/financials/2026Q2.parquet', union_by_name=true)
                                  WHERE fetch_status = 'ok')
        )
        SELECT fin.ts_code
        FROM fin JOIN sf ON sf.ts_code = fin.ts_code
        WHERE fin.netdebt IS NOT NULL AND sf.total_mv > 0
          AND (fin.netdebt / 1e8) / (sf.total_mv / 1e4) > {max_nd_ratio}
    """).fetchdf()
    overleveraged = {r['ts_code'].split('.')[0] for r in df.to_dict('records')}
    return [c for c in codes if c not in overleveraged]


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

    # ③ wyckoff_judge O(1) per i (最近 n_tail 个)
    #    sub_events 按 i ≤ idx 过滤 (历史窗口内触发过的)
    # 2026-10-29 改: 30 → 40 根 (V13 严判窗口; 必须 >= 判定窗口, 否则算不出占比)
    n_tail = max(40, 11)
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

    # ── V13 严判 (2026-10-29 改): "最近 40 根 K 线, M 占比 < 10%, 当前 M" ──
    #   1. 当前 (最后) 根 K 线 stage == 'Markup' — 必须
    #   2. 最近 40 根 K 线里 Markup 占比 < 10% (允许 ≤ 3 根)
    #      目的: 排除"之前 M → 转 A → 再转 M"这种假反弹/震荡反复 (用户反馈)
    #   3. 单日涨跌幅 > 20% 跳过 (除权/复牌)
    # 这是"刚刚启动, 且前期没有大涨过"的强信号 (跟 v11 的 T-1..T-5 全 A 相比, 更宽容 "?" 但更严 M 占比)
    # v12 → v13 变更: 窗口 30→40 根, M 占比阈值 20%→10%
    #   (窗口拉长 = 看更长的历史; 阈值收紧 = 只认更"干净"的启动, 允许 M 根数 5→3)
    last_chg = abs(closes_tail[-1] / closes_tail[-2] - 1) if len(closes_tail) >= 2 and closes_tail[-2] > 0 else 0
    if last_chg > 0.20:
        return None
    if stages[-1] != "Markup":
        return None
    if len(stages) < 11:
        return None
    # V13 严判: 最近 40 根里 M 占比 < 10% (允许 ≤ 3 根)
    m_window_size = min(40, len(stages))
    recent_stages = stages[-m_window_size:]
    m_count = sum(1 for s in recent_stages if s == "Markup")
    m_ratio = m_count / m_window_size
    if m_ratio >= 0.10:  # 排除"反复 M"假突破
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
        "m_ratio": m_ratio,  # v12 新增: 最近 40 根里 M 占比 (返回给 md 输出)
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
def _scan_existing_reports(sub_dir: Path) -> dict:
    """扫 docs/<subdir>/ 已有的详报 md, 返回 {code: {"path": Path, "name": str}}

    文件名格式: analyze-{code6}-{中文名}.md
    code 从文件名第 2 段切 (兼容名字里有 '-' 的情况, 如 '双星-新材')

    2026-10-29 加: 支撑 --refresh 模式 (不清空, 刷新已有 + 补充新增)
    """
    out: dict = {}
    if not sub_dir.exists():
        return out
    for p in sorted(sub_dir.glob("analyze-*.md")):
        stem = p.stem                     # analyze-001219-青岛食品
        parts = stem.split("-", 2)        # ['analyze', '001219', '青岛食品']
        if len(parts) < 3:
            continue
        code = parts[1]
        if not (len(code) == 6 and code.isdigit()):
            continue
        out[code] = {"path": p, "name": parts[2]}
    return out


def _stamp_report_status(md_path: Path, status: str, hit_note: str = "") -> bool:
    """在详报文件头插入本次 A→M 扫描状态标记 (2026-10-29 加)

    status: 'hit' (本次命中) / 'stale' (曾命中, 本次未命中)
    返回是否成功写入

    做法: 在标题行 (第一行 '# ...') 后面插入一段引用块, 幂等 ——
    已有标记时先删掉旧的再插新的, 避免重复跑越积越多
    """
    if not md_path or not Path(md_path).exists():
        return False
    p = Path(md_path)
    try:
        lines = p.read_text(encoding="utf-8").split("\n")
    except Exception:
        return False

    # 1. 删掉旧的标记块 (幂等)
    cleaned = []
    skip_block = False
    for ln in lines:
        if ln.strip().startswith("<!-- A2M_STATUS"):
            skip_block = True
            continue
        if skip_block:
            if ln.strip() == "<!-- /A2M_STATUS -->":
                skip_block = False
            continue
        cleaned.append(ln)

    # 2. 插新的
    if status == "hit":
        block = [
            "<!-- A2M_STATUS hit -->",
            f"> 🟢 **本票为本次 A→M 扫描命中**{hit_note}",
            "<!-- /A2M_STATUS -->",
        ]
    else:
        block = [
            "<!-- A2M_STATUS stale -->",
            "> ⚠️ **本票本次 A→M 扫描未命中** (曾命中过, 现留档) — "
            "可能已被 PE/质量/杠杆过滤, 或威科夫阶段已离开 Markup。数据已刷新到最新交易日。",
            "<!-- /A2M_STATUS -->",
        ]

    # 3. 插到标题行之后 (第一行是 '# ...')
    insert_at = 1 if cleaned and cleaned[0].startswith("#") else 0
    out = cleaned[:insert_at] + [""] + block + cleaned[insert_at:]
    try:
        p.write_text("\n".join(out), encoding="utf-8")
        return True
    except Exception:
        return False


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


def _print_effective_conditions(args) -> None:
    """打印本次运行"实际生效"的完整条件 (2026-10-29 加)

    为什么要有这个:
      - SKILL.md 里的默认条件是手写的, 改 flag 默认值后容易忘记同步 → 文档漂移
      - 用户问"现在的条件是啥"时, 唯一可靠答案是"代码里真实生效的值"
      - 所以这里直接从 args 读, 逐条打印, 包含"关掉了哪些" (避免以为在跑但其实没跑)

    打印内容分 3 块:
      1. 扫描范围 (全市场 / watchlist)
      2. 前置过滤链 (5 步, 逐步标 ON/OFF + 实际门槛)
      3. V13 严判条件 (硬编码在 wyckoff_judge 里, 这里同步展示)
    """
    # --- 扫描范围 ---
    if args.watchlist_only:
        scope_desc = f"watchlist (类型: {args.watchlist_types})"
    else:
        scope_desc = "全市场"

    # --- 前置过滤链 ---
    # step1 ST
    if args.include_st:
        st_on, st_desc = False, "❌ OFF (--include-st 显式包含 ST/*ST)"
    elif args.exclude_st:
        st_on, st_desc = True, "✅ ON  排除 ST/*ST"
    else:
        st_on, st_desc = False, "❌ OFF (不排除 ST)"

    # step2 PE
    if args.exclude_pe > 0:
        pe_on, pe_desc = True, f"✅ ON  PE > {args.exclude_pe:.0f} 排除"
    else:
        pe_on, pe_desc = False, "❌ OFF (--exclude-pe 0, 不限 PE)"

    # step3 quality
    quality_thr = {"basic": 0, "strong": 10, "elite": 20}.get(args.with_quality, 0)
    if args.min_growth is not None:
        quality_thr = args.min_growth
        growth_note = "(--min-growth 覆盖档位)"
    else:
        growth_note = "(档位默认值)"
    if args.with_quality != "off":
        q_parts = [f"营收yoy>{quality_thr:g}%", f"净利yoy>{quality_thr:g}%"]
        if args.min_roe > 0:
            q_parts.append(f"ROE>{args.min_roe:g}%")
        if args.min_gross_margin > 0:
            q_parts.append(f"毛利率>{args.min_gross_margin:g}%")
        q_on, q_desc = True, "✅ ON  " + " + ".join(q_parts)
    else:
        q_on, q_desc = False, "❌ OFF (--with-quality off)"

    # step4 杠杆
    if args.max_net_debt_ratio > 0:
        lv_on, lv_desc = True, f"✅ ON  净负债/市值 < {args.max_net_debt_ratio:g}"
    else:
        lv_on, lv_desc = False, "❌ OFF (--max-net-debt-ratio 0, 不限杠杆)"

    # step5 finance (亏损股)
    fin_on, fin_desc = (True, "✅ ON  剔除亏损股 (PE<=0)") if args.with_finance \
        else (False, "❌ OFF (--with-finance 未开)")

    # rsi (已弃用 flag, 但仍可能开着)
    rsi_on = bool(args.with_rsi)

    print("\n" + "=" * 72)
    print("  本次运行生效条件 (Effective Conditions)")
    print("=" * 72)
    print(f"\n  【扫描范围】")
    print(f"    scope            : {scope_desc}")
    print(f"    lookback         : 最近 {args.lookback} 根 K 线")
    print(f"    V13 判定窗口     : 固定 40 根 (硬编码, 不可调)")

    print(f"\n  【前置过滤链】(按执行顺序)")
    print(f"    1. exclude-st        : {st_desc}")
    print(f"    2. exclude-pe        : {pe_desc}")
    print(f"    3. with-quality      : {q_desc}")
    if args.with_quality != "off":
        print(f"         └ 档位={args.with_quality}, yoy 门槛={quality_thr:g}% {growth_note}")
        print(f"         └ ⚠️ 毛利率门槛作用在 grossprofit_margin (水平值/绝对%), 不是毛利率同比增速")
    print(f"    4. max-net-debt-ratio: {lv_desc}")
    print(f"    5. with-finance      : {fin_desc}")
    print(f"    + with-rsi          : {'⚠️ 已启用 (2026-10-17 起该 flag 判定逻辑已弃用)' if rsi_on else '❌ OFF'}")

    print(f"\n  【V13 严判】(硬编码, 无 flag 可调)")
    print(f"    ① 当前 K 线 stage == Markup            (必须)")
    print(f"    ② 最近 40 根 K 线 M 占比 < 10%         (允许 ≤3 根, 排除'反复 M'假突破)")
    print(f"    ③ 单日涨跌幅 > 20% 跳过                 (除权/复牌)")
    print(f"    ④ K 线长度 ≥ 11 根")

    print(f"\n  【输出】")
    # 注意: --write-md / --full-report 的 default=True, 真正的关闭开关是 --no-write-md / --no-full-report
    write_md_on = args.write_md and not args.no_write_md
    full_report_on = args.full_report and not args.no_full_report
    print(f"    汇总 md             : {args.output if write_md_on else '❌ 不落盘 (--no-write-md)'}")
    print(f"    详报                : {'✅ 跑 (前 %d 只)' % args.report_top if full_report_on else '❌ 不跑 (--no-full-report)'}")
    if args.refresh:
        print(f"    详报模式            : 🔄 --refresh (默认, 不清空!)")
        print(f"                         A. 刷新 = 已有 且 本次命中 → 重新生成")
        print(f"                         B. 补充 = 没有 且 本次命中 → 新生成")
        if args.keep_stale:
            print(f"                         C. 留档 = 已有 但本次未命中 → 不刷新, 只标注 ⚠️")
        else:
            print(f"                         C. 留档 = 已有 但本次未命中 → 也刷新数据 + 标注 ⚠️")
        if args.stale_only:
            print(f"                         ⚠️ --stale-only: 只跑 C 类, 不跑本次命中")
    elif full_report_on:
        print(f"    详报目录            : docs/{args.report_subdir}/"
              f" {'(保留旧文件)' if args.no_clean else '(先清空旧文件)'}")
    print(f"    详报 workers        : {args.workers if args.workers else 'os.cpu_count()'}")
    print("=" * 72 + "\n")


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
                    help="命中后逐只跑 /t-analyze 10 section 详报 (默认 True, 2026-10-16 改)")
    ap.add_argument("--no-full-report", action="store_true", help="不跑详报 (只输出扫描汇总)")
    ap.add_argument("--report-top", type=int, default=100,
                    help="--full-report 时只详报前 N 只 (默认 100, 0=全部)")
    ap.add_argument("--report-subdir", default="wyckoff-a2m",
                    help="详报输出子目录 (默认 wyckoff-a2m, 跳过 watchlist 分流, 不污染自选)")
    ap.add_argument("--no-clean", action="store_true",
                    help="不清理 docs/<subdir> 已有 md (等价于 --refresh 的 A+B 类, 但 C 类完全不碰)")
    ap.add_argument("--clean", action="store_true",
                    help="显式清空 docs/<subdir> 旧 md (2026-11-02 加: 清空从默认改成 opt-in,\n"
                         "因为默认 unlink() 不可恢复, 且历史命中票是复盘素材)")
    ap.add_argument("--refresh", action="store_true", default=True,
                    help="刷新模式 (2026-10-29 加, 2026-11-02 改默认开): 不清空目录, 而是\n"
                         "  A. 刷新 = 目录里已有 且 本次仍命中 → 重新生成 (数据更新到最新交易日)\n"
                         "  B. 补充 = 目录里没有 且 本次命中   → 新生成\n"
                         "  C. 留档 = 目录里已有 但本次未命中 → 也刷新数据, 但文件头标 '⚠️ 本次未命中'\n"
                         "  最终目录 = 旧文件 ∪ 本次命中, 不删任何历史票\n"
                         "  (与 --no-clean 的区别: --no-clean 是完全不碰旧文件, 数据永远停在生成那天)\n"
                         "  2026-11-02 起这是默认行为 (清空改成 --clean opt-in)")
    ap.add_argument("--keep-stale", action="store_true",
                    help="--refresh 时, C 类 (曾命中但本次未命中) 不刷新数据, 只在文件头标注状态\n"
                         "  默认会刷新 (数据旧比多跑几只票更坑)")
    ap.add_argument("--stale-only", action="store_true",
                    help="只刷新 C 类 (历史留档), 不跑本次命中票的详报 (配合 --refresh)")
    ap.add_argument("--with-finance", action="store_true",
                    help="加 finance 过滤: 剔除亏损股 (PE <= 0 视为亏损), 0 网络 (走 stk_factor parquet)")
    ap.add_argument("--with-quality", choices=["off", "basic", "strong", "elite"], default="basic",
                    help="quality 过滤 (财务质量前置, 减少扫描票数, 2026-10-19 改默认 basic):\n"
                         "off=不限 / basic=营收yoy>0 + 净利yoy>0 (默认, 2026-10-28 5→0 放宽) / "
                         "strong=营收yoy>10 + 净利yoy>10 + 毛利率>10 / "
                         "elite=营收yoy>20 + 净利yoy>20 + 毛利率>20\n"
                         "⚠️ 2026-10-28 澄清: 毛利率门槛作用在 grossprofit_margin (水平值, 绝对%%),\n"
                         "  不是毛利率同比增速 (financials 表没有毛利率 yoy 字段)。\n"
                         "  绝对值门槛建议用 --min-gross-margin 单独调, 别跟本档位混。\n"
                         "  营收/净利 yoy 门槛可用 --min-growth 单独覆盖。")
    ap.add_argument("--with-rsi", action="store_true",
                    help="(2026-10-17 弃用) 加 RSI 超卖过滤 — 现在 v10 改 A→M 转变判定, 不再需要 RSI")
    ap.add_argument("--exclude-st", action="store_true", default=True,
                    help="排除 ST/*ST 股 (退市风险, 默认 True, 2026-10-17 加)")
    ap.add_argument("--include-st", action="store_true",
                    help="包含 ST/*ST 股 (反旗, 默认已排除, 仅当显式指定时启用)")
    ap.add_argument("--exclude-pe", type=float, default=60.0,
                    help="前置 PE 过滤上限 (排除 pe > N 的高估值票, 2026-10-28 改默认 80→40→60):\n"
                         "  0   = 不限\n"
                         "  30  = 严 (剩约 1025 票)\n"
                         "  40  = 较严 (剩约 1210 票)\n"
                         "  60  = 推荐默认 (剩约 441 票, 叠加 basic 门槛后)\n"
                         "  80  = 较松 (剩约 1590 票, 最初默认)\n"
                         "  100 = 很松 (剩约 1700 票)\n"
                         "全市场 PE 中位 38.3, P75 85.4. 40 会砍掉芯联集成(176.8)/\n"
                         "  晶华新材(91.7)/创维数字(46.7), 但也误伤 PE 46.7 的科远智慧等;\n"
                         "  放宽到 60 后实测 10 只命中, 无一只差票\n"
                         "底层走 stk_factor 最新 trade_date + pe > 0 双向校验, 跟 --with-finance 互补")
    ap.add_argument("--min-growth", type=float, default=None,
                    help="营收/净利 yoy 门槛 %%, 覆盖 --with-quality 档位门槛 (2026-10-28 加)\n"
                         "默认 None = 用档位门槛 (basic=0 / strong=10 / elite=20)\n"
                         "  0  = 不限 (营收/净利 yoy 只要 > 0, 当前默认)\n"
                         "  5  = 基本持平也要增长 5%%\n"
                         "  10 = 较严\n"
                         "调参轨迹: 0 → 10 → 5 → 0.\n"
                         "  10 太严 (误伤基蛋生物 营收 yoy 8.3%% 但净利 +69.4%%);\n"
                         "  5 会砍掉零增长股 (云南白药 PE 18.5 但净利 yoy 1.9%%);\n"
                         "  放宽回 0 后实测 10 只命中, 新增 5 只全是 PE 便宜的稳健票, 无差票")
    ap.add_argument("--min-roe", type=float, default=5.0,
                    help="ROE 绝对值下限 %%, 0 = 不限 (2026-10-28 加, 默认 5)\n"
                         "跟 quality 的 yoy 门槛互补: yoy 高不代表 ROE 高 (低基数假增长)\n"
                         "全市场 ROE 中位仅 2.29%%, P75 4.89%%, 所以 5%% 已砍掉约一半\n"
                         "实测 22 只命中票: ROE<5 的砍掉芯联集成(2.1) 创维数字(2.5) 中海油服(4.3)\n"
                         "  中国中车(4.6) 永创智能(4.7) 新光药业(4.8) 金鹰重工(3.6) 等 9 只")
    ap.add_argument("--min-gross-margin", type=float, default=20.0,
                    help="毛利率绝对值下限 %%, 0 = 不限 (2026-10-28 加, 默认 20)\n"
                         "跟 quality 的 gm yoy 门槛互补: quality 看的是毛利率同比增速, 不是水平值\n"
                         "全市场毛利率中位 24.28%%, P25 14.95%%\n"
                         "实测 22 只命中票: 砍掉中创物流(6.7) 立中集团(10.5) 芯联集成(12.7)\n"
                         "  创维数字(15.1) 晶华新材(18.1) 中海油服(18.5) 等 7 只")
    ap.add_argument("--max-net-debt-ratio", type=float, default=0.5,
                    help="净负债/总市值 上限, 0 = 不限 (2026-10-28 加, 默认 0.5)\n"
                         "净现金票 (netdebt<0) 放行; 财务数据缺失也放行\n"
                         "全市场 P90 0.48, 所以 0.5 大约砍掉 10%%\n"
                         "实测 22 只命中票: 砍掉瀚蓝环境(净负债 320 亿 > 市值 224 亿, 比值 1.26)\n"
                         "  和立中集团(101 亿 / 125 亿 = 0.74) —— PE 低但重债, 不算便宜")
    ap.add_argument("--workers", type=int, default=0,
                    help="详报多进程 worker 数 (step 2 用, 跨 GIL 解锁; 默认 0 = os.cpu_count(); 设 1 强制单进程)")
    ap.add_argument("--bench-report", nargs="?", const=12, type=int,
                    help="跳过扫描, 只跑 N 个固定代码详报测试不同 workers (用于测 mp 加速比)")
    args = ap.parse_args()

    # 2026-10-29 加: 每次跑先把"实际生效的完整条件"打出来
    # 目的: 避免靠记忆/文档猜条件 —— 文档会过期, 只有这里打印的是真实生效值
    _print_effective_conditions(args)

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

    # 2026-10-20 加: 漏斗追踪, 用于 md 头部展示过滤各步
    funnel = []  # [(step_name, params, before_n, after_n), ...]
    funnel.append(("原始候选 (scope={})".format(scope), "—", len(codes), len(codes)))

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
        before_n = len(codes)
        kept = _filter_by_st(codes)
        print(f"  exclude-st (ST/*ST 退市风险): {before_n} → {len(kept)} 票", flush=True)
        funnel.append(("step1 exclude-st", "ST/*ST 退市风险", before_n, len(kept)))
        codes = kept
        if not codes:
            print("❌ ST 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-17 加: --exclude-pe 高估值前置过滤 (PE > N 默认 0 = 不过滤)
    if args.exclude_pe > 0:
        before_n = len(codes)
        kept = _filter_by_pe(codes, args.exclude_pe)
        print(f"  exclude-pe (PE>{args.exclude_pe:.0f} 高估值): {before_n} → {len(kept)} 票", flush=True)
        funnel.append(("step2 exclude-pe", f"PE>{args.exclude_pe:.0f}", before_n, len(kept)))
        codes = kept
        if not codes:
            print(f"❌ PE>{args.exclude_pe:.0f} 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-16: --with-quality 前置过滤 (财务质量, 减少扫描票数)
    # 2026-10-28 改: 叠加 ROE / 毛利率绝对值下限 (--min-roe / --min-gross-margin)
    # 2026-10-28 再改: basic 档营收/净利 yoy 门槛 0 → 10 (--min-growth 可覆盖)
    if args.with_quality != "off":
        # 档位门槛: basic=0 (2026-10-28 5→0 放宽) / strong=10 / elite=20
        quality_thr = {"basic": 0, "strong": 10, "elite": 20}[args.with_quality]
        # --min-growth 显式指定时覆盖档位门槛 (0 = 不限)
        if args.min_growth is not None:
            quality_thr = args.min_growth
        before_n = len(codes)
        kept_codes = _filter_by_quality(codes, or_yoy_min=quality_thr,
                                         gm_min=quality_thr, np_yoy_min=quality_thr,
                                         roe_min=args.min_roe,
                                         gm_abs_min=args.min_gross_margin)
        extra = []
        if args.min_roe is not None:
            extra.append(f"ROE>{args.min_roe:.0f}%")
        if args.min_gross_margin is not None:
            extra.append(f"毛利>{args.min_gross_margin:.0f}%")
        extra_s = " + " + " + ".join(extra) if extra else ""
        print(f"  quality={args.with_quality} (营收yoy>{quality_thr}% + 净利yoy>{quality_thr}%"
              f"{extra_s}): {before_n} → {len(kept_codes)} 票", flush=True)
        funnel.append((f"step3 with-quality={args.with_quality}",
                       f"营收yoy/净利yoy>{quality_thr}%" + (f" + {' + '.join(extra)}" if extra else ""),
                       before_n, len(kept_codes)))
        codes = kept_codes
        if not codes:
            print("❌ 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-28 加: --max-net-debt-ratio 杠杆过滤 (净负债/市值)
    if args.max_net_debt_ratio > 0:
        before_n = len(codes)
        kept = _filter_by_leverage(codes, args.max_net_debt_ratio)
        print(f"  max-net-debt-ratio (净负债/市值>{args.max_net_debt_ratio:.2f}): "
              f"{before_n} → {len(kept)} 票", flush=True)
        funnel.append((f"step4 max-net-debt-ratio", f"净负债/市值<{args.max_net_debt_ratio:.2f}",
                       before_n, len(kept)))
        codes = kept
        if not codes:
            print("❌ 杠杆过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-16: --with-rsi 超卖过滤 (在 quality 后, 进一步缩量)
    if args.with_rsi and codes:
        rsi_kept = _filter_by_rsi(codes, rsi6_max=30, rsi12_max=35)
        print(f"  rsi filter (RSI6<30 AND RSI12<35): {len(codes)} → {len(rsi_kept)} 票", flush=True)
        funnel.append(("step3b with-rsi", "RSI6<30 AND RSI12<35", len(codes), len(rsi_kept)))
        codes = rsi_kept
        if not codes:
            print("❌ RSI 过滤后无代码可跑", file=sys.stderr)
            sys.exit(1)

    # 2026-10-20 加: 漏斗最终候选 = 参与 wyckoff_judge 的票数
    funnel.append(("step4 wyckoff_a2m 扫描", f"lookback={args.lookback}", len(codes), len(codes)))

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
        funnel.append(("step5 with-finance", "PE∈(0,500]", before, len(hits)))
        print(f"\n=== finance 过滤: {before} → {len(hits)} 只 (剔除 {before - len(hits)} 只亏损/异常) ===\n")

    # 渲染输出
    # 2026-10-29 加: --refresh 需要 existing (目录里已有的详报) 和 hit_codes (本次命中)
    #   这两个在下面 step 2 也会用, 提前算好避免重复扫目录
    sub_dir = Path(f"docs/{args.report_subdir}")
    existing = _scan_existing_reports(sub_dir) if args.refresh else {}
    hit_codes = {r['code'] for r in hits}
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
    lines.append(f"> 数据源: AnalysisEngine + compute_factor_history (跟 /t-analyze 同源, 0 网络)")
    lines.append(f"> 判定: **V13 严判** — 当前 M + 最近 40 根 K 线 M 占比 < 10% (排除\"反复 M 假突破\")\n")

    # 2026-10-29 加: 把本次生效条件写进 md 头部 (避免事后看报告猜当时用的什么门槛)
    lines.append("## 本次生效条件\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append(f"| scope | {scope} |")
    lines.append(f"| lookback | 最近 {args.lookback} 根 K 线 |")
    lines.append(f"| ① exclude-st | {'ON (排除 ST/*ST)' if (args.exclude_st and not args.include_st) else 'OFF'} |")
    lines.append(f"| ② exclude-pe | {f'ON (PE>{args.exclude_pe:.0f} 排除)' if args.exclude_pe > 0 else 'OFF (不限)'} |")
    if args.with_quality != "off":
        _thr = {"basic": 0, "strong": 10, "elite": 20}.get(args.with_quality, 0)
        if args.min_growth is not None:
            _thr = args.min_growth
        _q = f"ON (营收yoy>{_thr:g}% + 净利yoy>{_thr:g}%"
        if args.min_roe > 0:
            _q += f" + ROE>{args.min_roe:g}%"
        if args.min_gross_margin > 0:
            _q += f" + 毛利率>{args.min_gross_margin:g}%"
        _q += ")"
    else:
        _q = "OFF"
    lines.append(f"| ③ with-quality | {_q} |")
    lines.append(f"| ④ max-net-debt-ratio | {f'ON (净负债/市值<{args.max_net_debt_ratio:g})' if args.max_net_debt_ratio > 0 else 'OFF (不限)'} |")
    lines.append(f"| ⑤ with-finance | {'ON (剔除亏损股)' if args.with_finance else 'OFF'} |")
    lines.append(f"| V13 严判 | 当前 M + 最近 40 根 M 占比<10% + 单日涨跌<20% + K线≥11根 |")
    lines.append("")

    # 2026-10-20 加: 过滤漏斗表 (从 funnel 列表渲染)
    lines.append("## 过滤漏斗\n")
    lines.append("| 步骤 | 过滤 | 阈值 | 前 → 后 | 剩余 |")
    lines.append("|---|---|---|---|---|")
    for idx, (step_name, params, before_n, after_n) in enumerate(funnel):
        lines.append(f"| {step_name} | {params or '—'} | — | {before_n} → {after_n} | {after_n} |")
    lines.append(f"| **A→M 命中** | V13 严判 | lookback={args.lookback} | {len(codes)} → {len(hits)} | **{len(hits)}** |\n")

    # 2026-10-29 加: --refresh 模式下, 汇总 md 列出"历史留档" (曾命中但本次未命中)
    if args.refresh and existing:
        stale_codes = sorted(c for c in existing if c not in hit_codes)
        if stale_codes:
            lines.append("## 历史留档 (曾命中, 本次未命中)\n")
            lines.append(f"> 目录里有但本次扫描没命中的票 —— 可能已被 PE/质量/杠杆过滤, 或威科夫阶段已离开 Markup.")
            lines.append(f"> `--refresh` 下这些票的详报**会一并刷新到最新交易日**, 文件头标 ⚠️。\n")
            lines.append("| 代码 | 名称 | 状态 |")
            lines.append("|---|---|---|")
            for c in stale_codes:
                lines.append(f"| `{c}` | {existing[c]['name']} | ⚠️ 本次未命中 (留档) |")
            lines.append("")

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
            # v13: trans_stages 现在是 40 根 (判定窗口), md 只输出最近 lookback+1 根
            stages_str = "/".join(s[0] for s in r['trans_stages'][-(args.lookback + 1):])
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

    # step 2: 命中后逐只跑 /t-analyze 10 section 详报
    if args.no_full_report:
        args.full_report = False
    if args.no_write_md:
        args.write_md = False

    # 2026-10-29 加: --refresh 模式 (不清空, 刷新已有 + 补充新增 + 留档标注)
    #   A. 刷新 = 目录里已有 且 本次仍命中 → 重新生成
    #   B. 补充 = 目录里没有 且 本次命中   → 新生成
    #   C. 留档 = 目录里已有 但本次未命中 → 也刷新数据, 文件头标 "⚠️ 本次未命中"
    if args.refresh:
        # 构造任务表: (code, name, subdir, status, hit_note)
        tasks: list = []
        if not args.stale_only:
            for r in hits:
                code = r['code']
                nm = basic.get(code, (code, "—"))[0]
                # 涨幅 + 阶段序列 (字段跟 md 表格渲染保持一致)
                try:
                    _chg = (r['last_close'] / r['trans_close'] - 1) * 100
                except Exception:
                    _chg = 0.0
                try:
                    _sg = "/".join(s[0] for s in r['trans_stages'][-(args.lookback + 1):])
                except Exception:
                    _sg = ""
                note = f" — 转变日 {r.get('trans_date', '?')}, 涨幅 {_chg:+.1f}%, 阶段 {_sg}"
                tasks.append((code, nm, args.report_subdir, "hit", note))
        # C 类: 曾命中, 本次未命中
        stale_list = [c for c in existing if c not in hit_codes]
        if not args.keep_stale:
            for code in stale_list:
                nm = existing[code]["name"]
                tasks.append((code, nm, args.report_subdir, "stale", ""))
        n_hit_tasks = len([t for t in tasks if t[3] == "hit"])
        n_stale_tasks = len([t for t in tasks if t[3] == "stale"])
        print(f"\n=== step 2 [--refresh]: 刷新/生成详报 (→ docs/{args.report_subdir}/) ===")
        print(f"  A. 本次命中 (刷新+生成): {n_hit_tasks} 只")
        print(f"  C. 历史留档 (本次未命中): {n_stale_tasks} 只"
              + ("  [已跳过刷新, 只标注]" if args.keep_stale else "  [会刷新数据]"))
        print(f"  目录现有: {len(existing)} 份 → 跑完预计: {len(existing) + len(hit_codes - set(existing))} 份")
        if not tasks:
            print("  无任务可跑")
            return
    elif args.full_report and hits:
        from tools.batch.t_analyze_one import process_one as t_analyze_process_one
        target_hits = hits[:args.report_top] if args.report_top > 0 else hits
        # 2026-11-02: 清空改成 --clean opt-in (默认 --refresh 不清空)
        #   原因: 旧逻辑用 p.unlink() 直接删, 不进回收站, 且 docs/ 不入 git → 删了就没了.
        #   历史命中票是复盘素材, 默认必须保留.
        if sub_dir.exists() and args.clean and not args.no_clean:
            old_mds = list(sub_dir.glob("*.md"))
            # 2026-11-02: 改成移到回收站, 不是 unlink() — 万一清错还能捞回来
            trash = Path.home() / ".Trash" / f"mavis-a2m-{args.report_subdir}-{int(time.time())}"
            trash.mkdir(parents=True, exist_ok=True)
            for p in old_mds:
                try:
                    shutil.move(str(p), str(trash / p.name))
                except Exception:
                    p.unlink()
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只 → docs/{args.report_subdir}/) ===")
            print(f"  ⚠️ 清空旧 md: {len(old_mds)} 份 (--clean 显式指定, 已移到 {trash})")
        else:
            print(f"\n=== step 2: 跑 /t-analyze 详报 ({len(target_hits)} 只 → docs/{args.report_subdir}/) ===")
            if args.clean:
                print(f"  子目录不存在, 无需清空")
            elif args.no_clean:
                print(f"  --no-clean: 保留历史详报")
            else:
                print(f"  保留历史详报 (默认不清理, 加 --clean 才清空)")
        tasks = [(r['code'], basic.get(r['code'], (r['code'], "—"))[0], args.report_subdir)
                 for r in target_hits]
    else:
        tasks = []

    if tasks:
        # 2026-10-17 加: multiprocessing 多进程跑详报 (跨 GIL, 4-8x 加速)
        # 默认 workers = os.cpu_count(), 设 1 单进程回退
        n_workers = args.workers if args.workers > 0 else (os.cpu_count() or 4)
        n_workers = min(n_workers, len(tasks))  # 不超过任务数
        # --refresh 模式下 task 是 5 元组, 兼容旧的 3 元组
        if args.refresh:
            _tasks_wrapped = tasks
        else:
            _tasks_wrapped = [(t[0], t[1], t[2], "hit", "") for t in tasks]
        rt0 = time.time()
        ok_n = 0
        stamped_hit = 0
        stamped_stale = 0
        if n_workers == 1:
            # 单进程 (无 Pool 开销, 直接循环)
            print(f"  1 worker (单进程回退)", flush=True)
            for idx, t in enumerate(_tasks_wrapped, 1):
                code, name, sub, st, note = t
                print(f"\n[{idx}/{len(_tasks_wrapped)}] {code} {name} ...", flush=True)
                code_r, ok, path_or_err, elapsed = _report_worker((code, name, sub))
                if ok:
                    ok_n += 1
                    if st == "hit":
                        stamped_hit += _stamp_report_status(Path(path_or_err), "hit", note)
                    else:
                        stamped_stale += _stamp_report_status(Path(path_or_err), "stale")
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
                for idx, result in enumerate(pool.imap_unordered(_report_worker,
                                                                [(t[0], t[1], t[2]) for t in _tasks_wrapped]), 1):
                    code, ok, path_or_err, elapsed = result
                    mark = "✅" if ok else "⚠️ "
                    sys.stdout.write(f"\r  [{idx}/{len(_tasks_wrapped)}] {mark} {code} ({elapsed:.2f}s)")
                    sys.stdout.flush()
                    if ok:
                        ok_n += 1
                        # 状态标记在主进程打 (worker 只负责生成 md)
                        t = _tasks_wrapped[idx - 1] if idx - 1 < len(_tasks_wrapped) else None
                        # imap_unordered 顺序不保证, 按 code 反查
                        t = next((x for x in _tasks_wrapped if x[0] == code), None)
                        if t:
                            if t[3] == "hit":
                                stamped_hit += _stamp_report_status(Path(path_or_err), "hit", t[4])
                            else:
                                stamped_stale += _stamp_report_status(Path(path_or_err), "stale")
            print()  # 换行
        print(f"\n=== step 2 完成 {ok_n}/{len(_tasks_wrapped)} ({ok_n/len(_tasks_wrapped)*100:.0f}%), {time.time()-rt0:.0f}s "
              f"(workers={n_workers}) ===")
        if args.refresh:
            print(f"  状态标记: 命中 {stamped_hit} 份 / 留档 {stamped_stale} 份")


if __name__ == "__main__":
    main()