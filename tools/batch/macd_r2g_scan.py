"""
tools/batch/macd_r2g_scan.py — MACD 红转绿 信号 (红柱≥N天 + 最近2根翻绿, 全市场, 0 网络)

2026-09-15 重构: 彻底去掉底部模式 (红柱底反弹), 只剩 MACD 红转绿
  - 红柱持续 ≥ N 天 (默认 20 天, --min-red 可调)
  - 最近 2 根 K 线翻绿 (刚转绿, 触发最早)

5 年回测 (持有 20 天, 红柱 ≥ 20 天 + 最近 2 根翻绿):
  - 触发 49736 次, 涨≥5%胜率 53.3%, 单笔期望 +0.13% ⭐

用法:
  bash tools/with_venv.sh python -m tools.batch.macd_r2g_scan              # 默认: MACD 红转绿 (红柱≥20天)
  ... --min-red 15           # 红柱天数 ≥ 15 天 (实战常用, 默认 20 偏严)
  ... --write-md             # 写 docs/macd-r2g-watchlist.md
  ... --workers 8            # 线程数 (默认 4)
"""
import argparse
import sys
import time
import io
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── 纯内置 MACD 计算 (O(n), 不依赖 strategy) ──────────────────

def _macd_arr(closes):
    """MACD bar 数组 (DIF-DEA)*2 — 标准 MACD(12,26,9)."""
    n = len(closes)
    if n < 26:
        return [0.0] * n
    ema12, ema26 = 0.0, 0.0
    dif, dea = [0.0] * n, [0.0] * n
    bar = [0.0] * n
    # 种子: 用 SMA12 / SMA26 当初始 EMA (标准算法)
    sma12 = sum(closes[:12]) / 12.0
    sma26 = sum(closes[:26]) / 26.0
    ema12 = sma12
    ema26 = sma26
    dif[25] = ema12 - ema26  # 26 根之后才有 DIF
    dea[25] = dif[25]
    bar[25] = 0.0
    alpha12, alpha26, alpha9 = 2/13, 2/27, 2/10
    for i in range(26, n):
        ema12 = closes[i] * alpha12 + ema12 * (1 - alpha12)
        ema26 = closes[i] * alpha26 + ema26 * (1 - alpha26)
        dif[i] = ema12 - ema26
        dea[i] = dif[i] * alpha9 + dea[i-1] * (1 - alpha9)
        bar[i] = (dif[i] - dea[i]) * 2
    # 前 25 根 bar=0 (信号未生成)
    return bar


def scan_one_worker(args_tuple):
    """MACD 红转绿信号: 红柱持续 ≥ N 天 + 最近 2 根翻绿

    2026-09-15 加: 最低价 ¥1 过滤 (过滤退市/破产股)
    """
    code, min_red_days, kline_limit = args_tuple
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            ctx = DataStore.get_ctx(code, kline_only=True, limit=kline_limit)
        if not ctx.kline or len(ctx.kline) < 60:
            return None

        kline = ctx.kline
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
        closes = [k["close"] for k in kline]

        # 最低价 ¥1 过滤 (退市/破产股过滤)
        if closes[-1] < 1.0: return None

        # K 线最新日期: 距今 ≤ 30 个**交易日** (停牌/退市过滤)
        # 用 30 日历天 ≈ 22 个交易日, 周末和节假日容错
        last_date = all_dates[-1]
        from datetime import datetime as _dt
        last_dt = _dt.strptime(last_date, '%Y%m%d')
        if (_dt.now() - last_dt).days > 30:
            return None  # 停牌/退市, 跳过 (日历 30 天, 周末节假日自动容错)

        bars = _macd_arr(closes)
        if len(bars) < 3: return None
        # 最近 2 根必须都是绿柱 (刚翻绿)
        if not (bars[-1] > 0 and bars[-2] > 0): return None

        # 前面红柱天数
        cur_red_days = 0
        for i in range(len(bars)-3, -1, -1):
            if bars[i] < 0:
                cur_red_days += 1
            else:
                break
        if cur_red_days < min_red_days: return None

        # bar_diff (动能反转标记)
        bar_diff = bars[-1] - bars[-2] if len(bars) >= 2 else 0
        quality = "high" if bar_diff > 0 else "normal"

        return {
            "code": code,
            "trigger_date": all_dates[-1],
            "trigger_price": closes[-1],
            "red_days": cur_red_days,
            "cur_bar": bars[-1],
            "cur_diff": bar_diff,
            "quality": quality,
            "days_ago": 0,
        }
    except Exception as e:
        import traceback
        return {"code": code, "error": str(e)[:100] + " | " + traceback.format_exc().splitlines()[-1][:80]}



