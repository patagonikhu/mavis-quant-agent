#!/usr/bin/env python
"""校验: docs/a2m-22-full-financials.md 的各票 4 季表 vs docs/wyckoff-a2m/analyze-*.md

真源是 /t-analyze 详报 (走生产链路 RenderData.quarterly_finance + report_renderer)。
本脚本只做表格行逐行比对, 不重算任何数字。

用法:
    bash tools/with_venv.sh python -m tools.batch.verify_a2m_financials
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

MD = Path("docs/a2m-22-full-financials.md")
REPORT_DIR = Path("docs/wyckoff-a2m")

ROW_RE = re.compile(r"^\| (20\d{6}) \|")
FIN_HDR = "ROC 资本回报率"


def extract_fin_rows(text: str) -> list[list[str]]:
    """只抽「最近 4 季财务 + Magic 公式」表的数据行。

    详报里还有 24 列的因子历史表 (日期/收盘/RSI6/...)，行首也是 8 位日期，
    必须靠表头定位窗口，否则会误抓。
    """
    lines = text.split("\n")
    # 找表头 (含 ROC 资本回报率)
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|") and FIN_HDR in line:
            start = i
            break
    if start is None:
        return []
    out = []
    # 从表头往下，遇到空行或非表行就停
    for line in lines[start + 2:]:
        s = line.strip()
        if not s:
            break
        if not s.startswith("|"):
            break
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not ROW_RE.match(s):
            break
        out.append(cells)
    return out


def main() -> int:
    md_text = MD.read_text(encoding="utf-8")

    # 切分: "## <code> <name>" 开头到下一个 "## " 之前
    sections: dict[str, str] = {}
    cur = None
    buf: list[str] = []
    for line in md_text.split("\n"):
        m = re.match(r"^## (\d{6}) ", line)
        if m:
            if cur:
                sections[cur] = "\n".join(buf)
            cur = m.group(1)
            buf = []
        elif cur:
            buf.append(line)
    if cur:
        sections[cur] = "\n".join(buf)

    total_cmp = 0
    total_diff = 0
    bad_stocks = []

    for rp in sorted(REPORT_DIR.glob("analyze-*.md")):
        code = rp.name.split("-")[1]
        if code not in sections:
            print(f"⚠️  {code} 不在导出文件中")
            total_diff += 1
            continue
        rep_rows = extract_fin_rows(rp.read_text(encoding="utf-8"))
        exp_rows = extract_fin_rows(sections[code])
        if not rep_rows:
            continue
        if rep_rows == exp_rows:
            total_cmp += len(rep_rows)
            continue
        # 有差异 → 逐行报告
        print(f"\n🔴 {code} {rp.name}")
        print(f"   详报 {len(rep_rows)} 行 / 导出 {len(exp_rows)} 行")
        for i in range(max(len(rep_rows), len(exp_rows))):
            a = rep_rows[i] if i < len(rep_rows) else None
            b = exp_rows[i] if i < len(exp_rows) else None
            total_cmp += 1
            if a != b:
                total_diff += 1
                print(f"   行{i+1} 详报: {a}")
                print(f"   行{i+1} 导出: {b}")
        bad_stocks.append(code)

    print("\n" + "=" * 60)
    print(f"比对单元格行数: {total_cmp} | 不一致行数: {total_diff}")
    if bad_stocks:
        print(f"❌ 有差异的票: {', '.join(bad_stocks)}")
        return 1
    print("✅ 22 只全部与 /t-analyze 详报逐行一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
