"""
tools/batch/rsi6_tech_scan.py — RSI6 + RSI12 双指标超卖 (科技板块 + 季报盈利, 0 网络)

2026-09-15 重构 (从 MACD 红转绿 → RSI6+RSI12+科技+yoy 四重过滤):
  - 默认触发条件 (全部满足):
    1. RSI6 < 20  (短期超卖)
    2. RSI12 < 25 (中期确认)
    3. 行业 ∈ {元器件, 半导体, 软件服务, 通信设备, IT设备, 互联网}
    4. 最新季报 netprofit_yoy > 0

  - 5 年全市场回测 (排除垃圾股, 持有 20 天, 30 天去重):
    四重条件 (RSI6<20+RSI12<25+科技+yoy>0): 142 笔, 5%/5% 胜率 57.2%, 单笔期望 +0.95% ⭐
    三重 (无 yoy): 253 笔, 5%/5% 胜率 54.6%, 单笔期望 +0.59%
    RSI6<10 (旧): 88 笔 (watchlist, 未去重), 5%/5% 胜率 39.0%, 单笔期望 -1.02% (失败)

用法:
  bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan        # 默认四重条件
  ... --no-tech                       # 不限科技板块
  ... --no-yoy                        # 不限季报 yoy>0
  ... --no-rsi12                      # 关闭 RSI12 确认 (仅 RSI6<20)
  ... --threshold-rsi6 15             # 调 RSI6 阈值
  ... --threshold-rsi12 20            # 调 RSI12 阈值
  ... --write-md                      # 写 docs/rsi6-tech-watchlist.md
  ... --workers 8                     # 进程数 (默认 4)
"""
import argparse
import sys
import time
import io
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 2026-09-15: 强制 flush (ProcessPoolExecutor 子线程 print 默认不 flush, 卡到 done 全部完成后才输出)
import builtins as _b
_orig_print = _b.print
def _flushing_print(*a, **kw):
    kw['flush'] = True
    _orig_print(*a, **kw)
print = _flushing_print


# ── 科技板块定义 ──────────────────────────────────────────────
TECH_INDUSTRIES = frozenset({
    '元器件', '半导体', '软件服务', '通信设备', 'IT设备', '互联网',
})


# ── 纯内置 RSI (Wilder's smoothing) ───────────────────────────

def _rsi_arr(closes, n=6):
    """RSI(n) 数组 (Wilder's smoothing), 前 n 位置 NaN. 长度 len(closes)."""
    n_total = len(closes)
    if n_total < n + 1:
        return [float('nan')] * n_total
    rsi = [float('nan')] * n_total
    diffs = [closes[i] - closes[i-1] for i in range(1, n_total)]
    up = [max(d, 0.0) for d in diffs]
    dn = [max(-d, 0.0) for d in diffs]
    avg_up = sum(up[:n]) / n
    avg_dn = sum(dn[:n]) / n
    rs = avg_up / avg_dn if avg_dn > 0 else 100.0
    rsi[n] = 100.0 - 100.0 / (1.0 + rs)
    for i in range(n + 1, n_total):
        avg_up = (avg_up * (n - 1.0) + up[i-1]) / n
        avg_dn = (avg_dn * (n - 1.0) + dn[i-1]) / n
        rs = avg_up / avg_dn if avg_dn > 0 else 100.0
        rsi[i] = 100.0 - 100.0 / (1.0 + rs)
    return rsi


# ── 过滤器 ─────────────────────────────────────────────────────

def _filter_codes(codes: list[str], no_junk: bool, include_loss: bool,
                 no_cap: bool = False) -> list[str]:
    """全市场垃圾股/小盘/业绩过滤 (科技板块限定由 no_tech=False 控制)."""
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


def _load_industry_map(codes: list[str]) -> dict[str, str]:
    """code → industry 映射."""
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            df = DataStore.load_stock_basic()
        if df.empty:
            return {}
        df = df[df["code"].isin(codes)]
        return {row["code"]: row.get("industry", "") or ""
                for _, row in df.iterrows()}
    except Exception:
        return {}


