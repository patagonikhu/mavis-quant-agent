"""
跌 70%+ 距 5y 低 < 3% 股票清单 (含反弹次数)

逻辑:
1. DataStore.list_codes() 全市场股票，从本地 parquet 聚合周线粗筛
2. 筛出候选清单 (N 只)
3. tushare 拉候选清单的 daily 最新价 (今天 8-20)
4. 用 daily 最新价 重算 gap, 输出最终清单
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, ".")


def find_max_drawdown(closes, weekly_bars):
    """5y 最大回撤 (high -> low) 严格按 weekly K 线算.
    返回 max_dd (负数, 如 -0.83 = 跌 83%)"""
    if not weekly_bars or len(weekly_bars) < 2:
        return None, None, None
    max_so_far = weekly_bars[0]["close"]
    peak_i = 0
    max_dd = 0.0
    dd_idx = 0
    dd_peak = 0
    for i, bar in enumerate(weekly_bars):
        c = bar["close"]
        if c > max_so_far:
            max_so_far = c
            peak_i = i
        dd = (c - max_so_far) / max_so_far
        if dd < max_dd:
            max_dd = dd
            dd_idx = i
            dd_peak = peak_i
    return max_dd, dd_peak, dd_idx


def find_peaks(c, w=3):
    n = len(c)
    return [
        i
        for i in range(w, n - w)
        if all(c[i] > c[i - j] for j in range(1, w + 1))
        and all(c[i] > c[i + j] for j in range(1, w + 1))
    ]


def find_troughs(c, w=3):
    n = len(c)
    return [
        i
        for i in range(w, n - w)
        if all(c[i] < c[i - j] for j in range(1, w + 1))
        and all(c[i] < c[i + j] for j in range(1, w + 1))
    ]


def count_bounces(closes, threshold=0.30, window=3):
    """5y weekly 内 30%+ 反弹事件次数 (window=3 strict local min)"""
    ts = find_troughs(closes, window)
    ps = find_peaks(closes, window)
    n = 0
    for t in ts:
        for p in ps:
            if p > t and (closes[p] - closes[t]) / closes[t] >= threshold:
                n += 1
                break
    return n


def load_all_kline_5y() -> dict[str, list[dict]]:
    """1 次 SQL 拉全市场 daily K线 (动态 5.5y cutoff, 不 hardcode)。
    替代每只股票单独 read_kline (5783 次 query → 1 次 query)。
    """
    import duckdb
    df = duckdb.execute("""
        WITH max_d AS (
            SELECT MAX(STRPTIME(trade_date, '%Y%m%d')) AS d
            FROM read_parquet('data/history/daily/*.parquet')
        )
        SELECT d.ts_code, d.trade_date, d.open, d.high, d.low, d.close
        FROM read_parquet('data/history/daily/*.parquet') d, max_d m
        WHERE STRPTIME(d.trade_date, '%Y%m%d') >= m.d - INTERVAL '5.5 year'
        ORDER BY d.ts_code, d.trade_date
    """).df()
    return {
        code: g[["trade_date", "open", "high", "low", "close"]].to_dict("records")
        for code, g in df.groupby("ts_code")
    }


def main():
    ap = argparse.ArgumentParser(description="跌 70%+ 距 5y 低 < N% 股票清单")
    ap.add_argument("--gap", type=float, default=3.0, help="距 5y 低阈值 (daily 价, 默认 3)")
    ap.add_argument("--weekly-gap", type=float, default=10.0, help="粗筛 weekly 末根距 5y 低阈值 (默认 10)")
    ap.add_argument("--drop", type=float, default=70.0, help="跌幅阈值 %% 下限 (默认 70)")
    ap.add_argument("--drop-max", type=float, default=80.0, help="跌幅阈值 %% 上限 (默认 80, 排除 80%+ 异常)")
    ap.add_argument("--lookback-years", type=int, default=5, help="max_dd (5y 内最深回撤) 计算窗口 (默认 5, 可选 3)")
    ap.add_argument("--min-bounces", type=int, default=0, help="最少反弹次数 (默认 0)")
    ap.add_argument("--write-md", action="store_true", help="写 docs/oversold-watchlist.md")
    args = ap.parse_args()

    gap_th = args.gap / 100.0
    weekly_gap_th = args.weekly_gap / 100.0
    drop_th = args.drop / 100.0
    drop_max_th = args.drop_max / 100.0

    from tools.kline_store import DataStore, _to_ts_code
    from tools.kline_store import sync_incremental
    sync_incremental()
    all_codes = DataStore.list_codes()
    print(f"Loaded: {len(all_codes)} 只股票 (本地历史库)")

    # 1 次 SQL 拉全市场 daily close (替代 5783 次 read_kline)
    all_daily = load_all_kline_5y()
    print(f"Bulk loaded: {len(all_daily)} 只 daily K线 (1 次 SQL)")

    rough_pool = []
    n_loaded = 0
    n_skipped = 0
    for code in all_codes:
        # 排除北交所
        if code.startswith(("920", "830", "8")) and len(code) == 6:
            n_skipped += 1
            continue
        # dict lookup (带后缀的 ts_code)
        daily = all_daily.get(_to_ts_code(code), [])
        if len(daily) < 250:  # 至少 1 年
            n_skipped += 1
            continue
        n_loaded += 1
        # 5y lookback (按 5y * 245 交易日 ≈ 1225 根, 5y 实际 ≈ 1217 天)
        # 2026-08-31 改: 直接用 daily, 不合成 weekly (weekly 是 daily 的 max/min 聚合, 结果一致但代码冗余)
        if len(daily) < 250:  # 至少 1 年
            continue

        # 5y high/low + 最大回撤 用 daily (用 low/high 字段, 不用 close)
        daily_lows = [float(d["low"]) for d in daily]
        daily_highs = [float(d["high"]) for d in daily]
        daily_closes = [float(d["close"]) for d in daily]
        lo_5y = min(daily_lows)
        hi_5y = max(daily_highs)

        # 用 lookback 窗口算 max_dd (5y 或 3y 内最深 high→low)
        lb = args.lookback_years * 245  # 5y≈1225 个交易日
        lb_daily = daily[-lb:] if len(daily) >= lb else daily
        lb_closes = [float(d["close"]) for d in lb_daily]
        max_dd_lb, _, _ = find_max_drawdown(lb_closes, lb_daily)
        if max_dd_lb is None:
            continue

        # 跌 70%-80% 用 lookback 窗口最大回撤 (5y 内最深, 不是末根距高点)
        if max_dd_lb > -drop_th:    # 不够跌 (5y 内最深回撤不到 70%)
            continue
        if max_dd_lb < -drop_max_th:  # 跌过头 (5y 内最深回撤超 80%)
            continue

        daily_cur = float(daily[-1]["close"])
        # current_drop: 末根距 5y 高点的当前跌幅 (反弹策略 "反弹空间" 参考)
        lb_hi = max(float(d["high"]) for d in lb_daily)
        current_drop = (daily_cur - lb_hi) / lb_hi

        # 粗筛: daily 末根距 5y 低 < 10%
        if daily_cur <= lo_5y:
            continue
        daily_gap = (daily_cur - lo_5y) / lo_5y
        if daily_gap >= weekly_gap_th:
            continue

        # 反弹次数 (用 5y daily 完整数据, close 序列)
        n_b = count_bounces(daily_closes)
        if n_b < args.min_bounces:
            continue

        rough_pool.append({
            "code": code,
            "daily_cur": daily_cur,
            "daily_gap": daily_gap,
            "lo_5y": lo_5y,
            "hi_5y": hi_5y,
            "current_drop": current_drop,
            "max_dd_5y": max_dd_lb,
            "n_b": n_b,
        })

    print(f"Loaded: {n_loaded}, Skipped: {n_skipped}")
    print(f"粗筛: 跌 ≥{args.drop:.0f}% + daily 末根距 5y 低 < {args.weekly_gap:.0f}% + 反弹 ≥{args.min_bounces} = {len(rough_pool)} 只")

    if not rough_pool:
        return

    # 第二步: daily 最新价从 DataStore.get_daily_basic (本地 parquet) 读, 0 网络
    rough_codes = [r["code"] for r in rough_pool]
    print(f"读本地 daily_basic parquet ({len(rough_codes)} 只, 0 网络)...")
    daily_map = {}
    for code in rough_codes:
        try:
            db = DataStore.get_daily_basic(code)
            daily_map[code] = (db.get("close"), db.get("trade_date", "?"))
        except Exception as e:
            daily_map[code] = (None, str(e))

    candidates = []
    for r in rough_pool:
        code = r["code"]
        lo_5y = r["lo_5y"]
        cur_today, today_date = daily_map.get(code, (None, "?"))
        if cur_today is None or cur_today <= 0:
            cur = r["daily_cur"]
            cur_date = "?"
            price_source = "daily (fallback)"
        else:
            cur = cur_today
            cur_date = today_date
            price_source = "daily (parquet)"

        if cur <= lo_5y:
            continue
        daily_gap = (cur - lo_5y) / lo_5y
        if daily_gap >= gap_th:
            continue

        # 名称/行业从 stock_basic parquet 读 (本地)
        sb = DataStore.get_stock_basic(code)
        name = sb.get("name") or code
        sector = sb.get("industry") or "—"

        candidates.append({
            "code": code,
            "name": name,
            "sector": sector,
            "cur": cur,
            "lo_5y": lo_5y,
            "daily_cur": r["daily_cur"],
            "daily_gap": r["daily_gap"],
            "daily_gap_v2": daily_gap,
            "max_dd_5y": r["max_dd_5y"],
            "current_drop": r["current_drop"],
            "n_b": r["n_b"],
            "cur_date": cur_date,
            "price_source": price_source,
        })

    candidates.sort(key=lambda r: r["daily_gap"])

    print(
        f"精筛: daily 价 (parquet) 距 5y 低 < {args.gap:.0f}% = {len(candidates)} 只"
    )
    if candidates:
        as_of = candidates[0]["cur_date"]
        print(f"现价: daily 末根 {as_of} (5y low/high 来自 weekly)\n")
    else:
        print()
        return

    print(
        f"{'代码':<8} {'名称':<10} {'行业':<12} {'现价':>7} {'上周':>7} {'5y低':>7} "
        f"{'5y最大回撤':>10} {'距5y高':>8} {'今gap':>6} {'反弹':>5}"
    )
    print("-" * 100)
    for r in candidates:
        print(
            f"{r['code']:<8} {r['name']:<10} {r['sector'][:10]:<12} "
            f"{r['cur']:>7.2f} {r['daily_cur']:>7.2f} {r['lo_5y']:>7.2f} "
            f"{r['max_dd_5y'] * 100:>+9.1f}% "
            f"{r['current_drop'] * 100:>+7.1f}% "
            f"{r['daily_gap'] * 100:>+5.2f}% {r['n_b']:>4d}次"
        )

    # 写 md 报告
    if args.write_md:
        import time
        from pathlib import Path

        ts = time.strftime("%Y-%m-%d %H:%M")
        rows_md = ""
        for r in candidates:
            rows_md += (
                f"| {r['code']} | {r['name']} | {r['sector']} | "
                f"{r['cur']:.2f} | {r['daily_cur']:.2f} | {r['lo_5y']:.2f} | "
                f"{r['max_dd_5y']*100:+.1f}% | {r['current_drop']*100:+.1f}% | "
                f"{r['daily_gap']*100:+.2f}% | {r['n_b']}次 |\n"
            )

        md = f"""# 超跌观察清单 · {ts}

