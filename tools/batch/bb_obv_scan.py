"""
tools/batch/bb_obv_scan.py — 科技股 BOLL+BBW+OBV 三重确认扫描 (compute_factor_history 直算)

策略 (严格 3 重确认, 每天 0-2 只):
  1. BOLL% < 15  (接近下轨, 短期超卖)
  2. BBW < 10   (布林带收窄, 低波/蓄势)
  3. OBV 60d 底背离次数 ≥1  (机构吸筹)

不走 cache: 直接用 AnalysisEngine.analyze_history(ctx, dates,
                                              strategies=[WyckoffStrategy, ObvStrategy])
跳过 chan/smc/fflow/peg (只跑必要 2 个), 比 cache 实时

用法:
  bash tools/with_venv.sh python -m tools.batch.bb_obv_scan
  bash tools/with_venv.sh python -m tools.batch.bb_obv_scan --window 5
  bash tools/with_venv.sh python -m tools.batch.bb_obv_scan --all
  bash tools/with_venv.sh python -m tools.batch.bb_obv_scan --no-obv
"""
import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_tech_codes() -> set:
    """一次性加载科技股代码集合"""
    try:
        import pyarrow.parquet as pq
        tbl = pq.read_table("data/history/stock_basic/stock_basic.parquet")
        df = tbl.to_pandas()
        TECH_KW = ["半导体", "软件服务", "通信设备", "电子", "计算机设备",
                    "电气设备", "电器仪表", "光学光电子", "互联网", "航空",
                    "军工", "航天", "汽车电子", "机器人", "新能源"]
        mask = df["industry"].fillna("").apply(lambda x: any(kw in x for kw in TECH_KW))
        return set(df[mask]["code"].tolist())
    except Exception:
        return None


def scan_one(code: str, window: int, boll_th: float, bbw_th: float,
             require_obv: bool, tech_codes: set) -> dict | None:
    """单只扫描: 直算 BOLL/BBW/OBV (不走 cache, cache 只给回测用)"""
    t0 = time.time()
    try:
        from tools.kline_store import DataStore
        from tools.analysis.analysis_engine import WyckoffStrategy, ObvStrategy
        from tools.analysis.analysis_result_signals import compute_factor_history

        # 科技股过滤
        if tech_codes is not None and code not in tech_codes:
            return None

        ctx = DataStore.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 20:
            return None

        # 只需要算最近 window + 20 天 (OBV MA20 需要)
        kline_window = ctx.kline[-(window + 20):]
        all_dates = [k['trade_date'].replace('-','')[:8] for k in ctx.kline]
        dates_window = all_dates[-(window + 20):]

        # 直算 (不走 cache): 只跑 WyckoffStrategy (BOLL/BBW) + ObvStrategy (obv5/obv_trend)
        # 跳过 chan/smc/fflow/peg (省 70% 时间)
        rows = compute_factor_history(ctx, step=1, lookback=len(dates_window),
                                     strategies=[WyckoffStrategy, ObvStrategy])
        history = {r.get('date', ''): r for r in rows if r.get('date')}

        # 找最近 window 日内 满足 BOLL AND BBW 条件
        trigger = None
        recent_dates = dates_window[-window:]
        for d in reversed(recent_dates):
            if d not in history: continue
            row = history[d]
            bp = row.get('boll_pct')
            bw = row.get('boll_width')
            if bp is not None and bw is not None and bp < boll_th and bw < bbw_th:
                trigger = (d, row)
                break
        if trigger is None:
            return None

        trigger_date, r_trig = trigger
        trigger_bpct = r_trig.get('boll_pct', 0)
        trigger_bbw = r_trig.get('boll_width', 0)
        trigger_price = r_trig.get('close', 0)

        # OBV 实战信号: obv5 (5日价跌+OBV涨) OR obv_trend (OBV>MA20)
        has_obv = False
        if require_obv:
            obv5 = r_trig.get('obv5') or 0
            obv_trend = r_trig.get('obv_trend') or 0
            if obv5 > 0 or obv_trend > 0:
                has_obv = True
        if require_obv and not has_obv:
            return None  # 3 重不满足

        # 距今天数
        try:
            dt = datetime.strptime(trigger_date, '%Y%m%d')
            days_ago = (datetime.now() - dt).days
        except Exception:
            days_ago = 99

        # 股票基础信息
        try:
            sb = DataStore.get_stock_basic(code)
            name = sb.get("name", code) or code
            industry = sb.get("industry", "")
        except Exception:
            name = code
            industry = ""

        # 三重状态
        n_conditions = 3 if require_obv else 2
        n_met = 2 + (1 if has_obv else 0) if require_obv else 2
        status = '✅' * n_met + '⬜' * (n_conditions - n_met)

        return {
            "code":           code,
            "name":           name,
            "industry":       industry,
            "trigger_date":   trigger_date,
            "trigger_price":  trigger_price,
            "boll_pct":       trigger_bpct,
            "bbw":            trigger_bbw,
            "obv_days_ago":   0 if has_obv else None,
            "days_ago":       days_ago,
            "status":         status,
            "elapsed":        time.time() - t0,
        }
    except Exception as e:
        return {"code": code, "error": str(e)[:100]}


