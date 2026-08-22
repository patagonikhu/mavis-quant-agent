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

WATCHLIST = Path("data/watchlist_oversold.json")


def get_tushare_daily_close(code: str):
    """tushare 拉 daily 最新价 (今天 8-19/8-20)"""
    try:
        from tools.fetch.tushare_fetcher import get_daily

        data, _ = get_daily(code, limit=1)
        if data and len(data) > 0:
            row = data[0]
            return float(row.get("close", 0)), row.get("trade_date", "?")
    except Exception as e:
        return None, str(e)
    return None, "empty"


def get_year_profit(code: str):
    """tushare 拉 2025A 和 2024A 净利, 判断今年 vs 去年
    返回 (status, profit_25, profit_24, change_pct)
    status: 'ok' / 'fail'
    """
    try:
        from tools.fetch.tushare_fetcher import get_income

        # 2025A (2025 年报)
        inc_25, _ = get_income(code, period="20251231")
        # 2024A
        inc_24, _ = get_income(code, period="20241231")
        if not inc_25 or not inc_24:
            return "fail", None, None, None
        np_25 = float(inc_25.get("n_income", 0) or 0)
        np_24 = float(inc_24.get("n_income", 0) or 0)
        if np_24 <= 0:
            change_pct = None  # 去年亏/0, 没法算同比
        else:
            change_pct = (np_25 - np_24) / abs(np_24) * 100
        return "ok", np_25, np_24, change_pct
    except Exception as e:
        return "fail", None, None, str(e)


def fetch_daily_concurrent(codes, max_workers=8):
    """并发拉 daily 最新价. 返回 {code: (price, date) or (None, 'error')}"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(get_tushare_daily_close, code): code for code in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                results[code] = fut.result()
            except Exception as e:
                results[code] = (None, str(e))
    return results


def fetch_profit_concurrent(codes, max_workers=8):
    """并发拉 2025A/2024A 净利. 返回 {code: (status, np_25, np_24, change_pct)}"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(get_year_profit, code): code for code in codes}
        for fut in as_completed(futures):
            code = futures[fut]
            try:
                results[code] = fut.result()
            except Exception as e:
                results[code] = ("fail", None, None, str(e))
    return results


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


def load_weekly(code: str) -> list[dict]:
    """从本地历史库读日线并聚合成周线。"""
    from tools.data_store import DataStore
    from tools.fetch.data_fetcher import _synthesize_weekly
    kline = DataStore.get_kline(code, limit=1300)  # ~5年
    return _synthesize_weekly(kline)


