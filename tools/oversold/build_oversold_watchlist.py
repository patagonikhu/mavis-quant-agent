"""
build_oversold_watchlist.py — 超跌股 watchlist 构建器 (2026-08-20, user 新策略)

跟现有框架 (缠论/威科夫/OBV) 完全独立:
- ❌ 不读 AnalysisEngine / signals_5method / factor_history
- ❌ 不读 fflow / EPS / 估值
- ✅ 拉全 A 股 weekly 250 根 → 筛跌幅 ≥ 70% → 写 watchlist_oversold.json
- ✅ 走 dump_oversold/ 缓存, 跟 data/dump/ 不冲突
- ✅ 输出 watchlist_oversold.json 跟 watchlist.json 同结构, 复用 refresh_all.sh

设计原则:
- 单文件, 单函数, 简单可读
- Tushare 200/分 频控: 5000 只 × 1 weekly = ~25 分钟
- 失败不 raise: 跳过单只, 跑完统计 OK/FAIL
- ST/*ST/退市 自动过滤
- 数据不足 (新股, < 50 根 weekly) 跳过

用法:
    bash tools/with_venv.sh python -m tools.oversold.build_oversold_watchlist
    # 或
    bash tools/with_venv.sh python -m tools.oversold.build_oversold_watchlist \\
        --drop-threshold 0.60 --top 50
"""
import os
import re
import sys
import json
import time
import logging
import argparse
import threading
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

# Tushare 走 sync_stock.py / tushare_fetcher.py 同套 token 加载
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.fetch.tushare_fetcher import _PRO, _load_dotenv  # noqa: E402

_load_dotenv()

# === 配置 ===
DUMP_OVER_DIR = "data/dump_oversold"          # lite dump 缓存
WATCHLIST_OUT = "data/watchlist_oversold.json"  # 主输出
DOCS_OUT = "docs/oversold.md"                  # 人读报告

WEEKLY_LIMIT = 250        # 250 根 weekly ≈ 5 年
WEEKLY_START = "20200101"  # 6 年前, 保证够 250 根
MIN_WEEKLY_BARS = 50       # < 50 根 (新股) 跳过
DROP_THRESHOLD = 0.70      # 默认跌幅阈值 70%
TOP_N_MD = 30              # markdown 表格显示前 N 只
FIELDS_WEEKLY = "ts_code,trade_date,open,high,low,close,vol,amount,pct_chg"
FIELDS_BASIC = "ts_code,symbol,name,industry,list_date,market"

ST_PATTERN = re.compile(r'ST|\*ST|退市|暂停|终止')
logger = logging.getLogger("oversold")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_env_token() -> str:
    """读 .env 的 TUSHARE_TOKEN (走 tushare_fetcher 同样的 _load_dotenv 已有,
    但本模块也兜底再 load 一次)"""
    return os.environ.get("TUSHARE_TOKEN", "").strip()


def _to_code(ts_code: str) -> str:
    """000725.SZ → 000725"""
    return ts_code.split(".")[0] if ts_code else ""


def is_st_or_delisted(name: str) -> bool:
    if not name:
        return False
    return bool(ST_PATTERN.search(name))


def fetch_all_a_stocks() -> List[Dict[str, Any]]:
    """调 Tushare pro.stock_basic 拿全 A 股 list (~5000+ 只, 1 call)"""
    if not _PRO:
        raise RuntimeError("TUSHARE_TOKEN 未配置, _PRO 不可用")
    df = _PRO.stock_basic(list_status='L', fields=FIELDS_BASIC)
    if df is None or len(df) == 0:
        raise RuntimeError("pro.stock_basic 返空, 检查 token / 积分档")
    return df.to_dict("records")


def fetch_weekly_one(ts_code: str) -> Tuple[List[Dict[str, Any]], str]:
    """从本地历史库读日线并聚合成周线（走 DataStore，0 网络）"""
    try:
        from tools.data_store import DataStore
        from tools.fetch.data_fetcher import _synthesize_weekly
        code = _to_code(ts_code)
        kline = DataStore.get_kline(code, limit=WEEKLY_LIMIT * 5)
        if not kline:
            return [], "EMPTY"
        weekly = _synthesize_weekly(kline)
        if not weekly:
            return [], "EMPTY"
        # 统一字段格式（与原 tushare weekly 对齐）
        result = []
        for w in weekly[-WEEKLY_LIMIT:]:
            result.append({
                "trade_date": str(w.get("trade_date", "")).replace("-", ""),
                "open":  w.get("open",  0),
                "high":  w.get("high",  0),
                "low":   w.get("low",   0),
                "close": w.get("close", 0),
                "vol":   w.get("volume", w.get("vol", 0)),
            })
        return result, "OK"
    except Exception as e:
        return [], f"ERR_{type(e).__name__}"


