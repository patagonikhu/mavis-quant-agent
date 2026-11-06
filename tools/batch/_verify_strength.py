"""判定强度 (strength) 一致性校验 (2026-11-05)

目的: 验证生产侧输出的 `strength` 档位**没有撒谎** —— 它必须忠实反映
  这一次 Markup/Accumulation/Distribution 判定**实际走了哪一层门**。

⚠️ 方法学 (踩过的坑):
  不能「读输出反推通道」—— fallback 分支会写回 scores, 反推必错。
  唯一可信手段 = **AST 插桩捕获实际执行的分支** (同 _verify_fb_channel_ast)。

插桩点 (stage_factor.py::wyckoff_judge):
  评分段 (顶层, 3 条门):
    strict 门  →  ('gate', 'strict')
    weak 门    →  ('gate', 'weak')
    extra 门   →  ('gate', 'extra')
  channel 段 (嵌套在 main 路径内, 3 条门):
    →  ('chan', 'main_strict' / 'main_weak' / 'main_extra')
  兜底段:
    fb1 / fb2_base / fb2_markup / fb2_accum

硬断言 (任何一条不过 → SystemExit 失败):
  A. 插桩必须生效 (否则不出结论)
  B. channel 的 main_* token 集合 == 实际命中的门集合 (生产两处条件重复写, 防漂移)
  C. strength == 由**插桩捕获的门**推出的档位 (独立于 channel 字符串)
  D. strength == stage_strength(stage, channel) (helper 与生产一致)
  E. stage-aware 校验: Accumulation / Distribution 各自的 channel → 档位映射
  F. stage == '?' ⇒ strength == 'none' 且 strength_label() 返回空串 (渲染器不渲染)
  G. stage != '?' ⇒ strength != 'none'

跑法: bash tools/with_venv.sh python -m tools.batch._verify_strength
"""
import ast
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.factors.wyckoff.helpers import stage_strength, strength_label

SRC = ROOT / "tools" / "factors" / "wyckoff" / "stage_factor.py"

# 3 层门的条件文本 (生产里写了两遍: 评分段 + channel 段)
T_STRICT = ("golden_cross_recent and above_ma200_sustained "
            "and ma_gap_pct_above > 0.5 and is_uptrend")
T_WEAK = "above_ma200 and ma_gap_pct_above > 0.5 and is_uptrend"
T_EXTRA = "dev_ma20 > 5 and pos_pct > 60 and is_uptrend"
T_MAIN = "max(scores.values()) > 0"
T_FB1 = ("slope_60 > 0 and dev_ma200 > -15 and _ma50_reached_ma200(ma50, ma200) "
         "and trend_30 > FB1_TREND30_MIN")
T_FB2_BASE = "fb2_base"
T_FB2_MARKUP = "slope_60 > 5 and trend_30 > 5"
T_FB2_ACCUM = "slope_60 < -5 and trend_30 < -5"

GATE2TOKEN = {"strict": "main_strict", "weak": "main_weak", "extra": "main_extra"}
TOKEN2GATE = {v: k for k, v in GATE2TOKEN.items()}


def _unparse(node):
    return ast.unparse(node.test)


def _norm(s):
    """归一化条件文本: 去掉空白与括号。

    ⚠️ ast.unparse 会给比较表达式加括号 (a > 0.5 → (a > 0.5)),
    硬编码原文必然对不上 → 统一去括号再比。
    """
    return re.sub(r"\s+", "", s).replace("(", "").replace(")", "")


def _find_ifs(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.If)]


def _subtree_ids(node):
    return {id(n) for n in ast.walk(node)}


def _body_ids(if_node):
    """只取 if_node **body** 内(含嵌套) 的节点 id。

    ⚠️ ast.walk 会连 orelse 一起走 —— 而 `if main: ... elif fb1: ...` 在 AST 里
    fb1 的 If 节点正是 main 的 orelse 子节点。若用 ast.walk, fb1 会被误判成
    「在 main 路径内」→ 区分评分段/channel 段就失效了。
    """
    out = set()
    for stmt in if_node.body:
        out |= _subtree_ids(stmt)
    return out


def _pick(fn, test, in_main_ids=None, want_in_main=None):
    """挑出满足条件文本且层级符合要求的 If 节点。

    ⚠️ wyckoff_judge 的所有门都在 `for i in ...` 循环体里, **不是** fn.body 直接成员,
    所以不能用「顶层/嵌套」区分「评分段」与「channel 段」。
    真正的区分: channel 段的门位于 main 路径 (`if max(scores.values()) > 0:`) 的子树内,
    评分段的门在它外面。
    """
    hits = [n for n in _find_ifs(fn) if _norm(_unparse(n)) == _norm(test)]
    if not hits:
        raise SystemExit(f"定位失败: 找不到条件 {test!r}")
    if want_in_main is None:
        sel = hits
    else:
        sel = [n for n in hits if ((id(n) in in_main_ids) == want_in_main)]
    if len(sel) != 1:
        raise SystemExit(
            f"定位失败: 条件 {test!r} in_main={want_in_main} 命中 {len(sel)} 个 (期望 1)")
    return sel[0]