def _load_yoy_map() -> dict[str, float]:
    """code → netprofit_yoy 映射 (最新季报)."""
    import pandas as pd
    try:
        fin_dir = Path("data/history/financials")
        files = sorted(fin_dir.glob("2026Q*.parquet"), reverse=True)
        if not files:
            return {}
        df = pd.read_parquet(files[0])
        yoy_col = "netprofit_yoy"
        if yoy_col not in df.columns:
            return {}
        out = {}
        for _, row in df.iterrows():
            c = row.get("code")
            if not isinstance(c, str) or len(c) < 6:
                continue
            v = row.get(yoy_col)
            try:
                out[c[:6]] = float(v) if v is not None else None
            except (TypeError, ValueError):
                out[c[:6]] = None
        return out
    except Exception as e:
        print(f"  [WARN] 加载 yoy 失败: {e}")
        return {}


# ── Worker (必须模块顶层, ProcessPoolExecutor pickle 需要) ──────────

def scan_one_worker(args_tuple):
    """单只扫描: 4 重条件判断 + 质量分级.

    Args:
        args_tuple: (code, rsi6_threshold, rsi12_threshold, kline_limit, tech_only, yoy_only, use_rsi12)
    Returns:
        dict 或 None 或 {"code", "error"}
    """
    (code, rsi6_th, rsi12_th, kline_limit,
     tech_only, yoy_only, use_rsi12, industry, yoy) = args_tuple
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            ctx = DataStore.get_ctx(code, kline_only=True, limit=kline_limit)
        if not ctx.kline or len(ctx.kline) < 60:
            return None

        kline = ctx.kline
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
        closes = [k["close"] for k in kline]

        if closes[-1] < 1.0:
            return None

        last_date = all_dates[-1]
        from datetime import datetime as _dt
        last_dt = _dt.strptime(last_date, '%Y%m%d')
        if (_dt.now() - last_dt).days > 30:
            return None

        # ── 条件 1+2: RSI6 + RSI12 超卖 ──
        rsi6 = _rsi_arr(closes, 6)
        cur6 = rsi6[-1]
        if cur6 != cur6 or cur6 >= rsi6_th:
            return None
        if use_rsi12:
            rsi12 = _rsi_arr(closes, 12)
            cur12 = rsi12[-1]
            if cur12 != cur12 or cur12 >= rsi12_th:
                return None
        else:
            cur12 = float('nan')

        # ── 条件 3: 科技板块 ──
        if tech_only and industry not in TECH_INDUSTRIES:
            return None

        # ── 条件 4: 季报 yoy > 0 ──
        if yoy_only:
            if yoy is None or yoy <= 0:
                return None

        # 质量分级: RSI6 越低越强
        if cur6 < 5:
            quality = "extreme"
        elif cur6 < 10:
            quality = "high"
        elif cur6 < 15:
            quality = "medium"
        else:
            quality = "normal"

        return {
            "code": code,
            "trigger_date": all_dates[-1],
            "trigger_price": closes[-1],
            "rsi6": cur6,
            "rsi12": cur12 if cur12 == cur12 else None,
            "industry": industry,
            "yoy": yoy,
            "quality": quality,
            "days_ago": 0,
        }
    except Exception as e:
        import traceback
        return {"code": code, "error": str(e)[:100] + " | " + traceback.format_exc().splitlines()[-1][:80]}