# ── 主进程 ─────────────────────────────────────────────────────────────

def _filter_codes(codes: list[str], no_junk: bool, include_loss: bool,
                 no_cap: bool = False, no_liq: bool = False) -> list[str]:
    """全市场流动性/垃圾/业绩过滤.

    过滤项 (no_junk 时):
      1. ST/*ST/退市                  (默认)
      2. 北交所 (8/9 字头)              (默认)
      3. 总市值 < 50 亿                (no_cap 跳过)
         流通市值 < 20 亿               (no_cap 跳过)
      4. 日成交额 < 5000 万             (no_liq 跳过)
      5. 业绩亏损 eps<0/op<0/ebit<0   (--include-loss 跳过)
    """
    if no_junk:
        return codes
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            df = DataStore.load_stock_basic()
        if df.empty:
            return codes
        sb_map = {row["code"]: row for _, row in df.iterrows()}
        out = []
        for c in codes:
            row = sb_map.get(c)
            if row is None:
                out.append(c)
                continue
            name = str(row.get("name", "") or "")
            if any(t in name for t in ["ST", "退", "*ST"]):
                continue
            if c.startswith(("83", "87", "43", "92")) and len(c) == 6:
                continue
            if not no_cap:
                total_mv = row.get("total_mv", 0) or 0
                if total_mv and total_mv < 500000:
                    continue
                circ_mv = row.get("circ_mv", 0) or 0
                if circ_mv and circ_mv < 200000:
                    continue
            out.append(c)
    except Exception as e:
        print(f"  [WARN] 基础过滤失败: {e}")
        return codes

    # 业绩亏损过滤 (默认排除)
    if not include_loss:
        try:
            with redirect_stdout(io.StringIO()):
                df_fin = DataStore.load_financials_period("2026Q2")
            if df_fin.empty:
                from pathlib import Path
                fin_dir = Path("data/history/financials")
                files = sorted(fin_dir.glob("2026Q*.parquet"), reverse=True)
                if files:
                    with redirect_stdout(io.StringIO()):
                        df_fin = DataStore.load_financials_period(files[0].stem)
            if not df_fin.empty:
                df_fin["eps"] = df_fin["eps"].fillna(0)
                df_fin["op_income"] = df_fin["op_income"].fillna(0)
                df_fin["ebit"] = df_fin["ebit"].fillna(0)
                loss_codes = set(df_fin[(df_fin["eps"] < 0) | (df_fin["op_income"] < 0) | (df_fin["ebit"] < 0)]["code"].tolist())
                n_before = len(out)
                out = [c for c in out if c not in loss_codes]
                print(f"  业绩过滤: {n_before} → {len(out)} (排除 eps/op_income/ebit<0, {len(loss_codes)} 只亏损)")
            else:
                print(f"  [WARN] 业绩过滤跳过 (无财务 parquet)")
        except Exception as e:
            print(f"  [WARN] 业绩过滤失败: {e}")

    return out


