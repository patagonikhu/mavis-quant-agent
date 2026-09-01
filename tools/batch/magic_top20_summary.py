"""
magic_top20_summary.py — 给 Magic Top 20 补 PEG / DCF / Magic 排名 3 项 (卡点 N/A)

输入:  docs/magic-top20.md (Top 20 表)
输出:  docs/magic-top20-summary.md (4 项摘要: 卡点⭐/PEG/DCF/Magic 排名)
       卡点⭐ = N/A (LLM 判断, 代码跑不出)

每只票从本地 parquet 拿:
  - EPS 机构一致预期 → _build_eps_table 直拉 (绕开 watchlist gate) + 写 data/cache/eps/{code}.json
  - 总市值 (万元)    → DataStore.get_daily_basic → 内部 / 1e4 转亿
  - 当前价           → DataStore.get_kline 末根 close
  - Magic 排名       → 直接读 docs/magic-top20.md Top 20 表

第一次跑: 20 只 × 1 EPS API (datacenter.eastmoney.com) ≈ 20s (Tushare VIP 没法 EPS, 走 datacenter)
第二次跑: 全本地 cache, 0 网络, 1-2s 完事
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

# 路径
_TOOLS = Path(__file__).resolve().parent.parent
_PROJECT = _TOOLS.parent
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from tools.kline_store import DataStore  # noqa: E402
from tools.analysis.report_section_evaluators import (  # noqa: E402
    compute_peg,
    compute_dcf_l,
)
from tools.eps_consensus_cache import EPS_DIR  # noqa: E402


def get_eps_for_summary(code: str, use_cache: bool = True) -> list[dict]:
    """绕开 watchlist gate 拿 EPS — Top 20 不一定在 watchlist

    优先读 data/cache/eps/{code}.json (本地), 缺则调 _build_eps_table 拉 + 写
    """
    import json
    import time
    path = EPS_DIR / f"{code}.json"

    if use_cache and path.exists() and path.stat().st_size > 10:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass

    try:
        from tools.fetch.data_fetcher import _build_eps_table
        table, source = _build_eps_table(code)
        if table:
            path.write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")
            return table
    except Exception as e:
        print(f"    ⚠️ EPS {code} 拉取失败: {e}")
    return []


# ============================================================
# 解析 magic-top20.md 拿 20 只票 + Magic 自身数据
# ============================================================

def parse_magic_top20(md_path: Path) -> list[dict]:
    """从 magic-top20.md 解析 Top 20 表

    返回 list[dict]: code, name, industry, roc, ey, roc_rank, ey_rank, combined_rank, mc_yi, ev_yi
    """
    text = md_path.read_text(encoding="utf-8")
    # 表头: | # | 代码 | 名称 | 行业 | ROC (%) | EY (%) | ROC 排名 | EY 排名 | 综合 | 市值 (亿) | EV (亿) |
    row_re = re.compile(
        r"\|\s*(\d+)\s*\|\s*(\d{6})\s*\|\s*([^\s|]+)\s*\|\s*([^\s|]+)\s*\|"
        r"\s*([\d.]+)\s*\|\s*([\d.]+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|"
        r"\s*([\d.]+)\s*\|\s*([\d,]+)\s*\|\s*([\d,]+)\s*\|"
    )
    out = []
    for m in row_re.finditer(text):
        out.append({
            "rank": int(m.group(1)),
            "code": m.group(2),
            "name": m.group(3).strip(),
            "industry": m.group(4).strip(),
            "roc": float(m.group(5)),
            "ey": float(m.group(6)),
            "roc_rank": int(m.group(7)),
            "ey_rank": int(m.group(8)),
            "combined_rank": float(m.group(9)),
            "mc_yi": float(m.group(10).replace(",", "")),
            "ev_yi": float(m.group(11).replace(",", "")),
        })
    return out


# ============================================================
# 单只 4 项摘要
# ============================================================

def summarize_one(code: str, name: str, industry: str) -> dict:
    """单只票: 4 项摘要 (卡点⭐/PEG/DCF/Magic 排名)

    返回 dict 含:
      - code, name, industry
      - price (当前价, 元)
      - peg: {'peg': float, 'verdict': str, 'g_pct': float, 'fwd_pe': float} 或 {'error': str}
      - dcf: {'L_yi': float, 'r10': float, 'L_e3': float, 'l_reach': float} 或 {'error': str}
      - card: 'N/A'  (代码跑不了, LLM 补)
    """
    out = {"code": code, "name": name, "industry": industry, "card": "N/A"}

    # 当前价 + 总市值
    kline = DataStore.get_kline(code, limit=5)
    if kline:
        out["price"] = float(kline[-1]["close"])
    else:
        out["price"] = None

    db = DataStore.get_daily_basic(code)
    market_cap_yi = (db.get("total_mv") / 1e4) if db and db.get("total_mv") else None

    # EPS (绕开 watchlist gate)
    eps_table = get_eps_for_summary(code)

    # PEG
    if out["price"] and eps_table:
        out["peg"] = compute_peg(eps_table, out["price"])
    else:
        out["peg"] = {"error": "EPS 或价格缺"}

    # DCF
    if market_cap_yi and eps_table:
        out["dcf"] = compute_dcf_l(eps_table, market_cap_yi)
    else:
        out["dcf"] = {"error": "市值或 EPS 缺"}

    return out


# ============================================================
# 渲染 markdown
# ============================================================

def _fmt_peg(peg: dict) -> str:
    if "error" in peg:
        return f"❌ {peg['error'][:20]}"
    p = peg["peg"]
    if p < 1.0:
        icon = "🟢"
    elif p < 1.5:
        icon = "🟡"
    elif p < 2.0:
        icon = "🟠"
    else:
        icon = "🔴"
    return f"{icon} {p:.2f} (g={peg['g']:.0f}%)"


def _fmt_dcf(dcf: dict) -> str:
    if "error" in dcf:
        return f"❌ {dcf['error'][:20]}"
    # DCF 实际返回: L_r10, L_E3_r10, L_achievable (str "L=123/可达=200=0.62x"), verdict
    L_r10 = dcf.get("L_r10")
    L_E3 = dcf.get("L_E3_r10")
    l_reach = dcf.get("L_achievable", "")
    parts = []
    if L_r10 is not None:
        parts.append(f"L={L_r10:.0f}亿")
    if L_E3 is not None:
        parts.append(f"L/E3={L_E3:.1f}x")
    # L_achievable 已经是 "L=xx/可达=yy=z.zx" 格式
    if l_reach:
        parts.append(l_reach)
    return " ".join(parts) if parts else "—"


def render_summary_md(items: list[dict], magic_data: dict[str, dict]) -> str:
    """渲染 docs/magic-top20-summary.md

    items: 4 项摘要列表
    magic_data: code → magic 数据 dict
    """
    today = datetime.now().strftime("%Y-%m-%d")
    lines = []
    lines.append(f"# Magic Top 20 摘要 — {today}")
    lines.append("")
    lines.append("> **范围:** Magic Top 20 票")
    lines.append("> **数据源:** 本地 parquet (0 网络)")
    lines.append("> **字段:** 卡点⭐ = N/A (需 LLM 判断), PEG / DCF / Magic 排名 (代码自动算)")
    lines.append("> **公式:** PEG = Forward PE / 稳态 g%; DCF = 隐含长期净利润 (r=10%); Magic = ROC 排名 + EY 排名 平均")
    lines.append("")
    lines.append("## 📊 摘要表 (按 Magic 排名)")
    lines.append("")
    lines.append("| # | 代码 | 名称 | 行业 | 卡点⭐ | PEG | DCF (r=10%) | Magic 排名 | 当前价 | 总市值 (亿) |")
    lines.append("|---|------|------|------|--------|-----|-------------|------------|--------|-------------|")

    for it in items:
        code = it["code"]
        magic = magic_data[code]
        magic_str = f"#{magic['rank']} (综合 {magic['combined_rank']})"
        lines.append(
            f"| {magic['rank']} | {code} | {it['name']} | {it['industry']} | "
            f"{it['card']} | {_fmt_peg(it['peg'])} | {_fmt_dcf(it['dcf'])} | "
            f"{magic_str} | {(it['price'] or 0):.2f} | {magic['mc_yi']:,.0f} |"
        )

    # 统计
    n_peg_ok = sum(1 for it in items if "error" not in it["peg"])
    n_dcf_ok = sum(1 for it in items if "error" not in it["dcf"])
    n_peg_cheap = sum(1 for it in items if "error" not in it["peg"] and it["peg"]["peg"] < 1.5)

    lines.append("")
    lines.append("## 📈 统计")
    lines.append("")
    lines.append(f"- PEG 成功: **{n_peg_ok}/20** (PEG<1.5 健康: **{n_peg_cheap}**)")
    lines.append(f"- DCF 成功: **{n_dcf_ok}/20**")
    lines.append(f"- 卡点⭐ 待补: **20/20** (LLM 单独判断每只产业链定位)")
    lines.append("")
    lines.append("## 💡 怎么用")
    lines.append("")
    lines.append("1. **Magic 排名靠前 + PEG<1.5** = 双优, 重点关注 (便宜的好公司)")
    lines.append("2. **Magic 排名靠前 + PEG>2** = 好公司但贵, 等 PEG 修复或减仓")
    lines.append("3. **PEG<1.5 + Magic 排名靠后** = 便宜但资本效率差, 谨慎")
    lines.append("4. **DCF L/E3 > 5** = 叙事透支, 警惕")
    lines.append("5. **卡点⭐** = 产业链不可替代环节 (1-5 星), 我可以单独给每只补, 你点哪只我展开")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"📅 **生成:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  |  "
                 f"🔧 **脚本:** `tools/batch/magic_top20_summary.py`")
    lines.append("")
    return "\n".join(lines)


# ============================================================
# Main
# ============================================================

def main() -> int:
    md_in = _PROJECT / "docs" / "magic-top20.md"
    if not md_in.exists():
        print(f"❌ {md_in} 不存在, 先跑 magic_top20")
        return 1

    print(f"📂 解析 {md_in.name}...")
    magic_data_list = parse_magic_top20(md_in)
    if not magic_data_list:
        print(f"❌ {md_in} 里没解析到 Top 20 表 (正则不匹配?)")
        return 1

    print(f"   找到 {len(magic_data_list)} 只")

    # code → magic 字典
    magic_data = {m["code"]: m for m in magic_data_list}

    # 4 项摘要
    print(f"🔄 跑 4 项摘要 (PEG/DCF/卡点⭐/Magic 排名)...")
    import time
    t0 = datetime.now()
    items = []
    for i, m in enumerate(magic_data_list):
        it = summarize_one(m["code"], m["name"], m["industry"])
        items.append(it)
        peg_s = _fmt_peg(it["peg"])
        dcf_s = _fmt_dcf(it["dcf"])
        print(f"   {m['code']} {m['name']:<8s}  PEG={peg_s:<22s}  DCF={dcf_s}")
        # 限流: 19 只没 cache 都要拉, sleep 避免 datacenter WAF
        if i < len(magic_data_list) - 1:
            time.sleep(0.4)
    elapsed = (datetime.now() - t0).total_seconds()
    print(f"   耗时 {elapsed:.1f}s")

    # 写文件
    out_path = _PROJECT / "docs" / "magic-top20-summary.md"
    md = render_summary_md(items, magic_data)
    out_path.write_text(md, encoding="utf-8")
    print(f"✅ 写: {out_path}  ({len(md)} 字节)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
