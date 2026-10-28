#!/usr/bin/env python
"""导出 A→M 命中名单的完整财务数据 (报告「财务数据」section 同源)

2026-10-28: 第一版自己重算 ROC/EY, 结果跟 /t-analyze 详报漂移 (603387 ROC 15.7% vs 16.4%)。
根因: 4 季大表的 ROC/EY 是「每季单季快照」语义 (compute_magic_one_day([r], ...)),
      不是最新季 TTM; 自己重算时口径搞错。

本脚本铁律: 走 AnalysisEngine(strategies=[FinanceStrategy]) 生产链路,
           不自己重算任何 ROC/EY/PE, 保证跟 /t-analyze 100% 一致。

用法:
    bash tools/with_venv.sh python -m tools.batch.export_a2m_financials
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.analysis.analysis_engine import AnalysisEngine, FinanceStrategy  # noqa: E402
from tools.storage.store import DataStore  # noqa: E402

OUT_MD = Path("docs/a2m-22-full-financials.md")
OUT_CSV = Path("docs/a2m-22-full-financials.csv")

# 22 只 A→M 命中 (2026-10-28 /t-wyckoff-a2m 扫描结果), 按转变日分组
HITS = [
    ("300519", "新光药业", "中成药", "T-27"),
    ("300832", "新产业", "医疗保健", "T-27"),
    ("301101", "明月镜片", "文教休闲", "T-27"),
    ("000538", "云南白药", "中成药", "T-28"),
    ("000810", "创维数字", "家用电器", "T-28"),
    ("002380", "科远智慧", "软件服务", "T-28"),
    ("600323", "瀚蓝环境", "环境保护", "T-28"),
    ("601766", "中国中车", "运输设备", "T-28"),
    ("601808", "中海油服", "石油开采", "T-28"),
    ("603337", "杰克科技", "纺织机械", "T-28"),
    ("603387", "基蛋生物", "医疗保健", "T-28"),
    ("603901", "永创智能", "专用机械", "T-28"),
    ("603967", "中创物流", "仓储物流", "T-28"),
    ("688410", "山外山", "医疗保健", "T-28"),
    ("688469", "芯联集成", "半导体", "T-28"),
    ("001219", "青岛食品", "食品", "T-29"),
    ("002421", "达实智能", "软件服务", "T-29"),
    ("300406", "九强生物", "医疗保健", "T-29"),
    ("300428", "立中集团", "汽车配件", "T-29"),
    ("301048", "金鹰重工", "运输设备", "T-29"),
    ("301109", "军信股份", "环境保护", "T-29"),
    ("603683", "晶华新材", "化工原料", "T-29"),
]


def collect() -> list[dict]:
    """跑生产链路 (FinanceStrategy) 拿 22 只的 4 季财务 + Magic 估值。"""
    engine = AnalysisEngine(strategies=[FinanceStrategy])
    out = []
    for code, name, industry, turn in HITS:
        ctx = DataStore.get_ctx(code)
        result = engine.analyze(ctx)
        fin = result.raw.get("finance") or {}
        quarterly = fin.get("quarterly") or []
        out.append({
            "code": code,
            "name": name,
            "industry": industry,
            "turn": turn,
            "price": ctx.current_price,
            "mc_yi": ctx.market_cap_yi,
            "period_label": fin.get("period_label") or "",
            "quarterly": quarterly,
            "peg": fin.get("PEG_真实"),
            "fwd_pe": fin.get("fwd_pe"),
            "g": fin.get("g"),
            "peg_verdict": fin.get("verdict"),
        })
        # 守护: quarterly 必须是降序 (最新季在 [0])，否则汇总表会取到最旧季
        qs = [r.get("quarter", "") for r in quarterly]
        assert qs == sorted(qs, reverse=True), f"{code} quarterly 非降序: {qs}"
        print(f"  {code} {name}: {len(quarterly)} 季, 最新={qs[0] if qs else '-'}, "
              f"ROC={quarterly[0].get('roc') if quarterly else None}", flush=True)
    return out


def fmt(v, digits=1, suffix="%"):
    """None → —, 否则格式化。"""
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    return f"{v:,.{digits}f}{suffix}"


def fmt0(v):
    """对齐 report_renderer._fmt: 整数千分位, 0/None → —。"""
    return f"{v:,.0f}" if v else "—"


def fmtp(v, digits=1):
    """对齐 report_renderer: 营收/净利/EBIT 用 :.1f / :.2f, 不加千分位。"""
    if v is None:
        return "—"
    return f"{v:.{digits}f}"


def fmt_signed(v, digits=1, suffix="%"):
    """对齐 report_renderer: yoy 类带 +/- 号, 不加千分位 (renderer 用 :+.1f)。"""
    if v is None:
        return "—"
    return f"{v:+.{digits}f}{suffix}"


def build_md(data: list[dict]) -> str:
    L: list[str] = []
    L.append("# A→M 命中 22 只 — 完整财务数据")
    L.append("")
    L.append("> 格式跟 /t-analyze 详报的「财务数据」section 一致 (最近 4 季 + Magic 估值)")
    L.append("> 数据源: Tushare fina_indicator (2026Q2 半年报) + daily_basic (20261027)")
    L.append("> ROC/EY/PE 全部走 AnalysisEngine → FinanceStrategy 生产链路, 与 /t-analyze 详报同源")
    L.append("")
    L.append("---")
    L.append("")
    L.append("# 跨票汇总 — 最新季 (2026Q2) 全字段 + PE")
    L.append("")
    L.append("> 22 只按净利 yoy 排序. 完整 4 季明细见下方各票小节; 全字段 CSV 见 `docs/a2m-22-full-financials.csv`")
    L.append("")

    # ---- 汇总表 (18 列) ----
    cols = ["代码", "名称", "行业", "转变", "营收 yoy", "净利 yoy", "毛利率", "ROE",
            "营收(亿)", "净利(亿)", "EBIT(亿)", "市值(亿)", "净负债(亿)", "EV(亿)",
            "投入资本(亿)", "ROC", "EY", "静态PE"]
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "---|" * len(cols))

    rows = []
    for d in data:
        # FinanceStrategy 的 quarterly 是「降序」(reversed(financials[-4:])) → [0] 才是最新季。
        # 早前误用 [-1] (最旧季 20250630) 导致汇总表整列错位。
        q = d["quarterly"][0] if d["quarterly"] else {}
        rows.append((q.get("np_yoy") if q.get("np_yoy") is not None else -999, d, q))
    rows.sort(key=lambda r: r[0], reverse=True)

    for _, d, q in rows:
        L.append("| {code} | {name} | {ind} | {turn} | {or_yoy} | {np_yoy} | {gm} | {roe} | "
                 "{rev} | {np} | {ebit} | {mc} | {nd} | {ev} | {cap} | {roc} | {ey} | {pe} |".format(
                     code=d["code"], name=d["name"], ind=d["industry"], turn=d["turn"],
                     or_yoy=fmt_signed(q.get("or_yoy")), np_yoy=fmt_signed(q.get("np_yoy")),
                     gm=fmt(q.get("gm"), 1, "%"), roe=fmt(q.get("roe"), 1, "%"),
                     rev=fmtp(q.get("revenue_yi"), 1), np=fmtp(q.get("netprofit_yi"), 2),
                     ebit=fmtp(q.get("ebit_yi"), 2), mc=fmt0(q.get("mc_yi")),
                     nd=fmt0(q.get("netdebt_yi")), ev=fmt0(q.get("ev_yi")),
                     cap=fmt0(q.get("capital_yi")),
                     roc=fmt(q.get("roc"), 1, "%"), ey=fmt(q.get("ey"), 2, "%"),
                     pe=fmt(q.get("static_pe"), 1, "")))
    L.append("")

    # ---- 各票小节 ----
    for d in data:
        L.append(f"## {d['code']} {d['name']} — {d['industry']} ({d['turn']})")
        L.append("")
        L.append(f"**现价:** ¥{d['price']:.2f} | **市值:** {d['mc_yi']:.1f} 亿")
        L.append("")
        pl = d["period_label"] or "TTM"
        L.append(f"### 最近 4 季财务 + Magic 公式 ({pl})")
        L.append("")
        L.append("**4 季财务 + Magic 估值 (营收/净利 yoy=单季同比; 营收/净利 (亿)=YTD 累计; Magic=TTM 单期; "
                 "PE=季末市值/YTD净利年化 (Q1×4 / Q2×2 / Q3×4/3 / Q4×1); 动态PE=forward PE, 1致预期净利):**")
        L.append("")
        cols1 = ["季", "营收 yoy", "净利 yoy", "毛利率", "ROE", "营收 (亿)", "净利 (亿)",
                 "EBIT (亿)", "ROC 资本回报率", "EY 收益率", "总市值 (亿)", "净负债 (亿)",
                 "EV (亿)", "投入资本 (亿)", "PE", "动态 PE"]
        L.append("| " + " | ".join(cols1) + " |")
        L.append("|" + "---|" * len(cols1))
        for q in d["quarterly"]:
            L.append("| {quarter} | {or_yoy} | {np_yoy} | {gm} | {roe} | {rev} | {np} | {ebit} | "
                     "{roc} | {ey} | {mc} | {nd} | {ev} | {cap} | {pe} | {fpe} |".format(
                         quarter=q.get("quarter", ""),
                         or_yoy=fmt_signed(q.get("or_yoy")), np_yoy=fmt_signed(q.get("np_yoy")),
                         gm=fmt(q.get("gm"), 1, "%"), roe=fmt(q.get("roe"), 1, "%"),
                         rev=fmtp(q.get("revenue_yi"), 1), np=fmtp(q.get("netprofit_yi"), 2),
                         ebit=fmtp(q.get("ebit_yi"), 2),
                         roc=fmt(q.get("roc"), 1, "%"), ey=fmt(q.get("ey"), 2, "%"),
                         mc=fmt0(q.get("mc_yi")), nd=fmt0(q.get("netdebt_yi")),
                         ev=fmt0(q.get("ev_yi")), cap=fmt0(q.get("capital_yi")),
                         pe=fmt(q.get("static_pe"), 1, ""), fpe=fmt(q.get("fwd_pe"), 1, "")))
        L.append("")
        L.append(f"**行业:** {d['industry']}")
        L.append("")
        L.append("**判定:** ROC 越高 = 资本效率越好, EY 越高 = EV 回报越好, 联合排名 (Greenblatt 原版)")
        L.append("")
        L.append("---")
        L.append("")

    return "\n".join(L)


def build_csv(data: list[dict]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["code", "name", "industry", "turn", "quarter", "or_yoy", "np_yoy", "gm", "roe",
              "ebit_yi", "revenue_yi", "netprofit_yi", "mc_yi", "netdebt_yi", "ev_yi",
              "capital_yi", "roc", "ey", "static_pe", "fwd_pe", "peg", "g", "peg_verdict",
              "L_r8", "L_r10", "L_r12", "L_E3_r10"]
    w.writerow(header)
    for d in data:
        for q in d["quarterly"]:
            w.writerow([
                d["code"], d["name"], d["industry"], d["turn"], q.get("quarter", ""),
                q.get("or_yoy"), q.get("np_yoy"), q.get("gm"), q.get("roe"),
                q.get("ebit_yi"), q.get("revenue_yi"), q.get("netprofit_yi"),
                q.get("mc_yi"), q.get("netdebt_yi"), q.get("ev_yi"), q.get("capital_yi"),
                q.get("roc"), q.get("ey"), q.get("static_pe"), q.get("fwd_pe"),
                q.get("peg"), q.get("g"), q.get("peg_verdict"),
                q.get("L_r8"), q.get("L_r10"), q.get("L_r12"), q.get("L_E3_r10"),
            ])
    return buf.getvalue()


def main():
    print("🔄 跑生产链路 (AnalysisEngine + FinanceStrategy)...")
    data = collect()
    print(f"✅ {len(data)} 只")

    md = build_md(data)
    OUT_MD.write_text(md, encoding="utf-8")
    print(f"📄 {OUT_MD} ({len(md.splitlines())} 行)")

    csv_txt = build_csv(data)
    OUT_CSV.write_text(csv_txt, encoding="utf-8")
    print(f"📄 {OUT_CSV} ({len(csv_txt.splitlines())} 行)")


if __name__ == "__main__":
    main()
