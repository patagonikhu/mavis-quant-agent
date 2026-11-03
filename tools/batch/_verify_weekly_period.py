"""
_verify_weekly_period.py — 周线/日线威科夫判定口径校验 (2026-11-05)

背景 (修掉的那个既有 bug):
  报告「周线分析」段的威科夫判定, 原来错显**日线**数据 ——
    report_renderer.py:  wy_data = s5.get("wyckoff_weekly") or s5.get("wyckoff")
    factor_matrix.py:    wy_key = {'weekly': 'wyckoff_weekly', ...}
  但 analysis dict 里**从来没有** wyckoff_weekly 这个顶层 key
  (它只是 RawContext 上的字段, 没进 raw) → 永远 fallback 到日线结果。
  真源: raw["wyckoff"]["3period"][level]。

  实测 67 只样本: 31 只 stage 不一致、33 只 strength 不一致。

本脚本做**报告 → 生产值**双向对账:
  1. 从 docs/ 各子目录的报告 md 里, 分别抠出「日线分析」段和「周线分析」段
  2. 解析两段里的「当前阶段」和「判定强度」
  3. 用 DataStore + AnalysisEngine 重算同一只票的生产值
     (raw["wyckoff"] = 日线, raw["wyckoff"]["3period"]["weekly"] = 周线)
  4. 逐项比对

0 网络, 只读本地 parquet。

用法:
    bash tools/with_venv.sh python -m tools.batch._verify_weekly_period
    bash tools/with_venv.sh python -m tools.batch._verify_weekly_period --codes 600763 301109
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.storage.store import DataStore          # noqa: E402
from tools.analysis.analysis_engine import AnalysisEngine  # noqa: E402

# 报告里「当前阶段」行: | 当前阶段 | **Markup** (主升浪) ... |
RE_STAGE = re.compile(r"\| 当前阶段 \| \*\*(\w+)\*\*")
# 报告里「判定强度」: 判定强度: **弱 — 仅加分线成立 (...)**
RE_STRENGTH = re.compile(r"判定强度: \*\*(.+?)\*\*")

# 生产 strength token → 报告中文标签里的档位关键词
STRENGTH_KEY = {
    "strict": "强",
    "weak": "中",
    "extra_only": "弱",
    "fallback": "兜底",
    "none": "",     # '?' 阶段不显示强度 → 不比对
}

LINT_SUBS = ["watchlist", "portfolio", "runA", "runB", "blowout", "wyckoff-a2m"]


def split_sections(md: str) -> dict:
    """切出日线段 / 周线段 (报告里「因子详情 — 周期独立展开」下面的两个子段)"""
    out = {}
    for key, start, end in (("daily", "### 📋 日线分析", "### 📋 周线分析"),
                            ("weekly", "### 📋 周线分析", "## 🧪")):
        i = md.find(start)
        if i < 0:
            out[key] = ""
            continue
        j = md.find(end, i + len(start))
        out[key] = md[i:j] if j > 0 else md[i:]
    return out


def discover_codes() -> list[str]:
    """从 docs/ 下已有报告里收集代码 (排除 factor-5y, 那是另一种版式)"""
    codes = set()
    for sub in LINT_SUBS:
        d = _ROOT / "docs" / sub
        if not d.exists():
            continue
        for f in d.glob("analyze-*.md"):
            m = re.match(r"analyze-(\d{6})", f.name)
            if m:
                codes.add(m.group(1))
    return sorted(codes)


def report_for(code: str) -> Path | None:
    for sub in LINT_SUBS:
        d = _ROOT / "docs" / sub
        if not d.exists():
            continue
        hits = sorted(d.glob(f"analyze-{code}-*.md"))
        if hits:
            return hits[0]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", nargs="*", default=None, help="指定代码 (默认从 docs/ 自动发现)")
    args = ap.parse_args()

    codes = args.codes or discover_codes()
    if not codes:
        print("❌ 没找到任何报告, 先跑 /t-analyze")
        return 1

    bad: list[tuple] = []
    checked = 0
    skipped: list[str] = []

    for code in codes:
        f = report_for(code)
        if f is None:
            skipped.append(code)
            continue
        sec = split_sections(f.read_text(encoding="utf-8"))

        ctx = DataStore.get_ctx(code)
        if not ctx.kline:
            skipped.append(code)
            continue
        all_dates = [k["trade_date"].replace("-", "")[:8] for k in ctx.kline]
        dates = all_dates[-480:] if len(all_dates) >= 480 else all_dates
        history = AnalysisEngine().analyze_history(ctx, dates)
        wy = (history[dates[-1]].raw.get("wyckoff") or {})
        p3 = wy.get("3period") or {}
        prod = {"daily": wy, "weekly": p3.get("weekly") or {}}

        for lvl in ("daily", "weekly"):
            body = sec.get(lvl) or ""
            if not body:
                continue
            p = prod[lvl]
            p_stage = p.get("stage", "?")
            p_strength = p.get("strength", "none")
            m_stage = RE_STAGE.search(body)
            m_strength = RE_STRENGTH.search(body)
            got_stage = m_stage.group(1) if m_stage else "?"
            got_strength = m_strength.group(1) if m_strength else ""
            checked += 1

            if got_stage != p_stage:
                bad.append((code, lvl, "阶段不符", f"prod={p_stage}", f"report={got_stage}"))
            exp_key = STRENGTH_KEY.get(p_strength, "")
            if exp_key and exp_key not in got_strength:
                bad.append((code, lvl, "强度不符",
                            f"prod={p_strength}({exp_key})", f"report={got_strength}"))
            print(f"  {code} {lvl:6s} prod={p_stage:12s}/{p_strength:10s} | "
                  f"report={got_stage:12s}/{got_strength[:26]}")

    print()
    print(f"检查点数: {checked} (跳过 {len(skipped)} 只无报告/无K线)")
    if bad:
        print(f"❌ 不一致 {len(bad)} 条:")
        for b in bad:
            print("   ", b)
        return 1
    print("✅ 报告的日线/周线威科夫判定 与 生产值 完全一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
