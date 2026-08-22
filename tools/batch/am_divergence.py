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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        ctx = DataStore.get_ctx(code)
        if len(ctx.kline) < 60:
            return None

        # 只算最近 window+30 天，只跑 3 个 strategy
        lookback = window + 30
        rows = compute_factor_history(ctx, step=1, lookback=lookback,
                                      strategies=strategies)
        if not rows:
            return None

        # 找最近 window 天内的 A→M 切换
        today_str = rows[-1]["date"] if rows else ""
        recent = rows[-window:] if len(rows) >= window else rows

        am_switch_row = None
        for i, row in enumerate(recent):
            prev = recent[i - 1] if i > 0 else None
            prev_stage = (prev or {}).get("wyckoff_daily", "")
            curr_stage = row.get("wyckoff_daily", "")
            # A→M: Accumulation → Markup
            if ("Accum" in str(prev_stage) or prev_stage == "Accumulation") and \
               ("Markup" in str(curr_stage)):
                am_switch_row = row
                break  # 取最近一次

        if am_switch_row is None:
            return None

        am_date = am_switch_row["date"]
        am_price = am_switch_row.get("close", 0)

        # 找 A→M 切换日之前 30 天内的底背驰信号
        switch_idx = next((i for i, r in enumerate(rows) if r["date"] == am_date), None)
        if switch_idx is None:
            return None

        lookback_rows = rows[max(0, switch_idx - 30): switch_idx + 1]

        # 缠论底背驰
        chan_date = None
        for r in reversed(lookback_rows):
            bc = r.get("daily_beichi") or {}
            direction = bc.get("direction", "") if isinstance(bc, dict) else ""
            strength = bc.get("strength", "") if isinstance(bc, dict) else ""
            if direction == "bot" and strength in ("strong", "weak"):
                chan_date = r["date"]
                break
            # 兼容字符串格式
            if isinstance(bc, str) and "底背" in bc:
                chan_date = r["date"]
                break

        # MACD 底背驰（从 factor_scores 读）
        macd_date = None
        for r in reversed(lookback_rows):
            macd = r.get("macd_div") or {}
            if isinstance(macd, dict) and macd.get("triggered"):
                macd_date = r["date"]
                break

        # 判断三重确认
        has_chan = chan_date is not None
        has_macd = macd_date is not None

        if require_macd:
            confirmed = has_chan and has_macd
        else:
            confirmed = has_chan  # --no-macd 只要 A→M + 缠论

        if not confirmed:
            return None

        # 计算距今天数
        try:
            am_dt = datetime.strptime(am_date, "%Y-%m-%d")
            days_ago = (datetime.now() - am_dt).days
        except Exception:
            days_ago = 99

        sb = DataStore.get_stock_basic(code)
        return {
            "code":      code,
            "name":      sb.get("name", code),
            "industry":  sb.get("industry", ""),
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
    parser.add_argument("--window",     type=int, default=5,  help="A→M 切换窗口天数（默认5）")
    parser.add_argument("--no-macd",    action="store_true",  help="只要 A→M + 缠论，不强求 MACD")
    parser.add_argument("--workers",    type=int, default=8,  help="并发数（默认8）")
    parser.add_argument("--write-md",   action="store_true",  help="写 docs/am-divergence-watchlist.md")
    parser.add_argument("--limit",      type=int, default=0,  help="调试用：只扫前 N 只")
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
    print(f"📊 扫描 {len(codes)} 只股票 | window={args.window}d | macd={'是' if require_macd else '否'} | {args.workers} 并发")

    t0 = time.time()
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
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