> **筛选条件**: 5y 最大回撤 {args.drop:.0f}–{args.drop_max:.0f}% + 距 5y 低 < {args.gap:.0f}% + 反弹 ≥ {args.min_bounces} 次  
> **数据来源**: 本地 parquet ({len(candidates)} 只通过精筛 / 全市场扫描)  
> **现价日期**: daily 末根 {candidates[0]["cur_date"]} (5y low/high 来自 weekly)

## 反弹策略说明

谷底跌 70-80% 反弹期望最好（中位 +45%），90%+ 反弹差（中位 +13%）。  
本清单内 80% 业绩下行，属于 **纯技术反弹策略，非价值投资**：

- 涨 10-20% → 跑
- 跌破谷底 10% → 砍
- 持有 1-3 个月
- 5-10 只分散，单只轻仓

## 清单 ({len(candidates)} 只)

| 代码 | 名称 | 行业 | 现价 | 上周 | 5y低 | 5y最大回撤 | 距5y高 | 今gap | 反弹 |
|---|---|---|---|---|---|---|---|---|---|
{rows_md}
"""
        out_path = Path("docs/oversold-watchlist.md")
        out_path.write_text(md, encoding="utf-8")
        print(f"\n📝 报告已写: {out_path}")


if __name__ == "__main__":
    main()
