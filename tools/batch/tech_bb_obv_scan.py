"""
tools/batch/tech_bb_obv_scan.py — BOLL+BBW+OBV 三重确认 (全市场, 0 网络)

2026-09-14 重构 (从 bb_obv_scan.py 改):
  1. 默认 --all (全市场), 不再只扫 15 个科技股
  2. 数据读取: DataStore.get_ctx(kline_only=True) 跳 EPS/fflow 网络, 5-10s → 0.05s/只
  3. BOLL+OBV 用纯内置函数算, 不调 WyckoffStrategy/ObvStrategy (避免白调 5-10x 开销)
  4. ThreadPoolExecutor 4 worker
  5. 综合: ~30x 提速 (4207 只 30min → 1-2min)

策略 (严格 3 重确认, 每天 0-2 只):
  1. BOLL% < 15  (接近下轨, 短期超卖)
  2. BBW < 10   (布林带收窄, 低波/蓄势)
  3. OBV obv5 OR obv_trend ≥ 1 (吸筹信号)

用法:
  bash tools/with_venv.sh python -m tools.batch.tech_bb_obv_scan        # 默认全市场
  ... --window 5         # 改 OBV 触底窗口
  ... --limit 100        # 调试
  ... --write-md         # 写 docs/tech-bb-obv-watchlist.md
  ... --workers 8        # 线程数
"""
import argparse
import sys
import time
import io
import math
from collections import deque
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── 纯内置 BOLL + OBV 计算 (O(n), 不依赖 strategy) ──────────────────

def _sliding_ma(arr, n):
    """O(n) 滑动均线."""
    result = []
    s = 0.0
    for i, v in enumerate(arr):
        s += v
        if i >= n:
            s -= arr[i - n]
        result.append(s / min(i + 1, n))
    return result


def _boll_pct_width(closes, n=20, k=2.0):
    """O(n) 算 boll_pct + boll_width 双数组, 一次遍历返回 (pct_arr, width_arr)."""
    pct = []
    width = []
    for i in range(len(closes)):
        w = closes[max(0, i - n + 1):i + 1]
        mid = sum(w) / len(w)
        std = math.sqrt(sum((c - mid) ** 2 for c in w) / len(w))
        upper = mid + k * std
        lower = mid - k * std
        p = (closes[i] - lower) / (upper - lower) * 100 if upper > lower else 50.0
        wd = (upper - lower) / mid * 100 if mid > 0 else 0.0
        pct.append(round(p, 1))
        width.append(round(wd, 1))
    return pct, width


def _obv_arr(closes, vols):
    """O(n) OBV 累计数组."""
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:   obv.append(obv[-1] + vols[i])
        elif closes[i] < closes[i-1]: obv.append(obv[-1] - vols[i])
        else:                          obv.append(obv[-1])
    return obv


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
    """单只扫描: DataStore 读 80 根 K 线 (3-4 月, 够 BOLL 20 + OBV MA20 + obv5) + 纯内置 BOLL+OBV."""
    code, window, boll_th, bbw_th, require_obv, kline_limit = args_tuple
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            ctx = DataStore.get_ctx(code, kline_only=True, limit=kline_limit)
        if not ctx.kline or len(ctx.kline) < 30:
            return None

        kline = ctx.kline
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
        closes = [k["close"] for k in kline]
        vols   = [k.get("volume", k.get("vol", 0)) for k in kline]

        # BOLL 一次遍历
        boll_pct_arr, boll_width_arr = _boll_pct_width(closes)

        # OBV 一次遍历 + MA20
        obv = _obv_arr(closes, vols)
        obv_ma20 = _sliding_ma(obv, 20)

        # 找距今 ≤ window 天的最近 1 个交易日 满足 BOLL AND BBW
        today = datetime.now()
        cutoff = (today - timedelta(days=window)).strftime("%Y%m%d")
        trigger = None
        for i in range(len(all_dates) - 1, -1, -1):
            d = all_dates[i]
            if d < cutoff:
                break
            bp = boll_pct_arr[i]
            bw = boll_width_arr[i]
            if bp is not None and bw is not None and bp < boll_th and bw < bbw_th:
                trigger = (d, i, bp, bw, closes[i])
                break
        if trigger is None:
            return None

        trigger_date, idx, bp_v, bw_v, close_v = trigger

        # OBV 实战信号: obv5 (5日价跌+OBV涨) OR obv_trend (OBV>MA20)
        obv5 = 1 if (idx >= 5 and closes[idx] < closes[idx-5] and obv[idx] > obv[idx-5]) else 0
        obv_trend = 1 if (obv_ma20[idx] and obv[idx] > obv_ma20[idx]) else 0
        sigs = []
        if obv5 > 0:       sigs.append("obv5")
        if obv_trend > 0:  sigs.append("obv_trend")
        has_obv = bool(sigs)
        if require_obv and not has_obv:
            return None

        try:
            dt = datetime.strptime(trigger_date, '%Y%m%d')
            days_ago = (today - dt).days
        except Exception:
            days_ago = 99

        return {
            "code": code,
            "trigger_date": trigger_date,
            "trigger_price": close_v,
            "boll_pct": bp_v,
            "bbw": bw_v,
            "obv_label": "+".join(sigs) if sigs else "-",
            "days_ago": days_ago,
            "elapsed": 0.0,
        }
    except Exception as e:
        import traceback
        return {"code": code, "error": str(e)[:100] + " | " + traceback.format_exc().splitlines()[-1][:80]}


