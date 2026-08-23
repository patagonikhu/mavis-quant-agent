"""
tools/batch/am_divergence.py — 全市场扫描 A→M 阶段切换 + 三重确认

算法:
  1. sync_incremental() 补缺失交易日
  2. 遍历本地历史库所有股票（DataStore.list_codes()）
  3. 每只只跑 3 个 strategy（WyckoffStrategy/ChanStrategy/MacdDivergenceStrategy）
  4. 找最近 window 天内的 A→M 切换，检查前 30d 内缠论底背驰 + MACD 底背驰
  5. 三重确认 → 输出清单

用法:
  bash tools/with_venv.sh python -m tools.batch.am_divergence
  bash tools/with_venv.sh python -m tools.batch.am_divergence --window 10
  bash tools/with_venv.sh python -m tools.batch.am_divergence --no-macd
  bash tools/with_venv.sh python -m tools.batch.am_divergence --workers 16
"""

import argparse
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ============================================================
# 单只扫描
# ============================================================

def scan_one(code: str, window: int, require_macd: bool) -> dict | None:
    """扫描单只股票，找 A→M + 三重确认。返回命中信息或 None。"""
    try:
        from tools.data_store import DataStore
        from tools.analysis.analysis_engine import (
            WyckoffStrategy, ChanStrategy, MacdDivergenceStrategy
        )
        from tools.analysis.factor_history import compute_factor_history

        strategies = [WyckoffStrategy, ChanStrategy, MacdDivergenceStrategy]
        ctx = DataStore.get_ctx(code, kline_only=True, limit=250)  # 需要 200 日均线，250 根保证一致性
        if len(ctx.kline) < 60:
            return None

        lookback = window + 60  # 2天切换窗口 + 30天无Markup检查 + 30天MACD回溯
        rows = compute_factor_history(ctx, step=1, lookback=lookback,
                                      strategies=strategies)
        if not rows:
            return None

        # A→M 切换必须在最近 2 天内（太晚没意义）
        # 要求：切换前 20 天内没有 Markup（排除短暂中断后回来的噪音）
        recent = rows[-2:] if len(rows) >= 2 else rows

        am_switch_row = None
        for i, row in enumerate(recent):
            prev = recent[i - 1] if i > 0 else None
            prev_stage = (prev or {}).get("wyckoff_daily", "")
            curr_stage = row.get("wyckoff_daily", "")
            if "Markup" not in str(prev_stage) and "Markup" in str(curr_stage):
                switch_idx_local = next((j for j, r in enumerate(rows) if r["date"] == row["date"]), None)
                if switch_idx_local is None:
                    continue
                pre20 = rows[max(0, switch_idx_local - 30): switch_idx_local]
                if any("Markup" in str(r.get("wyckoff_daily", "")) for r in pre20):
                    continue  # 前 30 天有 Markup，是短暂中断，跳过
                am_switch_row = row
                break

        if am_switch_row is None:
            return None

        am_date  = am_switch_row["date"]
        am_price = am_switch_row.get("close", 0)

        switch_idx = next((i for i, r in enumerate(rows) if r["date"] == am_date), None)
        if switch_idx is None:
            return None

        lookback_rows = rows[max(0, switch_idx - 30): switch_idx + 1]

        # 缠论底背驰（30天内）
        chan_date = None
        for r in reversed(lookback_rows):
            bc = r.get("daily_beichi") or {}
            direction = bc.get("direction", "") if isinstance(bc, dict) else ""
            strength  = bc.get("strength",  "") if isinstance(bc, dict) else ""
            if direction == "bot" and strength in ("strong", "weak"):
                chan_date = r["date"]
                break
            if isinstance(bc, str) and "底背" in bc:
                chan_date = r["date"]
                break

        # MACD 底背驰（5天内，作为 LPS 信号）
        macd_lookback = rows[max(0, switch_idx - 5): switch_idx + 1]
        macd_date = next((r["date"] for r in reversed(macd_lookback) if r.get("macd_div_bot")), None)

        has_chan = chan_date is not None
        has_macd = macd_date is not None
        # require_macd=True (默认): A→M + MACD 底背驰
        # require_macd=False (--no-macd): 只要 A→M
        confirmed = has_macd if require_macd else True
        if not confirmed:
            return None

        # 计算距今天数
        try:
            fmt = "%Y%m%d" if len(am_date) == 8 else "%Y-%m-%d"
            am_dt = datetime.strptime(am_date, fmt)
            days_ago = (datetime.now() - am_dt).days
        except Exception:
            days_ago = 99

        sb = DataStore.get_stock_basic(code)  # 只读本地缓存，无网络
        name = sb.get("name", code) or code
        industry = sb.get("industry", "")
        return {
            "code":      code,
            "name":      name,
            "industry":  industry,
            "am_date":   am_date,
            "am_price":  am_price,
            "chan_date":  chan_date or "—",
            "macd_date": macd_date or "—",
            "days_ago":  days_ago,
            "triple":    "✅✅✅" if (has_chan and has_macd) else "✅✅⬜",
        }
    except Exception as e:
        return None