def build_patched():
    """读源码 → AST 插桩 → exec 出一个逻辑等价但会记账的模块"""
    src = SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)

    fn = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "wyckoff_judge":
            fn = node
            break
    if fn is None:
        raise SystemExit("找不到 wyckoff_judge")

    # ── 定位插桩点 ──
    # 评分段: main 路径之外的 If; channel 段: main 路径子树内的 If
    n_main = _pick(fn, T_MAIN)
    main_ids = _body_ids(n_main)

    n_score = {
        "strict": _pick(fn, T_STRICT, main_ids, want_in_main=False),
        "weak": _pick(fn, T_WEAK, main_ids, want_in_main=False),
        "extra": _pick(fn, T_EXTRA, main_ids, want_in_main=False),
    }
    n_chan = {
        "main_strict": _pick(fn, T_STRICT, main_ids, want_in_main=True),
        "main_weak": _pick(fn, T_WEAK, main_ids, want_in_main=True),
        "main_extra": _pick(fn, T_EXTRA, main_ids, want_in_main=True),
    }
    n_fb1 = _pick(fn, T_FB1, main_ids, want_in_main=False)
    n_fb2_base = _pick(fn, T_FB2_BASE, main_ids, want_in_main=False)
    n_fb2_markup = _pick(fn, T_FB2_MARKUP, main_ids, want_in_main=False)
    n_fb2_accum = _pick(fn, T_FB2_ACCUM, main_ids, want_in_main=False)

    tags = [
        (n_score["strict"], "STRENGTH_LOG.append(('gate', 'strict'))"),
        (n_score["weak"], "STRENGTH_LOG.append(('gate', 'weak'))"),
        (n_score["extra"], "STRENGTH_LOG.append(('gate', 'extra'))"),
        (n_chan["main_strict"], "STRENGTH_LOG.append(('chan', 'main_strict'))"),
        (n_chan["main_weak"], "STRENGTH_LOG.append(('chan', 'main_weak'))"),
        (n_chan["main_extra"], "STRENGTH_LOG.append(('chan', 'main_extra'))"),
        (n_main, "STRENGTH_LOG.append(('path', 'main'))"),
        (n_fb1, "STRENGTH_LOG.append(('path', 'fb1'))"),
        (n_fb2_base, "STRENGTH_LOG.append(('path', 'fb2_base'))"),
        (n_fb2_markup, "STRENGTH_LOG.append(('path', 'fb2_markup'))"),
        (n_fb2_accum, "STRENGTH_LOG.append(('path', 'fb2_accum'))"),
    ]

    for node, tag in tags:
        node.body.insert(0, ast.parse(tag).body[0])

    # ── 硬断言: 插桩必须生效 ──
    n_tags = sum(1 for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "append")
    if n_tags < len(tags):
        raise SystemExit(f"❌ 插桩未完全生效 ({n_tags}/{len(tags)}), 终止")

    prelude = ast.parse("STRENGTH_LOG = []\n").body
    mod = ast.Module(body=prelude + tree.body, type_ignores=[])
    ast.fix_missing_locations(mod)
    g = {"__name__": "patched_stage_factor", "__file__": str(SRC)}
    exec(compile(mod, str(SRC), "exec"), g)
    return g


def _expected_strength(path, gates, stage):
    """由**插桩捕获**的实际分支推出应有档位 (完全不看 channel 字符串)"""
    if path == "main":
        if stage == "Markup":
            if "strict" in gates:
                return "strict"
            if "weak" in gates:
                return "weak"
            if "extra" in gates:
                return "extra_only"
            return "none"
        if stage == "Accumulation":
            # 累积门: 严格 AND 门 / 弱信号 A-B-C (channel 段单独记录, 见下)
            return None  # 由 channel 段 (累积语义) 判定, 走 E 分支
        if stage == "Distribution":
            return None
        return "none"
    if path in ("fb1", "fb2_markup"):
        return "fallback"
    if path == "fb2_accum":
        return "fallback"
    # fb2_base 进了但内部分支不满足 → '?' ; 什么都没捕到 → '?'
    return "none"