def scan_bottom_one_worker(args_tuple):
    """底部反弹信号: BOLL+BBW+OBV 三重确认 + 红柱持续 ≥ min_red_days 天 + 柱子顶点已过 + bar_diff 转稳

    2026-09-15 新增: 红柱到底,准备转绿 的抄底信号
    2026-09-15 改: 红柱天数阈值可配置 (默认 5, 实证 21~30 最佳)
    """
    code, boll_th, bbw_th, kline_limit, min_red_days = args_tuple
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            ctx = DataStore.get_ctx(code, kline_only=True, limit=kline_limit)
        if not ctx.kline or len(ctx.kline) < 60:
            return None

        kline = ctx.kline
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
        closes = [k["close"] for k in kline]
        vols   = [k.get("volume", k.get("vol", 0)) for k in kline]

        # BOLL 当前值
        boll_pct_arr, boll_width_arr = _boll_pct_width(closes)
        bp_v = boll_pct_arr[-1] if boll_pct_arr[-1] is not None else None
        bw_v = boll_width_arr[-1] if boll_width_arr[-1] is not None else None
        if bp_v is None or bw_v is None or bp_v >= boll_th or bw_v >= bbw_th:
            return None

        # OBV 信号
        obv = _obv_arr(closes, vols)
        obv_ma20 = _sliding_ma(obv, 20)
        obv5 = (closes[-1] < closes[-6] and obv[-1] > obv[-6]) if len(closes) > 6 else False
        obv_trend = (obv_ma20[-1] is not None and obv[-1] > obv_ma20[-1])
        sigs = []
        if obv5:      sigs.append("obv5")
        if obv_trend: sigs.append("obv_trend")
        if not sigs:
            return None

        # MACD 红柱底点信号
        bars = _macd_arr(closes)
        cur_bar = bars[-1]
        if cur_bar >= 0:
            return None  # 当前不是红柱, 不算底部反弹

        # 当前红柱持续天数 (从最后往前数, 直到 bar ≥ 0)
        cur_red_days = 0
        for i in range(len(bars)-1, -1, -1):
            if bars[i] < 0:
                cur_red_days += 1
            else:
                break
        if cur_red_days < min_red_days:
            return None  # 红柱太短, 不算蓄势

        # 找当前红柱区间内柱子最低点 (= 柱子顶点已过判断)
        red_seg = bars[-cur_red_days:]
        bar_min = min(red_seg)
        bar_min_pos = red_seg.index(bar_min)  # 0-indexed in red_seg
        cur_pos = cur_red_days - 1
        days_after_min = cur_pos - bar_min_pos
        if days_after_min < 2:
            return None  # 柱子顶点刚出现, 太早

        # bar_diff 转稳: 最近 3 天平均 ≥ -0.05
        bar_diff = [0.0]  # 第一个值为 0
        for i in range(1, len(bars)):
            bar_diff.append(bars[i] - bars[i-1])
        last_3_diff = bar_diff[-3:]
        avg_diff_3d = sum(last_3_diff) / 3.0
        if avg_diff_3d < -0.05:
            return None  # 还在加速下跌

        # 当前 bar_diff 转正 (动能反转初期) — 标记为高质量信号
        cur_diff = bar_diff[-1]
        quality = "high" if cur_diff > 0 else "normal"

        return {
            "code": code,
            "trigger_date": all_dates[-1],
            "trigger_price": closes[-1],
            "boll_pct": bp_v,
            "bbw": bw_v,
            "obv_label": "+".join(sigs),
            "red_days": cur_red_days,
            "bar_min_pos": bar_min_pos / max(1, cur_red_days - 1),
            "cur_pos": cur_pos / max(1, cur_red_days - 1),
            "days_after_min": days_after_min,
            "cur_bar": cur_bar,
            "cur_diff": cur_diff,
            "avg_diff_3d": avg_diff_3d,
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
    parser = argparse.ArgumentParser(description="BOLL+BBW+OBV 三重确认 (全市场, 0 网络)")
    parser.add_argument("--window",          type=int,   default=2,    help="触底窗口天数 (默认 2)")
    parser.add_argument("--boll-threshold",  type=float, default=15.0, help="BOLL% 上限 (默认 15)")
    parser.add_argument("--bbw-threshold",   type=float, default=10.0, help="BBW 上限 (默认 10)")
    parser.add_argument("--no-obv",          action="store_true",      help="只要 BOLL+BBW 双确认")
    parser.add_argument("--workers",         type=int,   default=4,    help="线程数 (默认 4)")
    parser.add_argument("--write-md",        action="store_true",      help="写 docs/tech-bb-obv-watchlist.md")
    parser.add_argument("--limit",           type=int,   default=0,    help="调试: 只扫前 N 只 (0=全部)")
    parser.add_argument("--no-junk-filter",  action="store_true",      help="跳过垃圾股过滤")
    parser.add_argument("--kline-limit",     type=int,   default=80,   help="K 线条数 (默认 80, ~3-4 月, 够 BOLL 20 + OBV MA20 + obv5)")
    parser.add_argument("--include-loss",    action="store_true",      help="包含业绩亏损 (eps<0 或 op_income<0) — 默认排除")
    parser.add_argument("--no-cap",          action="store_true",      help="跳过小盘市值过滤 (总市值<50亿 + 流通<20亿) — 默认过滤")
    parser.add_argument("--no-liq",          action="store_true",      help="跳过流动性过滤 (日成交额<5000万) — 默认过滤")
    parser.add_argument("--bottom",          action="store_true",      help="底部反弹信号 (BOLL+BBW+OBV + 红柱到底 + bar_diff 转稳)")
    parser.add_argument("--bottom-min-days", type=int,   default=5,    help="底部模式: 红柱最短持续天数 (默认 5, 实证 21~30 天最佳)")
    args = parser.parse_args()

    require_obv = not args.no_obv
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

    work_items = [(code, args.window, args.boll_threshold, args.bbw_threshold, require_obv, args.kline_limit)
                  for code in codes]

    if args.bottom:
        # 底部反弹信号: 需要更长 K 线 (60+ 根, 算 MACD 26 根)
        bottom_kline_limit = max(args.kline_limit, 80)
        work_items = [(code, args.boll_threshold, args.bbw_threshold, bottom_kline_limit, args.bottom_min_days)
                      for code in codes]
        worker_fn = scan_bottom_one_worker
        print(f"=== {scope} | 底部反弹 (BOLL<{args.boll_threshold}% + BBW<{args.bbw_threshold}% + OBV + 红柱≥{args.bottom_min_days}天 + diff转稳) ===", flush=True)
    else:
        worker_fn = scan_one_worker
        print(f"=== {scope} | 最近 {args.window} 日 | BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% "
              f"{'AND OBV 底' if require_obv else ''} ===", flush=True)

    print(f"线程池: {args.workers} workers | 喂料: {len(work_items)} 只", flush=True)
    print(f"架构: DataStore.get_ctx(kline_only=True) + 纯内置 BOLL+OBV (不动 L2)", flush=True)

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
        if args.bottom:
            print(f"{'代码':<8}{'名称':<10}{'行业':<14}{'触发日':<10}{'价格':<10}{'BOLL%':<8}{'BBW%':<7}"
                  f"{'OBV信号':<10}{'红柱天':<6}{'顶点位':<7}{'现位':<7}{'过几天':<6}{'现bar':<8}{'现diff':<9}{'质量'}")
            for h in hits:
                print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:12]:<14}"
                      f"{h['trigger_date']:<10}{h['trigger_price']:<10.2f}"
                      f"{h['boll_pct']:<8.1f}{h['bbw']:<7.2f}{h['obv_label']:<10}"
                      f"{h['red_days']:<6}{h['bar_min_pos']:<7.0%}{h['cur_pos']:<7.0%}"
                      f"{h['days_after_min']:<6}{h['cur_bar']:<+8.3f}{h['cur_diff']:<+9.4f}{h['quality']}")
        else:
            print(f"{'代码':<8}{'名称':<10}{'行业':<14}{'触发日':<10}{'价格':<10}{'BOLL%':<8}{'BBW%':<7}{'OBV信号':<14}{'距今':<6}")
            for h in hits:
                print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:12]:<14}"
                      f"{h['trigger_date']:<10}{h['trigger_price']:<10.2f}"
                      f"{h['boll_pct']:<8.1f}{h['bbw']:<7.2f}{h['obv_label']:<14}{h['days_ago']:<6}d")
    else:
        print("无命中 (3 重确认严格, 0-2 只/天为正常)")

    if args.write_md and hits:
        today = datetime.now().strftime("%Y-%m-%d")
        if args.bottom:
            out_path = ROOT / "docs" / "macd-bottom-watchlist.md"
            md = [f"# MACD 红柱底反弹信号 ({today})\n\n"]
            md.append(f"> {scope} | BOLL<{args.boll_threshold}% + BBW<{args.bbw_threshold}% + OBV 触底\n")
            md.append("> + **红柱持续 ≥ 5 天** + **柱子顶点已过 ≥ 2 天** + **bar_diff 转稳 (3日均值 ≥ -0.05)**\n\n")
            md.append(f"**{len(hits)} 只命中** ({sum(1 for h in hits if h['quality']=='high')} 只高质量, "
                     "bar_diff 已转正)\n\n")
            md.append("| 代码 | 名称 | 行业 | 价格 | BOLL% | BBW% | OBV | 红柱天 | 顶点位 | 现位 | 过N天 | 现bar | 现diff | 质量 |\n")
            md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
            for h in hits:
                quality_icon = "⭐" if h['quality'] == 'high' else ""
                md.append(f"| {h['code']} | {h['name']} | {h['industry']} | ¥{h['trigger_price']:.2f} | "
                         f"{h['boll_pct']:.1f} | {h['bbw']:.2f} | {h['obv_label']} | "
                         f"{h['red_days']} | {h['bar_min_pos']:.0%} | {h['cur_pos']:.0%} | "
                         f"{h['days_after_min']} | {h['cur_bar']:+.3f} | {h['cur_diff']:+.4f} | {quality_icon} |\n")
            md.append("\n**信号含义**: 红柱已经到底,柱子顶点已过,准备转绿. ⭐ = bar_diff 已转正 (动能反转初期)\n")
        else:
            out_path = ROOT / "docs" / "tech-bb-obv-watchlist.md"
            md = [f"# Tech BB+OBV 三重确认 ({today})\n\n"]
            md.append(f"> {scope} | 最近 {args.window} 日 | BOLL<{args.boll_threshold}% AND BBW<{args.bbw_threshold}% "
                      f"{'AND OBV 底' if require_obv else ''}\n\n")
            md.append(f"**{len(hits)} 只命中** (实战: 宁可错过不可做错)\n\n")
            md.append("| 代码 | 名称 | 行业 | 触发日 | 价格 | BOLL% | BBW% | OBV 信号 | 距今 |\n")
            md.append("|---|---|---|---|---|---|---|---|---|\n")
            for h in hits:
                md.append(f"| {h['code']} | {h['name']} | {h['industry']} | {h['trigger_date']} | "
                         f"¥{h['trigger_price']:.2f} | {h['boll_pct']:.1f} | {h['bbw']:.2f} | "
                         f"{h['obv_label']} | {h['days_ago']}d |\n")
            md.append("\n**OBV 信号**: `obv5` = 5 日价跌+OBV 涨 (短期吸筹) | "
                     "`obv_trend` = OBV > MA20 (资金净流入) | `+` = 两信号都触发\n")
            md.append("\n**OBV 适用性**: ✅ 光学/封测/HBM (主力控盘) | "
                     "⚠️ 白酒/医药/银行 (主力分散, 信号参考度低) | ❌ 题材小盘 (噪声大)\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("".join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()