def main():
    parser = argparse.ArgumentParser(description="科技股 BOLL+BBW+OBV 三重确认 (cache 读, 含 obv5/obv_trend)")
    parser.add_argument("--window",          type=int,   default=2,    help="触底窗口天数（默认2）")
    parser.add_argument("--boll-threshold",  type=float, default=15.0, help="BOLL% 上限（默认15）")
    parser.add_argument("--bbw-threshold",   type=float, default=10.0, help="BBW 上限（默认10）")
    parser.add_argument("--no-obv",          action="store_true",      help="只要 BOLL+BBW 双确认")
    parser.add_argument("--all",             action="store_true",      help="全市场 (默认只科技股)")
    parser.add_argument("--workers",         type=int,   default=4,    help="并发数（默认4）")
    parser.add_argument("--write-md",        action="store_true",      help="写 docs/bb-obv-watchlist.md")
    parser.add_argument("--limit",           type=int,   default=0,    help="调试: 只扫前 N 只 (0=全部)")
    args = parser.parse_args()

    require_obv = not args.no_obv

    # 加载科技股代码
    if args.all:
        tech_codes = None
        scope = "全市场"
    else:
        tech_codes = _load_tech_codes()
        scope = f"科技股 ({len(tech_codes)} 只)" if tech_codes else "科技股 (加载失败)"

    # 读 cache 全部代码 (跳过 stock_basic 列出 4000+ 死代码)
    import sqlite3
    conn = sqlite3.connect(str(ROOT / "data" / "analysis_cache.db"))
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM analysis_cache").fetchall()]
    conn.close()

    if args.limit:
        codes = codes[:args.limit]

    print(f"=== {scope} | 最近 {args.window} 日 | BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% {'AND OBV 底' if require_obv else ''} ===")
    print(f"直算 (compute_factor_history, strategies=[Wyckoff, Obv], 跳过 chan/smc/fflow/peg)")
    print(f"扫描 {len(codes)} 只 ({args.workers} workers)...")

    t0 = time.time()
    hits = []
    errs = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan_one, code, args.window, args.boll_threshold,
                           args.bbw_threshold, require_obv, tech_codes): code
               for code in codes}
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r is None: continue
            if "error" in r:
                errs.append(r)
            else:
                hits.append(r)
            if done % max(1, len(codes) // 10) == 0:
                elapsed = time.time() - t0
                print(f"  [{done}/{len(codes)}] +{len(hits)} 命中 | {elapsed:.0f}s", flush=True)

    elapsed = time.time() - t0
    hits.sort(key=lambda x: (x['days_ago'], x['code']))

    print()
    print(f"=== 完成 ({elapsed:.0f}s) ===")
    print(f"扫描: {len(codes)} 只 | 命中: {len(hits)} 只")
    if errs:
        print(f"错误: {len(errs)} 只 (前3: {[e['code'] for e in errs[:3]]})")
    print()
    if hits:
        print(f"{'代码':<8}{'名称':<10}{'行业':<14}{'触发日':<10}{'价格':<10}{'BOLL%':<8}{'BBW%':<7}{'状态':<10}{'距今':<6}")
        for h in hits:
            print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:12]:<14}"
                  f"{h['trigger_date']:<10}{h['trigger_price']:<10.2f}"
                  f"{h['boll_pct']:<8.1f}{h['bbw']:<7.2f}{h['status']:<10}{h['days_ago']:<6}d")
    else:
        print(f"无命中 (3 重确认严格, 0-2 只/天为正常)")

    if args.write_md and hits:
        out_path = ROOT / "docs" / "bb-obv-watchlist.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        md = [f"# BB+OBV 三重确认 ({today})\n\n"]
        md.append(f"> {scope} | 最近 {args.window} 日 | BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% {'AND OBV 底' if require_obv else ''}\n\n")
        md.append(f"**{len(hits)} 只命中** (实战: 宁可错过不可做错)\n\n")
        md.append("| 代码 | 名称 | 行业 | 触发日 | 价格 | BOLL% | BBW% | OBV 底 | 距今 |\n")
        md.append("|---|---|---|---|---|---|---|---|---|\n")
        for h in hits:
            obv_str = f"-{h['obv_days_ago']}d" if h.get('obv_days_ago') else "-"
            md.append(f"| {h['code']} | {h['name']} | {h['industry']} | {h['trigger_date']} | "
                     f"¥{h['trigger_price']:.2f} | {h['boll_pct']:.1f} | {h['bbw']:.2f} | "
                     f"{obv_str} | {h['days_ago']}d |\n")
        out_path.write_text("".join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
