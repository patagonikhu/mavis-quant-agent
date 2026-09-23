"""
tools/batch/rsi6_tech_scan.py — RSI 双指标超卖 + 放量 spike 双信号 AND 买点 (0 网络)

v4.3 (2026-09-23) 加 step 2: MCP 负面新闻过滤
v4.2 (2026-09-23) 重构:
  - 触发条件 (两者都过才算命中):
    1. RSI6 < 25 且 RSI12 < 30  (双指标超卖, 看最近 lookback 根 K 线任一跌破)
    2. 最近 volume_lookback 根 K 线任一 vol/MA{volume_window} >= volume_spike (默认 10 根 + 2.5x)
  - 0 网络, 走 DataStore (K线 parquet)
  - 默认从 watchlist.json 选股 (持仓+blowout+自选)
  - 不做业绩过滤 — 由 /t-finance-earnings-blowout 把关
  - --check-news 时启用 step 2 (MCP 负面新闻, 命中行加 ⚠️)

历史版本:
  - v4.1 (2026-09-23) 默认 watchlist + RSI/放量 OR + lookback 2, 去掉 6 重业绩过滤
  - v4.0 (2026-09-21) 默认全市场 + RSI6<25+RSI12<30 OR 放量, 去掉旧 6 重业绩过滤
  - v3   (2026-09-15) RSI6+RSI12+科技+yoy 四重 AND (本次废弃, 改为 v4 双信号 AND)

用法:
  bash tools/with_venv.sh python -m tools.batch.rsi6_tech_scan        # 默认 (AND 双信号)
  ... --all-market                    # 全市场扫 (默认 watchlist)
  ... --threshold-rsi6 20             # 调 RSI6 阈值
  ... --threshold-rsi12 25            # 调 RSI12 阈值
  ... --no-rsi12                      # 关 RSI12 (仅 RSI6<25)
  ... --volume-spike 2.0              # 调放量倍数
  ... --volume-window 10              # 放量 MA 窗口
  ... --volume-lookback 10            # 放量看最近 N 根 (默认 10)
  ... --lookback 2                    # RSI 看最近 N 根 (默认 2)
  ... --tech                          # 仅科技板块
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


def _load_yoy_map() -> dict[str, dict]:
    """code → yoy 详情 映射.

    Returns:
        {code: {
            "np_yoy":     float 最新季净利 yoy (单季),
            "rev_yoy":    float 最新季营收 yoy (单季),
            "np_yoy_prev": float 上季净利 yoy (单季),
            "np_yoy_yoy": float 最新季 yoy 同比变动 pp (相对上季 +50pp 等)
            "gross_margin": float 最新季毛利率 %
            "roe":        float 最新季 ROE %
        }}
        任何 yoy 缺失 → None
    """
    import pandas as pd
    try:
        fin_dir = Path("data/history/financials")
        files = sorted(fin_dir.glob("2026Q*.parquet"), reverse=True)
        if not files:
            return {}
        df_cur = pd.read_parquet(files[0])
        if "netprofit_yoy" not in df_cur.columns:
            return {}

        # 上季: 第二个最新季度文件
        prev_df = None
        if len(files) >= 2:
            try:
                prev_df = pd.read_parquet(files[1])
            except Exception:
                prev_df = None

        out = {}
        for _, row in df_cur.iterrows():
            c = row.get("code")
            if not isinstance(c, str) or len(c) < 6:
                continue
            code6 = c[:6]
            np_yoy  = row.get("netprofit_yoy")
            rev_yoy = row.get("tr_yoy")
            gross_margin = row.get("grossprofit_margin")  # Tushare 字段名
            roe = row.get("roe")
            np_yoy_prev = None
            if prev_df is not None:
                prev_rows = prev_df[prev_df["code"].str[:6] == code6] if "code" in prev_df.columns else pd.DataFrame()
                if not prev_rows.empty:
                    v = prev_rows.iloc[0].get("netprofit_yoy")
                    try:
                        np_yoy_prev = float(v) if v is not None else None
                    except (TypeError, ValueError):
                        np_yoy_prev = None

            def _to_float(x):
                try:
                    return float(x) if x is not None else None
                except (TypeError, ValueError):
                    return None

            out[code6] = {
                "np_yoy":       _to_float(np_yoy),
                "rev_yoy":      _to_float(rev_yoy),
                "np_yoy_prev":  np_yoy_prev,
                "gross_margin": _to_float(gross_margin),
                "roe":          _to_float(roe),
            }
        return out
    except Exception as e:
        print(f"  [WARN] 加载 yoy 失败: {e}")
        return {}


# ── Worker (必须模块顶层, ProcessPoolExecutor pickle 需要) ──────────

def scan_one_worker(args_tuple):
    """单只扫描: 只判断 RSI + 放量 (2026-09-23 去掉 6 重业绩过滤, 支持 lookback;
    2026-09-23 删 Wyckoff LPSY→JAC 旁路 — 4 步形态判定对趋势/位置/RSI 无任何约束,
    在下跌中继 + 一字板都能命中,不是真买点).

    Args:
        args_tuple: (code, rsi6_threshold, rsi12_threshold, kline_limit, tech_only, use_rsi12,
                     industry, volume_spike_th, volume_window, lookback, yoy_dict)
    Returns:
        dict 或 None 或 {"code", "error"}
    """
    (code, rsi6_th, rsi12_th, kline_limit,
     tech_only, use_rsi12, industry,
     volume_spike_th, volume_window, lookback, volume_lookback, yoy) = args_tuple
    try:
        from tools.storage.store import DataStore
        with redirect_stdout(io.StringIO()):
            ctx = DataStore.get_ctx(code, kline_only=True, limit=kline_limit)
        if not ctx.kline or len(ctx.kline) < 60:
            return None

        kline = ctx.kline
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in kline]
        closes_raw = [k.get("close", 0) for k in kline]
        closes = [c if isinstance(c, (int, float)) else 0 for c in closes_raw]

        if closes[-1] < 1.0:
            return None

        last_date = all_dates[-1]
        from datetime import datetime as _dt
        last_dt = _dt.strptime(last_date, '%Y%m%d')
        if (_dt.now() - last_dt).days > 30:
            return None

        # ── 条件 1: RSI6 + RSI12 超卖 (最近 lookback 根 K 线任一跌破算过) ──
        rsi6 = _rsi_arr(closes, 6)
        # 最近 lookback 根 K 线任一跌破算过
        rsi6_window = rsi6[-lookback:] if len(rsi6) >= lookback else rsi6
        rsi_pass = any((v == v and v < rsi6_th) for v in rsi6_window)
        cur6 = rsi6[-1]
        if use_rsi12:
            rsi12 = _rsi_arr(closes, 12)
            rsi12_window = rsi12[-lookback:] if len(rsi12) >= lookback else rsi12
            rsi_pass = rsi_pass and any((v == v and v < rsi12_th) for v in rsi12_window)
            cur12 = rsi12[-1]
        else:
            cur12 = float('nan')

        # ── 条件 2: 放量 spike (过去 10 根 K 线任一 vol/MA{volume_window}>= volume_spike_th) ──
        #   2026-09-23 改: 固定看最近 10 根 K 线 (--volume-lookback 可调), 与 RSI 改成 AND
        volume_pass = False
        volume_ratio = None
        max_volume_ratio = None
        if volume_spike_th > 0:
            volumes_raw = [k.get("vol", 0) or 0 for k in kline]
            volumes = [v if isinstance(v, (int, float)) else 0 for v in volumes_raw]
            eff_lookback = max(lookback, volume_lookback)
            if len(volumes) >= volume_window + eff_lookback:
                # 对最近 eff_lookback 根 K 线逐一算 vs 前 volume_window 根均量, 任一过即过
                ratios = []
                for i in range(1, eff_lookback + 1):
                    cur_idx = -i  # -1 是当日, -2 是前一日, ...
                    last_vol = volumes[cur_idx]
                    # MA = 前 volume_window 根 (不含当前这根), 所以是 [-i-volume_window : -i]
                    window_start = cur_idx - volume_window
                    if window_start >= -len(volumes):
                        ma_vol = sum(volumes[window_start:cur_idx]) / volume_window
                        if ma_vol > 0:
                            ratios.append(last_vol / ma_vol)
                if ratios:
                    max_volume_ratio = max(ratios)
                    volume_pass = max_volume_ratio >= volume_spike_th
                    volume_ratio = max_volume_ratio

        # 2026-09-23 改: RSI + 放量 AND (两者都过才算买点)
        if not (rsi_pass and volume_pass):
            return None

        trigger = ["RSI+放量"]

        # 2026-09-23 改: 去掉 6 重业绩过滤, 只判断 RSI + 放量
        # (业绩差票不再被排除, RSI 超卖/放量 spike 即可命中)

        # 质量分级: RSI6 越低越强
        if cur6 < 5:
            quality = "extreme"
        elif cur6 < 10:
            quality = "high"
        elif cur6 < 15:
            quality = "medium"
        else:
            quality = "normal"

        # 2026-09-23 改: 不再传 yoy 数据 (业绩过滤已移除), 但保留展示用字段
        yoy_disp = yoy if isinstance(yoy, dict) else {}
        return {
            "code": code,
            "trigger_date": all_dates[-1],
            "trigger_price": closes[-1],
            "rsi6": cur6,
            "rsi12": cur12 if cur12 == cur12 else None,
            "industry": industry,
            "quality": quality,
            "trigger": "+".join(trigger),     # 触发信号 (RSI / 放量)
            "volume_ratio": round(volume_ratio, 2) if volume_ratio else None,
            "rev_yoy":      yoy_disp.get("rev_yoy"),
            "np_yoy":       yoy_disp.get("np_yoy"),
            "gross_margin": yoy_disp.get("gross_margin"),
            "roe":          yoy_disp.get("roe"),
            "days_ago": 0,
        }
    except Exception as e:
        import traceback
        tb_lines = traceback.format_exc().splitlines()
        # 最后一行是 exception,前几行是 File "..." + code line
        # 把最后 3 行拼起来
        relevant = " | ".join(l.strip() for l in tb_lines[-3:] if l.strip())
        return {"code": code, "error": relevant[:200]}


# ── 命中后 MCP 负面新闻检查 (2026-09-23 加, 仅在 --check-news 时启用) ────────

def _resolve_inner_code_mcp(code: str) -> str | None:
    """6 位 → 聚源内码 (纯数字, 给 call_api stockObject 用).

    失败/无 MCP 返 None.
    """
    try:
        from tools.storage.sources.tushare import _code_to_ts
        from connector__hengsheng__resolve_entity import resolve_entity  # type: ignore
    except ImportError:
        return None
    try:
        secucode = _code_to_ts(code)
        cands = resolve_entity(entity_type="a_stock", query=secucode, top_k=1)
        if cands:
            cand = cands[0]
            inner = cand.get("code")
            if inner and str(inner).isdigit():
                return str(inner)
    except Exception:
        pass
    return None


def _check_negative_news(code: str, days: int = 30) -> dict:
    """通过 MCP (MiniMax Finance StockNewslist) 查近 N 天负面新闻.

    Returns:
        {"negative_count": int, "sample_title": str|None, "warn": bool, "mcp_available": bool}

    2026-09-23 注: MCP connector__hengsheng__* 是 LLM agent 内置函数,不是 Python 包。
    本函数在 bash `python -m` 子进程里调用时 MCP 不可用 → 静默返 mcp_available=False。
    交互场景 (LLM 在场) 由 LLM 端手工调 MCP,本函数留作 fallback / 测试用.
    """
    out = {"negative_count": 0, "sample_title": None, "warn": False, "mcp_available": False}
    inner = _resolve_inner_code_mcp(code)
    if not inner:
        return out
    out["mcp_available"] = True
    try:
        from connector__hengsheng__call_api import call_api  # type: ignore
        from datetime import datetime as _dt, timedelta as _td
        ed = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d")
        result = call_api(
            api_id="StockNewslist",
            format="json",
            params={
                "stockObject": [inner],
                "emotionDirectionCode": ["FCC0000002QA"],  # 负面
                "beginDate": ed,
                "endDate": _dt.now().strftime("%Y-%m-%d"),
                "pageSize": 5,
                "tagSource": "4",
            },
        )
        if isinstance(result, dict):
            data = result.get("data") or {}
            rows = data.get("rows") or []
            out["negative_count"] = len(rows)
            if rows:
                out["sample_title"] = (rows[0].get("title") or "")[:80]
                out["warn"] = len(rows) > 0
    except Exception as e:
        pass
    return out


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
    # 2026-09-21 改: 默认严 yoy 模式 — 6 条反向排除, 不再支持 --no-yoy / --strict-yoy
    parser.add_argument("--kline-limit",     type=int,   default=120,  help="K 线条数 (默认 120, 够 RSI12 + 历史)")
    # 2026-09-23 改: 默认从 watchlist 选 (不再全市场)
    parser.add_argument("--all-market",      action="store_true",
                        help="全市场扫描 (默认 False = 从 watchlist.json 选)")
    parser.add_argument("--from-watchlist",  action="store_true",
                        help="(兼容旧 CLI, 默认行为) 从 watchlist.json 选股")
    parser.add_argument("--watchlist-types", default="持仓,blowout,自选",
                        help="watchlist list_type 过滤, 逗号分隔 (默认 全部, 仅 --from-watchlist 时生效)")
    parser.add_argument("--volume-spike",    type=float, default=2.5,
                        help="放量倍数门槛 (默认 2.5, 2026-09-23 从 0 改; 例 2.0 当日量>=MA10*2). 与 RSI 一起 AND (两者都过才命中)")
    parser.add_argument("--volume-window",   type=int,   default=10,
                        help="放量 MA 窗口 (默认 10, 2026-09-23 从 5 改; 当日量 / MA{volume-window} >= 倍数)")
    parser.add_argument("--volume-lookback", type=int,   default=10,
                        help="放量看最近 N 根 K 线任一放量即过 (默认 10, 2026-09-23 新增; RSI 触发也独立用 lookback)")
    parser.add_argument("--lookback",        type=int,   default=2,
                        help="RSI 看最近 N 根 K 线任一跌破即过 (默认 2, 2026-09-23 从 1 改; --lookback 5 更宽松)")
    parser.add_argument("--check-news",      action="store_true",
                        help="命中后通过 MCP (MiniMax Finance) 查近 30 天负面新闻, 有则标 ⚠️")
    parser.add_argument("--news-days",       type=int,   default=30,
                        help="新闻回溯天数 (默认 30, 需 --check-news)")
    # 2026-09-23 删: Wyckoff LPSY → JAC 买点旁路 (4 步形态判定无趋势/位置/RSI 约束,
    # 在下跌中继 + 一字板都能命中, 不是真买点)
    args = parser.parse_args()

    use_rsi12 = not args.no_rsi12
    tech_only = args.tech   # 默认 False (全市场)
    # 2026-09-23 改: 默认从 watchlist 选 (--all-market 才全市场; --from-watchlist 是兼容旧 CLI)
    from_watchlist = (not args.all_market) or args.from_watchlist
    volume_spike_th = args.volume_spike
    # 2026-09-23 改: 不再做业绩过滤 (yoy_only/strict_yoy 移除, 只判断 RSI + 放量)

    data_src = "watchlist" if from_watchlist else "全市场"
    print(f"=== RSI 超卖 + 放量 (纯技术面, {data_src}, 0 网络) ===")
    print(f"  1. RSI6 < {args.threshold_rsi6}")
    print(f"  2. RSI12 < {args.threshold_rsi12}" if use_rsi12 else "  2. RSI12 已关闭")
    vol_th = args.volume_spike
    vol_w = args.volume_window
    lb = args.lookback
    print(f"  3. 放量 spike (默认关; --volume-spike {vol_th} 开启; MA{vol_w}, 最近 {lb} 根 K 线任一量/MA{vol_w}>= {vol_th} 即过)")
    print(f"  命中规则: 最近 {lb} 根 K 线任一 RSI6<{args.threshold_rsi6} AND RSI12<{args.threshold_rsi12}  AND  最近 10 根 K 线任一放量 >= {vol_th} 倍")
    print(f"  2026-09-23 改: RSI + 放量 改 AND, 放量固定看最近 10 根 K 线")
    if tech_only: print(f"  + 科技板块限定: {', '.join(sorted(TECH_INDUSTRIES))}")

    from tools.storage.store import DataStore

    if from_watchlist:
        # 2026-09-23 加: 从 watchlist.json 选股
        wl = DataStore.load_watchlist().get("stocks", [])
        allowed_types = set(t.strip() for t in args.watchlist_types.split(","))
        codes = [s["code"] for s in wl if s.get("list_type", "自选") in allowed_types]
        print(f"\n  Watchlist 加载: {len(wl)} 只, 类型过滤 {sorted(allowed_types)} → {len(codes)} 只")
        if not codes:
            print("  ⚠️  watchlist 为空或类型过滤太严, 退出")
            return
        # 不过滤 junk/cap (watchlist 已人工筛过)
        no_junk_filter, include_loss, no_cap = True, True, True
    else:
        codes = DataStore.list_codes()
        print(f"\n  DataStore 加载: {len(codes)} 只 (K线 parquet, 0 网络)")
        codes = _filter_codes(codes, args.no_junk_filter, args.include_loss, args.no_cap)
        print(f"  过滤后: {len(codes)} 只")
        no_junk_filter, include_loss, no_cap = args.no_junk_filter, args.include_loss, args.no_cap

    industry_map = _load_industry_map(codes)
    yoy_map = _load_yoy_map()    # 2026-09-23 改: 加载 yoy_map 用于展示 (不做过滤)
    print(f"  industry_map: {len(industry_map)}, yoy_map: {len(yoy_map)} (展示用, 不过滤)")

    if tech_only:
        before = len(codes)
        codes = [c for c in codes if industry_map.get(c) in TECH_INDUSTRIES]
        print(f"  科技板块过滤: {before} → {len(codes)} 只")

    if args.limit:
        codes = codes[:args.limit]

    work_items = [(c, args.threshold_rsi6, args.threshold_rsi12, args.kline_limit,
                   tech_only, use_rsi12,
                   industry_map.get(c, ""),
                   volume_spike_th, args.volume_window, args.lookback,
                   args.volume_lookback,
                   yoy_map.get(c))           # yoy 仅作展示
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

    # 2026-09-23 加: 命中后 MCP 负面新闻检查 (--check-news 时启用)
    if args.check_news and hits:
        print(f"\n🔎 [step 2/2] MCP 负面新闻检查 ({len(hits)} 只, 近 {args.news_days} 天)")
        # 2026-09-23: 先快速检测 MCP 是否可用 (调一次 resolve_entity)
        sample_inner = _resolve_inner_code_mcp(hits[0]["code"]) if hits else None
        if not sample_inner:
            print("  ⚠️  MCP 在此进程不可用 (connector__hengsheng__* 是 LLM 内置函数)")
            print("  💡 提示: 在交互场景 (LLM agent 在线) 下,我会手工调 MCP 检查每只命中的负面新闻")
            print("  💡 用法: 直接 @agent 跑完这个 scan,把 hits 喂给我,我逐只查 news")
        warned = 0
        for h in hits:
            news = _check_negative_news(h["code"], days=args.news_days)
            h["news_neg_count"] = news["negative_count"]
            h["news_neg_sample"] = news["sample_title"]
            h["news_warn"] = news["warn"] and news["mcp_available"]  # MCP 不可用时不标
            tag = " ⚠️ 有负面" if (news["warn"] and news["mcp_available"]) else ""
            if news["mcp_available"]:
                print(f"    {h['code']} {h['name'][:8]}: 负面 {news['negative_count']} 条{tag}"
                      + (f" | {news['sample_title'][:60]}" if news["sample_title"] else ""))
            if news["warn"] and news["mcp_available"]:
                warned += 1
        if sample_inner:
            print(f"  → {warned}/{len(hits)} 只有未消化负面新闻")

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
        # 2026-09-23 改: 显示业绩字段 (但不参与过滤, 业绩由 blowout 把关)
        print(f"{'代码':<8}{'名称':<10}{'行业':<10}{'营收yoy':<8}{'净利yoy':<8}{'毛利%':<6}{'ROE%':<6}{'RSI6':<7}{'RSI12':<7}{'触发':<10}{'放量比':<8}{'价格':<8}{'质量'}")
        for h in hits:
            rev_s = f"{h.get('rev_yoy'):+.1f}" if h.get('rev_yoy') is not None else "—"
            np_s  = f"{h.get('np_yoy'):+.1f}" if h.get('np_yoy') is not None else "—"
            gm_s  = f"{h.get('gross_margin'):.1f}" if h.get('gross_margin') is not None else "—"
            roe_s = f"{h.get('roe'):.1f}" if h.get('roe') is not None else "—"
            rsi12_s = f"{h['rsi12']:.1f}" if h.get('rsi12') is not None else "—"
            vol_s = f"{h['volume_ratio']:.1f}x" if h.get('volume_ratio') else "—"
            quality_icon = {"extreme": "⭐⭐", "high": "⭐", "medium": "·", "normal": ""}.get(h.get('quality'), "")
            news_warn = " ⚠️" if h.get("news_warn") else ""
            print(f"{h['code']:<8}{h['name'][:8]:<10}{(h['industry'] or '')[:8]:<10}"
                  f"{rev_s:<8}{np_s:<8}{gm_s:<6}{roe_s:<6}"
                  f"{h['rsi6']:<7.2f}{rsi12_s:<7}"
                  f"{h.get('trigger','—'):<10}{vol_s:<8}"
                  f"{h['trigger_price']:<8.2f}{quality_icon}{news_warn}")
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

        md = [f"# RSI 超卖 + 放量 ({today})\n\n"]
        md.append(f"> {data_src} | 触发条件: **{cond_str}** (2026-09-23 改: 纯技术面, 不再做业绩过滤)\n\n")
        n_extreme = sum(1 for h in hits if h.get('quality') == 'extreme')
        n_high = sum(1 for h in hits if h.get('quality') == 'high')
        n_medium = sum(1 for h in hits if h.get('quality') == 'medium')
        md.append(f"**{len(hits)} 只命中** ({n_extreme} 只极限超卖 ⭐⭐, {n_high} 只强超卖 ⭐, {n_medium} 只中度超卖)\n\n")
        md.append("| 代码 | 名称 | 行业 | 营收yoy | 净利yoy | 毛利% | ROE% | RSI6 | RSI12 | 触发 | 放量比 | 触发日 | 价格 | 质量 |\n")
        md.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for h in hits:
            rev_s = f"{h.get('rev_yoy'):+.1f}%" if h.get('rev_yoy') is not None else "—"
            np_s  = f"{h.get('np_yoy'):+.1f}%" if h.get('np_yoy') is not None else "—"
            gm_s  = f"{h.get('gross_margin'):.1f}" if h.get('gross_margin') is not None else "—"
            roe_s = f"{h.get('roe'):.1f}" if h.get('roe') is not None else "—"
            rsi12_s = f"{h['rsi12']:.1f}" if h.get('rsi12') is not None else "—"
            vol_s = f"{h['volume_ratio']:.1f}x" if h.get('volume_ratio') else "—"
            quality_icon = {"extreme": "⭐⭐", "high": "⭐", "medium": "·"}.get(h.get('quality'), "")
            news_mark = " ⚠️" if h.get("news_warn") else ""
            news_info = ""
            if h.get("news_warn") and h.get("news_neg_sample"):
                news_info = f" ⚠️{h.get('news_neg_count',0)}条:{h['news_neg_sample'][:30]}"
            md.append(f"| {h['code']} | {h['name']} | {h['industry']} | {rev_s} | {np_s} | {gm_s} | {roe_s} | "
                     f"{h['rsi6']:.2f} | {rsi12_s} | {h.get('trigger','—')} | {vol_s} | "
                     f"{h['trigger_date']} | ¥{h['trigger_price']:.2f} | {quality_icon}{news_mark}{news_info} |\n")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("".join(md), encoding='utf-8')
        print(f"\n📄 {out_path}")


if __name__ == "__main__":
    main()