# ============================================================
# 主入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="全市场扫描 A→M + 三重确认")
    parser.add_argument("--window",       type=int,   default=5,   help="A→M 切换窗口天数（默认5）")
    parser.add_argument("--no-macd",      action="store_true",     help="只要 A→M + 缠论，不强求 MACD")
    parser.add_argument("--workers",      type=int,   default=2,   help="并发数（默认2，ProcessPool，建议不超过 CPU/4）")
    parser.add_argument("--write-md",     action="store_true",     help="写 docs/am-divergence-watchlist.md")
    parser.add_argument("--limit",        type=int,   default=0,   help="调试用：只扫前 N 只")
    parser.add_argument("--min-amount",   type=float, default=3.0, help="20日均成交额下限（亿元，默认3亿，0=不过滤）")
    args = parser.parse_args()

    require_macd = not args.no_macd

    # L0: 同步增量
    print("🔄 同步K线历史...")
    from tools.history_sync import sync_incremental
    sync_incremental()

    # 获取所有股票
    from tools.data_store import DataStore
    codes = DataStore.list_codes()
    if args.limit:
        codes = codes[:args.limit]

    # 主进程预筛：过滤低成交额小票（单线程读 parquet，比多进程重复读快）
    if args.min_amount > 0:
        before = len(codes)
        def _check_amount(code):
            try:
                from tools.history_sync import read_kline
                from tools.data_store import _to_ts_code
                rows = read_kline(_to_ts_code(code), limit=20)
                if len(rows) < 10:
                    return True  # 数据不足保留
                amounts = [b.get("amount", 0) or 0 for b in rows]
                avg = sum(amounts) / len(amounts) / 1e5
                return avg >= args.min_amount
            except Exception:
                return True
        # 用线程池加速预筛（纯 IO，不受 GIL 影响）
        from concurrent.futures import ThreadPoolExecutor as _TPE
        with _TPE(max_workers=8) as ex:
            keep = list(ex.map(_check_amount, codes))
        codes = [c for c, ok in zip(codes, keep) if ok]
        print(f"  过滤小票: {before} → {len(codes)} 只 (min_amount={args.min_amount}亿, 去掉 {before-len(codes)} 只)")
    print(f"📊 扫描 {len(codes)} 只股票 | window={args.window}d | macd={'是' if require_macd else '否'} | min_amount={args.min_amount}亿 | {args.workers} 并发")

    t0 = time.time()
    results = []
    done = 0

    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan_one, code, args.window, require_macd): code
                for code in codes}
        for fut in as_completed(futs):
            done += 1
            if done % 500 == 0:
                print(f"  进度: {done}/{len(codes)} ({done/len(codes)*100:.0f}%)")
            r = fut.result()
            if r:
                results.append(r)

    elapsed = time.time() - t0
    results.sort(key=lambda x: x["days_ago"])

    # 输出表格
    print(f"\n⏱️  耗时 {elapsed:.1f}s | 命中 {len(results)} 只\n")
    if not results:
        print("  无命中")
        return

    header = f"{'代码':<8}{'名称':<12}{'行业':<12}{'A→M日':<12}{'价格':>6}{'缠论日':<12}{'MACD日':<12}{'距今':>5}  三重"
    print(header)
    print("-" * len(header))
    for r in results:
        print(f"{r['code']:<8}{r['name']:<12}{r['industry']:<12}"
              f"{r['am_date']:<12}{r['am_price']:>6.2f}"
              f"{r['chan_date']:<12}{r['macd_date']:<12}"
              f"{r['days_ago']:>4}d  {r['triple']}")

    # 写 markdown
    if args.write_md:
        md_path = ROOT / "docs" / "am-divergence-watchlist.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            f"# A→M + 三重确认 扫描结果",
            f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
            f"扫描 {len(codes)} 只 | 命中 {len(results)} 只 | window={args.window}d\n",
            f"| 代码 | 名称 | 行业 | A→M日 | 价格 | 缠论日 | MACD日 | 距今 | 三重 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for r in results:
            lines.append(
                f"| {r['code']} | {r['name']} | {r['industry']} | "
                f"{r['am_date']} | ¥{r['am_price']:.2f} | "
                f"{r['chan_date']} | {r['macd_date']} | "
                f"{r['days_ago']}d | {r['triple']} |"
            )
        md_path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\n✅ 报告已存: {md_path}")


if __name__ == "__main__":
    main()
