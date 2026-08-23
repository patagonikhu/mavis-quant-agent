"""
tools/batch/factor_history_export.py — 单只股票历史因子导出 (v1, 2026-08-18)

**用户原话 (8-18 08:00)**:
- 看到 docs/factor-history-300308-中际旭创-5year.md 这个文件
- 想生成其他股票的历史因子
- 一次生成单只股票的历史因子, 搞个 skill 出来

**输出**: docs/factor-history-{code}-{name}-{years}year.md
**数据**: data/dump/{code}.json → AnalysisData.from_raw → _section_factor_history(lookback=years*250)
**复用**: 100% 复用 tools/render/report_renderer._section_factor_history, 0 重复代码

**用法**:
  bash tools/with_venv.sh python3 -m tools.batch.factor_history_export 300308            # 默认 5 年
  bash tools/with_venv.sh python3 -m tools.batch.factor_history_export 300308 --years 3   # 3 年
  bash tools/with_venv.sh python3 -m tools.batch.factor_history_export 300274 002028 688981  # 多只
  bash tools/with_venv.sh python3 -m tools.batch.factor_history_export 300308 --out /tmp/x.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
WATCHLIST_JSON = PROJECT_ROOT / "data" / "watchlist.json"
DOCS_DIR = PROJECT_ROOT / "docs"

DEFAULT_YEARS = 5
TRADING_DAYS_PER_YEAR = 250


def _load_name_from_watchlist(code: str) -> str | None:
    """从 watchlist.json 找 code 对应的 name"""
    try:
        wl = json.load(open(WATCHLIST_JSON))
        for s in wl.get("stocks", []):
            if s.get("code") == code:
                return s.get("name")
    except Exception:
        pass
    return None


def export_one(code: str, years: int, out_path: Path | None) -> tuple[str, Path, int]:
    """导出单只票的历史因子"""
    from tools.data_store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.analysis_data import AnalysisData
    from tools.render.report_renderer import _section_factor_history

    ctx = DataStore.get_ctx(code)
    name = ctx.name or code
    result = AnalysisEngine().analyze(ctx)
    data = AnalysisData.from_result(ctx, result)

    lookback = years * TRADING_DAYS_PER_YEAR
    t0 = time.time()
    md = _section_factor_history(data, lookback=lookback)
    elapsed = time.time() - t0

    if out_path is None:
        out_path = DOCS_DIR / f"factor-history-{code}-{name}-{years}year.md"

    # 加 header (跟原 5year 文件结构一致, LLM/读者一眼能看出文件用途)
    header = (
        f"# 📈 {code} {name} 因子历史走势 ({years}年: {lookback}交易日)\n\n"
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"数据: data/dump/{code}.json | 算: _section_factor_history (lookback={lookback}) | "
        f"耗时: {elapsed:.1f}s\n\n"
    )
    out_path.write_text(header + md, encoding="utf-8")

    # 数行数 (排除 header + table header + separator)
    row_count = sum(1 for ln in md.splitlines() if ln.startswith("| ") and "---" not in ln and "日期" not in ln)
    return code, out_path, row_count


def _main():
    parser = argparse.ArgumentParser(description="单只股票历史因子导出 (默认 5 年, 输出 docs/factor-history-{code}-{name}-5year.md)")
    parser.add_argument("codes", nargs="+", help="股票代码 (e.g. 300308 002028 688981)")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS, help=f"年数 (默认 {DEFAULT_YEARS}, 1 年=250 交易日)")
    parser.add_argument("--out", help="输出路径 (单只票时用, 多只时此参数被忽略)")
    args = parser.parse_args()

    if len(args.codes) > 1 and args.out:
        print("⚠️ 多只票时 --out 忽略, 每只票单独写一个文件")

    print(f"导出 {len(args.codes)} 只票历史因子 (lookback={args.years * TRADING_DAYS_PER_YEAR} 天 = {args.years} 年)...")

    ok, fail = 0, 0
    for i, code in enumerate(args.codes, 1):
        try:
            out_path = Path(args.out) if (len(args.codes) == 1 and args.out) else None
            _code, path, rows = export_one(code, args.years, out_path)
            print(f"  ✅ {i}/{len(args.codes)} {_code} → {path.name} ({rows} 行, {path.stat().st_size // 1024} KB)")
            ok += 1
        except Exception as e:
            fail += 1
            print(f"  ❌ {i}/{len(args.codes)} {code}: {e}", file=sys.stderr)

    print(f"\n✅ 完成: 成功 {ok} / 失败 {fail}")


if __name__ == "__main__":
    _main()