def main():
    from tools.batch.wyckoff_a2m_scan import (
        _load_all_klines, _filter_by_st, _filter_by_pe, _filter_by_quality,
        _filter_by_leverage, load_codes_all_market, load_stock_basic,
    )
    from tools.factors.kline_arrays import build_kline_features
    from tools.factors.wyckoff.detectors.sub_event_scanner import scan_sub_events

    g = build_patched()
    wyckoff_judge = g["wyckoff_judge"]
    log = g["STRENGTH_LOG"]

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

    errs = []
    strength_counter = Counter()
    stage_counter = Counter()
    chan_counter = Counter()
    path_counter = Counter()
    rows = []
    n = 0
    tagged = 0

    for code, df in sorted(all_kl.items()):
        if df is None or len(df) < 220:
            continue
        closes = df["close"].tolist()
        highs = df["high"].tolist()
        lows = df["low"].tolist()
        vols = df["vol"].tolist()
        opens = df["open"].tolist() if "open" in df.columns else closes
        pct_chgs = df["pct_chg"].tolist() if "pct_chg" in df.columns else None
        dates_ = df["date"].astype(str).str.replace("-", "").str[:8].tolist()
        nn = len(closes)
        arrs = build_kline_features(closes, highs, lows, vols, window=min(250, nn))
        evs = scan_sub_events(closes, highs, lows, vols, o=opens, pct_chg=pct_chgs,
                              period_label="daily", code=code, as_of_idx=None,
                              dates=dates_, precomputed_arrs=arrs)
        i = nn - 1
        sub_all = {e["name"] for e in evs if e["idx"] <= i}

        log.clear()
        out = wyckoff_judge(i, arrs, sub_all, window=arrs["window"], period_label="daily")
        if out is None:
            continue
        n += 1
        events = list(log)
        if events:
            tagged += 1

        gates = {v for k, v in events if k == "gate"}
        chan_tags = {v for k, v in events if k == "chan"}
        paths = [v for k, v in events if k == "path"]
        # ⚠️ 取**最后一个** path tag: fb2_base 进入后再进内部分支时, 事件序列是
        #   ['fb2_base', 'fb2_markup'] —— 取首个会把「真走 fb2_markup」误判成 fb2_base。
        path = paths[-1] if paths else "(else '?')"

        stage = out.get("stage")
        channel = out.get("channel", "")
        strength = out.get("strength")
        fv = out.get("factor_values") or {}

        strength_counter[strength] += 1
        stage_counter[stage] += 1
        chan_counter[channel] += 1
        path_counter[path] += 1

        parts = [p for p in (channel or "").split("+") if p]
        chan_main = {p for p in parts if p in TOKEN2GATE}

        # ── B. channel 的 main_* token == 实际命中的门 ──
        #    (生产里 3 条门的条件写了两遍: 评分段 + channel 段; 插桩同时抓两处, 防漂移)
        if stage == "Markup" and path == "main":
            expect_tokens = {GATE2TOKEN[gt] for gt in gates}
            if chan_main != expect_tokens:
                errs.append(f"[B] {code} {name_by_code.get(code)}: channel main token={sorted(chan_main)} "
                            f"!= 实际命中门={sorted(gates)} (channel={channel!r})")
            if chan_tags != expect_tokens:
                errs.append(f"[B] {code} {name_by_code.get(code)}: channel 段插桩={sorted(chan_tags)} "
                            f"!= 评分段插桩={sorted(gates)}")

        # ── C. strength == 由插桩门推出的档位 ──
        expect = _expected_strength(path, gates, stage)
        if expect is not None and strength != expect:
            errs.append(f"[C] {code} {name_by_code.get(code)}: strength={strength!r} "
                        f"!= 插桩推出={expect!r} (stage={stage!r} path={path} gates={sorted(gates)})")

        # ── D. strength == stage_strength(stage, channel) ──
        helper = stage_strength(stage, channel)
        if strength != helper:
            errs.append(f"[D] {code}: strength={strength!r} != stage_strength({stage!r},{channel!r})={helper!r}")

        # ── E. stage-aware: 非 Markup 的 channel → 档位 ──
        if stage == "Accumulation":
            if channel == "main_strict":
                want = "strict"
            elif channel.startswith("main_weak_"):
                want = "weak"
            elif channel in ("fallback1", "fallback2_accum"):
                want = "fallback"
            else:
                want = "none"
            if strength != want:
                errs.append(f"[E] {code} {name_by_code.get(code)}: Accumulation channel={channel!r} "
                            f"⇒ 应为 {want!r}, 实际 {strength!r}")
        elif stage == "Distribution":
            want = ("strict" if "main_strict" in parts else
                    "weak" if "main_weak" in parts else
                    "extra_only" if "main_extra" in parts else "none")
            if strength != want:
                errs.append(f"[E] {code} {name_by_code.get(code)}: Distribution channel={channel!r} "
                            f"⇒ 应为 {want!r}, 实际 {strength!r}")

        # ── F. '?' 阶段: strength 必须 none 且标签为空串 (渲染器不渲染) ──
        if stage == "?":
            if strength != "none":
                errs.append(f"[F] {code}: stage='?' 但 strength={strength!r} (应 none)")
            if strength_label(stage, strength) != "":
                errs.append(f"[F] {code}: stage='?' 但 strength_label 非空串 → 报告会渲染噪音")

        # ── G. 非 '?' 阶段: strength 不得为 none ──
        if stage != "?" and strength == "none":
            errs.append(f"[G] {code}: stage={stage!r} channel={channel!r} 但 strength=none")

        # ── 附加: 数值侧独立复算 (fv 有四舍五入, 边界容差单独归类) ──
        gap = fv.get("ma_gap_pct_above") or 0
        d20 = fv.get("dev_ma20") or 0
        pp = fv.get("pos_pct") or 0
        up = fv.get("is_uptrend")
        am200 = fv.get("above_ma200")
        gc = fv.get("golden_cross_recent")
        ams = fv.get("above_ma200_sustained")
        num_strict = bool(gc and ams and gap > 0.5 and up)
        num_weak = bool((not num_strict) and am200 and gap > 0.5 and up)
        num_extra = bool(d20 > 5 and pp > 60 and up)
        num_gates = set()
        if num_strict:
            num_gates.add("strict")
        if num_weak:
            num_gates.add("weak")
        if num_extra:
            num_gates.add("extra")
        if stage == "Markup" and path == "main" and num_gates != gates:
            # 四舍五入导致的边界差 (gap 2位 / dev_ma20 2位 / pos_pct 1位) 单独报告
            errs.append(f"[X] {code} {name_by_code.get(code)}: fv 数值复算={sorted(num_gates)} "
                        f"!= 插桩={sorted(gates)} (gc={gc} ams={ams} am200={am200} gap={gap} "
                        f"up={up} d20={d20} pp={pp})")

        rows.append({
            "code": code, "name": name_by_code.get(code, code), "stage": stage,
            "channel": channel, "strength": strength, "path": path,
            "gates": sorted(gates),
            "gap": gap, "dev_ma20": d20, "pos_pct": pp, "is_uptrend": up,
            "above_ma200": am200, "gc": gc, "ams": ams,
            "slope_60": fv.get("slope_60"), "dev_ma200": fv.get("dev_ma200"),
            "trend_30": fv.get("trend_30"),
        })

    # ── A. 插桩必须生效 ──
    if tagged == 0:
        raise SystemExit("❌ 插桩一条都没记录 —— 终止, 不出结论")
    if tagged < n * 0.9:
        print(f"⚠️ 只有 {tagged}/{n} 只捕获到分支标记 (其余走 else '?'), 属正常")

    print(f"\n判定样本 {n} 只 (捕获到分支标记 {tagged} 只)")
    print(f"\nstrength 分布: {dict(strength_counter)}")
    print(f"stage 分布:    {dict(stage_counter)}")
    print(f"path 分布:     {dict(path_counter)}")
    print(f"channel 分布:  {dict(chan_counter)}")

    print("\n--- Markup 按强度分档明细 (插桩口径) ---")
    for s in ("strict", "weak", "extra_only", "fallback"):
        sub = [r for r in rows if r["stage"] == "Markup" and r["strength"] == s]
        print(f"\n[{s}] {len(sub)} 只")
        for r in sub:
            print(f"  {r['code']} {str(r['name'])[:10]:10} channel={r['channel']:22} "
                  f"gates={','.join(r['gates']) or '-':20} "
                  f"gc={r['gc']} ams={r['ams']} am200={r['above_ma200']} "
                  f"gap={r['gap']:+.2f}% up={r['is_uptrend']} "
                  f"d20={r['dev_ma20']:+.2f}% pp={r['pos_pct']:.1f}%")

    print("\n--- 重点案例 ---")
    for tgt in ("600763", "301109"):
        hit = [r for r in rows if r["code"] == tgt]
        if not hit:
            print(f"  {tgt}: 不在候选池")
            continue
        r = hit[0]
        print(f"  {r['code']} {r['name']}: stage={r['stage']} channel={r['channel']} "
              f"strength={r['strength']} path={r['path']} gates={r['gates']}")
        print(f"      gc={r['gc']} ams={r['ams']} ma200={r['above_ma200']} gap={r['gap']}% "
              f"up={r['is_uptrend']} dev_ma20={r['dev_ma20']}% pos_pct={r['pos_pct']}%")
        print(f"      标签: {strength_label(r['stage'], r['strength'])}")

    print(f"\n=== 一致性错误 {len(errs)} 条 ===")
    for e in errs[:60]:
        print("  " + e)
    if errs:
        raise SystemExit(1)
    print("全部硬断言通过 ✅")


if __name__ == "__main__":
    main()
