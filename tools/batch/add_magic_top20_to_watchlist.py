"""
add_magic_top20_to_watchlist.py — 把 Magic Top 20 加到 data/watchlist.json

读取:
  - docs/magic-top20.md       (Top 20 表, 拿 20 只代码 + ROC/EY/Magic 排名)
  - docs/magic-top20-summary.md (PEG / DCF 摘要)
  - data/watchlist.json       (现有 watchlist, 去重)

写入:
  - data/watchlist.json, 20 只新条目, list_type="Magic初筛", notes 简洁摘要
  - changelog 加一条 2026-09-01
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT))


def parse_top20(md_path: Path) -> list[dict]:
    text = md_path.read_text(encoding="utf-8")
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
        })
    return out


def parse_summary(summary_md: Path) -> dict[str, dict]:
    """code → {peg, dcf_l, dcf_e3, dcf_reach}"""
    text = summary_md.read_text(encoding="utf-8")
    row_re = re.compile(
        r"\|\s*(\d+)\s*\|\s*(\d{6})\s*\|"
    )
    # 用行级解析 (PEG/DCF 字段含 emoji, 跨行不稳)
    out = {}
    lines = text.splitlines()
    for line in lines:
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        # cells[0] 是空, cells[1]=#, cells[2]=code, cells[3]=name, cells[4]=industry,
        # cells[5]=卡点, cells[6]=PEG, cells[7]=DCF, cells[8]=Magic
        if len(cells) < 9 or not cells[2].isdigit() or len(cells[2]) != 6:
            continue
        code = cells[2]
        peg = cells[6]
        dcf = cells[7]
        # 提取 PEG 数字 + 色标
        m_peg = re.search(r"([🟢🟡🟠🔴])?\s*([\d.]+)\s*\(g=(-?[\d.]+)%\)", peg)
        m_dcf = re.search(r"L=([\d.]+)亿\s+L/E3=([\d.]+)x\s+([\d/]+=[\d.]+x)", dcf)
        out[code] = {
            "peg_raw": peg,
            "dcf_raw": dcf,
            "peg": m_peg.group(2) if m_peg else None,
            "peg_g": m_peg.group(3) if m_peg else None,
            "peg_color": m_peg.group(1) if m_peg and m_peg.group(1) else None,
            "dcf_L": m_dcf.group(1) if m_dcf else None,
            "dcf_e3": m_dcf.group(2) if m_dcf else None,
            "dcf_reach": m_dcf.group(3) if m_dcf else None,
            "dcf_err": dcf.startswith("❌"),
            "peg_err": peg.startswith("❌"),
        }
    return out


def fmt_peg(s: dict) -> str:
    if s["peg_err"]:
        return f"PEG缺 (无机构预期)"
    color = s["peg_color"] or "⬜"
    return f"PEG {s['peg']} ({color}, g={s['peg_g']}%)"


def fmt_dcf(s: dict) -> str:
    if s["dcf_err"]:
        return "DCF缺"
    return f"DCF L={s['dcf_L']}亿 L/E3={s['dcf_e3']}x L/可达={s['dcf_reach']}"


def main() -> int:
    docs = _PROJECT / "docs"
    top20_path = docs / "magic-top20.md"
    summary_path = docs / "magic-top20-summary.md"
    watchlist_path = _PROJECT / "data" / "watchlist.json"

    if not top20_path.exists():
        print(f"❌ {top20_path} 不存在, 先跑 magic_top20")
        return 1
    if not summary_path.exists():
        print(f"❌ {summary_path} 不存在, 先跑 magic_top20_summary")
        return 1

    print(f"📂 读 Top 20: {top20_path.name}")
    top20 = parse_top20(top20_path)
    print(f"   {len(top20)} 只")

    print(f"📂 读 4 项摘要: {summary_path.name}")
    summary = parse_summary(summary_path)
    print(f"   {len(summary)} 只")

    print(f"📂 读 watchlist: {watchlist_path.name}")
    watchlist = json.loads(watchlist_path.read_text(encoding="utf-8"))
    existing_codes = {s["code"] for s in watchlist["stocks"]}
    print(f"   现有 {len(existing_codes)} 只")

    # 加 20 只
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    skipped = 0
    for t in top20:
        code = t["code"]
        if code in existing_codes:
            print(f"   ⏭️  {code} {t['name']} 已在 watchlist, 跳过")
            skipped += 1
            continue

        s = summary.get(code, {})
        peg_str = fmt_peg(s) if s else "PEG/DCF 缺"
        dcf_str = fmt_dcf(s) if s else ""

        notes = (
            f"[{today} Magic Top20 #{t['rank']}] "
            f"ROC={t['roc']:.1f}% (rank {t['roc_rank']}) "
            f"EY={t['ey']:.2f}% (rank {t['ey_rank']}) "
            f"综合={t['combined_rank']} / "
            f"{peg_str} / {dcf_str} | 卡点⭐ 待 LLM 补"
        )

        new_stock = {
            "code": code,
            "name": t["name"],
            "sector": t["industry"],  # 行业
            "list_type": "Magic初筛",
            "notes": notes,
        }
        watchlist["stocks"].append(new_stock)
        existing_codes.add(code)
        added += 1
        print(f"   ✅ {code} {t['name']:<8s}  Magic #{t['rank']:>2d} 综合 {t['combined_rank']:>4.1f}  ({t['industry']})")

    # 更新元数据
    watchlist["last_updated"] = today

    # changelog 加 1 条
    if "changelog" not in watchlist:
        watchlist["changelog"] = []
    watchlist["changelog"].append({
        "date": today,
        "change": f"加 {added} 只 Magic Top 20 (list_type=Magic初筛, 跳过 {skipped} 只已存在)",
        "source": "docs/magic-top20.md (Greenblatt 联合排名 H1 2026)",
    })

    # 写
    watchlist_path.write_text(
        json.dumps(watchlist, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n✅ 写: {watchlist_path}")
    print(f"   加 {added} 只, 跳过 {skipped} 只, 现总 {len(watchlist['stocks'])} 只")

    return 0


if __name__ == "__main__":
    sys.exit(main())
