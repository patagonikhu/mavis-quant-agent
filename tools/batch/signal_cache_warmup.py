"""
signal_cache_warmup.py — 科技股信号缓存增量补全

默认: 科技股（申万行业：半导体/软件/通信/电子/计算机/光学等），5年，step=1
断点续跑: stale 检查，已缓存的直接跳过。每次跑 10 分钟，下次接着补。
Phase1: 多 worker 并发算（只返回结果，不写 DB）
Phase2: 主线程串行写（避免 SQLite 写锁竞争）

用法:
  python tools/batch/signal_cache_warmup.py                        # 科技股增量补缺，10分钟后自动退出
  python tools/batch/signal_cache_warmup.py --timeout 1800         # 跑 30 分钟
  python tools/batch/signal_cache_warmup.py --all                  # 全市场
  python tools/batch/signal_cache_warmup.py --codes 300274 000858  # 指定
  python tools/batch/signal_cache_warmup.py --workers 8            # 8 并发
  python tools/batch/signal_cache_warmup.py --lookback 1250        # 5年 (默认)
  python tools/batch/signal_cache_warmup.py --full                 # 跳过 stale 检查，强制重算
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from tools.kline_store import DataStore
from tools.analysis.signal_cache import write_batch, get_stats, check_stale_batch


def _load_tech_codes() -> list[str]:
    """从 stock_basic.parquet 取申万科技行业股票，与本地 parquet 取交集。"""
    try:
        import pyarrow.parquet as pq
        tbl = pq.read_table("data/history/stock_basic/stock_basic.parquet")
        df = tbl.to_pandas()
        TECH_KW = [
            "半导体", "软件服务", "通信设备", "电子元件", "电子信息",
            "计算机设备", "电气设备", "电器仪表", "光学光电子",
            "互联网", "军工", "航天", "航空", "汽车电子", "机器人", "新能源",
            "元器件", "专用机械", "IT设备", "新型电力",
        ]
        mask = df["industry"].fillna("").apply(
            lambda x: any(kw in x for kw in TECH_KW)
        )
        tech_codes = set(df[mask]["ts_code"].apply(lambda c: c.split(".")[0]))
        all_local = set(DataStore.list_codes())
        return sorted(tech_codes & all_local)
    except Exception as exc:
        print(f"⚠️  科技股过滤失败: {exc}，降级到 watchlist")
        wl = json.load(open("data/watchlist.json"))["stocks"]
        return [s["code"] for s in wl]


def calc_signals_for_code(code: str, full: bool, lookback: int, step: int):
    """Phase1: 只算，返回 (code, to_write_dict, kline, skipped, elapsed)"""
    t0 = time.time()
    try:
        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            return code, None, None, 0, time.time() - t0

        kline = ctx.kline
        # 取最近 lookback 条 K 线的日期
        tail = kline[-lookback:]
        dates = [k["trade_date"].replace("-", "")[:8] for k in tail]

        if full:
            stale_dates = dates
            skipped = 0
        else:
            stale_map = check_stale_batch(code, dates, kline)
            stale_dates = [d for d, s in stale_map.items() if s]
            skipped = len(dates) - len(stale_dates)

        if not stale_dates:
            return code, {}, kline, skipped, time.time() - t0

        # 从第一个 stale date 往前加 120 根作为缠论上下文缓冲
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
        first_stale_idx = all_dates.index(stale_dates[0]) if stale_dates[0] in all_dates else len(all_dates) - lookback
        buf_lookback = max(len(all_dates) - first_stale_idx + 120, 120)

        # 直接调 analyze_history，逐个提取后立即释放，避免 AnalysisResult 大对象驻留内存
        from tools.analysis.analysis_engine import AnalysisEngine
        tail_dates = all_dates[max(0, len(all_dates) - buf_lookback)::step]
        history = AnalysisEngine().analyze_history(ctx, tail_dates)
        stale_set = set(stale_dates)
        to_write = {}
        for d in tail_dates:
            result = history.pop(d, None)
            if d not in stale_set or result is None:
                continue
            to_write[d] = result
        return code, to_write, kline, skipped, time.time() - t0

    except Exception as e:
        return code, None, None, 0, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description="增量预热 analysis_cache.db")
    ap.add_argument("--codes", nargs="+", default=None, help="指定股票代码")
    ap.add_argument("--all", action="store_true", help="全市场 (~5783只)")
    ap.add_argument("--portfolio", action="store_true", help="仅持仓")
    ap.add_argument("--workers", type=int, default=2, help="并发 worker 数")
    ap.add_argument("--lookback", type=int, default=250, help="最近N根K线（默认250≈1年，多次跑完整5年）")
    ap.add_argument("--step", type=int, default=1,
                    help="计算间隔（默认1=每日；step=5每周快5x）")
    ap.add_argument("--full", action="store_true", help="跳过 stale 检查，强制重算")
    ap.add_argument("--timeout", type=int, default=600, help="超时秒数（默认600=10分钟），到时间写已完成的结果退出")
    args = ap.parse_args()

    # ── 收集 codes ────────────────────────────────────────────────────
    if args.all:
        CODES = DataStore().list_codes()
        print(f"全市场: {len(CODES)} 只")
    elif args.codes:
        CODES = args.codes
    elif args.portfolio:
        wl = json.load(open("data/watchlist.json"))["stocks"]
        CODES = [s["code"] for s in wl if s.get("list_type") == "持仓"]
        print(f"持仓: {len(CODES)} 只")
    else:
        CODES = _load_tech_codes()
        print(f"科技股: {len(CODES)} 只 (申万行业筛选 ∩ 本地K线)")

    mode = "全量重算" if args.full else "增量(stale跳过)"
    est_rows = len(CODES) * (args.lookback // args.step)
    print(f"预热 {len(CODES)} 只 | lookback={args.lookback}天 | step={args.step} | {args.workers}并发 | {mode} | timeout={args.timeout}s")
    print(f"预计写入上限: ~{est_rows:,} 行 | 初始: {get_stats()}")
    t0 = time.time()

    # ── Phase1: 并发算（不写 DB）────────────────────────────────────────
    results_map: dict[str, tuple] = {}  # code → (to_write, kline, skipped, elapsed)
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(calc_signals_for_code, code, args.full, args.lookback, args.step): code
                for code in CODES}
        for fut in as_completed(futs):
            code = futs[fut]
            _, to_write, kline, skipped, elapsed = fut.result()
            done += 1
            results_map[code] = (to_write, kline, skipped, elapsed)
            tag = "⏭️" if to_write == {} else ("❌" if to_write is None else f"+{len(to_write or {})}")
            print(f"  [{done:4d}/{len(CODES)}] {tag:>6} {code} {elapsed:.0f}s", flush=True)

            if time.time() - t0 >= args.timeout:
                timed_out = True
                print(f"\n⏰ timeout {args.timeout}s 到，取消剩余 {len(futs)-done} 个任务，写已完成结果...")
                for f in futs:
                    f.cancel()
                break

    # ── Phase2: 串行写（主线程，无锁竞争）──────────────────────────────
    print("\n── Phase2: 写缓存 ──")
    total_written = total_skipped = 0
    for code in CODES:
        to_write, kline, skipped, elapsed = results_map.get(code, (None, None, 0, 0))
        if to_write is None:
            print(f"  ❌ {code}: 无数据")
            continue
        if to_write:
            write_batch(code, kline, to_write)
        total_written += len(to_write)
        total_skipped += skipped

    elapsed_total = time.time() - t0
    print(f"\n完成: 写{total_written:,}行 / 跳{total_skipped:,}行 / {done}只 / {elapsed_total:.0f}s")
    print(f"缓存: {get_stats()}")


if __name__ == "__main__":
    main()
