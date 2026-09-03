"""
tools/batch/bb_obv_scan.py — 科技股 BOLL+BBW+OBV 三重确认扫描 (analyze_history 直算)

只读 4 字段 boll_pct/boll_width/obv5/obv_trend, 跳过 chan/smc/fflow/peg (省 70% 时间)

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
    """一次性加载科技股代码集合 (2026-09-03 v6.1.1 改: 走 DataStore.load_stock_basic)"""
    try:
        from tools.storage.store import DataStore
        df = DataStore.load_stock_basic()
        if df.empty:
            return set()
        TECH_KW = ["半导体", "软件服务", "通信设备", "电子", "计算机设备",
                    "电气设备", "电器仪表", "光学光电子", "互联网", "航空",
                    "军工", "航天", "汽车电子", "机器人", "新能源"]
        mask = df["industry"].fillna("").apply(lambda x: any(kw in x for kw in TECH_KW))
        return set(df[mask]["code"].tolist())
    except Exception:
        return None


def _load_all_basic() -> dict:
    """主线程一次性加载全市场 stock_basic (避免 worker 里 duckdb 竞态)

    Returns:
        {code: {"name": ..., "industry": ...}, ...}
    """
    try:
        from tools.storage.store import DataStore
        df = DataStore.load_stock_basic()
        if df.empty:
            return {}
        return {
            row["code"]: {"name": row.get("name", "") or "", "industry": row.get("industry", "") or ""}
            for _, row in df.iterrows()
        }
    except Exception as e:
        print(f"[WARN] _load_all_basic 失败: {e}", flush=True)
        return {}


def scan_one(code: str, window: int, boll_th: float, bbw_th: float,
             require_obv: bool, tech_codes: set, basic_map: dict) -> dict | None:
    """单只扫描: 直算 BOLL/BBW/OBV (不走 cache, cache 只给回测用)

    basic_map: 主线程预加载的 {code: {"name", "industry"}}, 避免 worker 里 duckdb 竞态
    """
    t0 = time.time()
    try:
        from tools.storage.store import DataStore
        from tools.analysis.analysis_engine import WyckoffStrategy, ObvStrategy
        # 科技股过滤
        if tech_codes is not None and code not in tech_codes:
            return None

        ctx = DataStore.get_ctx(code)
        if not ctx.kline or len(ctx.kline) < 20:
            return None

        # 只需要算最近 window 个日历日 + 20 天 (OBV MA20 需要)
        # 2026-08-29 改: window = "距今天 ≤ N 个日历日" (以今天为基准,不是最后交易日)
        # 例: 今天 8/29 周六, 8/25 (周一) 距今 4 天, window=3 应排除
        all_dates = [k['trade_date'].replace('-','')[:8] for k in ctx.kline]
        today = datetime.now()
        cutoff_dt = today - timedelta(days=window)
        cutoff_str = cutoff_dt.strftime('%Y%m%d')
        dates_window = [d for d in all_dates if d >= cutoff_str]
        # 至少要 20 根 K 线算 OBV MA20
        if len(dates_window) < 20:
            extra_needed = 20 - len(dates_window)
            earlier = [d for d in all_dates if d < cutoff_str][-extra_needed:]
            dates_window = earlier + dates_window

        # 只跑 WyckoffStrategy (BOLL/BBW) + ObvStrategy (obv5/obv_trend), 跳过 chan/smc/fflow/peg
        from tools.analysis.analysis_engine import AnalysisEngine
        engine = AnalysisEngine(strategies=[WyckoffStrategy, ObvStrategy])
        history_raw = engine.analyze_history(ctx, dates_window)
        # BOLL 字段 (boll_pct/boll_width) 存在 ctx.kline_arrs 里 (kline_arrays.py 算),
        # wyckoff strategy 没注入到 raw['wyckoff'], 修复: 直接从 ctx.kline_arrs 读
        arrs = ctx.kline_arrs or {}
        boll_pct_arr  = arrs.get('boll_pct')   # numpy array 按 all_dates 顺序
        boll_width_arr = arrs.get('boll_width')
        # date -> index 映射
        date_to_idx = {d: i for i, d in enumerate(all_dates)}
        # 简化字段访问: 拼一个 flat dict, 只装 bb_obv_scan 用的字段
        history = {}
        for d, r in history_raw.items():
            d_key = d.replace('-', '')[:8] if '-' in d else d
            idx = date_to_idx.get(d_key)
            history[d_key] = {
                'close':       r.current_price,
                'boll_pct':    float(boll_pct_arr[idx]) if (boll_pct_arr is not None and idx is not None) else None,
                'boll_width':  float(boll_width_arr[idx]) if (boll_width_arr is not None and idx is not None) else None,
                'obv5':        (r.raw.get('obv') or {}).get('obv5', 0),
                'obv_trend':   (r.raw.get('obv') or {}).get('obv_trend', 0),
            }

        # 找距今 ≤ window 天 的最近一个交易日 满足 BOLL AND BBW
        trigger = None
        recent_dates = [d for d in dates_window if d >= cutoff_str]
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
        obv5 = r_trig.get('obv5') or 0
        obv_trend = r_trig.get('obv_trend') or 0
        # 信号类型标签: obv5 / obv_trend / 两者
        sigs = []
        if obv5 > 0:       sigs.append("obv5")
        if obv_trend > 0:  sigs.append("obv_trend")
        obv_label = "+".join(sigs) if sigs else "-"
        has_obv = bool(sigs)
        if require_obv and not has_obv:
            return None  # 3 重不满足

        # 距今天数 (基于 today, 不是最后交易日)
        try:
            dt = datetime.strptime(trigger_date, '%Y%m%d')
            days_ago = (today - dt).days
        except Exception:
            days_ago = 99

        # 股票基础信息 (主线程预加载, 避免 duckdb worker 竞态)
        sb = basic_map.get(code, {})
        name = sb.get("name", "") or code
        industry = sb.get("industry", "") or ""

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
            "obv_label":      obv_label,
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
    parser.add_argument("--no-junk-filter",  action="store_true",      help="跳过垃圾股过滤 (ST/北交所/小市值)")
    args = parser.parse_args()

    require_obv = not args.no_obv

    # 加载科技股代码
    if args.all:
        tech_codes = None
        scope = "全市场"
    else:
        tech_codes = _load_tech_codes()
        scope = f"科技股 ({len(tech_codes)} 只)" if tech_codes else "科技股 (加载失败)"

    # v6.0 改: 不再偷偷 sync, 跑前用户先 `python -m tools.storage.sync --kline`
    print(f"  ℹ️  本脚本 0 网络, 缺数据请先跑: python -m tools.storage.sync --kline", flush=True)

    # 读代码列表: 直接走 DataStore (K线 parquet), 不走 analysis_cache.db (那是回测用的)
    from tools.storage.store import DataStore
    codes = DataStore.list_codes()
    print(f"  DataStore 加载: {len(codes)} 只 (K线 parquet, 0 网络)")

    # 垃圾股过滤: ST/退市风险/北交所/小盘股 (从 stock_basic 拿名称 + 市值)
    if not args.no_junk_filter:
        try:
            from tools.storage.store import DataStore
            sb = DataStore.load_stock_basic()
            if sb.empty:
                sb_map = {}
            else:
                sb_map = {row["code"]: row for _, row in sb.iterrows()}
            n_before = len(codes)
            codes_filtered = []
            for c in codes:
                row = sb_map.get(c)
                if row is None:
                    codes_filtered.append(c)
                    continue
                name = str(row.get("name", "") or "")
                # 1. 排除 ST/*ST/ST 股 (退市风险)
                if any(tag in name for tag in ["ST", "退", "*ST"]):
                    continue
                # 2. 排除北交所 (8/9 字头 6 位, 流动性差, 投机性强)
                if (c.startswith(("83", "87", "43", "92")) and len(c) == 6):
                    continue
                # 3. 排除小盘股 (4 重过滤, 噪声大 OBV 失真):
                #    a. 总市值 < 50 亿 (小盘)
                total_mv = row.get("total_mv", 0) or 0  # 万元
                if total_mv and total_mv < 500000:  # 50 亿 = 500000 万元
                    continue
                #    b. 流通市值 < 20 亿 (流动性差)
                circ_mv = row.get("circ_mv", 0) or 0  # 万元
                if circ_mv and circ_mv < 200000:  # 20 亿
                    continue
                codes_filtered.append(c)
            codes = codes_filtered
            # 4. 排除日成交额 < 5000 万的票 (流动性差, 主力难控盘)
            #    amount 单位: 千元 (Tushare 默认), 5000 万 = 5e4 千元
            try:
                from tools.storage.store import DataStore
                # 流动性过滤需要 amount (K 线字段, 不在 daily_basic)
                amt_df = DataStore.load_all_daily_basic_lite()
                if not amt_df.empty and "amount" in amt_df.columns:
                    amt_df["code"] = amt_df["ts_code"].str.split(".").str[0]
                    low_liq = set(amt_df[amt_df["amount"] < 50000]["code"].tolist())  # <5000万
                    n_pre = len(codes)
                    codes = [c for c in codes if c not in low_liq]
                    print(f"  流动性过滤: {n_pre} → {len(codes)} (排除日成交额<5000万)")
            except Exception as e:
                print(f"  [WARN] 流动性过滤失败: {e}")
            print(f"  垃圾股过滤: {n_before} → {len(codes)} (排除 ST/北交所/<50亿/<20亿流通/<5000万成交)")
        except Exception as e:
            print(f"  [WARN] 垃圾股过滤失败: {e}")

    if args.limit:
        codes = codes[:args.limit]

    # 主线程预加载 stock_basic (一次性, 避免 worker 竞态)
    print("加载 stock_basic ...", flush=True)
    basic_map = _load_all_basic()
    print(f"  {len(basic_map)} 只票基础信息已加载", flush=True)

    print(f"=== {scope} | 最近 {args.window} 日 | BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% {'AND OBV 底' if require_obv else ''} ===")
    print(f"直算 (analyze_history, strategies=[Wyckoff, Obv], 跳过 chan/smc/fflow/peg)")
    print(f"扫描 {len(codes)} 只 ({args.workers} workers)...")

    t0 = time.time()
    hits = []
    errs = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan_one, code, args.window, args.boll_threshold,
                           args.bbw_threshold, require_obv, tech_codes, basic_map): code
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
        print(f"{'代码':<8}{'名称':<10}{'行业':<14}{'触发日':<10}{'价格':<10}{'BOLL%':<8}{'BBW%':<7}{'OBV信号':<14}{'距今':<6}")
        for h in hits:
            print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:12]:<14}"
                  f"{h['trigger_date']:<10}{h['trigger_price']:<10.2f}"
                  f"{h['boll_pct']:<8.1f}{h['bbw']:<7.2f}{h['obv_label']:<14}{h['days_ago']:<6}d")
    else:
        print(f"无命中 (3 重确认严格, 0-2 只/天为正常)")

    if args.write_md and hits:
        out_path = ROOT / "docs" / "bb-obv-watchlist.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        md = [f"# BB+OBV 三重确认 ({today})\n\n"]
        md.append(f"> {scope} | 最近 {args.window} 日 | BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% {'AND OBV 底' if require_obv else ''}\n\n")
        md.append(f"**{len(hits)} 只命中** (实战: 宁可错过不可做错)\n\n")
        md.append("| 代码 | 名称 | 行业 | 触发日 | 价格 | BOLL% | BBW% | OBV 信号 | 距今 |\n")
        md.append("|---|---|---|---|---|---|---|---|---|\n")
        for h in hits:
            md.append(f"| {h['code']} | {h['name']} | {h['industry']} | {h['trigger_date']} | "
                     f"¥{h['trigger_price']:.2f} | {h['boll_pct']:.1f} | {h['bbw']:.2f} | "
                     f"{h['obv_label']} | {h['days_ago']}d |\n")
        md.append("\n**OBV 信号说明**: `obv5` = 5 日价跌+OBV 涨 (短期吸筹) | "
                 "`obv_trend` = OBV > MA20 (资金净流入) | `+` = 两信号都触发\n")
        out_path.write_text("".join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