def main():
    ap = argparse.ArgumentParser(description="跌 70%+ 距 5y 低 < N% 股票清单")
    ap.add_argument("--gap", type=float, default=3.0, help="距 5y 低阈值 (daily 价, 默认 3)")
    ap.add_argument("--weekly-gap", type=float, default=10.0, help="粗筛 weekly 末根距 5y 低阈值 (默认 10)")
    ap.add_argument("--drop", type=float, default=70.0, help="跌幅阈值 %% 下限 (默认 70)")
    ap.add_argument("--drop-max", type=float, default=80.0, help="跌幅阈值 %% 上限 (默认 80, 排除 80%+ 异常)")
    ap.add_argument("--lookback-years", type=int, default=5, help="max_drop 计算窗口 (默认 5, 可选 3)")
    ap.add_argument("--min-bounces", type=int, default=0, help="最少反弹次数 (默认 0)")
    ap.add_argument("--skip-tushare", action="store_true", help="跳过 tushare 拉 daily (只用 weekly 末根)")
    args = ap.parse_args()

    if not WATCHLIST.exists():
        print(f"⚠️ {WATCHLIST} 不存在，将扫描全市场")
        pass
    else:
        pass  # watchlist_oversold.json 已废弃，改走 DataStore.list_codes()

    gap_th = args.gap / 100.0
    weekly_gap_th = args.weekly_gap / 100.0
    drop_th = args.drop / 100.0
    drop_max_th = args.drop_max / 100.0
    lookback_weeks = args.lookback_years * 52  # 5y=260 周

    from tools.data_store import DataStore
    from tools.history_sync import sync_incremental
    sync_incremental()
    all_codes = DataStore.list_codes()
    print(f"Loaded: {len(all_codes)} 只股票 (本地历史库)")

    rough_pool = []
    n_loaded = 0
    n_skipped = 0
    for code in all_codes:
        # 排除北交所
        if code.startswith(("920", "830", "8")) and len(code) == 6:
            n_skipped += 1
            continue
        weekly = load_weekly(code)
        if len(weekly) < 60:
            n_skipped += 1
            continue
        n_loaded += 1

        # 5y high/low + 最大回撤 用 weekly
        weekly_closes = [float(w["close"]) for w in weekly]
        lo_5y = min(weekly_closes)
        hi_5y = max(weekly_closes)

        # 用 lookback 窗口算 max_drop (5y 或 3y)
        lb_weekly = weekly[-lookback_weeks:] if len(weekly) >= lookback_weeks else weekly
        lb_closes = [float(w["close"]) for w in lb_weekly]
        lb_lo = min(lb_closes)
        lb_hi = max(lb_closes)
        # lookback 窗口最大回撤
        max_dd_lb, _, _ = find_max_drawdown(lb_closes, lb_weekly)
        if max_dd_lb is None:
            continue

        # 跌 70%-80% 用 lookback 窗口
        weekly_cur = float(weekly[-1]["close"])
        # 用 weekly_cur vs lb_hi (lookback 窗口高) 算当前跌幅
        max_drop = (weekly_cur - lb_hi) / lb_hi
        if max_drop > -drop_th:  # 不够跌
            continue
        if max_drop < -drop_max_th:  # 跌过头 (超 80%)
            continue

        # 粗筛: weekly 末根距 5y 低 < 10%
        if weekly_cur <= lo_5y:
            continue
        weekly_gap = (weekly_cur - lo_5y) / lo_5y
        if weekly_gap >= weekly_gap_th:
            continue

        # 反弹次数 (用 5y weekly 完整数据)
        n_b = count_bounces(weekly_closes)
        if n_b < args.min_bounces:
            continue

        rough_pool.append({
            "code": code,
            "name": DataStore.get_stock_basic(code).get("name", code),
            "sector": DataStore.get_stock_basic(code).get("industry", "?"),
            "weekly_cur": weekly_cur,
            "weekly_gap": weekly_gap,
            "lo_5y": lo_5y,
            "hi_5y": hi_5y,
            "max_drop": max_drop,
            "max_dd_5y": max_dd_lb,
            "n_b": n_b,
        })

    print(f"Loaded: {n_loaded}, Skipped: {n_skipped}")
    print(f"粗筛: 跌 ≥{args.drop:.0f}% + weekly 末根距 5y 低 < {args.weekly_gap:.0f}% + 反弹 ≥{args.min_bounces} = {len(rough_pool)} 只")

    if not rough_pool:
        return

    # 第二步: tushare 并发拉 daily 价 + income 净利
    rough_codes = [r["code"] for r in rough_pool]
    if args.skip_tushare:
        daily_map = {c: (None, "?") for c in rough_codes}
        profit_map = {c: ("fail", None, None, "skip") for c in rough_codes}
    else:
        print(f"并发拉 tushare daily ({len(rough_codes)} 只, 8 worker)...")
        daily_map = fetch_daily_concurrent(rough_codes, max_workers=8)
        print(f"并发拉 tushare income (2025A + 2024A, 8 worker)...")
        profit_map = fetch_profit_concurrent(rough_codes, max_workers=8)

    candidates = []
    for r in rough_pool:
        code = r["code"]
        lo_5y = r["lo_5y"]
        cur_today, today_date = daily_map.get(code, (None, "?"))
        if cur_today is None or cur_today <= 0:
            cur = r["weekly_cur"]
            cur_date = "?"
            price_source = "weekly (fallback)"
        else:
            cur = cur_today
            cur_date = today_date
            price_source = "daily (tushare)"

        if cur <= lo_5y:
            continue
        daily_gap = (cur - lo_5y) / lo_5y
        if daily_gap >= gap_th:
            continue

        # 今年亏赚 (2025A 净利 vs 2024A) - 用 profit_map 缓存
        profit_status, np_25, np_24, profit_chg = profit_map.get(code, ("fail", None, None, None))
        if profit_status == "ok":
            if np_25 is None or np_25 == 0:
                profit_label = "—"
            elif np_25 < 0:
                profit_label = "亏"
            elif np_24 is not None and np_24 > 0 and profit_chg is not None:
                if profit_chg > 0:
                    profit_label = f"+{profit_chg:.0f}%"
                else:
                    profit_label = f"{profit_chg:.0f}%"
            elif np_24 is not None and np_24 <= 0:
                profit_label = "扭亏"
            else:
                profit_label = "赚"
        else:
            profit_label = "?"

        candidates.append({
            "code": code,
            "name": r["name"],
            "sector": r["sector"],
            "cur": cur,
            "lo_5y": lo_5y,
            "weekly_cur": r["weekly_cur"],
            "weekly_gap": r["weekly_gap"],
            "daily_gap": daily_gap,
            "max_drop": r["max_drop"],
            "max_dd_5y": r["max_dd_5y"],
            "n_b": r["n_b"],
            "cur_date": cur_date,
            "price_source": price_source,
            "profit_label": profit_label,
            "np_25": np_25,
            "np_24": np_24,
        })

    candidates.sort(key=lambda r: r["daily_gap"])

    print(
        f"精筛: daily 价 (tushare) 距 5y 低 < {args.gap:.0f}% = {len(candidates)} 只"
    )
    if candidates:
        as_of = candidates[0]["cur_date"]
        print(f"现价: daily 末根 {as_of} (5y low/high 来自 weekly)\n")
    else:
        print()
        return

    print(
        f"{'代码':<8} {'名称':<10} {'行业':<12} {'现价':>7} {'上周':>7} {'5y低':>7} "
        f"{'5y最大回撤':>10} {'今gap':>6} {'反弹':>5} {'2025净利':>10} {'今年':>6}"
    )
    print("-" * 110)
    for r in candidates:
        np_str = f"{r['np_25']/1e8:.1f}亿" if r['np_25'] else "?"
        print(
            f"{r['code']:<8} {r['name']:<10} {r['sector'][:10]:<12} "
            f"{r['cur']:>7.2f} {r['weekly_cur']:>7.2f} {r['lo_5y']:>7.2f} "
            f"{r['max_dd_5y'] * 100:>+9.1f}% "
            f"{r['daily_gap'] * 100:>+5.2f}% {r['n_b']:>4d}次 {np_str:>10} {r['profit_label']:>6}"
        )


if __name__ == "__main__":
    main()