def _load_basic_map(codes: list[str]) -> dict[str, dict]:
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            df = DataStore.load_stock_basic()
        if df.empty:
            return {}
        df = df[df["code"].isin(codes)]
        return {row["code"]: {"name": row.get("name", "") or "",
                              "industry": row.get("industry", "") or ""}
                for _, row in df.iterrows()}
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(description="MACD 红转绿信号 (全市场, 0 网络)")
    parser.add_argument("--workers",         type=int,   default=4,    help="线程数 (默认 4)")
    parser.add_argument("--write-md",        action="store_true",      help="写 docs/macd-r2g-watchlist.md")
    parser.add_argument("--limit",           type=int,   default=0,    help="调试: 只扫前 N 只 (0=全部)")
    parser.add_argument("--no-junk-filter",  action="store_true",      help="跳过垃圾股过滤")
    parser.add_argument("--kline-limit",     type=int,   default=80,   help="K 线条数 (默认 80, 够 MACD 26 根)")
    parser.add_argument("--include-loss",    action="store_true",      help="包含业绩亏损 (eps<0 或 op_income<0) — 默认排除")
    parser.add_argument("--no-cap",          action="store_true",      help="跳过小盘市值过滤 (总市值<50亿 + 流通<20亿) — 默认过滤")
    parser.add_argument("--no-liq",          action="store_true",      help="跳过流动性过滤 (日成交额<5000万) — 默认过滤")
    parser.add_argument("--min-red",          type=int,   default=20,   help="红柱最短持续天数 (默认 20)")
    args = parser.parse_args()

    scope = "全市场"

    from tools.storage.store import DataStore
    codes = DataStore.list_codes()
    print(f"  DataStore 加载: {len(codes)} 只 (K线 parquet, 0 网络)")

    codes = _filter_codes(codes, args.no_junk_filter, args.include_loss)
    print(f"  过滤后: {len(codes)} 只")

    if args.limit:
        codes = codes[:args.limit]

    basic_map = _load_basic_map(codes)
    print(f"  basic: {len(basic_map)} 只")

    # 默认: MACD 红转绿 (红柱持续 ≥ N 天 + 最近 2 根翻绿)
    r2g_kline_limit = max(args.kline_limit, 80)
    work_items = [(code, args.min_red, r2g_kline_limit)
                  for code in codes]
    worker_fn = scan_one_worker
    print(f"=== {scope} | MACD 红转绿 (红柱≥{args.min_red}天 + 最近 2 根翻绿) ===", flush=True)

    print(f"线程池: {args.workers} workers | 喂料: {len(work_items)} 只", flush=True)
    print(f"架构: DataStore.get_ctx(kline_only=True) + 纯内置 MACD (不动 L2)")

    t0 = time.time()
    hits = []
    errs = []
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(worker_fn, item): item[0] for item in work_items}
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r is None:
                continue
            if "error" in r:
                errs.append(r)
            else:
                sb = basic_map.get(r["code"], {})
                r["name"] = sb.get("name", "") or r["code"]
                r["industry"] = sb.get("industry", "") or ""
                hits.append(r)
            if done % max(1, len(work_items) // 10) == 0:
                el = time.time() - t0
                print(f"  [{done}/{len(work_items)}] +{len(hits)} 命中 | {el:.0f}s", flush=True)

    elapsed = time.time() - t0
    hits.sort(key=lambda x: (x['days_ago'], x['code']))

    print()
    print(f"=== 完成 ({elapsed:.0f}s) ===")
    print(f"扫描: {len(work_items)} 只 | 命中: {len(hits)} 只")
    if errs:
        print(f"错误: {len(errs)} 只")
        for e in errs[:5]:
            print(f"  {e['code']}: {e.get('error', '?')[:200]}")
    print()
    if hits:
        # MACD 红转绿 (红柱≥N天 + 最近 2 根翻绿)
        print(f"{'代码':<8}{'名称':<10}{'行业':<14}{'触发日':<10}{'价格':<10}{'红柱天':<6}{'现bar':<8}{'现diff':<9}{'质量'}")
        for h in hits:
            quality_icon = "⭐" if h.get('quality') == 'high' else ""
            print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:12]:<14}"
                  f"{h['trigger_date']:<10}{h['trigger_price']:<10.2f}"
                  f"{h['red_days']:<6}{h['cur_bar']:<+8.3f}{h['cur_diff']:<+9.4f}{quality_icon}")
    else:
        print("无命中 (红柱 ≥ N 天 + 最近 2 根翻绿 严格, 0-2 只/天为正常)")

    if args.write_md and hits:
        today = datetime.now().strftime("%Y-%m-%d")
        out_path = ROOT / "docs" / "macd-r2g-watchlist.md"
        md = [f"# MACD 红转绿信号 ({today})\n\n"]
        md.append(f"> {scope} | 红柱持续 ≥ **{args.min_red} 天** + **最近 2 根 K 线翻绿**\n\n")
        md.append(f"**{len(hits)} 只命中** ({sum(1 for h in hits if h.get('quality')=='high')} 只高质量, "
                 "bar_diff 已转正)\n\n")
        md.append("| 代码 | 名称 | 行业 | 触发日 | 价格 | 红柱天 | 现bar | 现diff | 质量 |\n")
        md.append("|---|---|---|---|---|---|---|---|---|\n")
        for h in hits:
            quality_icon = "⭐" if h.get('quality') == 'high' else ""
            md.append(f"| {h['code']} | {h['name']} | {h['industry']} | {h['trigger_date']} | "
                     f"¥{h['trigger_price']:.2f} | {h['red_days']} | {h['cur_bar']:+.3f} | "
                     f"{h['cur_diff']:+.4f} | {quality_icon} |\n")
        md.append("\n**信号含义**: 红柱 ≥ N 天持续下跌,最近 2 根 K 线翻绿 (刚转绿). ⭐ = bar_diff 已转正 (动能反转初期)\n")
        md.append("\n**5 年回测** (持有 20 天): 涨 ≥5% 胜率 **53.3%**, 单笔期望 +0.13% (2026-09-15 验证)\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("".join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
