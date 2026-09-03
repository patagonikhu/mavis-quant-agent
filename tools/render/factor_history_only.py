"""factor_history_only.py — 只输出 因子历史走势 section (近 N 天)

复用 render 里的 _section_factor_history, 默认 6 个月改成 5 年
用法:
  python tools/render/factor_history_only.py 399006.SZ --days 1260
  python tools/render/factor_history_only.py 000001.SH --days 250
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))


def main():
    ap = argparse.ArgumentParser(description="只输出因子历史走势 section")
    ap.add_argument("code", help="指数/股票代码 (例 399006.SZ 创业板)")
    ap.add_argument("--days", type=int, default=1260, help="回看天数 (默认 1260 ≈ 5 年)")
    ap.add_argument("--out", default=None, help="输出文件 (默认 stdout)")
    args = ap.parse_args()

    from tools.storage.store import DataStore
    from tools.analysis.analysis_engine import AnalysisEngine
    from tools.analysis.render_data import RenderData
    from tools.render.report_renderer import _section_factor_history

    ctx = DataStore.get_ctx(args.code)
    if not ctx.kline:
        print(f"❌ 没找到 {args.code} 的 K 线", file=sys.stderr)
        return

    ar = AnalysisEngine().analyze(ctx)
    data = RenderData.from_result(ctx, ar)
    section = _section_factor_history(data, lookback=args.days)

    header = f"# 因子历史走势 — {args.code} {ctx.name}\n\n"
    header += f"> 数据范围: {ctx.kline[0]['trade_date']} ~ {ctx.kline[-1]['trade_date']} ({len(ctx.kline)} 日)\n"
    header += f"> 回看: {args.days} 日 ≈ {args.days/252:.1f} 年\n\n"

    out = header + section
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
        print(f"✅ 已生成: {args.out}", file=sys.stderr)
    else:
        print(out)


if __name__ == "__main__":
    main()