def compute_drop(weekly: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """算超跌指标. weekly 已按 trade_date 升序."""
    if len(weekly) < MIN_WEEKLY_BARS:
        return None
    closes = [k["close"] for k in weekly if k.get("close")]
    if not closes:
        return None
    n_high = max(closes)
    n_low = min(closes)
    cur = closes[-1]
    if n_high <= 0:
        return None
    drop_pct = (cur - n_high) / n_high
    bounce_pct = (cur - n_low) / n_low if n_low > 0 else 0.0
    return {
        "current_price": cur,
        "n_week_high": n_high,
        "n_week_low": n_low,
        "drop_pct": drop_pct,
        "bounce_pct": bounce_pct,
        "n_bars_used": len(closes),
        "as_of": weekly[-1].get("trade_date", "?"),
    }


def is_oversold(drop_pct: float, threshold: float = DROP_THRESHOLD) -> bool:
    return drop_pct <= -threshold


def process_one(basic: Dict[str, Any], drop_threshold: float,
                incremental: bool = True, max_age_days: int = 7) -> Tuple[str, Dict[str, Any]]:
    """单只全流程: ST 过滤 + 读本地 weekly + 算 drop。

    weekly 从本地历史库读（DataStore），0 网络调用。
    返回 (status, payload):
      - ('pick', {...})    命中超跌
      - ('ok', {...})      未超跌
      - ('skip_st', {...}) ST/退市
      - ('short', {...})   数据不足
      - ('err', {...})     异常
    """
    ts_code = basic.get("ts_code", "")
    code = _to_code(ts_code)
    name = basic.get("name", "")
    industry = basic.get("industry", "")
    meta = {"code": code, "name": name, "industry": industry, "ts_code": ts_code}

    if is_st_or_delisted(name):
        return "skip_st", meta

    weekly, status = fetch_weekly_one(ts_code)
    if not weekly:
        return "err", {**meta, "status": status, "weekly": []}

    m = compute_drop(weekly)
    if m is None:
        return "short", {**meta, "weekly": weekly}

    payload = {**meta, "weekly": weekly, **m}
    if is_oversold(m["drop_pct"], drop_threshold):
        return "pick", payload
    return "ok", payload


def build_watchlist(picks: List[Dict[str, Any]], drop_threshold: float,
                    old_picks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """输出 data/watchlist_oversold.json (跟 data/watchlist.json 同结构)

    合并策略 (v2, user 8-20 拍板):
      - 读旧 watchlist (如存在) 的 picks
      - 合并新 picks (按 code 去重)
      - 跑多次 picks 累加, 不覆盖

    跌幅阈值 100% (default threshold) 仍可调整 (e.g. --drop-threshold 0.50 看 50%+ 跌)
    """
    # 合并 picks
    by_code: Dict[str, Dict[str, Any]] = {}
    for p in (old_picks or []):
        by_code[p["code"]] = p
    for p in picks:
        by_code[p["code"]] = p  # 覆盖 (新数据)

    merged = list(by_code.values())
    merged.sort(key=lambda x: x.get("drop_pct", 0))

    stocks = []
    for p in merged:
        code = p["code"]
        name = p["name"]
        industry = p["industry"]
        cur = p["current_price"]
        n_high = p["n_week_high"]
        n_low = p["n_week_low"]
        drop_pct = p["drop_pct"]
        bounce_pct = p["bounce_pct"]
        notes = (
            f"[auto {drop_threshold*100:.0f}% drop @ {_now()[:10]}] "
            f"from ¥{n_high:.2f} ({WEEKLY_LIMIT}w high) to ¥{cur:.2f} (now), "
            f"跌 {drop_pct*100:.1f}%, 反弹 {bounce_pct*100:+.1f}% from {WEEKLY_LIMIT}w low ¥{n_low:.2f}"
        )
        stocks.append({
            "code": code,
            "name": name,
            "sector": industry,
            "notes": notes,
        })
    return {
        "version": "1.0",
        "last_updated": _now()[:10],
        "description": (
            f"Auto-generated 超跌股 watchlist (merge). 跌幅 ≥ {drop_threshold*100:.0f}% from "
            f"{WEEKLY_LIMIT} 根 weekly high ({WEEKLY_START} 至今). 跟 watchlist.json 同结构, "
            f"复用 refresh_all.sh / sync_stock.py 走完整分析. 多次跑 picks 累加. "
            f"来源: tools/oversold/build_oversold_watchlist.py"
        ),
        "stocks": stocks,
    }


def render_md(results: List[Dict[str, Any]], watchlist: Dict[str, Any], stats: Dict[str, Any],
              drop_threshold: float, weekly_limit: int = WEEKLY_LIMIT, top_n_md: int = TOP_N_MD) -> str:
    """生成 docs/oversold.md (人读报告)"""
    lines = [
        f"# 超跌股 watchlist (auto-generated, {_now()[:10]})",
        "",
        f"> 来源: `tools/oversold/build_oversold_watchlist.py`  ",
        f"> 跌幅阈值: ≥ {drop_threshold*100:.0f}% from {weekly_limit} 根 weekly high ({WEEKLY_START} 至今)  ",
        f"> 全 A 股扫描: {stats['total_scanned']} 只 → 筛出 {len(results)} 只 (排除 ST/*ST/退市/{stats['skip_short_hist']} 数据不足)  ",
        f"> 拉取: ✅{stats['weekly_ok']} / ❌{stats['weekly_fail']} (频控 {stats.get('rate_limited', 0)} / 权限 {stats.get('perm_denied', 0)} / 其它 {stats.get('other_fail', 0)})  ",
        f"> 耗时: {stats['elapsed_sec']:.0f} 秒 ({stats['elapsed_sec']/60:.1f} 分钟)",
        "",
        f"## 📋 摘要",
        "",
        f"- **筛选结果**: {len(results)} 只超跌股 (跌 ≥ {drop_threshold*100:.0f}%)",
        f"- **平均跌幅**: {sum(r['drop_pct'] for r in results)/max(len(results),1)*100:.1f}%",
        f"- **平均反弹**: {sum(r['bounce_pct'] for r in results)/max(len(results),1)*100:+.1f}%",
        f"- **行业分布**: {len(set(r['industry'] for r in results))} 个行业",
        f"- **输出 watchlist**: `{WATCHLIST_OUT}` ({len(watchlist['stocks'])} 只, 可直接 `bash tools/refresh_all.sh --watchlist {WATCHLIST_OUT}` 跑完整 analysis)",
        "",
        f"## 🎯 Top {min(top_n_md, len(results))} (按跌幅排序)",
        "",
        "| 代码 | 名称 | 行业 | 当前价 | {n}周高 | 跌幅 | 反弹% | as_of |".format(n=weekly_limit),
        "|------|------|------|--------|---------|------|-------|-------|",
    ]
    for r in results[:top_n_md]:
        lines.append(
            f"| {r['code']} | {r['name']} | {r['industry']} | "
            f"¥{r['current_price']:.2f} | ¥{r['n_week_high']:.2f} | "
            f"{r['drop_pct']*100:.1f}% | {r['bounce_pct']*100:+.1f}% | {r['as_of']} |"
        )
    if not results:
        lines.append("| - | - | - | - | - | - | - | - |")
        lines.append("")
        lines.append(f"**未发现跌幅 ≥ {drop_threshold*100:.0f}% 的超跌股** (回看 {weekly_limit} 根 weekly)")
    lines.extend([
        "",
        f"## 📁 输出文件",
        "",
        f"- `{WATCHLIST_OUT}` — 新 watchlist, 跟 `data/watchlist.json` 同结构, 可走完整 analysis",
        f"- `{DUMP_OVER_DIR}/{{code}}.json` — lite dump 缓存 (weekly {weekly_limit} 根 + name/industry), {len(results)} 个",
        f"- `docs/oversold.md` — 本报告",
        "",
        f"## 🚀 下一步",
        "",
        f"```bash",
        f"# 1. 拉完整 dump (走 sync_stock.py 路径, fflow/eps/daily/60m)",
        f"bash tools/refresh_all.sh --watchlist {WATCHLIST_OUT} --workers 4",
        f"",
        f"# 2. 看强信号汇总",
        f"cat docs/signal-watchlist.md",
        f"```",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="全 A 股 weekly 扫描, 找跌幅≥阈值的超跌股, 写新 watchlist"
    )
    parser.add_argument("--drop-threshold", type=float, default=DROP_THRESHOLD,
                        help=f"跌幅阈值 (默认 {DROP_THRESHOLD*100:.0f}%%)")
    parser.add_argument("--weekly-limit", type=int, default=WEEKLY_LIMIT,
                        help=f"weekly 拉取根数 (默认 {WEEKLY_LIMIT} ≈ 5 年)")
    parser.add_argument("--top", type=int, default=TOP_N_MD,
                        help=f"markdown 表格显示前 N (默认 {TOP_N_MD})")
    parser.add_argument("--max-codes", type=int, default=0,
                        help="限制处理只数 (调试用, 0=不限制)")
    parser.add_argument("--workers", type=int, default=8,
                        help="并发 worker 数 (默认 8, 单线程 = 1)")
    parser.add_argument("--no-incremental", action="store_true",
                        help="关闭增量模式 (默认开, 跳过已有 dump_oversold/{code}.json)")
    parser.add_argument("--max-age-days", type=int, default=7,
                        help="dump_oversold/{code}.json as_of 距今 < N 天才 cached, 否则重拉 (默认 7)")
    args = parser.parse_args()

    # 用 local 变量覆盖 module-level
    weekly_limit = args.weekly_limit
    top_n_md = args.top
    workers = max(1, args.workers)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not _load_env_token():
        print("❌ TUSHARE_TOKEN 未配置, 检查 .env 文件", file=sys.stderr)
        return 1

    # 先补缺失交易日（全市场增量，无缺口秒返回）
    print("🔄 同步K线历史...")
    from tools.history_sync import sync_incremental
    sync_incremental()

    print(f"🔄 全 A 股 weekly 扫描 (跌幅 ≥ {args.drop_threshold*100:.0f}%, weekly {weekly_limit} 根)")
    print(f"⏰ 开始: {_now()}")
    t0 = time.time()

    # 1. 拿全 A 股 list
    print("📋 Step 1/3: 拉全 A 股 stock_basic list...")
    all_stocks = fetch_all_a_stocks()
    print(f"   ✅ 拿到 {len(all_stocks)} 只")
    if args.max_codes:
        all_stocks = all_stocks[:args.max_codes]
        print(f"   ⚠️ 限制前 {args.max_codes} 只 (调试)")

    # 增量模式: 统计已分析的 (跳过 ST 后, 剩下的)
    incremental = not args.no_incremental
    if incremental:
        os.makedirs(DUMP_OVER_DIR, exist_ok=True)
        # 只算 status="ok" 的 dump (有 weekly 数据, 真分析过), fail placeholder 算未分析 (重试)
        existing_ok = set()
        for f in os.listdir(DUMP_OVER_DIR):
            if not f.endswith(".json") or f.startswith("_"):
                continue
            try:
                with open(os.path.join(DUMP_OVER_DIR, f), encoding="utf-8") as fp:
                    d = json.load(fp)
                if d.get("status") == "ok" and d.get("weekly"):
                    existing_ok.add(f[:-5])
            except Exception:
                pass
        # 排除 ST (他们没 dump, 但也不该跑)
        non_st = [b for b in all_stocks if not is_st_or_delisted(b.get("name", ""))]
        st_count = len(all_stocks) - len(non_st)
        todo = [b for b in non_st if _to_code(b.get("ts_code", "")) not in existing_ok]
        cached_count = len(non_st) - len(todo)
        print(f"   📂 增量: 已分析 {len(existing_ok)} 只, 待跑 {len(todo)} 只 (ST 跳过 {st_count})")
        all_stocks = todo  # 只跑待跑列表

    # 2. 遍历拉 weekly (并发)
    print(f"📦 Step 2/3: 遍历拉 weekly ({workers} worker 并发)...")
    picks: List[Dict[str, Any]] = []
    stats = {
        "total_scanned": len(all_stocks),
        "weekly_ok": 0,
        "weekly_fail": 0,
        "cached": 0,
        "rate_limited": 0,
        "perm_denied": 0,
        "other_fail": 0,
        "skip_short_hist": 0,
        "skip_st": 0,
        "skip_no_drop": 0,
    }
    last_report = time.time()
    done_count = 0
    lock = threading.Lock()

    def handle_result(status: str, payload: Dict[str, Any]):
        """主线程回调: 写 dump + 收集 picks + 更新 stats"""
        nonlocal done_count
        with lock:
            done_count += 1
            cur = done_count
        if status == "pick":
            stats["weekly_ok"] += 1
            # 写 lite dump (含 weekly, status=ok)
            try:
                basic_for_dump = {
                    "name": payload["name"],
                    "industry": payload["industry"],
                }
                write_dump_lite(payload["code"], basic_for_dump, payload["weekly"], status="ok")
            except Exception as e:
                logger.warning("write dump_oversold %s fail: %s", payload["code"], e)
            picks.append({k: v for k, v in payload.items() if k not in ("weekly", "ts_code")})
        elif status == "ok":
            stats["weekly_ok"] += 1
            stats["skip_no_drop"] += 1
            # 写 lite dump (含 weekly, status=ok, 涨幅不够不写 picks)
            try:
                basic_for_dump = {
                    "name": payload["name"],
                    "industry": payload["industry"],
                }
                write_dump_lite(payload["code"], basic_for_dump, payload["weekly"], status="ok")
            except Exception as e:
                logger.warning("write dump_oversold %s fail: %s", payload["code"], e)
        elif status == "short":
            stats["weekly_ok"] += 1
            stats["skip_short_hist"] += 1
            # 写 dump (status=ok + 短 weekly, 标记已分析)
            try:
                basic_for_dump = {
                    "name": payload["name"],
                    "industry": payload["industry"],
                }
                write_dump_lite(payload["code"], basic_for_dump, payload["weekly"], status="ok")
            except Exception as e:
                logger.warning("write dump_oversold %s fail: %s", payload["code"], e)
        elif status == "cached":
            stats["cached"] += 1
            # 缓存的也算 picks/ok
            if is_oversold(payload.get("drop_pct", 0), args.drop_threshold):
                picks.append({k: v for k, v in payload.items() if k not in ("weekly", "ts_code")})
        elif status == "skip_st":
            stats["skip_st"] += 1
            # ST 不写 dump (永久 skip)
        elif status == "rate":
            stats["weekly_fail"] += 1
            stats["rate_limited"] += 1
            # 写 fail placeholder (频控临时错, 下次重试)
            try:
                basic_for_dump = {
                    "name": payload["name"],
                    "industry": payload["industry"],
                }
                write_dump_lite(payload["code"], basic_for_dump, [], status="fail", reason="RATE_LIMITED")
            except Exception as e:
                logger.warning("write dump_oversold %s fail: %s", payload["code"], e)
        elif status == "perm":
            stats["weekly_fail"] += 1
            stats["perm_denied"] += 1
            # 写 fail placeholder (权限问题, 重试大概率还 fail)
            try:
                basic_for_dump = {
                    "name": payload["name"],
                    "industry": payload["industry"],
                }
                write_dump_lite(payload["code"], basic_for_dump, [], status="fail", reason="PERM_DENIED")
            except Exception as e:
                logger.warning("write dump_oversold %s fail: %s", payload["code"], e)
        else:  # err
            stats["weekly_fail"] += 1
            stats["other_fail"] += 1
            # 写 fail placeholder
            try:
                reason = payload.get("status", "ERR")
                basic_for_dump = {
                    "name": payload["name"],
                    "industry": payload["industry"],
                }
                write_dump_lite(payload["code"], basic_for_dump, [], status="fail", reason=reason)
            except Exception as e:
                logger.warning("write dump_oversold %s fail: %s", payload["code"], e)

        # 进度打印 (每 200 只或每 10 秒)
        if cur % 200 == 0 or (time.time() - nonlocal_last_report[0]) > 10:
            elapsed = time.time() - t0
            rate = cur / elapsed if elapsed else 0
            eta = (len(all_stocks) - cur) / rate if rate else 0
            print(f"   [{cur}/{len(all_stocks)}] ok=✅{stats['weekly_ok']} picks={len(picks)} "
                  f"skip_st={stats['skip_st']} short={stats['skip_short_hist']} "
                  f"fail={stats['weekly_fail']} (rate={stats['rate_limited']}) "
                  f"rate={rate:.0f}/s elapsed={elapsed:.0f}s eta={eta:.0f}s")
            nonlocal_last_report[0] = time.time()

    nonlocal_last_report = [last_report]

    if workers == 1:
        for basic in all_stocks:
            status, payload = process_one(basic, args.drop_threshold, incremental, args.max_age_days)
            handle_result(status, payload)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(process_one, b, args.drop_threshold, incremental, args.max_age_days) for b in all_stocks]
            for f in as_completed(futures):
                try:
                    status, payload = f.result()
                    handle_result(status, payload)
                except Exception as e:
                    logger.warning("process_one exception: %s", e)

    # 按跌幅降序
    picks.sort(key=lambda x: x["drop_pct"])

    # 3. 写 watchlist_oversold.json + docs/oversold.md
    print(f"📝 Step 3/3: 写 watchlist_oversold.json + docs/oversold.md...")
    os.makedirs("data", exist_ok=True)
    os.makedirs("docs", exist_ok=True)

    # 合并旧 watchlist picks (增量累加, user 8-20 拍板)
    old_picks: List[Dict[str, Any]] = []
    if os.path.exists(WATCHLIST_OUT):
        try:
            with open(WATCHLIST_OUT, encoding="utf-8") as f:
                old_wl = json.load(f)
            for s in old_wl.get("stocks", []):
                # 从 notes 解析 drop_pct 等 (旧 watchlist 没存 drop_pct, 重新从 dump 算)
                code = s["code"]
                dump_fp = os.path.join(DUMP_OVER_DIR, f"{code}.json")
                if os.path.exists(dump_fp):
                    try:
                        with open(dump_fp, encoding="utf-8") as f:
                            d = json.load(f)
                        if d.get("status") == "ok" and d.get("weekly"):
                            m = compute_drop(d["weekly"])
                            if m and m["drop_pct"] <= -args.drop_threshold:
                                old_picks.append({
                                    "code": code,
                                    "name": d.get("name", s.get("name", "")),
                                    "industry": d.get("industry", s.get("sector", "")),
                                    **m,
                                })
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("read old watchlist fail: %s", e)

    watchlist = build_watchlist(picks, args.drop_threshold, old_picks=old_picks)
    with open(WATCHLIST_OUT, "w", encoding="utf-8") as f:
        json.dump(watchlist, f, ensure_ascii=False, indent=2)

    stats["elapsed_sec"] = time.time() - t0
    md = render_md(picks, watchlist, stats, args.drop_threshold,
                   weekly_limit=weekly_limit, top_n_md=top_n_md)
    with open(DOCS_OUT, "w", encoding="utf-8") as f:
        f.write(md)

    # 控制台输出
    elapsed = stats["elapsed_sec"]
    print()
    print("=" * 60)
    # picks 局部 = 本次新增, watchlist["stocks"] = 累计 (含 cached)
    new_picks_count = len(picks)
    total_picks_count = len(watchlist["stocks"])
    print(f"✅ 完成: 本次新 {new_picks_count} 只, 累计 {total_picks_count} 只超跌股 (跌幅 ≥ {args.drop_threshold*100:.0f}%)")
    print(f"   扫描 {stats['total_scanned']} 只 → 排除 ST {stats['skip_st']} / 数据不足 {stats['skip_short_hist']} / 跌幅不够 {stats['skip_no_drop']}")
    print(f"   本次 weekly 拉取: ✅{stats['weekly_ok']} / ❌{stats['weekly_fail']} (频控 {stats['rate_limited']} / 权限 {stats['perm_denied']} / 其它 {stats['other_fail']})")
    print(f"   累计 cached 跳过: {stats['cached']}")
    print(f"   耗时: {elapsed:.0f} 秒 ({elapsed/60:.1f} 分钟)")
    print(f"   📁 {WATCHLIST_OUT} ({total_picks_count} 只累计)")
    print(f"   📁 {DOCS_OUT}")
    print("=" * 60)
    if total_picks_count:
        print()
        # 显示累计 top 5 (从 watchlist 读, 不是本次)
        for s in watchlist["stocks"][:5]:
            print(f"   {s['code']} {s['name']} ({s['sector']}) — {s['notes']}")
        print()
        print(f"🚀 下一步: bash tools/refresh_all.sh --watchlist {WATCHLIST_OUT} --workers 4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