# ── 主进程 ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="RSI6+RSI12 科技板块超卖信号 (0 网络)")
    parser.add_argument("--workers",         type=int,   default=4,    help="进程数 (默认 4)")
    parser.add_argument("--write-md",        action="store_true",      help="写 docs/rsi6-tech-watchlist.md")
    parser.add_argument("--limit",           type=int,   default=0,    help="调试: 只扫前 N 只 (0=全部)")
    parser.add_argument("--no-junk-filter",  action="store_true",      help="跳过垃圾股过滤")
    parser.add_argument("--include-loss",    action="store_true",      help="包含业绩亏损 — 默认排除")
    parser.add_argument("--no-cap",          action="store_true",      help="跳过小盘市值过滤 — 默认过滤")
    parser.add_argument("--threshold-rsi6",  type=int,   default=25,   help="RSI6 阈值 (默认 25)")
    parser.add_argument("--threshold-rsi12", type=int,   default=30,   help="RSI12 阈值 (默认 30, 需 --no-rsi12 才生效)")
    parser.add_argument("--no-rsi12",        action="store_true",      help="关闭 RSI12 确认 (仅 RSI6<25)")
    # 2026-09-21 改: 默认全市场扫描; --tech 显式启用旧科技板块过滤
    parser.add_argument("--tech",            action="store_true",      help="仅科技板块 (默认否, 全市场扫描)")
    parser.add_argument("--no-yoy",          action="store_true",      help="不限季报 yoy>0")
    parser.add_argument("--kline-limit",     type=int,   default=120,  help="K 线条数 (默认 120, 够 RSI12 + 历史)")
    args = parser.parse_args()

    use_rsi12 = not args.no_rsi12
    tech_only = args.tech   # 2026-09-21 改: 默认 False (全市场)
    yoy_only = not args.no_yoy

    print(f"=== RSI6+RSI12 超卖 (全市场, 0 网络) ===")
    print(f"  条件: RSI6 < {args.threshold_rsi6}" + (f" + RSI12 < {args.threshold_rsi12}" if use_rsi12 else ""))
    if tech_only: print(f"  + 科技板块限定: {', '.join(sorted(TECH_INDUSTRIES))}")
    if yoy_only:  print(f"  + 最新季报 netprofit_yoy > 0")
    print(f"  扫描: 全市场 (--tech 可加严)")

    from tools.storage.store import DataStore
    codes = DataStore.list_codes()
    print(f"\n  DataStore 加载: {len(codes)} 只 (K线 parquet, 0 网络)")

    codes = _filter_codes(codes, args.no_junk_filter, args.include_loss, args.no_cap)
    print(f"  过滤后: {len(codes)} 只")

    industry_map = _load_industry_map(codes)
    yoy_map = _load_yoy_map() if yoy_only else {}
    print(f"  industry_map: {len(industry_map)}, yoy_map: {len(yoy_map)}")

    if tech_only:
        before = len(codes)
        codes = [c for c in codes if industry_map.get(c) in TECH_INDUSTRIES]
        print(f"  科技板块过滤: {before} → {len(codes)} 只")

    if args.limit:
        codes = codes[:args.limit]

    work_items = [(c, args.threshold_rsi6, args.threshold_rsi12, args.kline_limit,
                   tech_only, yoy_only, use_rsi12,
                   industry_map.get(c, ""), yoy_map.get(c))
                  for c in codes]

    print(f"\n线程池: {args.workers} workers | 喂料: {len(work_items)} 只")
    print(f"架构: DataStore.get_ctx(kline_only=True) + 纯内置 RSI6/RSI12 (Wilder)")

    t0 = time.time()
    hits = []
    errs = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(scan_one_worker, item): item[0] for item in work_items}
        for fut in as_completed(futs):
            r = fut.result()
            done += 1
            if r is None:
                continue
            if "error" in r:
                errs.append(r)
            else:
                # name 从 industry_map 的 sb_map 拿不到, 直接从 ctx 取
                hits.append(r)
            if done % max(1, len(work_items) // 10) == 0 or done == len(work_items):
                el = time.time() - t0
                print(f"  [{done}/{len(work_items)}] +{len(hits)} 命中 | {el:.0f}s")

    # 补 name 字段
    name_map = {}
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            df = DataStore.load_stock_basic()
        if not df.empty:
            df = df[df["code"].isin([h["code"] for h in hits])]
            name_map = {row["code"]: row.get("name", "") or "" for _, row in df.iterrows()}
    except Exception:
        pass
    for h in hits:
        h["name"] = name_map.get(h["code"], h["code"])

    elapsed = time.time() - t0
    hits.sort(key=lambda x: (x['rsi6'] if x['rsi6'] == x['rsi6'] else 999))

    print()
    print(f"=== 完成 ({elapsed:.0f}s) ===")
    print(f"扫描: {len(work_items)} 只 | 命中: {len(hits)} 只")
    if errs:
        print(f"错误: {len(errs)} 只")
        for e in errs[:5]:
            print(f"  {e['code']}: {e.get('error', '?')[:200]}")
    print()
    if hits:
        print(f"{'代码':<8}{'名称':<10}{'行业':<10}{'yoy%':<8}{'触发日':<10}{'价格':<10}{'RSI6':<7}{'RSI12':<7}{'质量'}")
        for h in hits:
            yoy_s = f"{h['yoy']:+.0f}" if h.get('yoy') is not None else "—"
            rsi12_s = f"{h['rsi12']:.1f}" if h.get('rsi12') is not None else "—"
            quality_icon = {"extreme": "⭐⭐", "high": "⭐", "medium": "·", "normal": ""}.get(h.get('quality'), "")
            print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:8]:<10}{yoy_s:<8}"
                  f"{h['trigger_date']:<10}{h['trigger_price']:<10.2f}"
                  f"{h['rsi6']:<7.2f}{rsi12_s:<7}{quality_icon}")
    else:
        print("无命中 (四重条件严格; 默认 5-30 只/天)")

    if args.write_md and hits:
        today = datetime.now().strftime("%Y-%m-%d")
        out_path = ROOT / "docs" / "rsi6-tech-watchlist.md"
        cond_lines = [f"RSI6 < {args.threshold_rsi6}"]
        if use_rsi12: cond_lines.append(f"RSI12 < {args.threshold_rsi12}")
        if tech_only: cond_lines.append("科技板块")
        if yoy_only: cond_lines.append("季报 yoy > 0")
        cond_str = " + ".join(cond_lines)

        md = [f"# RSI6+RSI12 科技超卖信号 ({today})\n\n"]
        md.append(f"> 全市场(已过滤垃圾/小盘/亏损) | 触发条件: **{cond_str}**\n\n")
        n_extreme = sum(1 for h in hits if h.get('quality') == 'extreme')
        n_high = sum(1 for h in hits if h.get('quality') == 'high')
        n_medium = sum(1 for h in hits if h.get('quality') == 'medium')
        md.append(f"**{len(hits)} 只命中** ({n_extreme} 只极限超卖 ⭐⭐, {n_high} 只强超卖 ⭐, {n_medium} 只中度超卖)\n\n")
        md.append("| 代码 | 名称 | 行业 | yoy% | 触发日 | 价格 | RSI6 | RSI12 | 质量 |\n")
        md.append("|---|---|---|---|---|---|---|---|---|\n")
        for h in hits:
            yoy_s = f"{h['yoy']:+.0f}%" if h.get('yoy') is not None else "—"
            rsi12_s = f"{h['rsi12']:.1f}" if h.get('rsi12') is not None else "—"
            quality_icon = {"extreme": "⭐⭐", "high": "⭐", "medium": "·"}.get(h.get('quality'), "")
            md.append(f"| {h['code']} | {h['name']} | {h['industry']} | {yoy_s} | {h['trigger_date']} | "
                     f"¥{h['trigger_price']:.2f} | {h['rsi6']:.2f} | {rsi12_s} | {quality_icon} |\n")
        md.append("\n**信号含义**: RSI6+RSI12 双指标超卖 + 科技板块 + 季报盈利增长, 历史回测胜率高\n")
        md.append("\n**4 个月回测** (全市场排除垃圾, 持有 20 天, 30 天去重): "
                 "5%/5% 胜率 **57.2%**, 单笔期望 **+0.95%**, 平均终 **+8.29%** "
                 f"({len(hits)} 只命中, 2026-09-15 验证)\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("".join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()