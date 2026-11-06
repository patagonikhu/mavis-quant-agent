"""独立验证 (v2): 按**行号**给生产函数打标记, 直接捕获实际执行的分支

方法 (比 v1 稳):
  1. 读 stage_factor.py 源码
  2. 用 ast 找到 wyckoff_judge 里判定链那几个 If 的**行号**
  3. 在这些行对应的 body 开头插入 `CHANNEL_LOG.append(...)`
  4. exec 改写后的模块 → 逻辑完全等价, 但能记录走了哪条分支

先自检: 插桩后必须能看到 CHANNEL_LOG 字样, 否则直接报错 (不静默通过)。
上一版 (v1) 静默没插上, 全部报"无标记", 白跑一轮 —— 所以这次加硬断言。

跑法: bash tools/with_venv.sh python -m tools.batch._verify_fb_channel_ast
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.factors.wyckoff.helpers import FB1_TREND30_MIN

SRC = ROOT / "tools" / "factors" / "wyckoff" / "stage_factor.py"


def _tag(stmt):
    """生成一行 CHANNEL_LOG.append(...) 语句"""
    return ast.parse(stmt).body[0]


class _Injector(ast.NodeTransformer):
    """按行号把标记插进指定 body"""

    def __init__(self, plan):
        # plan: {lineno: tag_statement}
        self.plan = plan

    def _inject(self, body, lineno):
        """在 body 列表里找到 lineno 对应的节点, 在它前面插标记"""
        for i, st in enumerate(body):
            # 节点的 lineno 是它的第一条语句的行号
            if getattr(st, "lineno", None) == lineno:
                body.insert(i, _tag(self.plan[lineno]))
                return True
        return False

    def visit_If(self, node):
        # 不 generic_visit, 手工处理, 避免 elif 链被提前改结构
        # 先处理本 If 的 body / orelse
        lineno = node.lineno
        if lineno in self.plan:
            node.body.insert(0, _tag(self.plan[lineno]))
        # 递归 orelse 里的 If (elif)
        for i, st in enumerate(node.orelse):
            if isinstance(st, ast.If):
                node.orelse[i] = self.visit(st)
        # 递归 body 里的 If
        for i, st in enumerate(node.body):
            if isinstance(st, ast.If):
                node.body[i] = self.visit(st)
        return node


def build_patched():
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # ── 定位判定链的各个 If 的行号 ──
    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "wyckoff_judge":
            fn = node
            break
    if fn is None:
        raise SystemExit("找不到 wyckoff_judge")

    main_line = None
    fb1_line = None
    fb2_line = None
    fb2_markup_line = None
    fb2_accum_line = None

    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        c = ast.unparse(node.test)
        if c == "max(scores.values()) > 0":
            main_line = node.lineno
        elif "slope_60 > 0" in c and "trend_30 > FB1_TREND30_MIN" in c:
            fb1_line = node.lineno
        elif c == "fb2_base":
            fb2_line = node.lineno
            # fb2 内部两个分支 —— ⚠️ 必须扫 orelse!
            #   AST 里 `elif` 链是 orelse, 不是 body:
            #     body   = [If(slope_60>5  and trend_30>5)]   ← Markup 分支
            #     orelse = [If(slope_60<-5 and trend_30<-5)]  ← Accumulation 分支
            #   v1 只扫 node.body → 漏掉 Accumulation 分支 → 报 "fb2_accum=0 只" (假象!)
            for sub in ast.walk(node):
                if sub is node or not isinstance(sub, ast.If):
                    continue
                ct = ast.unparse(sub.test)
                if "slope_60 > 5" in ct and "trend_30 > 5" in ct:
                    fb2_markup_line = sub.lineno
                elif "slope_60 < -5" in ct and "trend_30 < -5" in ct:
                    fb2_accum_line = sub.lineno

    print(f"[插桩定位] main={main_line} fb1={fb1_line} fb2={fb2_line} "
          f"fb2_markup={fb2_markup_line} fb2_accum={fb2_accum_line}")

    if not (main_line and fb1_line and fb2_line):
        raise SystemExit("定位失败: 判定链没找全")
    if not (fb2_markup_line and fb2_accum_line):
        raise SystemExit(
            f"定位失败: fb2 内部分支没找全 "
            f"(markup={fb2_markup_line}, accum={fb2_accum_line}) —— "
            f"⚠️ 漏标会让该分支的命中数变成 0 (假象), 必须修"
        )

    plan = {
        main_line: "CHANNEL_LOG.append(('main', max(scores, key=scores.get)))",
        fb1_line: "CHANNEL_LOG.append(('fb1', 'Markup'))",
        fb2_line: "CHANNEL_LOG.append(('fb2_base', 'entered'))",
    }
    if fb2_markup_line:
        plan[fb2_markup_line] = "CHANNEL_LOG.append(('fb2_markup', 'Markup'))"
    if fb2_accum_line:
        plan[fb2_accum_line] = "CHANNEL_LOG.append(('fb2_accum', 'Accumulation'))"

    # ── 手动遍历所有 If, 逐个插标记 (不用 NodeTransformer, 避免结构被改) ──
    def inject(body):
        """body: 语句列表。找到 lineno 命中的 If, 在其 body 开头插标记"""
        for st in body:
            if isinstance(st, ast.If):
                if st.lineno in plan:
                    st.body.insert(0, _tag(plan[st.lineno]))
                    # fb2_base 内部的子 If 也要插
                    for sub in st.body:
                        if isinstance(sub, ast.If) and sub.lineno in plan:
                            sub.body.insert(0, _tag(plan[sub.lineno]))
                # 递归
                inject(st.body)
                inject(st.orelse)

    inject(fn.body)

    # ── 硬断言: 插桩必须生效 ──
    has_tag = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "append"
        for n in ast.walk(fn)
    )
    if not has_tag:
        raise SystemExit("❌ 插桩未生效 (硬断言失败), 终止")

    prelude = ast.parse("CHANNEL_LOG = []\n").body
    mod = ast.Module(body=prelude + tree.body, type_ignores=[])
    ast.fix_missing_locations(mod)
    g = {"__name__": "patched_stage_factor", "__file__": str(SRC)}
    exec(compile(mod, str(SRC), "exec"), g)
    return g


def main():
    from tools.batch.wyckoff_a2m_scan import (
        _load_all_klines, _filter_by_st, _filter_by_pe, _filter_by_quality,
        _filter_by_leverage, load_codes_all_market, load_stock_basic,
    )
    from tools.factors.kline_arrays import build_kline_features
    from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events

    g = build_patched()
    wyckoff_judge = g["wyckoff_judge"]
    log = g["CHANNEL_LOG"]

    # ── 冒烟测试: 确认插桩真的在记东西 ──
    codes = load_codes_all_market()
    codes = _filter_by_st(codes)
    codes = _filter_by_pe(codes, 60.0)
    codes = _filter_by_quality(codes, or_yoy_min=0, gm_min=0, np_yoy_min=0,
                              roe_min=5.0, gm_abs_min=20.0)
    codes = _filter_by_leverage(codes, 0.5)
    print(f"候选池 {len(codes)}", flush=True)
    all_kl = _load_all_klines(codes, limit_per_code=2000)
    basic = load_stock_basic()
    name_by_code = {}
    if isinstance(basic, dict):
        for c, v in basic.items():
            name_by_code[c] = str(v[0]) if isinstance(v, (tuple, list)) and v else str(v)

    from collections import Counter
    chan_counter = Counter()
    rows = []
    n = 0

    for code, df in sorted(all_kl.items()):
        if df is None or len(df) < 220:
            continue
        closes = df['close'].tolist()
        highs = df['high'].tolist()
        lows = df['low'].tolist()
        vols = df['vol'].tolist()
        opens = df['open'].tolist() if 'open' in df.columns else closes
        pct_chgs = df['pct_chg'].tolist() if 'pct_chg' in df.columns else None
        dates_ = df['date'].astype(str).str.replace('-', '').str[:8].tolist()
        nn = len(closes)
        arrs = build_kline_features(closes, highs, lows, vols, window=min(250, nn))
        evs = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
                              period_label="daily", code=code, as_of_idx=None,
                              dates=dates_, precomputed_arrs=arrs)
        i = nn - 1
        sub_all = {e["name"] for e in evs if e["idx"] <= i}

        log.clear()
        out = wyckoff_judge(i, arrs, sub_all, window=arrs['window'], period_label="daily")
        if out is None:
            continue
        n += 1
        events = list(log)
        if not events:
            chan = "(else '?')"
        elif events[0][0] == "fb2_base":
            chan = events[1][0] if len(events) > 1 else "fb2_base→(内部分支不满足→'?')"
        else:
            chan = events[0][0]
        chan_counter[chan] += 1

        fv = out.get("factor_values", {}) or {}
        sc = out.get("scores", {}) or {}
        rows.append({
            "code": code, "name": name_by_code.get(code, code),
            "stage": out.get("stage"), "chan": chan, "scores": sc,
            "slope_60": fv.get("slope_60"), "trend_30": fv.get("trend_30"),
            "dev_ma200": fv.get("dev_ma200"), "ma50": fv.get("ma50"),
            "ma200": fv.get("ma200"), "n_sub": len(sub_all), "subs": sorted(sub_all),
            "above_ma200": fv.get("above_ma200"),
            "ma_gap_pct_above": fv.get("ma_gap_pct_above"),
            "is_uptrend": fv.get("is_uptrend"),
            "golden_cross_recent": fv.get("golden_cross_recent"),
            "above_ma200_sustained": fv.get("above_ma200_sustained"),
            "dev_ma20": fv.get("dev_ma20"), "pos_pct": fv.get("pos_pct"),
            "ma50_angle_5d": fv.get("ma50_angle_5d"),
            "wta": (out.get("markup_detail", {}) or {}).get("wta_markup_hit"),
        })

    # ── 硬断言: 插桩必须记录到东西 ──
    if chan_counter.get("(else '?')", 0) == n:
        raise SystemExit("❌ 全部都是 (else '?'), 插桩没生效 —— 终止, 不出结论")

    print(f"\n有效池 {n} 只")
    print("\n★ AST 插桩捕获的**实际执行分支**分布:")
    for ch, c in chan_counter.most_common():
        print(f"   {ch:32s}: {c:3d}")

    def show(title, group):
        print("\n" + "=" * 112)
        print(title)
        print("=" * 112)
        if not group:
            print("  (空)")
            return
        print(f"  {'代码':8s} {'名称':10s} {'stage':13s} {'acc/mk/dis':>11s} "
              f"{'slope_60':>9s} {'trend_30':>9s} {'dev_ma200':>10s} {'n_sub':>6s}")
        for r in sorted(group, key=lambda x: -(x["slope_60"] or 0)):
            sm = f"{r['scores'].get('Accumulation')}/{r['scores'].get('Markup')}/{r['scores'].get('Distribution')}"
            print(f"  {r['code']:8s} {r['name'][:10]:10s} {str(r['stage']):13s} {sm:>11s} "
                  f"{r['slope_60']:+9.2f} {r['trend_30']:+9.2f} {r['dev_ma200']:+10.2f} "
                  f"{r['n_sub']:6d}")
            if r["subs"]:
                print(f"  {'':8s} {'':10s}   sub: {', '.join(r['subs'])}")

    show(f"★ 真走 fb1 的 ({sum(1 for r in rows if r['chan']=='fb1')} 只)",
         [r for r in rows if r["chan"] == "fb1"])
    show(f"★ 真走 fb2_markup 的 ({sum(1 for r in rows if r['chan']=='fb2_markup')} 只)",
         [r for r in rows if r["chan"] == "fb2_markup"])
    show(f"★ 真走 fb2_accum 的 ({sum(1 for r in rows if r['chan']=='fb2_accum')} 只)",
         [r for r in rows if r["chan"] == "fb2_accum"])

    # ── Markup 票的主路径细分 ──
    print("\n" + "=" * 112)
    print("Markup 票的主路径细分 (score_Markup 是从哪条分支打出来的)")
    print("=" * 112)
    detail = Counter()
    mrows = []
    for r in rows:
        if r["stage"] != "Markup":
            continue
        if r["golden_cross_recent"] and r["above_ma200_sustained"] \
                and (r["ma_gap_pct_above"] or 0) > 0.5 and r["is_uptrend"]:
            p = "① 强信号 (金叉+持续上方+gap>0.5%+上升趋势)"
        elif r["above_ma200"] and (r["ma_gap_pct_above"] or 0) > 0.5 and r["is_uptrend"]:
            p = "② 弱信号 (MA200上方+gap>0.5%+上升趋势)"
        elif (r["dev_ma20"] or 0) > 5 and (r["pos_pct"] or 0) > 60 and r["is_uptrend"]:
            p = "③ 加分线 (MA20偏离>5%+位置>60%+上升趋势)"
        else:
            p = "④ 未匹配 (score_Markup>0 但 3 条都不匹配)"
        detail[p] += 1
        mrows.append((r, p))
    for p, c in detail.most_common():
        print(f"  {p:48s}: {c:3d}")

    # ── 渲染器会怎么标 (对照) ──
    print("\n" + "=" * 112)
    print("对照: 渲染器 (report_renderer L451-453) 会给这些票标什么")
    print("=" * 112)
    from tools.factors.wyckoff.helpers import _ma50_reached_ma200
    disp_counter = Counter()
    for r in rows:
        if r["stage"] != "Markup" or r["scores"].get("Markup", 0) <= 0:
            continue
        if r["golden_cross_recent"] and r["above_ma200_sustained"]:
            disp_counter["强信号 (金叉+持续上方)"] += 1
            continue
        _fb1 = (r["slope_60"] > 0 and r["dev_ma200"] > -15
                and _ma50_reached_ma200(r["ma50"], r["ma200"]) and r["trend_30"] > FB1_TREND30_MIN)
        _fb2 = (r["trend_30"] > 5 and r["slope_60"] > 5)
        _main_weak = (r["above_ma200"] and (r["ma_gap_pct_above"] or 0) > 0.5 and r["is_uptrend"])
        _main_extra = ((r["dev_ma20"] or 0) > 5 and (r["pos_pct"] or 0) > 60 and r["is_uptrend"])
        if _fb2 and not _fb1:
            d = "fallback2"
        elif _fb1:
            d = "fallback1"
        elif _main_weak:
            d = "主路径弱信号"
        elif _main_extra:
            d = "主路径加分线"
        else:
            d = "弱信号/起点 fallback (未分类)"
        disp_counter[d] += 1
    for d, c in disp_counter.most_common():
        print(f"  {d:32s}: {c:3d}")

    # ── A→M ──
    am = {"000333", "002683", "300488", "603199", "605338", "688128"}
    print("\n" + "=" * 112)
    print("A→M 命中 6 只: 插桩捕获的实际分支")
    print("=" * 112)
    by = {r["code"]: r for r in rows}
    for c in sorted(am):
        r = by.get(c)
        if r:
            print(f"  {c} {r['name'][:10]:10s} 实际分支={r['chan']:12s} "
                  f"stage={r['stage']:12s} scores={r['scores']}")


if __name__ == "__main__":
    main()
